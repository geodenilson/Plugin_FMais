# -*- coding: utf-8 -*-
"""
Gerador de Laudos PDF - Plugin Floresta+
Geração de laudos de elegibilidade em formato PDF

AJUSTES (PDF):
- Layout do PDF recriado para ficar visualmente igual ao “exemplo do visualizador” (imagem):
  * Caixa "Dados do Imóvel" com borda verde e linhas internas de campos
  * Título "Avaliação de Elegibilidade" e tabela com cabeçalho azul escuro
  * Cores/estilos de "Elegível", "Não Elegível" e "Não se aplica"
  * Caixa "Resultado" com borda vermelha, faixa central com resultado e caixa de justificativa
"""

import os
import tempfile
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QProgressBar,
    QMessageBox, QFileDialog, QCheckBox, QRadioButton
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.core import (
    QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsGeometry, QgsRectangle,
    QgsMapSettings, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject, QgsFillSymbol, QgsSingleSymbolRenderer
)


class GeradorLaudosThread(QThread):
    """Thread para geração de laudos em background."""
    progress = pyqtSignal(int, int, str)  # atual, total, mensagem
    finished = pyqtSignal(int, int)       # sucesso, falhas
    error = pyqtSignal(str)

    def __init__(self, layer, features, output_dir, gpkg_path=None, planet_url=None):
        super().__init__()
        self.layer = layer
        self.features = features
        self.output_dir = output_dir
        self.gpkg_path = gpkg_path
        self.planet_url = planet_url
        self.cancelar = False

    def run(self):
        sucesso = 0
        falhas = 0
        total = len(self.features)

        for i, feat in enumerate(self.features):
            if self.cancelar:
                break

            try:
                car = self._get_attr(feat, ["n_do_car", "cod_imovel"], f"imovel_{feat.id()}")
                cpf = self._get_attr(feat, ["cpf_cnpj"], "")

                cpf_clean = str(cpf).replace(".", "").replace("-", "").replace("/", "").replace(" ", "")[:14] if cpf else ""
                car_clean = str(car).replace("/", "-").replace("\\", "-").replace(" ", "")

                if cpf_clean:
                    filename = f"{cpf_clean}_{car_clean}.pdf"
                else:
                    filename = f"{car_clean}.pdf"

                filepath = os.path.join(self.output_dir, filename)
                self.progress.emit(i + 1, total, f"Gerando: {filename[:70]}...")

                self._gerar_pdf(feat, filepath)
                sucesso += 1

            except Exception as e:
                falhas += 1
                # não interrompe; continua gerando os demais
                import traceback
                print(f"[Laudos] Erro ao gerar '{feat.id()}': {e}")
                print(f"[Laudos] Traceback: {traceback.format_exc()}")

        self.finished.emit(sucesso, falhas)

    # -------------------------------------------------------------------------
    # PDF (ReportLab) - Layout “igual ao exemplo”
    # -------------------------------------------------------------------------
    def _gerar_story(self, feat):
        """Gera apenas o story (conteúdo) do PDF sem criar o documento.
        Usado para combinar múltiplos laudos em um único PDF."""
        # Criar um arquivo temporário para gerar o PDF
        import tempfile
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_laudo_{feat.id()}.pdf")
        
        # Usar o método existente para gerar o story
        story = self._gerar_pdf_interno(feat, temp_path, return_story=True)
        
        # Limpar arquivo temporário se existir
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except:
            pass
        
        return story if story else []
    
    def _gerar_pdf(self, feat, filepath):
        """Gera o PDF de um imóvel específico com layout igual ao exemplo."""
        return self._gerar_pdf_interno(feat, filepath, return_story=False)
    
    def _gerar_pdf_interno(self, feat, filepath, return_story=False):
        """Implementação interna da geração de PDF.
        Se return_story=True, retorna o story sem criar o documento."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, Image
            )
            from reportlab.lib.styles import ParagraphStyle
            from reportlab.lib.enums import TA_LEFT, TA_CENTER
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except Exception:
            self._gerar_pdf_simples(feat, filepath)
            return

        # ---------------------------------------------------------------------
        # Tentativa de fonte com suporte a "✗" (opcional). Se falhar, usa Helvetica
        # ---------------------------------------------------------------------
        font_base = "Helvetica"
        font_bold = "Helvetica-Bold"
        cross_char = "✗"
        try:
            # caminhos comuns (Windows / OSGeo4W). Se não achar, ignora.
            possible = [
                r"C:\Windows\Fonts\DejaVuSans.ttf",
                r"C:\Windows\Fonts\dejavusans.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ]
            for p in possible:
                if os.path.exists(p):
                    pdfmetrics.registerFont(TTFont("BodyFont", p))
                    font_base = "BodyFont"
                    font_bold = "BodyFont"
                    break
        except Exception:
            pass

        # Se a fonte não suportar, substitui por "X" (sempre renderiza)
        try:
            _ = cross_char.encode("utf-8")
        except Exception:
            cross_char = "X"

        # ---------------------------------------------------------------------
        # Cores (aproximação fiel ao exemplo)
        # ---------------------------------------------------------------------
        GREEN_BORDER = colors.HexColor("#2e7d32")
        GREEN_TEXT = colors.HexColor("#1b5e20")
        BLUE_HEADER = colors.HexColor("#2f4a5b")   # cabeçalho da tabela (azul escuro)
        GRID = colors.HexColor("#d9d9d9")
        LIGHT_GREEN_BG = colors.HexColor("#eaf4ea")  # leve “verde água” no topo da caixa
        RESULT_RED = colors.HexColor("#d32f2f")
        RESULT_LIGHT_RED_BG = colors.HexColor("#fdeaea")
        RESULT_FAIXA_BG = colors.HexColor("#f8f8f8")  # faixa central do resultado
        NOTE_BG = colors.HexColor("#eef5ff")          # caixa de justificativa (azulada)
        NOTE_BORDER = colors.HexColor("#c9d9f2")
        GREY_TEXT = colors.HexColor("#6b6b6b")

        OK_GREEN = colors.HexColor("#1e8e3e")
        NO_RED = colors.HexColor("#d93025")
        NA_GREY = colors.HexColor("#7a7a7a")

        # ---------------------------------------------------------------------
        # Dados do imóvel (extração)
        # ---------------------------------------------------------------------
        car = self._get_attr(feat, ["n_do_car", "cod_imovel"], "N/D")
        cpf_raw = self._get_attr(feat, ["cpf_cnpj"], "N/D")
        
        # Formatar CPF: 01599912260 -> 015.999.122-60
        def format_cpf(cpf_str):
            if not cpf_str or cpf_str == "N/D":
                return cpf_str
            # Remover caracteres não numéricos
            digits = ''.join(c for c in str(cpf_str) if c.isdigit())
            if len(digits) == 11:
                return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
            elif len(digits) == 14:  # CNPJ
                return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
            return cpf_str  # Retorna original se não for CPF/CNPJ válido
        
        cpf = format_cpf(cpf_raw)
        proprietario = self._get_attr(feat, ["nom_comple"], "N/D")

        # Endereço: no exemplo aparece “Endereço: ... , MUNICÍPIO/UF”
        nom_lograd = self._get_attr(feat, ["nom_lograd"], "")
        municipio = self._get_attr(feat, ["municipio"], "N/D")
        uf = self._get_attr(feat, ["uf", "cod_estado"], "N/D")

        # Alguns bancos tem endereço “Endereço: Av. ...” e bairro separado
        nom_bairro = self._get_attr(feat, ["nom_bairro"], "")

        status = self._get_attr(feat, ["status", "ind_status", "status_imo", "status_imovel", "status_imove"], "N/D")
        tipo_imovel = self._get_attr(feat, ["tipo_imove", "ind_tipo", "tipo_imovel", "tipo"], "")
        condicao = self._get_attr(feat, ["condicao", "des_condic", "condicao_imovel"], "N/D")

        # área e RVN
        area = self._get_attr(feat, ["area_imove", "area", "num_area", "area_ha", "area_imovel"], 0)
        rvn = self._get_attr(feat, ["RVN_area", "rvn_area", "rvn"], 0)
        rvn_pct = self._get_attr(feat, ["percent_rvn", "rvn_pct", "rvn_percent"], 0)

        # resultado e justificativa
        elegibilidade = self._get_attr(feat, ["elegibilidade"], "N/D")  # "Fase 1" / "Fase 2" / "Inelegível"
        parecer = self._get_attr(feat, ["parecer"], "")

        # Montar endereço
        endereco_partes = []
        if nom_lograd:
            endereco_partes.append(str(nom_lograd))
        if nom_bairro:
            # no exemplo o bairro aparece no endereço da interface, mas não é obrigatório
            # mantém (ajuda a ficar fiel quando existir)
            endereco_partes.append(str(nom_bairro))
        if municipio and uf:
            endereco_partes.append(f"{municipio}/{uf}")
        endereco = ", ".join([p for p in endereco_partes if p])

        # Normalizações numéricas
        def _to_float(x, default=0.0):
            try:
                if x is None:
                    return default
                if isinstance(x, str):
                    x = x.replace(",", ".")
                return float(x)
            except Exception:
                return default

        area_f = _to_float(area, 0.0)
        rvn_f = _to_float(rvn, 0.0)
        rvn_pct_f = _to_float(rvn_pct, 0.0)

        # no exemplo: "RVN: 1.33 ha (21.8%)"
        # se o campo vier como 0.218, converte; se vier 21.8 já, mantém
        pct_show = rvn_pct_f
        if 0 < rvn_pct_f <= 1.0:
            pct_show = rvn_pct_f * 100.0

        # ---------------------------------------------------------------------
        # Critérios (17) - mantém seu mapeamento
        # ---------------------------------------------------------------------
        criterios = [
            ("1) Localização na Amazônia Legal", "dentro_da_amzl", "dentro_da_amzl", True, True),
            ("2) Localização nos Municípios prioritários conforme a portaria MMA", "em_prioritarios", None, True, False),
            ("3) Situação do imóvel no CAR", "chek_status_f1", "chek_status_f2", True, True),
            ("4) Condição da análise do imóvel no CAR", "condicao_Fase1", "condicao_Fase2", True, True),
            ("5) Sobreposição em relação à Unidades de Conservação que não admitem domínio privado", "uc", "uc", True, True),
            ("6) Sobreposição em relação à Terras Indígenas", "terra_indi", "terra_indi", True, True),
            ("7) Sobreposição em relação à Território Remanescente de Quilombola", "quilombola", "quilombola", True, True),
            ("8) Floresta Pública Tipo B (Não Destinada)", "cnfp", "cnfp", True, True),
            ("9) RVN mínimo 1 hectare", "rvn_minima", "rvn_minima", True, True),
            ("10) RVN superior a 20% para áreas de campos gerais, 35% para cerrado e 50% para floresta", "rvn_minima", "rvn_minima", True, True),
            ("11) Desmatamento PRODES após 2008 menor que 6,25 ha", "prodes_6ha", "prodes_6ha", True, True),
            ("12) Módulos fiscais", "modulos_imovel", "modulos_imovel", True, True),
            ("13) Descumprimento com a Lei de Proteção da Vegetação Nativa (Lei nº 12.651/2012)", "cert_mpf", "cert_mpf", True, True),
            ("14) Infrações ou embargos IBAMA", "embargo_ibama", "embargo_ibama", True, True),
            ("15) Infrações ou embargos ICMBio", "embargo_icmbio", "embargo_icmbio", True, True),
            ("16) Inadimplência em relação ao TAC ou de compromisso firmado com órgãos competentes", "cert_mpf", "cert_mpf", True, True),
            ("17) Sobreposição com outro CAR", "sobrep_car", None, True, False),
        ]

        def _normalize_status(valor, aplica: bool):
            """
            Retorna (texto, cor) seguindo o visual:
            - Elegível em verde
            - Não Elegível em vermelho
            - Não se aplica em cinza
            """
            if not aplica:
                return "Não se aplica", NA_GREY

            if valor is None or str(valor).strip() == "" or str(valor).strip().upper() in ("N/D", "ND"):
                # no exemplo o “N/D” não aparece muito; mas mantém
                return "N/D", GREY_TEXT

            v = str(valor).strip().upper()

            yes_tokens = {"ELEGÍVEL", "ELEGIVEL", "SIM", "OK", "DENTRO", "APTO", "APTA", "TRUE", "1"}
            no_tokens = {"NÃO ELEGÍVEL", "NAO ELEGIVEL", "NÃO", "NAO", "FORA", "INAPTO", "INAPTA", "FALSE", "0"}

            if v in yes_tokens:
                return "Elegível", OK_GREEN
            if v in no_tokens:
                return "Não Elegível", NO_RED

            # heurística conservadora
            if any(t in v for t in ["SOBR", "EXCEDE", "INSUF", "DESMAT", "EMBARGO", "INFRA", "IRREG", "PEND"]):
                return "Não Elegível", NO_RED

            return "Elegível", OK_GREEN

        # ---------------------------------------------------------------------
        # Estilos (ReportLab) para ficar “igual ao exemplo”
        # ---------------------------------------------------------------------
        base = ParagraphStyle(
            "base", fontName=font_base, fontSize=8.2, leading=10.5, textColor=colors.black
        )
        base_bold = ParagraphStyle(
            "base_bold", parent=base, fontName=font_bold
        )
        section_title = ParagraphStyle(
            "section_title", parent=base_bold, fontSize=9.2, textColor=GREEN_TEXT
        )
        small = ParagraphStyle(
            "small", parent=base, fontSize=7.5, leading=9.5
        )
        small_center = ParagraphStyle(
            "small_center", parent=small, alignment=TA_CENTER
        )
        crit_style = ParagraphStyle(
            "crit", parent=small, alignment=TA_LEFT
        )
        result_big = ParagraphStyle(
            "result_big", parent=base_bold, fontSize=12.5, leading=15, alignment=TA_CENTER
        )
        note_style = ParagraphStyle(
            "note", parent=base, fontSize=8.0, leading=10.5, alignment=TA_LEFT
        )
        footer_style = ParagraphStyle(
            "footer", parent=small_center, textColor=GREY_TEXT
        )

        # ---------------------------------------------------------------------
        # Documento
        # ---------------------------------------------------------------------
        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=5 * mm,
            bottomMargin=5 * mm,
            title=f"Laudo_{car}",
        )

        story = []
        
        # Largura total util (pagina menos margens)
        w_total = A4[0] - 20 * mm

        # ---------------------------------------------------------------------
        # 0) Titulo principal do documento (sem logos laterais)
        # ---------------------------------------------------------------------
        import os
        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        titulo_principal = ParagraphStyle(
            "titulo_principal", parent=base_bold, fontSize=15, textColor=colors.black, alignment=TA_CENTER
        )
        
        # Título centralizado
        story.append(Paragraph("<b>Laudo de elegibilidade - Projeto Floresta + Amazônia</b>", titulo_principal))
        story.append(Spacer(1, 5 * mm))

        # ---------------------------------------------------------------------
        # 1) Caixa "Dados do Imovel"
        #    Reproduz o “grupo” verde do exemplo (borda verde e conteúdo em linhas)
        # ---------------------------------------------------------------------
        dados_title_style = ParagraphStyle(
            "dados_title", parent=base_bold, fontSize=9.5, textColor=GREEN_TEXT, alignment=TA_CENTER
        )

        # Linhas internas - usando fonte small (mesmo tamanho dos critérios)
        # CAR 40% / Endereço 60%
        row1 = [
            Paragraph(f"<b>CAR:</b> {car}", small),
            Paragraph(f"<b>Endereço:</b> {endereco if endereco else '-'}", small),
        ]
        # Proprietário(a)/Possuidor(a) 85% / CPF 15%
        row2 = [
            Paragraph(f"<b>Proprietário(a)/Possuidor(a):</b> {proprietario if proprietario else '-'}", small),
            Paragraph(f"<b>CPF:</b> {cpf if cpf else '-'}", small),
        ]
        # Status 9% / Tipo 8% / Condição + Área + RVN (em 4 “caixas”)
        tipo_str = tipo_imovel if tipo_imovel and str(tipo_imovel).strip() not in ('', 'NULL', 'None', 'N/D') else '-'
        row3 = [
            Paragraph(f"<b>Status:</b> {status}", small),
            Paragraph(f"<b>Tipo:</b> {tipo_str}", small),
            Paragraph(f"<b>Condição:</b> {condicao}", small),
            Paragraph(f"<b>Área:</b> {area_f:.2f} ha", small),
            Paragraph(f"<b>RVN:</b> {rvn_f:.2f} ha ({pct_show:.1f}%)", small),
        ]

        # Tabela interna “layout” (sem grade pesada; separações suaves)

        # Como a Table acima está “1 coluna”, cria sub-tabelas por linha para controlar “caixinhas”
        # Vamos montar de forma mais fiel: uma tabela externa com borda e dentro, tabelas por linha.
        # (Evita gambiarras de span excessivo e dá controle igual ao exemplo)

        # linha 1: 2 colunas (CAR 45% / Endereço 55%)
        dados_l1 = Table([row1], colWidths=[w_total * 0.43, w_total * 0.57])
        dados_l1.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBEFORE", (1, 0), (1, 0), 0.3, GRID),
        ]))

        # linha 2: 2 colunas (Proprietário 75% / CPF 25%)
        dados_l2 = Table([row2], colWidths=[w_total * 0.85, w_total * 0.15])
        dados_l2.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBEFORE", (1, 0), (1, 0), 0.3, GRID),
        ]))

        # linha 3: 5 colunas (Status 9% / Condição 50% / Área 20% / RVN 20%)
        dados_l3 = Table([row3], colWidths=[w_total * 0.09, w_total * 0.08, w_total * 0.50, w_total * 0.15, w_total * 0.18])
        dados_l3.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBEFORE", (1, 0), (1, 0), 0.3, GRID),
            ("LINEBEFORE", (2, 0), (2, 0), 0.3, GRID),
            ("LINEBEFORE", (3, 0), (3, 0), 0.3, GRID),
            ("LINEBEFORE", (4, 0), (4, 0), 0.3, GRID),
        ]))


        # tabela externa (borda verde)
        dados_box = Table(
            [
                [Paragraph("Dados do Imóvel e do(a) agricultor(a) familiar", dados_title_style)],
                [dados_l1],
                [dados_l2],
                [dados_l3],
            ],
            colWidths=[w_total],
        )
        dados_box.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, GREEN_BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 0), (-1, 0), 3),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
            ("LINEABOVE", (0, 1), (-1, 1), 0.3, GRID),
            ("LINEABOVE", (0, 2), (-1, 2), 0.3, GRID),
            ("LINEABOVE", (0, 3), (-1, 3), 0.3, GRID),
        ]))

        story.append(dados_box)
        story.append(Spacer(1, 1 * mm))

        # ---------------------------------------------------------------------
        # 2) Tabela de Avaliação de Elegibilidade (sem título)
        # ---------------------------------------------------------------------

        # Cabeçalho - texto branco
        header_white = ParagraphStyle("header_white", parent=small, textColor=colors.white)
        header_white_center = ParagraphStyle("header_white_center", parent=small_center, textColor=colors.white)
        table_data = [
            [
                Paragraph("<b>Critério de elegibilidade</b>", header_white),
                Paragraph("<b>Fase 1</b>", header_white_center),
                Paragraph("<b>Fase 2</b>", header_white_center),
            ]
        ]

        for nome, campo_f1, campo_f2, aplica_f1, aplica_f2 in criterios:
            v1 = self._get_attr(feat, [campo_f1], "N/D") if campo_f1 else "N/D"
            v2 = self._get_attr(feat, [campo_f2], "N/D") if campo_f2 else "N/D"

            t1, c1 = _normalize_status(v1, aplica_f1)
            t2, c2 = _normalize_status(v2, aplica_f2)

            table_data.append([
                Paragraph(nome, crit_style),
                Paragraph(f'<font color="{c1.hexval()}">{t1}</font>', small_center),
                Paragraph(f'<font color="{c2.hexval()}">{t2}</font>', small_center),
            ])

        tbl = Table(table_data, colWidths=[w_total * 0.70, w_total * 0.15, w_total * 0.15])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), font_bold),
            ("FONTSIZE", (0, 0), (-1, 0), 7.5),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.3, GRID),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8f8")]),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 1 * mm))

        # ---------------------------------------------------------------------
        # 2b) Tabela de Critérios de Priorização (se disponível)
        # ---------------------------------------------------------------------
        try:
            self._adicionar_tabela_priorizacao(
                story, feat, w_total,
                small=small,
                small_center=small_center,
                header_white=header_white,
                header_white_center=header_white_center,
                crit_style=crit_style,
                font_bold=font_bold,
                BLUE_HEADER=BLUE_HEADER,
                GRID=GRID,
            )
        except Exception:
            pass

        # ---------------------------------------------------------------------
        # 3) Resultado Final (caixa vermelha com faixa e justificativa)
        # ---------------------------------------------------------------------
        # Determinar texto do resultado (sem título separado)
        if str(elegibilidade).strip().upper() in ("FASE 1", "Fase 1".upper()):
            result_text = "Resultado: Elegível - Fase 1"
            result_color = OK_GREEN
            result_bg = colors.HexColor("#ecf7ee")
            border_color = OK_GREEN
            cross = ""  # sem “X”
        elif str(elegibilidade).strip().upper() in ("FASE 2", "Fase 2".upper()):
            result_text = "Resultado: Elegível - Fase 2"
            result_color = OK_GREEN
            border_color = OK_GREEN
        else:
            result_text = "Resultado: Inelegível"
            result_color = RESULT_RED
            border_color = RESULT_RED

        # Faixa do resultado (com borda fina como a tabela)
        faixa = Table(
            [[Paragraph(f'<font color="{result_color.hexval()}"><b>{result_text}</b></font>', result_big)]],
            colWidths=[w_total],
        )
        faixa.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("LEFTPADDING", (0, 0), (0, 0), 4),
            ("RIGHTPADDING", (0, 0), (0, 0), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        # Justificativa (parecer)
        # Se não vier parecer, cria um texto mínimo coerente (melhor do que “vazio”)
        if not str(parecer).strip():
            # tenta compor algo com RVN (como no exemplo)
            if rvn_f < 1.0:
                parecer_auto = (
                    "O imóvel encontra-se INELEGÍVEL, pois não atendeu critérios mínimos de RVN."
                )
            else:
                parecer_auto = (
                    "O imóvel encontra-se INELEGÍVEL por não atender a um ou mais critérios de elegibilidade."
                )
            parecer_final = parecer_auto
        else:
            parecer_final = str(parecer).strip()

        note_box = Table(
            [[Paragraph(parecer_final, note_style)]],
            colWidths=[w_total],
        )
        note_box.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))

        # Caixa externa (como no exemplo)
        result_outer = Table(
            [
                [faixa],
                [note_box],
            ],
            colWidths=[w_total],
        )
        result_outer.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.3, border_color),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEABOVE", (0, 1), (-1, 1), 0.3, border_color),
        ]))

        story.append(KeepTogether([result_outer]))
        story.append(Spacer(1, 1 * mm))

        # ---------------------------------------------------------------------
        # 4) Mapas do imovel - dois lado a lado com legenda
        # ---------------------------------------------------------------------
        try:
            from reportlab.platypus import Image as RLImage
            
            # Gerar imagens dos mapas em arquivos temporarios
            map1_path = os.path.join(tempfile.gettempdir(), f"map1_{car.replace('/', '_')}.png")
            map2_path = os.path.join(tempfile.gettempdir(), f"map2_{car.replace('/', '_')}.png")
            
            # Dimensoes: cada mapa (+8% total)
            map_width_px = 428   # pixels largura
            map_height_px = 311  # pixels altura
            map_width_mm = 71    # mm no PDF
            map_height_mm = 52   # mm no PDF
            
            # Mapa 1: Com overlays (PRODES, UC, TI, Quilombolas, CNFP)
            result1 = self._render_map(feat, map1_path, map_width_px, map_height_px, 
                                       include_overlays=True, vegetation_only=False)
            
            # Mapa 2: Apenas vegetacao
            result2 = self._render_map(feat, map2_path, map_width_px, map_height_px, 
                                       include_overlays=False, vegetation_only=True)
            
            # Coletar camadas visiveis para legenda
            all_visible_layers = []
            if result1.get('success'):
                all_visible_layers.extend(result1.get('visible_layers', []))
            if result2.get('success'):
                for layer_info in result2.get('visible_layers', []):
                    if layer_info not in all_visible_layers:
                        all_visible_layers.append(layer_info)
            
            if result1.get('success') or result2.get('success'):
                # Titulo em caixa estilizada
                story.append(Spacer(1, 1 * mm))
                map_title_style = ParagraphStyle(
                    "map_title", parent=section_title, alignment=1  # 1 = CENTER
                )
                map_title = Paragraph('<font color="#1D3411"><b>Mapas do Imóvel</b></font>', map_title_style)
                title_table = Table([[map_title]], colWidths=[w_total])
                title_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#C0E854")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]))
                story.append(title_table)
                story.append(Spacer(1, 1 * mm))
                
                # Criar conteudo dos mapas (titulos serao adicionados dentro da imagem)
                map_cells = []
                
                # Adicionar titulo e borda preta nas imagens usando PIL
                try:
                    from PIL import Image as PILImage, ImageDraw, ImageFont
                    
                    def add_border(img, border_width=1, color=(0, 0, 0)):
                        """Adiciona borda preta fina a imagem"""
                        draw = ImageDraw.Draw(img)
                        w, h = img.size
                        for i in range(border_width):
                            draw.rectangle([i, i, w-1-i, h-1-i], outline=color)
                        return img
                    
                    def draw_text_with_halo(draw, pos, text, font, text_color=(0,0,0), halo_color=(255,255,255)):
                        """Desenha texto com halo (contorno) branco"""
                        x, y = pos
                        # Desenhar halo branco em 8 direções
                        for dx in [-2, -1, 0, 1, 2]:
                            for dy in [-2, -1, 0, 1, 2]:
                                if dx != 0 or dy != 0:
                                    draw.text((x + dx, y + dy), text, fill=halo_color, font=font)
                        # Desenhar texto preto por cima
                        draw.text((x, y), text, fill=text_color, font=font)
                    
                    # Mapa 1: adicionar titulo e borda
                    if result1.get('success') and os.path.exists(map1_path):
                        img1 = PILImage.open(map1_path)
                        draw1 = ImageDraw.Draw(img1)
                        try:
                            font = ImageFont.truetype("arial.ttf", 16)
                        except:
                            font = ImageFont.load_default()
                        draw_text_with_halo(draw1, (6, 4), "Sobreposições", font)
                        add_border(img1)
                        img1.save(map1_path)
                    
                    # Mapa 2: adicionar titulo e borda
                    if result2.get('success') and os.path.exists(map2_path):
                        img2 = PILImage.open(map2_path)
                        draw2 = ImageDraw.Draw(img2)
                        try:
                            font = ImageFont.truetype("arial.ttf", 16)
                        except:
                            font = ImageFont.load_default()
                        draw_text_with_halo(draw2, (6, 4), "Vegetação", font)
                        add_border(img2)
                        img2.save(map2_path)
                except Exception as e:
                    print(f"[Laudos] Nao foi possivel adicionar titulos aos mapas: {e}")
                
                # Mapa 1
                if result1.get('success'):
                    map1_img = RLImage(map1_path, width=map_width_mm * mm, height=map_height_mm * mm)
                    map1_content = [map1_img]
                else:
                    map1_content = [Paragraph("Mapa indisponivel", small)]
                
                # Mapa 2
                if result2.get('success'):
                    map2_img = RLImage(map2_path, width=map_width_mm * mm, height=map_height_mm * mm)
                    map2_content = [map2_img]
                else:
                    map2_content = [Paragraph("Mapa indisponivel", small)]
                
                # Legenda visual com caixas retangulares
                from reportlab.graphics.shapes import Drawing, Rect, Line, Group
                from reportlab.graphics import renderPDF
                
                legend_rows = []
                
                # Titulo da legenda
                legend_title = Paragraph(
                    '<font color="#1a472a"><b>LEGENDA</b></font>',
                    ParagraphStyle("legend_title", fontSize=8, alignment=1)
                )
                legend_rows.append(legend_title)
                legend_rows.append(Spacer(1, 1.5 * mm))
                
                box_w, box_h = 20, 12  # Tamanho da caixa de simbolo
                spacing = 3  # Espacamento entre linhas de hachura
                
                def clip_line_to_rect(x1, y1, x2, y2, rx, ry, rw, rh):
                    """Recorta uma linha para ficar dentro de um retangulo."""
                    # Algoritmo de Cohen-Sutherland simplificado
                    def clip_point(x, y, dx, dy):
                        t_min, t_max = 0.0, 1.0
                        for edge, p, d in [(rx, x, dx), (rx + rw, x, dx), (ry, y, dy), (ry + rh, y, dy)]:
                            if d == 0:
                                continue
                            t = (edge - p) / d
                            if d > 0:
                                t_min = max(t_min, t) if edge in [rx, ry] else t_min
                                t_max = min(t_max, t) if edge in [rx + rw, ry + rh] else t_max
                            else:
                                t_max = min(t_max, t) if edge in [rx, ry] else t_max
                                t_min = max(t_min, t) if edge in [rx + rw, ry + rh] else t_min
                        return t_min, t_max
                    
                    dx, dy = x2 - x1, y2 - y1
                    # Recortar nos limites
                    cx1 = max(rx, min(rx + rw, x1))
                    cy1 = max(ry, min(ry + rh, y1))
                    cx2 = max(rx, min(rx + rw, x2))
                    cy2 = max(ry, min(ry + rh, y2))
                    return cx1, cy1, cx2, cy2
                
                for layer_name, color, style_type in all_visible_layers:
                    # Criar desenho do simbolo
                    d = Drawing(box_w + 4, box_h + 4)
                    ox, oy = 2, 2  # Offset para margem
                    
                    if style_type == 'line':
                        # Limite do imovel: apenas contorno vermelho, sem preenchimento
                        d.add(Rect(ox, oy, box_w, box_h, fillColor=None, 
                                   strokeColor=colors.HexColor(color), strokeWidth=2))
                    elif style_type == 'x':
                        # Hachura quadriculada (X): linhas diagonais nos dois sentidos
                        # Primeiro o fundo branco
                        d.add(Rect(ox, oy, box_w, box_h, fillColor=colors.white, 
                                   strokeColor=None, strokeWidth=0))
                        # Linhas diagonais / 
                        for i in range(-box_h, box_w + box_h, spacing):
                            lx1, ly1 = ox + i, oy
                            lx2, ly2 = ox + i + box_h, oy + box_h
                            # Recortar manualmente
                            if lx1 < ox: ly1 += (ox - lx1); lx1 = ox
                            if lx2 > ox + box_w: ly2 -= (lx2 - ox - box_w); lx2 = ox + box_w
                            if ly1 >= oy and ly2 <= oy + box_h and lx1 <= lx2:
                                d.add(Line(lx1, ly1, lx2, ly2, 
                                          strokeColor=colors.HexColor(color), strokeWidth=0.5))
                        # Linhas diagonais \
                        for i in range(-box_h, box_w + box_h, spacing):
                            lx1, ly1 = ox + i, oy + box_h
                            lx2, ly2 = ox + i + box_h, oy
                            # Recortar manualmente
                            if lx1 < ox: ly1 -= (ox - lx1); lx1 = ox
                            if lx2 > ox + box_w: ly2 += (lx2 - ox - box_w); lx2 = ox + box_w
                            if ly1 <= oy + box_h and ly2 >= oy and lx1 <= lx2:
                                d.add(Line(lx1, ly1, lx2, ly2, 
                                          strokeColor=colors.HexColor(color), strokeWidth=0.5))
                        # Contorno por cima
                        d.add(Rect(ox, oy, box_w, box_h, fillColor=None, 
                                   strokeColor=colors.HexColor(color), strokeWidth=1))
                    else:
                        # Hachura diagonal simples: linhas paralelas em uma direcao
                        # Primeiro o fundo branco
                        d.add(Rect(ox, oy, box_w, box_h, fillColor=colors.white, 
                                   strokeColor=None, strokeWidth=0))
                        # Linhas diagonais /
                        for i in range(-box_h, box_w + box_h, spacing):
                            lx1, ly1 = ox + i, oy
                            lx2, ly2 = ox + i + box_h, oy + box_h
                            # Recortar manualmente
                            if lx1 < ox: ly1 += (ox - lx1); lx1 = ox
                            if lx2 > ox + box_w: ly2 -= (lx2 - ox - box_w); lx2 = ox + box_w
                            if ly1 >= oy and ly2 <= oy + box_h and lx1 <= lx2:
                                d.add(Line(lx1, ly1, lx2, ly2, 
                                          strokeColor=colors.HexColor(color), strokeWidth=0.5))
                        # Contorno por cima
                        d.add(Rect(ox, oy, box_w, box_h, fillColor=None, 
                                   strokeColor=colors.HexColor(color), strokeWidth=1))
                    
                    # Criar linha da legenda: [Simbolo | Nome]
                    legend_row = Table(
                        [[d, Paragraph(f"<b>{layer_name}</b>", ParagraphStyle("leg_txt", fontSize=8, leading=10))]],
                        colWidths=[box_w + 4, 50 * mm]
                    )
                    legend_row.setStyle(TableStyle([
                        ("ALIGN", (0, 0), (0, 0), "CENTER"),
                        ("ALIGN", (1, 0), (1, 0), "LEFT"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 1),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ]))
                    legend_rows.append(legend_row)
                
                # Adicionar fonte do basemap (na mesma linha)
                basemap_src = "PlanetScope" if self.planet_url else "ESRI Imagery"
                legend_rows.append(Spacer(1, 2 * mm))
                legend_rows.append(
                    Paragraph(
                        f'<font color="#666666"><b>Imagem: {basemap_src}</b></font>',
                        ParagraphStyle("legend_src", fontSize=7)
                    )
                )
                
                legend_content = legend_rows if legend_rows else [Paragraph("", small)]
                
                # Montar tabela: [Mapa1 | Mapa2 | Legenda]
                # Criar sub-tabelas para cada coluna
                map1_table = Table([[c] for c in map1_content], colWidths=[map_width_mm * mm])
                map1_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]))
                
                map2_table = Table([[c] for c in map2_content], colWidths=[map_width_mm * mm])
                map2_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]))
                
                # Altura fixa da legenda = altura do mapa
                legend_fixed_height = map_height_mm * mm  # Mesma altura que o mapa
                legend_col_width = w_total - 2 * map_width_mm * mm - 6 * mm
                
                # Criar tabela interna com os itens da legenda
                legend_inner = Table([[c] for c in legend_content], colWidths=[legend_col_width - 8])
                legend_inner.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]))
                
                # Tabela externa com altura fixa e borda
                legend_table = Table([[legend_inner]], colWidths=[legend_col_width], rowHeights=[legend_fixed_height])
                legend_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.4, 0.4, 0.4)),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.98, 0.98, 0.98)),
                ]))
                
                # Tabela principal com os 3 elementos
                main_map_table = Table(
                    [[map1_table, map2_table, legend_table]],
                    colWidths=[map_width_mm * mm + 2 * mm, map_width_mm * mm + 2 * mm, w_total - 2 * map_width_mm * mm - 4 * mm]
                )
                main_map_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]))
                
                story.append(main_map_table)
                story.append(Spacer(1, 1 * mm))
                
                # ---------------------------------------------------------------------
                # 5) Mapas de Localização (3 mapas lado a lado)
                # ---------------------------------------------------------------------
                loc_title_style = ParagraphStyle(
                    "loc_title", parent=section_title, alignment=1  # 1 = CENTER
                )
                loc_title = Paragraph('<font color="#1D3411"><b>Localização</b></font>', loc_title_style)
                loc_title_table = Table([[loc_title]], colWidths=[w_total])
                loc_title_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#C0E854")),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]))
                story.append(loc_title_table)
                story.append(Spacer(1, 1 * mm))
                
                # Dimensoes para 3 mapas lado a lado (+8% total)
                # Resolucao aumentada para melhor qualidade (igual aos mapas principais)
                loc_width_px = 428
                loc_height_px = 311
                loc_width_mm = 61
                loc_height_mm = 43
                
                # Gerar os 3 mapas de localização
                loc1_path = os.path.join(tempfile.gettempdir(), f"loc1_{car.replace('/', '_')}.png")
                loc2_path = os.path.join(tempfile.gettempdir(), f"loc2_{car.replace('/', '_')}.png")
                loc3_path = os.path.join(tempfile.gettempdir(), f"loc3_{car.replace('/', '_')}.png")
                
                loc1_ok = self._render_location_map(feat, loc1_path, loc_width_px, loc_height_px, 
                                                    'amazonia', 'Na Amazônia Legal')
                loc2_ok = self._render_location_map(feat, loc2_path, loc_width_px, loc_height_px, 
                                                    'estado', 'No Estado')
                loc3_ok = self._render_location_map(feat, loc3_path, loc_width_px, loc_height_px, 
                                                    'municipio', 'No Município')
                
                loc_cells = []
                if loc1_ok and os.path.exists(loc1_path):
                    loc_cells.append(RLImage(loc1_path, width=loc_width_mm * mm, height=loc_height_mm * mm))
                else:
                    loc_cells.append(Paragraph("Indisponível", small))
                
                if loc2_ok and os.path.exists(loc2_path):
                    loc_cells.append(RLImage(loc2_path, width=loc_width_mm * mm, height=loc_height_mm * mm))
                else:
                    loc_cells.append(Paragraph("Indisponível", small))
                
                if loc3_ok and os.path.exists(loc3_path):
                    loc_cells.append(RLImage(loc3_path, width=loc_width_mm * mm, height=loc_height_mm * mm))
                else:
                    loc_cells.append(Paragraph("Indisponível", small))
                
                # Tabela com os 3 mapas de localização
                # Calcular largura das colunas para distribuir o espaço
                loc_col_width = w_total / 3
                loc_table = Table([loc_cells], colWidths=[loc_col_width, loc_col_width, loc_col_width])
                loc_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]))
                story.append(loc_table)
                
            else:
                print(f"[Laudos] Mapas nao foram gerados para {car}")
                
        except Exception as e:
            print(f"[Laudos] Erro ao adicionar mapas: {e}")
            import traceback
            print(traceback.format_exc())

        # ---------------------------------------------------------------------
        # RODAPÉ: Imagem regua.png na base do documento
        # ---------------------------------------------------------------------
        try:
            regua_path = os.path.join(plugin_dir, "icone", "regua.png")
            if os.path.exists(regua_path):
                story.append(Spacer(1, 1 * mm))
                # Imagem proporcional ocupando toda a largura (altura ajusta automaticamente)
                regua_img = Image(regua_path, width=w_total, height=25*mm, kind='proportional')
                story.append(regua_img)
        except Exception as e:
            print(f"[Laudos] Erro ao adicionar rodape: {e}")

        # Retornar story se solicitado, senão construir o documento
        if return_story:
            # NÃO deletar arquivos temporários - serão usados quando doc.build() for chamado
            # Os arquivos serão limpos posteriormente pelo chamador
            return story
        else:
            doc.build(story)
            
            # Limpar arquivos temporarios dos mapas (apenas no modo normal)
            try:
                if 'map1_path' in locals() and os.path.exists(map1_path):
                    os.remove(map1_path)
                if 'map2_path' in locals() and os.path.exists(map2_path):
                    os.remove(map2_path)
            except:
                pass
            
            return None

    # -------------------------------------------------------------------------
    # Fallback simples
    # -------------------------------------------------------------------------
    def _gerar_pdf_simples(self, feat, filepath):
        txt_path = filepath.replace(".pdf", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            car = self._get_attr(feat, ["n_do_car", "cod_imovel"], "N/D")
            cpf = self._get_attr(feat, ["cpf_cnpj"], "N/D")
            municipio = self._get_attr(feat, ["municipio"], "N/D")
            uf = self._get_attr(feat, ["uf", "cod_estado"], "N/D")
            elegibilidade = self._get_attr(feat, ["elegibilidade"], "N/D")
            f.write("LAUDO DE ELEGIBILIDADE - FLORESTA+\n")
            f.write("=" * 60 + "\n")
            f.write(f"CAR: {car}\nCPF/CNPJ: {cpf}\nMunicípio/UF: {municipio}/{uf}\n")
            f.write(f"RESULTADO: {elegibilidade}\n")

    def _get_attr(self, feat, field_names, default=None):
        campos = [f.name() for f in feat.fields()]
        for nome in field_names:
            if nome in campos:
                valor = feat.attribute(nome)
                if valor is not None:
                    return valor
        return default

    def _adicionar_tabela_priorizacao(
        self, story, feat, w_total,
        small=None, small_center=None, header_white=None,
        header_white_center=None, crit_style=None, font_bold="Helvetica-Bold",
        BLUE_HEADER=None, GRID=None,
    ):
        """Adiciona tabela de critérios de priorização ao laudo (se houver dados)."""
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

        campos_camada = {f.name() for f in feat.fields()}
        if "score_priorizacao" not in campos_camada:
            return  # plugin processou sem priorização

        criterios = [
            ("A1", "Municípios prioritários (>50% área)", "prio_mun_prioritario", 1),
            ("A2", "Municípios desmate sob controle (>50% área)", "prio_mun_controle", 1),
            ("A3", "Municípios Programa União (>50% área)", "prio_mun_uniao", 1),
            ("A4", "Áreas prior. biodiversidade (intersect)", "prio_biodiversidade", 1),
            ("A5", "Entorno UC 3km (exceto APA/RPPN)", "prio_entorno_uc", 1),
            ("A6", "Sobreposto ≥50% APA/RPPN", "prio_apa_rppn", 1),
            ("A7", "Entorno TI 3km", "prio_entorno_ti", 1),
            ("A8", "Bioma Amazônia (IBGE)", "prio_bioma_amazonia", 1),
            ("P1", "Inscrito CAF/DAP-PRONAF", "prio_caf", 3),
            ("P2", "Proprietária sexo feminino", "prio_sexo_feminino", 3),
            ("P3", "Produtor sociobiodiversidade", "prio_sociobio", 3),
        ]

        score = self._get_attr(feat, ["score_priorizacao"], 0) or 0
        ranking = self._get_attr(feat, ["ranking"], 0) or 0
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = 0
        try:
            ranking = int(ranking)
        except (TypeError, ValueError):
            ranking = 0

        # Cabeçalho com título da seção
        titulo_prio = (
            f'<b>Critérios de Priorização (Item 6 do Edital)</b> &nbsp;&nbsp; '
            f'Score: <b>{score}</b>'
            + (f' &nbsp;&nbsp; Ranking: <b>{ranking}º</b>' if ranking > 0 else '')
        )
        story.append(Paragraph(titulo_prio, small or crit_style))
        story.append(Spacer(1, 1 * mm))

        OK = colors.HexColor("#27ae60")
        NA = colors.HexColor("#7f8c8d")

        table_data = [
            [
                Paragraph("<b>ID</b>", header_white_center),
                Paragraph("<b>Critério</b>", header_white),
                Paragraph("<b>Peso</b>", header_white_center),
                Paragraph("<b>Resultado</b>", header_white_center),
            ]
        ]

        for sigla, nome, campo, peso in criterios:
            valor = str(self._get_attr(feat, [campo], "Não") or "Não").strip()
            cor = OK if valor.lower() == "sim" else NA
            table_data.append([
                Paragraph(sigla, small_center),
                Paragraph(nome, crit_style),
                Paragraph(str(peso), small_center),
                Paragraph(f'<font color="{cor.hexval()}">{valor}</font>', small_center),
            ])

        tbl = Table(
            table_data,
            colWidths=[w_total * 0.08, w_total * 0.62, w_total * 0.10, w_total * 0.20],
        )
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE_HEADER),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), font_bold),
            ("FONTSIZE", (0, 0), (-1, 0), 7.5),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.3, GRID),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f8f8")]),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 1 * mm))

    def _render_map(self, feat, output_path, width_px=300, height_px=420, 
                     include_overlays=False, vegetation_only=False):
        """
        Renderiza um mapa com o limite do imovel sobre imagem satelite.
        
        Args:
            feat: QgsFeature com a geometria do imovel
            output_path: Caminho para salvar a imagem PNG
            width_px: Largura em pixels
            height_px: Altura em pixels
            include_overlays: Se True, inclui PRODES, UC, TI, Quilombolas, CNFP
            vegetation_only: Se True, mostra apenas vegetacao (rvn)
        
        Returns:
            dict com 'success', 'visible_layers' (lista de camadas visiveis)
        """
        visible_layers_info = []
        try:
            from qgis.core import (
                QgsMapRendererCustomPainterJob, QgsRuleBasedRenderer,
                QgsExpression, QgsFeatureRequest
            )
            
            geom = feat.geometry()
            if geom.isNull() or geom.isEmpty():
                print("[Laudos] Geometria vazia, mapa nao gerado")
                return {'success': False, 'visible_layers': []}

            # CRS Web Mercator (para tiles)
            crs_web = QgsCoordinateReferenceSystem("EPSG:3857")
            crs_orig = QgsCoordinateReferenceSystem("EPSG:4674")
            
            # Transformar geometria para Web Mercator
            transform = QgsCoordinateTransform(crs_orig, crs_web, QgsProject.instance())
            geom_transformed = QgsGeometry(geom)
            geom_transformed.transform(transform)

            # Criar camada temporaria com o limite do imovel
            mem_layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "limite_imovel", "memory")
            mem_provider = mem_layer.dataProvider()
            
            new_feat = QgsFeature()
            new_feat.setGeometry(geom_transformed)
            mem_provider.addFeature(new_feat)
            mem_layer.updateExtents()

            # Estilo do limite: contorno vermelho (linha mais grossa para destaque)
            symbol = QgsFillSymbol.createSimple({
                'color': '0,0,0,0',
                'outline_color': '#FF0000',
                'outline_width': '1.0'
            })
            mem_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            visible_layers_info.append(('Limite do Imóvel', '#FF0000', 'line'))

            # Lista de camadas para renderizar (sera reorganizada no final)
            render_layers = []
            layer_imovel = mem_layer  # Guardar referencia para adicionar no topo depois
            
            # Extent do imovel (em WGS84 para consulta espacial)
            geom_wgs84 = QgsGeometry(geom)
            transform_to_wgs = QgsCoordinateTransform(crs_orig, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            geom_wgs84.transform(transform_to_wgs)
            bbox_wgs84 = geom_wgs84.boundingBox()
            
            # Carregar camadas do GeoPackage se disponivel
            if self.gpkg_path and os.path.exists(self.gpkg_path):
                import sqlite3
                try:
                    conn = sqlite3.connect(self.gpkg_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
                    gpkg_layers = [row[0] for row in cursor.fetchall()]
                    conn.close()
                except:
                    gpkg_layers = []
                
                # Definicao de camadas overlay com estilo de hachura
                # (key, names, hatch_color, outline_color, display_name, is_overlay, is_veg, hatch_type)
                # hatch_type: 'x' = X, '/' = diagonal, 'solid' = preenchimento
                overlay_defs = [
                    ('cnfp', ['cnfp', 'florestapublicatipob'], '#00AA00', '#000000', 'Floresta Pública Tipo B', True, False, '/'),
                    ('prodes', ['prodes'], '#0066CC', '#0066CC', 'PRODES', True, False, 'x'),
                    ('uc', ['uc', 'unidades_conservacao', 'unidadesconservacao'], '#FFD700', '#DAA520', 'Unid. Conservação', True, False, '/'),
                    ('ti', ['ti', 'terras_indigenas', 'terrasindigenas'], '#FF0000', '#FF0000', 'Terra Indígena', True, False, '/'),
                    ('quilombolas', ['quilombolas', 'quilombola'], '#A9A9A9', '#808080', 'Território Quilombola', True, False, '/'),
                    ('rvn', ['rvn', 'vegetacaonativa', 'vegetacao_nativa'], '#00AA00', '#228B22', 'Vegetação Nativa', False, True, 'x'),
                ]
                
                for layer_key, possible_names, hatch_color, outline_color, display_name, is_overlay, is_vegetation, hatch_type in overlay_defs:
                    # Verificar se deve incluir esta camada
                    if is_overlay and not include_overlays:
                        continue
                    if is_vegetation and not vegetation_only:
                        continue
                    if not is_vegetation and vegetation_only:
                        continue
                    
                    # Encontrar nome real da camada
                    real_name = None
                    for name in possible_names:
                        if name.lower() in [l.lower() for l in gpkg_layers]:
                            real_name = next((l for l in gpkg_layers if l.lower() == name.lower()), None)
                            break
                    
                    if not real_name:
                        continue
                    
                    try:
                        uri = f"{self.gpkg_path}|layername={real_name}"
                        layer = QgsVectorLayer(uri, real_name, "ogr")
                        
                        if not layer.isValid():
                            continue
                        
                        # Filtrar features que intersectam o imovel
                        bbox_expanded = bbox_wgs84.buffered(0.01)
                        
                        # Criar camada memoria com features filtradas
                        geom_type = layer.geometryType()
                        if geom_type == 0:
                            mem_geom_type = "Point"
                        elif geom_type == 1:
                            mem_geom_type = "LineString"
                        else:
                            mem_geom_type = "Polygon"
                        
                        # Para CNFP, filtrar apenas Tipo B
                        filter_exp = None
                        if layer_key == 'cnfp':
                            filter_exp = "\"tipo\" = 'TIPO B'"
                        
                        mem_overlay = QgsVectorLayer(f"{mem_geom_type}?crs=EPSG:3857", display_name, "memory")
                        mem_prov = mem_overlay.dataProvider()
                        
                        request = QgsFeatureRequest().setFilterRect(bbox_expanded)
                        if filter_exp:
                            request.setFilterExpression(filter_exp)
                        
                        has_features = False
                        for f in layer.getFeatures(request):
                            f_geom = f.geometry()
                            if f_geom.isNull():
                                continue
                            f_geom_trans = QgsGeometry(f_geom)
                            transform_layer = QgsCoordinateTransform(layer.crs(), crs_web, QgsProject.instance())
                            f_geom_trans.transform(transform_layer)
                            
                            new_f = QgsFeature()
                            new_f.setGeometry(f_geom_trans)
                            mem_prov.addFeature(new_f)
                            has_features = True
                        
                        if has_features:
                            mem_overlay.updateExtents()
                            
                            # Criar simbolo com hachura usando QgsLinePatternFillSymbolLayer
                            from qgis.core import QgsLinePatternFillSymbolLayer, QgsSimpleLineSymbolLayer
                            
                            sym = QgsFillSymbol()
                            sym.deleteSymbolLayer(0)  # Remove camada padrao
                            
                            # Camada de hachura
                            if hatch_type == 'x':
                                # Hachura em X (duas diagonais)
                                hatch1 = QgsLinePatternFillSymbolLayer()
                                hatch1.setLineAngle(45)
                                hatch1.setDistance(2.0)
                                hatch1.setLineWidth(0.3)
                                hatch1.setColor(QColor(hatch_color))
                                sym.appendSymbolLayer(hatch1)
                                
                                hatch2 = QgsLinePatternFillSymbolLayer()
                                hatch2.setLineAngle(-45)
                                hatch2.setDistance(2.0)
                                hatch2.setLineWidth(0.3)
                                hatch2.setColor(QColor(hatch_color))
                                sym.appendSymbolLayer(hatch2)
                            else:
                                # Hachura diagonal simples
                                hatch = QgsLinePatternFillSymbolLayer()
                                hatch.setLineAngle(45)
                                hatch.setDistance(2.0)
                                hatch.setLineWidth(0.3)
                                hatch.setColor(QColor(hatch_color))
                                sym.appendSymbolLayer(hatch)
                            
                            # Contorno
                            outline = QgsSimpleLineSymbolLayer()
                            outline.setColor(QColor(outline_color))
                            outline.setWidth(0.3)
                            sym.appendSymbolLayer(outline)
                            
                            mem_overlay.setRenderer(QgsSingleSymbolRenderer(sym))
                            render_layers.append(mem_overlay)
                            visible_layers_info.append((display_name, hatch_color, hatch_type))
                            
                    except Exception as e:
                        print(f"[Laudos] Erro ao carregar {layer_key}: {e}")
                        import traceback
                        print(traceback.format_exc())
                        continue

            # Basemap: Planet (se disponivel) ou ESRI
            if self.planet_url:
                basemap_url = (
                    f"type=xyz&"
                    f"url={self.planet_url}&"
                    f"zmax=19&zmin=0"
                )
                basemap_layer = QgsRasterLayer(basemap_url, "Planet", "wms")
                basemap_name = "Planet"
            else:
                basemap_url = (
                    "type=xyz&"
                    "url=https://server.arcgisonline.com/ArcGIS/rest/services/"
                    "World_Imagery/MapServer/tile/{z}/{y}/{x}&"
                    "zmax=19&zmin=0"
                )
                basemap_layer = QgsRasterLayer(basemap_url, "ESRI_Satellite", "wms")
                basemap_name = "ESRI"
            
            if not basemap_layer.isValid():
                print(f"[Laudos] Falha ao carregar basemap {basemap_name}")
                return {'success': False, 'visible_layers': []}
            
            render_layers.append(basemap_layer)
            
            # Inserir limite do imovel no topo (primeiro da lista = renderizado por ultimo = no topo)
            render_layers.insert(0, layer_imovel)

            # Configurar extent com margem
            extent = layer_imovel.extent()
            extent.scale(1.3)

            # Configurar mapa
            settings = QgsMapSettings()
            settings.setLayers(render_layers)
            settings.setExtent(extent)
            settings.setOutputSize(QSize(width_px, height_px))
            settings.setBackgroundColor(QColor(255, 255, 255))
            settings.setDestinationCrs(crs_web)

            # Criar imagem e painter
            image = QImage(QSize(width_px, height_px), QImage.Format_ARGB32_Premultiplied)
            image.fill(QColor(255, 255, 255))
            
            painter = QPainter(image)
            
            render = QgsMapRendererCustomPainterJob(settings, painter)
            render.start()
            render.waitForFinished()
            
            painter.end()

            if image.isNull():
                print("[Laudos] Imagem renderizada esta vazia")
                return {'success': False, 'visible_layers': []}
                
            image.save(output_path, "PNG")
            print(f"[Laudos] Mapa salvo em: {output_path}")
            return {'success': True, 'visible_layers': visible_layers_info}

        except Exception as e:
            print(f"[Laudos] Erro ao renderizar mapa: {e}")
            import traceback
            print(traceback.format_exc())
            return {'success': False, 'visible_layers': []}

    def _render_location_map(self, feat, output_path, width_px, height_px, 
                              location_type='amazonia', label=''):
        """
        Renderiza mapa de localizacao com OpenStreetMap de fundo.
        
        Args:
            feat: QgsFeature com a geometria do imovel
            output_path: Caminho para salvar a imagem PNG
            width_px, height_px: Dimensoes em pixels
            location_type: 'amazonia', 'estado' ou 'municipio'
            label: Texto para exibir no mapa
        
        Returns:
            True se sucesso, False se falha
        """
        try:
            from qgis.core import QgsMapRendererCustomPainterJob
            import sqlite3
            
            geom = feat.geometry()
            if geom.isNull() or geom.isEmpty():
                return False

            crs_web = QgsCoordinateReferenceSystem("EPSG:3857")
            crs_orig = QgsCoordinateReferenceSystem("EPSG:4674")
            
            # Obter dados do imovel para filtros
            campos = [f.name() for f in feat.fields()]
            uf = None
            municipio = None
            for campo in ['uf', 'cod_estado', 'sigla_uf', 'estado', 'nm_uf', 'SIGLA_UF', 'UF']:
                if campo in campos:
                    val = feat.attribute(campo)
                    if val:
                        uf = val
                        break
            for campo in ['municipio', 'nom_munic', 'nm_mun', 'nome_municipio']:
                if campo in campos:
                    val = feat.attribute(campo)
                    if val:
                        municipio = val
                        break

            # Transformar geometria do imovel
            transform = QgsCoordinateTransform(crs_orig, crs_web, QgsProject.instance())
            geom_transformed = QgsGeometry(geom)
            geom_transformed.transform(transform)
            
            # Criar quadrado de evidencia baseado no centroide - tamanho fixo para cada tipo de mapa
            # Unidades em metros (EPSG:3857)
            centroid = geom_transformed.centroid().asPoint()
            
            # Tamanho do lado do quadrado em metros para cada tipo de mapa
            if location_type == 'amazonia':
                half_size = 150000  # 300km de lado (visivel na AMZL)
            elif location_type == 'estado':
                half_size = 50000   # 100km de lado (visivel no estado)
            else:
                half_size = 0       # Municipio nao usa retangulo
            
            # Criar quadrado perfeito em torno do centroide
            square_rect = QgsRectangle(
                centroid.x() - half_size,
                centroid.y() - half_size,
                centroid.x() + half_size,
                centroid.y() + half_size
            )
            
            rect_layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "evidencia", "memory")
            rect_prov = rect_layer.dataProvider()
            rect_feat = QgsFeature()
            rect_geom = QgsGeometry.fromRect(square_rect)
            rect_feat.setGeometry(rect_geom)
            rect_prov.addFeature(rect_feat)
            rect_layer.updateExtents()
            
            # Estilo do retangulo de evidencia (preto)
            rect_symbol = QgsFillSymbol.createSimple({
                'color': '0,0,0,0',
                'outline_color': '#000000',
                'outline_width': '0.8'
            })
            rect_layer.setRenderer(QgsSingleSymbolRenderer(rect_symbol))
            
            # Criar camada do imovel (contorno vermelho)
            imovel_layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "imovel", "memory")
            imovel_prov = imovel_layer.dataProvider()
            imovel_feat = QgsFeature()
            imovel_feat.setGeometry(geom_transformed)
            imovel_prov.addFeature(imovel_feat)
            imovel_layer.updateExtents()
            
            # Estilo do imovel (somente contorno vermelho, sem preenchimento)
            imovel_symbol = QgsFillSymbol.createSimple({
                'color': '0,0,0,0',
                'outline_color': '#FF0000',
                'outline_width': '1.0'
            })
            imovel_layer.setRenderer(QgsSingleSymbolRenderer(imovel_symbol))
            
            # Para municipio, nao mostrar retangulo preto
            if location_type == 'municipio':
                render_layers = [imovel_layer]
            else:
                render_layers = [imovel_layer, rect_layer]
            extent = None
            
            # Carregar camadas do GeoPackage conforme o tipo de localizacao
            if self.gpkg_path and os.path.exists(self.gpkg_path):
                try:
                    conn = sqlite3.connect(self.gpkg_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
                    gpkg_layers_orig = [row[0] for row in cursor.fetchall()]
                    gpkg_layers = [l.lower() for l in gpkg_layers_orig]
                    conn.close()
                except:
                    gpkg_layers = []
                    gpkg_layers_orig = []
                
                def find_layer_name(search_names):
                    """Encontra o nome original da camada no geopackage"""
                    for name in search_names:
                        for i, layer in enumerate(gpkg_layers):
                            if name == layer:
                                return gpkg_layers_orig[i]
                    return None
                
                if location_type == 'amazonia':
                    from qgis.core import (
                        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
                        QgsTextFormat, QgsTextBufferSettings
                    )
                    
                    # Camadas auxiliares para reordenar no final
                    uf_mem_layer = None
                    amz_mem_layer = None
                    
                    # Carregar estados COM rótulos
                    uf_names = ['uf', 'ufs', 'estados', 'unidades_federacao', 'unidadesfederacao']
                    real_uf_name = find_layer_name(uf_names)
                    if real_uf_name:
                        uri = f"{self.gpkg_path}|layername={real_uf_name}"
                        uf_layer = QgsVectorLayer(uri, "estados", "ogr")
                        if uf_layer.isValid():
                            uf_mem = QgsVectorLayer("Polygon?crs=EPSG:3857", "ufs", "memory")
                            uf_prov = uf_mem.dataProvider()
                            uf_prov.addAttributes(uf_layer.fields())
                            uf_mem.updateFields()
                            trans = QgsCoordinateTransform(uf_layer.crs(), crs_web, QgsProject.instance())
                            for f in uf_layer.getFeatures():
                                g = QgsGeometry(f.geometry())
                                g.transform(trans)
                                nf = QgsFeature(uf_layer.fields())
                                nf.setGeometry(g)
                                nf.setAttributes(f.attributes())
                                uf_prov.addFeature(nf)
                            uf_mem.updateExtents()
                            uf_sym = QgsFillSymbol.createSimple({
                                'color': '0,0,0,0',
                                'outline_color': '#606060',
                                'outline_width': '0.3'
                            })
                            uf_mem.setRenderer(QgsSingleSymbolRenderer(uf_sym))
                            
                            # Labels com sigla dos estados
                            label_settings = QgsPalLayerSettings()
                            label_settings.fieldName = 'sigla'
                            label_settings.enabled = True
                            text_format = QgsTextFormat()
                            text_format.setSize(7)
                            text_format.setColor(QColor(60, 60, 60))
                            buffer_settings = QgsTextBufferSettings()
                            buffer_settings.setEnabled(True)
                            buffer_settings.setSize(0.8)
                            buffer_settings.setColor(QColor(255, 255, 255))
                            text_format.setBuffer(buffer_settings)
                            label_settings.setFormat(text_format)
                            uf_mem.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
                            uf_mem.setLabelsEnabled(True)
                            
                            uf_mem_layer = uf_mem
                    
                    # Carregar AMZL
                    amz_names = ['amazonia_legal', 'amazonialegal', 'amz_legal', 'amazonia']
                    real_amz_name = find_layer_name(amz_names)
                    if real_amz_name:
                        uri = f"{self.gpkg_path}|layername={real_amz_name}"
                        amz_layer = QgsVectorLayer(uri, "amazonia", "ogr")
                        if amz_layer.isValid():
                            amz_mem = QgsVectorLayer("Polygon?crs=EPSG:3857", "amz", "memory")
                            amz_prov = amz_mem.dataProvider()
                            trans = QgsCoordinateTransform(amz_layer.crs(), crs_web, QgsProject.instance())
                            for f in amz_layer.getFeatures():
                                g = QgsGeometry(f.geometry())
                                g.transform(trans)
                                nf = QgsFeature()
                                nf.setGeometry(g)
                                amz_prov.addFeature(nf)
                            amz_mem.updateExtents()
                            # Linha verde fina para AMZL
                            amz_sym = QgsFillSymbol.createSimple({
                                'color': '0,0,0,0',
                                'outline_color': '#228B22',
                                'outline_width': '0.5'
                            })
                            amz_mem.setRenderer(QgsSingleSymbolRenderer(amz_sym))
                            amz_mem_layer = amz_mem
                            extent = amz_mem.extent()
                    
                    # Reordenar: [imovel, rect, amzl, estados] (primeiro = topo)
                    render_layers = [imovel_layer, rect_layer]
                    if amz_mem_layer:
                        render_layers.append(amz_mem_layer)
                    if uf_mem_layer:
                        render_layers.append(uf_mem_layer)
                
                elif location_type == 'estado':
                    # Camadas auxiliares
                    uf_mem_layer = None
                    mun_mem_layer = None
                    
                    # Carregar estado especifico
                    uf_names = ['uf', 'ufs', 'estados', 'unidades_federacao', 'unidadesfederacao']
                    real_uf_name = find_layer_name(uf_names)
                    if real_uf_name:
                        uri = f"{self.gpkg_path}|layername={real_uf_name}"
                        uf_layer = QgsVectorLayer(uri, "estado", "ogr")
                        if uf_layer.isValid():
                            uf_mem = QgsVectorLayer("Polygon?crs=EPSG:3857", "estado", "memory")
                            uf_prov = uf_mem.dataProvider()
                            trans = QgsCoordinateTransform(uf_layer.crs(), crs_web, QgsProject.instance())
                            trans_orig = QgsCoordinateTransform(crs_orig, uf_layer.crs(), QgsProject.instance())
                            
                            # Centroide do imovel para busca espacial
                            imovel_centroid = geom.centroid()
                            imovel_centroid.transform(trans_orig)
                            
                            for f in uf_layer.getFeatures():
                                f_uf = None
                                for col in ['sigla', 'uf', 'sigla_uf', 'cod_uf', 'SIGLA_UF', 'UF']:
                                    try:
                                        f_uf = f.attribute(col)
                                        if f_uf:
                                            break
                                    except:
                                        continue
                                
                                g = QgsGeometry(f.geometry())
                                g.transform(trans)
                                nf = QgsFeature()
                                nf.setGeometry(g)
                                uf_prov.addFeature(nf)
                                
                                # Tentar match por atributo
                                if f_uf and uf and str(f_uf).upper() == str(uf).upper():
                                    extent = g.boundingBox()
                                # Fallback: match espacial (estado que contem o imovel)
                                elif extent is None and f.geometry().contains(imovel_centroid):
                                    extent = g.boundingBox()
                            
                            uf_mem.updateExtents()
                            uf_sym = QgsFillSymbol.createSimple({
                                'color': '0,0,0,0',
                                'outline_color': '#404040',
                                'outline_width': '0.5'
                            })
                            uf_mem.setRenderer(QgsSingleSymbolRenderer(uf_sym))
                            uf_mem_layer = uf_mem
                    
                    # Carregar municipios SEM rótulos para o mapa de estado
                    mun_names = ['municipios', 'municipio', 'mun']
                    real_mun_name = find_layer_name(mun_names)
                    if real_mun_name:
                        uri = f"{self.gpkg_path}|layername={real_mun_name}"
                        mun_layer = QgsVectorLayer(uri, "municipios", "ogr")
                        if mun_layer.isValid():
                            mun_mem = QgsVectorLayer("Polygon?crs=EPSG:3857", "muns", "memory")
                            mun_prov = mun_mem.dataProvider()
                            trans = QgsCoordinateTransform(mun_layer.crs(), crs_web, QgsProject.instance())
                            for f in mun_layer.getFeatures():
                                g = QgsGeometry(f.geometry())
                                g.transform(trans)
                                nf = QgsFeature()
                                nf.setGeometry(g)
                                mun_prov.addFeature(nf)
                            mun_mem.updateExtents()
                            
                            # Linha bem fina cinza (sem rótulos)
                            mun_sym = QgsFillSymbol.createSimple({
                                'color': '0,0,0,0',
                                'outline_color': '#999999',
                                'outline_width': '0.2'
                            })
                            mun_mem.setRenderer(QgsSingleSymbolRenderer(mun_sym))
                            mun_mem_layer = mun_mem
                    
                    # Reordenar: [imovel, rect, mun, uf] (primeiro = topo)
                    render_layers = [imovel_layer, rect_layer]
                    if mun_mem_layer:
                        render_layers.append(mun_mem_layer)
                    if uf_mem_layer:
                        render_layers.append(uf_mem_layer)
                
                elif location_type == 'municipio':
                    from qgis.core import (
                        QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
                        QgsTextFormat, QgsTextBufferSettings
                    )
                    
                    # Carregar municipios COM rótulos
                    mun_names = ['municipios', 'municipio', 'mun']
                    real_mun_name = find_layer_name(mun_names)
                    if real_mun_name:
                        uri = f"{self.gpkg_path}|layername={real_mun_name}"
                        mun_layer = QgsVectorLayer(uri, "municipio", "ogr")
                        if mun_layer.isValid():
                            mun_mem = QgsVectorLayer("Polygon?crs=EPSG:3857", "mun", "memory")
                            mun_prov = mun_mem.dataProvider()
                            mun_prov.addAttributes(mun_layer.fields())
                            mun_mem.updateFields()
                            trans = QgsCoordinateTransform(mun_layer.crs(), crs_web, QgsProject.instance())
                            
                            for f in mun_layer.getFeatures():
                                g = QgsGeometry(f.geometry())
                                g.transform(trans)
                                nf = QgsFeature(mun_layer.fields())
                                nf.setGeometry(g)
                                nf.setAttributes(f.attributes())
                                mun_prov.addFeature(nf)
                            
                            mun_mem.updateExtents()
                            mun_sym = QgsFillSymbol.createSimple({
                                'color': '0,0,0,0',
                                'outline_color': '#404040',
                                'outline_width': '0.3'
                            })
                            mun_mem.setRenderer(QgsSingleSymbolRenderer(mun_sym))
                            
                            # Labels com nome dos municipios
                            label_settings = QgsPalLayerSettings()
                            label_settings.fieldName = 'nome'
                            label_settings.enabled = True
                            text_format = QgsTextFormat()
                            text_format.setSize(9)  # Fonte maior para melhor leitura
                            text_format.setColor(QColor(40, 40, 40))
                            buffer_settings = QgsTextBufferSettings()
                            buffer_settings.setEnabled(True)
                            buffer_settings.setSize(0.8)
                            buffer_settings.setColor(QColor(255, 255, 255))
                            text_format.setBuffer(buffer_settings)
                            label_settings.setFormat(text_format)
                            mun_mem.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
                            mun_mem.setLabelsEnabled(True)
                            
                            render_layers.append(mun_mem)
            
            # Definir extent conforme o tipo de mapa
            if location_type == 'amazonia':
                # Usar extent da Amazonia Legal
                if extent is not None:
                    extent.scale(1.05)
            elif location_type == 'estado':
                # Zoom FIXO para estado - 600km x 600km centrado no imovel
                view_half = 300000  # 300km de cada lado = 600km total
                extent = QgsRectangle(
                    centroid.x() - view_half,
                    centroid.y() - view_half,
                    centroid.x() + view_half,
                    centroid.y() + view_half
                )
            elif location_type == 'municipio':
                # Zoom proximo - usar extent do imovel
                extent = imovel_layer.extent()
                extent.scale(25.0)  # Zoom mais afastado do imovel
                
                # Deslocar o mapa para baixo (imóvel aparece mais acima)
                # Isso evita sobreposição com o label do município no centro
                altura_extent = extent.height()
                offset_y = altura_extent * 0.15  # Desloca 15% da altura para baixo
                extent = QgsRectangle(
                    extent.xMinimum(),
                    extent.yMinimum() - offset_y,
                    extent.xMaximum(),
                    extent.yMaximum() - offset_y
                )
            
            # Fallback se nao encontrou extent
            if extent is None:
                extent = rect_layer.extent()
                extent.scale(5.0)
            
            # OpenStreetMap como fundo
            osm_url = (
                "type=xyz&"
                "url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&"
                "zmax=19&zmin=0"
            )
            osm_layer = QgsRasterLayer(osm_url, "OSM", "wms")
            if osm_layer.isValid():
                render_layers.append(osm_layer)
            
            # Configurar mapa
            settings = QgsMapSettings()
            settings.setLayers(render_layers)
            settings.setExtent(extent)
            settings.setOutputSize(QSize(width_px, height_px))
            settings.setBackgroundColor(QColor(255, 255, 255))
            settings.setDestinationCrs(crs_web)

            # Criar imagem
            image = QImage(QSize(width_px, height_px), QImage.Format_ARGB32_Premultiplied)
            image.fill(QColor(255, 255, 255))
            
            painter = QPainter(image)
            render = QgsMapRendererCustomPainterJob(settings, painter)
            render.start()
            render.waitForFinished()
            painter.end()

            if image.isNull():
                return False
            
            image.save(output_path, "PNG")
            
            # Adicionar label usando PIL (texto preto com halo branco)
            if label:
                try:
                    from PIL import Image as PILImage, ImageDraw, ImageFont
                    img = PILImage.open(output_path)
                    draw = ImageDraw.Draw(img)
                    try:
                        font = ImageFont.truetype("arial.ttf", 14)
                    except:
                        font = ImageFont.load_default()
                    # Texto preto com halo branco para melhor visibilidade
                    x, y = 6, 4
                    # Desenhar halo branco
                    for dx in [-2, -1, 0, 1, 2]:
                        for dy in [-2, -1, 0, 1, 2]:
                            if dx != 0 or dy != 0:
                                draw.text((x + dx, y + dy), label, fill=(255, 255, 255), font=font)
                    # Desenhar texto preto
                    draw.text((x, y), label, fill=(0, 0, 0), font=font)
                    # Adicionar borda preta fina
                    w, h = img.size
                    draw.rectangle([0, 0, w-1, h-1], outline=(0, 0, 0))
                    img.save(output_path)
                except Exception as e:
                    print(f"[Laudos] Erro ao adicionar label: {e}")
            
            return True

        except Exception as e:
            print(f"[Laudos] Erro ao renderizar mapa de localizacao: {e}")
            import traceback
            print(traceback.format_exc())
            return False


class GeradorLaudosDialog(QDialog):
    """Diálogo para configuração e geração de laudos em PDF."""

    COLORS = {
        "verde_escuro": "#1a472a",
        "verde_medio": "#2d5a3d",
        "verde_claro": "#4a7c59",
        "branco": "#ffffff",
    }

    def __init__(self, layer: QgsVectorLayer, gpkg_path: str = None, planet_url: str = None, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.gpkg_path = gpkg_path
        self.planet_url = planet_url
        self.features_selecionados = []
        self.thread = None

        self.setWindowTitle("Gerador de Laudos PDF - Floresta+")
        self.setMinimumSize(520, 480)
        self.setFixedWidth(520)

        self._setup_ui()
        self._atualizar_contagem()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # === SELEÇÃO DE IMÓVEIS ===
        selecao_group = QGroupBox("Seleção de Imóveis")
        selecao_group.setStyleSheet(self._get_groupbox_style())
        selecao_group.setMinimumHeight(220)
        selecao_layout = QVBoxLayout(selecao_group)

        self.radio_todos = QRadioButton("Todos os imóveis processados")
        self.radio_todos.setChecked(True)
        self.radio_todos.toggled.connect(self._atualizar_contagem)
        selecao_layout.addWidget(self.radio_todos)

        self.radio_elegiveis_f1 = QRadioButton("Apenas elegíveis Fase 1")
        self.radio_elegiveis_f1.toggled.connect(self._atualizar_contagem)
        selecao_layout.addWidget(self.radio_elegiveis_f1)

        self.radio_elegiveis_f2 = QRadioButton("Apenas elegíveis Fase 2")
        self.radio_elegiveis_f2.toggled.connect(self._atualizar_contagem)
        selecao_layout.addWidget(self.radio_elegiveis_f2)

        self.radio_elegiveis = QRadioButton("Elegíveis em qualquer fase")
        self.radio_elegiveis.toggled.connect(self._atualizar_contagem)
        selecao_layout.addWidget(self.radio_elegiveis)

        self.radio_inelegiveis = QRadioButton("Apenas inelegíveis")
        self.radio_inelegiveis.toggled.connect(self._atualizar_contagem)
        selecao_layout.addWidget(self.radio_inelegiveis)

        # Opção de CAR específico
        car_layout = QHBoxLayout()
        self.radio_car_especifico = QRadioButton("CAR específico:")
        self.radio_car_especifico.toggled.connect(self._atualizar_contagem)
        self.radio_car_especifico.toggled.connect(self._toggle_car_input)
        car_layout.addWidget(self.radio_car_especifico)

        self.txt_car_especifico = QLineEdit()
        self.txt_car_especifico.setPlaceholderText("Digite o código do CAR...")
        self.txt_car_especifico.setEnabled(False)
        self.txt_car_especifico.textChanged.connect(self._atualizar_contagem)
        car_layout.addWidget(self.txt_car_especifico)
        selecao_layout.addLayout(car_layout)

        self.lbl_contagem = QLabel("Laudos a gerar: -")
        self.lbl_contagem.setStyleSheet("font-weight: bold; margin-top: 10px;")
        selecao_layout.addWidget(self.lbl_contagem)

        layout.addWidget(selecao_group)

        # === PASTA DE DESTINO ===
        destino_group = QGroupBox("Pasta de Destino")
        destino_group.setStyleSheet(self._get_groupbox_style())
        destino_layout = QHBoxLayout(destino_group)

        self.txt_pasta = QLineEdit()
        self.txt_pasta.setPlaceholderText("Selecione a pasta para salvar os PDFs...")
        self.txt_pasta.setReadOnly(True)
        destino_layout.addWidget(self.txt_pasta)

        btn_selecionar = QPushButton("📁 Selecionar")
        btn_selecionar.clicked.connect(self._selecionar_pasta)
        btn_selecionar.setStyleSheet(self._get_button_style())
        destino_layout.addWidget(btn_selecionar)

        layout.addWidget(destino_group)

        # === OPÇÕES ===
        opcoes_group = QGroupBox("Opções")
        opcoes_group.setStyleSheet(self._get_groupbox_style())
        opcoes_layout = QVBoxLayout(opcoes_group)

        self.chk_abrir_pasta = QCheckBox("Abrir pasta após conclusão")
        self.chk_abrir_pasta.setChecked(True)
        opcoes_layout.addWidget(self.chk_abrir_pasta)

        layout.addWidget(opcoes_group)

        # === PROGRESSO ===
        self.progress_group = QGroupBox("Progresso")
        self.progress_group.setStyleSheet(self._get_groupbox_style())
        self.progress_group.setVisible(False)
        progress_layout = QVBoxLayout(self.progress_group)

        self.progress_bar = QProgressBar()
        progress_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("font-style: italic; color: #666;")
        progress_layout.addWidget(self.lbl_status)

        layout.addWidget(self.progress_group)

        # === BOTÕES ===
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_gerar = QPushButton("📄 Gerar Laudos")
        self.btn_gerar.clicked.connect(self._iniciar_geracao)
        self.btn_gerar.setStyleSheet(self._get_button_style())
        self.btn_gerar.setEnabled(False)
        btn_layout.addWidget(self.btn_gerar)

        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.clicked.connect(self._cancelar)
        self.btn_cancelar.setStyleSheet(self._get_button_style())
        btn_layout.addWidget(self.btn_cancelar)

        layout.addLayout(btn_layout)

    def _selecionar_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self,
            "Selecionar Pasta de Destino",
            "",
            QFileDialog.ShowDirsOnly
        )

        if pasta:
            self.txt_pasta.setText(pasta)
            self.btn_gerar.setEnabled(True)

    def _toggle_car_input(self, checked):
        self.txt_car_especifico.setEnabled(checked)
        if checked:
            self.txt_car_especifico.setFocus()

    def _atualizar_contagem(self):
        self.features_selecionados = self._filtrar_features()
        self.lbl_contagem.setText(f"Laudos a gerar: {len(self.features_selecionados)}")

    def _filtrar_features(self):
        from qgis.core import QgsFeature
        features = []

        car_busca = ""
        if self.radio_car_especifico.isChecked():
            car_busca = self.txt_car_especifico.text().strip().upper()

        for feat in self.layer.getFeatures():
            elegibilidade = self._get_attr(feat, ["elegibilidade"], "")

            is_elegivel_f1 = elegibilidade == "Fase 1"
            is_elegivel_f2 = elegibilidade == "Fase 2"
            is_inelegivel = (elegibilidade == "Inelegível") or (elegibilidade not in ["Fase 1", "Fase 2"])

            # Criar cópia da feature para manter dados válidos na thread
            feat_copy = QgsFeature(feat)

            if self.radio_car_especifico.isChecked():
                car = self._get_attr(feat, ["n_do_car", "cod_imovel"], "").upper()
                if car_busca and car_busca in car:
                    features.append(feat_copy)
                continue

            if self.radio_todos.isChecked():
                features.append(feat_copy)
            elif self.radio_elegiveis_f1.isChecked() and is_elegivel_f1:
                features.append(feat_copy)
            elif self.radio_elegiveis_f2.isChecked() and is_elegivel_f2:
                features.append(feat_copy)
            elif self.radio_elegiveis.isChecked() and (is_elegivel_f1 or is_elegivel_f2):
                features.append(feat_copy)
            elif self.radio_inelegiveis.isChecked() and is_inelegivel:
                features.append(feat_copy)

        return features

    def _iniciar_geracao(self):
        pasta = self.txt_pasta.text()

        if not pasta or not os.path.isdir(pasta):
            QMessageBox.warning(self, "Pasta Inválida", "Selecione uma pasta válida para salvar os laudos.")
            return

        if not self.features_selecionados:
            QMessageBox.warning(self, "Nenhum Imóvel", "Não há imóveis para gerar laudos com o filtro selecionado.")
            return

        # Verificar ReportLab
        try:
            import reportlab  # noqa
        except ImportError:
            resp = QMessageBox.warning(
                self,
                "ReportLab não instalado",
                "A biblioteca ReportLab é necessária para gerar PDFs.\n\n"
                "Para instalar, abra o Console Python do QGIS e execute:\n\n"
                "import subprocess, sys\n"
                "subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'reportlab'])\n\n"
                "Ou no OSGeo4W Shell:\n"
                "pip install reportlab\n\n"
                "Deseja tentar instalar automaticamente?",
                QMessageBox.Yes | QMessageBox.No
            )
            if resp == QMessageBox.Yes:
                try:
                    import subprocess
                    import sys
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])
                    QMessageBox.information(
                        self,
                        "Instalação concluída",
                        "ReportLab foi instalado com sucesso!\n\nReinicie o QGIS e tente novamente."
                    )
                except Exception as e:
                    QMessageBox.critical(
                        self,
                        "Erro na instalação",
                        f"Não foi possível instalar automaticamente:\n{str(e)}\n\nTente instalar manualmente."
                    )
            return

        resp = QMessageBox.question(
            self,
            "Confirmar Geração",
            f"Serão gerados {len(self.features_selecionados)} laudos.\n\n"
            f"Pasta de destino:\n{pasta}\n\nDeseja continuar?",
            QMessageBox.Yes | QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return

        self.progress_group.setVisible(True)
        self.progress_bar.setMaximum(len(self.features_selecionados))
        self.progress_bar.setValue(0)
        self.btn_gerar.setEnabled(False)

        self.thread = GeradorLaudosThread(
            self.layer,
            self.features_selecionados,
            pasta,
            self.gpkg_path,
            self.planet_url
        )
        self.thread.progress.connect(self._atualizar_progresso)
        self.thread.finished.connect(self._geracao_concluida)
        self.thread.error.connect(self._erro_geracao)
        self.thread.start()

    def _atualizar_progresso(self, atual, total, msg):
        self.progress_bar.setValue(atual)
        self.lbl_status.setText(msg)

    def _geracao_concluida(self, sucesso, falhas):
        self.btn_gerar.setEnabled(True)
        self.lbl_status.setText("Concluído!")

        msg = "Geração concluída!\n\n"
        msg += f"Laudos gerados com sucesso: {sucesso}\n"
        if falhas > 0:
            msg += f"Falhas: {falhas}"

        QMessageBox.information(self, "Geração Concluída", msg)

        if self.chk_abrir_pasta.isChecked():
            pasta = self.txt_pasta.text()
            if pasta and os.path.isdir(pasta):
                os.startfile(pasta)

    def _erro_geracao(self, erro):
        QMessageBox.critical(self, "Erro na Geração", f"Ocorreu um erro durante a geração:\n\n{erro}")
        self.btn_gerar.setEnabled(True)

    def _cancelar(self):
        if self.thread and self.thread.isRunning():
            self.thread.cancelar = True
            self.thread.wait()
        self.close()

    def _get_attr(self, feat, field_names, default=None):
        campos = [f.name() for f in feat.fields()]
        for nome in field_names:
            if nome in campos:
                valor = feat.attribute(nome)
                if valor is not None:
                    return valor
        return default

    def _get_groupbox_style(self):
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {self.COLORS['verde_medio']};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: {self.COLORS['branco']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {self.COLORS['verde_escuro']};
            }}
        """

    def _get_button_style(self):
        return f"""
            QPushButton {{
                background-color: {self.COLORS['verde_medio']};
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {self.COLORS['verde_claro']};
            }}
            QPushButton:pressed {{
                background-color: {self.COLORS['verde_escuro']};
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
                color: #888888;
            }}
        """
