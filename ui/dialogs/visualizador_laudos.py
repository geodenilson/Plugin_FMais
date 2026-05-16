# -*- coding: utf-8 -*-
"""
Visualizador de Laudos - Plugin Floresta+
Janela de visualização detalhada dos resultados de elegibilidade
"""

import os
import subprocess
import sys
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QPushButton, QLineEdit, QComboBox, QScrollArea, QWidget,
    QFrame, QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QSizePolicy, QTextEdit, QTabWidget, QFileDialog,
    QProgressDialog, QApplication
)
from qgis.PyQt.QtCore import Qt, QSize
from qgis.PyQt.QtGui import QFont, QColor, QIcon
from qgis.core import QgsVectorLayer, QgsFeatureRequest


class VisualizadorLaudosDialog(QDialog):
    """Diálogo para visualização detalhada dos resultados de elegibilidade."""
    
    # Cores do tema
    COLORS = {
        'verde_escuro': '#1a472a',
        'verde_medio': '#2d5a3d',
        'verde_claro': '#4a7c59',
        'verde_lima': '#90EE90',
        'fundo': '#f0f4f0',
        'texto': '#1a1a1a',
        'texto_claro': '#666666',
        'elegivel': '#27ae60',
        'nao_elegivel': '#e74c3c',
        'nd': '#f39c12',
        'branco': '#ffffff',
    }
    
    # Variável de classe para lembrar última pasta usada (persiste entre instâncias)
    _ultima_pasta_salva = None
    
    def __init__(self, layer: QgsVectorLayer, gpkg_path=None, planet_url=None, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.gpkg_path = gpkg_path
        self.planet_url = planet_url
        self.features_filtrados = []
        self.indice_atual = 0
        self.output_dir = None  # Pasta de destino para laudos em lote
        
        self.setWindowTitle("Visualizador de Resultados - Floresta+")
        self.setMinimumSize(1200, 760)
        self.resize(1400, 850)
        
        self._setup_ui()
        self._carregar_dados()
        self._atualizar_estatisticas()
    
    def _setup_ui(self):
        """Configura a interface do diálogo."""
        layout = QHBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # === PAINEL ESQUERDO: Filtros + Estatísticas ===
        painel_esquerdo = QWidget()
        painel_esquerdo.setFixedWidth(320)
        painel_esquerdo_layout = QVBoxLayout(painel_esquerdo)
        painel_esquerdo_layout.setContentsMargins(0, 0, 0, 0)
        painel_esquerdo_layout.setSpacing(6)
        
        # --- FILTROS E BUSCA ---
        filtros_group = QGroupBox("🔍 Filtros e Busca")
        filtros_group.setStyleSheet(self._get_groupbox_style())
        filtros_layout = QVBoxLayout(filtros_group)
        filtros_layout.setSpacing(6)
        
        # Busca por CAR
        filtros_layout.addWidget(QLabel("Código CAR:"))
        self.txt_busca_car = QLineEdit()
        self.txt_busca_car.setPlaceholderText("Digite o código do CAR...")
        self.txt_busca_car.textChanged.connect(self._aplicar_filtros)
        filtros_layout.addWidget(self.txt_busca_car)
        
        # Busca por CPF
        filtros_layout.addWidget(QLabel("CPF/CNPJ:"))
        self.txt_busca_cpf = QLineEdit()
        self.txt_busca_cpf.setPlaceholderText("Digite o CPF ou CNPJ...")
        self.txt_busca_cpf.textChanged.connect(self._aplicar_filtros)
        filtros_layout.addWidget(self.txt_busca_cpf)
        
        # Filtro por elegibilidade
        filtros_layout.addWidget(QLabel("Elegibilidade:"))
        self.cmb_elegibilidade = QComboBox()
        self.cmb_elegibilidade.addItems([
            "Todos",
            "Elegível F1",
            "Elegível F2",
            "Elegível F1 ou F2",
            "Inelegível (ambas)",
        ])
        self.cmb_elegibilidade.currentIndexChanged.connect(self._aplicar_filtros)
        filtros_layout.addWidget(self.cmb_elegibilidade)
        
        # Botão limpar filtros
        btn_limpar = QPushButton("🔄 Limpar Filtros")
        btn_limpar.clicked.connect(self._limpar_filtros)
        btn_limpar.setStyleSheet(self._get_button_style())
        filtros_layout.addWidget(btn_limpar)
        
        # Separador visual
        filtros_layout.addWidget(self._criar_separador())
        
        # --- GERAÇÃO DE LAUDOS ---
        filtros_layout.addWidget(QLabel("<b>Gerar Laudos PDF</b>"))
        
        # Pasta de destino
        pasta_layout = QHBoxLayout()
        self.lbl_pasta_destino = QLineEdit()
        self.lbl_pasta_destino.setPlaceholderText("Selecione a pasta de destino...")
        self.lbl_pasta_destino.setReadOnly(True)
        pasta_layout.addWidget(self.lbl_pasta_destino)
        
        btn_selecionar_pasta = QPushButton("📁")
        btn_selecionar_pasta.setToolTip("Selecionar pasta de destino")
        btn_selecionar_pasta.setFixedWidth(30)
        btn_selecionar_pasta.clicked.connect(self._selecionar_pasta_destino)
        btn_selecionar_pasta.setStyleSheet(self._get_button_style())
        pasta_layout.addWidget(btn_selecionar_pasta)
        filtros_layout.addLayout(pasta_layout)
        
        # Botão gerar laudos
        self.btn_gerar_laudos = QPushButton("📄 Gerar Laudos (Filtrados)")
        self.btn_gerar_laudos.setToolTip("Gerar laudos em PDF para os imóveis filtrados")
        self.btn_gerar_laudos.clicked.connect(self._gerar_laudos_filtrados)
        self.btn_gerar_laudos.setStyleSheet(self._get_button_style_destaque())
        self.btn_gerar_laudos.setEnabled(False)
        filtros_layout.addWidget(self.btn_gerar_laudos)
        
        # Status da geração
        self.lbl_status_geracao = QLabel("")
        self.lbl_status_geracao.setStyleSheet("color: #666; font-size: 9px;")
        self.lbl_status_geracao.setWordWrap(True)
        filtros_layout.addWidget(self.lbl_status_geracao)
        
        painel_esquerdo_layout.addWidget(filtros_group)
        
        # --- ESTATÍSTICAS ---
        stats_group = QGroupBox("📊 Estatísticas")
        stats_group.setStyleSheet(self._get_groupbox_style())
        stats_layout = QVBoxLayout(stats_group)
        stats_layout.setSpacing(3)
        
        self.lbl_total_imoveis = QLabel("Total de imóveis: -")
        self.lbl_total_imoveis.setFont(QFont("Arial", 9, QFont.Bold))
        stats_layout.addWidget(self.lbl_total_imoveis)
        
        self.lbl_filtrados = QLabel("Filtrados: -")
        stats_layout.addWidget(self.lbl_filtrados)
        
        # Elegibilidade
        self.lbl_elegiveis_f1 = QLabel("✓ Elegíveis Fase 1: -")
        self.lbl_elegiveis_f1.setStyleSheet(f"color: {self.COLORS['elegivel']};")
        stats_layout.addWidget(self.lbl_elegiveis_f1)
        
        self.lbl_elegiveis_f2 = QLabel("✓ Elegíveis Fase 2: -")
        self.lbl_elegiveis_f2.setStyleSheet(f"color: {self.COLORS['elegivel']};")
        stats_layout.addWidget(self.lbl_elegiveis_f2)
        
        self.lbl_inelegiveis = QLabel("✗ Inelegíveis: -")
        self.lbl_inelegiveis.setStyleSheet(f"color: {self.COLORS['nao_elegivel']};")
        stats_layout.addWidget(self.lbl_inelegiveis)
        
        # Separador
        stats_layout.addWidget(self._criar_separador())
        
        # Valores financeiros
        self.lbl_valor_total = QLabel("💰 Valor Total: R$ -")
        self.lbl_valor_total.setFont(QFont("Arial", 9, QFont.Bold))
        self.lbl_valor_total.setStyleSheet(f"color: {self.COLORS['verde_escuro']};")
        stats_layout.addWidget(self.lbl_valor_total)
        
        self.lbl_valor_f1 = QLabel("   Fase 1: R$ -")
        stats_layout.addWidget(self.lbl_valor_f1)
        
        self.lbl_valor_f2 = QLabel("   Fase 2: R$ -")
        stats_layout.addWidget(self.lbl_valor_f2)
        
        # Separador
        stats_layout.addWidget(self._criar_separador())
        
        # Área total RVN
        self.lbl_area_rvn = QLabel("🌳 Área RVN Total: - ha")
        self.lbl_area_rvn.setFont(QFont("Arial", 9, QFont.Bold))
        stats_layout.addWidget(self.lbl_area_rvn)
        
        self.lbl_area_floresta = QLabel("   Floresta: - ha")
        stats_layout.addWidget(self.lbl_area_floresta)
        
        self.lbl_area_cerrado = QLabel("   Cerrado: - ha")
        stats_layout.addWidget(self.lbl_area_cerrado)
        
        self.lbl_area_campo = QLabel("   Campo: - ha")
        stats_layout.addWidget(self.lbl_area_campo)
        
        painel_esquerdo_layout.addWidget(stats_group)
        
        # --- MOTIVOS DE INELEGIBILIDADE ---
        motivos_group = QGroupBox("⚠️ Motivos de Inelegibilidade")
        motivos_group.setStyleSheet(self._get_groupbox_style())
        motivos_layout = QVBoxLayout(motivos_group)
        
        self.txt_motivos = QTextEdit()
        self.txt_motivos.setReadOnly(True)
        self.txt_motivos.setStyleSheet("font-size: 9px;")
        motivos_layout.addWidget(self.txt_motivos)
        
        # Motivos ocupa todo o espaço restante
        painel_esquerdo_layout.addWidget(motivos_group, 1)  # stretch = 1
        
        layout.addWidget(painel_esquerdo)
        
        # === PAINEL DIREITO: Detalhes do Imóvel (ocupa todo o espaço restante) ===
        painel_direito = QWidget()
        painel_direito_layout = QVBoxLayout(painel_direito)
        painel_direito_layout.setContentsMargins(0, 0, 0, 0)
        painel_direito_layout.setSpacing(6)
        
        # Navegação
        nav_widget = QWidget()
        nav_widget.setStyleSheet(f"background-color: {self.COLORS['verde_medio']}; border-radius: 5px;")
        nav_layout = QHBoxLayout(nav_widget)
        nav_layout.setContentsMargins(10, 6, 10, 6)
        
        self.btn_anterior = QPushButton("◀ Anterior")
        self.btn_anterior.clicked.connect(self._imovel_anterior)
        self.btn_anterior.setStyleSheet(self._get_nav_button_style())
        nav_layout.addWidget(self.btn_anterior)
        
        nav_layout.addStretch()
        
        self.lbl_navegacao = QLabel("0 / 0")
        self.lbl_navegacao.setAlignment(Qt.AlignCenter)
        self.lbl_navegacao.setFont(QFont("Arial", 12, QFont.Bold))
        self.lbl_navegacao.setStyleSheet("color: white;")
        nav_layout.addWidget(self.lbl_navegacao)
        
        nav_layout.addStretch()
        
        self.btn_proximo = QPushButton("Próximo ▶")
        self.btn_proximo.clicked.connect(self._imovel_proximo)
        self.btn_proximo.setStyleSheet(self._get_nav_button_style())
        nav_layout.addWidget(self.btn_proximo)
        
        # Separador visual
        nav_layout.addSpacing(20)
        
        # Botão Gerar Laudo do imóvel atual
        self.btn_salvar_pdf_atual = QPushButton("📄 Gerar Laudo")
        self.btn_salvar_pdf_atual.setToolTip("Gerar laudo em PDF do imóvel atual")
        self.btn_salvar_pdf_atual.clicked.connect(self._salvar_pdf_atual)
        self.btn_salvar_pdf_atual.setStyleSheet(f"""
            QPushButton {{
                background-color: #e67e22;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #d35400;
            }}
            QPushButton:pressed {{
                background-color: #a04000;
            }}
            QPushButton:disabled {{
                background-color: #888888;
                color: #cccccc;
            }}
        """)
        nav_layout.addWidget(self.btn_salvar_pdf_atual)
        
        painel_direito_layout.addWidget(nav_widget)
        
        # Área de detalhes com scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { 
                border: 1px solid #ccc; 
                border-radius: 5px;
                background-color: white;
            }
        """)
        
        self.widget_detalhes = QWidget()
        self.widget_detalhes.setStyleSheet("background-color: white;")
        self.layout_detalhes = QVBoxLayout(self.widget_detalhes)
        self.layout_detalhes.setSpacing(8)
        self.layout_detalhes.setContentsMargins(10, 10, 10, 10)
        
        scroll.setWidget(self.widget_detalhes)
        painel_direito_layout.addWidget(scroll)
        
        layout.addWidget(painel_direito, stretch=1)
    
    def _criar_separador(self):
        """Cria uma linha separadora."""
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #ddd;")
        return line
    
    def _get_nav_button_style(self):
        """Estilo dos botões de navegação."""
        return f"""
            QPushButton {{
                background-color: {self.COLORS['verde_claro']};
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {self.COLORS['verde_lima']};
                color: {self.COLORS['verde_escuro']};
            }}
            QPushButton:pressed {{
                background-color: {self.COLORS['verde_escuro']};
            }}
            QPushButton:disabled {{
                background-color: #888888;
                color: #cccccc;
            }}
        """
    
    def _carregar_dados(self):
        """Carrega todos os dados da camada."""
        self.features_filtrados = list(self.layer.getFeatures())
        self.indice_atual = 0
        self._atualizar_navegacao()
        self._mostrar_imovel_atual()
        
        # Atualizar botão de gerar laudos
        self.btn_gerar_laudos.setEnabled(self.output_dir is not None and len(self.features_filtrados) > 0)
        if self.output_dir:
            self.lbl_status_geracao.setText(f"Prontos para gerar: {len(self.features_filtrados)} laudos")
    
    def _aplicar_filtros(self):
        """Aplica os filtros selecionados."""
        busca_car = self.txt_busca_car.text().strip().upper()
        busca_cpf = self.txt_busca_cpf.text().strip().replace(".", "").replace("-", "").replace("/", "")
        elegibilidade_filtro = self.cmb_elegibilidade.currentIndex()
        
        self.features_filtrados = []
        
        for feat in self.layer.getFeatures():
            # Filtro por CAR
            car = self._get_attr(feat, ["n_do_car", "cod_imovel"], "").upper()
            if busca_car and busca_car not in car:
                continue
            
            # Filtro por CPF
            cpf = str(self._get_attr(feat, ["cpf_cnpj"], "")).replace(".", "").replace("-", "").replace("/", "")
            if busca_cpf and busca_cpf not in cpf:
                continue
            
            # Filtro por elegibilidade - usar coluna 'elegibilidade' com valores: "Fase 1", "Fase 2", "Inelegível"
            elegibilidade = self._get_attr(feat, ["elegibilidade"], "")
            
            is_elegivel_f1 = elegibilidade == "Fase 1"
            is_elegivel_f2 = elegibilidade == "Fase 2"
            is_inelegivel = elegibilidade == "Inelegível" or elegibilidade not in ["Fase 1", "Fase 2"]
            
            if elegibilidade_filtro == 1 and not is_elegivel_f1:  # Elegível F1
                continue
            elif elegibilidade_filtro == 2 and not is_elegivel_f2:  # Elegível F2
                continue
            elif elegibilidade_filtro == 3 and not (is_elegivel_f1 or is_elegivel_f2):  # F1 ou F2
                continue
            elif elegibilidade_filtro == 4 and not is_inelegivel:  # Inelegível
                continue
            
            self.features_filtrados.append(feat)
        
        self.indice_atual = 0
        self._atualizar_navegacao()
        self._atualizar_estatisticas()
        self._mostrar_imovel_atual()
        
        # Atualizar botão de gerar laudos
        self.btn_gerar_laudos.setEnabled(self.output_dir is not None and len(self.features_filtrados) > 0)
        if self.output_dir:
            self.lbl_status_geracao.setText(f"Prontos para gerar: {len(self.features_filtrados)} laudos")
    
    def _limpar_filtros(self):
        """Limpa todos os filtros."""
        self.txt_busca_car.clear()
        self.txt_busca_cpf.clear()
        self.cmb_elegibilidade.setCurrentIndex(0)
        self._carregar_dados()
        self._atualizar_estatisticas()
    
    def _atualizar_navegacao(self):
        """Atualiza os controles de navegação."""
        total = len(self.features_filtrados)
        atual = self.indice_atual + 1 if total > 0 else 0
        
        self.lbl_navegacao.setText(f"{atual} / {total}")
        self.btn_anterior.setEnabled(self.indice_atual > 0)
        self.btn_proximo.setEnabled(self.indice_atual < total - 1)
        self.btn_salvar_pdf_atual.setEnabled(total > 0)
    
    def _imovel_anterior(self):
        """Navega para o imóvel anterior."""
        if self.indice_atual > 0:
            self.indice_atual -= 1
            self._atualizar_navegacao()
            self._mostrar_imovel_atual()
    
    def _imovel_proximo(self):
        """Navega para o próximo imóvel."""
        if self.indice_atual < len(self.features_filtrados) - 1:
            self.indice_atual += 1
            self._atualizar_navegacao()
            self._mostrar_imovel_atual()
    
    def _formatar_numero_br(self, valor, casas_decimais=2):
        """Formata número no padrão brasileiro (ponto para milhar, vírgula para decimal)."""
        if valor == 0:
            return "0,00" if casas_decimais == 2 else "0"
        
        # Formatar com casas decimais
        numero_str = f"{valor:,.{casas_decimais}f}"
        # Trocar temporariamente
        numero_str = numero_str.replace(",", "X").replace(".", ",").replace("X", ".")
        return numero_str
    
    def _formatar_inteiro_br(self, valor):
        """Formata inteiro no padrão brasileiro (ponto para milhar)."""
        return f"{valor:,}".replace(",", ".")
    
    def _atualizar_estatisticas(self):
        """Atualiza as estatísticas com base nos imóveis filtrados."""
        total = self.layer.featureCount()
        filtrados = len(self.features_filtrados)
        
        elegiveis_f1 = 0
        elegiveis_f2 = 0
        inelegiveis = 0
        valor_total = 0
        valor_f1 = 0
        valor_f2 = 0
        area_rvn = 0
        area_floresta = 0
        area_cerrado = 0
        area_campo = 0
        motivos = {}
        
        for feat in self.features_filtrados:
            # Elegibilidade - usar coluna 'elegibilidade' que tem: "Fase 1", "Fase 2" ou "Inelegível"
            elegibilidade = self._get_attr(feat, ["elegibilidade"], "")
            
            if elegibilidade == "Fase 1":
                elegiveis_f1 += 1
                # Somar valores dos elegíveis Fase 1
                valor_rec = self._get_attr(feat, ["Total_rec"], 0) or 0
                valor_f1 += valor_rec
                valor_total += valor_rec
            elif elegibilidade == "Fase 2":
                elegiveis_f2 += 1
                # Somar valores dos elegíveis Fase 2
                valor_rec = self._get_attr(feat, ["Total_rec"], 0) or 0
                valor_f2 += valor_rec
                valor_total += valor_rec
            else:
                inelegiveis += 1
                # Extrair motivos de inelegibilidade das colunas Elegivel_F1 e Elegivel_F2
                eleg_f1 = self._get_attr(feat, ["Elegivel_F1"], "")
                if eleg_f1 and eleg_f1 not in ["SIM", "Elegível", ""]:
                    for motivo in str(eleg_f1).split(","):
                        motivo = motivo.strip()
                        if motivo and motivo not in ["Não Elegível", "NAO"]:
                            motivos[motivo] = motivos.get(motivo, 0) + 1
            
            # Áreas (para todos)
            area_rvn += self._get_attr(feat, ["RVN_area"], 0) or 0
            area_floresta += self._get_attr(feat, ["rvn_floresta"], 0) or 0
            area_cerrado += self._get_attr(feat, ["rvn_cerrado"], 0) or 0
            area_campo += self._get_attr(feat, ["rvn_campo"], 0) or 0
        
        # Total de elegíveis
        total_elegiveis = elegiveis_f1 + elegiveis_f2
        
        # Atualizar labels (com separador de milhar)
        self.lbl_total_imoveis.setText(f"Total de imóveis: {self._formatar_inteiro_br(total)}")
        self.lbl_filtrados.setText(f"Filtrados: {self._formatar_inteiro_br(filtrados)}")
        
        self.lbl_elegiveis_f1.setText(f"✓ Elegíveis Fase 1: {self._formatar_inteiro_br(elegiveis_f1)}")
        self.lbl_elegiveis_f2.setText(f"✓ Elegíveis Fase 2: {self._formatar_inteiro_br(elegiveis_f2)}")
        self.lbl_inelegiveis.setText(f"✗ Inelegíveis: {self._formatar_inteiro_br(inelegiveis)}")
        
        self.lbl_valor_total.setText(f"💰 Valor Total: R$ {self._formatar_numero_br(valor_total)}")
        self.lbl_valor_f1.setText(f"   Fase 1: R$ {self._formatar_numero_br(valor_f1)}")
        self.lbl_valor_f2.setText(f"   Fase 2: R$ {self._formatar_numero_br(valor_f2)}")
        
        self.lbl_area_rvn.setText(f"🌳 Área RVN Total: {self._formatar_numero_br(area_rvn)} ha")
        self.lbl_area_floresta.setText(f"   Floresta: {self._formatar_numero_br(area_floresta)} ha")
        self.lbl_area_cerrado.setText(f"   Cerrado: {self._formatar_numero_br(area_cerrado)} ha")
        self.lbl_area_campo.setText(f"   Campo: {self._formatar_numero_br(area_campo)} ha")
        
        # Motivos mais comuns de inelegibilidade
        motivos_ordenados = sorted(motivos.items(), key=lambda x: x[1], reverse=True)[:10]
        texto_motivos = "\n".join([f"• {m}: {c}" for m, c in motivos_ordenados])
        self.txt_motivos.setPlainText(texto_motivos if texto_motivos else "Nenhum motivo encontrado")
    
    def _mostrar_imovel_atual(self):
        """Mostra os detalhes do imóvel atual."""
        # Limpar layout anterior
        while self.layout_detalhes.count():
            item = self.layout_detalhes.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        if not self.features_filtrados:
            lbl = QLabel("Nenhum imóvel encontrado com os filtros aplicados.")
            lbl.setAlignment(Qt.AlignCenter)
            self.layout_detalhes.addWidget(lbl)
            return
        
        feat = self.features_filtrados[self.indice_atual]
        
        # === CABEÇALHO ===
        header_group = QGroupBox("Dados do Imóvel")
        header_group.setStyleSheet(self._get_groupbox_style_highlight())
        header_layout = QVBoxLayout(header_group)
        
        # Obter dados
        car = self._get_attr(feat, ["n_do_car", "cod_imovel"], "N/D")
        cpf = self._get_attr(feat, ["cpf_cnpj"], "N/D")
        proprietario = self._get_attr(feat, ["nom_comple"], "N/D")
        nom_lograd = self._get_attr(feat, ["nom_lograd"], "")
        nom_bairro = self._get_attr(feat, ["nom_bairro"], "")
        municipio = self._get_attr(feat, ["municipio"], "N/D")
        uf = self._get_attr(feat, ["uf", "cod_estado"], "N/D")
        status = self._get_attr(feat, ["status", "ind_status", "status_imo", "status_imovel", "status_imove"], "N/D")
        condicao = self._get_attr(feat, ["condicao", "des_condic", "condicao_imovel"], "N/D")
        tipo_imovel = self._get_attr(feat, ["tipo_imove", "ind_tipo", "tipo_imovel", "tipo"], "")
        area = self._get_attr(feat, ["area_imove", "area", "num_area", "area_ha", "area_imovel"], 0)
        rvn = self._get_attr(feat, ["RVN_area"], 0)
        rvn_pct = self._get_attr(feat, ["percent_rvn", "rvn_pct"], 0)
        
        # Linha 1: CAR (40%) | Endereço (60%)
        linha1_layout = QHBoxLayout()
        lbl_car = QLabel(f"<b>CAR:</b> {car}")
        lbl_car.setTextInteractionFlags(Qt.TextSelectableByMouse)
        
        # Montar endereço
        partes_endereco = []
        if nom_lograd:
            partes_endereco.append(str(nom_lograd))
        if nom_bairro:
            partes_endereco.append(str(nom_bairro))
        partes_endereco.append(f"{municipio}/{uf}")
        endereco = ", ".join(partes_endereco)
        lbl_endereco = QLabel(f"<b>Endereço:</b> {endereco}")
        lbl_endereco.setWordWrap(True)
        
        linha1_layout.addWidget(lbl_car, 40)  # 40%
        linha1_layout.addWidget(lbl_endereco, 60)  # 60%
        header_layout.addLayout(linha1_layout)
        
        # Linha 2: Proprietário + CPF na mesma linha
        linha2_layout = QHBoxLayout()
        prop_text = proprietario if proprietario != "N/D" else "-"
        cpf_text = cpf if cpf != "N/D" else "-"
        linha2_layout.addWidget(QLabel(f"<b>Proprietário(a)/Possuidor(a):</b> {prop_text}"), 85)
        linha2_layout.addWidget(QLabel(f"<b>CPF/CNPJ:</b> {cpf_text}"), 15)

        header_layout.addLayout(linha2_layout)
        
        # Linha 3: Status 9% / Tipo 8% / Condição 50% / Área 15% / RVN 18%
        tipo_str = tipo_imovel if tipo_imovel and str(tipo_imovel).strip() not in ('', 'NULL', 'None', 'N/D') else '-'
        linha3_layout = QHBoxLayout()
        linha3_layout.addWidget(QLabel(f"<b>Status:</b> {status}"), 9)
        linha3_layout.addWidget(QLabel(f"<b>Tipo:</b> {tipo_str}"), 8)
        linha3_layout.addWidget(QLabel(f"<b>Condição:</b> {condicao}"), 50)
        linha3_layout.addWidget(QLabel(f"<b>Área:</b> {area:.2f} ha"), 15)
        linha3_layout.addWidget(QLabel(f"<b>RVN:</b> {rvn:.2f} ha ({rvn_pct:.1%})"), 18)
        header_layout.addLayout(linha3_layout)
        
        self.layout_detalhes.addWidget(header_group)
        
        # === AVALIAÇÃO DE ELEGIBILIDADE (TABELA 17 CRITÉRIOS) ===
        avaliacao_group = QGroupBox("Avaliação de Elegibilidade")
        avaliacao_group.setStyleSheet(self._get_groupbox_style())
        avaliacao_layout = QVBoxLayout(avaliacao_group)
        
        # Criar tabela
        from qgis.PyQt.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        tabela = QTableWidget()
        tabela.setColumnCount(3)
        tabela.setRowCount(17)
        tabela.setHorizontalHeaderLabels(["Critério de elegibilidade", "Fase 1", "Fase 2"])
        tabela.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        tabela.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        tabela.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        tabela.verticalHeader().setVisible(False)
        tabela.setEditTriggers(QTableWidget.NoEditTriggers)
        tabela.setSelectionMode(QTableWidget.NoSelection)
        tabela.setStyleSheet("""
            QTableWidget {
                background-color: white;
                gridline-color: #ddd;
                font-size: 10px;
            }
            QHeaderView::section {
                background-color: #34495e;
                color: white;
                padding: 4px;
                font-weight: bold;
                border: 1px solid #2c3e50;
            }
        """)
        
        # Definir os 17 critérios com campos correspondentes
        # (nome_criterio, campo_f1, campo_f2, aplica_f1, aplica_f2)
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
        
        def formatar_valor_criterio(valor, aplica):
            """Formata o valor do critério para exibição na tabela."""
            if not aplica:
                return "Não se aplica", "#7f8c8d"  # Cinza
            if valor is None or valor == "N/D" or valor == "":
                return "N/D", "#7f8c8d"
            valor_str = str(valor).strip().upper()
            if valor_str in ["ELEGÍVEL", "ELEGIVEL", "SIM", "OK", "DENTRO"]:
                return "Elegível", "#27ae60"  # Verde
            elif valor_str in ["NÃO ELEGÍVEL", "NAO ELEGIVEL", "NÃO", "NAO", "FORA"]:
                return "Não Elegível", "#e74c3c"  # Vermelho
            else:
                # Se contém texto de inelegibilidade
                if any(x in valor_str for x in ["SOBR", "EXCEDE", "INSUF", "DESMAT"]):
                    return "Não Elegível", "#e74c3c"
                return "Elegível", "#27ae60"
        
        for row, (nome, campo_f1, campo_f2, aplica_f1, aplica_f2) in enumerate(criterios):
            # Coluna 0: Nome do critério
            item_nome = QTableWidgetItem(nome)
            tabela.setItem(row, 0, item_nome)
            
            # Coluna 1: Fase 1
            valor_f1 = self._get_attr(feat, [campo_f1], "N/D") if campo_f1 else "N/D"
            texto_f1, cor_f1 = formatar_valor_criterio(valor_f1, aplica_f1)
            item_f1 = QTableWidgetItem(texto_f1)
            item_f1.setForeground(QColor(cor_f1))
            item_f1.setTextAlignment(Qt.AlignCenter)
            tabela.setItem(row, 1, item_f1)
            
            # Coluna 2: Fase 2
            valor_f2 = self._get_attr(feat, [campo_f2], "N/D") if campo_f2 else "N/D"
            texto_f2, cor_f2 = formatar_valor_criterio(valor_f2, aplica_f2)
            item_f2 = QTableWidgetItem(texto_f2)
            item_f2.setForeground(QColor(cor_f2))
            item_f2.setTextAlignment(Qt.AlignCenter)
            tabela.setItem(row, 2, item_f2)
        
        # Ajustar altura da tabela para mostrar todas as linhas
        tabela.setMinimumHeight(485)
        tabela.resizeRowsToContents()
        
        avaliacao_layout.addWidget(tabela)
        self.layout_detalhes.addWidget(avaliacao_group)

        # === PRIORIZAÇÃO (se disponível) ===
        try:
            self._adicionar_secao_priorizacao(feat)
        except Exception as _e:
            pass

        # === RESULTADO FINAL ===
        # Coluna 'elegibilidade' tem valores: "Fase 1", "Fase 2" ou "Inelegível"
        resultado = self._get_attr(feat, ["elegibilidade"], "N/D")
        parecer = self._get_attr(feat, ["parecer"], "")
        
        resultado_group = QGroupBox("Resultado Final")
        if resultado in ["Fase 1", "Fase 2"]:
            resultado_group.setStyleSheet(self._get_groupbox_style_elegivel())
        else:
            resultado_group.setStyleSheet(self._get_groupbox_style_inelegivel())
        
        resultado_layout = QVBoxLayout(resultado_group)
        
        # Formatar texto do resultado para exibição
        if resultado == "Fase 1":
            resultado_texto = "✓ Elegível - Fase 1"
            cor_resultado = self.COLORS['elegivel']
        elif resultado == "Fase 2":
            resultado_texto = "✓ Elegível - Fase 2"
            cor_resultado = self.COLORS['elegivel']
        else:
            resultado_texto = "✗ Inelegível"
            cor_resultado = self.COLORS['nao_elegivel']
        
        lbl_resultado = QLabel(f"<h3 style='color: {cor_resultado};'>{resultado_texto}</h3>")
        lbl_resultado.setAlignment(Qt.AlignCenter)
        resultado_layout.addWidget(lbl_resultado)
        
        # Parecer descritivo
        if parecer:
            lbl_parecer = QLabel(parecer)
            lbl_parecer.setWordWrap(True)
            lbl_parecer.setStyleSheet("""
                QLabel {
                    padding: 10px;
                    background-color: #f8f9fa;
                    border: 1px solid #dee2e6;
                    border-radius: 4px;
                    font-size: 11px;
                    line-height: 1.5;
                }
            """)
            resultado_layout.addWidget(lbl_parecer)
        
        self.layout_detalhes.addWidget(resultado_group)
        
        # Espaço final
        self.layout_detalhes.addStretch()
    
    def _criar_linha_criterio(self, nome, valor):
        """Cria uma linha de critério com indicador visual."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 2, 0, 2)
        
        # Indicador
        if valor == "Elegível":
            indicador = "✓"
            cor = self.COLORS['elegivel']
        elif valor == "Não Elegível":
            indicador = "✗"
            cor = self.COLORS['nao_elegivel']
        elif valor == "N/D":
            indicador = "○"
            cor = self.COLORS['nd']
        else:
            indicador = "○"
            cor = self.COLORS['texto_claro']
        
        lbl_indicador = QLabel(indicador)
        lbl_indicador.setStyleSheet(f"color: {cor}; font-weight: bold; font-size: 14px;")
        lbl_indicador.setFixedWidth(20)
        layout.addWidget(lbl_indicador)
        
        lbl_nome = QLabel(nome)
        layout.addWidget(lbl_nome, stretch=1)
        
        lbl_valor = QLabel(str(valor))
        lbl_valor.setStyleSheet(f"color: {cor}; font-weight: bold;")
        layout.addWidget(lbl_valor)
        
        return widget
    
    def _get_attr(self, feat, field_names, default=None):
        """Obtém atributo de forma segura."""
        campos = [f.name() for f in feat.fields()]
        for nome in field_names:
            if nome in campos:
                valor = feat.attribute(nome)
                if valor is not None:
                    return valor
        return default

    def _adicionar_secao_priorizacao(self, feat):
        """Adiciona seção 'Critérios de Priorização' se as colunas existirem."""
        from qgis.PyQt.QtWidgets import QGroupBox, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLabel
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtGui import QColor

        # Verificar se a camada tem colunas de priorização
        campos_camada = {f.name() for f in feat.fields()}
        if "score_priorizacao" not in campos_camada:
            return  # plugin processou sem priorização

        criterios_prio = [
            ("A1", "Municípios prioritários (>50% área)", "prio_mun_prioritario", 1),
            ("A2", "Mun. desmate sob controle (>50% área)", "prio_mun_controle", 1),
            ("A3", "Mun. Programa União (>50% área)", "prio_mun_uniao", 1),
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
        pct_rvn = self._get_attr(feat, ["pct_rvn_total"], 0) or 0
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = 0
        try:
            ranking = int(ranking)
        except (TypeError, ValueError):
            ranking = 0
        try:
            pct_rvn = float(pct_rvn)
        except (TypeError, ValueError):
            pct_rvn = 0.0

        grupo = QGroupBox("Critérios de Priorização")
        grupo.setStyleSheet(self._get_groupbox_style())
        layout = QVBoxLayout(grupo)

        # Resumo
        if ranking > 0:
            resumo = QLabel(
                f"<b>Score:</b> {score}    "
                f"<b>Ranking:</b> {ranking}º    "
                f"<b>RVN/Área:</b> {pct_rvn*100:.1f}%"
            )
        else:
            resumo = QLabel(
                f"<b>Score:</b> {score}    "
                f"<b>Ranking:</b> não aplicável (imóvel não elegível)    "
                f"<b>RVN/Área:</b> {pct_rvn*100:.1f}%"
            )
        resumo.setStyleSheet("font-size: 11px; padding: 4px;")
        layout.addWidget(resumo)

        # Tabela
        tabela = QTableWidget()
        tabela.setColumnCount(4)
        tabela.setRowCount(len(criterios_prio))
        tabela.setHorizontalHeaderLabels(["ID", "Critério", "Peso", "Resultado"])
        tabela.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        tabela.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        tabela.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        tabela.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        tabela.verticalHeader().setVisible(False)
        tabela.setEditTriggers(QTableWidget.NoEditTriggers)
        tabela.setSelectionMode(QTableWidget.NoSelection)
        tabela.setStyleSheet("""
            QTableWidget { background-color: white; gridline-color: #ddd; font-size: 10px; }
            QHeaderView::section { background-color: #34495e; color: white;
                                    padding: 4px; font-weight: bold; border: 1px solid #2c3e50; }
        """)

        for row, (sigla, nome, campo, peso) in enumerate(criterios_prio):
            tabela.setItem(row, 0, QTableWidgetItem(sigla))
            tabela.setItem(row, 1, QTableWidgetItem(nome))
            item_peso = QTableWidgetItem(str(peso))
            item_peso.setTextAlignment(Qt.AlignCenter)
            tabela.setItem(row, 2, item_peso)

            valor = str(self._get_attr(feat, [campo], "Não") or "Não").strip()
            cor = "#27ae60" if valor.lower() == "sim" else "#7f8c8d"
            item_res = QTableWidgetItem(valor)
            item_res.setForeground(QColor(cor))
            item_res.setTextAlignment(Qt.AlignCenter)
            tabela.setItem(row, 3, item_res)

        tabela.resizeRowsToContents()
        tabela.setMinimumHeight(280)
        layout.addWidget(tabela)
        self.layout_detalhes.addWidget(grupo)
    
    def _exportar_estatisticas(self):
        """Exporta as estatísticas para um arquivo."""
        from qgis.PyQt.QtWidgets import QFileDialog
        
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar Estatísticas",
            "estatisticas_elegiveis.txt",
            "Arquivos de Texto (*.txt)"
        )
        
        if not filepath:
            return
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write("ESTATÍSTICAS DE ELEGIBILIDADE - FLORESTA+\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"{self.lbl_total_imoveis.text()}\n")
                f.write(f"{self.lbl_filtrados.text()}\n\n")
                f.write("ELEGIBILIDADE:\n")
                f.write(f"  {self.lbl_elegiveis_f1.text()}\n")
                f.write(f"  {self.lbl_elegiveis_f2.text()}\n")
                f.write(f"  {self.lbl_inelegiveis.text()}\n\n")
                f.write("VALORES:\n")
                f.write(f"  {self.lbl_valor_total.text()}\n")
                f.write(f"  {self.lbl_valor_f1.text()}\n")
                f.write(f"  {self.lbl_valor_f2.text()}\n\n")
                f.write("ÁREAS RVN:\n")
                f.write(f"  {self.lbl_area_rvn.text()}\n")
                f.write(f"  {self.lbl_area_floresta.text()}\n")
                f.write(f"  {self.lbl_area_cerrado.text()}\n")
                f.write(f"  {self.lbl_area_campo.text()}\n\n")
                f.write("PRINCIPAIS MOTIVOS DE INELEGIBILIDADE:\n")
                f.write(self.txt_motivos.toPlainText())
            
            QMessageBox.information(
                self,
                "Exportação Concluída",
                f"Estatísticas exportadas para:\n{filepath}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Erro na Exportação",
                f"Erro ao exportar estatísticas:\n{str(e)}"
            )
    
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
    
    def _get_groupbox_style_highlight(self):
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {self.COLORS['verde_medio']};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #e8f5e9;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {self.COLORS['verde_escuro']};
            }}
        """
    
    def _get_groupbox_style_elegivel(self):
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {self.COLORS['elegivel']};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #e8f5e9;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {self.COLORS['elegivel']};
            }}
        """
    
    def _get_groupbox_style_inelegivel(self):
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {self.COLORS['nao_elegivel']};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #ffebee;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {self.COLORS['nao_elegivel']};
            }}
        """
    
    def _get_button_style(self):
        return f"""
            QPushButton {{
                background-color: {self.COLORS['verde_medio']};
                color: white;
                border: none;
                padding: 6px 12px;
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
    
    def _get_button_style_destaque(self):
        """Estilo do botão de destaque (gerar laudos)."""
        return f"""
            QPushButton {{
                background-color: #e67e22;
                color: white;
                border: none;
                padding: 8px 12px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #d35400;
            }}
            QPushButton:pressed {{
                background-color: #a04000;
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
                color: #888888;
            }}
        """
    
    def _selecionar_pasta_destino(self):
        """Abre diálogo para selecionar pasta de destino dos laudos."""
        # Usar última pasta salva ou pasta padrão
        pasta_inicial = VisualizadorLaudosDialog._ultima_pasta_salva or os.path.expanduser("~")
        
        pasta = QFileDialog.getExistingDirectory(
            self,
            "Selecionar Pasta de Destino",
            pasta_inicial,
            QFileDialog.ShowDirsOnly
        )
        
        if pasta:
            self.output_dir = pasta
            self.lbl_pasta_destino.setText(pasta)
            self.btn_gerar_laudos.setEnabled(len(self.features_filtrados) > 0)
            self.lbl_status_geracao.setText(f"Prontos para gerar: {len(self.features_filtrados)} laudos")
            
            # Guardar a pasta para próximos salvamentos
            VisualizadorLaudosDialog._ultima_pasta_salva = pasta
    
    def _gerar_laudos_filtrados(self):
        """Gera laudos em PDF para os imóveis filtrados."""
        if not self.output_dir:
            QMessageBox.warning(self, "Aviso", "Selecione uma pasta de destino primeiro.")
            return
        
        if not self.features_filtrados:
            QMessageBox.warning(self, "Aviso", "Nenhum imóvel filtrado para gerar laudos.")
            return
        
        total = len(self.features_filtrados)
        
        # Criar diálogo personalizado para confirmar e escolher modo
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirmar Geração")
        dialog.setMinimumWidth(350)
        layout = QVBoxLayout(dialog)
        
        # Info
        info_label = QLabel(f"<b>Gerar {total} laudo(s) em PDF?</b><br><br>Pasta: {self.output_dir}")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        layout.addSpacing(10)
        
        # Opções de agrupamento
        group = QGroupBox("Modo de geração:")
        group_layout = QVBoxLayout(group)
        
        from qgis.PyQt.QtWidgets import QRadioButton, QSpinBox
        
        self.radio_individual = QRadioButton("Arquivos individuais (1 PDF por imóvel)")
        self.radio_individual.setChecked(True)
        group_layout.addWidget(self.radio_individual)
        
        self.radio_unico = QRadioButton("PDF único (todos os laudos em 1 arquivo)")
        group_layout.addWidget(self.radio_unico)
        
        # Opção agrupado com spinner
        agrupado_layout = QHBoxLayout()
        self.radio_agrupado = QRadioButton("Agrupar em arquivos de")
        agrupado_layout.addWidget(self.radio_agrupado)
        
        self.spin_agrupamento = QSpinBox()
        self.spin_agrupamento.setRange(1, 10000)  # Permite qualquer valor de 1 a 10000
        self.spin_agrupamento.setValue(100)  # Padrão: 100
        self.spin_agrupamento.setSingleStep(10)  # Setas mudam de 10 em 10
        self.spin_agrupamento.setFixedWidth(70)
        self.spin_agrupamento.setEnabled(False)
        self.spin_agrupamento.setKeyboardTracking(True)  # Permite digitação livre
        agrupado_layout.addWidget(self.spin_agrupamento)
        
        agrupado_layout.addWidget(QLabel("laudos cada"))
        agrupado_layout.addStretch()
        group_layout.addLayout(agrupado_layout)
        
        # Conectar radio para habilitar/desabilitar spinner
        self.radio_agrupado.toggled.connect(self.spin_agrupamento.setEnabled)
        
        layout.addWidget(group)
        
        layout.addSpacing(10)
        
        # Botões
        btn_layout = QHBoxLayout()
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.clicked.connect(dialog.reject)
        btn_layout.addWidget(btn_cancelar)
        
        btn_gerar = QPushButton("Gerar")
        btn_gerar.setDefault(True)
        btn_gerar.clicked.connect(dialog.accept)
        btn_gerar.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 6px 20px;")
        btn_layout.addWidget(btn_gerar)
        
        layout.addLayout(btn_layout)
        
        if dialog.exec_() != QDialog.Accepted:
            return
        
        # Determinar modo de geração
        if self.radio_unico.isChecked():
            modo_geracao = "unico"
            tamanho_grupo = total
        elif self.radio_agrupado.isChecked():
            modo_geracao = "agrupado"
            tamanho_grupo = self.spin_agrupamento.value()
        else:
            modo_geracao = "individual"
            tamanho_grupo = 1
        
        # Importar gerador de laudos
        try:
            from Plugin_FMais.ui.dialogs.gerador_laudos import GeradorLaudosThread
        except ImportError as e:
            QMessageBox.critical(self, "Erro", f"Erro ao importar gerador de laudos: {e}")
            return
        
        # Criar progress dialog
        progress = QProgressDialog("Gerando laudos...", "Cancelar", 0, total, self)
        progress.setWindowTitle("Gerando Laudos")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        
        if modo_geracao == "individual":
            # Modo individual: usar thread existente (1 PDF por imóvel)
            self.gerador_thread = GeradorLaudosThread(
                self.layer,
                self.features_filtrados,
                self.output_dir,
                self.gpkg_path,
                self.planet_url
            )
            
            # Conectar sinais
            def on_progress(current, total_count, msg):
                progress.setValue(current)
                progress.setLabelText(msg)
                self.lbl_status_geracao.setText(msg)
                QApplication.processEvents()
            
            def on_finished(success_count, fail_count):
                progress.close()
                
                # Abrir pasta automaticamente
                if success_count > 0:
                    self._abrir_pasta(self.output_dir)
                
                # Mostrar resultado
                if fail_count > 0:
                    QMessageBox.warning(
                        self,
                        "Geração Concluída com Erros",
                        f"Laudos gerados: {success_count}\nErros: {fail_count}\n\n"
                        f"Verifique o console para detalhes dos erros."
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Geração Concluída",
                        f"✓ {success_count} laudo(s) gerado(s) com sucesso!\n\n"
                        f"Pasta: {self.output_dir}"
                    )
                
                self.lbl_status_geracao.setText(f"✓ {success_count} laudos gerados")
            
            self.gerador_thread.progress.connect(on_progress)
            self.gerador_thread.finished.connect(on_finished)
            
            # Cancelamento
            progress.canceled.connect(lambda: setattr(self.gerador_thread, 'cancelar', True))
            
            # Iniciar geração
            self.gerador_thread.start()
        
        else:
            # Modo combinado/agrupado: gerar PDFs com múltiplos laudos por arquivo
            self._gerar_laudos_combinados(progress, modo_geracao, tamanho_grupo)
    
    def _abrir_pasta(self, pasta):
        """Abre a pasta no explorador de arquivos."""
        try:
            if sys.platform == 'win32':
                os.startfile(pasta)
            elif sys.platform == 'darwin':
                subprocess.run(['open', pasta])
            else:
                subprocess.run(['xdg-open', pasta])
        except Exception as e:
            print(f"Erro ao abrir pasta: {e}")
    
    def _gerar_laudos_combinados(self, progress, modo, tamanho_grupo):
        """Gera laudos combinados em um ou mais arquivos PDF."""
        from datetime import datetime
        
        features = self.features_filtrados
        total = len(features)
        
        # Dividir em grupos
        if modo == "unico":
            grupos = [features]
            nomes_arquivos = ["laudos_completo.pdf"]
        else:
            # Agrupar por tamanho
            grupos = [features[i:i + tamanho_grupo] for i in range(0, total, tamanho_grupo)]
            nomes_arquivos = [f"laudos_parte_{i+1:03d}.pdf" for i in range(len(grupos))]
        
        try:
            from Plugin_FMais.ui.dialogs.gerador_laudos import GeradorLaudosThread
            
            # Criar instância do gerador para usar seus métodos
            gerador = GeradorLaudosThread(
                self.layer,
                [],  # features vazios, vamos processar manualmente
                self.output_dir,
                self.gpkg_path,
                self.planet_url
            )
            
            sucesso = 0
            erros = 0
            cancelado = False
            
            for idx_grupo, (grupo, nome_arquivo) in enumerate(zip(grupos, nomes_arquivos)):
                if cancelado:
                    break
                
                filepath = os.path.join(self.output_dir, nome_arquivo)
                
                try:
                    # Gerar PDF combinado para este grupo
                    self._gerar_pdf_combinado(gerador, grupo, filepath, progress, idx_grupo, len(grupos))
                    sucesso += 1
                except Exception as e:
                    erros += 1
                    print(f"[Laudos] Erro ao gerar {nome_arquivo}: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Verificar cancelamento
                if progress.wasCanceled():
                    cancelado = True
                    break
            
            progress.close()
            
            # Abrir pasta
            if sucesso > 0:
                self._abrir_pasta(self.output_dir)
            
            # Resultado
            if cancelado:
                QMessageBox.warning(
                    self,
                    "Geração Cancelada",
                    f"Geração cancelada pelo usuário.\n\n"
                    f"Arquivos gerados: {sucesso}"
                )
            elif erros > 0:
                QMessageBox.warning(
                    self,
                    "Geração Concluída com Erros",
                    f"Arquivos gerados: {sucesso}\nErros: {erros}"
                )
            else:
                msg_arquivos = f"{sucesso} arquivo(s)" if sucesso > 1 else "1 arquivo"
                QMessageBox.information(
                    self,
                    "Geração Concluída",
                    f"✓ {total} laudos gerados em {msg_arquivos}!\n\n"
                    f"Pasta: {self.output_dir}"
                )
            
            self.lbl_status_geracao.setText(f"✓ {total} laudos em {sucesso} arquivo(s)")
            
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "Erro", f"Erro ao gerar laudos: {e}")
            import traceback
            traceback.print_exc()
    
    def _gerar_pdf_combinado(self, gerador, features, filepath, progress, idx_grupo, total_grupos):
        """Gera um PDF com múltiplos laudos."""
        import tempfile
        import glob
        
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, PageBreak
        
        # Atualizar progress
        progress.setLabelText(f"Gerando arquivo {idx_grupo + 1}/{total_grupos}...")
        QApplication.processEvents()
        
        # Coletar todas as stories dos laudos
        all_stories = []
        
        for i, feat in enumerate(features):
            if progress.wasCanceled():
                raise Exception("Cancelado pelo usuário")
            
            # Atualizar progress detalhado
            progress_value = int((idx_grupo / total_grupos + (i / len(features)) / total_grupos) * len(self.features_filtrados))
            progress.setValue(progress_value)
            
            car = gerador._get_attr(feat, ["n_do_car", "cod_imovel"], f"imovel_{feat.id()}")
            progress.setLabelText(f"Arquivo {idx_grupo + 1}/{total_grupos} - Laudo {i + 1}/{len(features)}")
            QApplication.processEvents()
            
            # Gerar story deste laudo
            story = gerador._gerar_story(feat)
            
            if story:
                all_stories.extend(story)
                # Adicionar quebra de página entre laudos (exceto após o último)
                if i < len(features) - 1:
                    all_stories.append(PageBreak())
        
        # Criar o documento PDF
        if all_stories:
            doc = SimpleDocTemplate(
                filepath,
                pagesize=A4,
                leftMargin=10 * mm,
                rightMargin=10 * mm,
                topMargin=8 * mm,
                bottomMargin=8 * mm
            )
            doc.build(all_stories)
        
        # Limpar arquivos temporários de mapas após construir o documento
        try:
            temp_dir = tempfile.gettempdir()
            for pattern in ['map1_*.png', 'map2_*.png', 'loc1_*.png', 'loc2_*.png', 'loc3_*.png']:
                for f in glob.glob(os.path.join(temp_dir, pattern)):
                    try:
                        os.remove(f)
                    except:
                        pass
        except:
            pass
    
    def _salvar_pdf_atual(self):
        """Salva o laudo em PDF do imóvel atualmente visível."""
        if not self.features_filtrados:
            QMessageBox.warning(self, "Aviso", "Nenhum imóvel para salvar.")
            return
        
        # Obter o imóvel atual
        feat = self.features_filtrados[self.indice_atual]
        
        # Gerar nome sugerido para o arquivo
        car = self._get_attr(feat, ["n_do_car", "cod_imovel"], f"imovel_{feat.id()}")
        cpf = self._get_attr(feat, ["cpf_cnpj"], "")
        
        cpf_clean = str(cpf).replace(".", "").replace("-", "").replace("/", "").replace(" ", "")[:14] if cpf else ""
        car_clean = str(car).replace("/", "-").replace("\\", "-").replace(" ", "")
        
        if cpf_clean:
            nome_sugerido = f"{cpf_clean}_{car_clean}.pdf"
        else:
            nome_sugerido = f"{car_clean}.pdf"
        
        # Usar última pasta salva ou pasta padrão
        pasta_inicial = VisualizadorLaudosDialog._ultima_pasta_salva or os.path.expanduser("~")
        caminho_sugerido = os.path.join(pasta_inicial, nome_sugerido)
        
        # Diálogo para salvar arquivo
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar Laudo em PDF",
            caminho_sugerido,
            "Arquivos PDF (*.pdf)"
        )
        
        if not filepath:
            return
        
        # Guardar a pasta escolhida para próximos salvamentos
        VisualizadorLaudosDialog._ultima_pasta_salva = os.path.dirname(filepath)
        
        # Garantir extensão .pdf
        if not filepath.lower().endswith('.pdf'):
            filepath += '.pdf'
        
        # Gerar o PDF
        try:
            from Plugin_FMais.ui.dialogs.gerador_laudos import GeradorLaudosThread
            
            # Criar instância temporária para gerar o PDF
            gerador = GeradorLaudosThread(
                self.layer,
                [feat],  # Lista com apenas o imóvel atual
                os.path.dirname(filepath),
                self.gpkg_path,
                self.planet_url
            )
            
            # Gerar diretamente (sem thread para um único arquivo)
            gerador._gerar_pdf(feat, filepath)
            
            # Mostrar notificação rápida (tooltip) sem precisar clicar OK
            from qgis.PyQt.QtWidgets import QToolTip
            from qgis.PyQt.QtCore import QPoint
            
            # Mostrar tooltip perto do botão
            pos = self.btn_salvar_pdf_atual.mapToGlobal(QPoint(0, -30))
            QToolTip.showText(pos, f"✓ Laudo salvo!", self.btn_salvar_pdf_atual, self.btn_salvar_pdf_atual.rect(), 3000)
                    
        except Exception as e:
            QMessageBox.critical(
                self,
                "Erro ao Salvar",
                f"Erro ao gerar o PDF:\n\n{str(e)}"
            )
            import traceback
            traceback.print_exc()
