# -*- coding: utf-8 -*-
"""
Widget de gerenciamento de listas de municípios.

Apresenta um QComboBox para selecionar qual lista editar, um campo de busca
(por nome ou geocódigo) e uma lista (QListWidget) com checkboxes para todos
os municípios da camada 'municipios' do GeoPackage.

Listas gerenciadas:
  - Municípios Prioritários (Fase 1)         → 81 padrão
  - Desmatamento Monitorado e Sob Controle  → 10 padrão
  - Programa União com Municípios            → 70 padrão (= 81 - 11)
"""

import os
from typing import Dict, List, Set

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)


# Identificadores e rótulos das listas gerenciadas
LISTA_PRIORITARIOS = "geocodigos_prioritarios"
LISTA_DESMATE_CONTROLE = "geocodigos_desmate_controle"
LISTA_PROGRAMA_UNIAO = "geocodigos_programa_uniao"

LISTAS_INFO = [
    (LISTA_PRIORITARIOS, "Municípios Prioritários (Fase 1) — Elegibilidade + A1"),
    (LISTA_DESMATE_CONTROLE, "Desmatamento Monitorado e Sob Controle — A2"),
    (LISTA_PROGRAMA_UNIAO, "Programa União com Municípios — A3"),
]


class ListasMunicipiosWidget(QWidget):
    """Widget para gerenciar listas de municípios usadas em elegibilidade/priorização."""

    def __init__(self, gpkg_path: str, listas_iniciais: Dict[str, Set[str]], parent=None):
        """
        Args:
            gpkg_path: caminho para o GeoPackage com a camada 'municipios'.
            listas_iniciais: dict {nome_lista: set(geocodigos)} com os valores atuais.
        """
        super().__init__(parent)
        self.gpkg_path = gpkg_path
        # Trabalhar em cópia para permitir cancelamento sem efeito colateral
        self.listas: Dict[str, Set[str]] = {
            chave: set(listas_iniciais.get(chave, set())) for chave, _ in LISTAS_INFO
        }
        self._municipios: List[Dict[str, str]] = []  # lista completa [{geo, nome, uf}]
        self._construir_ui()
        self._carregar_municipios()
        self._popular_lista()

    # ------------------------------------------------------------------ #
    # Construção da UI                                                    #
    # ------------------------------------------------------------------ #

    def _construir_ui(self):
        layout = QVBoxLayout(self)

        # Seletor de lista
        topo = QHBoxLayout()
        topo.addWidget(QLabel("Lista:"))
        self.combo_lista = QComboBox()
        for chave, rotulo in LISTAS_INFO:
            self.combo_lista.addItem(rotulo, chave)
        self.combo_lista.currentIndexChanged.connect(self._on_mudou_lista)
        topo.addWidget(self.combo_lista, 1)
        layout.addLayout(topo)

        # Aviso explicativo
        info = QLabel(
            "Edite a lista marcando/desmarcando municípios. As listas vêm pré-preenchidas "
            "com valores conhecidos e podem ser ajustadas a qualquer momento."
        )
        info.setStyleSheet("font-size: 9px; color: #555;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Busca
        busca_layout = QHBoxLayout()
        busca_layout.addWidget(QLabel("Buscar:"))
        self.edit_busca = QLineEdit()
        self.edit_busca.setPlaceholderText("Digite nome ou geocódigo...")
        self.edit_busca.textChanged.connect(self._filtrar)
        busca_layout.addWidget(self.edit_busca, 1)
        layout.addLayout(busca_layout)

        # Lista de municípios
        self.lista_widget = QListWidget()
        self.lista_widget.setUniformItemSizes(True)
        self.lista_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.lista_widget, 1)

        # Botões de ação
        botoes = QHBoxLayout()
        self.lbl_contador = QLabel()
        self.lbl_contador.setStyleSheet("font-weight: bold;")
        botoes.addWidget(self.lbl_contador)
        botoes.addStretch()

        self.btn_marcar_padrao = QPushButton("Lista Padrão")
        self.btn_marcar_padrao.setToolTip("Restaura a lista padrão pré-preenchida")
        self.btn_marcar_padrao.clicked.connect(self._restaurar_padrao)
        botoes.addWidget(self.btn_marcar_padrao)

        self.btn_marcar_filtro = QPushButton("Marcar Filtrados")
        self.btn_marcar_filtro.setToolTip("Marca todos os municípios visíveis (filtrados)")
        self.btn_marcar_filtro.clicked.connect(lambda: self._marcar_visiveis(True))
        botoes.addWidget(self.btn_marcar_filtro)

        self.btn_desmarcar_filtro = QPushButton("Desmarcar Filtrados")
        self.btn_desmarcar_filtro.setToolTip("Desmarca todos os municípios visíveis")
        self.btn_desmarcar_filtro.clicked.connect(lambda: self._marcar_visiveis(False))
        botoes.addWidget(self.btn_desmarcar_filtro)

        self.btn_limpar = QPushButton("Limpar Tudo")
        self.btn_limpar.setToolTip("Desmarca todos os municípios da lista atual")
        self.btn_limpar.clicked.connect(self._limpar_lista)
        botoes.addWidget(self.btn_limpar)

        layout.addLayout(botoes)

    # ------------------------------------------------------------------ #
    # Carregamento de municípios do GeoPackage                            #
    # ------------------------------------------------------------------ #

    def _carregar_municipios(self):
        """Carrega lista de municípios da camada 'municipios' do GeoPackage."""
        if not self.gpkg_path or not os.path.exists(self.gpkg_path):
            self._municipios = []
            return
        try:
            from qgis.core import QgsVectorLayer
        except ImportError:
            return

        uri = f"{self.gpkg_path}|layername=municipios"
        layer = QgsVectorLayer(uri, "municipios", "ogr")
        if not layer.isValid():
            self._municipios = []
            return

        # Detectar campos
        campos = [f.name() for f in layer.fields()]
        campos_lower = {c.lower(): c for c in campos}
        campo_geo = None
        campo_nome = None
        campo_uf = None

        for nome in ("geocodigo", "cod_municipio", "codmun", "cd_mun", "cd_geocmu", "ibge"):
            if nome in campos_lower:
                campo_geo = campos_lower[nome]
                break
        for nome in ("nome", "nm_mun", "municipio", "nome_municipio", "nm_municip"):
            if nome in campos_lower:
                campo_nome = campos_lower[nome]
                break
        for nome in ("uf", "sigla_uf", "sg_uf", "estado"):
            if nome in campos_lower:
                campo_uf = campos_lower[nome]
                break

        if not campo_geo or not campo_nome:
            self._municipios = []
            return

        municipios = []
        for feat in layer.getFeatures():
            geo = str(feat.attribute(campo_geo) or "").strip()
            nome = str(feat.attribute(campo_nome) or "").strip()
            uf = str(feat.attribute(campo_uf) or "").strip() if campo_uf else ""
            if not geo or not nome:
                continue
            municipios.append({"geo": geo, "nome": nome, "uf": uf})

        municipios.sort(key=lambda m: (m["uf"], m["nome"]))
        self._municipios = municipios

    # ------------------------------------------------------------------ #
    # Popular e atualizar a lista                                          #
    # ------------------------------------------------------------------ #

    def _lista_atual(self) -> str:
        return self.combo_lista.currentData() or LISTA_PRIORITARIOS

    def _popular_lista(self):
        """Popula o QListWidget com todos os municípios."""
        self.lista_widget.blockSignals(True)
        self.lista_widget.clear()

        chave_lista = self._lista_atual()
        selecionados = self.listas.get(chave_lista, set())

        if not self._municipios:
            item = QListWidgetItem(
                "⚠ Camada 'municipios' não encontrada no GeoPackage. "
                "Carregue a camada para gerenciar as listas."
            )
            item.setFlags(Qt.NoItemFlags)
            item.setForeground(QColor("#c0392b"))
            self.lista_widget.addItem(item)
            self.lista_widget.blockSignals(False)
            self._atualizar_contador()
            return

        for mun in self._municipios:
            geo = mun["geo"]
            uf = f" ({mun['uf']})" if mun["uf"] else ""
            texto = f"{geo} - {mun['nome']}{uf}"
            item = QListWidgetItem(texto)
            item.setData(Qt.UserRole, geo)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if geo in selecionados else Qt.Unchecked)
            self.lista_widget.addItem(item)

        self.lista_widget.blockSignals(False)
        self._filtrar()
        self._atualizar_contador()

    def _filtrar(self):
        """Filtra municípios pelo texto de busca."""
        termo = self.edit_busca.text().strip().lower()
        for i in range(self.lista_widget.count()):
            item = self.lista_widget.item(i)
            if not termo:
                item.setHidden(False)
            else:
                texto = item.text().lower()
                item.setHidden(termo not in texto)

    def _atualizar_contador(self):
        chave = self._lista_atual()
        n = len(self.listas.get(chave, set()))
        total = len(self._municipios)
        self.lbl_contador.setText(f"Selecionados: {n} de {total} municípios")

    # ------------------------------------------------------------------ #
    # Eventos                                                              #
    # ------------------------------------------------------------------ #

    def _on_mudou_lista(self):
        self._popular_lista()

    def _on_item_changed(self, item: QListWidgetItem):
        geo = item.data(Qt.UserRole)
        if not geo:
            return
        chave = self._lista_atual()
        s = self.listas.setdefault(chave, set())
        if item.checkState() == Qt.Checked:
            s.add(geo)
        else:
            s.discard(geo)
        self._atualizar_contador()

    # ------------------------------------------------------------------ #
    # Ações em massa                                                       #
    # ------------------------------------------------------------------ #

    def _marcar_visiveis(self, marcar: bool):
        self.lista_widget.blockSignals(True)
        chave = self._lista_atual()
        s = self.listas.setdefault(chave, set())
        for i in range(self.lista_widget.count()):
            item = self.lista_widget.item(i)
            if item.isHidden():
                continue
            geo = item.data(Qt.UserRole)
            if not geo:
                continue
            if marcar:
                s.add(geo)
                item.setCheckState(Qt.Checked)
            else:
                s.discard(geo)
                item.setCheckState(Qt.Unchecked)
        self.lista_widget.blockSignals(False)
        self._atualizar_contador()

    def _limpar_lista(self):
        self.lista_widget.blockSignals(True)
        chave = self._lista_atual()
        self.listas[chave] = set()
        for i in range(self.lista_widget.count()):
            item = self.lista_widget.item(i)
            item.setCheckState(Qt.Unchecked)
        self.lista_widget.blockSignals(False)
        self._atualizar_contador()

    def _restaurar_padrao(self):
        from Plugin_FMais.core.processamento_elegiveis import ConfigProcessamento
        cfg_default = ConfigProcessamento()
        chave = self._lista_atual()
        padrao = set(getattr(cfg_default, chave, set()))
        self.listas[chave] = padrao
        self._popular_lista()

    # ------------------------------------------------------------------ #
    # API pública                                                          #
    # ------------------------------------------------------------------ #

    def get_listas(self) -> Dict[str, Set[str]]:
        """Retorna dicionário com as 3 listas finais {chave: set(geocodigos)}."""
        return {chave: set(s) for chave, s in self.listas.items()}
