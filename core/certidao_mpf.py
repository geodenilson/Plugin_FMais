"""
Módulo para verificação de inadimplência via certidão do MPF.

Acessa a API do portal do MPF para emitir certidão de "nada consta" para
cada CPF, utilizando o serviço 2Captcha para resolver o Cloudflare Turnstile.
O PDF resultante é baixado e analisado para verificar se consta "NADA CONSTA".

Abordagem via HTTP direto (sem navegador).
"""

import os
import re
import time
import shutil
from typing import Callable, Optional

import requests


PAGE_URL = "https://aplicativos.mpf.mp.br/ouvidoria/app/cidadao/certidao"
API_URL = "https://aplicativos.mpf.mp.br/ouvidoria/rest/v1/publico/certidao"
TURNSTILE_SITEKEY = "0x4AAAAAACMhejJkLsBWVaMb"


def _solve_turnstile(api_key: str, timeout: int = 180) -> str:
    """Resolve Cloudflare Turnstile via serviço 2Captcha."""
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "TurnstileTaskProxyless",
            "websiteURL": PAGE_URL,
            "websiteKey": TURNSTILE_SITEKEY,
        },
    }
    resp = requests.post(
        "https://api.2captcha.com/createTask", json=payload, timeout=20
    ).json()
    task_id = resp.get("taskId")
    if not task_id:
        raise RuntimeError(f"2Captcha não criou task: {resp}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        result = requests.post(
            "https://api.2captcha.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=20,
        ).json()
        if result.get("status") == "ready":
            return result["solution"]["token"]
    raise TimeoutError("2Captcha excedeu o tempo-limite para Turnstile")


def _pdf_contem_nada_consta(conteudo_bytes: bytes) -> bool:
    """
    Verifica se o PDF contém 'NADA CONSTA'.
    Descomprime streams FlateDecode do PDF para buscar no texto real.
    """
    import zlib

    # 1) Busca direta nos bytes brutos (PDFs sem compressão)
    texto_bruto = conteudo_bytes.decode("latin-1", errors="ignore")
    if "NADA CONSTA" in texto_bruto:
        return True

    # 2) Descomprimir cada stream FlateDecode e buscar
    for match in re.finditer(rb"stream\s*\r?\n(.*?)\r?\nendstream", conteudo_bytes, re.DOTALL):
        raw = match.group(1)
        try:
            decompressed = zlib.decompress(raw)
            texto = decompressed.decode("latin-1", errors="ignore")
            if "NADA CONSTA" in texto:
                return True
        except Exception:
            pass

    return False


def _sanitize_filename(name: str) -> str:
    """Remove caracteres inválidos para nome de arquivo."""
    return name.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")


def _processar_pdf(
    conteudo: bytes, cpf: str, cod_imovel: str,
    save_dir: Optional[str],
) -> str:
    """Analisa PDF e opcionalmente salva. Retorna 'Elegível' ou 'Não Elegível'."""
    resultado = "Elegível" if _pdf_contem_nada_consta(conteudo) else "Não Elegível"

    if save_dir and os.path.isdir(save_dir):
        nome_cod = _sanitize_filename(cod_imovel) if cod_imovel else "sem_car"
        nome_final = f"mpf_{cpf}_{nome_cod}.pdf"
        destino = os.path.join(save_dir, nome_final)
        try:
            with open(destino, "wb") as f:
                f.write(conteudo)
        except Exception:
            pass

    return resultado


HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": PAGE_URL,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
}


def emitir_certidao(
    cpf: str,
    api_key_2captcha: str,
    save_dir: Optional[str] = None,
    cod_imovel: str = "",
    log: Optional[Callable] = None,
) -> tuple:
    """
    Emite certidão MPF para um CPF via HTTP direto.

    Returns:
        Tupla (resultado, nome) onde resultado é "Elegível" ou "Não Elegível"
        e nome é o nome retornado pela consulta (ou "").
    """
    if log is None:
        log = lambda msg: None

    nome_pessoa = ""

    session = requests.Session()
    session.headers.update(HEADERS)

    session.get(PAGE_URL, timeout=30)

    token = _solve_turnstile(api_key_2captcha)

    resp_consulta = session.get(
        f"{API_URL}/consultar",
        params={"documento": cpf, "tipoPessoa": "F"},
        timeout=30,
    )
    if resp_consulta.status_code != 200:
        raise RuntimeError(f"Consulta falhou: HTTP {resp_consulta.status_code}")

    try:
        dados_consulta = resp_consulta.json()
        nome_pessoa = dados_consulta.get("data", "") or ""
    except (ValueError, KeyError):
        pass

    resp_emitir = session.get(
        f"{API_URL}/emitir",
        params={
            "documento": cpf,
            "recaptcha": token,
            "tipoPessoa": "F",
        },
        timeout=60,
    )
    if resp_emitir.status_code != 200:
        raise RuntimeError(
            f"Emissão falhou: HTTP {resp_emitir.status_code} — {resp_emitir.text[:200]}"
        )

    content_type = resp_emitir.headers.get("Content-Type", "")
    conteudo = resp_emitir.content

    if b"%PDF" in conteudo[:20] or "pdf" in content_type.lower():
        return _processar_pdf(conteudo, cpf, cod_imovel, save_dir), nome_pessoa

    if "json" in content_type.lower():
        try:
            dados = resp_emitir.json()
            hash_certidao = dados.get("data", "")
            if hash_certidao and dados.get("success"):
                urls_download = [
                    f"{API_URL}/download/{hash_certidao}",
                    f"{API_URL}/download?hash={hash_certidao}",
                    f"{API_URL}/download?id={hash_certidao}",
                    f"{API_URL}/{hash_certidao}",
                ]
                for url in urls_download:
                    resp_pdf = session.get(url, timeout=60)
                    if resp_pdf.status_code == 200 and b"%PDF" in resp_pdf.content[:20]:
                        return _processar_pdf(resp_pdf.content, cpf, cod_imovel, save_dir), nome_pessoa

                raise RuntimeError(
                    f"PDF não encontrado nas URLs de download (último HTTP {resp_pdf.status_code})"
                )
        except (ValueError, KeyError):
            pass

    raise RuntimeError(
        f"Resposta inesperada do MPF (Content-Type: {content_type})"
    )


def verificar_certidoes_lote(
    cpfs_fids: list,
    api_key_2captcha: str,
    save_dir: Optional[str] = None,
    log: Optional[Callable] = None,
    progress: Optional[Callable] = None,
) -> dict:
    """
    Verifica certidões MPF para uma lista de (cpf, fid, cod_imovel).

    Returns:
        Dicionário {fid: "Elegível" | "Não Elegível"}.
    """
    if log is None:
        log = lambda msg: None

    resultados = {}
    total = len(cpfs_fids)
    cpfs_cache = {}
    cpfs_pdf_path = {}

    for i, (cpf, fid, cod_imovel) in enumerate(cpfs_fids):
        cpf_limpo = cpf.replace(".", "").replace("-", "").replace("/", "").strip()
        idx_label = f"[{i+1}/{total}]"

        if not cpf_limpo:
            resultados[fid] = "Elegível"
            continue

        if cpf_limpo in cpfs_cache:
            resultado_cache = cpfs_cache[cpf_limpo]
            resultados[fid] = resultado_cache
            simbolo = "✓" if resultado_cache == "Elegível" else "✗"
            log(f"   {idx_label} CPF {cpf_limpo}: {simbolo} {resultado_cache} (cache)")
            if save_dir and cpf_limpo in cpfs_pdf_path:
                nome_cod = _sanitize_filename(cod_imovel) if cod_imovel else "sem_car"
                destino = os.path.join(save_dir, f"mpf_{cpf_limpo}_{nome_cod}.pdf")
                if not os.path.exists(destino):
                    try:
                        shutil.copy2(cpfs_pdf_path[cpf_limpo], destino)
                    except Exception:
                        pass
        else:
            try:
                resultado, nome = emitir_certidao(
                    cpf_limpo, api_key_2captcha,
                    save_dir=save_dir,
                    cod_imovel=cod_imovel,
                    log=log,
                )
                cpfs_cache[cpf_limpo] = resultado
                resultados[fid] = resultado
                simbolo = "✓" if resultado == "Elegível" else "✗"
                nome_curto = nome[:30] if nome else ""
                if nome_curto:
                    log(f"   {idx_label} CPF {cpf_limpo} ({nome_curto}): {simbolo} {resultado}")
                else:
                    log(f"   {idx_label} CPF {cpf_limpo}: {simbolo} {resultado}")
                if save_dir:
                    nome_cod = _sanitize_filename(cod_imovel) if cod_imovel else "sem_car"
                    pdf_salvo = os.path.join(save_dir, f"mpf_{cpf_limpo}_{nome_cod}.pdf")
                    if os.path.exists(pdf_salvo):
                        cpfs_pdf_path[cpf_limpo] = pdf_salvo
            except Exception as e:
                resultados[fid] = "Elegível"
                log(f"   {idx_label} CPF {cpf_limpo}: ⚠ Erro ({e}) — assumindo Elegível")

        if progress:
            progress(int((i + 1) / total * 100))

    return resultados
