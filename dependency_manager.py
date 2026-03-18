# -*- coding: utf-8 -*-
"""
Gerenciador de dependências do plugin Floresta+ Amazônia.
Verifica, diagnostica e instala bibliotecas Python necessárias para
o classificador de vegetação (scikit-learn, numpy, scipy, etc.).
"""

import sys
import os
import subprocess
import importlib
from typing import List, Tuple, Optional


# Dependências necessárias para o classificador de vegetação.
# Formato: (nome_import, nome_pip, versão_mínima_ou_None)
DEPENDENCIAS_CLASSIFICADOR = [
    ("numpy",      "numpy",        None),
    ("pandas",     "pandas",       None),
    ("sklearn",    "scikit-learn", None),
    ("scipy",      "scipy",        None),
    ("rasterio",   "rasterio",     None),
    ("geopandas",  "geopandas",    None),
    ("shapely",    "shapely",      None),
    ("reportlab",  "reportlab",    None),
]


def verificar_dependencias(deps: list = None) -> Tuple[List[str], List[dict]]:
    """
    Verifica quais dependências estão disponíveis e compatíveis.

    Returns:
        (ok_list, problemas_list)
        ok_list: nomes de pacotes disponíveis
        problemas_list: lista de dicts com 'pacote', 'pip_name' e 'erro'
    """
    if deps is None:
        deps = DEPENDENCIAS_CLASSIFICADOR

    ok = []
    problemas = []

    for nome_import, nome_pip, _versao_min in deps:
        try:
            mod = importlib.import_module(nome_import)

            if nome_import == "numpy":
                _checar_numpy_compatibilidade(mod)

            ok.append(nome_import)
        except Exception as e:
            problemas.append({
                "pacote": nome_import,
                "pip_name": nome_pip,
                "erro": str(e),
            })

    return ok, problemas


def _checar_numpy_compatibilidade(np_mod):
    """
    Testa se o NumPy carregado é compatível com os binários do ambiente
    (pyarrow, GDAL, etc.). A incompatibilidade 1.x vs 2.x é o caso
    mais comum dentro do QGIS/OSGeo4W.
    """
    try:
        _ = np_mod.core.multiarray._flagdict  # acesso rápido que falha se ABI quebrada
    except AttributeError:
        pass

    try:
        import pyarrow  # noqa: F401 – presente no QGIS; se falhar, numpy é incompatível
    except ImportError as e:
        msg = str(e).lower()
        if "numpy" in msg or "multiarray" in msg:
            raise RuntimeError(
                f"NumPy {np_mod.__version__} incompatível com pyarrow do QGIS: {e}"
            ) from e


def formatar_mensagem_problemas(problemas: list) -> str:
    """Gera texto amigável listando os pacotes com problema."""
    linhas = ["As seguintes bibliotecas precisam ser instaladas/atualizadas:\n"]
    for p in problemas:
        linhas.append(f"  • {p['pip_name']}  —  {p['erro']}")
    return "\n".join(linhas)


def _find_python() -> str:
    """
    Localiza o executável Python real do ambiente QGIS.
    No Windows, sys.executable geralmente aponta para qgis-bin.exe/qgis-ltr-bin.exe,
    não para python.exe, o que faz subprocess abrir outra instância do QGIS.
    """
    exe = sys.executable
    if exe and "python" in os.path.basename(exe).lower():
        return exe

    # Estratégia 1: python.exe ao lado do qgis-bin.exe (OSGeo4W)
    qgis_dir = os.path.dirname(exe)
    for candidate in ["python.exe", "python3.exe"]:
        p = os.path.join(qgis_dir, candidate)
        if os.path.isfile(p):
            return p

    # Estratégia 2: <QGIS_PREFIX>/bin/python.exe
    bin_dir = os.path.join(qgis_dir, "bin")
    for candidate in ["python.exe", "python3.exe"]:
        p = os.path.join(bin_dir, candidate)
        if os.path.isfile(p):
            return p

    # Estratégia 3: Subir um nível (apps/qgis-ltr -> apps/Python312)
    parent = os.path.dirname(qgis_dir)
    for d in sorted(os.listdir(parent)) if os.path.isdir(parent) else []:
        if d.lower().startswith("python"):
            p = os.path.join(parent, d, "python.exe")
            if os.path.isfile(p):
                return p

    # Estratégia 4: Procurar na variável OSGEO4W_ROOT
    osgeo_root = os.environ.get("OSGEO4W_ROOT", "")
    if osgeo_root:
        for candidate in [
            os.path.join(osgeo_root, "bin", "python.exe"),
            os.path.join(osgeo_root, "apps", "Python312", "python.exe"),
            os.path.join(osgeo_root, "apps", "Python311", "python.exe"),
            os.path.join(osgeo_root, "apps", "Python39", "python.exe"),
        ]:
            if os.path.isfile(candidate):
                return candidate

    # Fallback: usa sys.executable mesmo (pode não funcionar)
    return exe


def _pip_executable() -> list:
    """Retorna o comando base para chamar pip no Python do QGIS."""
    return [_find_python(), "-m", "pip"]


def instalar_pacotes(problemas: list, upgrade_numpy: bool = False) -> Tuple[bool, str]:
    """
    Tenta instalar/atualizar os pacotes listados em *problemas*.

    Se *upgrade_numpy* for True e houver conflito de NumPy, tenta
    instalar numpy<2 para compatibilidade com binários do QGIS.

    Returns:
        (sucesso: bool, mensagem: str)
    """
    pacotes_para_instalar: List[str] = []

    tem_numpy_problema = any(p["pacote"] == "numpy" for p in problemas)
    tem_sklearn_problema = any(p["pacote"] == "sklearn" for p in problemas)

    if tem_numpy_problema or upgrade_numpy:
        numpy_version = _detectar_numpy_alvo()
        pacotes_para_instalar.append(numpy_version)

    for p in problemas:
        if p["pacote"] == "numpy":
            continue
        pacotes_para_instalar.append(p["pip_name"])

    if tem_sklearn_problema and "scikit-learn" not in pacotes_para_instalar:
        pacotes_para_instalar.append("scikit-learn")

    if not pacotes_para_instalar:
        return True, "Nenhum pacote para instalar."

    cmd = _pip_executable() + [
        "install",
        "--user",
        "--no-warn-script-location",
        "--disable-pip-version-check",
    ] + pacotes_para_instalar

    python_usado = _find_python()
    log_linhas: List[str] = []
    log_linhas.append(f"Python detectado: {python_usado}")
    log_linhas.append(f"sys.executable: {sys.executable}")
    log_linhas.append(f"Comando: {' '.join(cmd)}\n")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        log_linhas.append(result.stdout)
        if result.stderr:
            log_linhas.append(result.stderr)

        if result.returncode == 0:
            return True, "\n".join(log_linhas)
        else:
            return False, "\n".join(log_linhas)

    except subprocess.TimeoutExpired:
        return False, "Tempo limite excedido (10 min). Verifique sua conexão com a internet."
    except FileNotFoundError:
        return False, "pip não encontrado no Python do QGIS."
    except Exception as e:
        return False, f"Erro inesperado: {e}"


def _detectar_numpy_alvo() -> str:
    """
    Decide qual especificação de NumPy instalar com base no pyarrow
    que já vem no QGIS. Se o pyarrow foi compilado com NumPy 1.x,
    forçamos numpy<2; caso contrário, deixamos livre.
    """
    try:
        import pyarrow
        pa_numpy = getattr(pyarrow, "cpp_build_info", None)
        if pa_numpy and hasattr(pa_numpy, "numpy_version"):
            if pa_numpy.numpy_version.startswith("1"):
                return "numpy<2"
    except Exception:
        pass

    try:
        import numpy as np
        major = int(np.__version__.split(".")[0])
        if major >= 2:
            return "numpy<2"
    except Exception:
        pass

    return "numpy<2"


def verificar_e_instalar_interativo(parent_widget=None) -> bool:
    """
    Fluxo completo para usar dentro do QGIS com diálogos Qt:
    1. Verifica dependências
    2. Se há problemas, mostra diálogo perguntando se quer instalar
    3. Instala ou avisa o usuário

    Returns:
        True se tudo OK (ou já estava OK), False se ficou com pendências.
    """
    from qgis.PyQt.QtWidgets import QMessageBox, QApplication
    from qgis.PyQt.QtCore import Qt

    ok, problemas = verificar_dependencias()

    if not problemas:
        return True

    msg_problemas = formatar_mensagem_problemas(problemas)

    tem_numpy_conflict = any(
        "incompatível" in p["erro"].lower() or "multiarray" in p["erro"].lower()
        for p in problemas
    )

    texto_extra = ""
    if tem_numpy_conflict:
        texto_extra = (
            "\n\nFoi detectado um conflito de versão do NumPy.\n"
            "A instalação tentará corrigir automaticamente."
        )

    resp = QMessageBox.question(
        parent_widget,
        "Floresta+ — Dependências Necessárias",
        f"{msg_problemas}{texto_extra}\n\n"
        "Deseja tentar instalar automaticamente?\n"
        "(Requer conexão com a internet)",
        QMessageBox.Yes | QMessageBox.No,
    )

    if resp != QMessageBox.Yes:
        QMessageBox.warning(
            parent_widget,
            "Floresta+ — Bibliotecas Pendentes",
            "O plugin funcionará parcialmente sem as bibliotecas.\n\n"
            "A funcionalidade de classificação de vegetação ficará\n"
            "indisponível até que as dependências sejam instaladas.\n\n"
            "Para instalar manualmente, abra o OSGeo4W Shell e execute:\n\n"
            + _gerar_comando_manual(problemas),
        )
        return False

    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        sucesso, log = instalar_pacotes(problemas, upgrade_numpy=tem_numpy_conflict)
    finally:
        QApplication.restoreOverrideCursor()

    if sucesso:
        QMessageBox.information(
            parent_widget,
            "Floresta+ — Instalação Concluída",
            "Bibliotecas instaladas com sucesso!\n\n"
            "Recomenda-se reiniciar o QGIS para que as\n"
            "alterações tenham efeito completo.",
        )
        return True
    else:
        QMessageBox.critical(
            parent_widget,
            "Floresta+ — Falha na Instalação",
            "Não foi possível instalar as bibliotecas automaticamente.\n\n"
            "Possíveis causas:\n"
            "  • Sem conexão com a internet\n"
            "  • Permissões insuficientes\n"
            "  • Proxy/firewall bloqueando o pip\n\n"
            "Para instalar manualmente, abra o OSGeo4W Shell e execute:\n\n"
            + _gerar_comando_manual(problemas)
            + "\n\nDetalhes do erro:\n"
            + (log[:500] if log else "Sem detalhes"),
        )
        return False


def _gerar_comando_manual(problemas: list) -> str:
    """Gera o comando pip para instalação manual."""
    nomes = []
    tem_numpy = False
    for p in problemas:
        if p["pacote"] == "numpy":
            tem_numpy = True
            continue
        nomes.append(p["pip_name"])

    partes = []
    if tem_numpy:
        partes.append("pip install \"numpy<2\"")
    if nomes:
        partes.append(f"pip install {' '.join(nomes)}")

    return "\n".join(partes)
