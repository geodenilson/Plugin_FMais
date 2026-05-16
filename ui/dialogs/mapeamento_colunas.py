# -*- coding: utf-8 -*-
"""
Diálogo de Mapeamento de Colunas - Plugin Floresta+
Permite ao usuário mapear as colunas da sua camada de imóveis
para os campos esperados pelo processamento.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLabel,
    QPushButton, QComboBox, QScrollArea, QWidget, QFrame, QMessageBox,
    QSizePolicy
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QColor, QIcon


COLUNAS_ESPERADAS = [
    ("n_do_car",    "Código do CAR",           True),
    ("idt_car",     "Identificador do CAR",    False),
    ("nom_comple",  "Nome do proprietário",    False),
    ("cpf_cnpj",    "CPF / CNPJ",             False),
    ("nome_imove",  "Nome do imóvel",          False),
    ("area_imove",  "Área do imóvel (ha)",     False),
    ("tipo_imove",  "Tipo do imóvel",          False),
    ("status",      "Status do imóvel",        False),
    ("modulo_f",    "Módulo fiscal",           False),
    ("sum_modulo",  "Soma de módulos (CPF)",   False),
    ("condicao",    "Condição de análise",     False),
    ("documentos",  "Documentos",              False),
    ("municipio",   "Município",               False),
    ("uf",          "UF (Estado)",             False),
    ("nom_lograd",  "Logradouro",              False),
    ("nom_bairro",  "Bairro",                  False),
]

ALIASES_CONHECIDOS = {
    "n_do_car":    ["n_do_car", "cod_imovel", "car", "codigo_car", "cod_car", "num_car"],
    "idt_car":     ["idt_car", "id_car", "idt"],
    "nom_comple":  ["nom_comple", "nome_comple", "proprietario", "nome_prop", "nome"],
    "cpf_cnpj":    ["cpf_cnpj", "cpf", "cnpj", "cpfcnpj", "cpf_cnpj_1"],
    "nome_imove":  ["nome_imove", "nom_imovel", "nome_imovel"],
    "area_imove":  ["area_imove", "num_area", "area", "area_ha", "area_hectares", "area_imovel"],
    "tipo_imove":  ["tipo_imove", "ind_tipo", "tipo", "tipo_imovel"],
    "status":      ["status", "ind_status", "status_imo", "situacao", "status_imovel", "status_imove"],
    "modulo_f":    ["modulo_f", "m_fiscal", "mod_fiscal", "modulo_fiscal", "mf"],
    "sum_modulo":  ["sum_modulo", "soma_modulos", "soma_mf"],
    "condicao":    ["condicao", "des_condic", "condicao_analise", "condicao_imovel"],
    "documentos":  ["documentos", "docs", "tipo_doc", "documento"],
    "municipio":   ["municipio", "nom_munici", "nome_municipio", "mun"],
    "uf":          ["uf", "sigla_uf", "estado", "cod_estado"],
    "nom_lograd":  ["nom_lograd", "logradouro", "endereco"],
    "nom_bairro":  ["nom_bairro", "bairro", "localidade"],
}


class DialogoMapeamentoColunas(QDialog):
    """Diálogo para mapeamento interativo das colunas da camada de imóveis."""

    COLORS = {
        'verde_escuro': '#1a472a',
        'verde_medio': '#2d5a3d',
        'verde_claro': '#4a7c59',
        'fundo': '#f0f4f0',
        'texto': '#1a1a1a',
        'elegivel': '#27ae60',
        'nao_elegivel': '#e74c3c',
        'aviso': '#f39c12',
        'branco': '#ffffff',
    }

    def __init__(self, colunas_layer: list, parent=None):
        """
        Args:
            colunas_layer: lista de nomes de campo presentes na camada de imóveis
        """
        super().__init__(parent)
        self.colunas_layer = colunas_layer
        self.combos = {}
        self.resultado_mapeamento = {}

        self.setWindowTitle("Floresta+ — Mapeamento de Colunas")
        self.setMinimumWidth(620)
        self.setMaximumHeight(700)
        self._setup_ui()
        self._auto_detectar()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel(
            "A camada de imóveis selecionada não possui todas as colunas no formato padrão.\n"
            "Indique abaixo a correspondência entre os campos esperados e os campos disponíveis.\n"
            "Campos sem correspondência podem ficar como \"(não disponível)\"."
        )
        header.setWordWrap(True)
        header.setStyleSheet("color: #333; font-size: 11px; padding: 4px;")
        main_layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_widget = QWidget()
        grid = QGridLayout(scroll_widget)
        grid.setSpacing(6)
        grid.setContentsMargins(4, 4, 4, 4)

        lbl_esperado = QLabel("Campo esperado")
        lbl_esperado.setFont(QFont("Segoe UI", 9, QFont.Bold))
        lbl_layer = QLabel("Coluna na sua camada")
        lbl_layer.setFont(QFont("Segoe UI", 9, QFont.Bold))
        grid.addWidget(lbl_esperado, 0, 0)
        grid.addWidget(lbl_layer, 0, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #ccc;")
        grid.addWidget(sep, 1, 0, 1, 2)

        row = 2
        for campo_interno, descricao, obrigatorio in COLUNAS_ESPERADAS:
            texto = descricao
            if obrigatorio:
                texto += "  *"
            lbl = QLabel(texto)
            lbl.setStyleSheet("font-size: 10px;")
            if obrigatorio:
                lbl.setStyleSheet("font-size: 10px; font-weight: bold; color: #1a472a;")

            combo = QComboBox()
            combo.setMinimumWidth(240)
            combo.addItem("(não disponível)", "")
            for col in sorted(self.colunas_layer):
                combo.addItem(col, col)
            combo.setStyleSheet("font-size: 10px; padding: 2px;")

            grid.addWidget(lbl, row, 0)
            grid.addWidget(combo, row, 1)
            self.combos[campo_interno] = combo
            row += 1

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll, 1)

        legenda = QLabel("* Campo recomendado para processamento completo")
        legenda.setStyleSheet("color: #888; font-size: 9px; font-style: italic;")
        main_layout.addWidget(legenda)

        info = QLabel()
        self.info_label = info
        info.setWordWrap(True)
        info.setStyleSheet(
            "background: #fff8e1; border: 1px solid #ffe082; border-radius: 4px; "
            "padding: 6px; font-size: 10px; color: #6d4c00;"
        )
        info.setVisible(False)
        main_layout.addWidget(info)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.setMinimumWidth(100)
        btn_cancelar.setStyleSheet(
            "QPushButton { padding: 6px 16px; border: 1px solid #ccc; "
            "border-radius: 4px; font-size: 10px; }"
            "QPushButton:hover { background: #eee; }"
        )
        btn_cancelar.clicked.connect(self.reject)

        btn_ok = QPushButton("Confirmar e Processar")
        btn_ok.setMinimumWidth(160)
        btn_ok.setStyleSheet(
            f"QPushButton {{ padding: 6px 16px; background: {self.COLORS['verde_escuro']}; "
            f"color: white; border-radius: 4px; font-size: 10px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {self.COLORS['verde_medio']}; }}"
        )
        btn_ok.clicked.connect(self._confirmar)

        btn_layout.addWidget(btn_cancelar)
        btn_layout.addWidget(btn_ok)
        main_layout.addLayout(btn_layout)

    def _auto_detectar(self):
        """Tenta detectar automaticamente as correspondências."""
        colunas_lower = {c.lower(): c for c in self.colunas_layer}

        for campo_interno, aliases in ALIASES_CONHECIDOS.items():
            if campo_interno not in self.combos:
                continue
            combo = self.combos[campo_interno]
            encontrado = False
            for alias in aliases:
                if alias.lower() in colunas_lower:
                    real_name = colunas_lower[alias.lower()]
                    idx = combo.findData(real_name)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                        encontrado = True
                        break
            if not encontrado:
                combo.setCurrentIndex(0)

        self._atualizar_info()

    def _atualizar_info(self):
        mapeados = 0
        total = len(COLUNAS_ESPERADAS)
        for campo_interno, _, _ in COLUNAS_ESPERADAS:
            combo = self.combos.get(campo_interno)
            if combo and combo.currentData():
                mapeados += 1

        if mapeados == total:
            self.info_label.setVisible(False)
        else:
            faltam = total - mapeados
            self.info_label.setText(
                f"ℹ {mapeados} de {total} campos mapeados. "
                f"{faltam} campo(s) sem correspondência — as análises que dependem "
                f"desses campos serão limitadas, mas o processamento prosseguirá normalmente."
            )
            self.info_label.setVisible(True)

    def _confirmar(self):
        self.resultado_mapeamento = {}
        for campo_interno, _, _ in COLUNAS_ESPERADAS:
            combo = self.combos.get(campo_interno)
            if combo:
                valor = combo.currentData()
                if valor:
                    self.resultado_mapeamento[campo_interno] = valor

        self.accept()

    def get_mapeamento(self) -> dict:
        """Retorna o mapeamento {campo_esperado: campo_real_na_layer}."""
        return self.resultado_mapeamento
