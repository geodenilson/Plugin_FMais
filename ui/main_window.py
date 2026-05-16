# -*- coding: utf-8 -*-
"""
Interface principal do plugin Floresta+ Amazonia
Janela independente com mapa embutido e sistema de abas.
"""

# ============================================================
# WORKAROUND: Corrigir sys.stderr None no QGIS
# O NumPy 2.x tenta escrever em sys.stderr que pode ser None
# Isso deve ser feito ANTES de qualquer import que use NumPy
# ============================================================
import sys
import io
if sys.stderr is None:
    sys.stderr = io.StringIO()
if sys.stdout is None:
    sys.stdout = io.StringIO()
# ============================================================

import os
import json
import zipfile
import tempfile
import csv
import shutil
import traceback
from datetime import datetime

# NOTA: NumPy 2.x tem incompatibilidade com GDAL/OSGeo4W compilado com NumPy 1.x
# Usamos QGIS Processing ao invés de NumPy direto para evitar crashes
NUMPY_AVAILABLE = False
np = None
gdal = None
ogr = None
SKLEARN_AVAILABLE = False
RandomForestClassifier = None
from qgis.PyQt.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QPushButton, QTabWidget, QGroupBox, QListWidget, QFrame,
    QSplitter, QMessageBox, QComboBox, QLineEdit, QTextEdit, QApplication,
    QFileDialog, QProgressBar, QListWidgetItem, QCheckBox,
    QScrollArea, QGridLayout, QToolButton, QSpinBox, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QDialog, QDialogButtonBox, QFormLayout, QListWidgetItem
)
from qgis.PyQt.QtCore import Qt, QSize, QThread, pyqtSignal, QUrl, QEventLoop, QVariant
from qgis.PyQt.QtGui import QFont, QColor, QPixmap, QIcon, QPalette
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply, QNetworkAccessManager
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, 
    QgsCoordinateReferenceSystem, QgsVectorFileWriter,
    QgsWkbTypes, QgsFeature, QgsGeometry,
    QgsCoordinateTransform, QgsRectangle,
    QgsField, QgsFields, QgsPointXY, QgsFillSymbol,
    QgsLinePatternFillSymbolLayer, QgsSpatialIndex
)
from qgis.gui import QgsMapCanvas, QgsMapToolPan, QgsMapToolZoom, QgsMapTool, QgsRubberBand

# Import do cliente Planet
try:
    from ..core.planet_client import planet_client
    PLANET_AVAILABLE = True
except ImportError:
    PLANET_AVAILABLE = False
    planet_client = None


class QuadSelectionTool(QgsMapTool):
    """Ferramenta para seleção de quads por retângulo no mapa."""
    
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback = callback  # Função chamada quando seleção é concluída
        self.rubber_band = None
        self.start_point = None
        self.end_point = None
        self.is_selecting = False
    
    def canvasPressEvent(self, event):
        """Início da seleção - clique do mouse."""
        self.start_point = self.toMapCoordinates(event.pos())
        self.end_point = self.start_point
        self.is_selecting = True
        
        # Criar rubber band para visualização
        if self.rubber_band:
            self.canvas.scene().removeItem(self.rubber_band)
        
        self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubber_band.setColor(QColor(30, 100, 255, 100))  # Azul semi-transparente
        self.rubber_band.setWidth(2)
        self._show_rect(self.start_point, self.end_point)
    
    def canvasMoveEvent(self, event):
        """Movimento do mouse durante a seleção."""
        if not self.is_selecting:
            return
        
        self.end_point = self.toMapCoordinates(event.pos())
        self._show_rect(self.start_point, self.end_point)
    
    def canvasReleaseEvent(self, event):
        """Fim da seleção - soltar o mouse."""
        if not self.is_selecting:
            return
        
        self.end_point = self.toMapCoordinates(event.pos())
        self.is_selecting = False
        
        # Criar retângulo de seleção
        rect = QgsRectangle(self.start_point, self.end_point)
        
        # Remover rubber band
        if self.rubber_band:
            self.canvas.scene().removeItem(self.rubber_band)
            self.rubber_band = None
        
        # Chamar callback com o retângulo
        if self.callback and rect.width() > 0 and rect.height() > 0:
            self.callback(rect)
    
    def _show_rect(self, start, end):
        """Desenha o retângulo de seleção."""
        if not self.rubber_band:
            return
        
        self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        
        # Criar polígono do retângulo
        points = [
            QgsPointXY(start.x(), start.y()),
            QgsPointXY(end.x(), start.y()),
            QgsPointXY(end.x(), end.y()),
            QgsPointXY(start.x(), end.y()),
            QgsPointXY(start.x(), start.y())
        ]
        
        for point in points:
            self.rubber_band.addPoint(point, False)
        self.rubber_band.addPoint(points[0], True)  # Fechar e atualizar
    
    def deactivate(self):
        """Desativar a ferramenta."""
        if self.rubber_band:
            self.canvas.scene().removeItem(self.rubber_band)
            self.rubber_band = None
        super().deactivate()


class MainWindow(QMainWindow):
    """Janela principal do plugin Floresta+ Amazônia."""
    
    # Cores do tema Floresta+
    COLORS = {
        'verde_escuro': '#2C5530',
        'verde_medio': '#4A7C59',
        'verde_claro': '#8BC34A',
        'verde_lima': '#A8D08D',
        'amarelo': '#C5D86D',
        'branco': '#FFFFFF',
        'cinza_claro': '#F5F5F5',
        'cinza': '#E0E0E0',
        'texto': '#333333',
        'texto_claro': '#666666'
    }
    
    def __init__(self, iface, plugin_dir, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.plugin_dir = plugin_dir
        
        # Carregar configurações
        self._load_config()
        
        # Dicionário para armazenar camadas carregadas
        self.loaded_layers = {}
        
        # Histórico de mensagens do log de processamento
        self._log_mensagens = []
        
        # Network manager para downloads
        self.network_manager = QNetworkAccessManager()
        
        # Configurar janela
        self._setup_window()
        
        # Criar interface
        self._create_ui()
    
    # ==========================================================================
    #                          CONFIGURAÇÃO DA JANELA
    # ==========================================================================
    
    def _load_config(self):
        """Carrega configurações do arquivo JSON."""
        config_path = os.path.join(self.plugin_dir, 'config', 'config.json')
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except Exception as e:
            print(f"Erro ao carregar config: {e}")
            self.config = {}
    
    def _get_credentials_file_path(self):
        """Retorna o caminho do arquivo de credenciais."""
        return os.path.join(self.plugin_dir, 'config', '.planet_credentials.json')
    
    def _save_planet_credentials(self):
        """Salva credenciais Planet se checkbox marcado."""
        if not hasattr(self, 'remember_planet_credentials'):
            return
        if not self.remember_planet_credentials.isChecked():
            # Remover arquivo se existir
            cred_path = self._get_credentials_file_path()
            if os.path.exists(cred_path):
                try:
                    os.remove(cred_path)
                except:
                    pass
            return
        
        try:
            import base64
            cred_path = self._get_credentials_file_path()
            
            email = self.planet_email.text().strip()
            password = self.planet_password.text()
            
            # Codificar em base64 (não é criptografia forte, mas ofusca)
            credentials = {
                'email': base64.b64encode(email.encode()).decode(),
                'password': base64.b64encode(password.encode()).decode(),
                'remember': True
            }
            
            with open(cred_path, 'w') as f:
                json.dump(credentials, f)
            
            print("Credenciais Planet salvas")
        except Exception as e:
            print(f"Erro ao salvar credenciais: {e}")
    
    def _load_planet_credentials(self):
        """Carrega credenciais Planet salvas."""
        try:
            import base64
            cred_path = self._get_credentials_file_path()
            
            if not os.path.exists(cred_path):
                return
            
            with open(cred_path, 'r') as f:
                credentials = json.load(f)
            
            if credentials.get('remember'):
                email = base64.b64decode(credentials.get('email', '')).decode()
                password = base64.b64decode(credentials.get('password', '')).decode()
                
                self.planet_email.setText(email)
                self.planet_password.setText(password)
                self.remember_planet_credentials.setChecked(True)
                
                print("Credenciais Planet carregadas")
        except Exception as e:
            print(f"Erro ao carregar credenciais: {e}")
    
    def _setup_window(self):
        """Configura propriedades da janela."""
        self.setWindowTitle("Floresta+ Amazônia - Análise de Elegibilidade")
        self.setMinimumSize(1400, 850)
        
        # Ícone da janela
        icon_path = os.path.join(self.plugin_dir, 'icone', 'Logo.png')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Janela independente
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
    
    # ==========================================================================
    #                          CRIAÇÃO DA INTERFACE
    # ==========================================================================
    
    def _create_ui(self):
        """Cria toda a interface do usuário."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 1. CABEÇALHO
        main_layout.addWidget(self._create_header())
        
        # 2. CONTEÚDO (Janela única - sem abas)
        main_layout.addWidget(self._create_tab_preparacao(), 1)
        
        # 3. RODAPÉ
        main_layout.addWidget(self._create_footer())
    
    # ==========================================================================
    #                              CABEÇALHO
    # ==========================================================================
    
    def _create_header(self):
        """Cria o cabeçalho do plugin."""
        header = QFrame()
        # Fundo verde bem claro para o logo (verde) aparecer
        header.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #E8F5E9, 
                    stop:1 {self.COLORS['verde_lima']});
                border-bottom: 4px solid {self.COLORS['verde_escuro']};
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        header.setFixedHeight(90)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        
        # Logo (agora visivel no fundo claro)
        logo_path = os.path.join(self.plugin_dir, 'icone', 'Logo.png')
        if os.path.exists(logo_path):
            logo_label = QLabel()
            pixmap = QPixmap(logo_path).scaled(
                180, 70, 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            logo_label.setPixmap(pixmap)
            layout.addWidget(logo_label)
        
        layout.addSpacing(20)
        
        # Titulo e subtitulo (cor escura para contraste)
        title_layout = QVBoxLayout()
        title_layout.setSpacing(0)
        title_layout.setContentsMargins(0, 0, 0, 0)
        
        title = QLabel("ANÁLISE DE ELEGIBILIDADE")
        title.setStyleSheet(f"""
            background: transparent;
            color: {self.COLORS['verde_escuro']}; 
            font-size: 26px; 
            font-weight: bold;
            font-family: 'Segoe UI', Arial, sans-serif;
            margin-bottom: 0px;
            padding-bottom: 0px;
        """)
        title_layout.addWidget(title)
        
        subtitle = QLabel("Modalidade Conservação")
        subtitle.setStyleSheet(f"""
            background: transparent;
            color: {self.COLORS['verde_medio']}; 
            font-size: 14px;
            font-family: 'Segoe UI', Arial, sans-serif;
            margin-top: 0px;
            padding-top: 0px;
        """)
        title_layout.addWidget(subtitle)
        
        layout.addLayout(title_layout)
        layout.addStretch()
        
        # Info do projeto (cor escura para contraste)
        info_layout = QVBoxLayout()
        info_layout.setAlignment(Qt.AlignRight)
        info_layout.setSpacing(0)
        info_layout.setContentsMargins(0, 0, 0, 0)
        
        info1 = QLabel("MMA | PNUD | GCF")
        info1.setStyleSheet(f"background: transparent; color: {self.COLORS['verde_escuro']}; font-size: 13px; font-weight: bold; margin-bottom: 0px;")
        info_layout.addWidget(info1, alignment=Qt.AlignRight)
        
        info2 = QLabel("Fundo Verde para o Clima")
        info2.setStyleSheet(f"background: transparent; color: {self.COLORS['verde_medio']}; font-size: 11px; margin-top: 0px;")
        info_layout.addWidget(info2, alignment=Qt.AlignRight)
        
        layout.addLayout(info_layout)
        
        return header
    
    # ==========================================================================
    #                               RODAPÉ
    # ==========================================================================
    
    def _create_footer(self):
        """Cria o rodapé do plugin."""
        footer = QFrame()
        footer.setStyleSheet(f"""
            QFrame {{
                background-color: {self.COLORS['verde_escuro']};
                border-top: 2px solid {self.COLORS['verde_medio']};
            }}
        """)
        footer.setFixedHeight(35)
        
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 5, 20, 5)
        layout.setSpacing(0)
        
        # Container esquerdo (versão) - largura fixa
        left_container = QWidget()
        left_layout = QHBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        version = QLabel(f"v{self.config.get('version', '1.0.0')}")
        version.setStyleSheet(f"color: {self.COLORS['verde_lima']}; font-size: 11px;")
        left_layout.addWidget(version)
        left_layout.addStretch()
        left_container.setMinimumWidth(150)
        layout.addWidget(left_container, 1)
        
        # Container central (status) - centralizado
        center_container = QWidget()
        center_layout = QHBoxLayout(center_container)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setAlignment(Qt.AlignCenter)
        self.status_label = QLabel("Pronto")
        self.status_label.setStyleSheet(f"color: {self.COLORS['cinza']}; font-size: 11px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        center_layout.addWidget(self.status_label)
        layout.addWidget(center_container, 2)
        
        # Container direito (créditos) - alinhado à direita
        right_container = QWidget()
        right_layout = QHBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch()
        copyright_label = QLabel("Projeto Floresta+ Amazônia | Desenvolvido por Denilson Passo")
        copyright_label.setStyleSheet(f"color: {self.COLORS['cinza']}; font-size: 10px;")
        right_layout.addWidget(copyright_label)
        right_container.setMinimumWidth(150)
        layout.addWidget(right_container, 1)
        
        return footer
    
    # ==========================================================================
    #                    ABA 1: PREPARAÇÃO DA BASE DE REFERÊNCIA
    # ==========================================================================
    
    def _create_tab_preparacao(self):
        """Cria a aba de preparação da base de referência."""
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Splitter para redimensionar painel/mapa (colados, ajustável pelo usuário)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(5)  # Handle visível para arrastar
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #95a5a6;
            }
            QSplitter::handle:hover {
                background-color: #3498db;
            }
        """)
        
        # --- PAINEL LATERAL ESQUERDO ---
        panel_left = self._create_panel_preparacao()
        
        # --- ÁREA DO MAPA ---
        map_container = self._create_map_area()
        
        # Adicionar ao splitter (sem margens)
        splitter.addWidget(panel_left)
        splitter.addWidget(map_container)
        # Mapa colado na tabela - painel com tamanho mínimo, mapa ocupa o resto
        splitter.setStretchFactor(0, 0)  # Painel mantém tamanho mínimo
        splitter.setStretchFactor(1, 1)  # Mapa expande para preencher
        splitter.setSizes([360, 1])  # Força painel no tamanho mínimo, mapa preenche
        splitter.setCollapsible(0, False)  # Painel não pode colapsar
        splitter.setCollapsible(1, False)  # Mapa não pode colapsar
        
        layout.addWidget(splitter)
        return tab
    
    def _create_panel_preparacao(self):
        """Cria o painel lateral da aba de preparação - layout responsivo."""
        # Painel principal SEM scroll - as listas internas têm scroll
        panel = QWidget()
        panel.setMinimumWidth(350)
        panel.setMaximumWidth(420)
        panel.setStyleSheet(f"background-color: {self.COLORS['cinza_claro']};")
        
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(8)
        
        # --- GRUPO: CAMADAS DE REFERÊNCIA (parte superior, flexível) ---
        group_layers = QGroupBox("Camadas de Referencia")
        group_layers.setStyleSheet(self._get_groupbox_style())
        group_layers.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        layers_layout = QVBoxLayout(group_layers)
        layers_layout.setSpacing(4)
        layers_layout.setContentsMargins(6, 0, 6, 6)
        
        # Seção: Seleção do GeoPackage
        path_layout = QHBoxLayout()
        path_layout.setSpacing(4)
        self.gpkg_path = QLineEdit()
        self.gpkg_path.setPlaceholderText("Selecione um GeoPackage...")
        self.gpkg_path.setStyleSheet(self._get_input_style())
        self.gpkg_path.setReadOnly(True)
        path_layout.addWidget(self.gpkg_path, 1)  # Stretch factor para expandir
        
        btn_browse = QPushButton("Abrir")
        btn_browse.setFixedSize(48, 22)
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.COLORS['verde_medio']};
                color: white;
                border: none;
                border-radius: 3px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: {self.COLORS['verde_escuro']};
            }}
        """)
        btn_browse.setToolTip("Abrir GeoPackage existente")
        btn_browse.clicked.connect(self._browse_and_load_gpkg)
        path_layout.addWidget(btn_browse)
        
        btn_new = QPushButton("Novo")
        btn_new.setFixedSize(48, 22)
        btn_new.setStyleSheet(f"""
            QPushButton {{
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 3px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #2980b9;
            }}
        """)
        btn_new.setToolTip("Criar novo GeoPackage")
        btn_new.clicked.connect(self._create_new_gpkg)
        path_layout.addWidget(btn_new)
        
        layers_layout.addLayout(path_layout)
        
        # Tabela de camadas (4 colunas: Camada, Data, Toggle, Ação) - FLEXÍVEL
        self.layers_table = QTableWidget()
        self.layers_table.setColumnCount(4)
        self.layers_table.setHorizontalHeaderLabels(["Camada", "Data", "", ""])
        self.layers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.layers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.layers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.layers_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.layers_table.setColumnWidth(1, 70)  # Data
        self.layers_table.setColumnWidth(2, 36)  # Toggle (ligar/desligar)
        self.layers_table.setColumnWidth(3, 65)  # Ação (Atualizar)
        self.layers_table.verticalHeader().setVisible(False)
        self.layers_table.verticalHeader().setDefaultSectionSize(26)  # Altura das linhas (compacta)
        self.layers_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.layers_table.setAlternatingRowColors(True)
        self.layers_table.setStyleSheet(self._get_table_style())
        # Tabela é flexível - expande com o espaço disponível
        self.layers_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.layers_table.setMinimumHeight(180)  # Mínimo para exibir várias linhas
        
        layers_layout.addWidget(self.layers_table, 1)  # stretch factor = 1
        
        # Layout inferior: info + botão baixar todas
        bottom_layout = QHBoxLayout()
        
        # Info sobre camadas (criar ANTES de popular a tabela)
        self.layers_info = QLabel("Selecione um GeoPackage para verificar as camadas")
        self.layers_info.setStyleSheet(f"color: {self.COLORS['texto_claro']}; font-size: 10px; font-style: italic;")
        bottom_layout.addWidget(self.layers_info, 1)
        
        # Botão "Baixar Todas" (inicialmente oculto)
        self.btn_download_all = QPushButton("Baixar Todas")
        self.btn_download_all.setFixedSize(90, 26)
        self.btn_download_all.setStyleSheet(f"""
            QPushButton {{
                background-color: #e67e22;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #d35400;
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
            }}
        """)
        self.btn_download_all.setToolTip("Baixar todas as camadas de referência")
        self.btn_download_all.clicked.connect(self._download_all_layers)
        self.btn_download_all.setVisible(False)  # Inicialmente oculto
        bottom_layout.addWidget(self.btn_download_all)
        
        layers_layout.addLayout(bottom_layout)
        
        # Barra de progresso
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(self._get_progress_style())
        self.progress_bar.setVisible(False)
        layers_layout.addWidget(self.progress_bar)
        
        # Preencher tabela com camadas do config (DEPOIS de criar widgets)
        self._populate_layers_from_config()
        
        panel_layout.addWidget(group_layers, 3)  # stretch factor = 3 (mais espaço)
        
        # --- GRUPO: MAPEAR VEGETAÇÃO (parte inferior) ---
        group_rvn = QGroupBox("Mapear Vegetação")
        group_rvn.setStyleSheet(self._get_groupbox_style())
        group_rvn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        rvn_layout = QVBoxLayout(group_rvn)
        rvn_layout.setSpacing(4)
        rvn_layout.setContentsMargins(6, 0, 6, 6)
        
        # Botão de Login Planet (dentro do grupo)
        login_layout = QHBoxLayout()
        
        self.btn_planet_login_rvn = QPushButton("Conectar Planet")
        self.btn_planet_login_rvn.setStyleSheet(f"""
            QPushButton {{
                background-color: #2ecc71;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #27ae60;
            }}
        """)
        self.btn_planet_login_rvn.clicked.connect(self._open_planet_dialog)
        login_layout.addWidget(self.btn_planet_login_rvn)
        
        self.planet_status_rvn = QLabel("Desconectado")
        self.planet_status_rvn.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
        login_layout.addWidget(self.planet_status_rvn)
        login_layout.addStretch()
        
        rvn_layout.addLayout(login_layout)
        
        # Separador
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {self.COLORS['cinza']}; max-height: 1px; margin: 5px 0;")
        rvn_layout.addWidget(sep)
        
        # Status dos Imóveis
        self.imoveis_status_rvn = QLabel("⚠ Carregue 'Imóveis a Analisar'")
        self.imoveis_status_rvn.setStyleSheet("color: #e67e22; font-size: 10px; font-weight: bold;")
        rvn_layout.addWidget(self.imoveis_status_rvn)
        
        # Botão buscar quads dos imóveis
        # Container horizontal para botão de busca + toggle de visualização
        buscar_quads_container = QWidget()
        buscar_quads_layout = QHBoxLayout(buscar_quads_container)
        buscar_quads_layout.setContentsMargins(0, 0, 0, 0)
        buscar_quads_layout.setSpacing(4)
        
        self.btn_buscar_quads_imoveis = QPushButton("Buscar Quads dos Imóveis")
        self.btn_buscar_quads_imoveis.setToolTip("Busca quads Planet que sobrepõem os imóveis")
        self.btn_buscar_quads_imoveis.setStyleSheet(self._get_button_style("primary"))
        self.btn_buscar_quads_imoveis.clicked.connect(self._buscar_quads_para_imoveis)
        self.btn_buscar_quads_imoveis.setEnabled(False)
        buscar_quads_layout.addWidget(self.btn_buscar_quads_imoveis, 1)
        
        # Botão toggle de visualização dos quads (olho)
        self.btn_toggle_quads_view = QPushButton("👁")
        self.btn_toggle_quads_view.setCheckable(True)
        self.btn_toggle_quads_view.setChecked(True)
        self.btn_toggle_quads_view.setFixedSize(24, 22)
        self.btn_toggle_quads_view.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_quads_view.setToolTip("Mostrar/ocultar quads no mapa")
        self._update_quads_toggle_style(True)
        self.btn_toggle_quads_view.clicked.connect(self._toggle_quads_visibility)
        
        # Botão de seleção por retângulo no mapa
        self.btn_selecionar_quads_mapa = QPushButton("⬚")
        self.btn_selecionar_quads_mapa.setCheckable(True)
        self.btn_selecionar_quads_mapa.setFixedSize(24, 22)
        self.btn_selecionar_quads_mapa.setCursor(Qt.PointingHandCursor)
        self.btn_selecionar_quads_mapa.setToolTip("Selecionar quads por retângulo no mapa\n(Desenhe um retângulo sobre os quads)")
        self._update_quad_select_tool_style(False)
        self.btn_selecionar_quads_mapa.clicked.connect(self._toggle_quad_selection_tool)
        buscar_quads_layout.addWidget(self.btn_toggle_quads_view)
        buscar_quads_layout.addWidget(self.btn_selecionar_quads_mapa)
        
        rvn_layout.addWidget(buscar_quads_container)
        
        # Lista de quads a processar - FLEXÍVEL
        quads_label = QLabel("Quads a Processar:")
        quads_label.setStyleSheet(f"font-weight: bold; color: {self.COLORS['texto']}; font-size: 10px;")
        rvn_layout.addWidget(quads_label)
        
        self.quads_list = QListWidget()
        self.quads_list.setSelectionMode(QListWidget.ExtendedSelection)  # Permite Ctrl+Click e Shift+Click
        # Lista flexível - é o elemento que se ajusta quando a barra de progresso aparece
        self.quads_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.quads_list.setMinimumHeight(40)  # Mínimo bem pequeno para dar espaço à barra de progresso
        self.quads_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {self.COLORS['cinza']};
                border-radius: 4px;
                font-size: 10px;
            }}
            QListWidget::item {{
                padding: 2px;
            }}
            QListWidget::item:selected {{
                background-color: #FFA500;
                color: white;
            }}
        """)
        self.quads_list.itemClicked.connect(self._on_quad_selected)
        self.quads_list.itemSelectionChanged.connect(self._highlight_selected_quads)  # Destacar no mapa
        rvn_layout.addWidget(self.quads_list, 1)  # stretch factor = 1
        
        # Info de quads
        self.quads_info = QLabel("Conecte ao Planet e busque quads")
        self.quads_info.setStyleSheet("color: #999; font-size: 9px;")
        rvn_layout.addWidget(self.quads_info)
        
        # Quad atual
        quad_atual_layout = QHBoxLayout()
        quad_atual_layout.addWidget(QLabel("Quad atual:"))
        self.quad_atual_label = QLabel("-")
        self.quad_atual_label.setStyleSheet("font-weight: bold; color: #3498db;")
        quad_atual_layout.addWidget(self.quad_atual_label)
        quad_atual_layout.addStretch()
        rvn_layout.addLayout(quad_atual_layout)
        
        # Separador
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"background-color: {self.COLORS['cinza']}; max-height: 1px; margin: 5px 0;")
        rvn_layout.addWidget(sep2)
        
        # Status das amostras
        self.amostras_status = QLabel("⚠ Selecione amostras de treinamento")
        self.amostras_status.setStyleSheet("color: #e67e22; font-size: 10px; font-weight: bold;")
        rvn_layout.addWidget(self.amostras_status)
        
        # Variável para guardar caminho do CSV selecionado
        self.amostras_csv_path = None
        
        # Botões de processamento (lado a lado: Amostras + Processar Seleção + Processar Todos)
        process_btns_layout = QHBoxLayout()
        process_btns_layout.setSpacing(6)
        
        # Botão Selecionar Amostras
        self.btn_selecionar_amostras = QPushButton("📁 Amostras")
        self.btn_selecionar_amostras.setToolTip("Selecionar arquivo CSV com amostras de treinamento")
        self.btn_selecionar_amostras.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        self.btn_selecionar_amostras.clicked.connect(self._selecionar_amostras_csv)
        process_btns_layout.addWidget(self.btn_selecionar_amostras)
        
        self.btn_mapear_veg = QPushButton("Processar Seleção")
        self.btn_mapear_veg.setToolTip("Processa o quad selecionado: download, classificação e salvamento")
        self.btn_mapear_veg.setStyleSheet(f"""
            QPushButton {{
                background-color: #27ae60;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #219a52;
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
            }}
        """)
        self.btn_mapear_veg.clicked.connect(self._mapear_vegetacao_quad_atual)
        self.btn_mapear_veg.setEnabled(False)
        process_btns_layout.addWidget(self.btn_mapear_veg)
        
        self.btn_processar_todos = QPushButton("⚡ Processar Todos")
        self.btn_processar_todos.setToolTip("Processa todos os quads pendentes automaticamente (~1 min cada)")
        self.btn_processar_todos.setStyleSheet(f"""
            QPushButton {{
                background-color: #8e44ad;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #7d3c98;
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
            }}
        """)
        self.btn_processar_todos.clicked.connect(self._processar_todos_quads)
        self.btn_processar_todos.setEnabled(False)
        process_btns_layout.addWidget(self.btn_processar_todos)
        
        rvn_layout.addLayout(process_btns_layout)
        
        # Barra de progresso
        self.rvn_progress = QProgressBar()
        self.rvn_progress.setStyleSheet(self._get_progress_style())
        self.rvn_progress.setVisible(False)
        rvn_layout.addWidget(self.rvn_progress)
        
        # Status do processamento
        self.rvn_status = QLabel("")
        self.rvn_status.setStyleSheet("color: #666; font-size: 9px; font-style: italic;")
        self.rvn_status.setWordWrap(True)
        rvn_layout.addWidget(self.rvn_status)
        
        # Armazenar dados de quads
        self.planet_quads = []
        self.quads_processados = set()  # IDs de quads já processados
        self.quad_atual_idx = -1
        self.imoveis_layer_rvn = None
        
        panel_layout.addWidget(group_rvn, 2)  # stretch factor = 2 (menos espaço que camadas)
        
        # Adiciona stretch antes do grupo Elegibilidade para empurrá-lo para baixo
        panel_layout.addStretch()
        
        # --- GRUPO: ELEGIBILIDADE (fixado na parte inferior) ---
        group_elegibilidade = QGroupBox("Elegibilidade")
        group_elegibilidade.setStyleSheet(self._get_groupbox_style())
        group_elegibilidade.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        elegibilidade_layout = QVBoxLayout(group_elegibilidade)
        elegibilidade_layout.setSpacing(4)
        elegibilidade_layout.setContentsMargins(6, 0, 6, 6)
        
        # Status do processamento
        self.processamento_status = QLabel("⚠ Configure os parâmetros antes de processar")
        self.processamento_status.setStyleSheet("color: #e67e22; font-size: 10px; font-weight: bold;")
        self.processamento_status.setWordWrap(True)
        elegibilidade_layout.addWidget(self.processamento_status)
        
        # Botões lado a lado: Configurar + Processar + Laudos
        proc_btns_layout = QHBoxLayout()
        proc_btns_layout.setSpacing(4)
        
        # Botão Configurar Análise
        self.btn_configurar_analise = QPushButton("⚙ Configurar")
        self.btn_configurar_analise.setToolTip("Configurar parâmetros da análise de elegibilidade")
        self.btn_configurar_analise.setStyleSheet(f"""
            QPushButton {{
                background-color: #3498db;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #2980b9;
            }}
        """)
        self.btn_configurar_analise.clicked.connect(self._abrir_config_processamento)
        proc_btns_layout.addWidget(self.btn_configurar_analise)
        
        # Botão Processar (antes era "Processar Elegibilidade")
        self.btn_iniciar_processamento = QPushButton("▶ Processar")
        self.btn_iniciar_processamento.setToolTip("Executar análise completa de elegibilidade nos imóveis")
        self.btn_iniciar_processamento.setStyleSheet(f"""
            QPushButton {{
                background-color: #27ae60;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #219a52;
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
            }}
        """)
        self.btn_iniciar_processamento.clicked.connect(self._iniciar_processamento_elegiveis)
        self.btn_iniciar_processamento.setEnabled(False)
        proc_btns_layout.addWidget(self.btn_iniciar_processamento)
        
        # Botão Reprocessar Parecer
        self.btn_reprocessar_parecer = QPushButton("🔄 Parecer")
        self.btn_reprocessar_parecer.setToolTip("Reprocessar apenas o julgamento e parecer final (rápido)")
        self.btn_reprocessar_parecer.setEnabled(False)
        self.btn_reprocessar_parecer.clicked.connect(self._reprocessar_parecer)
        self.btn_reprocessar_parecer.setStyleSheet(f"""
            QPushButton {{
                background-color: #8e44ad;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #7d3c98;
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
            }}
        """)
        proc_btns_layout.addWidget(self.btn_reprocessar_parecer)
        
        # Botão Laudos (antes estava em grupo separado)
        self.btn_visualizar_laudos = QPushButton("📊 Laudos")
        self.btn_visualizar_laudos.setToolTip("Visualizar resultados e gerar laudos em PDF")
        self.btn_visualizar_laudos.setEnabled(False)
        self.btn_visualizar_laudos.clicked.connect(self._abrir_visualizador_laudos)
        self.btn_visualizar_laudos.setStyleSheet(f"""
            QPushButton {{
                background-color: #e67e22;
                color: white;
                border: none;
                padding: 6px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: #d35400;
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
            }}
        """)
        proc_btns_layout.addWidget(self.btn_visualizar_laudos)
        
        elegibilidade_layout.addLayout(proc_btns_layout)
        
        # Barra de progresso do processamento
        self.processamento_progress = QProgressBar()
        self.processamento_progress.setStyleSheet(self._get_progress_style())
        self.processamento_progress.setVisible(False)
        elegibilidade_layout.addWidget(self.processamento_progress)
        
        # Log resumido do processamento / Info de laudos
        self.processamento_log = QLabel("")
        self.processamento_log.setStyleSheet("color: #666; font-size: 9px; font-style: italic;")
        self.processamento_log.setWordWrap(True)
        elegibilidade_layout.addWidget(self.processamento_log)
        
        # Layout inferior: info + botão salvar log
        info_log_layout = QHBoxLayout()
        info_log_layout.setSpacing(4)
        
        self.laudos_info = QLabel("")
        self.laudos_info.setStyleSheet(f"color: {self.COLORS['texto_claro']}; font-size: 10px;")
        info_log_layout.addWidget(self.laudos_info, 1)
        
        self.btn_salvar_log = QToolButton()
        self.btn_salvar_log.setIcon(self.style().standardIcon(self.style().SP_DialogSaveButton))
        self.btn_salvar_log.setToolTip("Salvar .txt do Log")
        self.btn_salvar_log.setFixedSize(24, 24)
        self.btn_salvar_log.setEnabled(False)
        self.btn_salvar_log.setStyleSheet("""
            QToolButton {
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #f9f9f9;
            }
            QToolButton:hover {
                background-color: #e0e0e0;
                border-color: #999;
            }
            QToolButton:disabled {
                background-color: #f0f0f0;
                border-color: #ddd;
            }
        """)
        self.btn_salvar_log.clicked.connect(self._salvar_log_txt)
        info_log_layout.addWidget(self.btn_salvar_log)
        
        elegibilidade_layout.addLayout(info_log_layout)
        
        # Variáveis de configuração do processamento
        self.config_processamento = None
        
        panel_layout.addWidget(group_elegibilidade)
        
        return panel
    
    def _create_map_area(self):
        """Cria a area do mapa com painel Planet."""
        map_container = QWidget()
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 0, 0, 0)  # Totalmente colado ao painel
        map_layout.setSpacing(2)
        
        # Toolbar do mapa
        map_toolbar = QHBoxLayout()
        
        # Estilo especifico para botoes pequenos da toolbar
        toolbar_btn_style = f"""
            QPushButton {{
                background-color: {self.COLORS['cinza']};
                color: {self.COLORS['texto']};
                border: 1px solid #bdc3c7;
                padding: 4px 8px;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #bdc3c7;
            }}
        """
        
        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setToolTip("Aproximar")
        btn_zoom_in.setFixedSize(28, 26)
        
        btn_zoom_out = QPushButton("-")
        btn_zoom_out.setToolTip("Afastar")
        btn_zoom_out.setFixedSize(28, 26)
        
        for btn in [btn_zoom_in, btn_zoom_out]:
            btn.setStyleSheet(toolbar_btn_style)
            map_toolbar.addWidget(btn)
        
        map_toolbar.addStretch()
        
        # Combo de basemap
        map_toolbar.addWidget(QLabel("Base:"))
        self.combo_basemap = QComboBox()
        # Carregar basemaps do config
        basemaps = self.config.get('basemaps', {})
        for key, info in basemaps.items():
            self.combo_basemap.addItem(info.get('nome', key))
        self.combo_basemap.setStyleSheet(self._get_combo_style())
        self.combo_basemap.setMinimumWidth(140)
        self.combo_basemap.currentTextChanged.connect(self._change_basemap)
        map_toolbar.addWidget(self.combo_basemap)
        
        # Armazenar URLs de mosaicos Planet adicionados
        self.planet_basemap_urls = {}
        
        map_layout.addLayout(map_toolbar)
        
        # Armazenar dados dos mosaicos Planet
        self.planet_mosaics = []
        self.current_planet_mosaic = None
        self.planet_logged_in = False
        
        # Canvas do mapa
        self.map_canvas = QgsMapCanvas()
        self.map_canvas.setCanvasColor(QColor(240, 245, 240))
        self.map_canvas.enableAntiAliasing(True)
        
        # Definir CRS para WGS84 (necessario para tiles XYZ)
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        self.map_canvas.setDestinationCrs(crs)
        
        # Centralizar no Brasil - Amazonia Legal
        brasil_extent = QgsRectangle(-74.0, -18.0, -44.0, 5.0)
        self.map_canvas.setExtent(brasil_extent)
        
        # Ferramentas de navegacao
        self.map_tool_pan = QgsMapToolPan(self.map_canvas)
        self.map_canvas.setMapTool(self.map_tool_pan)
        
        # Conectar botoes de zoom
        btn_zoom_in.clicked.connect(self._zoom_in)
        btn_zoom_out.clicked.connect(self._zoom_out)
        
        map_layout.addWidget(self.map_canvas)
        
        # Carregar basemap padrao (OpenStreetMap)
        self._load_default_basemap()
        
        return map_container
    
    # =========================================================================
    #                         FUNCOES PLANET
    # =========================================================================
    
    def _open_planet_dialog(self):
        """Abre diálogo Planet - login ou seleção de mosaicos se já logado."""
        if not PLANET_AVAILABLE:
            QMessageBox.warning(self, "Erro", "Módulo Planet não disponível")
            return
        
        self._planet_login_dialog()
    
    def _planet_login_dialog(self):
        """Abre dialogo de login Planet com lista de mosaicos."""
        if not PLANET_AVAILABLE:
            QMessageBox.warning(self, "Erro", "Modulo Planet nao disponivel")
            return
        
        # Criar diálogo - tamanho dinâmico baseado no conteúdo
        self.planet_dialog = QDialog(self)
        self.planet_dialog.setWindowTitle("Planet Basemaps")
        self.planet_dialog.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        
        layout = QVBoxLayout(self.planet_dialog)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        
        # === SEÇÃO DE LOGIN ===
        self.login_group = QGroupBox("Login Planet")
        login_layout = QVBoxLayout(self.login_group)
        login_layout.setSpacing(6)
        login_layout.setContentsMargins(10, 15, 10, 10)
        
        # Status
        self.planet_status_label = QLabel("Desconectado")
        self.planet_status_label.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 12px;")
        self.planet_status_label.setAlignment(Qt.AlignCenter)
        login_layout.addWidget(self.planet_status_label)
        
        # Form de login com Email e Senha
        form = QFormLayout()
        form.setSpacing(6)
        
        self.planet_email = QLineEdit()
        self.planet_email.setPlaceholderText("seu.email@dominio.com")
        self.planet_email.setMinimumWidth(280)
        form.addRow("Email:", self.planet_email)
        
        self.planet_password = QLineEdit()
        self.planet_password.setEchoMode(QLineEdit.Password)
        self.planet_password.setPlaceholderText("Sua senha Planet")
        form.addRow("Senha:", self.planet_password)
        
        # Checkbox lembrar credenciais
        self.remember_planet_credentials = QCheckBox("Lembrar credenciais")
        self.remember_planet_credentials.setChecked(True)
        form.addRow("", self.remember_planet_credentials)
        
        login_layout.addLayout(form)
        
        # Carregar credenciais salvas
        self._load_planet_credentials()
        
        # Botão de login/logout centralizado
        self.btn_planet_connect = QPushButton("Conectar")
        self.btn_planet_connect.setStyleSheet(self._get_button_style("success"))
        self.btn_planet_connect.clicked.connect(self._do_planet_login_dialog)
        login_layout.addWidget(self.btn_planet_connect)
        
        self.btn_planet_disconnect = QPushButton("Desconectar")
        self.btn_planet_disconnect.setStyleSheet(self._get_button_style("secondary"))
        self.btn_planet_disconnect.clicked.connect(self._do_planet_logout)
        self.btn_planet_disconnect.setVisible(False)
        login_layout.addWidget(self.btn_planet_disconnect)
        
        layout.addWidget(self.login_group)
        
        # === SEÇÃO DE MOSAICOS (inicialmente oculta) ===
        self.mosaics_group = QGroupBox("Mosaicos Disponíveis")
        mosaics_layout = QVBoxLayout(self.mosaics_group)
        mosaics_layout.setSpacing(8)
        mosaics_layout.setContentsMargins(10, 15, 10, 10)
        
        # Info
        info_label = QLabel("Selecione um mosaico para carregar no mapa:")
        info_label.setStyleSheet("color: #666; font-size: 11px;")
        mosaics_layout.addWidget(info_label)
        
        # Lista de mosaicos com altura fixa
        self.mosaics_list = QListWidget()
        self.mosaics_list.setFixedHeight(180)
        self.mosaics_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {self.COLORS['cinza']};
                border-radius: 4px;
                font-size: 11px;
            }}
            QListWidget::item {{
                padding: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {self.COLORS['verde_lima']};
                color: {self.COLORS['texto']};
            }}
        """)
        self.mosaics_list.itemDoubleClicked.connect(self._on_mosaic_selected)
        mosaics_layout.addWidget(self.mosaics_list)
        
        # Botão de carregar (fora da lista, com margem superior)
        self.btn_load_mosaic = QPushButton("Carregar Mosaico Selecionado")
        self.btn_load_mosaic.setStyleSheet(self._get_button_style("primary"))
        self.btn_load_mosaic.clicked.connect(self._on_mosaic_selected)
        mosaics_layout.addWidget(self.btn_load_mosaic)
        
        self.mosaics_group.setVisible(False)
        layout.addWidget(self.mosaics_group)
        
        # Espaçador flexível
        layout.addStretch()
        
        # Botão fechar
        btn_close = QPushButton("Fechar")
        btn_close.setStyleSheet(self._get_button_style("secondary"))
        btn_close.clicked.connect(self.planet_dialog.accept)
        layout.addWidget(btn_close)
        
        # Se já estiver logado, mostrar mosaicos
        if planet_client.is_logged_in:
            self._update_planet_dialog_ui(True)
            self._load_planet_mosaics_to_list()
        
        # Ajustar tamanho ao conteúdo
        self.planet_dialog.adjustSize()
        
        self.planet_dialog.exec_()
    
    def _do_planet_login_dialog(self):
        """Executa o login Planet a partir do diálogo usando email/senha."""
        email = self.planet_email.text().strip()
        password = self.planet_password.text()
        
        if not email or not password:
            QMessageBox.warning(self, "Aviso", "Informe email e senha")
            return
        
        self._update_status("Conectando ao Planet...")
        self.planet_status_label.setText("Conectando...")
        self.planet_status_label.setStyleSheet("color: #f39c12; font-weight: bold;")
        QApplication.processEvents()
        
        success, message = planet_client.login(email, password)
        
        if success:
            self._update_planet_dialog_ui(True)
            self._load_planet_mosaics_to_list()
            self._update_status("Conectado ao Planet!")
            self.planet_logged_in = True
            
            # Salvar credenciais se checkbox marcado
            self._save_planet_credentials()
            
            # Atualizar status no grupo RVN
            self.planet_status_rvn.setText("Conectado")
            self.planet_status_rvn.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
            self.btn_planet_login_rvn.setText("Reconectar ou Trocar Mosaico")
            
            # Verificar se imóveis estão carregados e habilitar busca de quads
            if self._verificar_imoveis_carregados():
                self.btn_buscar_quads_imoveis.setEnabled(True)
        else:
            self.planet_status_label.setText("Erro: " + message[:50])
            self.planet_status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            self._update_status("Erro no login Planet")
    
    def _do_planet_logout(self):
        """Faz logout do Planet."""
        planet_client.logout()
        self._update_planet_dialog_ui(False)
        self.planet_logged_in = False
        # Atualizar status no grupo RVN
        self.planet_status_rvn.setText("Desconectado")
        self.planet_status_rvn.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
        self.btn_planet_login_rvn.setText("Conectar Planet")
        self.btn_buscar_quads_imoveis.setEnabled(False)
        self.btn_mapear_veg.setEnabled(False)
        self.btn_processar_todos.setEnabled(False)
        
        # Remover "Planet" do combo de basemaps se existir
        idx = self.combo_basemap.findText("Planet")
        if idx >= 0:
            self.combo_basemap.removeItem(idx)
        
        self._update_status("Desconectado do Planet")
    
    def _update_planet_dialog_ui(self, logged_in):
        """Atualiza UI do diálogo Planet baseado no status de login."""
        if logged_in:
            self.planet_status_label.setText("Conectado!")
            self.planet_status_label.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 12px;")
            self.btn_planet_connect.setVisible(False)
            self.btn_planet_disconnect.setVisible(True)
            self.planet_email.setEnabled(False)
            self.planet_password.setEnabled(False)
            self.mosaics_group.setVisible(True)
        else:
            self.planet_status_label.setText("Desconectado")
            self.planet_status_label.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 12px;")
            self.btn_planet_connect.setVisible(True)
            self.btn_planet_disconnect.setVisible(False)
            self.planet_email.setEnabled(True)
            self.planet_password.setEnabled(True)
            self.mosaics_group.setVisible(False)
            self.mosaics_list.clear()
            self.planet_mosaics = []
        
        # Ajustar tamanho do diálogo ao novo conteúdo
        if hasattr(self, 'planet_dialog') and self.planet_dialog:
            self.planet_dialog.adjustSize()
    
    def _load_planet_mosaics_to_list(self):
        """Carrega lista de mosaicos no QListWidget do diálogo."""
        if not planet_client.is_logged_in:
            return
        
        self._update_status("Carregando mosaicos Planet...")
        self.mosaics_list.clear()
        QApplication.processEvents()
        
        # Buscar series - PS Tropical Normalized Analytic Monthly Monitoring
        series_list = planet_client.list_tropical_series()
        
        mosaics = []
        if series_list:
            for series in series_list:
                series_id = series.get("id")
                if series_id:
                    series_mosaics = planet_client.get_mosaics_for_series(series_id)
                    mosaics.extend(series_mosaics)
        
        if not mosaics:
            # Tentar buscar diretamente
            mosaics = planet_client.list_all_mosaics("ps_tropical_normalized_analytic")
        
        self.planet_mosaics = mosaics
        
        # Adicionar à lista (sem limite!)
        for mosaic in mosaics:
            display_name = planet_client.get_mosaic_display_name(mosaic)
            mosaic_name = mosaic.get("name", "")
            self.mosaics_list.addItem(f"{display_name} ({mosaic_name})")
        
        if mosaics:
            self._update_status(f"Carregados {len(mosaics)} mosaicos Planet")
        else:
            self._update_status("Nenhum mosaico encontrado")
    
    def _on_mosaic_selected(self, item=None):
        """Quando um mosaico é selecionado, adiciona ao combo de basemaps e carrega."""
        current_row = self.mosaics_list.currentRow()
        if current_row < 0 or current_row >= len(self.planet_mosaics):
            QMessageBox.warning(self, "Aviso", "Selecione um mosaico da lista")
            return
        
        mosaic = self.planet_mosaics[current_row]
        self.current_planet_mosaic = mosaic
        
        # Obter URL do mosaico
        mosaic_name = mosaic.get('name', '')
        tile_url = planet_client.get_tile_url(mosaic_name)
        
        if not tile_url:
            QMessageBox.warning(self, "Erro", "Não foi possível obter URL do mosaico")
            return
        
        # Armazenar URL para o combo de basemaps
        self.planet_basemap_urls["Planet"] = tile_url
        
        # Adicionar "Planet" ao combo de basemaps se não existir
        if self.combo_basemap.findText("Planet") < 0:
            self.combo_basemap.addItem("Planet")
        
        # Bloquear sinal para evitar troca dupla
        self.combo_basemap.blockSignals(True)
        self.combo_basemap.setCurrentText("Planet")
        self.combo_basemap.blockSignals(False)
        
        # Carregar no mapa
        self._load_planet_basemap()
        
        # Fechar diálogo
        self.planet_dialog.accept()
        
        self._update_status(f"Planet: {planet_client.get_mosaic_display_name(mosaic)}")
    
    def _load_planet_mosaics(self):
        """Carrega lista de mosaicos PS Tropical Normalized Analytic Monthly Monitoring."""
        if not planet_client.is_logged_in:
            return
        
        self._update_status("Carregando mosaicos Planet...")
        QApplication.processEvents()
        self.combo_planet_mosaic.clear()
        
        # Buscar series - filtrado para PS Tropical Normalized Analytic Monthly Monitoring
        series_list = planet_client.list_tropical_series()
        
        if not series_list:
            # Tentar buscar diretamente os mosaicos
            self._update_status("Buscando mosaicos diretamente...")
            self.planet_mosaics = planet_client.list_all_mosaics("ps_tropical_normalized_analytic")
        else:
            # Buscar a serie correta
            target_series = None
            for s in series_list:
                name = s.get("name", "").lower()
                if "tropical" in name and "monthly" in name and "normalized" in name:
                    target_series = s
                    break
            
            if not target_series:
                target_series = series_list[0]
            
            self._update_status(f"Serie: {target_series.get('name', '')[:40]}...")
            QApplication.processEvents()
            
            # Carregar mosaicos da serie
            self.planet_mosaics = planet_client.get_mosaics_for_series(target_series['id'])
        
        if self.planet_mosaics:
            # Adicionar apenas mosaicos mensais (ultimos 24 meses)
            count = 0
            for mosaic in self.planet_mosaics:
                if count >= 24:
                    break
                # Verificar se e mensal (interval = "1 mon")
                interval = mosaic.get("interval", "")
                if interval == "1 mon" or not interval:  # Incluir se nao tiver intervalo definido
                    display_name = planet_client.get_mosaic_display_name(mosaic)
                    self.combo_planet_mosaic.addItem(display_name, mosaic)
                    count += 1
            
            self._update_status(f"{count} mosaicos mensais carregados")
            
            # Selecionar primeiro automaticamente
            if self.combo_planet_mosaic.count() > 0:
                self.combo_planet_mosaic.setCurrentIndex(0)
                self.current_planet_mosaic = self.combo_planet_mosaic.itemData(0)
        else:
            self._update_status("Nenhum mosaico encontrado")
    
    def _on_planet_mosaic_changed(self, index):
        """Chamado quando mosaico e selecionado."""
        if index >= 0:
            self.current_planet_mosaic = self.combo_planet_mosaic.itemData(index)
    
    def _load_planet_basemap(self):
        """Carrega mosaico Planet como basemap no mapa."""
        if not self.current_planet_mosaic:
            QMessageBox.warning(self, "Aviso", "Selecione um mosaico primeiro")
            return
        
        mosaic_name = self.current_planet_mosaic.get('name', '')
        tile_url = planet_client.get_tile_url(mosaic_name)
        
        if not tile_url:
            QMessageBox.warning(self, "Erro", "Não foi possível obter URL do mosaico")
            return
        
        self._update_status("Carregando basemap Planet...")
        QApplication.processEvents()
        
        layer_name = planet_client.get_mosaic_display_name(self.current_planet_mosaic)
        
        # Criar camada XYZ - usar URL diretamente
        uri = f"type=xyz&zmin=0&zmax=22&url={tile_url}"
        layer = QgsRasterLayer(uri, f"Planet - {layer_name}", "wms")
        
        if layer.isValid():
            # Remover basemap anterior se existir (tanto Planet quanto outros)
            if hasattr(self, 'basemap_layer') and self.basemap_layer:
                try:
                    current_layers = list(self.map_canvas.layers())
                    current_layers = [l for l in current_layers if l.id() != self.basemap_layer.id()]
                    self.map_canvas.setLayers(current_layers)
                except:
                    pass
            
            # Também remover camada Planet anterior se existir
            if hasattr(self, 'planet_layer') and self.planet_layer:
                try:
                    current_layers = list(self.map_canvas.layers())
                    current_layers = [l for l in current_layers if l.id() != self.planet_layer.id()]
                    self.map_canvas.setLayers(current_layers)
                except:
                    pass
            
            self.planet_layer = layer
            self.basemap_layer = layer  # Armazenar também como basemap
            
            # Adicionar ao projeto QGIS
            QgsProject.instance().addMapLayer(layer, False)
            
            # Adicionar no fundo (como basemap)
            current_layers = list(self.map_canvas.layers())
            current_layers.append(layer)  # Adicionar no fundo
            self.map_canvas.setLayers(current_layers)
            
            # Centralizar na Amazonia
            amazonia_extent = QgsRectangle(-74.0, -18.0, -44.0, 5.0)
            self.map_canvas.setExtent(amazonia_extent)
            self.map_canvas.refresh()
            
            self._update_status(f"✓ Mosaico '{layer_name}' carregado")
        else:
            # Tentar formato alternativo
            uri_alt = f"type=xyz&url={tile_url}&zmax=22&zmin=0"
            layer_alt = QgsRasterLayer(uri_alt, f"Planet - {layer_name}", "wms")
            
            if layer_alt.isValid():
                self.planet_layer = layer_alt
                QgsProject.instance().addMapLayer(layer_alt, False)
                
                current_layers = list(self.map_canvas.layers())
                current_layers.insert(0, layer_alt)
                self.map_canvas.setLayers(current_layers)
                
                amazonia_extent = QgsRectangle(-74.0, -18.0, -44.0, 5.0)
                self.map_canvas.setExtent(amazonia_extent)
                self.map_canvas.refresh()
                
                self._update_status(f"Planet {layer_name} carregado!")
            else:
                error_msg = layer.error().message() if layer.error() else "Erro na camada"
                QMessageBox.warning(self, "Erro ao Carregar", 
                    f"Nao foi possivel carregar o mosaico Planet.\n\n"
                    f"Verifique sua conexao com a internet.\n\n"
                    f"Detalhes: {error_msg}")
    
    def _get_map_extent_wgs84(self):
        """Retorna extent atual do mapa em WGS84."""
        extent = self.map_canvas.extent()
        crs = self.map_canvas.mapSettings().destinationCrs()
        
        if crs.authid() != "EPSG:4326":
            # Transformar para WGS84
            transform = QgsCoordinateTransform(
                crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance()
            )
            extent = transform.transformBoundingBox(extent)
        
        return (extent.xMinimum(), extent.yMinimum(), 
                extent.xMaximum(), extent.yMaximum())
    
    def _populate_layers_from_config(self):
        """Preenche a tabela com as camadas necessárias do config."""
        camadas = self.config.get('camadas_referencia', {})
        gpkg_path = self.gpkg_path.text() if hasattr(self, 'gpkg_path') else ""
        
        # Obter lista de camadas existentes no GeoPackage
        gpkg_layers = self._get_gpkg_layer_names(gpkg_path) if gpkg_path else []
        
        self.layers_table.setRowCount(len(camadas))
        
        # Dicionário para guardar estado das camadas no mapa
        if not hasattr(self, 'layer_visibility'):
            self.layer_visibility = {}
        
        ausentes = 0
        presentes = 0
        
        for i, (key, info) in enumerate(camadas.items()):
            # Nome da camada (renomear RVN para mais curto)
            nome = info.get('nome', key)
            if 'Remanescente' in nome:
                nome = "Vegetacao Nativa"
            item_nome = QTableWidgetItem(nome)
            item_nome.setData(Qt.UserRole, key)
            item_nome.setToolTip(info.get('descricao', nome))
            self.layers_table.setItem(i, 0, item_nome)
            
            # Verificar se camada existe no GeoPackage
            layer_exists = self._check_layer_exists(key, gpkg_layers)
            
            if layer_exists:
                presentes += 1
            else:
                ausentes += 1
            
            # Data de atualização (obter do GeoPackage se existir)
            if layer_exists and gpkg_path:
                data_str = self._get_layer_date(gpkg_path, key, gpkg_layers)
            else:
                data_str = "-"
            
            item_data = QTableWidgetItem(data_str)
            item_data.setTextAlignment(Qt.AlignCenter)
            # Cor vermelha se ausente
            if not layer_exists and gpkg_path:
                item_data.setForeground(QColor("#e74c3c"))
            self.layers_table.setItem(i, 1, item_data)
            
            # COLUNA 2: Toggle (ligar/desligar camada no mapa)
            toggle_widget = QWidget()
            toggle_layout = QHBoxLayout(toggle_widget)
            toggle_layout.setContentsMargins(2, 2, 2, 2)
            toggle_layout.setAlignment(Qt.AlignCenter)
            
            btn_toggle = QPushButton("👁")
            btn_toggle.setCheckable(True)
            btn_toggle.setFixedSize(22, 18)
            btn_toggle.setCursor(Qt.PointingHandCursor)
            btn_toggle.setEnabled(layer_exists)
            
            # Estado inicial
            is_visible = self.layer_visibility.get(key, False)
            btn_toggle.setChecked(is_visible)
            
            # Estilo do toggle - sempre com olho, cor indica estado
            if layer_exists:
                if is_visible:
                    btn_toggle.setStyleSheet("""
                        QPushButton {
                            background-color: #27ae60;
                            border: 1px solid #1e8449;
                            border-radius: 3px;
                            color: white;
                            font-size: 10px;
                            padding: 0px;
                        }
                        QPushButton:hover {
                            background-color: #2ecc71;
                        }
                    """)
                else:
                    btn_toggle.setStyleSheet("""
                        QPushButton {
                            background-color: #95a5a6;
                            border: 1px solid #7f8c8d;
                            border-radius: 3px;
                            color: #bdc3c7;
                            font-size: 10px;
                            padding: 0px;
                        }
                        QPushButton:hover {
                            background-color: #7f8c8d;
                        }
                    """)
            else:
                btn_toggle.setStyleSheet("""
                    QPushButton {
                        background-color: #ecf0f1;
                        border: 1px solid #ddd;
                        border-radius: 3px;
                        color: #ccc;
                        font-size: 10px;
                        padding: 0px;
                    }
                """)
            
            btn_toggle.clicked.connect(lambda checked, k=key, btn=btn_toggle: self._toggle_layer_visibility(k, checked, btn))
            toggle_layout.addWidget(btn_toggle)
            self.layers_table.setCellWidget(i, 2, toggle_widget)
            
            # COLUNA 3: Botão de ação (Atualizar para WFS/download ou Local)
            btn_action = QPushButton()
            btn_action.setFixedSize(50, 18)
            btn_action.setCursor(Qt.PointingHandCursor)
            
            tipo = info.get('tipo', 'local')
            
            # Priorizar o tipo definido na configuração
            # Se tipo é 'local', mostrar botão Local independente de ter url_download
            if tipo == 'local':
                # Camada local - botão para selecionar arquivo (Shapefile ou GeoPackage)
                btn_action.setText("Local")
                btn_action.setToolTip("Carregar arquivo local (Shapefile ou GeoPackage)")
                btn_action.setStyleSheet("""
                    QPushButton {
                        background-color: #9b59b6;
                        color: white;
                        border: 1px solid #8e44ad;
                        border-radius: 3px;
                        font-size: 8px;
                        font-weight: bold;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: #8e44ad;
                    }
                """)
                btn_action.clicked.connect(lambda checked, k=key: self._select_local_layer(k))
            else:
                # WFS, download ou outros tipos - mostrar "Atualizar"
                btn_action.setText("Atualizar")
                btn_action.setToolTip("Baixar/atualizar dados da fonte oficial")
                btn_action.setStyleSheet("""
                    QPushButton {
                        background-color: #3498db;
                        color: white;
                        border: 1px solid #2980b9;
                        border-radius: 3px;
                        font-size: 8px;
                        font-weight: bold;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: #2980b9;
                    }
                """)
                btn_action.clicked.connect(lambda checked, k=key: self._update_layer_from_source(k))
            
            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.addWidget(btn_action)
            cell_layout.setAlignment(Qt.AlignCenter)
            cell_layout.setContentsMargins(2, 2, 2, 2)
            self.layers_table.setCellWidget(i, 3, cell_widget)
        
        # Atualizar info (resumo na base)
        total = len(camadas)
        if gpkg_path and os.path.exists(gpkg_path):
            self.layers_info.setText(f"{presentes} presentes, {ausentes} ausentes de {total} necessarias")
            if ausentes > 0:
                self.layers_info.setStyleSheet(f"color: #e74c3c; font-size: 10px; font-weight: bold;")
            else:
                self.layers_info.setStyleSheet(f"color: {self.COLORS['verde_medio']}; font-size: 10px; font-weight: bold;")
        else:
            self.layers_info.setText(f"{total} camadas necessarias - selecione um GeoPackage")
            self.layers_info.setStyleSheet(f"color: {self.COLORS['texto_claro']}; font-size: 10px; font-style: italic;")
    
    def _get_gpkg_layer_names(self, gpkg_path):
        """Obtém lista de nomes de camadas do GeoPackage (nomes originais)."""
        if not gpkg_path or not os.path.exists(gpkg_path):
            return []
        
        try:
            import sqlite3
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()
            cursor.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
            # Retornar nomes ORIGINAIS (preservar case e acentos)
            layer_names = [row[0] for row in cursor.fetchall()]
            conn.close()
            return layer_names
        except:
            return []
    
    def _get_table_name(self, layer_key):
        """Retorna o nome padronizado da tabela no GeoPackage para uma chave de camada.
        
        Centraliza o mapeamento de nomes para garantir consistência entre
        diferentes formas de importar/salvar camadas.
        """
        # Mapeamento de nomes padronizados
        name_mapping = {
            'rvn': 'vegetacao_nativa',  # Vegetação nativa sempre como "vegetacao_nativa"
        }
        
        if layer_key in name_mapping:
            return name_mapping[layer_key]
        
        # Padrão: remover underscores
        return layer_key.replace('_', '')
    
    def _get_layer_date(self, gpkg_path, config_key, gpkg_layers):
        """Obtém a data de última modificação de uma camada do GeoPackage."""
        try:
            import sqlite3
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()
            
            # Encontrar o nome real da camada no gpkg
            real_layer_name = None
            for gpkg_layer in gpkg_layers:
                if self._check_layer_exists(config_key, [gpkg_layer]):
                    # gpkg_layer já é o nome original
                    real_layer_name = gpkg_layer
                    break
            
            if real_layer_name:
                cursor.execute("SELECT last_change FROM gpkg_contents WHERE table_name = ?", (real_layer_name,))
                result = cursor.fetchone()
                conn.close()
                
                if result and result[0]:
                    data_str = result[0]
                    if 'T' in data_str:
                        data_str = data_str.split('T')[0]
                    # Converter para formato brasileiro
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(data_str, "%Y-%m-%d")
                        return dt.strftime("%d/%m/%y")
                    except:
                        return data_str[:10]
            
            conn.close()
            # Fallback: data do arquivo
            from datetime import datetime
            mtime = os.path.getmtime(gpkg_path)
            return datetime.fromtimestamp(mtime).strftime("%d/%m/%y")
            
        except:
            return "-"
    
    def _check_layer_exists(self, config_key, gpkg_layers):
        """Verifica se uma camada do config existe no GeoPackage."""
        if not gpkg_layers:
            return False
        
        # Função para normalizar texto (remover acentos e caracteres especiais)
        def normalize(text):
            import unicodedata
            # Normalizar unicode e remover acentos
            nfkd = unicodedata.normalize('NFKD', text.lower())
            ascii_text = ''.join(c for c in nfkd if not unicodedata.combining(c))
            # Remover underscores, espaços, hífens
            return ascii_text.replace('_', '').replace(' ', '').replace('-', '')
        
        key_norm = normalize(config_key)
        
        for gpkg_layer in gpkg_layers:
            gpkg_norm = normalize(gpkg_layer)
            
            # Verificar correspondência parcial
            if key_norm in gpkg_norm or gpkg_norm in key_norm:
                return True
            
            # Verificações específicas com variações comuns
            # IMPORTANTE: As variações devem ser específicas para evitar matches errados
            # Ex: 'embargos' fazia match errado com 'embargosicmbio' quando buscava 'embargosibama'
            mappings = {
                'amazonialegal': ['amazonialegal', 'amazonia', 'amazlegal', 'amlegal'],
                'municipios': ['municipios', 'municipio', 'bc250municipio', 'mun'],
                'unidadesfederacao': ['estados', 'uf', 'unidadesfederacao', 'unidadefederacao'],
                'caramazonia': ['car', 'caramazonia', 'cadastroambiental'],
                'florestapublicatipob': ['cnfp', 'florestapublica', 'tipob', 'fpub'],
                'unidadesconservacao': ['uc', 'unidadesconservacao', 'unidadeconservacao'],
                'terrasindigenas': ['ti', 'terrasindigenas', 'terraindigena'],
                'quilombolas': ['quilombo', 'quilombolas', 'quilombola', 'territoriosquilombolas'],
                # Embargos: usar variações específicas que não conflitam entre si
                'embargosibama': ['embargosibama', 'embargoibama', 'ibama'],
                'embargosicmbio': ['embargosicmbio', 'embargoicmbio', 'icmbio'],
                'fitofisionomia': ['fitofisionomia', 'fito', 'vegetacaoibge'],
                'prodes': ['prodes', 'desmat', 'desmatamento'],
                'rvn': ['rvn', 'vegetacaonativa', 'remanescente', 'remvegnativa']
            }
            
            for cfg_key, variations in mappings.items():
                if cfg_key in key_norm:
                    for var in variations:
                        # Verificar match: variação contida no nome OU nome contido na variação
                        if var in gpkg_norm or gpkg_norm in var:
                            return True
        
        return False
    
    def _layer_action(self, layer_key):
        """Ação individual para uma camada (Ver ou Importar)."""
        gpkg_path = self.gpkg_path.text()
        gpkg_layers = self._get_gpkg_layer_names(gpkg_path) if gpkg_path else []
        layer_exists = self._check_layer_exists(layer_key, gpkg_layers)
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        layer_name = layer_info.get('nome', layer_key)
        
        if layer_exists:
            # Carregar camada no mapa
            self._load_layer_from_gpkg(layer_key)
        else:
            # Abrir diálogo para importar
            url = layer_info.get('url_download', '')
            fonte = layer_info.get('fonte', '')
            tipo = layer_info.get('tipo', 'local')
            
            msg = f"A camada '{layer_name}' não existe no GeoPackage.\n\n"
            msg += f"Fonte: {fonte}\n"
            msg += f"Tipo: {tipo}\n\n"
            
            if url:
                msg += f"URL para download:\n{url}\n\n"
                msg += "Deseja abrir o link no navegador?"
                
                reply = QMessageBox.question(self, "Importar Camada", msg,
                    QMessageBox.Yes | QMessageBox.No)
                
                if reply == QMessageBox.Yes:
                    import webbrowser
                    webbrowser.open(url)
            else:
                msg += "Importe a camada manualmente para o GeoPackage."
                QMessageBox.information(self, "Importar Camada", msg)
    
    def _aplicar_estilo_camada(self, layer, layer_key):
        """Aplica estilo padronizado para cada tipo de camada."""
        try:
            # Mapear layer_key para estilo
            # Formatos: (cor_preenchimento, cor_contorno, espessura, hachura)
            # cor = 'R,G,B,A' ou None para transparente
            # hachura = 'nome_hachura' ou None
            
            estilos = {
                # Amazonia Legal: contorno verde sem preenchimento
                'amazonia_legal': {'fill': None, 'outline': '34,139,34,255', 'width': 1.0},
                'amazonia': {'fill': None, 'outline': '34,139,34,255', 'width': 1.0},
                
                # Municípios: contorno preto fino sem preenchimento
                'municipios': {'fill': None, 'outline': '0,0,0,255', 'width': 0.3},
                'municipio': {'fill': None, 'outline': '0,0,0,255', 'width': 0.3},
                
                # Unidade da Federação: contorno cinza mais escuro
                'unidades_federacao': {'fill': None, 'outline': '80,80,80,255', 'width': 0.8},
                'federacao': {'fill': None, 'outline': '80,80,80,255', 'width': 0.8},
                'uf': {'fill': None, 'outline': '80,80,80,255', 'width': 0.8},
                
                # CAR Amazonia legal: verde, transparente, linha fina (igual Municípios)
                'car_amazonia': {'fill': '34,139,34,60', 'outline': '34,139,34,150', 'width': 0.3},
                'car': {'fill': '34,139,34,60', 'outline': '34,139,34,150', 'width': 0.3},
                
                # CNFP: hachura listrada verde
                'cnfp': {'fill': None, 'outline': '0,128,0,255', 'width': 0.5, 'hatch': 'green'},
                'florestas_publicas': {'fill': None, 'outline': '0,128,0,255', 'width': 0.5, 'hatch': 'green'},
                
                # Unidade de conservação: hachura listrada amarela
                'unidades_conservacao': {'fill': None, 'outline': '218,165,32,255', 'width': 0.5, 'hatch': 'yellow'},
                'conservacao': {'fill': None, 'outline': '218,165,32,255', 'width': 0.5, 'hatch': 'yellow'},
                'uc': {'fill': None, 'outline': '218,165,32,255', 'width': 0.5, 'hatch': 'yellow'},
                
                # Terras indígenas: Amarelo
                'terras_indigenas': {'fill': '255,215,0,100', 'outline': '218,165,32,255', 'width': 0.5},
                'indigenas': {'fill': '255,215,0,100', 'outline': '218,165,32,255', 'width': 0.5},
                'ti': {'fill': '255,215,0,100', 'outline': '218,165,32,255', 'width': 0.5},
                
                # Quilombolas: cinza
                'quilombolas': {'fill': '169,169,169,100', 'outline': '128,128,128,255', 'width': 0.5},
                'quilombola': {'fill': '169,169,169,100', 'outline': '128,128,128,255', 'width': 0.5},
                
                # Embargos: rosa
                'embargos_ibama': {'fill': '255,105,180,80', 'outline': '255,20,147,255', 'width': 0.5},
                'embargos_icmbio': {'fill': '255,105,180,80', 'outline': '255,20,147,255', 'width': 0.5},
                'embargos': {'fill': '255,105,180,80', 'outline': '255,20,147,255', 'width': 0.5},
                'embargo': {'fill': '255,105,180,80', 'outline': '255,20,147,255', 'width': 0.5},
                
                # Prodes: vermelho transparente com contorno fino
                'prodes': {'fill': '255,0,0,80', 'outline': '255,0,0,255', 'width': 0.3},
                
                # Vegetação nativa (RVN): verde transparente SEM contorno
                'rvn': {'fill': '34,139,34,120', 'outline': None, 'width': 0},
                'vegetacao_nativa': {'fill': '34,139,34,120', 'outline': None, 'width': 0},
                'vegetacao': {'fill': '34,139,34,120', 'outline': None, 'width': 0},
                'remanescente': {'fill': '34,139,34,120', 'outline': None, 'width': 0},
                
                # Imóveis a analisar: contorno vermelho fino, sem preenchimento
                'imoveis_analisar': {'fill': None, 'outline': '255,0,0,255', 'width': 0.3},
                'imoveis': {'fill': None, 'outline': '255,0,0,255', 'width': 0.3},
                'imovel': {'fill': None, 'outline': '255,0,0,255', 'width': 0.3},
                
                # Fitofisionomias: tons de verde
                'fitofisionomias': {'fill': '144,238,144,100', 'outline': '34,139,34,255', 'width': 0.5},
                'fitofisionomia': {'fill': '144,238,144,100', 'outline': '34,139,34,255', 'width': 0.5},
            }
            
            # Normalizar layer_key para busca
            key_normalized = layer_key.lower().replace(' ', '_').replace('-', '_')
            
            # Buscar estilo correspondente - PRIORIZAR MATCH EXATO
            estilo = None
            matched_key = None
            
            # Primeiro: match exato
            if key_normalized in estilos:
                estilo = estilos[key_normalized]
                matched_key = key_normalized
            else:
                # Segundo: match parcial (mas preferir o mais específico/longo)
                best_match_len = 0
                for key, style in estilos.items():
                    if key in key_normalized or key_normalized in key:
                        # Preferir o match mais longo (mais específico)
                        if len(key) > best_match_len:
                            best_match_len = len(key)
                            estilo = style
                            matched_key = key
            
            if matched_key:
                print(f"[ESTILO] Camada '{layer_key}' -> estilo '{matched_key}': width={estilo.get('width')}")
            
            # Se não encontrou, usar estilo padrão
            if not estilo:
                estilo = {'fill': '200,200,200,50', 'outline': '100,100,100,255', 'width': 0.5}
            
            # Criar símbolo
            fill_color = estilo.get('fill', '0,0,0,0') or '0,0,0,0'
            outline_color = estilo.get('outline', '0,0,0,255')
            outline_width = str(estilo.get('width', 0.5))
            
            # Verificar se tem hachura
            if 'hatch' in estilo:
                hatch_color = estilo.get('hatch', 'green')
                color_map = {
                    'green': '0,128,0,255',
                    'yellow': '218,165,32,255',
                    'red': '255,0,0,255'
                }
                hatch_line_color = color_map.get(hatch_color, '0,0,0,255')
                
                # Criar símbolo com hachura
                symbol = QgsFillSymbol.createSimple({
                    'color': '0,0,0,0',
                    'outline_color': outline_color,
                    'outline_width': outline_width
                })
                
                # Adicionar camada de hachura
                line_pattern = QgsLinePatternFillSymbolLayer()
                line_pattern.setLineAngle(45)
                line_pattern.setDistance(3)
                line_pattern.setLineWidth(0.3)
                line_pattern.setColor(QColor(*[int(c) for c in hatch_line_color.split(',')]))
                
                symbol.appendSymbolLayer(line_pattern)
            else:
                # Símbolo simples
                symbol = QgsFillSymbol.createSimple({
                    'color': fill_color,
                    'outline_color': outline_color,
                    'outline_width': outline_width
                })
            
            layer.renderer().setSymbol(symbol)
            layer.triggerRepaint()
            
        except Exception as e:
            print(f"Erro ao aplicar estilo para {layer_key}: {e}")
    
    def _load_layer_from_gpkg(self, layer_key):
        """Carrega uma camada do GeoPackage no mapa."""
        gpkg_path = self.gpkg_path.text()
        if not gpkg_path or not os.path.exists(gpkg_path):
            QMessageBox.warning(self, "Aviso", "Selecione um GeoPackage primeiro")
            return
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        if 'Remanescente' in display_name:
            display_name = "Vegetacao Nativa"
        
        try:
            import sqlite3
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()
            
            # Buscar nome real da camada (com case correto)
            cursor.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
            all_layers = cursor.fetchall()
            conn.close()
            
            # Encontrar a camada correspondente
            real_layer_name = None
            for (table_name,) in all_layers:
                if self._check_layer_exists(layer_key, [table_name]):
                    real_layer_name = table_name
                    break
            
            if real_layer_name:
                uri = f"{gpkg_path}|layername={real_layer_name}"
                layer = QgsVectorLayer(uri, display_name, "ogr")
                
                if layer.isValid():
                    # Aplicar estilo padronizado
                    self._aplicar_estilo_camada(layer, layer_key)
                    
                    # Adicionar ao projeto QGIS
                    QgsProject.instance().addMapLayer(layer, False)
                    
                    # Adicionar ao canvas
                    current_layers = list(self.map_canvas.layers())
                    current_layers.insert(0, layer)
                    self.map_canvas.setLayers(current_layers)
                    self.map_canvas.refresh()
                    
                    self._update_status(f"Camada '{display_name}' carregada")
                    return
            
            QMessageBox.warning(self, "Erro", f"Camada '{display_name}' nao encontrada no GeoPackage")
            
        except Exception as e:
            QMessageBox.warning(self, "Erro", f"Erro ao carregar camada: {str(e)}")
    
    def _toggle_layer_visibility(self, layer_key, checked, btn):
        """Liga/desliga a visibilidade de uma camada no mapa."""
        gpkg_path = self.gpkg_path.text()
        if not gpkg_path or not os.path.exists(gpkg_path):
            if btn:
                btn.setChecked(False)
            return
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        if 'Remanescente' in display_name:
            display_name = "Vegetacao Nativa"
        
        # Atualizar estado
        self.layer_visibility[layer_key] = checked
        
        # Atualizar estilo do botão toggle (se existir)
        if btn:
            if checked:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #27ae60;
                        border: 1px solid #1e8449;
                        border-radius: 3px;
                        color: white;
                        font-size: 10px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: #2ecc71;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #95a5a6;
                        border: 1px solid #7f8c8d;
                        border-radius: 3px;
                        color: #bdc3c7;
                        font-size: 10px;
                        padding: 0px;
                    }
                    QPushButton:hover {
                        background-color: #7f8c8d;
                    }
                """)
        
        if checked:
            # Carregar camada no mapa
            self._load_layer_from_gpkg(layer_key)
        else:
            # Remover camada do mapa (buscar por nome parcial também)
            layers_to_remove_ids = []
            for layer in QgsProject.instance().mapLayers().values():
                if layer.name() == display_name or display_name in layer.name():
                    layers_to_remove_ids.append(layer.id())
            
            # Remover do projeto
            for layer_id in layers_to_remove_ids:
                QgsProject.instance().removeMapLayer(layer_id)
            
            # Atualizar canvas com camadas restantes
            remaining_layers = [l for l in self.map_canvas.layers() if l.id() not in layers_to_remove_ids]
            self.map_canvas.setLayers(remaining_layers)
            self.map_canvas.refresh()
            self._update_status(f"Camada '{display_name}' removida do mapa")
    
    def _update_layer_from_source(self, layer_key):
        """Atualiza uma camada baixando da fonte (WFS, ZIP, CAR ou download)."""
        gpkg_path = self.gpkg_path.text()
        if not gpkg_path:
            QMessageBox.warning(self, "Aviso", "Selecione um GeoPackage primeiro")
            return
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        layer_name = layer_info.get('nome', layer_key)
        layer_type = layer_info.get('tipo', 'local')
        url_wfs = layer_info.get('url_wfs', '')
        wfs_layer_name = layer_info.get('layer_name', '')
        url_download = layer_info.get('url_download', '')
        
        # Tipo especial: CAR (download de múltiplos estados)
        if layer_type == 'car_wfs':
            self._download_car_amazonia(layer_key, gpkg_path)
        # Tipo especial: PRODES (WFS com paginação)
        elif layer_type == 'prodes_wfs':
            self._download_prodes_layer(layer_key, gpkg_path)
        # Tipo especial: Quilombolas (download de múltiplos estados via WFS)
        elif layer_type == 'quilombolas_wfs':
            self._download_quilombolas(layer_key, gpkg_path)
        # Tipo download direto (ZIP, SHAPE-ZIP, /data)
        elif layer_type == 'download' or (url_download and (
            url_download.lower().endswith('.zip') or
            url_download.lower().endswith('/data') or
            'SHAPE-ZIP' in url_download or
            'outputFormat=SHAPE' in url_download
        )):
            self._download_from_zip(layer_key, url_download, gpkg_path)
        # WFS padrão
        elif url_wfs and wfs_layer_name:
            self._download_from_wfs(layer_key, url_wfs, wfs_layer_name, gpkg_path)
        elif url_download:
            # Abrir link para download manual (último recurso)
            msg = f"A camada '{layer_name}' requer download manual.\n\n"
            msg += f"URL: {url_download}\n\n"
            msg += "Após baixar, importe para o GeoPackage.\n"
            msg += "Deseja abrir o link no navegador?"
            
            reply = QMessageBox.question(self, "Download Manual", msg,
                QMessageBox.Yes | QMessageBox.No)
            
            if reply == QMessageBox.Yes:
                import webbrowser
                webbrowser.open(url_download)
        else:
            QMessageBox.information(self, "Info", f"Camada '{layer_name}' não possui fonte externa configurada.")
    
    def _download_from_wfs(self, layer_key, url_wfs, wfs_layer_name, gpkg_path):
        """Baixa camada de um serviço WFS e salva no GeoPackage."""
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        fallback_layer = layer_info.get('layer_name_fallback', '')
        
        # Mostrar barra de progresso
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._update_status(f"Conectando ao WFS: {display_name}...")
        QApplication.processEvents()
        
        try:
            # Construir URI do WFS (formato que funciona no QGIS)
            wfs_uri = (
                f"pagingEnabled='default' "
                f"preferCoordinatesForWfsT11='false' "
                f"restrictToRequestBBOX='1' "
                f"srsname='EPSG:4674' "
                f"typename='{wfs_layer_name}' "
                f"url='{url_wfs}' "
                f"version='auto'"
            )
            
            self.progress_bar.setValue(20)
            self._update_status(f"Baixando {display_name} do WFS...")
            QApplication.processEvents()
            
            # Criar camada WFS
            wfs_layer = QgsVectorLayer(wfs_uri, display_name, "WFS")
            
            # Se falhar e houver fallback, tentar o fallback
            if not wfs_layer.isValid() and fallback_layer:
                self._update_status(f"Tentando fonte alternativa para {display_name}...")
                QApplication.processEvents()
                
                wfs_uri_fallback = (
                    f"pagingEnabled='default' "
                    f"preferCoordinatesForWfsT11='false' "
                    f"restrictToRequestBBOX='1' "
                    f"srsname='EPSG:4674' "
                    f"typename='{fallback_layer}' "
                    f"url='{url_wfs}' "
                    f"version='auto'"
                )
                wfs_layer = QgsVectorLayer(wfs_uri_fallback, display_name, "WFS")
                wfs_uri = wfs_uri_fallback  # Para mensagem de erro
            
            if not wfs_layer.isValid():
                raise Exception(f"Não foi possível conectar ao WFS.\nURI: {wfs_uri}")
            
            self.progress_bar.setValue(50)
            self._update_status(f"Salvando {display_name} no GeoPackage...")
            QApplication.processEvents()
            
            # Nome da tabela no GeoPackage (padronizado)
            table_name = self._get_table_name(layer_key)
            
            # Salvar no GeoPackage
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = table_name
            options.fileEncoding = "UTF-8"
            
            # Verificar se arquivo existe para determinar ação
            if not os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                wfs_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] != QgsVectorFileWriter.NoError:
                raise Exception(f"Erro ao salvar no GeoPackage: {error[1]}")
            
            self.progress_bar.setValue(90)
            
            # Atualizar tabela
            self._populate_layers_from_config()
            
            self.progress_bar.setValue(100)
            self._update_status(f"Camada '{display_name}' atualizada com sucesso!")
            
            QMessageBox.information(self, "Sucesso", 
                f"Camada '{display_name}' baixada e salva no GeoPackage!\n\n"
                f"Features: {wfs_layer.featureCount()}")
            
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao baixar WFS:\n{str(e)}")
            self._update_status(f"Erro ao baixar {display_name}")
        
        finally:
            self.progress_bar.setVisible(False)
    
    def _download_prodes_layer(self, layer_key, gpkg_path):
        """Baixa camada PRODES via WFS e salva no GeoPackage (versão com UI)."""
        # Confirmar download (demora bastante)
        reply = QMessageBox.question(
            self, "Download PRODES",
            "O download do PRODES pode demorar bastante (~25-40 minutos).\n\n"
            "Serão baixados ~3 milhões de polígonos de desmatamento\n"
            "(incrementos anuais de 2008 até o presente).\n\n"
            "Deseja continuar?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # Mostrar barra de progresso
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._update_status("PRODES: preparando download...")
        QApplication.processEvents()
        
        try:
            success = self._download_prodes_silent(layer_key, gpkg_path)
            
            if success:
                QMessageBox.information(self, "Sucesso", "PRODES baixado com sucesso!")
                self._populate_layers_from_config()
            else:
                QMessageBox.warning(self, "Erro", "Falha ao baixar PRODES. Verifique a conexão.")
        except Exception as e:
            QMessageBox.warning(self, "Erro", f"Erro ao baixar PRODES:\n{str(e)}")
        finally:
            self.progress_bar.setVisible(False)
            self._update_status("Pronto")
    
    def _download_from_zip(self, layer_key, url_zip, gpkg_path):
        """Baixa arquivo ZIP, extrai shapefile e salva no GeoPackage."""
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        
        # Mostrar barra de progresso
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._update_status(f"Baixando {display_name}...")
        QApplication.processEvents()
        
        try:
            # Etapa 1: Download do ZIP
            self.progress_bar.setValue(10)
            self._update_status(f"Conectando a {url_zip[:50]}...")
            QApplication.processEvents()
            
            zip_path = self._download_zip_file(url_zip)
            if not zip_path:
                raise Exception("Falha no download do arquivo ZIP")
            
            # Etapa 2: Extração
            self.progress_bar.setValue(40)
            self._update_status(f"Extraindo arquivos...")
            QApplication.processEvents()
            
            extracted_files = self._extract_zip_file(zip_path)
            if not extracted_files:
                raise Exception("Falha na extração do ZIP")
            
            # Etapa 3: Encontrar shapefile
            self.progress_bar.setValue(60)
            self._update_status(f"Procurando shapefile...")
            QApplication.processEvents()
            
            shapefile_path = self._find_shapefile(extracted_files)
            if not shapefile_path:
                raise Exception("Nenhum arquivo .shp encontrado no ZIP")
            
            # Etapa 4: Carregar shapefile (com retry para resolver problemas de timing)
            self.progress_bar.setValue(70)
            self._update_status(f"Carregando shapefile...")
            QApplication.processEvents()
            
            layer = self._load_shapefile_with_retry(shapefile_path, display_name)
            if not layer or not layer.isValid():
                raise Exception(f"Shapefile inválido após 3 tentativas: {shapefile_path}")
            
            # Etapa 5: Salvar no GeoPackage
            self.progress_bar.setValue(80)
            self._update_status(f"Salvando {display_name} no GeoPackage...")
            QApplication.processEvents()
            
            # Nome da tabela no GeoPackage (padronizado)
            table_name = self._get_table_name(layer_key)
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = table_name
            options.fileEncoding = "UTF-8"
            
            if not os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] != QgsVectorFileWriter.NoError:
                raise Exception(f"Erro ao salvar no GeoPackage: {error[1]}")
            
            # Etapa 6: Limpar arquivos temporários
            self.progress_bar.setValue(90)
            self._cleanup_temp_files(zip_path, extracted_files)
            
            # Atualizar tabela
            self._populate_layers_from_config()
            
            self.progress_bar.setValue(100)
            self._update_status(f"Camada '{display_name}' atualizada com sucesso!")
            
            QMessageBox.information(self, "Sucesso", 
                f"Camada '{display_name}' baixada e salva no GeoPackage!\n\n"
                f"Features: {layer.featureCount()}")
            
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao baixar ZIP:\n{str(e)}")
            self._update_status(f"Erro ao baixar {display_name}")
        
        finally:
            self.progress_bar.setVisible(False)
    
    def _download_from_zip_batch(self, layer_key, url_zip, gpkg_path):
        """
        Versão batch do download ZIP - mesma lógica do botão individual.
        Retorna True se sucesso, False se falha.
        """
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        
        zip_path = None
        extracted_files = None
        
        try:
            # Etapa 1: Download do ZIP
            self._update_status(f"  {display_name}: baixando ZIP...")
            QApplication.processEvents()
            
            zip_path = self._download_zip_file(url_zip)
            if not zip_path:
                self._update_status(f"  {display_name}: falha no download")
                return False
            
            # Etapa 2: Extração
            self._update_status(f"  {display_name}: extraindo...")
            QApplication.processEvents()
            
            extracted_files = self._extract_zip_file(zip_path)
            if not extracted_files:
                self._update_status(f"  {display_name}: falha na extração")
                return False
            
            # Etapa 3: Encontrar shapefile
            self._update_status(f"  {display_name}: procurando shapefile...")
            QApplication.processEvents()
            
            shapefile_path = self._find_shapefile(extracted_files)
            if not shapefile_path:
                self._update_status(f"  {display_name}: shapefile não encontrado")
                return False
            
            # Etapa 4: Carregar shapefile (com retry para resolver problemas de timing)
            self._update_status(f"  {display_name}: carregando shapefile...")
            QApplication.processEvents()
            
            layer = self._load_shapefile_with_retry(shapefile_path, display_name)
            if not layer or not layer.isValid():
                self._update_status(f"  {display_name}: shapefile inválido após tentativas")
                return False
            
            # Etapa 5: Salvar no GeoPackage
            self._update_status(f"  {display_name}: salvando {layer.featureCount()} features...")
            QApplication.processEvents()
            
            table_name = self._get_table_name(layer_key)
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = table_name
            options.fileEncoding = "UTF-8"
            
            if not os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] != QgsVectorFileWriter.NoError:
                self._update_status(f"  {display_name}: erro ao salvar - {error[1][:30]}")
                return False
            
            self._update_status(f"  {display_name}: OK ({layer.featureCount()} features)")
            QApplication.processEvents()
            return True
            
        except Exception as e:
            self._update_status(f"  {display_name}: erro - {str(e)[:40]}")
            return False
            
        finally:
            # Limpar arquivos temporários
            if zip_path or extracted_files:
                try:
                    self._cleanup_temp_files(zip_path, extracted_files)
                except:
                    pass
    
    def _download_zip_file(self, url):
        """Baixa arquivo ZIP de uma URL usando urllib com suporte a redirecionamentos."""
        import urllib.request
        import ssl
        
        try:
            self._update_status(f"  Conectando: {url[:50]}...")
            QApplication.processEvents()
            
            # SSL context permissivo
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Criar request com User-Agent de navegador completo
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            req.add_header('Accept', 'application/zip, application/octet-stream, */*')
            req.add_header('Accept-Language', 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7')
            
            # Download com chunks para manter interface responsiva
            self._update_status(f"  Baixando arquivo...")
            QApplication.processEvents()
            
            response = urllib.request.urlopen(req, timeout=180, context=ssl_context)
            
            # Verificar Content-Type
            content_type = response.headers.get('Content-Type', '').lower()
            self._update_status(f"  Tipo de conteúdo: {content_type[:30]}...")
            QApplication.processEvents()
            
            # Ler em chunks
            chunks = []
            total_size = 0
            while True:
                chunk = response.read(65536)  # 64KB por vez
                if not chunk:
                    break
                chunks.append(chunk)
                total_size += len(chunk)
                if total_size % 500000 < 65536:  # Atualiza a cada ~500KB
                    self._update_status(f"  Baixando: {total_size/1024/1024:.1f} MB...")
                    QApplication.processEvents()
            
            response.close()
            data = b''.join(chunks)
            
            # Verifica se arquivo é válido
            if len(data) < 1000:
                raise Exception(f"Arquivo muito pequeno ({len(data)} bytes)")
            
            # Verifica se é realmente um ZIP (magic bytes: PK)
            if not data[:2] == b'PK':
                # Pode ser HTML de erro
                content_preview = data[:500].decode('utf-8', errors='ignore')
                if '<html' in content_preview.lower() or '<!doctype' in content_preview.lower():
                    raise Exception(f"URL retornou HTML em vez de ZIP. O servidor pode ter bloqueado o download.")
                raise Exception(f"Arquivo não parece ser um ZIP válido")
            
            # Salva em arquivo temporário
            temp_dir = tempfile.gettempdir()
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            zip_filename = f"florestamais_{timestamp}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)
            
            with open(zip_path, 'wb') as f:
                f.write(data)
            
            self._update_status(f"  Download concluído: {len(data)/1024/1024:.1f} MB")
            QApplication.processEvents()
            
            return zip_path
            
        except urllib.error.HTTPError as e:
            raise Exception(f"Erro HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise Exception(f"Erro de conexão: {str(e.reason)[:40]}")
        except Exception as e:
            raise Exception(f"Erro ao baixar: {str(e)[:50]}")
    
    def _extract_zip_file(self, zip_path):
        """Extrai arquivo ZIP e retorna lista de arquivos."""
        try:
            # Cria diretório temporário ÚNICO para cada extração (evita conflitos)
            import uuid
            unique_id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid.uuid4().hex[:8]}"
            extract_dir = os.path.join(tempfile.gettempdir(), f"florestamais_extract_{unique_id}")
            os.makedirs(extract_dir, exist_ok=True)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
                
                # Lista arquivos extraídos (caminhos completos)
                extracted_files = []
                for root, dirs, files in os.walk(extract_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        extracted_files.append(file_path)
                
                return extracted_files
                
        except Exception as e:
            raise Exception(f"Erro na extração: {str(e)}")
    
    def _find_shapefile(self, extracted_files):
        """Encontra o arquivo .shp principal nos arquivos extraídos."""
        try:
            # Filtra apenas arquivos .shp
            shapefiles = [f for f in extracted_files if f.lower().endswith('.shp')]
            
            if not shapefiles:
                return None
            
            # Se há apenas um, usa ele
            if len(shapefiles) == 1:
                return shapefiles[0]
            
            # Se há múltiplos, usa o maior (geralmente o principal)
            largest = max(shapefiles, key=lambda x: os.path.getsize(x))
            return largest
            
        except Exception as e:
            raise Exception(f"Erro ao procurar shapefile: {str(e)}")
    
    def _load_shapefile_with_retry(self, shapefile_path, layer_name, max_retries=3):
        """
        Carrega shapefile com retry - resolve problemas de timing após extração.
        """
        import time
        
        for attempt in range(max_retries):
            # Pequeno delay antes de tentar (arquivo pode ainda estar sendo escrito)
            if attempt > 0:
                self._update_status(f"  Tentativa {attempt + 1}/{max_retries}...")
                QApplication.processEvents()
                time.sleep(1)  # Espera 1 segundo entre tentativas
            
            # Verificar se arquivos auxiliares existem (.dbf, .shx)
            base_path = shapefile_path[:-4]  # Remove .shp
            dbf_exists = os.path.exists(base_path + '.dbf') or os.path.exists(base_path + '.DBF')
            shx_exists = os.path.exists(base_path + '.shx') or os.path.exists(base_path + '.SHX')
            
            if not (dbf_exists and shx_exists):
                self._update_status(f"  Aguardando arquivos auxiliares...")
                QApplication.processEvents()
                time.sleep(0.5)
                continue
            
            # Tentar carregar
            layer = QgsVectorLayer(shapefile_path, layer_name, "ogr")
            
            if layer.isValid() and layer.featureCount() > 0:
                return layer
            
            # Se não é válido mas é a última tentativa, tenta forçar reload
            if attempt == max_retries - 1:
                # Força garbage collection e tenta novamente
                import gc
                gc.collect()
                QApplication.processEvents()
                time.sleep(0.5)
                layer = QgsVectorLayer(shapefile_path, layer_name, "ogr")
                if layer.isValid():
                    return layer
        
        return None
    
    def _cleanup_temp_files(self, zip_path, extracted_files):
        """Remove arquivos temporários."""
        try:
            # Remove ZIP
            if os.path.exists(zip_path):
                os.remove(zip_path)
            
            # Remove pasta de extração
            if extracted_files:
                extract_dir = os.path.dirname(extracted_files[0])
                # Encontrar diretório raiz da extração
                while extract_dir and 'florestamais_extract' not in os.path.basename(extract_dir):
                    parent = os.path.dirname(extract_dir)
                    if parent == extract_dir:
                        break
                    extract_dir = parent
                
                if extract_dir and 'florestamais_extract' in extract_dir:
                    import shutil
                    shutil.rmtree(extract_dir, ignore_errors=True)
        except:
            pass  # Ignora erros na limpeza
    
    def _download_quilombolas(self, layer_key, gpkg_path):
        """
        Baixa dados de Territórios Quilombolas de todos os estados via WFS do INCRA.
        Cada estado tem seu próprio endpoint, então baixamos todos e juntamos.
        """
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        url_base = layer_info.get('url_wfs_base', 'http://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=quilombolas_')
        estados = layer_info.get('estados', ['ac', 'am', 'ap', 'ma', 'mt', 'pa', 'pi', 'ro', 'rr', 'to'])
        
        # Confirmar download
        msg = (
            f"Download de Territórios Quilombolas de {len(estados)} estados:\n"
            f"{', '.join([e.upper() for e in estados])}\n\n"
            "O download será feito estado por estado e depois unificado.\n\n"
            "Deseja continuar?"
        )
        reply = QMessageBox.question(self, "Download Quilombolas", msg,
            QMessageBox.Yes | QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Mostrar barra de progresso
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        todas_features = []
        estados_sucesso = []
        estados_erro = []
        crs_referencia = None
        fields_referencia = None
        
        total_estados = len(estados)
        
        for i, estado in enumerate(estados):
            progress = int((i / total_estados) * 100)
            self.progress_bar.setValue(progress)
            self._update_status(f"Baixando quilombolas de {estado.upper()}... ({i+1}/{total_estados})")
            QApplication.processEvents()
            
            try:
                # Construir URL do WFS para o estado
                url_estado = f"{url_base}{estado}"
                
                # URI para QGIS WFS - typename precisa do prefixo 'ms:'
                wfs_uri = (
                    f"pagingEnabled='default' "
                    f"preferCoordinatesForWfsT11='false' "
                    f"restrictToRequestBBOX='1' "
                    f"srsname='EPSG:4326' "
                    f"typename='ms:quilombolas_{estado}' "
                    f"url='{url_estado}' "
                    f"version='auto'"
                )
                
                # Criar camada WFS
                wfs_layer = QgsVectorLayer(wfs_uri, f"quilombolas_{estado}", "WFS")
                
                if wfs_layer.isValid() and wfs_layer.featureCount() > 0:
                    # Guardar CRS e campos da primeira camada válida
                    if crs_referencia is None:
                        crs_referencia = wfs_layer.crs()
                        fields_referencia = wfs_layer.fields()
                    
                    # Coletar features
                    for feat in wfs_layer.getFeatures():
                        todas_features.append(feat)
                    
                    estados_sucesso.append(estado.upper())
                    print(f"[Quilombolas] {estado.upper()}: {wfs_layer.featureCount()} feições")
                else:
                    # Tentar abordagem alternativa via request direto
                    try:
                        import urllib.request
                        import json
                        
                        # Tentar GetFeature em GeoJSON
                        url_geojson = f"{url_estado}&service=WFS&version=1.0.0&request=GetFeature&outputFormat=application/json"
                        
                        req = urllib.request.Request(url_geojson, headers={'User-Agent': 'QGIS-FlorestaMais'})
                        with urllib.request.urlopen(req, timeout=60) as response:
                            data = json.loads(response.read().decode('utf-8'))
                            
                            if 'features' in data and len(data['features']) > 0:
                                # Criar camada temporária do GeoJSON
                                temp_layer = QgsVectorLayer(json.dumps(data), f"quilombolas_{estado}", "ogr")
                                
                                if temp_layer.isValid():
                                    if crs_referencia is None:
                                        crs_referencia = temp_layer.crs()
                                        fields_referencia = temp_layer.fields()
                                    
                                    for feat in temp_layer.getFeatures():
                                        todas_features.append(feat)
                                    
                                    estados_sucesso.append(estado.upper())
                                    print(f"[Quilombolas] {estado.upper()} (GeoJSON): {temp_layer.featureCount()} feições")
                    except Exception as e2:
                        print(f"[Quilombolas] {estado.upper()} - Erro alternativo: {str(e2)}")
                        estados_erro.append(estado.upper())
                        
            except Exception as e:
                print(f"[Quilombolas] Erro ao baixar {estado.upper()}: {str(e)}")
                estados_erro.append(estado.upper())
        
        # Verificar se conseguiu dados
        if not todas_features:
            self.progress_bar.setVisible(False)
            QMessageBox.warning(
                self,
                "Erro no Download",
                f"Não foi possível baixar dados de quilombolas de nenhum estado.\n\n"
                f"Estados com erro: {', '.join(estados_erro) if estados_erro else 'Todos'}\n\n"
                "Verifique sua conexão com a internet ou tente novamente mais tarde."
            )
            return
        
        self._update_status(f"Unificando {len(todas_features)} feições de quilombolas...")
        self.progress_bar.setValue(90)
        QApplication.processEvents()
        
        try:
            # Criar camada unificada em memória
            if crs_referencia is None:
                crs_referencia = QgsCoordinateReferenceSystem("EPSG:4674")
            
            # Determinar tipo de geometria (geralmente MultiPolygon)
            geom_type = "MultiPolygon"
            
            unified_layer = QgsVectorLayer(
                f"{geom_type}?crs={crs_referencia.authid()}",
                "quilombolas_unified",
                "memory"
            )
            
            provider = unified_layer.dataProvider()
            
            # Adicionar campos
            if fields_referencia:
                provider.addAttributes(fields_referencia)
                unified_layer.updateFields()
            
            # Adicionar todas as features
            provider.addFeatures(todas_features)
            
            # Salvar no GeoPackage
            self._update_status(f"Salvando quilombolas no GeoPackage...")
            self.progress_bar.setValue(95)
            QApplication.processEvents()
            
            output_name = layer_key  # 'quilombolas'
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = output_name
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                unified_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            self.progress_bar.setValue(100)
            
            if error[0] == QgsVectorFileWriter.NoError:
                self._update_status(f"✓ Quilombolas baixados: {len(todas_features)} feições")
                self._populate_layers_from_config()
                
                msg = (
                    f"Download concluído!\n\n"
                    f"Total de feições: {len(todas_features)}\n"
                    f"Estados com dados: {', '.join(estados_sucesso)}\n"
                )
                if estados_erro:
                    msg += f"\nEstados sem dados: {', '.join(estados_erro)}"
                
                QMessageBox.information(self, "Download Concluído", msg)
            else:
                raise Exception(f"Erro ao salvar: {error[1]}")
                
        except Exception as e:
            QMessageBox.critical(
                self,
                "Erro ao Salvar",
                f"Erro ao salvar quilombolas no GeoPackage:\n{str(e)}"
            )
        finally:
            self.progress_bar.setVisible(False)
    
    def _download_car_amazonia(self, layer_key, gpkg_path):
        """
        Baixa dados do CAR para todos os estados da Amazônia Legal.
        Usa paginação para lidar com grandes volumes de dados.
        """
        import ssl
        import urllib.request
        import urllib.parse
        import shutil
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        display_name = layer_info.get('nome', layer_key)
        url_base = layer_info.get('url_wfs', 'https://geoserver.car.gov.br/geoserver/sicar/wfs')
        estados = layer_info.get('estados_amzl', ['AC', 'AM', 'AP', 'MA', 'MT', 'PA', 'RO', 'RR', 'TO'])
        
        # Confirmar download (pode demorar muito!)
        msg = (
            f"Download do CAR para {len(estados)} estados da Amazônia Legal:\n"
            f"{', '.join(estados)}\n\n"
            "⚠️ ATENÇÃO: Este processo pode demorar várias horas!\n"
            "Cada estado pode ter milhões de registros.\n\n"
            "Deseja continuar?"
        )
        reply = QMessageBox.question(self, "Download CAR", msg,
            QMessageBox.Yes | QMessageBox.No)
        
        if reply != QMessageBox.Yes:
            return
        
        # Criar pasta temporária
        pasta_temp = os.path.join(tempfile.gettempdir(), f"car_download_{id(self)}")
        os.makedirs(pasta_temp, exist_ok=True)
        
        # Criar contexto SSL compatível com servidor CAR
        ssl_context = self._create_ssl_context_car()
        
        # Mostrar barra de progresso
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        todas_features = []
        estados_sucesso = []
        estados_erro = []
        
        try:
            for idx, estado in enumerate(estados):
                progresso_base = int((idx / len(estados)) * 100)
                self.progress_bar.setValue(progresso_base)
                self._update_status(f"Baixando CAR de {estado} ({idx+1}/{len(estados)})...")
                QApplication.processEvents()
                
                try:
                    # Nome da camada WFS do estado
                    nome_camada = f"sicar:sicar_imoveis_{estado.lower()}"
                    
                    # Baixar estado em lotes
                    features_estado = self._download_car_estado(
                        url_base, nome_camada, estado, pasta_temp, ssl_context
                    )
                    
                    if features_estado:
                        todas_features.extend(features_estado)
                        estados_sucesso.append(estado)
                        self._update_status(f"✅ {estado}: {len(features_estado)} registros")
                    else:
                        estados_erro.append(estado)
                        self._update_status(f"⚠️ {estado}: sem dados ou erro")
                    
                    QApplication.processEvents()
                    
                except Exception as e:
                    estados_erro.append(estado)
                    self._update_status(f"❌ Erro em {estado}: {str(e)}")
                    continue
            
            # Consolidar em uma única camada
            if todas_features:
                self.progress_bar.setValue(90)
                self._update_status(f"Consolidando {len(todas_features)} arquivos...")
                QApplication.processEvents()
                
                # Mesclar todos os shapefiles em um
                self._consolidar_car(todas_features, gpkg_path, layer_key)
                
                # Atualizar tabela
                self._populate_layers_from_config()
                
                self.progress_bar.setValue(100)
                
                msg_sucesso = (
                    f"Download do CAR concluído!\n\n"
                    f"Estados com sucesso: {', '.join(estados_sucesso)}\n"
                    f"Total de arquivos: {len(todas_features)}\n"
                )
                if estados_erro:
                    msg_sucesso += f"\nEstados com erro: {', '.join(estados_erro)}"
                
                QMessageBox.information(self, "Sucesso", msg_sucesso)
            else:
                QMessageBox.warning(self, "Aviso", "Nenhum dado foi baixado com sucesso.")
            
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro no download do CAR:\n{str(e)}")
        
        finally:
            self.progress_bar.setVisible(False)
            # Limpar pasta temporária
            try:
                shutil.rmtree(pasta_temp, ignore_errors=True)
            except:
                pass
    
    def _download_car_estado(self, url_base, nome_camada, estado, pasta_temp, ssl_context):
        """
        Baixa dados do CAR de um estado específico com paginação.
        Retorna lista de caminhos dos arquivos baixados.
        """
        import urllib.request
        import urllib.parse
        import urllib.error
        import time
        
        arquivos_baixados = []
        limite_por_lote = 10000  # 10k registros por lote (padrão do servidor)
        ultimo_id = None
        lote_numero = 1
        max_lotes = 500  # Limite de segurança
        lotes_vazios_consecutivos = 0
        max_lotes_vazios = 3
        
        self._update_status(f"  {estado}: verificando disponibilidade...")
        QApplication.processEvents()
        
        # Primeiro, verificar se há dados (teste com 1 registro)
        max_tentativas_teste = 3
        teste_ok = False
        
        for tentativa in range(max_tentativas_teste):
            try:
                test_params = {
                    'service': 'WFS',
                    'version': '1.0.0',
                    'request': 'GetFeature',
                    'typeName': nome_camada,
                    'outputFormat': 'application/json',
                    'maxFeatures': 1
                }
                test_url = f"{url_base}?{urllib.parse.urlencode(test_params)}"
                req_test = urllib.request.Request(test_url)
                req_test.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                req_test.add_header('Accept', 'application/json, */*')
                
                with urllib.request.urlopen(req_test, timeout=90, context=ssl_context) as resp:
                    test_data = json.loads(resp.read().decode('utf-8'))
                    if not test_data.get('features'):
                        self._update_status(f"  {estado}: nenhum dado disponível")
                        QApplication.processEvents()
                        return []
                
                self._update_status(f"  {estado}: dados disponíveis, iniciando download...")
                QApplication.processEvents()
                teste_ok = True
                break
                
            except urllib.error.URLError as e:
                erro_str = str(e)
                if 'SSL' in erro_str or 'handshake' in erro_str.lower():
                    self._update_status(f"  {estado}: erro SSL (tentativa {tentativa+1}/{max_tentativas_teste})...")
                    QApplication.processEvents()
                    time.sleep(2)
                    continue
                else:
                    self._update_status(f"  {estado}: erro de conexão - {erro_str[:30]}")
                    QApplication.processEvents()
                    return []  # Retornar vazio em vez de exceção
                    
            except Exception as e:
                self._update_status(f"  {estado}: erro - {str(e)[:30]} (tentativa {tentativa+1})")
                QApplication.processEvents()
                time.sleep(2)
                continue
        
        if not teste_ok:
            self._update_status(f"  {estado}: FALHA após {max_tentativas_teste} tentativas")
            QApplication.processEvents()
            return []  # Retornar lista vazia para continuar com outros estados
        
        while lote_numero <= max_lotes:
            try:
                # Montar filtro CQL para paginação por ID
                filtro_cql = None
                if ultimo_id:
                    filtro_cql = f"cod_imovel > '{ultimo_id}'"
                
                # Parâmetros da requisição WFS
                params = {
                    'service': 'WFS',
                    'version': '1.0.0',
                    'request': 'GetFeature',
                    'typeName': nome_camada,
                    'outputFormat': 'application/json',
                    'maxFeatures': limite_por_lote,
                    'sortBy': 'cod_imovel'  # Ordenar por ID para paginação consistente
                }
                
                if filtro_cql:
                    params['CQL_FILTER'] = filtro_cql
                
                url = f"{url_base}?{urllib.parse.urlencode(params)}"
                
                # Fazer requisição com User-Agent de navegador (importante!)
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                req.add_header('Accept', 'application/json, */*')
                
                self._update_status(f"  {estado}: lote {lote_numero} (ID > {ultimo_id or 'início'})...")
                QApplication.processEvents()
                
                # Timeout maior para lotes grandes
                with urllib.request.urlopen(req, timeout=300, context=ssl_context) as response:
                    json_data = response.read()
                
                # Salvar JSON temporário
                json_path = os.path.join(pasta_temp, f"{estado}_lote_{lote_numero:04d}.json")
                with open(json_path, 'wb') as f:
                    f.write(json_data)
                
                # Analisar JSON
                data = json.loads(json_data.decode('utf-8'))
                features = data.get('features', [])
                
                if not features:
                    lotes_vazios_consecutivos += 1
                    self._update_status(f"  {estado}: lote vazio ({lotes_vazios_consecutivos}/{max_lotes_vazios})")
                    QApplication.processEvents()
                    try:
                        os.remove(json_path)
                    except:
                        pass
                    
                    if lotes_vazios_consecutivos >= max_lotes_vazios:
                        self._update_status(f"  {estado}: fim dos dados")
                        break
                    else:
                        lote_numero += 1
                        time.sleep(1)
                        continue
                
                # Reset contador de lotes vazios
                lotes_vazios_consecutivos = 0
                
                num_features = len(features)
                self._update_status(f"  {estado}: {num_features} registros no lote {lote_numero}")
                QApplication.processEvents()
                
                # Extrair último ID para próxima requisição
                ultimo_feature = features[-1]
                properties = ultimo_feature.get('properties', {})
                novo_ultimo_id = properties.get('cod_imovel')
                
                # Debug: mostrar o ID extraído
                if lote_numero <= 3 or lote_numero % 10 == 0:
                    self._update_status(f"  {estado}: lote {lote_numero} - último ID: {novo_ultimo_id}")
                    QApplication.processEvents()
                
                if novo_ultimo_id == ultimo_id:
                    self._update_status(f"  {estado}: ID repetido ({novo_ultimo_id}) - fim dos dados")
                    QApplication.processEvents()
                    try:
                        os.remove(json_path)
                    except:
                        pass
                    break
                
                if not novo_ultimo_id:
                    self._update_status(f"  {estado}: AVISO - não encontrou cod_imovel no JSON!")
                    # Tentar outros campos possíveis
                    for campo in ['cod_imov', 'codigo', 'id', 'gid', 'fid']:
                        if properties.get(campo):
                            novo_ultimo_id = properties.get(campo)
                            self._update_status(f"  {estado}: usando campo alternativo '{campo}': {novo_ultimo_id}")
                            break
                
                ultimo_id = novo_ultimo_id
                
                # Converter JSON para Layer temporária
                try:
                    layer_temp = QgsVectorLayer(json_path, f"{estado}_lote_{lote_numero}", "ogr")
                    
                    if layer_temp.isValid() and layer_temp.featureCount() > 0:
                        # Salvar como shapefile temporário
                        shp_path = os.path.join(pasta_temp, f"{estado}_lote_{lote_numero:04d}.shp")
                        
                        options = QgsVectorFileWriter.SaveVectorOptions()
                        options.driverName = "ESRI Shapefile"
                        options.fileEncoding = "UTF-8"
                        
                        error = QgsVectorFileWriter.writeAsVectorFormatV3(
                            layer_temp, shp_path,
                            QgsProject.instance().transformContext(),
                            options
                        )
                        
                        if error[0] == QgsVectorFileWriter.NoError:
                            arquivos_baixados.append(shp_path)
                            total_baixados = sum(1 for _ in arquivos_baixados) * limite_por_lote  # Estimativa
                            self._update_status(f"  {estado}: lote {lote_numero} OK - ~{total_baixados:,} registros acumulados")
                            QApplication.processEvents()
                        else:
                            self._update_status(f"  {estado}: erro ao salvar lote {lote_numero}")
                        
                        # Se baixou menos que o limite, é o último lote
                        if num_features < limite_por_lote:
                            self._update_status(f"  {estado}: último lote detectado ({num_features} < {limite_por_lote})")
                            QApplication.processEvents()
                            break
                    else:
                        self._update_status(f"  {estado}: layer inválida ou vazia no lote {lote_numero}")
                    
                except Exception as e:
                    self._update_status(f"  {estado}: erro ao processar lote {lote_numero}: {str(e)[:40]}")
                    QApplication.processEvents()
                
                # Remover JSON temporário
                try:
                    os.remove(json_path)
                except:
                    pass
                
                lote_numero += 1
                
                # Pequena pausa entre requisições para não sobrecarregar
                import time
                time.sleep(0.3)
                
                # Log de progresso
                if lote_numero % 10 == 0:
                    self._update_status(f"  {estado}: {len(arquivos_baixados)} lotes baixados, continuando...")
                    QApplication.processEvents()
                
            except urllib.error.HTTPError as e:
                erro_msg = f"HTTP {e.code}: {e.reason}"
                self._update_status(f"  {estado}: erro HTTP - {erro_msg}")
                QApplication.processEvents()
                if e.code in [500, 502, 503, 504]:
                    # Erro de servidor - tentar novamente
                    import time
                    time.sleep(5)
                    continue
                break
                
            except urllib.error.URLError as e:
                self._update_status(f"  {estado}: erro de conexão - {str(e.reason)[:40]}")
                QApplication.processEvents()
                break
                
            except Exception as e:
                self._update_status(f"  {estado}: erro inesperado - {str(e)[:50]}")
                QApplication.processEvents()
                break
        
        total_registros = sum(1 for _ in arquivos_baixados) if arquivos_baixados else 0
        if arquivos_baixados:
            self._update_status(f"  {estado}: finalizado - {len(arquivos_baixados)} lotes baixados")
        else:
            self._update_status(f"  {estado}: AVISO - nenhum dado baixado")
        QApplication.processEvents()
        
        return arquivos_baixados
    
    def _consolidar_car(self, arquivos, gpkg_path, layer_key):
        """Consolida múltiplos shapefiles do CAR em uma camada no GeoPackage."""
        if not arquivos:
            self._update_status("CAR: nenhum arquivo para consolidar")
            return
        
        self._update_status(f"CAR: consolidando {len(arquivos)} arquivos...")
        QApplication.processEvents()
        
        # Nome da tabela no GeoPackage
        table_name = "car_amazonia"
        
        total_features = 0
        arquivos_processados = 0
        
        for idx, arquivo in enumerate(arquivos):
            try:
                # Carregar arquivo
                layer = QgsVectorLayer(arquivo, f"car_lote_{idx}", "ogr")
                
                if not layer.isValid():
                    self._update_status(f"  Arquivo {idx+1}/{len(arquivos)}: inválido, pulando...")
                    continue
                
                feat_count = layer.featureCount()
                if feat_count == 0:
                    continue
                
                # Configurar opções de salvamento
                options = QgsVectorFileWriter.SaveVectorOptions()
                options.layerName = table_name
                options.fileEncoding = "UTF-8"
                
                if idx == 0:
                    # Primeiro arquivo: criar ou sobrescrever
                    if os.path.exists(gpkg_path):
                        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
                    else:
                        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
                else:
                    # Demais arquivos: append
                    options.actionOnExistingFile = QgsVectorFileWriter.AppendToLayerNoNewFields
                
                error = QgsVectorFileWriter.writeAsVectorFormatV3(
                    layer, gpkg_path,
                    QgsProject.instance().transformContext(),
                    options
                )
                
                if error[0] == QgsVectorFileWriter.NoError:
                    total_features += feat_count
                    arquivos_processados += 1
                    self._update_status(f"  CAR: {arquivos_processados}/{len(arquivos)} - {total_features:,} registros")
                    QApplication.processEvents()
                else:
                    self._update_status(f"  Erro no arquivo {idx+1}: {error[1][:30]}")
                    
            except Exception as e:
                self._update_status(f"  Erro no arquivo {idx+1}: {str(e)[:30]}")
                continue
        
        self._update_status(f"CAR consolidado: {total_features:,} registros de {arquivos_processados} arquivos")
    
    # ==========================================================================
    #                    ABA 2: PROCESSAMENTO (EM CONSTRUÇÃO)
    # ==========================================================================
    
    def _create_tab_processamento(self):
        """Cria a aba de processamento."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setAlignment(Qt.AlignCenter)
        
        # Titulo indicando desenvolvimento
        title = QLabel("[ EM DESENVOLVIMENTO ]")
        title.setStyleSheet(f"""
            font-size: 32px;
            font-weight: bold;
            color: {self.COLORS['verde_escuro']};
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # Descrição
        desc = QLabel(
            "Esta aba implementará o processamento automatizado de elegibilidade\n"
            "conforme os critérios da Chamada Pública 02/2024.\n\n"
            "Funcionalidades previstas:\n"
            "- Verificacao de localizacao (Amazonia Legal / Municipios Prioritarios)\n"
            "- Checagem de sobreposicoes (TI, Quilombos, UC, CNFP)\n"
            "- Analise de embargos (IBAMA/ICMBio)\n"
            "- Calculo de modulos fiscais\n"
            "- Verificacao de PRODES\n"
            "- Avaliacao de RVN por fitofisionomia\n"
            "- Classificacao Fase 1 / Fase 2 / Inelegivel"
        )
        desc.setStyleSheet(f"""
            font-size: 14px;
            color: {self.COLORS['texto_claro']};
            line-height: 1.6;
        """)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        
        return tab
    
    # ==========================================================================
    #                    ABA 3: LAUDOS (EM CONSTRUÇÃO)
    # ==========================================================================
    
    def _create_tab_laudos(self):
        """Cria a aba de geração de laudos."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setAlignment(Qt.AlignCenter)
        
        # Titulo indicando desenvolvimento
        title = QLabel("[ EM DESENVOLVIMENTO ]")
        title.setStyleSheet(f"""
            font-size: 32px;
            font-weight: bold;
            color: {self.COLORS['verde_escuro']};
        """)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # Descrição
        desc = QLabel(
            "Esta aba implementará a geração automática de laudos técnicos.\n\n"
            "Funcionalidades previstas:\n"
            "- Geracao de parecer individual por imovel\n"
            "- Mapas de localizacao e situacao\n"
            "- Exportacao em PDF\n"
            "- Planilha consolidada de resultados\n"
            "- Relatorios estatisticos por municipio/estado"
        )
        desc.setStyleSheet(f"""
            font-size: 14px;
            color: {self.COLORS['texto_claro']};
            line-height: 1.6;
        """)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        
        return tab
    
    # ==========================================================================
    #                              EVENTOS / AÇÕES
    # ==========================================================================
    
    def _browse_and_load_gpkg(self):
        """Abre diálogo para selecionar GeoPackage e verifica suas camadas."""
        path, _ = QFileDialog.getOpenFileName(
            self, 
            "Selecionar GeoPackage de Referencia",
            "",
            "GeoPackage (*.gpkg);;Todos os arquivos (*.*)"
        )
        if path:
            self.gpkg_path.setText(path)
            self._update_status(f"Verificando camadas de: {os.path.basename(path)}")
            QApplication.processEvents()
            
            # Verificar camadas necessárias na tabela
            self._populate_layers_from_config()
            self._update_status(f"GeoPackage verificado: {os.path.basename(path)}")
            
            # Esconder botão "Baixar Todas" para bases existentes
            self.btn_download_all.setVisible(False)
            
            # Verificar se tem imóveis e atualizar status no grupo RVN
            self._verificar_imoveis_para_rvn()
            
            # Habilitar botão de processamento de elegibilidade
            if hasattr(self, 'btn_iniciar_processamento'):
                self.btn_iniciar_processamento.setEnabled(True)
            
            # Verificar se existe camada de elegíveis processada
            self._verificar_camada_elegiveis(path)
    
    def _verificar_camada_elegiveis(self, gpkg_path):
        """
        Verifica se existe a camada 'elegiveis' no GeoPackage.
        Se existir, habilita os botões de laudos e carrega a camada.
        """
        if not gpkg_path or not os.path.exists(gpkg_path):
            return
        
        # Verificar se a camada 'elegiveis' existe
        uri = f"{gpkg_path}|layername=elegiveis"
        layer = QgsVectorLayer(uri, "elegiveis", "ogr")
        
        if layer.isValid() and layer.featureCount() > 0:
            # Camada existe e tem feições - habilitar botões de laudos
            self.camada_elegiveis = layer
            self.gpkg_elegiveis = gpkg_path
            
            # Habilitar botões de laudos e reprocessar parecer
            if hasattr(self, 'btn_visualizar_laudos'):
                self.btn_visualizar_laudos.setEnabled(True)
            if hasattr(self, 'btn_reprocessar_parecer'):
                self.btn_reprocessar_parecer.setEnabled(True)
            
            # Atualizar label informativo
            if hasattr(self, 'laudos_info'):
                self.laudos_info.setText(f"✓ {layer.featureCount()} imóveis processados disponíveis")
                self.laudos_info.setStyleSheet(f"color: #27ae60; font-size: 10px;")
            
            self._update_status(f"Camada de elegíveis encontrada: {layer.featureCount()} imóveis")
        else:
            # Camada não existe - desabilitar botão de laudos
            if hasattr(self, 'btn_visualizar_laudos'):
                self.btn_visualizar_laudos.setEnabled(False)
            
            if hasattr(self, 'laudos_info'):
                self.laudos_info.setText("Visualize resultados e gere laudos em PDF")
                self.laudos_info.setStyleSheet(f"color: {self.COLORS['texto_claro']}; font-size: 10px; font-style: italic;")
    
    def _create_new_gpkg(self):
        """Cria um novo GeoPackage vazio para as camadas de referência."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Criar Novo GeoPackage",
            "camadas_referencia.gpkg",
            "GeoPackage (*.gpkg)"
        )
        
        if not path:
            return
        
        # Garantir extensão .gpkg
        if not path.lower().endswith('.gpkg'):
            path += '.gpkg'
        
        # Se já existe, perguntar se deseja sobrescrever
        if os.path.exists(path):
            reply = QMessageBox.question(
                self, "Arquivo Existente",
                f"O arquivo já existe:\n{path}\n\nDeseja sobrescrever?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            os.remove(path)
        
        # Criar GeoPackage vazio usando SQLite
        try:
            import sqlite3
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            
            # Criar tabelas mínimas do GeoPackage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gpkg_contents (
                    table_name TEXT NOT NULL PRIMARY KEY,
                    data_type TEXT NOT NULL,
                    identifier TEXT UNIQUE,
                    description TEXT DEFAULT '',
                    last_change DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    min_x DOUBLE,
                    min_y DOUBLE,
                    max_x DOUBLE,
                    max_y DOUBLE,
                    srs_id INTEGER
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
                    srs_name TEXT NOT NULL,
                    srs_id INTEGER NOT NULL PRIMARY KEY,
                    organization TEXT NOT NULL,
                    organization_coordsys_id INTEGER NOT NULL,
                    definition TEXT NOT NULL,
                    description TEXT
                )
            """)
            
            # Adicionar SRS padrão (WGS84)
            cursor.execute("""
                INSERT OR IGNORE INTO gpkg_spatial_ref_sys 
                (srs_name, srs_id, organization, organization_coordsys_id, definition) VALUES
                ('WGS 84', 4326, 'EPSG', 4326, 
                'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
            """)
            
            # Adicionar SIRGAS 2000
            cursor.execute("""
                INSERT OR IGNORE INTO gpkg_spatial_ref_sys 
                (srs_name, srs_id, organization, organization_coordsys_id, definition) VALUES
                ('SIRGAS 2000', 4674, 'EPSG', 4674,
                'GEOGCS["SIRGAS 2000",DATUM["Sistema_de_Referencia_Geocentrico_para_las_AmericaS_2000",SPHEROID["GRS 1980",6378137,298.257222101]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]')
            """)
            
            conn.commit()
            conn.close()
            
            # Definir caminho e atualizar interface
            self.gpkg_path.setText(path)
            self._populate_layers_from_config()
            
            # Mostrar botão "Baixar Todas" para nova base
            self.btn_download_all.setVisible(True)
            
            self._update_status(f"Novo GeoPackage criado: {os.path.basename(path)}")
            
            # Habilitar botão de processamento
            if hasattr(self, 'btn_iniciar_processamento'):
                self.btn_iniciar_processamento.setEnabled(True)
            
            QMessageBox.information(
                self, "Sucesso",
                f"GeoPackage criado com sucesso!\n\n"
                f"Arquivo: {os.path.basename(path)}\n\n"
                "Clique em 'Baixar Todas' para popular todas as camadas\n"
                "ou use o botão 'Atualizar' em cada camada individualmente."
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao criar GeoPackage:\n{str(e)}")
    
    def _download_all_layers(self):
        """Baixa todas as camadas de referência configuradas."""
        gpkg_path = self.gpkg_path.text()
        if not gpkg_path:
            QMessageBox.warning(self, "Aviso", "Selecione ou crie um GeoPackage primeiro")
            return
        
        camadas = self.config.get('camadas_referencia', {})
        
        # Filtrar camadas que têm fonte de download (WFS, ZIP ou CAR)
        camadas_disponiveis = []
        
        for key, info in camadas.items():
            tipo = info.get('tipo', 'local')
            has_wfs = 'url_wfs' in info and 'layer_name' in info
            has_download = 'url_download' in info
            url_download = info.get('url_download', '')
            
            if tipo == 'car_wfs':
                camadas_disponiveis.append((key, info, "CAR"))
            elif tipo == 'prodes_wfs':
                camadas_disponiveis.append((key, info, "PRODES"))
            elif tipo == 'quilombolas_wfs':
                camadas_disponiveis.append((key, info, "QUILOMBOLAS"))
            elif tipo == 'wfs' or (has_wfs and tipo != 'download'):
                camadas_disponiveis.append((key, info, "WFS"))
            elif tipo == 'download' or (has_download and (
                url_download.lower().endswith('.zip') or
                url_download.lower().endswith('/data') or
                'SHAPE-ZIP' in url_download or
                'outputFormat=SHAPE' in url_download
            )):
                camadas_disponiveis.append((key, info, "ZIP"))
        
        if not camadas_disponiveis:
            QMessageBox.information(self, "Info", "Nenhuma camada com fonte de download automático configurada.")
            return
        
        # Criar diálogo de seleção
        dialog = QDialog(self)
        dialog.setWindowTitle("Selecionar Camadas para Download")
        dialog.setMinimumWidth(450)
        dialog.setMinimumHeight(400)
        
        layout = QVBoxLayout(dialog)
        
        # Instrução
        label = QLabel("Selecione as camadas que deseja baixar:")
        label.setStyleSheet("font-weight: bold; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(label)
        
        # Lista de checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        checkboxes = {}
        for key, info, tipo in camadas_disponiveis:
            nome = info.get('nome', key)
            
            # Indicador de tempo estimado para cada tipo
            if tipo == "CAR":
                label_text = f"🗂️ {nome} (~18 min)"
            elif tipo == "PRODES":
                label_text = f"🛰️ {nome} (~1h 30min)"
            elif tipo == "QUILOMBOLAS":
                label_text = f"🏘️ {nome} (~18s)"
            elif tipo == "WFS":
                label_text = f"🌐 {nome} (~30s)"
            elif tipo == "ZIP":
                label_text = f"📦 {nome} (~30s)"
            else:
                label_text = f"{nome} [{tipo}]"
            
            cb = QCheckBox(label_text)
            cb.setChecked(True)  # Todas marcadas por padrão
            cb.setToolTip(info.get('descricao', ''))
            checkboxes[key] = (cb, info, tipo)
            scroll_layout.addWidget(cb)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        # Botões Selecionar Todas / Nenhuma
        btn_layout = QHBoxLayout()
        btn_all = QPushButton("Selecionar Todas")
        btn_all.clicked.connect(lambda: [cb.setChecked(True) for cb, _, _ in checkboxes.values()])
        btn_none = QPushButton("Nenhuma")
        btn_none.clicked.connect(lambda: [cb.setChecked(False) for cb, _, _ in checkboxes.values()])
        btn_layout.addWidget(btn_all)
        btn_layout.addWidget(btn_none)
        layout.addLayout(btn_layout)
        
        # Aviso
        aviso = QLabel("⏱️ CAR ~18min | PRODES ~1h30 | WFS/ZIP ~30s cada | Total ~2h")
        aviso.setStyleSheet("color: #3498db; font-size: 10px; font-style: italic;")
        layout.addWidget(aviso)
        
        # Botões OK/Cancelar
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec_() != QDialog.Accepted:
            return
        
        # Obter camadas selecionadas
        camadas_para_baixar = []
        for key, (cb, info, tipo) in checkboxes.items():
            if cb.isChecked():
                camadas_para_baixar.append((key, info))
        
        if not camadas_para_baixar:
            QMessageBox.information(self, "Info", "Nenhuma camada selecionada.")
            return
        
        # Iniciar download
        self.progress_bar.setVisible(True)
        self.btn_download_all.setEnabled(False)
        
        resultados = {'sucesso': [], 'erro': []}
        
        # TEMPORÁRIO: Contagem de tempo por camada
        import time as time_module
        tempos_camadas = {}
        tempo_total_inicio = time_module.time()
        
        for idx, (layer_key, layer_info) in enumerate(camadas_para_baixar):
            layer_name = layer_info.get('nome', layer_key)
            
            # Atualizar progresso
            progresso = int((idx / len(camadas_para_baixar)) * 100)
            self.progress_bar.setValue(progresso)
            self._update_status(f"Baixando {layer_name} ({idx+1}/{len(camadas_para_baixar)})...")
            QApplication.processEvents()
            
            # TEMPORÁRIO: Iniciar contagem de tempo
            tempo_inicio = time_module.time()
            
            try:
                # Chamar função de download apropriada
                tipo = layer_info.get('tipo', 'local')
                url_wfs = layer_info.get('url_wfs', '')
                wfs_layer_name = layer_info.get('layer_name', '')
                url_download = layer_info.get('url_download', '')
                
                download_ok = False
                
                if tipo == 'car_wfs':
                    self._update_status(f"⏳ {layer_name} - INICIANDO...")
                    QApplication.processEvents()
                    download_ok = self._download_car_amazonia_silent(layer_key, gpkg_path)
                elif tipo == 'prodes_wfs':
                    self._update_status(f"⏳ {layer_name} - INICIANDO...")
                    QApplication.processEvents()
                    download_ok = self._download_prodes_silent(layer_key, gpkg_path)
                elif tipo == 'quilombolas_wfs':
                    self._update_status(f"⏳ {layer_name} - INICIANDO...")
                    QApplication.processEvents()
                    download_ok = self._download_quilombolas_silent(layer_key, gpkg_path)
                elif url_wfs and wfs_layer_name:
                    download_ok = self._download_from_wfs_silent(layer_key, url_wfs, wfs_layer_name, gpkg_path)
                elif tipo == 'download' or (url_download and (
                    url_download.lower().endswith('.zip') or 
                    url_download.lower().endswith('/data') or
                    'SHAPE-ZIP' in url_download or
                    'outputFormat=SHAPE' in url_download
                )):
                    download_ok = self._download_from_zip_batch(layer_key, url_download, gpkg_path)
                else:
                    resultados['erro'].append(f"{layer_name} (sem fonte configurada)")
                    continue
                
                # TEMPORÁRIO: Calcular tempo decorrido
                tempo_fim = time_module.time()
                tempo_decorrido = tempo_fim - tempo_inicio
                tempos_camadas[layer_name] = {
                    'tempo_segundos': tempo_decorrido,
                    'tempo_formatado': self._formatar_tempo(tempo_decorrido),
                    'tipo': tipo,
                    'sucesso': download_ok
                }
                
                if download_ok:
                    resultados['sucesso'].append(layer_name)
                    self._update_status(f"✅ {layer_name} OK ({self._formatar_tempo(tempo_decorrido)})")
                    QApplication.processEvents()
                else:
                    resultados['erro'].append(f"{layer_name} (falha)")
                    self._update_status(f"❌ {layer_name} falhou ({self._formatar_tempo(tempo_decorrido)})")
                    QApplication.processEvents()
                    
            except Exception as e:
                tempo_fim = time_module.time()
                tempo_decorrido = tempo_fim - tempo_inicio
                tempos_camadas[layer_name] = {
                    'tempo_segundos': tempo_decorrido,
                    'tempo_formatado': self._formatar_tempo(tempo_decorrido),
                    'tipo': tipo if 'tipo' in dir() else 'unknown',
                    'sucesso': False
                }
                error_msg = str(e)[:50] if str(e) else "Erro desconhecido"
                resultados['erro'].append(f"{layer_name}: {error_msg}")
                self._update_status(f"❌ {layer_name}: {error_msg}")
                QApplication.processEvents()
                continue
        
        # TEMPORÁRIO: Tempo total
        tempo_total_fim = time_module.time()
        tempo_total = tempo_total_fim - tempo_total_inicio
        
        # Finalizar
        self.progress_bar.setValue(100)
        self.btn_download_all.setEnabled(True)
        self.btn_download_all.setVisible(False)
        
        # Atualizar tabela
        self._populate_layers_from_config()
        
        self.progress_bar.setVisible(False)
        
        # TEMPORÁRIO: Imprimir relatório de tempos no console
        print("\n" + "="*70)
        print("📊 RELATÓRIO DE TEMPOS DE DOWNLOAD")
        print("="*70)
        for nome, dados in tempos_camadas.items():
            status = "✅" if dados['sucesso'] else "❌"
            print(f"{status} {nome:<35} | {dados['tempo_formatado']:>10} | {dados['tipo']}")
        print("-"*70)
        print(f"⏱️ TEMPO TOTAL: {self._formatar_tempo(tempo_total)}")
        print("="*70 + "\n")
        
        # Mostrar resultado em janela maior
        msg_resultado = f"Download concluído!\n\n"
        msg_resultado += f"⏱️ Tempo total: {self._formatar_tempo(tempo_total)}\n\n"
        msg_resultado += f"✅ Sucesso: {len(resultados['sucesso'])} camadas\n"
        if resultados['sucesso']:
            for nome in resultados['sucesso']:
                tempo_info = tempos_camadas.get(nome, {})
                tempo_str = tempo_info.get('tempo_formatado', '?')
                msg_resultado += f"  • {nome} ({tempo_str})\n"
        
        if resultados['erro']:
            msg_resultado += f"\n❌ Erros: {len(resultados['erro'])} camadas\n"
            msg_resultado += "\n".join(f"  • {nome}" for nome in resultados['erro'])
        
        # Criar diálogo com tamanho adequado
        resultado_dialog = QMessageBox(self)
        resultado_dialog.setWindowTitle("Resultado do Download")
        resultado_dialog.setText(msg_resultado)
        resultado_dialog.setIcon(QMessageBox.Information)
        resultado_dialog.setStandardButtons(QMessageBox.Ok)
        resultado_dialog.exec_()
    
    def _formatar_tempo(self, segundos):
        """Formata tempo em segundos para string legível."""
        if segundos < 60:
            return f"{segundos:.1f}s"
        elif segundos < 3600:
            minutos = int(segundos // 60)
            segs = int(segundos % 60)
            return f"{minutos}m {segs}s"
        else:
            horas = int(segundos // 3600)
            minutos = int((segundos % 3600) // 60)
            return f"{horas}h {minutos}m"
    
    def _download_from_wfs_silent(self, layer_key, url_wfs, wfs_layer_name, gpkg_path):
        """
        Versão silenciosa do download WFS.
        Retorna True se sucesso, False se falha.
        """
        try:
            layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
            display_name = layer_info.get('nome', layer_key)
            fallback_layer = layer_info.get('layer_name_fallback', '')
            
            self._update_status(f"  WFS: conectando {display_name}...")
            QApplication.processEvents()
            
            wfs_uri = (
                f"pagingEnabled='default' "
                f"preferCoordinatesForWfsT11='false' "
                f"restrictToRequestBBOX='1' "
                f"srsname='EPSG:4674' "
                f"typename='{wfs_layer_name}' "
                f"url='{url_wfs}' "
                f"version='auto'"
            )
            
            wfs_layer = QgsVectorLayer(wfs_uri, display_name, "WFS")
            
            # Tentar fallback se falhar
            if not wfs_layer.isValid() and fallback_layer:
                self._update_status(f"  WFS: tentando fonte alternativa...")
                QApplication.processEvents()
                wfs_uri_fallback = (
                    f"pagingEnabled='default' "
                    f"preferCoordinatesForWfsT11='false' "
                    f"restrictToRequestBBOX='1' "
                    f"srsname='EPSG:4674' "
                    f"typename='{fallback_layer}' "
                    f"url='{url_wfs}' "
                    f"version='auto'"
                )
                wfs_layer = QgsVectorLayer(wfs_uri_fallback, display_name, "WFS")
            
            if not wfs_layer.isValid():
                self._update_status(f"  WFS: camada inválida")
                return False
            
            feat_count = wfs_layer.featureCount()
            if feat_count == 0:
                self._update_status(f"  WFS: nenhum dado retornado")
                return False
            
            self._update_status(f"  WFS: salvando {feat_count} features...")
            QApplication.processEvents()
            
            # Salvar no GeoPackage (nome padronizado)
            table_name = self._get_table_name(layer_key)
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = table_name
            options.fileEncoding = "UTF-8"
            
            if not os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                wfs_layer, gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] != QgsVectorFileWriter.NoError:
                self._update_status(f"  WFS: erro ao salvar - {error[1][:30]}")
                return False
            
            self._update_status(f"  WFS: {display_name} salvo com sucesso")
            return True
            
        except Exception as e:
            self._update_status(f"  WFS: erro - {str(e)[:40]}")
            return False
    
    def _download_from_zip_silent(self, layer_key, url_zip, gpkg_path):
        """
        Versão silenciosa do download ZIP.
        Retorna True se sucesso, False se falha.
        """
        zip_path = None
        extracted_files = None
        
        try:
            layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
            display_name = layer_info.get('nome', layer_key)
            
            self._update_status(f"  ZIP: baixando {display_name}...")
            QApplication.processEvents()
            
            zip_path = self._download_zip_file(url_zip)
            if not zip_path:
                self._update_status(f"  ZIP: falha no download")
                return False
            
            self._update_status(f"  ZIP: extraindo arquivos...")
            QApplication.processEvents()
            
            extracted_files = self._extract_zip_file(zip_path)
            if not extracted_files:
                self._update_status(f"  ZIP: falha na extração")
                return False
            
            shapefile_path = self._find_shapefile(extracted_files)
            if not shapefile_path:
                self._update_status(f"  ZIP: shapefile não encontrado")
                return False
            
            layer = QgsVectorLayer(shapefile_path, display_name, "ogr")
            if not layer.isValid():
                self._update_status(f"  ZIP: shapefile inválido")
                return False
            
            self._update_status(f"  ZIP: salvando {layer.featureCount()} features...")
            QApplication.processEvents()
            
            # Salvar no GeoPackage (nome padronizado)
            table_name = self._get_table_name(layer_key)
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = table_name
            options.fileEncoding = "UTF-8"
            
            if not os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] != QgsVectorFileWriter.NoError:
                self._update_status(f"  ZIP: erro ao salvar - {error[1][:30]}")
                return False
            
            self._update_status(f"  ZIP: {display_name} salvo com sucesso")
            return True
            
        except Exception as e:
            self._update_status(f"  ZIP: erro - {str(e)[:40]}")
            return False
            
        finally:
            if zip_path or extracted_files:
                try:
                    self._cleanup_temp_files(zip_path, extracted_files)
                except:
                    pass
    
    def _download_prodes_silent(self, layer_key, gpkg_path):
        """
        Download do PRODES Amazônia Legal via WFS com paginação.
        Retorna True se sucesso, False se falha.
        """
        import urllib.request
        import urllib.parse
        import urllib.error
        import ssl
        import time
        
        try:
            layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
            display_name = layer_info.get('nome', 'PRODES')
            
            base_url = layer_info.get('url_wfs', 
                'https://terrabrasilis.dpi.inpe.br/geoserver/prodes-legal-amz/yearly_deforestation/ows')
            typename = layer_info.get('layer_name', 'prodes-legal-amz:yearly_deforestation')
            
            self._update_status(f"PRODES: conectando ao servidor...")
            self.progress_bar.setValue(1)
            QApplication.processEvents()
            
            # SSL context permissivo
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Configuração de paginação - 50k features por página
            page_size = 50000  # 50k por página
            start_index = 0
            all_temp_files = []
            total_features = 0
            page_number = 1
            max_pages = 100  # Limite de segurança (~5M features)
            
            temp_dir = tempfile.gettempdir()
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            
            self._update_status(f"PRODES: preparando download paginado (50k/página)...")
            self.progress_bar.setValue(2)
            QApplication.processEvents()
            
            # PRODES tem cerca de 3 milhões de features, com 50k/página = ~60 páginas
            estimated_total = 3000000
            
            while page_number <= max_pages:
                # Progresso do download (0-85%) baseado em features baixadas
                download_progress = min(85, int((total_features / estimated_total) * 85) + 3)
                self.progress_bar.setValue(download_progress)
                self._update_status(f"PRODES: página {page_number} ({total_features:,} features)...")
                QApplication.processEvents()
                
                # Parâmetros WFS
                params = {
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeName": typename,
                    "outputFormat": "GML2",
                    "srsName": "EPSG:4674",
                    "count": page_size,
                    "startIndex": start_index
                }
                
                url = f"{base_url}?{urllib.parse.urlencode(params)}"
                
                try:
                    req = urllib.request.Request(url)
                    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) QGIS')
                    req.add_header('Accept', 'application/xml, */*')
                    
                    # Download com processamento de eventos
                    self._update_status(f"PRODES: conectando página {page_number}...")
                    QApplication.processEvents()
                    
                    response = urllib.request.urlopen(req, timeout=300, context=ssl_context)
                    
                    # Ler em chunks para manter interface responsiva
                    chunks = []
                    bytes_read = 0
                    self._update_status(f"PRODES: recebendo dados da página {page_number}...")
                    QApplication.processEvents()
                    
                    while True:
                        chunk = response.read(32768)  # 32KB por vez
                        if not chunk:
                            break
                        chunks.append(chunk)
                        bytes_read += len(chunk)
                        
                        # Atualizar status a cada 500KB
                        if bytes_read % 524288 < 32768:
                            self._update_status(f"PRODES: página {page_number} - {bytes_read // 1024}KB...")
                            QApplication.processEvents()
                    
                    response.close()
                    content = b''.join(chunks)
                    
                    self._update_status(f"PRODES: página {page_number} - {len(content) // 1024}KB recebidos")
                    QApplication.processEvents()
                    
                    if len(content) < 100:
                        self._update_status(f"PRODES: resposta vazia na página {page_number}")
                        if page_number == 1:
                            return False
                        break
                    
                    # Salvar arquivo temporário
                    temp_file = os.path.join(temp_dir, f"prodes_{timestamp}_page_{page_number}.gml")
                    with open(temp_file, 'wb') as f:
                        f.write(content)
                    
                    self._update_status(f"PRODES: página {page_number} salva, verificando...")
                    QApplication.processEvents()
                    
                    # Verificar se página contém erro do servidor
                    content_start = content[:1000].decode('utf-8', errors='ignore')
                    if 'ExceptionReport' in content_start or 'ServiceException' in content_start:
                        self._update_status(f"PRODES: erro no servidor - fim dos dados")
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                        break
                    
                    if 'numberOfFeatures="0"' in content_start:
                        self._update_status(f"PRODES: fim dos dados (0 features)")
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                        break
                    
                    # Testar layer (rápido)
                    self._update_status(f"PRODES: validando página {page_number}...")
                    QApplication.processEvents()
                    
                    test_layer = QgsVectorLayer(temp_file, f"prodes_page_{page_number}", "ogr")
                    
                    if not test_layer.isValid():
                        self._update_status(f"PRODES: página {page_number} inválida - possível fim dos dados")
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                        break
                    
                    page_features = test_layer.featureCount()
                    
                    if page_features == 0:
                        self._update_status(f"PRODES: fim dos dados (página vazia)")
                        try:
                            os.remove(temp_file)
                        except:
                            pass
                        break
                    
                    # Página OK - adicionar à lista
                    all_temp_files.append(temp_file)
                    total_features += page_features
                    
                    self._update_status(f"PRODES: página {page_number} ✓ {page_features:,} features (total: {total_features:,})")
                    QApplication.processEvents()
                    
                    # Última página?
                    if page_features < page_size:
                        self._update_status(f"PRODES: última página ({page_features:,} < {page_size:,})")
                        break
                    
                    start_index += page_size
                    page_number += 1
                    
                    # Pausa breve entre requisições
                    time.sleep(0.2)
                    QApplication.processEvents()
                    
                except urllib.error.HTTPError as e:
                    self._update_status(f"PRODES: erro HTTP {e.code}")
                    if page_number == 1:
                        return False
                    break
                except urllib.error.URLError as e:
                    self._update_status(f"PRODES: erro de conexão - {str(e.reason)[:30]}")
                    if page_number == 1:
                        return False
                    break
                except Exception as e:
                    self._update_status(f"PRODES: erro - {str(e)[:40]}")
                    if page_number == 1:
                        return False
                    break
            
            if not all_temp_files:
                self._update_status(f"PRODES: nenhum dado baixado")
                return False
            
            self._update_status(f"PRODES: consolidando {len(all_temp_files)} páginas ({total_features:,} features)...")
            self.progress_bar.setValue(95)
            QApplication.processEvents()
            
            # Consolidar páginas no GeoPackage (salvar incrementalmente)
            table_name = "prodes"
            total_pages = len(all_temp_files)
            saved_features = 0
            
            for idx, temp_file in enumerate(all_temp_files):
                try:
                    # Progresso do salvamento (87-100%)
                    save_progress = 87 + int(((idx + 1) / total_pages) * 13)
                    self.progress_bar.setValue(save_progress)
                    
                    self._update_status(f"Salvando PRODES no GeoPackage... {idx+1}/{total_pages}")
                    QApplication.processEvents()
                    
                    layer = QgsVectorLayer(temp_file, f"prodes_page_{idx}", "ogr")
                    if not layer.isValid():
                        self._update_status(f"PRODES: página {idx+1} inválida, pulando...")
                        QApplication.processEvents()
                        continue
                    
                    feat_count = layer.featureCount()
                    self._update_status(f"Salvando PRODES... página {idx+1}/{total_pages} ({feat_count:,} features)")
                    QApplication.processEvents()
                    
                    options = QgsVectorFileWriter.SaveVectorOptions()
                    options.layerName = table_name
                    options.fileEncoding = "UTF-8"
                    
                    if idx == 0:
                        self._update_status(f"PRODES: criando camada no GeoPackage...")
                        QApplication.processEvents()
                        if os.path.exists(gpkg_path):
                            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
                        else:
                            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
                    else:
                        self._update_status(f"PRODES: adicionando página {idx+1}...")
                        QApplication.processEvents()
                        options.actionOnExistingFile = QgsVectorFileWriter.AppendToLayerNoNewFields
                    
                    # Escrever no GeoPackage
                    error = QgsVectorFileWriter.writeAsVectorFormatV3(
                        layer, gpkg_path,
                        QgsProject.instance().transformContext(),
                        options
                    )
                    
                    # Processar eventos entre cada salvamento
                    QApplication.processEvents()
                    
                    if error[0] == QgsVectorFileWriter.NoError:
                        saved_features += feat_count
                        self._update_status(f"PRODES: página {idx+1} salva ✓ (total: {saved_features:,})")
                    else:
                        self._update_status(f"PRODES: erro ao salvar página {idx+1}: {error[1][:30]}")
                        
                except Exception as e:
                    self._update_status(f"PRODES: erro na página {idx+1} - {str(e)[:30]}")
                finally:
                    try:
                        os.remove(temp_file)
                    except:
                        pass
            
            self._update_status(f"PRODES: concluído - {saved_features:,} features salvas")
            self.progress_bar.setValue(100)
            QApplication.processEvents()
            return True
            
        except Exception as e:
            self._update_status(f"PRODES: erro geral - {str(e)[:40]}")
            return False
    
    def _create_ssl_context_car(self):
        """
        Cria um contexto SSL compatível com o servidor CAR (que tem configuração antiga).
        """
        import ssl
        
        try:
            # Criar contexto com TLS
            context = ssl.create_default_context()
            
            # Desabilitar verificação (servidor gov pode ter cert problemático)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            # Habilitar protocolos TLS mais antigos (servidor CAR pode não suportar TLS 1.3)
            try:
                context.options &= ~ssl.OP_NO_TLSv1
                context.options &= ~ssl.OP_NO_TLSv1_1
                context.options &= ~ssl.OP_NO_TLSv1_2
            except:
                pass
            
            # Definir cifras compatíveis (importante para servidores antigos)
            try:
                context.set_ciphers('DEFAULT:@SECLEVEL=1')
            except:
                try:
                    context.set_ciphers('DEFAULT')
                except:
                    pass
            
            return context
            
        except Exception as e:
            # Fallback para contexto mais permissivo
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                return context
            except:
                # Último recurso
                return ssl._create_unverified_context()
    
    def _download_quilombolas_silent(self, layer_key, gpkg_path):
        """
        Versão silenciosa do download de Quilombolas (sem confirmação).
        Retorna True se sucesso, False se falha.
        """
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        url_base = layer_info.get('url_wfs_base', 'http://acervofundiario.incra.gov.br/i3geo/ogc.php?tema=quilombolas_')
        estados = layer_info.get('estados', ['ac', 'am', 'ap', 'ma', 'mt', 'pa', 'pi', 'ro', 'rr', 'to'])
        
        todas_features = []
        estados_sucesso = []
        crs_referencia = None
        fields_referencia = None
        
        total_estados = len(estados)
        
        try:
            for i, estado in enumerate(estados):
                self._update_status(f"Quilombolas: baixando {estado.upper()} ({i+1}/{total_estados})...")
                QApplication.processEvents()
                
                try:
                    url_estado = f"{url_base}{estado}"
                    
                    # URI para QGIS WFS - typename precisa do prefixo 'ms:'
                    wfs_uri = (
                        f"pagingEnabled='default' "
                        f"preferCoordinatesForWfsT11='false' "
                        f"restrictToRequestBBOX='1' "
                        f"srsname='EPSG:4326' "
                        f"typename='ms:quilombolas_{estado}' "
                        f"url='{url_estado}' "
                        f"version='auto'"
                    )
                    
                    wfs_layer = QgsVectorLayer(wfs_uri, f"quilombolas_{estado}", "WFS")
                    
                    if wfs_layer.isValid() and wfs_layer.featureCount() > 0:
                        if crs_referencia is None:
                            crs_referencia = wfs_layer.crs()
                            fields_referencia = wfs_layer.fields()
                        
                        for feat in wfs_layer.getFeatures():
                            todas_features.append(feat)
                        
                        estados_sucesso.append(estado.upper())
                    else:
                        # Tentar GeoJSON alternativo
                        try:
                            import urllib.request
                            import json
                            
                            url_geojson = f"{url_estado}&service=WFS&version=1.0.0&request=GetFeature&outputFormat=application/json"
                            req = urllib.request.Request(url_geojson, headers={'User-Agent': 'QGIS-FlorestaMais'})
                            with urllib.request.urlopen(req, timeout=60) as response:
                                data = json.loads(response.read().decode('utf-8'))
                                
                                if 'features' in data and len(data['features']) > 0:
                                    temp_layer = QgsVectorLayer(json.dumps(data), f"quilombolas_{estado}", "ogr")
                                    if temp_layer.isValid():
                                        if crs_referencia is None:
                                            crs_referencia = temp_layer.crs()
                                            fields_referencia = temp_layer.fields()
                                        
                                        for feat in temp_layer.getFeatures():
                                            todas_features.append(feat)
                                        
                                        estados_sucesso.append(estado.upper())
                        except:
                            pass
                except Exception as e:
                    print(f"[Quilombolas Silent] Erro {estado.upper()}: {str(e)}")
            
            if not todas_features:
                return False
            
            # Criar camada unificada
            if crs_referencia is None:
                crs_referencia = QgsCoordinateReferenceSystem("EPSG:4674")
            
            unified_layer = QgsVectorLayer(
                f"MultiPolygon?crs={crs_referencia.authid()}",
                "quilombolas_unified",
                "memory"
            )
            
            provider = unified_layer.dataProvider()
            if fields_referencia:
                provider.addAttributes(fields_referencia)
                unified_layer.updateFields()
            
            provider.addFeatures(todas_features)
            
            # Salvar no GeoPackage
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = layer_key
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                unified_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] == QgsVectorFileWriter.NoError:
                print(f"[Quilombolas] Salvo {len(todas_features)} feições de {len(estados_sucesso)} estados")
                return True
            else:
                print(f"[Quilombolas] Erro ao salvar: {error[1]}")
                return False
                
        except Exception as e:
            print(f"[Quilombolas Silent] Erro geral: {str(e)}")
            return False
    
    def _download_car_amazonia_silent(self, layer_key, gpkg_path):
        """
        Versão silenciosa do download CAR (sem confirmação).
        Retorna True se sucesso, False se falha.
        """
        import ssl
        import urllib.request
        import urllib.parse
        import urllib.error
        import shutil
        import time
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        url_base = layer_info.get('url_wfs', 'https://geoserver.car.gov.br/geoserver/sicar/wfs')
        estados = layer_info.get('estados_amzl', ['AC', 'AM', 'AP', 'MA', 'MT', 'PA', 'RO', 'RR', 'TO'])
        
        # Criar pasta temporária única
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        pasta_temp = os.path.join(tempfile.gettempdir(), f"car_download_{timestamp}")
        os.makedirs(pasta_temp, exist_ok=True)
        
        # Criar contexto SSL compatível com servidor CAR
        ssl_context = self._create_ssl_context_car()
        
        todas_features = []
        estados_ok = []
        estados_erro = []
        
        total_estados = len(estados)
        # Estimativa: ~1.4 milhões de registros / 10k por lote = ~140 lotes total
        total_lotes_estimado = 140
        lotes_baixados = 0
        
        try:
            self._update_status(f"CAR: iniciando download de {total_estados} estados (~{total_lotes_estimado} lotes)...")
            QApplication.processEvents()
            
            for idx, estado in enumerate(estados):
                self._update_status(f"CAR [{idx+1}/{total_estados}]: baixando {estado}...")
                QApplication.processEvents()
                
                try:
                    nome_camada = f"sicar:sicar_imoveis_{estado.lower()}"
                    features_estado = self._download_car_estado(
                        url_base, nome_camada, estado, pasta_temp, ssl_context
                    )
                    if features_estado:
                        todas_features.extend(features_estado)
                        estados_ok.append(estado)
                        lotes_baixados += len(features_estado)
                        # Progresso baseado em lotes baixados (mais preciso)
                        progresso_pct = min(85, int((lotes_baixados / total_lotes_estimado) * 85))
                        self.progress_bar.setValue(progresso_pct)
                        self._update_status(f"CAR: {estado} OK - {len(features_estado)} lotes ({lotes_baixados} total)")
                    else:
                        estados_erro.append(f"{estado} (sem dados)")
                        self._update_status(f"CAR: {estado} - nenhum dado retornado")
                except Exception as e:
                    erro_msg = str(e)[:50]
                    estados_erro.append(f"{estado} ({erro_msg})")
                    self._update_status(f"CAR: ERRO em {estado} - {erro_msg}")
                
                QApplication.processEvents()
                # Pequena pausa entre estados
                time.sleep(0.5)
            
            self.progress_bar.setValue(90)
            
            if todas_features:
                self._update_status(f"CAR: consolidando {len(todas_features)} arquivos...")
                QApplication.processEvents()
                
                self._consolidar_car(todas_features, gpkg_path, layer_key)
                
                self.progress_bar.setValue(100)
                self._update_status(f"CAR concluído: {len(estados_ok)} estados OK, {len(estados_erro)} com erro")
                QApplication.processEvents()
                
                return True
            else:
                erro_detalhes = "; ".join(estados_erro) if estados_erro else "desconhecido"
                self._update_status(f"CAR: FALHA - nenhum dado baixado. Erros: {erro_detalhes[:100]}")
                QApplication.processEvents()
                return False
                
        except Exception as e:
            self._update_status(f"CAR: ERRO GERAL - {str(e)[:60]}")
            QApplication.processEvents()
            return False
            
        finally:
            # Limpar pasta temporária
            try:
                shutil.rmtree(pasta_temp, ignore_errors=True)
            except:
                pass
    
    def _browse_gpkg(self):
        """Abre diálogo para selecionar GeoPackage existente (legado)."""
        self._browse_and_load_gpkg()
    
    def _browse_raster(self):
        """Abre diálogo para selecionar imagem raster."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar Imagem Raster",
            "",
            "Imagens (*.tif *.tiff *.img *.jp2);;Todos os arquivos (*.*)"
        )
        if path:
            self.raster_path.setText(path)
            # Carregar no mapa
            layer = QgsRasterLayer(path, os.path.basename(path))
            if layer.isValid():
                self._add_layer_to_canvas(layer)
                self._update_status(f"Raster carregado: {os.path.basename(path)}")
    
    def _load_local_layer(self):
        """Carrega camada local (shapefile/geopackage)."""
        row = self.layers_table.currentRow()
        if row < 0:
            QMessageBox.warning(
                self, 
                "Aviso", 
                "Selecione uma camada na tabela primeiro."
            )
            return
        
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar Camada",
            "",
            "Vetores (*.shp *.gpkg *.geojson);;Todos os arquivos (*.*)"
        )
        if path:
            layer_name = self.layers_table.item(row, 1).text()
            layer = QgsVectorLayer(path, layer_name, "ogr")
            
            if layer.isValid():
                key = self.layers_table.item(row, 1).data(Qt.UserRole)
                self.loaded_layers[key] = layer
                self._add_layer_to_canvas(layer)
                
                # Atualizar status na tabela
                self.layers_table.item(row, 3).setText("OK")
                self._update_status(f"Camada '{layer_name}' carregada com sucesso")
            else:
                QMessageBox.critical(
                    self,
                    "Erro",
                    f"Não foi possível carregar a camada:\n{path}"
                )
    
    def _load_wfs_layer(self):
        """Carrega camada de serviço WFS."""
        row = self.layers_table.currentRow()
        if row < 0:
            QMessageBox.warning(
                self,
                "Aviso",
                "Selecione uma camada na tabela primeiro."
            )
            return
        
        key = self.layers_table.item(row, 1).data(Qt.UserRole)
        layer_info = self.config.get('camadas_referencia', {}).get(key, {})
        
        if layer_info.get('tipo') != 'wfs':
            QMessageBox.warning(
                self,
                "Aviso",
                "Esta camada não possui serviço WFS configurado.\n"
                "Use 'Carregar Local' para esta camada."
            )
            return
        
        url_wfs = layer_info.get('url_wfs', '')
        layer_name_wfs = layer_info.get('layer_name', '')
        
        if not url_wfs or not layer_name_wfs:
            QMessageBox.warning(
                self,
                "Aviso",
                "Configuração WFS incompleta para esta camada."
            )
            return
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminado
        self._update_status(f"Baixando {layer_info.get('nome', key)}...")
        
        # URI do WFS
        uri = f"{url_wfs}?service=WFS&version=2.0.0&request=GetFeature&typeName={layer_name_wfs}"
        
        layer_name = layer_info.get('nome', key)
        layer = QgsVectorLayer(uri, layer_name, "WFS")
        
        self.progress_bar.setVisible(False)
        
        if layer.isValid():
            self.loaded_layers[key] = layer
            self._add_layer_to_canvas(layer)
            self.layers_table.item(row, 3).setText("OK")
            self._update_status(f"Camada '{layer_name}' baixada com sucesso")
        else:
            QMessageBox.critical(
                self,
                "Erro",
                f"Não foi possível baixar a camada WFS.\n"
                f"Verifique sua conexão ou tente carregar localmente."
            )
            self._update_status("Erro ao baixar camada WFS")
    
    # =========================================================================
    #                      FUNCOES PLANET QUADS
    # =========================================================================
    
    def _browse_download_folder(self):
        """Seleciona pasta para downloads."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Selecionar Pasta de Download",
            ""
        )
        if folder:
            self.download_folder.setText(folder)
    
    def _select_local_layer(self, layer_key):
        """Seleciona arquivo local e importa para o GeoPackage."""
        gpkg_path = self.gpkg_path.text()
        if not gpkg_path:
            QMessageBox.warning(self, "Aviso", "Selecione um GeoPackage primeiro")
            return
        
        layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
        layer_name = layer_info.get('nome', layer_key)
        
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            f"Selecionar arquivo para '{layer_name}'",
            "",
            "Vetores (*.shp *.gpkg);;Shapefile (*.shp);;GeoPackage (*.gpkg);;GeoJSON (*.geojson *.json);;Todos (*.*)"
        )
        
        if not filepath:
            return
        
        # Se for GeoPackage, mostrar diálogo para escolher a camada
        source_uri = filepath
        if filepath.lower().endswith('.gpkg'):
            selected_layer = self._select_layer_from_gpkg(filepath, layer_name)
            if selected_layer is None:
                return  # Usuário cancelou
            source_uri = f"{filepath}|layername={selected_layer}"
        
        # Mostrar progresso
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(20)
        self._update_status(f"Carregando {layer_name}...")
        QApplication.processEvents()
        
        try:
            # Carregar arquivo (com layername se for gpkg)
            layer = QgsVectorLayer(source_uri, layer_name, "ogr")
            
            if not layer.isValid():
                raise Exception(f"Arquivo/camada inválido: {source_uri}")
            
            self.progress_bar.setValue(50)
            self._update_status(f"Salvando {layer_name} no GeoPackage...")
            QApplication.processEvents()
            
            # Salvar no GeoPackage
            # Salvar no GeoPackage (nome padronizado)
            table_name = self._get_table_name(layer_key)
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = table_name
            options.fileEncoding = "UTF-8"
            
            if not os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] != QgsVectorFileWriter.NoError:
                raise Exception(f"Erro ao salvar: {error[1]}")
            
            self.progress_bar.setValue(90)
            
            # Atualizar tabela e mostrar no mapa
            self._populate_layers_from_config()
            
            # Carregar no mapa
            self._load_layer_from_gpkg(layer_key)
            
            self.progress_bar.setValue(100)
            self._update_status(f"{layer_name} importado com sucesso ({layer.featureCount()} features)")
            
            # Se foi a camada de imóveis, verificar e habilitar botão RVN
            if 'imoveis' in layer_key.lower() or 'imovel' in layer_key.lower():
                if self._verificar_imoveis_carregados() and self.planet_logged_in:
                    self.btn_buscar_quads_imoveis.setEnabled(True)
            
            QMessageBox.information(self, "Sucesso", 
                f"Camada '{layer_name}' importada!\n\nFeatures: {layer.featureCount()}")
            
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao importar:\n{str(e)}")
            self._update_status(f"Erro ao importar {layer_name}")
        
        finally:
            self.progress_bar.setVisible(False)
    
    def _select_layer_from_gpkg(self, gpkg_filepath, target_layer_name):
        """
        Mostra diálogo para selecionar uma camada de um GeoPackage.
        
        Args:
            gpkg_filepath: Caminho do arquivo GeoPackage
            target_layer_name: Nome da camada de destino (para exibição)
            
        Returns:
            Nome da camada selecionada ou None se cancelou
        """
        import sqlite3
        
        # Listar camadas do GeoPackage
        try:
            conn = sqlite3.connect(gpkg_filepath)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT table_name, column_name 
                FROM gpkg_geometry_columns
            """)
            layers = cursor.fetchall()
            conn.close()
            
            if not layers:
                QMessageBox.warning(
                    self, 
                    "Aviso", 
                    "Nenhuma camada vetorial encontrada neste GeoPackage."
                )
                return None
                
            # Se só tem uma camada, usar diretamente
            if len(layers) == 1:
                return layers[0][0]
                
            # Criar diálogo para escolher
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Selecionar Camada para '{target_layer_name}'")
            dialog.setMinimumWidth(400)
            
            layout = QVBoxLayout(dialog)
            
            # Info
            info_label = QLabel(f"O GeoPackage contém {len(layers)} camadas.\nSelecione qual deseja importar:")
            info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
            layout.addWidget(info_label)
            
            # Lista de camadas
            layer_list = QListWidget()
            layer_list.setSelectionMode(QListWidget.SingleSelection)
            
            for layer_name, _ in layers:
                # Tentar obter contagem de features
                try:
                    conn = sqlite3.connect(gpkg_filepath)
                    cursor = conn.cursor()
                    cursor.execute(f'SELECT COUNT(*) FROM "{layer_name}"')
                    count = cursor.fetchone()[0]
                    conn.close()
                    item_text = f"{layer_name} ({count:,} features)"
                except:
                    item_text = layer_name
                    
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, layer_name)
                layer_list.addItem(item)
                
            layer_list.setCurrentRow(0)
            layout.addWidget(layer_list)
            
            # Botões
            btn_layout = QHBoxLayout()
            btn_layout.addStretch()
            
            btn_cancel = QPushButton("Cancelar")
            btn_cancel.clicked.connect(dialog.reject)
            btn_layout.addWidget(btn_cancel)
            
            btn_select = QPushButton("Selecionar")
            btn_select.setStyleSheet("""
                QPushButton {
                    background-color: #27ae60;
                    color: white;
                    font-weight: bold;
                    padding: 6px 16px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background-color: #219a52;
                }
            """)
            btn_select.clicked.connect(dialog.accept)
            btn_layout.addWidget(btn_select)
            
            layout.addLayout(btn_layout)
            
            # Double-click para selecionar
            layer_list.itemDoubleClicked.connect(dialog.accept)
            
            if dialog.exec_() == QDialog.Accepted:
                selected_item = layer_list.currentItem()
                if selected_item:
                    return selected_item.data(Qt.UserRole)
            
            return None
            
        except Exception as e:
            QMessageBox.critical(
                self, 
                "Erro", 
                f"Erro ao ler GeoPackage:\n{str(e)}"
            )
            return None
    
    def _search_planet_quads(self, search_type='mapa'):
        """Busca quads Planet na area visivel do mapa ou na área dos imóveis.
        
        Args:
            search_type: 'mapa' para área visível, 'imoveis' para área dos imóveis
        """
        if not PLANET_AVAILABLE or not planet_client.is_logged_in:
            QMessageBox.warning(
                self, 
                "Aviso", 
                "Faça login no Planet primeiro (botão 'Conectar Planet')"
            )
            return
        
        if not self.current_planet_mosaic:
            QMessageBox.warning(
                self,
                "Aviso",
                "Selecione um mosaico Planet primeiro no diálogo de conexão"
            )
            return
        
        # Obter bbox baseado no tipo de busca
        bbox = None
        
        if search_type == 'imoveis':
            # Buscar pela área dos imóveis
            bbox = self._get_imoveis_extent()
            if not bbox:
                QMessageBox.warning(
                    self,
                    "Aviso",
                    "Carregue a camada 'Imóveis a Analisar' primeiro.\n\n"
                    "Use o botão 'Local' na tabela de camadas para selecionar o arquivo."
                )
                return
            self._update_status("Buscando quads na área dos imóveis...")
        else:
            # Buscar pela área do mapa
            bbox = self._get_map_extent_wgs84()
            self._update_status("Buscando quads na área do mapa...")
        
        QApplication.processEvents()
        
        # Verificar se extent é válido (não muito grande)
        if bbox[2] - bbox[0] > 10 or bbox[3] - bbox[1] > 10:
            QMessageBox.warning(
                self,
                "Aviso",
                "Área muito grande para buscar quads.\n\n"
                "Dê zoom no mapa ou use imóveis em área menor."
            )
            return
        
        # Buscar quads
        mosaic_id = self.current_planet_mosaic.get('id')
        self.planet_quads = planet_client.get_quads(mosaic_id, bbox)
        
        # Atualizar lista
        self.quads_list.clear()
        
        if self.planet_quads:
            for quad in self.planet_quads:
                quad_id = quad.get('id', 'Unknown')
                coverage = quad.get('percent_covered', 0)
                item = QListWidgetItem(f"{quad_id} ({coverage:.0f}%)")
                item.setData(Qt.UserRole, quad)
                self.quads_list.addItem(item)
            
            self.quads_info.setText(f"{len(self.planet_quads)} quads encontrados")
            self._update_status(f"Encontrados {len(self.planet_quads)} quads")
            
            # Desenhar retângulos dos quads no mapa
            self._draw_quads_on_map()
        else:
            self.quads_info.setText("Nenhum quad encontrado nesta área")
            self._clear_quads_from_map()
    
    def _get_imoveis_extent(self):
        """Obtém o extent da camada de imóveis a analisar."""
        # Procurar camada de imóveis no GeoPackage ou no mapa
        gpkg_path = self.gpkg_path.text()
        
        if gpkg_path and os.path.exists(gpkg_path):
            # Tentar carregar do GeoPackage
            layer_names = self._get_gpkg_layer_names(gpkg_path)
            
            # Procurar por variações do nome da camada de imóveis
            imoveis_variations = ['imoveisanalisar', 'imoveis_analisar', 'imoveis', 'imoveis_a_analisar']
            found_name = None
            
            for name in layer_names:
                normalized = name.lower().replace(' ', '').replace('_', '').replace('-', '')
                for variation in imoveis_variations:
                    if variation in normalized or normalized in variation:
                        found_name = name
                        break
                if found_name:
                    break
            
            if found_name:
                uri = f"{gpkg_path}|layername={found_name}"
                layer = QgsVectorLayer(uri, "temp_imoveis", "ogr")
                
                if layer.isValid() and layer.featureCount() > 0:
                    extent = layer.extent()
                    return (extent.xMinimum(), extent.yMinimum(), 
                           extent.xMaximum(), extent.yMaximum())
        
        # Procurar nas camadas do mapa
        for layer in self.map_canvas.layers():
            layer_name = layer.name().lower().replace(' ', '').replace('_', '')
            if 'imoveis' in layer_name or 'imovel' in layer_name:
                extent = layer.extent()
                return (extent.xMinimum(), extent.yMinimum(), 
                       extent.xMaximum(), extent.yMaximum())
        
        return None
    
    def _draw_quads_on_map(self):
        """Desenha os retângulos dos quads Planet no mapa."""
        # Remover camada anterior de quads se existir
        self._clear_quads_from_map()
        
        if not self.planet_quads:
            return
        
        # Criar camada de memória para os quads
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        self.quads_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Planet Quads", "memory")
        
        provider = self.quads_layer.dataProvider()
        
        # Adicionar campos
        provider.addAttributes([
            QgsField("quad_id", typeName="String"),
            QgsField("coverage", typeName="Real")
        ])
        self.quads_layer.updateFields()
        
        # Adicionar features para cada quad
        features = []
        for quad in self.planet_quads:
            quad_id = quad.get('id', '')
            coverage = quad.get('percent_covered', 0)
            bbox = quad.get('bbox', [])
            
            if len(bbox) == 4:
                # bbox = [lon_min, lat_min, lon_max, lat_max]
                feat = QgsFeature()
                points = [
                    QgsPointXY(bbox[0], bbox[1]),  # SW
                    QgsPointXY(bbox[2], bbox[1]),  # SE
                    QgsPointXY(bbox[2], bbox[3]),  # NE
                    QgsPointXY(bbox[0], bbox[3]),  # NW
                    QgsPointXY(bbox[0], bbox[1])   # Fechar polígono
                ]
                geom = QgsGeometry.fromPolygonXY([points])
                feat.setGeometry(geom)
                feat.setAttributes([quad_id, coverage])
                features.append(feat)
        
        provider.addFeatures(features)
        
        # Estilo: apenas contorno azul, SEM preenchimento
        symbol = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',  # Totalmente transparente (sem preenchimento)
            'outline_color': '30,100,255,255',  # Azul sólido
            'outline_width': '0.3'  # Linha fina
        })
        self.quads_layer.renderer().setSymbol(symbol)
        
        # Adicionar ao mapa
        current_layers = list(self.map_canvas.layers())
        current_layers.insert(0, self.quads_layer)  # No topo
        self.map_canvas.setLayers(current_layers)
        self.map_canvas.refresh()
        
        self._update_status(f"Desenhados {len(features)} quads no mapa")
    
    def _clear_quads_from_map(self):
        """Remove a camada de quads do mapa."""
        if hasattr(self, 'quads_layer') and self.quads_layer:
            try:
                current_layers = list(self.map_canvas.layers())
                current_layers = [l for l in current_layers if l.id() != self.quads_layer.id()]
                self.map_canvas.setLayers(current_layers)
                self.quads_layer = None
                self.map_canvas.refresh()
            except:
                pass
        
        # Também remover camada de seleção
        self._clear_selected_quads_layer()
        self._update_status("Nenhum quad encontrado")
    
    def _clear_selected_quads_layer(self):
        """Remove a camada de quads selecionados do mapa."""
        if hasattr(self, 'selected_quads_layer') and self.selected_quads_layer:
            try:
                current_layers = list(self.map_canvas.layers())
                current_layers = [l for l in current_layers if l.id() != self.selected_quads_layer.id()]
                self.map_canvas.setLayers(current_layers)
                self.selected_quads_layer = None
            except:
                pass
    
    def _highlight_selected_quads(self):
        """Destaca os quads selecionados no mapa com cor diferente."""
        # Remover camada de seleção anterior
        self._clear_selected_quads_layer()
        
        # Obter itens selecionados
        selected_items = self.quads_list.selectedItems()
        
        if not selected_items:
            self.map_canvas.refresh()
            return
        
        # Criar nova camada de memória para os quads selecionados
        self.selected_quads_layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "Quads Selecionados", "memory")
        provider = self.selected_quads_layer.dataProvider()
        
        # Adicionar campo de ID
        provider.addAttributes([QgsField("quad_id", typeName="String")])
        self.selected_quads_layer.updateFields()
        
        # Adicionar features para cada quad selecionado
        features = []
        for item in selected_items:
            quad = item.data(Qt.UserRole)
            if not quad:
                continue
            
            quad_id = quad.get('id', '')
            bbox = quad.get('bbox', [])
            
            if len(bbox) == 4:
                feat = QgsFeature()
                points = [
                    QgsPointXY(bbox[0], bbox[1]),
                    QgsPointXY(bbox[2], bbox[1]),
                    QgsPointXY(bbox[2], bbox[3]),
                    QgsPointXY(bbox[0], bbox[3]),
                    QgsPointXY(bbox[0], bbox[1])
                ]
                geom = QgsGeometry.fromPolygonXY([points])
                feat.setGeometry(geom)
                feat.setAttributes([quad_id])
                features.append(feat)
        
        if not features:
            return
        
        provider.addFeatures(features)
        
        # Estilo: apenas contorno laranja destacado, SEM preenchimento
        symbol = QgsFillSymbol.createSimple({
            'color': '0,0,0,0',  # Totalmente transparente (sem preenchimento)
            'outline_color': '255,140,0,255',  # Laranja escuro
            'outline_width': '1.0'  # Linha mais grossa
        })
        self.selected_quads_layer.renderer().setSymbol(symbol)
        
        # Adicionar ao mapa (no topo, acima dos quads normais)
        current_layers = list(self.map_canvas.layers())
        current_layers.insert(0, self.selected_quads_layer)
        self.map_canvas.setLayers(current_layers)
        self.map_canvas.refresh()
    
    def _update_quads_toggle_style(self, is_visible):
        """Atualiza o estilo do botão toggle de quads."""
        if is_visible:
            self.btn_toggle_quads_view.setStyleSheet("""
                QPushButton {
                    background-color: #27ae60;
                    border: 1px solid #1e8449;
                    border-radius: 3px;
                    color: white;
                    font-size: 10px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #2ecc71;
                }
            """)
        else:
            self.btn_toggle_quads_view.setStyleSheet("""
                QPushButton {
                    background-color: #95a5a6;
                    border: 1px solid #7f8c8d;
                    border-radius: 3px;
                    color: #bdc3c7;
                    font-size: 10px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #7f8c8d;
                }
            """)
    
    def _toggle_quads_visibility(self):
        """Alterna a visibilidade da camada de quads no mapa."""
        is_visible = self.btn_toggle_quads_view.isChecked()
        self._update_quads_toggle_style(is_visible)
        
        try:
            current_layers = list(self.map_canvas.layers())
            
            # Remover camada de quads (se existir)
            if hasattr(self, 'quads_layer') and self.quads_layer:
                current_layers = [l for l in current_layers if l.id() != self.quads_layer.id()]
            
            # Remover camada de quads selecionados (se existir)
            if hasattr(self, 'selected_quads_layer') and self.selected_quads_layer:
                current_layers = [l for l in current_layers if l.id() != self.selected_quads_layer.id()]
            
            if is_visible:
                # Mostrar quads - inserir NO TOPO da lista
                if hasattr(self, 'quads_layer') and self.quads_layer:
                    current_layers.insert(0, self.quads_layer)
                # Camada de seleção sempre acima (índice 0)
                if hasattr(self, 'selected_quads_layer') and self.selected_quads_layer:
                    current_layers.insert(0, self.selected_quads_layer)
            
            self.map_canvas.setLayers(current_layers)
            self.map_canvas.refresh()
        except Exception as e:
            print(f"Erro ao alternar visibilidade dos quads: {e}")
    
    def _update_quad_select_tool_style(self, is_active):
        """Atualiza o estilo do botão de seleção por retângulo."""
        if is_active:
            self.btn_selecionar_quads_mapa.setStyleSheet("""
                QPushButton {
                    background-color: #3498db;
                    border: 1px solid #2980b9;
                    border-radius: 3px;
                    color: white;
                    font-size: 12px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #5dade2;
                }
            """)
        else:
            self.btn_selecionar_quads_mapa.setStyleSheet("""
                QPushButton {
                    background-color: #95a5a6;
                    border: 1px solid #7f8c8d;
                    border-radius: 3px;
                    color: #bdc3c7;
                    font-size: 12px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #7f8c8d;
                }
            """)
    
    def _toggle_quad_selection_tool(self):
        """Ativa/desativa a ferramenta de seleção de quads por retângulo no mapa."""
        is_active = self.btn_selecionar_quads_mapa.isChecked()
        self._update_quad_select_tool_style(is_active)
        
        if is_active:
            # Verificar se há quads para selecionar
            if not hasattr(self, 'planet_quads') or not self.planet_quads:
                QMessageBox.warning(self, "Aviso", 
                    "Primeiro busque os quads (botão 'Buscar Quads dos Imóveis')")
                self.btn_selecionar_quads_mapa.setChecked(False)
                self._update_quad_select_tool_style(False)
                return
            
            # Criar e ativar a ferramenta de seleção
            self.quad_selection_tool = QuadSelectionTool(self.map_canvas, self._on_quads_selected_by_rect)
            self.map_canvas.setMapTool(self.quad_selection_tool)
            self._update_status("🔲 Desenhe um retângulo no mapa para selecionar quads")
        else:
            # Voltar para a ferramenta de pan
            self.map_canvas.setMapTool(self.map_tool_pan)
            self._update_status("")
    
    def _on_quads_selected_by_rect(self, rect):
        """Callback quando um retângulo de seleção é desenhado no mapa."""
        if not hasattr(self, 'planet_quads') or not self.planet_quads:
            return
        
        # Criar geometria do retângulo de seleção
        select_geom = QgsGeometry.fromRect(rect)
        
        # Limpar seleção atual na lista
        self.quads_list.clearSelection()
        
        # Encontrar quads que intersectam o retângulo
        selected_count = 0
        
        for i in range(self.quads_list.count()):
            item = self.quads_list.item(i)
            quad = item.data(Qt.UserRole)
            
            if not quad:
                continue
            
            bbox = quad.get('bbox', [])
            if len(bbox) != 4:
                continue
            
            # Criar geometria do quad
            quad_rect = QgsRectangle(bbox[0], bbox[1], bbox[2], bbox[3])
            quad_geom = QgsGeometry.fromRect(quad_rect)
            
            # Verificar interseção
            if select_geom.intersects(quad_geom):
                item.setSelected(True)
                selected_count += 1
        
        # Feedback para o usuário
        if selected_count > 0:
            self._update_status(f"✓ {selected_count} quad(s) selecionado(s)")
        else:
            self._update_status("Nenhum quad na área selecionada")
        
        # Desativar a ferramenta e voltar para pan
        self.btn_selecionar_quads_mapa.setChecked(False)
        self._update_quad_select_tool_style(False)
        self.map_canvas.setMapTool(self.map_tool_pan)
    
    def _download_selected_quads(self):
        """Baixa quads selecionados."""
        if not PLANET_AVAILABLE or not planet_client.is_logged_in:
            QMessageBox.warning(self, "Aviso", "Faca login no Planet primeiro")
            return
        
        selected_items = self.quads_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Aviso", "Selecione pelo menos um quad")
            return
        
        output_folder = self.download_folder.text()
        if not output_folder:
            QMessageBox.warning(self, "Aviso", "Selecione a pasta de destino")
            return
        
        # Criar subpasta para o mosaico
        mosaic_name = self.current_planet_mosaic.get('name', 'planet')
        mosaic_folder = os.path.join(output_folder, mosaic_name)
        os.makedirs(mosaic_folder, exist_ok=True)
        
        # Configurar progresso
        self.download_progress.setVisible(True)
        self.download_progress.setRange(0, len(selected_items))
        self.download_progress.setValue(0)
        
        downloaded = 0
        errors = []
        
        for i, item in enumerate(selected_items):
            quad = item.data(Qt.UserRole)
            quad_id = quad.get('id', f'quad_{i}')
            output_path = os.path.join(mosaic_folder, f"{quad_id}.tif")
            
            self._update_status(f"Baixando {quad_id}...")
            
            success, result = planet_client.download_quad(quad, output_path)
            
            if success:
                downloaded += 1
                # Carregar no mapa
                layer = QgsRasterLayer(result, quad_id)
                if layer.isValid():
                    self._add_layer_to_canvas(layer)
            else:
                errors.append(f"{quad_id}: {result}")
            
            self.download_progress.setValue(i + 1)
        
        self.download_progress.setVisible(False)
        
        if errors:
            QMessageBox.warning(
                self,
                "Download",
                f"Baixados {downloaded} de {len(selected_items)} quads.\n\n"
                f"Erros:\n" + "\n".join(errors[:5])
            )
        else:
            QMessageBox.information(
                self,
                "Download",
                f"Todos os {downloaded} quads baixados com sucesso!\n"
                f"Pasta: {mosaic_folder}"
            )
        
        self._update_status(f"Download concluido: {downloaded} quads")
    
    # ==========================================================================
    # FUNÇÕES DE MAPEAMENTO DE VEGETAÇÃO NATIVA (RVN)
    # ==========================================================================
    
    def _verificar_imoveis_carregados(self):
        """Verifica se a camada de imóveis está carregada e atualiza o status."""
        imoveis_extent = self._get_imoveis_extent()
        
        if imoveis_extent:
            self.imoveis_status_rvn.setText("✓ Imóveis carregados")
            self.imoveis_status_rvn.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
            return True
        else:
            self.imoveis_status_rvn.setText("⚠ Carregue 'Imóveis a Analisar'")
            self.imoveis_status_rvn.setStyleSheet("color: #e67e22; font-size: 10px; font-weight: bold;")
            return False
    
    def _verificar_imoveis_para_rvn(self):
        """Verifica se imóveis existem no GeoPackage e atualiza botões RVN."""
        gpkg_path = self.gpkg_path.text()
        
        if not gpkg_path or not os.path.exists(gpkg_path):
            return
        
        # Verificar se tem camada de imóveis
        layer_names = self._get_gpkg_layer_names(gpkg_path)
        
        has_imoveis = False
        for name in layer_names:
            normalized = name.lower().replace(' ', '').replace('_', '').replace('-', '')
            if 'imoveis' in normalized or 'imovel' in normalized:
                has_imoveis = True
                break
        
        if has_imoveis:
            self.imoveis_status_rvn.setText("✓ Imóveis carregados")
            self.imoveis_status_rvn.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
            
            # Habilitar botão de buscar quads se Planet estiver conectado
            if hasattr(self, 'planet_logged_in') and self.planet_logged_in:
                self.btn_buscar_quads_imoveis.setEnabled(True)
        else:
            self.imoveis_status_rvn.setText("⚠ Carregue 'Imóveis a Analisar'")
            self.imoveis_status_rvn.setStyleSheet("color: #e67e22; font-size: 10px; font-weight: bold;")
    
    def _buscar_quads_para_imoveis(self):
        """Busca quads Planet que sobrepõem os imóveis a analisar.
        
        Usa shapefile LOCAL de quads para identificação rápida.
        Muito mais rápido que buscar via API!
        """
        if not PLANET_AVAILABLE or not planet_client.is_logged_in:
            QMessageBox.warning(self, "Aviso", "Conecte ao Planet primeiro")
            return
        
        if not self.current_planet_mosaic:
            QMessageBox.warning(self, "Aviso", "Selecione um mosaico Planet primeiro (botão 'Conectar Planet')")
            return
        
        # Verificar e carregar imóveis
        imoveis_layer = self._get_imoveis_layer()
        if not imoveis_layer or imoveis_layer.featureCount() == 0:
            QMessageBox.warning(self, "Aviso", 
                "Carregue a camada 'Imóveis a Analisar' primeiro.\n\n"
                "Use o botão 'Local' na tabela de camadas.")
            return
        
        self.rvn_status.setText("Carregando shapefile de quads...")
        self.rvn_progress.setVisible(True)
        self.rvn_progress.setValue(5)
        QApplication.processEvents()
        
        # 1. Carregar shapefile LOCAL de quads (do config.json)
        plugin_dir = os.path.dirname(os.path.dirname(__file__))
        config_path = os.path.join(plugin_dir, 'config', 'config.json')
        
        quads_shp_relative = "quads/quads.shp"  # Padrão
        quads_id_field_name = "quad_id"  # Padrão
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                rvn_config = config.get('rvn_config', {})
                quads_shp_relative = rvn_config.get('quads_shapefile', quads_shp_relative)
                quads_id_field_name = rvn_config.get('quads_id_field', quads_id_field_name)
        except Exception as e:
            print(f"Aviso: não foi possível ler config.json: {e}")
        
        quads_shp_path = os.path.join(plugin_dir, quads_shp_relative)
        
        if not os.path.exists(quads_shp_path):
            QMessageBox.critical(self, "Erro", 
                f"Shapefile de quads não encontrado!\n\n{quads_shp_path}\n\n"
                "O shapefile deve estar em 'Plugin_FMais/quads/quads.shp'")
            self.rvn_progress.setVisible(False)
            return
        
        quads_layer = QgsVectorLayer(quads_shp_path, "quads_temp", "ogr")
        if not quads_layer.isValid():
            QMessageBox.critical(self, "Erro", "Não foi possível carregar o shapefile de quads")
            self.rvn_progress.setVisible(False)
            return
        
        print(f"Shapefile de quads carregado: {quads_layer.featureCount()} quads")
        
        self.rvn_status.setText("Preparando índices espaciais...")
        self.rvn_progress.setValue(15)
        QApplication.processEvents()
        
        # 2. Preparar geometrias dos imóveis (converter para WGS84 se necessário)
        imoveis_crs = imoveis_layer.crs()
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        need_transform = imoveis_crs.authid() != "EPSG:4326"
        
        if need_transform:
            transform_to_wgs84 = QgsCoordinateTransform(imoveis_crs, crs_4326, QgsProject.instance())
        
        # Criar índice espacial dos imóveis
        imoveis_index = QgsSpatialIndex()
        imoveis_geoms = {}
        
        for feat in imoveis_layer.getFeatures():
            geom = QgsGeometry(feat.geometry())
            if need_transform:
                geom.transform(transform_to_wgs84)
            fid = feat.id()
            imoveis_index.addFeature(feat)
            imoveis_geoms[fid] = geom
        
        print(f"Imóveis indexados: {len(imoveis_geoms)}")
        
        self.rvn_status.setText(f"Identificando quads sobrepostos ({len(imoveis_geoms)} imóveis)...")
        self.rvn_progress.setValue(30)
        QApplication.processEvents()
        
        # 3. Buscar quads que intersectam imóveis
        quads_com_imoveis = []
        total_quads = quads_layer.featureCount()
        
        # Identificar o índice do campo quad_id (usando nome do config)
        quad_id_field_idx = quads_layer.fields().indexOf(quads_id_field_name)
        if quad_id_field_idx < 0:
            # Tentar variações do nome configurado
            for field_name in [quads_id_field_name.upper(), quads_id_field_name.lower(), 
                              'quad_id', 'QUAD_ID', 'quadid', 'id', 'ID', 'name', 'NAME']:
                quad_id_field_idx = quads_layer.fields().indexOf(field_name)
                if quad_id_field_idx >= 0:
                    print(f"Campo de ID encontrado: {field_name}")
                    break
        
        if quad_id_field_idx < 0:
            QMessageBox.critical(self, "Erro", 
                f"Campo '{quads_id_field_name}' não encontrado no shapefile de quads!\n\n"
                f"Campos disponíveis: {[f.name() for f in quads_layer.fields()]}")
            self.rvn_progress.setVisible(False)
            return
        
        # Rastrear quais imóveis têm quads sobrepostos
        imoveis_com_quads = set()
        
        # Iterar sobre todos os quads do shapefile
        for idx, quad_feat in enumerate(quads_layer.getFeatures()):
            # Atualizar progresso a cada 500 quads
            if idx % 500 == 0:
                progress = 30 + int((idx / total_quads) * 60)
                self.rvn_progress.setValue(progress)
                self.rvn_status.setText(f"Verificando quad {idx+1}/{total_quads}... ({len(quads_com_imoveis)} encontrados)")
                QApplication.processEvents()
            
            quad_geom = quad_feat.geometry()
            quad_rect = quad_geom.boundingBox()
            
            # Usar índice espacial para encontrar imóveis candidatos
            candidatos_ids = imoveis_index.intersects(quad_rect)
            
            # Verificar interseção real
            quad_adicionado = False
            for cand_id in candidatos_ids:
                if cand_id in imoveis_geoms and quad_geom.intersects(imoveis_geoms[cand_id]):
                    # Registrar que este imóvel tem quad
                    imoveis_com_quads.add(cand_id)
                    
                    # Se ainda não adicionou o quad, adiciona
                    if not quad_adicionado:
                        quad_id = quad_feat.attribute(quad_id_field_idx)
                        bbox = [quad_rect.xMinimum(), quad_rect.yMinimum(), 
                               quad_rect.xMaximum(), quad_rect.yMaximum()]
                        
                        quads_com_imoveis.append({
                            'id': str(quad_id),
                            'bbox': bbox,
                            'percent_covered': 100,  # Não temos essa info local
                            '_local': True  # Marcar que veio do shapefile local
                        })
                        quad_adicionado = True
        
        total_candidatos = total_quads
        self.rvn_progress.setValue(95)
        QApplication.processEvents()
        
        # Verificar imóveis fora da cobertura (fora da Amazônia Legal)
        imoveis_sem_quads = len(imoveis_geoms) - len(imoveis_com_quads)
        if imoveis_sem_quads > 0:
            print(f"⚠ {imoveis_sem_quads} imóveis estão fora da área de cobertura (ignorados)")
        
        print(f"Quads encontrados: {len(quads_com_imoveis)} de {total_quads} no shapefile")
        
        # 5. Atualizar lista com apenas os quads que têm imóveis
        self.planet_quads = quads_com_imoveis
        self.quads_list.clear()
        self.quads_processados = set()
        
        print(f"Quads filtrados: {len(quads_com_imoveis)} de {total_candidatos} candidatos")
        
        if self.planet_quads:
            for i, quad in enumerate(self.planet_quads):
                quad_id = quad.get('id', f'quad_{i}')
                
                item = QListWidgetItem(f"🔲 {quad_id}")
                item.setData(Qt.UserRole, quad)
                item.setData(Qt.UserRole + 1, 'pendente')  # Status
                self.quads_list.addItem(item)
            
            info_text = f"{len(self.planet_quads)} quads com imóveis"
            if imoveis_sem_quads > 0:
                info_text += f" ({imoveis_sem_quads} imóveis fora da área)"
            self.quads_info.setText(info_text)
            
            # Verificar requisitos e habilitar botões
            self._verificar_requisitos_processamento()
            
            # Selecionar primeiro quad
            self.quads_list.setCurrentRow(0)
            self._on_quad_selected(self.quads_list.item(0))
            
            # Desenhar quads no mapa
            self._draw_quads_on_map()
        else:
            self.quads_info.setText(f"Nenhum quad sobrepõe os imóveis (verificados: {total_candidatos})")
            self.btn_mapear_veg.setEnabled(False)
            self.btn_processar_todos.setEnabled(False)
            self._clear_quads_from_map()
        
        self.rvn_progress.setValue(100)
        self.rvn_progress.setVisible(False)
        self.rvn_status.setText("")
    
    def _on_quad_selected(self, item):
        """Chamado quando um quad é selecionado na lista."""
        if not item:
            return
        
        quad = item.data(Qt.UserRole)
        if not quad:
            return
        
        quad_id = quad.get('id', '')
        self.quad_atual_label.setText(quad_id)
        self.quad_atual_idx = self.quads_list.currentRow()
        
        # Não aplica zoom automático - usuário faz zoom manualmente se quiser
    
    def _mapear_vegetacao_quad_atual(self):
        """Processa os quads selecionados: download, classificação e salvamento."""
        # Obter todos os itens selecionados
        selected_items = self.quads_list.selectedItems()
        
        if not selected_items:
            QMessageBox.warning(self, "Aviso", "Selecione um ou mais quads na lista")
            return
        
        # Coletar quads selecionados
        quads_a_processar = []
        quads_ja_processados = []
        
        for item in selected_items:
            quad = item.data(Qt.UserRole)
            if quad:
                quad_id = quad.get('id', '')
                if quad_id in self.quads_processados:
                    quads_ja_processados.append(quad_id)
                else:
                    quads_a_processar.append(quad)
        
        # Se todos já foram processados, perguntar se quer reprocessar
        if not quads_a_processar and quads_ja_processados:
            reply = QMessageBox.question(self, "Confirmar",
                f"{len(quads_ja_processados)} quad(s) já foram processados. Processar novamente?",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                # Reprocessar todos os selecionados
                for item in selected_items:
                    quad = item.data(Qt.UserRole)
                    if quad:
                        quads_a_processar.append(quad)
            else:
                return
        
        # Se só tem 1 quad, processar direto
        if len(quads_a_processar) == 1:
            self._processar_quad(quads_a_processar[0])
            return
        
        # Se tem múltiplos, confirmar e processar em batch
        reply = QMessageBox.question(self, "Confirmar",
            f"Processar {len(quads_a_processar)} quads selecionados?",
            QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.No:
            return
        
        # Processar múltiplos quads
        self._processar_multiplos_quads(quads_a_processar)
    
    def _processar_multiplos_quads(self, quads):
        """Processa uma lista de quads em sequência."""
        import gc
        
        total = len(quads)
        processados = 0
        erros = 0
        
        self.rvn_status.setText(f"Processando 0/{total}...")
        self.rvn_progress.setVisible(True)
        self.rvn_progress.setValue(0)
        QApplication.processEvents()
        
        for i, quad in enumerate(quads):
            quad_id = quad.get('id', '')
            
            self.rvn_status.setText(f"Processando {i+1}/{total}: {quad_id}...")
            self.rvn_progress.setValue(int((i / total) * 100))
            QApplication.processEvents()
            
            try:
                self._processar_quad(quad, silencioso=True)
                processados += 1
            except Exception as e:
                erros += 1
                print(f"Erro ao processar {quad_id}: {e}")
            
            # Limpar memória
            gc.collect()
            QApplication.processEvents()
        
        self.rvn_progress.setValue(100)
        self.rvn_progress.setVisible(False)
        
        # Ligar camada de vegetação
        self._ligar_camada_vegetacao_nativa()
        
        # Mensagem final
        if erros > 0:
            QMessageBox.warning(self, "Concluído com erros",
                f"Processados: {processados}/{total}\nErros: {erros}")
        else:
            QMessageBox.information(self, "Concluído",
                f"Todos os {total} quads foram processados com sucesso!")
        
        self.rvn_status.setText("")
    
    def _processar_todos_quads(self):
        """Processa todos os quads pendentes automaticamente."""
        pendentes = []
        for i in range(self.quads_list.count()):
            item = self.quads_list.item(i)
            status = item.data(Qt.UserRole + 1)
            if status == 'pendente':
                pendentes.append(i)
        
        if not pendentes:
            QMessageBox.information(self, "Info", "Todos os quads já foram processados!")
            return
        
        # Confirmar
        reply = QMessageBox.question(self, "Confirmar",
            f"Processar {len(pendentes)} quads?\n\n"
            f"Tempo estimado: ~{len(pendentes)} minutos",
            QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.No:
            return
        
        # Processar cada quad
        self.rvn_progress.setVisible(True)
        self.rvn_progress.setRange(0, len(pendentes))
        
        sucesso = 0
        erros = []
        self._processamento_cancelado = False
        
        for idx, quad_idx in enumerate(pendentes):
            # Verificar se foi cancelado
            if self._processamento_cancelado:
                break
            
            self.rvn_progress.setValue(idx)
            self.quads_list.setCurrentRow(quad_idx)
            
            quad = self.planet_quads[quad_idx]
            quad_id = quad.get('id', '')
            
            self.rvn_status.setText(f"Processando {idx+1}/{len(pendentes)}: {quad_id}")
            
            # Processar eventos para manter UI responsiva
            QApplication.processEvents()
            
            try:
                result = self._processar_quad(quad, silencioso=True)
                if result:
                    sucesso += 1
                else:
                    erros.append(quad_id)
            except MemoryError:
                erros.append(f"{quad_id}: Sem memória")
                # Forçar coleta de lixo
                import gc
                gc.collect()
            except Exception as e:
                erros.append(f"{quad_id}: {str(e)[:50]}")
            
            # Processar eventos novamente
            QApplication.processEvents()
            
            # Pausa curta entre processamentos para estabilidade
            QThread.msleep(100)
        
        self.rvn_progress.setValue(len(pendentes))
        self.rvn_progress.setVisible(False)
        
        # Processar eventos para garantir que a tabela foi atualizada
        QApplication.processEvents()
        
        # Ligar a camada de vegetação e dar zoom no último quad processado
        if sucesso > 0 and len(self.planet_quads) > 0:
            ultimo_quad = self.planet_quads[pendentes[-1]] if pendentes else None
            if ultimo_quad:
                self._ligar_vegetacao_e_zoom_quad(ultimo_quad)
        else:
            # Mesmo sem sucesso, tentar ligar a camada de vegetação se existir
            self._ligar_camada_vegetacao_nativa()
        
        # Resultado
        msg = f"Processamento concluído!\n\nSucesso: {sucesso}\nErros: {len(erros)}"
        if erros:
            msg += f"\n\nQuads com erro:\n" + "\n".join(erros[:5])
            if len(erros) > 5:
                msg += f"\n...e mais {len(erros) - 5}"
        
        QMessageBox.information(self, "Resultado", msg)
        self.rvn_status.setText("")
    
    def _processar_quad(self, quad, silencioso=False):
        """
        Processa um quad completo:
        1. Download da imagem
        2. Classificação (Vegetação/Não Vegetação)
        3. Recorte pelos imóveis
        4. Salvamento na camada Vegetação Nativa
        5. Limpeza de temporários
        """
        import time
        
        quad_id = quad.get('id', '')
        mosaic_name = getattr(self, 'current_planet_mosaic', {}).get('name', 'planet')
        
        # DEBUG: Início do processamento
        tempo_total_inicio = time.time()
        print(f"\n{'='*70}")
        print(f"⏱️  DEBUG TIMING - PROCESSAMENTO DO QUAD: {quad_id}")
        print(f"{'='*70}")
        
        temp_dir = os.path.join(tempfile.gettempdir(), f'rvn_processing_{quad_id}')
        try:
            os.makedirs(temp_dir, exist_ok=True)
        except Exception:
            temp_dir = tempfile.mkdtemp(prefix=f'rvn_{quad_id}_')
        
        try:
            # ============================================================
            # PASSO 1: Download da imagem do quad
            # ============================================================
            if not silencioso:
                self.rvn_status.setText(f"1/5 Baixando imagem {quad_id}...")
                self.rvn_progress.setVisible(True)
                self.rvn_progress.setValue(10)
            QApplication.processEvents()
            
            t1_inicio = time.time()
            image_path = self._download_quad_image(quad, temp_dir)
            t1_fim = time.time()
            print(f"📥 PASSO 1 - Download da imagem: {t1_fim - t1_inicio:.2f} segundos")
            
            if not image_path:
                raise Exception("Falha no download da imagem")
            
            # ============================================================
            # PASSO 2: Classificação da vegetação
            # ============================================================
            if not silencioso:
                self.rvn_status.setText(f"2/5 Classificando vegetação...")
                self.rvn_progress.setValue(30)
            QApplication.processEvents()
            
            t2_inicio = time.time()
            classified_path = self._classificar_vegetacao(image_path, quad_id, temp_dir)
            t2_fim = time.time()
            print(f"🌿 PASSO 2 - Classificação: {t2_fim - t2_inicio:.2f} segundos")
            
            if not classified_path:
                raise Exception("Falha na classificação")
            
            # ============================================================
            # PASSO 3: Recorte pelos imóveis
            # ============================================================
            if not silencioso:
                self.rvn_status.setText(f"3/5 Recortando por imóveis...")
                self.rvn_progress.setValue(50)
            QApplication.processEvents()
            
            t3_inicio = time.time()
            veg_layer = self._recortar_por_imoveis(classified_path, quad, temp_dir)
            t3_fim = time.time()
            print(f"✂️  PASSO 3 - Recorte por imóveis: {t3_fim - t3_inicio:.2f} segundos")
            
            if not veg_layer:
                raise Exception("Falha no recorte")
            
            # ============================================================
            # PASSO 4: Salvamento no GeoPackage
            # ============================================================
            if not silencioso:
                self.rvn_status.setText(f"4/5 Salvando Vegetação Nativa...")
                self.rvn_progress.setValue(70)
            QApplication.processEvents()
            
            t4_inicio = time.time()
            saved = self._salvar_vegetacao_nativa(veg_layer, quad_id, mosaic_name)
            t4_fim = time.time()
            print(f"💾 PASSO 4 - Salvamento: {t4_fim - t4_inicio:.2f} segundos")
            
            if not saved:
                raise Exception("Falha ao salvar")
            
            # ============================================================
            # PASSO 5: Atualizar UI e limpar temporários
            # ============================================================
            if not silencioso:
                self.rvn_status.setText(f"5/5 Finalizando...")
                self.rvn_progress.setValue(90)
            QApplication.processEvents()
            
            t5_inicio = time.time()
            
            # Marcar quad como processado
            self.quads_processados.add(quad_id)
            self._atualizar_item_quad(quad_id, 'processado')
            
            # Limpar temporários
            self._limpar_temporarios(temp_dir)
            
            t5_fim = time.time()
            print(f"🧹 PASSO 5 - Limpeza e UI: {t5_fim - t5_inicio:.2f} segundos")
            
            # RESUMO DE TEMPO
            tempo_total = time.time() - tempo_total_inicio
            print(f"\n{'─'*50}")
            print(f"⏱️  TEMPO TOTAL: {tempo_total:.2f} segundos ({tempo_total/60:.1f} min)")
            print(f"{'─'*50}")
            print(f"   Download:      {t1_fim - t1_inicio:6.2f}s ({(t1_fim - t1_inicio)/tempo_total*100:5.1f}%)")
            print(f"   Classificação: {t2_fim - t2_inicio:6.2f}s ({(t2_fim - t2_inicio)/tempo_total*100:5.1f}%)")
            print(f"   Recorte:       {t3_fim - t3_inicio:6.2f}s ({(t3_fim - t3_inicio)/tempo_total*100:5.1f}%)")
            print(f"   Salvamento:    {t4_fim - t4_inicio:6.2f}s ({(t4_fim - t4_inicio)/tempo_total*100:5.1f}%)")
            print(f"   Limpeza/UI:    {t5_fim - t5_inicio:6.2f}s ({(t5_fim - t5_inicio)/tempo_total*100:5.1f}%)")
            print(f"{'='*70}\n")
            
            if not silencioso:
                self.rvn_progress.setValue(100)
                self.rvn_progress.setVisible(False)
                self.rvn_status.setText(f"✓ {quad_id} processado com sucesso!")
                
                # Processar eventos para garantir que a tabela foi atualizada
                QApplication.processEvents()
                
                # Ligar a camada de vegetação automaticamente e dar zoom no quad
                self._ligar_vegetacao_e_zoom_quad(quad)
            else:
                # Mesmo em modo silencioso, ligar a vegetação
                self._ligar_camada_vegetacao_nativa()
            
            return True
            
        except Exception as e:
            # Imprimir erro detalhado no console do QGIS
            import traceback
            print(f"\n{'='*60}")
            print(f"❌ ERRO AO PROCESSAR QUAD: {quad_id}")
            print(f"{'='*60}")
            print(f"Tipo: {type(e).__name__}")
            print(f"Mensagem: {e}")
            print(f"\nTraceback completo:")
            traceback.print_exc()
            print(f"{'='*60}\n")
            
            self._limpar_temporarios(temp_dir)
            self._atualizar_item_quad(quad_id, 'erro')
            
            if not silencioso:
                self.rvn_progress.setVisible(False)
                self.rvn_status.setText(f"✗ Erro: {str(e)}")
                QMessageBox.critical(self, "Erro", f"Erro ao processar {quad_id}:\n{str(e)}\n\nVeja o console Python para detalhes.")
            
            return False
    
    def _download_quad_image(self, quad, temp_dir):
        """Baixa a imagem GeoTIFF do quad usando a sessão autenticada do Planet."""
        quad_id = quad.get('id', '')
        mosaic_name = self.current_planet_mosaic.get('name', '')
        
        output_path = os.path.join(temp_dir, f"{quad_id}.tif")
        
        # Tentar várias URLs de download
        urls_to_try = []
        
        # Tentar obter tanto o name quanto o id do mosaico
        mosaic_id = self.current_planet_mosaic.get('id', mosaic_name)
        
        print(f"Mosaico: name='{mosaic_name}', id='{mosaic_id}'")
        
        # Se o quad veio do shapefile local, buscar metadados na API
        if quad.get('_local'):
            print(f"Quad local, buscando metadados na API Planet...")
            # Tentar com o nome do mosaico primeiro
            quad_api = planet_client.get_quad_by_id(mosaic_name, quad_id)
            if not quad_api and mosaic_id != mosaic_name:
                # Tentar com o ID do mosaico
                quad_api = planet_client.get_quad_by_id(mosaic_id, quad_id)
            
            if quad_api:
                download_link = quad_api.get('_links', {}).get('download')
                if download_link:
                    urls_to_try.append(download_link)
                    print(f"✓ Link de download obtido da API")
            else:
                print(f"⚠ Não foi possível obter metadados do quad {quad_id}")
        else:
            # URL do link de download (se disponível no quad)
            download_link = quad.get('_links', {}).get('download')
            if download_link:
                urls_to_try.append(download_link)
        
        # URLs de fallback - tentar com nome e id do mosaico
        urls_to_try.append(f"https://api.planet.com/basemaps/v1/mosaics/{mosaic_name}/quads/{quad_id}/full")
        if mosaic_id != mosaic_name:
            urls_to_try.append(f"https://api.planet.com/basemaps/v1/mosaics/{mosaic_id}/quads/{quad_id}/full")
        urls_to_try.append(f"https://tiles.planet.com/basemaps/v1/planet-tiles/{mosaic_name}/quads/{quad_id}/full")
        
        for download_url in urls_to_try:
            try:
                print(f"Tentando download: {download_url[:80]}...")
                
                # Usar a sessão autenticada do planet_client
                if planet_client.session:
                    response = planet_client.session.get(download_url, stream=True, timeout=120)
                    
                    if response.status_code == 200:
                        content_type = response.headers.get('content-type', '')
                        
                        # Verificar se é um TIFF
                        if 'tiff' in content_type.lower() or 'octet-stream' in content_type.lower():
                            with open(output_path, 'wb') as f:
                                for chunk in response.iter_content(chunk_size=65536):
                                    if chunk:
                                        f.write(chunk)
                                    QApplication.processEvents()
                            
                            # Verificar se o arquivo é válido
                            if os.path.exists(output_path):
                                file_size = os.path.getsize(output_path)
                                
                                # Verificar magic bytes do TIFF
                                with open(output_path, 'rb') as f:
                                    header = f.read(4)
                                
                                # TIFF começa com II ou MM
                                if file_size > 10000 and header[:2] in [b'II', b'MM']:
                                    print(f"Download OK: {file_size/1024/1024:.2f} MB")
                                    return output_path
                                else:
                                    print(f"Arquivo não é TIFF válido (header: {header[:4]})")
                                    os.remove(output_path)
                        else:
                            print(f"Content-type inesperado: {content_type}")
                    else:
                        print(f"HTTP {response.status_code}")
                else:
                    # Fallback: usar urllib com Basic Auth
                    import urllib.request
                    import ssl
                    import base64
                    
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    
                    req = urllib.request.Request(download_url)
                    
                    # Adicionar autenticação Basic
                    if planet_client.user_email:
                        credentials = f"{planet_client.user_email}:{planet_client.api_key or ''}"
                        encoded = base64.b64encode(credentials.encode()).decode()
                        req.add_header('Authorization', f'Basic {encoded}')
                    
                    with urllib.request.urlopen(req, context=ctx, timeout=120) as response:
                        with open(output_path, 'wb') as f:
                            while True:
                                chunk = response.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                                QApplication.processEvents()
                    
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 10000:
                        return output_path
                        
            except Exception as e:
                print(f"Erro com URL {download_url[:50]}: {e}")
                continue
        
        print(f"Falha em todas as tentativas de download para {quad_id}")
        return None
    
    def _classificar_vegetacao(self, image_path, quad_id, temp_dir):
        """
        Classifica a imagem em Vegetação (1) e Não Vegetação (2).
        Usa o classificador_local.py com Random Forest e amostras de treinamento.
        Retorna o caminho do GeoPackage com a classificação vetorizada.
        """
        output_gpkg = os.path.join(temp_dir, f"{quad_id}_classificacao.gpkg")
        
        try:
            # Verificar se arquivo existe
            if not os.path.exists(image_path):
                raise Exception(f"Arquivo não encontrado: {image_path}")
            
            print(f"Classificando: {image_path}")
            
            # Garantir stderr válido
            import sys
            import io
            if sys.stderr is None:
                sys.stderr = io.StringIO()
            if sys.stdout is None:
                sys.stdout = io.StringIO()
            
            # Verificar dependências antes de tentar classificar
            try:
                from ..dependency_manager import verificar_dependencias, formatar_mensagem_problemas
                _ok, _problemas = verificar_dependencias()
                if _problemas:
                    libs_faltando = ", ".join(p["pip_name"] for p in _problemas)
                    raise Exception(
                        f"Bibliotecas necessárias não disponíveis: {libs_faltando}.\n"
                        "Reinicie o QGIS após instalar as dependências."
                    )
            except ImportError:
                pass
            
            # Obter caminho do CSV de amostras
            amostras_csv = self._get_amostras_csv_path()
            if not amostras_csv or not os.path.exists(amostras_csv):
                raise Exception("Arquivo de amostras não encontrado!")
            
            print(f"Usando amostras de: {amostras_csv}")
            
            # Importar e usar o classificador local
            try:
                from ..classificador_local import classificar_imagem_planet
                print("Classificador local importado com sucesso!")
            except ImportError:
                # Tentar importação alternativa
                try:
                    import importlib.util
                    classificador_path = os.path.join(
                        os.path.dirname(os.path.dirname(__file__)), 
                        'classificador_local.py'
                    )
                    spec = importlib.util.spec_from_file_location("classificador_local", classificador_path)
                    classificador_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(classificador_module)
                    classificar_imagem_planet = classificador_module.classificar_imagem_planet
                    print(f"Classificador carregado de: {classificador_path}")
                except Exception as e:
                    raise Exception(f"Não foi possível importar classificador_local: {e}")
            
            # Executar classificação com Random Forest
            print("=" * 50)
            print("INICIANDO CLASSIFICAÇÃO COM RANDOM FOREST")
            print(f"  Imagem: {image_path}")
            print(f"  Quad ID: {quad_id}")
            print(f"  Amostras: {amostras_csv}")
            print(f"  Saída: {output_gpkg}")
            print("=" * 50)
            
            try:
                gdf = classificar_imagem_planet(
                    caminho_imagem=image_path,
                    caminho_csv=amostras_csv,
                    caminho_saida=output_gpkg,
                    n_arvores=10,
                    fator_desvio=1.0
                )
            except Exception as e_class:
                print(f"❌ ERRO NO CLASSIFICADOR: {type(e_class).__name__}: {e_class}")
                import traceback
                traceback.print_exc()
                raise Exception(f"Classificador falhou: {e_class}")
            
            if os.path.exists(output_gpkg):
                print(f"✓ Classificação vetorial salva: {output_gpkg}")
                return output_gpkg
            else:
                raise Exception("Arquivo de saída não foi criado pelo classificador")
            
        except Exception as e:
            print(f"❌ Erro na classificação: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _selecionar_amostras_csv(self):
        """Abre diálogo para selecionar arquivo CSV de amostras de treinamento."""
        # Diretório inicial: onde o plugin está ou último diretório usado
        initial_dir = os.path.dirname(os.path.dirname(__file__))
        if hasattr(self, 'last_amostras_dir') and self.last_amostras_dir:
            initial_dir = self.last_amostras_dir
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar Amostras de Treinamento",
            initial_dir,
            "CSV Files (*.csv);;All Files (*)"
        )
        
        if file_path:
            self.amostras_csv_path = file_path
            self.last_amostras_dir = os.path.dirname(file_path)
            
            # Contar amostras no arquivo
            try:
                import pandas as pd
                df = pd.read_csv(file_path)
                n_amostras = len(df)
                n_quads = df['quad_id'].nunique() if 'quad_id' in df.columns else '?'
                
                # Atualizar status
                nome_arquivo = os.path.basename(file_path)
                self.amostras_status.setText(f"✓ {nome_arquivo} ({n_amostras:,} amostras, {n_quads} quads)")
                self.amostras_status.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
                self.amostras_status.setToolTip(file_path)
                
                print(f"Amostras carregadas: {file_path}")
                print(f"  Total de amostras: {n_amostras}")
                print(f"  Quads únicos: {n_quads}")
                
                # Verificar se pode habilitar botões de processamento
                self._verificar_requisitos_processamento()
                
            except Exception as e:
                self.amostras_status.setText(f"⚠ Erro ao ler CSV: {str(e)[:30]}")
                self.amostras_status.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
                print(f"Erro ao ler CSV de amostras: {e}")
    
    def _verificar_requisitos_processamento(self):
        """Verifica se todos os requisitos para processamento estão atendidos."""
        # Requisitos: amostras + quads na lista
        tem_amostras = self.amostras_csv_path is not None and os.path.exists(self.amostras_csv_path)
        tem_quads = hasattr(self, 'planet_quads') and len(self.planet_quads) > 0
        
        pode_processar = tem_amostras and tem_quads
        
        self.btn_mapear_veg.setEnabled(pode_processar)
        self.btn_processar_todos.setEnabled(pode_processar)
        
        if not tem_amostras:
            print("Processamento desabilitado: selecione amostras de treinamento")
        elif not tem_quads:
            print("Processamento desabilitado: busque quads dos imóveis")
    
    # =========================================================================
    #               PROCESSAMENTO DE ELEGIBILIDADE
    # =========================================================================
    
    def _abrir_config_processamento(self):
        """Abre diálogo para configurar parâmetros da análise de elegibilidade."""
        from qgis.PyQt.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
            QDoubleSpinBox, QSpinBox, QLineEdit, QPushButton, QLabel,
            QScrollArea, QWidget, QTextEdit, QTabWidget
        )
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Configurar Análise de Elegibilidade")
        dialog.setMinimumSize(620, 720)
        
        layout = QVBoxLayout(dialog)
        
        # Info sobre GeoPackage em uso
        gpkg_atual = self.gpkg_path.text() if hasattr(self, 'gpkg_path') and self.gpkg_path.text() else "Nenhum"
        info_gpkg = QLabel(f"📁 GeoPackage: {os.path.basename(gpkg_atual) if gpkg_atual != 'Nenhum' else 'Nenhum carregado'}")
        info_gpkg.setStyleSheet("color: #3498db; font-size: 10px; font-weight: bold; padding: 4px;")
        info_gpkg.setToolTip(gpkg_atual)
        layout.addWidget(info_gpkg)

        # ============================================================ #
        # QTabWidget com 3 abas: Elegibilidade | Listas | Priorização   #
        # ============================================================ #
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ----- ABA 1: ELEGIBILIDADE (conteúdo original) ----- #
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        # --- Grupo: Tolerâncias e Thresholds ---
        group_tolerancias = QGroupBox("Tolerâncias e Limites")
        tol_layout = QFormLayout(group_tolerancias)
        
        self.config_tol_fp_uc = QDoubleSpinBox()
        self.config_tol_fp_uc.setRange(0, 1)
        self.config_tol_fp_uc.setSingleStep(0.01)
        self.config_tol_fp_uc.setValue(0.05)
        self.config_tol_fp_uc.setDecimals(2)
        self.config_tol_fp_uc.setSuffix(" (5%)")
        tol_layout.addRow("Tolerância FP/UC:", self.config_tol_fp_uc)
        
        self.config_tol_sobrep = QDoubleSpinBox()
        self.config_tol_sobrep.setRange(0, 1)
        self.config_tol_sobrep.setSingleStep(0.01)
        self.config_tol_sobrep.setValue(0.50)
        self.config_tol_sobrep.setDecimals(2)
        self.config_tol_sobrep.setSuffix(" (50%)")
        tol_layout.addRow("Tolerância Sobreposição CAR:", self.config_tol_sobrep)
        
        self.config_modulos = QSpinBox()
        self.config_modulos.setRange(1, 10)
        self.config_modulos.setValue(4)
        tol_layout.addRow("Limite Módulos Fiscais:", self.config_modulos)
        
        self.config_prodes_1ha = QDoubleSpinBox()
        self.config_prodes_1ha.setRange(0, 100)
        self.config_prodes_1ha.setValue(1.0)
        self.config_prodes_1ha.setSuffix(" ha")
        tol_layout.addRow("Limite PRODES (1ha):", self.config_prodes_1ha)
        
        self.config_prodes_6ha = QDoubleSpinBox()
        self.config_prodes_6ha.setRange(0, 100)
        self.config_prodes_6ha.setValue(6.25)
        self.config_prodes_6ha.setSuffix(" ha")
        tol_layout.addRow("Limite PRODES (6,25ha):", self.config_prodes_6ha)
        
        scroll_layout.addWidget(group_tolerancias)
        
        # --- Checkbox: Certidão MPF ---
        self.config_certidao_mpf = QCheckBox("Verificação de inadimplência via certidão MPF")
        self.config_certidao_mpf.setChecked(False)
        self.config_certidao_mpf.setToolTip(
            "Quando ativado, consulta automaticamente o portal do MPF para\n"
            "emitir certidão de nada consta para cada CPF dos imóveis elegíveis.\n"
            "Requer internet, Chrome instalado e chave 2Captcha configurada."
        )
        scroll_layout.addWidget(self.config_certidao_mpf)
        
        self.config_mpf_options_widget = QWidget()
        mpf_layout = QFormLayout(self.config_mpf_options_widget)
        mpf_layout.setContentsMargins(20, 0, 0, 0)
        
        self.config_api_key_2captcha = QLineEdit()
        self.config_api_key_2captcha.setPlaceholderText("Chave da API 2Captcha")
        self.config_api_key_2captcha.setEchoMode(QLineEdit.Password)
        mpf_layout.addRow("Chave 2Captcha:", self.config_api_key_2captcha)
        
        pasta_widget = QWidget()
        pasta_h = QHBoxLayout(pasta_widget)
        pasta_h.setContentsMargins(0, 0, 0, 0)
        self.config_pasta_certidoes = QLineEdit()
        self.config_pasta_certidoes.setPlaceholderText("(opcional) Pasta para salvar os PDFs das certidões")
        pasta_h.addWidget(self.config_pasta_certidoes)
        btn_pasta_certidoes = QPushButton("...")
        btn_pasta_certidoes.setFixedWidth(30)
        btn_pasta_certidoes.clicked.connect(lambda: self._selecionar_pasta_certidoes())
        pasta_h.addWidget(btn_pasta_certidoes)
        mpf_layout.addRow("Salvar PDFs em:", pasta_widget)
        
        self.config_mpf_options_widget.setVisible(False)
        self.config_certidao_mpf.toggled.connect(self.config_mpf_options_widget.setVisible)
        scroll_layout.addWidget(self.config_mpf_options_widget)
        
        # --- Grupo: Thresholds de RVN ---
        group_rvn = QGroupBox("Thresholds de RVN por Fitofisionomia")
        rvn_layout = QFormLayout(group_rvn)
        
        self.config_rvn_floresta = QDoubleSpinBox()
        self.config_rvn_floresta.setRange(0, 1)
        self.config_rvn_floresta.setSingleStep(0.05)
        self.config_rvn_floresta.setValue(0.50)
        self.config_rvn_floresta.setDecimals(2)
        self.config_rvn_floresta.setSuffix(" (50%)")
        rvn_layout.addRow("Floresta (Fase 1):", self.config_rvn_floresta)
        
        self.config_rvn_cerrado = QDoubleSpinBox()
        self.config_rvn_cerrado.setRange(0, 1)
        self.config_rvn_cerrado.setSingleStep(0.05)
        self.config_rvn_cerrado.setValue(0.35)
        self.config_rvn_cerrado.setDecimals(2)
        self.config_rvn_cerrado.setSuffix(" (35%)")
        rvn_layout.addRow("Cerrado (Fase 1):", self.config_rvn_cerrado)
        
        self.config_rvn_campos = QDoubleSpinBox()
        self.config_rvn_campos.setRange(0, 1)
        self.config_rvn_campos.setSingleStep(0.05)
        self.config_rvn_campos.setValue(0.20)
        self.config_rvn_campos.setDecimals(2)
        self.config_rvn_campos.setSuffix(" (20%)")
        rvn_layout.addRow("Campos Gerais (Fase 1):", self.config_rvn_campos)
        
        self.config_rvn_floresta_f2 = QDoubleSpinBox()
        self.config_rvn_floresta_f2.setRange(0, 1)
        self.config_rvn_floresta_f2.setSingleStep(0.05)
        self.config_rvn_floresta_f2.setValue(0.80)
        self.config_rvn_floresta_f2.setDecimals(2)
        self.config_rvn_floresta_f2.setSuffix(" (80%)")
        rvn_layout.addRow("Floresta (Fase 2):", self.config_rvn_floresta_f2)
        
        scroll_layout.addWidget(group_rvn)
        
        # --- Grupo: Valores de Pagamento ---
        group_valores = QGroupBox("Valores de Pagamento")
        val_layout = QFormLayout(group_valores)
        
        self.config_faixa1_limite = QDoubleSpinBox()
        self.config_faixa1_limite.setRange(0, 1000)
        self.config_faixa1_limite.setValue(60.0)
        self.config_faixa1_limite.setSuffix(" ha")
        val_layout.addRow("Faixa 1 - Limite:", self.config_faixa1_limite)
        
        self.config_faixa1_valor = QDoubleSpinBox()
        self.config_faixa1_valor.setRange(0, 10000)
        self.config_faixa1_valor.setValue(200.0)
        self.config_faixa1_valor.setPrefix("R$ ")
        self.config_faixa1_valor.setSuffix("/ha")
        val_layout.addRow("Faixa 1 - Valor:", self.config_faixa1_valor)
        
        self.config_faixa2_limite = QDoubleSpinBox()
        self.config_faixa2_limite.setRange(0, 1000)
        self.config_faixa2_limite.setValue(20.0)
        self.config_faixa2_limite.setSuffix(" ha")
        val_layout.addRow("Faixa 2 - Limite:", self.config_faixa2_limite)
        
        self.config_faixa2_valor = QDoubleSpinBox()
        self.config_faixa2_valor.setRange(0, 10000)
        self.config_faixa2_valor.setValue(800.0)
        self.config_faixa2_valor.setPrefix("R$ ")
        self.config_faixa2_valor.setSuffix("/ha")
        val_layout.addRow("Faixa 2 - Valor:", self.config_faixa2_valor)
        
        self.config_valor_minimo = QDoubleSpinBox()
        self.config_valor_minimo.setRange(0, 100000)
        self.config_valor_minimo.setValue(1500.0)
        self.config_valor_minimo.setPrefix("R$ ")
        val_layout.addRow("Valor Mínimo:", self.config_valor_minimo)
        
        self.config_valor_fase1 = QDoubleSpinBox()
        self.config_valor_fase1.setRange(0, 100000)
        self.config_valor_fase1.setValue(1500.0)
        self.config_valor_fase1.setPrefix("R$ ")
        val_layout.addRow("Valor Fase 1:", self.config_valor_fase1)
        
        scroll_layout.addWidget(group_valores)
        
        # --- Grupo: Lista de CARs a Remover ---
        group_remover = QGroupBox("CARs a Remover da Análise de Sobreposição")
        remover_layout = QVBoxLayout(group_remover)
        
        label_remover = QLabel("Cole abaixo os códigos CAR (um por linha) que devem ser\n"
                               "ignorados na checagem de sobreposição com outros imóveis:")
        label_remover.setStyleSheet("font-size: 9px; color: #666;")
        remover_layout.addWidget(label_remover)
        
        self.config_lista_car_remover = QTextEdit()
        self.config_lista_car_remover.setPlaceholderText("Ex:\nAM-1303403-3F4BA07F5BB645E6A7DF2E9230C5E00A\nPA-1504455-36032ADC7E88456BAE98A3AD2F631644")
        self.config_lista_car_remover.setMinimumHeight(120)
        self.config_lista_car_remover.setMaximumHeight(200)
        
        # Carregar lista padrão do arquivo Remover_MF_SOB.csv
        lista_padrao = self._carregar_lista_car_padrao()
        if lista_padrao:
            self.config_lista_car_remover.setPlainText(lista_padrao)
        
        remover_layout.addWidget(self.config_lista_car_remover)
        
        # Contador de CARs
        self.label_count_car = QLabel("0 códigos CAR na lista")
        self.label_count_car.setStyleSheet("font-size: 9px; color: #999;")
        remover_layout.addWidget(self.label_count_car)
        
        # Atualizar contador quando o texto mudar
        self.config_lista_car_remover.textChanged.connect(self._atualizar_contador_car)
        self._atualizar_contador_car()  # Atualizar inicialmente
        
        scroll_layout.addWidget(group_remover)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        tabs.addTab(scroll, "Elegibilidade")

        # ----- ABA 2: LISTAS DE MUNICÍPIOS ----- #
        try:
            from Plugin_FMais.ui.dialogs.listas_municipios_widget import (
                ListasMunicipiosWidget, LISTA_PRIORITARIOS,
                LISTA_DESMATE_CONTROLE, LISTA_PROGRAMA_UNIAO,
            )
            # Listas iniciais (config atual ou padrão)
            cfg_atual = getattr(self, 'config_processamento', None)
            if cfg_atual:
                listas_iniciais = {
                    LISTA_PRIORITARIOS: set(getattr(cfg_atual, 'geocodigos_prioritarios', set())),
                    LISTA_DESMATE_CONTROLE: set(getattr(cfg_atual, 'geocodigos_desmate_controle', set())),
                    LISTA_PROGRAMA_UNIAO: set(getattr(cfg_atual, 'geocodigos_programa_uniao', set())),
                }
            else:
                from Plugin_FMais.core.processamento_elegiveis import ConfigProcessamento
                cfg_def = ConfigProcessamento()
                listas_iniciais = {
                    LISTA_PRIORITARIOS: set(cfg_def.geocodigos_prioritarios),
                    LISTA_DESMATE_CONTROLE: set(cfg_def.geocodigos_desmate_controle),
                    LISTA_PROGRAMA_UNIAO: set(cfg_def.geocodigos_programa_uniao),
                }
            self._listas_municipios_widget = ListasMunicipiosWidget(
                gpkg_atual if gpkg_atual != "Nenhum" else "",
                listas_iniciais,
                parent=dialog,
            )
            tabs.addTab(self._listas_municipios_widget, "Listas de Municípios")
        except Exception as e:
            placeholder = QLabel(f"Erro ao carregar aba de Listas: {e}")
            placeholder.setStyleSheet("color: #c0392b; padding: 20px;")
            tabs.addTab(placeholder, "Listas de Municípios")
            self._listas_municipios_widget = None

        # ----- ABA 3: PRIORIZAÇÃO ----- #
        try:
            from Plugin_FMais.ui.dialogs.priorizacao_widget import PriorizacaoWidget
            cfg_atual = getattr(self, 'config_processamento', None)
            cfg_dict = {}
            if cfg_atual:
                for chave in (
                    'crit_a1_ativo', 'crit_a2_ativo', 'crit_a3_ativo',
                    'crit_a4_ativo', 'crit_a5_ativo', 'crit_a6_ativo',
                    'crit_a7_ativo', 'crit_a8_ativo',
                    'planilha_candidatos', 'mapeamento_candidatos',
                ):
                    cfg_dict[chave] = getattr(cfg_atual, chave, None)
            self._priorizacao_widget = PriorizacaoWidget(cfg_dict, parent=dialog)
            tabs.addTab(self._priorizacao_widget, "Priorização")
        except Exception as e:
            placeholder = QLabel(f"Erro ao carregar aba de Priorização: {e}")
            placeholder.setStyleSheet("color: #c0392b; padding: 20px;")
            tabs.addTab(placeholder, "Priorização")
            self._priorizacao_widget = None

        # Botões
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        btn_cancelar = QPushButton("Cancelar")
        btn_cancelar.clicked.connect(dialog.reject)
        btn_layout.addWidget(btn_cancelar)
        
        btn_salvar = QPushButton("Salvar Configuração")
        btn_salvar.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #219a52;
            }
        """)
        btn_salvar.clicked.connect(lambda: self._salvar_config_processamento(dialog))
        btn_layout.addWidget(btn_salvar)
        
        layout.addLayout(btn_layout)
        
        dialog.exec_()
    
    def _carregar_lista_car_padrao(self) -> str:
        """Carrega lista padrão de CARs a remover do arquivo Remover_MF_SOB.csv."""
        try:
            # Tentar encontrar o arquivo na pasta do plugin ou do workspace
            # Pasta do plugin (Plugin_FMais) - main_window.py está em ui/
            plugin_dir = os.path.dirname(os.path.dirname(__file__))
            
            possiveis_caminhos = [
                os.path.join(plugin_dir, "Remover_MF_SOB.csv"),
                os.path.join(os.path.dirname(self.gpkg_path.text()) if hasattr(self, 'gpkg_path') and self.gpkg_path.text() else "", "Remover_MF_SOB.csv"),
            ]
            
            for caminho in possiveis_caminhos:
                print(f"[Lista CAR] Buscando em: {caminho}")
                if caminho and os.path.exists(caminho):
                    codigos = []
                    with open(caminho, 'r', encoding='utf-8') as f:
                        for linha in f:
                            cod = linha.strip()
                            if cod and len(cod) > 10:  # Filtrar linhas válidas
                                codigos.append(cod)
                    if codigos:
                        print(f"[Lista CAR] ✓ Carregados {len(codigos)} códigos")
                        return '\n'.join(codigos)
            print("[Lista CAR] ⚠ Arquivo Remover_MF_SOB.csv não encontrado")
            return ""
        except Exception as e:
            print(f"[Lista CAR] Erro ao carregar: {e}")
            return ""
    
    def _selecionar_pasta_certidoes(self):
        """Abre diálogo para selecionar pasta onde salvar PDFs das certidões MPF."""
        pasta = QFileDialog.getExistingDirectory(
            self, "Selecionar pasta para certidões MPF",
            self.config_pasta_certidoes.text() or ""
        )
        if pasta:
            self.config_pasta_certidoes.setText(pasta)
    
    def _atualizar_contador_car(self):
        """Atualiza o contador de CARs na lista."""
        if hasattr(self, 'config_lista_car_remover') and hasattr(self, 'label_count_car'):
            texto = self.config_lista_car_remover.toPlainText()
            count = len([linha for linha in texto.strip().split('\n') if linha.strip()])
            self.label_count_car.setText(f"{count} códigos CAR na lista")
    
    def _encontrar_camada_gpkg(self, gpkg_path: str, padroes: list) -> str:
        """
        Encontra o nome real de uma camada no GeoPackage baseado em padrões.
        
        Args:
            gpkg_path: Caminho do GeoPackage
            padroes: Lista de padrões para buscar (ex: ['imoveis', 'imovel'])
            
        Returns:
            Nome da camada encontrada ou string vazia
        """
        try:
            layer_names = self._get_gpkg_layer_names(gpkg_path)
            
            for name in layer_names:
                normalized = name.lower().replace(' ', '').replace('_', '').replace('-', '')
                for padrao in padroes:
                    if padrao in normalized:
                        return name
            return ""
        except:
            return ""
    
    def _salvar_config_processamento(self, dialog):
        """Salva a configuração do processamento."""
        from Plugin_FMais.core.processamento_elegiveis import ConfigProcessamento
        
        # Usar GeoPackage padrão já carregado
        gpkg_atual = self.gpkg_path.text() if hasattr(self, 'gpkg_path') and self.gpkg_path.text() else ""
        
        if not gpkg_atual or not os.path.exists(gpkg_atual):
            QMessageBox.warning(
                self,
                "GeoPackage Necessário",
                "Carregue um GeoPackage de referência antes de configurar.\n\n"
                "Use o botão 'Abrir' na seção 'Camadas de Referência'."
            )
            return
        
        # Detectar nomes reais das camadas no GeoPackage
        camadas_detectadas = {
            'imoveis': self._encontrar_camada_gpkg(gpkg_atual, ['imoveis', 'imovel', 'imóveis', 'imóvel']),
            'cnfp': self._encontrar_camada_gpkg(gpkg_atual, ['cnfp', 'florestaspublicas']),
            'ucs': self._encontrar_camada_gpkg(gpkg_atual, ['ucs', 'unidadesconservacao', 'unidades_conservacao']),
            'quilombolas': self._encontrar_camada_gpkg(gpkg_atual, ['quilombola', 'quilombolas']),
            'terras_indigenas': self._encontrar_camada_gpkg(gpkg_atual, ['indigena', 'indigenas', 'terrasindigenas']),
            'embargos_icmbio': self._encontrar_camada_gpkg(gpkg_atual, ['embargosicmbio', 'embargo_icmbio', 'icmbio']),
            'embargos_ibama': self._encontrar_camada_gpkg(gpkg_atual, ['embargosibama', 'embargo_ibama']),
            'car_amazonia': self._encontrar_camada_gpkg(gpkg_atual, ['caramazonia', 'car_amazonia', 'car']),
            'prodes': self._encontrar_camada_gpkg(gpkg_atual, ['prodes']),
            'fitofisionomia': self._encontrar_camada_gpkg(gpkg_atual, ['fitofisionomia', 'fitofisionomias', 'fito']),
            'rvn': self._encontrar_camada_gpkg(gpkg_atual, ['vegetacaonativa', 'vegetacao_nativa', 'rvn']),
            'amazonia_legal': self._encontrar_camada_gpkg(gpkg_atual, ['amazonialegal', 'amazonia_legal', 'amzl']),
            'municipios': self._encontrar_camada_gpkg(gpkg_atual, ['municipios', 'municipio', 'municípios']),
            'biomas': self._encontrar_camada_gpkg(gpkg_atual, ['biomas', 'bioma']),
            'areas_prioritarias': self._encontrar_camada_gpkg(gpkg_atual, ['areasprioritarias', 'areas_prioritarias', 'areaspriorit']),
        }
        
        # Verificar se camada de imóveis foi encontrada
        if not camadas_detectadas['imoveis']:
            QMessageBox.warning(
                self,
                "Camada de Imóveis Não Encontrada",
                "Não foi possível encontrar a camada de imóveis no GeoPackage.\n\n"
                "Certifique-se que o GeoPackage contém uma camada com 'imoveis' ou 'imóvel' no nome."
            )
            return
        
        print(f"[Config] Camadas detectadas no GeoPackage:")
        for key, value in camadas_detectadas.items():
            status = f"✓ {value}" if value else "⚠ Não encontrada"
            print(f"  - {key}: {status}")
        
        # Coletar listas de municípios da aba "Listas de Municípios"
        listas_munis = {}
        if hasattr(self, '_listas_municipios_widget') and self._listas_municipios_widget:
            try:
                listas_munis = self._listas_municipios_widget.get_listas()
            except Exception as e:
                print(f"[Config] Erro ao obter listas de municípios: {e}")

        from Plugin_FMais.ui.dialogs.listas_municipios_widget import (
            LISTA_PRIORITARIOS, LISTA_DESMATE_CONTROLE, LISTA_PROGRAMA_UNIAO,
        )
        # Coletar configurações de priorização
        cfg_prio = {}
        if hasattr(self, '_priorizacao_widget') and self._priorizacao_widget:
            try:
                cfg_prio = self._priorizacao_widget.get_config_priorizacao()
            except Exception as e:
                print(f"[Config] Erro ao obter config de priorização: {e}")

        # Criar objeto de configuração com nomes das camadas detectados
        self.config_processamento = ConfigProcessamento(
            gpkg_path=gpkg_atual,
            
            # Nomes das camadas (detectados dinamicamente)
            camada_imoveis=camadas_detectadas['imoveis'] or "imoveis_analisar",
            camada_cnfp=camadas_detectadas['cnfp'] or "cnfp",
            camada_ucs=camadas_detectadas['ucs'] or "ucs",
            camada_quilombolas=camadas_detectadas['quilombolas'] or "quilombolas",
            camada_terras_indigenas=camadas_detectadas['terras_indigenas'] or "terras_indigenas",
            camada_embargos_icmbio=camadas_detectadas['embargos_icmbio'] or "embargos_icmbio",
            camada_embargos_ibama=camadas_detectadas['embargos_ibama'] or "embargos_ibama",
            camada_car_amazonia=camadas_detectadas['car_amazonia'] or "car_amazonia",
            camada_prodes=camadas_detectadas['prodes'] or "prodes",
            camada_fitofisionomia=camadas_detectadas['fitofisionomia'] or "fitofisionomia",
            camada_rvn=camadas_detectadas['rvn'] or "vegetacao_nativa",
            camada_amazonia_legal=camadas_detectadas['amazonia_legal'] or "amazonia_legal",
            camada_municipios=camadas_detectadas['municipios'] or "municipios",
            camada_biomas=camadas_detectadas['biomas'] or "biomas",
            camada_areas_prioritarias=camadas_detectadas['areas_prioritarias'] or "areas_prioritarias_conservacao",
            
            # Tolerâncias
            tolerancia_fp_uc=self.config_tol_fp_uc.value(),
            tolerancia_sobreposicao_car=self.config_tol_sobrep.value(),
            limite_modulos_fiscais=self.config_modulos.value(),
            limite_prodes_1ha=self.config_prodes_1ha.value(),
            limite_prodes_6ha=self.config_prodes_6ha.value(),
            
            # RVN thresholds
            threshold_rvn_floresta=self.config_rvn_floresta.value(),
            threshold_rvn_cerrado=self.config_rvn_cerrado.value(),
            threshold_rvn_campos=self.config_rvn_campos.value(),
            threshold_rvn_floresta_f2=self.config_rvn_floresta_f2.value(),
            
            # Valores
            faixa1_limite_ha=self.config_faixa1_limite.value(),
            faixa1_valor_ha=self.config_faixa1_valor.value(),
            faixa2_limite_ha=self.config_faixa2_limite.value(),
            faixa2_valor_ha=self.config_faixa2_valor.value(),
            valor_minimo=self.config_valor_minimo.value(),
            valor_fase1=self.config_valor_fase1.value(),
            
            # Lista de CARs a remover da análise de sobreposição
            lista_car_remover=self.config_lista_car_remover.toPlainText() if hasattr(self, 'config_lista_car_remover') else "",
            
            # Verificação de certidão MPF
            verificar_certidao_mpf=self.config_certidao_mpf.isChecked() if hasattr(self, 'config_certidao_mpf') else False,
            api_key_2captcha=self.config_api_key_2captcha.text().strip() if hasattr(self, 'config_api_key_2captcha') else "",
            pasta_certidoes_mpf=self.config_pasta_certidoes.text().strip() if hasattr(self, 'config_pasta_certidoes') else "",

            # Listas de municípios (da aba "Listas de Municípios")
            geocodigos_prioritarios=listas_munis.get(LISTA_PRIORITARIOS) or ConfigProcessamento.__dataclass_fields__['geocodigos_prioritarios'].default_factory(),
            geocodigos_desmate_controle=listas_munis.get(LISTA_DESMATE_CONTROLE) or ConfigProcessamento.__dataclass_fields__['geocodigos_desmate_controle'].default_factory(),
            geocodigos_programa_uniao=listas_munis.get(LISTA_PROGRAMA_UNIAO) or ConfigProcessamento.__dataclass_fields__['geocodigos_programa_uniao'].default_factory(),

            # Critérios de priorização ativos
            crit_a1_ativo=cfg_prio.get('crit_a1_ativo', True),
            crit_a2_ativo=cfg_prio.get('crit_a2_ativo', True),
            crit_a3_ativo=cfg_prio.get('crit_a3_ativo', True),
            crit_a4_ativo=cfg_prio.get('crit_a4_ativo', True),
            crit_a5_ativo=cfg_prio.get('crit_a5_ativo', True),
            crit_a6_ativo=cfg_prio.get('crit_a6_ativo', True),
            crit_a7_ativo=cfg_prio.get('crit_a7_ativo', True),
            crit_a8_ativo=cfg_prio.get('crit_a8_ativo', True),
            planilha_candidatos=cfg_prio.get('planilha_candidatos', "") or "",
            mapeamento_candidatos=cfg_prio.get('mapeamento_candidatos', {}) or {},
        )
        
        # Atualizar UI
        self.processamento_status.setText("✓ Configuração salva! Pronto para processar.")
        self.processamento_status.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
        self.btn_iniciar_processamento.setEnabled(True)
        
        dialog.accept()
        
        QMessageBox.information(
            self,
            "Configuração Salva",
            "Configuração de processamento salva com sucesso!\n\n"
            "Clique em 'Processar Elegibilidade' para iniciar a análise."
        )
    
    def _criar_config_processamento_padrao(self):
        """Cria configuração de processamento com valores padrão."""
        from Plugin_FMais.core.processamento_elegiveis import ConfigProcessamento
        
        gpkg_atual = self.gpkg_path.text() if hasattr(self, 'gpkg_path') and self.gpkg_path.text() else ""
        
        if not gpkg_atual or not os.path.exists(gpkg_atual):
            return False
        
        # Detectar nomes reais das camadas no GeoPackage
        camadas_detectadas = {
            'imoveis': self._encontrar_camada_gpkg(gpkg_atual, ['imoveis', 'imovel', 'imóveis', 'imóvel']),
            'cnfp': self._encontrar_camada_gpkg(gpkg_atual, ['cnfp', 'florestaspublicas']),
            'ucs': self._encontrar_camada_gpkg(gpkg_atual, ['ucs', 'unidadesconservacao', 'unidades_conservacao']),
            'quilombolas': self._encontrar_camada_gpkg(gpkg_atual, ['quilombola', 'quilombolas']),
            'terras_indigenas': self._encontrar_camada_gpkg(gpkg_atual, ['indigena', 'indigenas', 'terrasindigenas']),
            'embargos_icmbio': self._encontrar_camada_gpkg(gpkg_atual, ['embargosicmbio', 'embargo_icmbio', 'icmbio']),
            'embargos_ibama': self._encontrar_camada_gpkg(gpkg_atual, ['embargosibama', 'embargo_ibama']),
            'car_amazonia': self._encontrar_camada_gpkg(gpkg_atual, ['caramazonia', 'car_amazonia', 'car']),
            'prodes': self._encontrar_camada_gpkg(gpkg_atual, ['prodes']),
            'fitofisionomia': self._encontrar_camada_gpkg(gpkg_atual, ['fitofisionomia', 'fitofisionomias', 'fito']),
            'rvn': self._encontrar_camada_gpkg(gpkg_atual, ['vegetacaonativa', 'vegetacao_nativa', 'rvn']),
            'amazonia_legal': self._encontrar_camada_gpkg(gpkg_atual, ['amazonialegal', 'amazonia_legal', 'amzl']),
            'municipios': self._encontrar_camada_gpkg(gpkg_atual, ['municipios', 'municipio', 'municípios']),
            'biomas': self._encontrar_camada_gpkg(gpkg_atual, ['biomas', 'bioma']),
            'areas_prioritarias': self._encontrar_camada_gpkg(gpkg_atual, ['areasprioritarias', 'areas_prioritarias', 'areaspriorit']),
        }
        
        # Verificar se camada de imóveis foi encontrada
        if not camadas_detectadas['imoveis']:
            return False
        
        print(f"[Config Padrão] Usando configuração padrão com GeoPackage: {os.path.basename(gpkg_atual)}")
        
        # Carregar lista padrão de CARs a remover
        lista_car_remover = self._carregar_lista_car_padrao()
        
        # Criar objeto de configuração com valores padrão
        self.config_processamento = ConfigProcessamento(
            gpkg_path=gpkg_atual,
            
            # Nomes das camadas (detectados dinamicamente)
            camada_imoveis=camadas_detectadas['imoveis'] or "imoveis_analisar",
            camada_cnfp=camadas_detectadas['cnfp'] or "cnfp",
            camada_ucs=camadas_detectadas['ucs'] or "ucs",
            camada_quilombolas=camadas_detectadas['quilombolas'] or "quilombolas",
            camada_terras_indigenas=camadas_detectadas['terras_indigenas'] or "terras_indigenas",
            camada_embargos_icmbio=camadas_detectadas['embargos_icmbio'] or "embargos_icmbio",
            camada_embargos_ibama=camadas_detectadas['embargos_ibama'] or "embargos_ibama",
            camada_car_amazonia=camadas_detectadas['car_amazonia'] or "car_amazonia",
            camada_prodes=camadas_detectadas['prodes'] or "prodes",
            camada_fitofisionomia=camadas_detectadas['fitofisionomia'] or "fitofisionomia",
            camada_rvn=camadas_detectadas['rvn'] or "vegetacao_nativa",
            camada_amazonia_legal=camadas_detectadas['amazonia_legal'] or "amazonia_legal",
            camada_municipios=camadas_detectadas['municipios'] or "municipios",
            camada_biomas=camadas_detectadas.get('biomas') or "biomas",
            camada_areas_prioritarias=camadas_detectadas.get('areas_prioritarias') or "areas_prioritarias_conservacao",
            
            # Valores padrão para tolerâncias
            tolerancia_fp_uc=0.05,
            tolerancia_sobreposicao_car=0.50,
            limite_modulos_fiscais=4,
            limite_prodes_1ha=1.0,
            limite_prodes_6ha=6.25,
            
            # RVN thresholds padrão
            threshold_rvn_floresta=0.50,
            threshold_rvn_cerrado=0.35,
            threshold_rvn_campos=0.20,
            threshold_rvn_floresta_f2=0.80,
            
            # Valores de pagamento padrão
            faixa1_limite_ha=60.0,
            faixa1_valor_ha=200.0,
            faixa2_limite_ha=20.0,
            faixa2_valor_ha=800.0,
            valor_minimo=1500.0,
            valor_fase1=1500.0,
            
            # Lista de CARs a remover
            lista_car_remover=lista_car_remover,
        )
        
        return True
    
    def _detectar_tipo_fonte_dados(self):
        """
        Detecta o tipo de fonte de dados baseado nas colunas disponíveis na camada de imóveis.
        
        Returns:
            tuple: (tipo_fonte, colunas_ausentes, mapeamento_colunas)
                - tipo_fonte: 'completa', 'geoserver', 'consulta_publica', ou 'desconhecida'
                - colunas_ausentes: lista de funcionalidades indisponíveis
                - mapeamento_colunas: dict com nomes padronizados -> nomes reais
        """
        gpkg_path = self.gpkg_path.text() if hasattr(self, 'gpkg_path') else ""
        if not gpkg_path or not os.path.exists(gpkg_path):
            return 'desconhecida', [], {}
        
        # Encontrar camada de imóveis
        camada_imoveis = self._encontrar_camada_gpkg(gpkg_path, ['imoveis', 'imovel', 'imóveis', 'imóvel'])
        if not camada_imoveis:
            return 'desconhecida', [], {}
        
        # Carregar camada e obter colunas
        uri = f"{gpkg_path}|layername={camada_imoveis}"
        layer = QgsVectorLayer(uri, camada_imoveis, "ogr")
        if not layer.isValid():
            return 'desconhecida', [], {}
        
        colunas = [f.name().lower() for f in layer.fields()]
        
        # Definir colunas características de cada fonte
        colunas_completa = {'n_do_car', 'cpf_cnpj', 'documentos', 'idt_car', 'nom_comple'}
        colunas_geoserver = {'cod_imovel', 'status_imo', 'm_fiscal'}
        colunas_consulta = {'cod_imovel', 'ind_status', 'mod_fiscal', 'des_condic', 'num_area'}
        
        # Detectar tipo
        colunas_set = set(colunas)
        
        if colunas_completa.intersection(colunas_set):
            # Fonte completa (Metabase)
            tipo = 'completa'
            mapeamento = {
                'cod_imovel': 'n_do_car',
                'area': 'area_imove',
                'status': 'status',
                'm_fiscal': 'modulo_f',
                'tipo': 'tipo_imove',
                'condicao': 'condicao',
                'cpf_cnpj': 'cpf_cnpj',
                'documentos': 'documentos',
            }
            ausentes = []
            
        elif 'status_imo' in colunas_set or 'm_fiscal' in colunas_set:
            # Fonte Geoserver
            tipo = 'geoserver'
            mapeamento = {
                'cod_imovel': 'cod_imovel',
                'area': 'area',
                'status': 'status_imo',
                'm_fiscal': 'm_fiscal',
                'tipo': 'tipo_imove',
                'condicao': 'condicao',
            }
            ausentes = [
                'Soma de módulos fiscais por CPF',
                'Verificação de embargos por CPF',
                'Checagem de nada consta no MPF',
                'Flexibilidade na análise do CNFP (documentos)',
            ]
            
        elif 'ind_status' in colunas_set or 'mod_fiscal' in colunas_set or 'des_condic' in colunas_set:
            # Fonte Consulta Pública
            tipo = 'consulta_publica'
            mapeamento = {
                'cod_imovel': 'cod_imovel',
                'area': 'num_area',
                'status': 'ind_status',
                'm_fiscal': 'mod_fiscal',
                'tipo': 'ind_tipo',
                'condicao': 'des_condic',
            }
            ausentes = [
                'Soma de módulos fiscais por CPF',
                'Verificação de embargos por CPF',
                'Checagem de nada consta no MPF',
                'Flexibilidade na análise do CNFP (documentos)',
            ]
        else:
            tipo = 'desconhecida'
            mapeamento = {}
            ausentes = ['Estrutura de dados não reconhecida']
        
        return tipo, ausentes, mapeamento
    
    def _obter_colunas_camada_imoveis(self):
        """Retorna a lista de nomes de campos da camada de imóveis no GeoPackage."""
        gpkg_path = self.gpkg_path.text() if hasattr(self, 'gpkg_path') else ""
        if not gpkg_path or not os.path.exists(gpkg_path):
            return []

        camada_imoveis = self._encontrar_camada_gpkg(gpkg_path, ['imoveis', 'imovel', 'imóveis', 'imóvel'])
        if not camada_imoveis:
            return []

        uri = f"{gpkg_path}|layername={camada_imoveis}"
        layer = QgsVectorLayer(uri, camada_imoveis, "ogr")
        if not layer.isValid():
            return []

        return [f.name() for f in layer.fields()]

    def _iniciar_processamento_elegiveis(self):
        """Inicia o processamento de elegibilidade."""
        # Se não tem configuração, criar uma padrão automaticamente
        if not self.config_processamento:
            self._criar_config_processamento_padrao()
        
        # Verificar se a configuração foi criada com sucesso
        if not self.config_processamento:
            QMessageBox.warning(
                self,
                "GeoPackage Necessário",
                "Carregue um GeoPackage de referência antes de processar.\n\n"
                "Use o botão 'Abrir' na seção 'Camadas de Referência'."
            )
            return
        
        # Obter colunas da camada de imóveis
        colunas_layer = self._obter_colunas_camada_imoveis()
        if not colunas_layer:
            QMessageBox.warning(
                self,
                "Camada de Imóveis",
                "Não foi possível localizar a camada de imóveis no GeoPackage.\n\n"
                "Verifique se o GeoPackage contém uma camada com nome 'imoveis'."
            )
            return

        # Colunas esperadas no formato padrão (Metabase/SICAR)
        from Plugin_FMais.ui.dialogs.mapeamento_colunas import COLUNAS_ESPERADAS, ALIASES_CONHECIDOS

        colunas_lower = {c.lower() for c in colunas_layer}
        colunas_encontradas = set()
        mapeamento_auto = {}

        for campo_interno, _, _ in COLUNAS_ESPERADAS:
            aliases = ALIASES_CONHECIDOS.get(campo_interno, [campo_interno])
            for alias in aliases:
                if alias.lower() in colunas_lower:
                    real_name = next(c for c in colunas_layer if c.lower() == alias.lower())
                    mapeamento_auto[campo_interno] = real_name
                    colunas_encontradas.add(campo_interno)
                    break

        todas_presentes = len(colunas_encontradas) == len(COLUNAS_ESPERADAS)

        if todas_presentes:
            mapeamento = mapeamento_auto
            titulo = "Análise Completa"
            msg = (
                "✅ Todas as colunas esperadas foram identificadas na camada de imóveis.\n\n"
                "Todas as análises serão realizadas:\n"
                "• Módulos fiscais (individual e soma por CPF)\n"
                "• Florestas Públicas TIPO B (com verificação de documentos)\n"
                "• Unidades de Conservação\n"
                "• Quilombolas e Terras Indígenas\n"
                "• Embargos IBAMA/ICMBio (área e CPF)\n"
                "• Sobreposição com outros CARs\n"
                "• PRODES\n"
                "• RVN e Fitofisionomia\n"
                "• Municípios Prioritários\n\n"
                "Deseja iniciar o processamento?"
            )
            reply = QMessageBox.question(
                self, titulo, msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply != QMessageBox.Yes:
                return
        else:
            faltantes = [
                desc for campo, desc, _ in COLUNAS_ESPERADAS
                if campo not in colunas_encontradas
            ]
            from Plugin_FMais.ui.dialogs.mapeamento_colunas import DialogoMapeamentoColunas
            dlg = DialogoMapeamentoColunas(colunas_layer, parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            mapeamento = dlg.get_mapeamento()

        # Determinar tipo de análise com base nos campos mapeados
        tem_cpf = "cpf_cnpj" in mapeamento
        tem_docs = "documentos" in mapeamento
        if tem_cpf and tem_docs:
            tipo_fonte = "completa"
        else:
            tipo_fonte = "parcial"

        self.config_processamento.tipo_fonte = tipo_fonte
        self.config_processamento.mapeamento_colunas = mapeamento
            
        # Importar módulo de processamento
        from Plugin_FMais.core.processamento_elegiveis import ProcessamentoElegiveis
        
        # Callbacks para UI
        def update_progress(value, max_val):
            self.processamento_progress.setMaximum(max_val)
            self.processamento_progress.setValue(value)
            QApplication.processEvents()
            
        self._log_mensagens = []
        self.btn_salvar_log.setEnabled(False)
        
        def update_log(msg):
            self.processamento_log.setText(msg)
            self._log_mensagens.append(msg)
            QApplication.processEvents()
            
        # Mostrar barra de progresso
        self.processamento_progress.setVisible(True)
        self.processamento_progress.setValue(0)
        self.btn_iniciar_processamento.setEnabled(False)
        self.btn_configurar_analise.setEnabled(False)
        
        try:
            # Criar processador
            processador = ProcessamentoElegiveis(
                config=self.config_processamento,
                callback_progress=update_progress,
                callback_log=update_log
            )
            
            # Executar processamento
            resultado = processador.executar()
            
            if resultado:
                # Salvar resultado no GeoPackage
                gpkg_path = self.config_processamento.gpkg_path
                if gpkg_path:
                    processador.salvar_resultado(f"{gpkg_path}|layername=elegiveis")
                    
                # Adicionar camada ao mapa
                QgsProject.instance().addMapLayer(resultado)
                
                # Armazenar referência para uso nos laudos
                self.camada_elegiveis = resultado
                self.gpkg_elegiveis = gpkg_path
                
                # Habilitar botões de laudos e reprocessar parecer
                self.btn_visualizar_laudos.setEnabled(True)
                self.btn_reprocessar_parecer.setEnabled(True)
                self.laudos_info.setText(f"✓ {resultado.featureCount()} imóveis disponíveis para análise")
                self.laudos_info.setStyleSheet(f"color: #27ae60; font-size: 10px;")
                
                self.processamento_status.setText("✓ Processamento concluído com sucesso!")
                self.processamento_status.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
                
                QMessageBox.information(
                    self,
                    "Processamento Concluído",
                    f"Análise de elegibilidade concluída!\n\n"
                    f"Total de imóveis analisados: {resultado.featureCount()}\n\n"
                    f"A camada 'elegiveis' foi adicionada ao mapa.\n"
                    f"Use os botões em 'Laudos' para visualizar ou gerar PDFs."
                )
            else:
                self.processamento_status.setText("❌ Erro no processamento")
                self.processamento_status.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
                
                QMessageBox.critical(
                    self,
                    "Erro no Processamento",
                    "Ocorreu um erro durante o processamento.\n"
                    "Verifique o console para mais detalhes."
                )
                
        except Exception as e:
            self.processamento_status.setText(f"❌ Erro: {str(e)[:50]}")
            self.processamento_status.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
            
            QMessageBox.critical(
                self,
                "Erro no Processamento",
                f"Ocorreu um erro durante o processamento:\n\n{str(e)}"
            )
            traceback.print_exc()
            
        finally:
            # Restaurar UI
            self.processamento_progress.setVisible(False)
            self.btn_iniciar_processamento.setEnabled(True)
            self.btn_configurar_analise.setEnabled(True)
            if self._log_mensagens:
                self.btn_salvar_log.setEnabled(True)
    
    # =========================================================================
    # REPROCESSAR PARECER
    # =========================================================================
    
    def _reprocessar_parecer(self):
        """Reprocessa apenas o julgamento e parecer final da camada de elegíveis."""
        camada = self._obter_camada_elegiveis()
        if not camada:
            QMessageBox.warning(
                self, "Sem Camada",
                "Nenhuma camada de elegíveis encontrada.\n"
                "Execute o processamento completo primeiro."
            )
            return
        
        # --- Obter mapeamento de colunas ---
        mapeamento = None
        
        # 1) Tentar usar o mapeamento já em memória (mesma sessão)
        if (hasattr(self, 'config_processamento') and self.config_processamento
                and getattr(self.config_processamento, 'mapeamento_colunas', None)):
            mapeamento = self.config_processamento.mapeamento_colunas
        
        # 2) Se não disponível, auto-detectar a partir das colunas da camada
        if not mapeamento:
            from Plugin_FMais.ui.dialogs.mapeamento_colunas import COLUNAS_ESPERADAS, ALIASES_CONHECIDOS
            colunas_layer = [f.name() for f in camada.fields()]
            colunas_lower = {c.lower() for c in colunas_layer}
            mapeamento_auto = {}
            
            for campo_interno, _, _ in COLUNAS_ESPERADAS:
                aliases = ALIASES_CONHECIDOS.get(campo_interno, [campo_interno])
                for alias in aliases:
                    if alias.lower() in colunas_lower:
                        real_name = next(c for c in colunas_layer if c.lower() == alias.lower())
                        mapeamento_auto[campo_interno] = real_name
                        break
            
            if len(mapeamento_auto) == len(COLUNAS_ESPERADAS):
                mapeamento = mapeamento_auto
            else:
                # 3) Auto-detecção incompleta: abrir diálogo de mapeamento
                from Plugin_FMais.ui.dialogs.mapeamento_colunas import DialogoMapeamentoColunas
                dlg = DialogoMapeamentoColunas(colunas_layer, parent=self)
                if dlg.exec_() != QDialog.Accepted:
                    return
                mapeamento = dlg.get_mapeamento()
        
        resposta = QMessageBox.question(
            self, "Reprocessar Parecer",
            f"Isso vai reler as colunas de critérios e recalcular:\n\n"
            f"  1) Elegivel_F1 e Elegivel_F2\n"
            f"  2) Elegibilidade (Fase 1 / Fase 2 / Inelegível)\n"
            f"  3) Parecer final (texto do laudo)\n\n"
            f"para os {camada.featureCount()} imóveis da camada '{camada.name()}'.\n\n"
            f"As etapas espaciais (áreas, sobreposições, RVN) NÃO serão refeitas.\n"
            f"Útil após editar manualmente colunas como prodes_6ha, embargo_ibama, etc.\n\n"
            f"Deseja continuar?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if resposta != QMessageBox.Yes:
            return
        
        from Plugin_FMais.core.processamento_elegiveis import ProcessamentoElegiveis
        
        def update_log(msg):
            self.processamento_log.setText(msg)
            self._log_mensagens.append(msg)
            QApplication.processEvents()
        
        self.btn_reprocessar_parecer.setEnabled(False)
        try:
            ok = ProcessamentoElegiveis.reprocessar_parecer(
                camada, callback_log=update_log, mapeamento=mapeamento
            )
            if ok:
                self.processamento_status.setText("✓ Parecer reprocessado com sucesso!")
                self.processamento_status.setStyleSheet("color: #27ae60; font-size: 10px; font-weight: bold;")
                if self._log_mensagens:
                    self.btn_salvar_log.setEnabled(True)
                camada.triggerRepaint()
            else:
                QMessageBox.warning(self, "Aviso", "Não foi possível reprocessar o parecer.\nVerifique os campos da camada.")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao reprocessar parecer:\n{e}")
            traceback.print_exc()
        finally:
            self.btn_reprocessar_parecer.setEnabled(True)
    
    # =========================================================================
    # SALVAR LOG
    # =========================================================================
    
    def _salvar_log_txt(self):
        """Salva o log do processamento em um arquivo .txt."""
        if not self._log_mensagens:
            QMessageBox.information(self, "Log Vazio", "Nenhum log de processamento disponível.")
            return
        
        default_name = f"log_processamento_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar Log do Processamento", default_name,
            "Arquivos de Texto (*.txt);;Todos os Arquivos (*)"
        )
        if not path:
            return
        
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"Log de Processamento - Floresta+ Amazônia\n")
                f.write(f"Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")
                for msg in self._log_mensagens:
                    f.write(msg + "\n")
            QMessageBox.information(self, "Log Salvo", f"Log salvo com sucesso em:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Erro ao salvar log:\n{e}")
    
    # =========================================================================
    # FUNÇÕES DE LAUDOS
    # =========================================================================
    
    def _abrir_visualizador_laudos(self):
        """Abre a janela de visualização de resultados dos imóveis processados."""
        camada = self._obter_camada_elegiveis()
        if not camada:
            return
        
        # Obter gpkg_path
        gpkg_path = getattr(self, 'gpkg_elegiveis', None)
        
        # Verificar se Planet está logado e tem mosaico selecionado
        planet_url = None
        try:
            from Plugin_FMais.core.planet_client import planet_client
            if planet_client.is_logged_in and hasattr(self, 'planet_basemap_urls'):
                planet_url = self.planet_basemap_urls.get("Planet", None)
                if planet_url:
                    print(f"[Laudos] Usando Planet como basemap")
        except Exception as e:
            print(f"[Laudos] Planet não disponível: {e}")
        
        # Abrir diálogo de visualização
        from Plugin_FMais.ui.dialogs.visualizador_laudos import VisualizadorLaudosDialog
        dialog = VisualizadorLaudosDialog(camada, gpkg_path, planet_url, self)
        dialog.exec_()
    
    def _abrir_gerador_laudos(self):
        """Abre a janela para geração de laudos em PDF."""
        camada = self._obter_camada_elegiveis()
        if not camada:
            return
        
        # Verificar se Planet esta logado e tem mosaico selecionado
        planet_url = None
        try:
            from Plugin_FMais.core.planet_client import planet_client
            if planet_client.is_logged_in and hasattr(self, 'planet_basemap_urls'):
                planet_url = self.planet_basemap_urls.get("Planet", None)
                if planet_url:
                    print(f"[Laudos] Usando Planet como basemap")
        except Exception as e:
            print(f"[Laudos] Planet nao disponivel: {e}")
        
        # Abrir dialogo de geracao
        from Plugin_FMais.ui.dialogs.gerador_laudos import GeradorLaudosDialog
        dialog = GeradorLaudosDialog(
            camada, 
            getattr(self, 'gpkg_elegiveis', None),
            planet_url,
            self
        )
        dialog.exec_()
    
    def _obter_camada_elegiveis(self):
        """
        Obtém a camada de elegíveis de várias fontes possíveis.
        Retorna a camada ou None se não encontrar.
        """
        # 1. Verificar se já temos a camada em memória e é válida
        if hasattr(self, 'camada_elegiveis') and self.camada_elegiveis:
            if self.camada_elegiveis.isValid():
                return self.camada_elegiveis
        
        # 2. Tentar encontrar no projeto QGIS
        layers = QgsProject.instance().mapLayersByName('elegiveis')
        if layers and layers[0].isValid():
            self.camada_elegiveis = layers[0]
            return self.camada_elegiveis
        
        # 3. Tentar carregar do GeoPackage atual
        gpkg_path = self.gpkg_path.text() if hasattr(self, 'gpkg_path') else ""
        if gpkg_path and os.path.exists(gpkg_path):
            uri = f"{gpkg_path}|layername=elegiveis"
            layer = QgsVectorLayer(uri, "elegiveis", "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                self.camada_elegiveis = layer
                self.gpkg_elegiveis = gpkg_path
                return layer
        
        # 4. Tentar do gpkg_elegiveis salvo
        if hasattr(self, 'gpkg_elegiveis') and self.gpkg_elegiveis and os.path.exists(self.gpkg_elegiveis):
            uri = f"{self.gpkg_elegiveis}|layername=elegiveis"
            layer = QgsVectorLayer(uri, "elegiveis", "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                self.camada_elegiveis = layer
                return layer
        
        # Não encontrou em nenhum lugar
        QMessageBox.warning(
            self,
            "Camada não encontrada",
            "Não há camada de resultados processados disponível.\n\n"
            "Execute o processamento de elegibilidade primeiro ou\n"
            "carregue um GeoPackage que contenha a camada 'elegiveis'."
        )
        return None
    
    def _get_amostras_csv_path(self):
        """Retorna o caminho do arquivo CSV com amostras de treinamento."""
        # Se foi selecionado manualmente, usar esse
        if hasattr(self, 'amostras_csv_path') and self.amostras_csv_path and os.path.exists(self.amostras_csv_path):
            return self.amostras_csv_path
        
        # Fallback: procurar em caminhos padrão
        possible_paths = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'amostras_todas_quads_AMZL_2.csv'),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'amostras_todas_quads_AMZL_2.csv'),
            r'D:\Aut_Elegiveis_F\ToolboxF+\amostras_todas_quads_AMZL_2.csv',
            r'D:\Aut_Elegiveis_F\ToolboxF+\Plugin_FMais\config\amostras_todas_quads_AMZL_2.csv',
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def _recortar_por_imoveis(self, classified_gpkg_path, quad, temp_dir):
        """
        Recorta a classificação vetorial pelos imóveis sobrepostos.
        Recebe um GeoPackage do classificador_local.py (idealmente em EPSG:4674).
        Se estiver em Pseudo-Mercator, reprojeta usando QGIS.
        Retorna camada vetorial com vegetação recortada.
        """
        from qgis import processing
        
        quad_id = quad.get('id', '')
        
        try:
            # Carregar classificação vetorial do GeoPackage
            veg_layer = QgsVectorLayer(classified_gpkg_path, "classificacao", "ogr")
            if not veg_layer.isValid():
                raise Exception(f"Classificação vetorial inválida: {classified_gpkg_path}")
            
            print(f"Classificação carregada: {veg_layer.featureCount()} polígonos")
            
            # Verificar CRS da classificação
            veg_crs = veg_layer.crs()
            veg_crs_str = veg_crs.toWkt().upper() if veg_crs.isValid() else ""
            veg_crs_id = veg_crs.authid()
            
            print(f"CRS da classificação: {veg_crs_id}")
            
            # Verificar se precisa reprojetar
            # Se o CRS não é EPSG:4674 ou é LOCAL_CS/Pseudo-Mercator, reprojetar
            precisa_reprojetar = False
            if not veg_crs.isValid():
                precisa_reprojetar = True
            elif 'LOCAL_CS' in veg_crs_str or 'PSEUDO' in veg_crs_str or 'MERCATOR' in veg_crs_str:
                precisa_reprojetar = True
            elif veg_crs_id != "EPSG:4674":
                precisa_reprojetar = True
            
            if precisa_reprojetar:
                print(f"⚠ Classificação não está em EPSG:4674, reprojetando via QGIS...")
                
                # Se CRS é inválido ou LOCAL_CS, atribuir EPSG:3857 primeiro
                if not veg_crs.isValid() or 'LOCAL_CS' in veg_crs_str:
                    print(f"  → Atribuindo EPSG:3857 à camada...")
                    temp_assigned = os.path.join(temp_dir, f"{quad_id}_assigned.gpkg")
                    try:
                        result = processing.run("native:assignprojection", {
                            'INPUT': veg_layer,
                            'CRS': QgsCoordinateReferenceSystem('EPSG:3857'),
                            'OUTPUT': temp_assigned
                        })
                        veg_layer = QgsVectorLayer(result['OUTPUT'], "classificacao_assigned", "ogr")
                    except Exception as e:
                        print(f"  ⚠ Erro ao atribuir CRS: {e}")
                
                # Agora reprojetar para EPSG:4674
                print(f"  → Reprojetando para EPSG:4674...")
                temp_reproj = os.path.join(temp_dir, f"{quad_id}_reprojected.gpkg")
                try:
                    result = processing.run("native:reprojectlayer", {
                        'INPUT': veg_layer,
                        'TARGET_CRS': QgsCoordinateReferenceSystem('EPSG:4674'),
                        'OUTPUT': temp_reproj
                    })
                    veg_layer = QgsVectorLayer(result['OUTPUT'], "classificacao_reproj", "ogr")
                    print(f"  ✓ Reprojetado com sucesso!")
                except Exception as e:
                    print(f"  ⚠ Erro na reprojeção QGIS: {e}")
            
            # Carregar imóveis
            imoveis_layer = self._get_imoveis_layer()
            if not imoveis_layer:
                raise Exception("Camada de imóveis não encontrada")
            
            print(f"CRS dos imóveis: {imoveis_layer.crs().authid()}")
            
            # Usar CRS de trabalho (EPSG:4674)
            target_crs = QgsCoordinateReferenceSystem("EPSG:4674")
            
            # Obter geometria do quad (em EPSG:4326, converter para CRS da classificação)
            bbox = quad.get('bbox', [])
            if len(bbox) != 4:
                raise Exception("Bbox do quad inválido")
            
            # Criar geometria do quad e transformar para o CRS correto
            quad_geom = QgsGeometry.fromRect(QgsRectangle(bbox[0], bbox[1], bbox[2], bbox[3]))
            
            # Transformar bbox de EPSG:4326 para o CRS da classificação
            crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
            if target_crs.authid() != "EPSG:4326":
                transform = QgsCoordinateTransform(crs_4326, target_crs, QgsProject.instance())
                quad_geom.transform(transform)
            
            # Selecionar imóveis que intersectam o quad
            # Transformar geometria do imóvel para o CRS da classificação se necessário
            imoveis_no_quad = []
            imoveis_crs = imoveis_layer.crs()
            
            for feat in imoveis_layer.getFeatures():
                imovel_geom = QgsGeometry(feat.geometry())
                
                # Transformar imóvel para CRS da classificação se diferente
                if imoveis_crs.authid() != target_crs.authid():
                    transform_imovel = QgsCoordinateTransform(imoveis_crs, target_crs, QgsProject.instance())
                    imovel_geom.transform(transform_imovel)
                
                if imovel_geom.intersects(quad_geom):
                    # Guardar geometria já transformada
                    imoveis_no_quad.append((feat, imovel_geom))
            
            if not imoveis_no_quad:
                print(f"Nenhum imóvel sobrepõe o quad {quad_id}")
                return None
            
            print(f"Imóveis sobrepostos ao quad: {len(imoveis_no_quad)}")
            
            # UNIR TODOS OS IMÓVEIS EM UMA ÚNICA GEOMETRIA
            # Isso evita duplicações quando imóveis se sobrepõem
            print(f"Unindo geometrias dos imóveis...")
            imoveis_unidos = None
            for _, imovel_geom in imoveis_no_quad:
                if imoveis_unidos is None:
                    imoveis_unidos = QgsGeometry(imovel_geom)
                else:
                    imoveis_unidos = imoveis_unidos.combine(imovel_geom)
            
            if imoveis_unidos is None or imoveis_unidos.isEmpty():
                print("⚠ Falha ao unir geometrias dos imóveis")
                return None
            
            print(f"✓ Geometria dos imóveis unida")
            
            # Criar camada de resultado no CRS da classificação
            result_layer = QgsVectorLayer(f"Polygon?crs={target_crs.authid()}", "Vegetacao_Nativa", "memory")
            provider = result_layer.dataProvider()
            
            # Adicionar campos
            fields = QgsFields()
            fields.append(QgsField("quad_id", QVariant.String))
            fields.append(QgsField("mosaico", QVariant.String))
            fields.append(QgsField("classe", QVariant.Int))
            fields.append(QgsField("area_ha", QVariant.Double))
            provider.addAttributes(fields)
            result_layer.updateFields()
            
            # Obter features de vegetação (classe = 'veg' ou valor = 1)
            veg_features = []
            total_features = veg_layer.featureCount()
            print(f"Total de features na classificação (reprojetada): {total_features}")
            
            for feat in veg_layer.getFeatures():
                try:
                    classe = feat['classe']
                    # O classificador_local.py usa 'veg' e 'nveg' como strings
                    if str(classe).lower() == 'veg' or classe == 1:
                        veg_features.append(feat)
                except Exception as e:
                    print(f"Erro ao ler feature: {e}")
                    pass
            
            print(f"Polígonos de VEGETAÇÃO filtrados: {len(veg_features)}")
            
            if not veg_features:
                print("⚠ Nenhuma vegetação encontrada no quad após filtro!")
                return None
            
            # Configurar transformação para calcular área em metros
            # De EPSG:4674 para EPSG:5880 (SIRGAS 2000 / Brazil Polyconic - métrico)
            crs_metrico = QgsCoordinateReferenceSystem("EPSG:5880")
            transform_context = QgsProject.instance().transformContext()
            
            count_added = 0
            
            # RECORTAR VEGETAÇÃO COM A GEOMETRIA UNIDA DOS IMÓVEIS
            # Assim não há duplicação mesmo com imóveis sobrepostos
            for veg_feat in veg_features:
                veg_geom = veg_feat.geometry()
                
                if veg_geom.intersects(imoveis_unidos):
                    intersect = veg_geom.intersection(imoveis_unidos)
                    
                    if intersect and not intersect.isEmpty():
                        feat = QgsFeature()
                        feat.setGeometry(intersect)
                        
                        # Calcular área em hectares usando projeção métrica
                        try:
                            transform = QgsCoordinateTransform(target_crs, crs_metrico, transform_context)
                            geom_projected = QgsGeometry(intersect)
                            geom_projected.transform(transform)
                            area_m2 = geom_projected.area()
                            area_ha = area_m2 / 10000.0  # m² para hectares
                        except Exception as e:
                            print(f"Erro no cálculo de área: {e}")
                            # Fallback: usar área do calculator do QGIS
                            area_ha = intersect.area() / 10000.0
                        
                        feat.setAttributes([
                            quad_id,
                            getattr(self, 'current_planet_mosaic', {}).get('name', ''),
                            1,  # Vegetação
                            round(area_ha, 4)
                        ])
                        provider.addFeature(feat)
                        count_added += 1
            
            print(f"✓ Polígonos de vegetação recortados: {count_added}")
            result_layer.updateExtents()
            
            return result_layer
            
        except Exception as e:
            print(f"Erro no recorte: {e}")
            return None
    
    def _exibir_imoveis_contorno(self):
        """Exibe a camada de imóveis no mapa apenas com contorno (sem preenchimento)."""
        try:
            gpkg_path = self.gpkg_path.text()
            if not gpkg_path or not os.path.exists(gpkg_path):
                return
            
            layer_names = self._get_gpkg_layer_names(gpkg_path)
            
            # Procurar camada de imóveis
            imoveis_name = None
            for name in layer_names:
                normalized = name.lower().replace(' ', '').replace('_', '')
                if 'imoveis' in normalized or 'imovel' in normalized:
                    imoveis_name = name
                    break
            
            if not imoveis_name:
                return
            
            # Remover camada anterior de imóveis se existir
            if hasattr(self, 'imoveis_map_layer') and self.imoveis_map_layer:
                try:
                    current_layers = list(self.map_canvas.layers())
                    current_layers = [l for l in current_layers if l.id() != self.imoveis_map_layer.id()]
                    self.map_canvas.setLayers(current_layers)
                except:
                    pass
            
            # Carregar camada de imóveis
            uri = f"{gpkg_path}|layername={imoveis_name}"
            imoveis_layer = QgsVectorLayer(uri, "Imóveis a Analisar", "ogr")
            
            if not imoveis_layer.isValid():
                return
            
            # Estilo: APENAS contorno vermelho, sem preenchimento
            symbol = QgsFillSymbol.createSimple({
                'color': '0,0,0,0',  # Totalmente transparente (sem preenchimento)
                'outline_color': '255,0,0,255',  # Vermelho
                'outline_width': '1'  # Metade da espessura anterior
            })
            imoveis_layer.renderer().setSymbol(symbol)
            
            # Guardar referência
            self.imoveis_map_layer = imoveis_layer
            
            # Adicionar ao mapa (abaixo dos quads)
            current_layers = list(self.map_canvas.layers())
            
            # Inserir depois dos quads e vegetação
            insert_pos = len(current_layers)
            for i, layer in enumerate(current_layers):
                if hasattr(self, 'quads_layer') and self.quads_layer and layer.id() == self.quads_layer.id():
                    insert_pos = i + 1
                    break
            
            current_layers.insert(insert_pos, imoveis_layer)
            self.map_canvas.setLayers(current_layers)
            self.map_canvas.refresh()
            
            print(f"Imóveis exibidos no mapa: {imoveis_layer.featureCount()} polígonos")
            
        except Exception as e:
            print(f"Erro ao exibir imóveis: {e}")
    
    def _get_imoveis_layer(self):
        """Obtém a camada de imóveis a analisar."""
        gpkg_path = self.gpkg_path.text()
        
        if gpkg_path and os.path.exists(gpkg_path):
            layer_names = self._get_gpkg_layer_names(gpkg_path)
            
            # Procurar camada de imóveis
            for name in layer_names:
                normalized = name.lower().replace(' ', '').replace('_', '')
                if 'imoveis' in normalized or 'imovel' in normalized:
                    uri = f"{gpkg_path}|layername={name}"
                    layer = QgsVectorLayer(uri, name, "ogr")
                    if layer.isValid():
                        return layer
        
        return None
    
    def _salvar_vegetacao_nativa(self, veg_layer, quad_id, mosaic_name):
        """Salva a vegetação na camada Vegetação Nativa do GeoPackage."""
        gpkg_path = self.gpkg_path.text()
        
        if not gpkg_path:
            return False
        
        try:
            layer_name = "vegetacao_nativa"
            
            # Verificar se camada já existe
            existing_layers = self._get_gpkg_layer_names(gpkg_path)
            layer_exists = any('vegetacao' in l.lower().replace('_', '').replace(' ', '') 
                              for l in existing_layers)
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.layerName = layer_name
            options.fileEncoding = "UTF-8"
            
            if layer_exists:
                # Append
                options.actionOnExistingFile = QgsVectorFileWriter.AppendToLayerNoNewFields
            else:
                # Criar nova
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                veg_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] == QgsVectorFileWriter.NoError:
                print(f"Vegetação salva com sucesso: {veg_layer.featureCount()} polígonos")
                
                # Atualizar tabela de camadas
                self._populate_layers_from_config()
                
                return True
            else:
                print(f"Erro ao salvar: {error[1]}")
                return False
                
        except Exception as e:
            print(f"Erro ao salvar vegetação: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _exibir_vegetacao_nativa_no_mapa(self, gpkg_path, layer_name):
        """Exibe a camada de vegetação nativa no mapa com cor verde."""
        try:
            # Carregar camada do GeoPackage
            uri = f"{gpkg_path}|layername={layer_name}"
            veg_map_layer = QgsVectorLayer(uri, "Vegetação Nativa", "ogr")
            
            if not veg_map_layer.isValid():
                print("Camada de vegetação não válida para exibição")
                return
            
            # Estilo: verde sólido
            symbol = QgsFillSymbol.createSimple({
                'color': '34,139,34,180',  # Verde floresta semi-transparente
                'outline_color': '0,100,0,255',  # Verde escuro
                'outline_width': '0.5'
            })
            veg_map_layer.renderer().setSymbol(symbol)
            
            # Remover camada anterior de vegetação se existir
            if hasattr(self, 'veg_nativa_map_layer') and self.veg_nativa_map_layer:
                try:
                    current_layers = list(self.map_canvas.layers())
                    current_layers = [l for l in current_layers if l.id() != self.veg_nativa_map_layer.id()]
                    self.map_canvas.setLayers(current_layers)
                except:
                    pass
            
            # Guardar referência
            self.veg_nativa_map_layer = veg_map_layer
            
            # Adicionar ao mapa (abaixo dos quads, acima do basemap)
            current_layers = list(self.map_canvas.layers())
            
            # Encontrar posição: após quads se existir
            insert_pos = len(current_layers)
            for i, layer in enumerate(current_layers):
                if hasattr(self, 'quads_layer') and self.quads_layer and layer.id() == self.quads_layer.id():
                    insert_pos = i + 1
                    break
            
            current_layers.insert(insert_pos, veg_map_layer)
            self.map_canvas.setLayers(current_layers)
            self.map_canvas.refresh()
            
            print(f"Vegetação nativa exibida no mapa: {veg_map_layer.featureCount()} polígonos")
            
        except Exception as e:
            print(f"Erro ao exibir vegetação no mapa: {e}")
    
    def _ligar_vegetacao_e_zoom_quad(self, quad):
        """Liga a camada de vegetação nativa (se não estiver) e dá zoom no quad processado."""
        try:
            # 1. SEMPRE ligar/atualizar a camada de vegetação
            self._ligar_camada_vegetacao_nativa()
            
            # 2. Dar zoom no quad processado
            bbox = quad.get('bbox', [])
            if len(bbox) == 4:
                # bbox = [lon_min, lat_min, lon_max, lat_max] em EPSG:4326
                rect = QgsRectangle(bbox[0], bbox[1], bbox[2], bbox[3])
                
                # Transformar para o CRS do canvas
                canvas_crs = self.map_canvas.mapSettings().destinationCrs()
                crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
                
                if canvas_crs.authid() != "EPSG:4326":
                    transform = QgsCoordinateTransform(crs_4326, canvas_crs, QgsProject.instance())
                    rect = transform.transformBoundingBox(rect)
                
                # Expandir um pouco para dar margem
                rect.scale(1.1)
                
                # Aplicar zoom
                self.map_canvas.setExtent(rect)
                self.map_canvas.refresh()
                print(f"Zoom aplicado ao quad {quad.get('id', '')}")
                
        except Exception as e:
            print(f"Erro ao ligar vegetação/zoom: {e}")
    
    def _ligar_camada_vegetacao_nativa(self):
        """Liga a camada de Vegetação Nativa na tabela e no mapa."""
        try:
            print("\n=== Tentando ligar Vegetação Nativa ===")
            
            # Procurar a camada na tabela por várias variações do nome
            layer_keys_to_try = ['rvn', 'vegetacao_nativa', 'remanescente', 'vegetacao']
            layer_found = False
            
            # Debug: listar camadas na tabela
            print(f"Camadas na tabela ({self.layers_table.rowCount()} linhas):")
            for i in range(self.layers_table.rowCount()):
                item = self.layers_table.item(i, 0)
                if item:
                    print(f"  [{i}] key='{item.data(Qt.UserRole)}' text='{item.text()}'")
            
            for i in range(self.layers_table.rowCount()):
                item = self.layers_table.item(i, 0)
                if not item:
                    continue
                
                row_key = item.data(Qt.UserRole)
                item_text = item.text().lower() if item.text() else ""
                
                # Verificar se é a camada de vegetação
                is_veg_layer = False
                if row_key:
                    row_key_lower = str(row_key).lower()
                    if any(k in row_key_lower for k in layer_keys_to_try):
                        is_veg_layer = True
                if 'vegeta' in item_text or 'nativa' in item_text or 'rvn' in item_text:
                    is_veg_layer = True
                
                if is_veg_layer:
                    layer_found = True
                    print(f"✓ Encontrada camada: key='{row_key}' text='{item.text()}'")
                    
                    # Encontrar o widget container da coluna 2
                    toggle_widget = self.layers_table.cellWidget(i, 2)
                    toggle_btn = None
                    
                    # O toggle_widget é um QWidget container, precisamos encontrar o QPushButton dentro dele
                    if toggle_widget:
                        for child in toggle_widget.children():
                            if isinstance(child, QPushButton) and child.isCheckable():
                                toggle_btn = child
                                break
                    
                    if toggle_btn:
                        print(f"  Toggle encontrado, checked={toggle_btn.isChecked()}")
                        # Verificar se já está ligado
                        if not toggle_btn.isChecked():
                            print("  → Ligando camada...")
                            toggle_btn.setChecked(True)
                            # Disparar o toggle manualmente
                            if row_key:
                                self._toggle_layer_visibility(row_key, True, toggle_btn)
                        else:
                            # Já está ligada, apenas recarregar para atualizar dados
                            print("  → Atualizando camada (já estava ligada)...")
                            if row_key:
                                self._reload_layer_on_map(row_key)
                    else:
                        print("  ⚠ Toggle não encontrado, ligando diretamente...")
                        # Tentar ligar diretamente
                        if row_key:
                            self._toggle_layer_visibility(row_key, True, None)
                    
                    # Atualizar o estado interno
                    if row_key:
                        self.layer_visibility[row_key] = True
                    
                    break
            
            if not layer_found:
                print("⚠ Camada de Vegetação Nativa não encontrada na tabela")
                # Tentar carregar diretamente do GeoPackage
                self._carregar_vegetacao_direto()
                
        except Exception as e:
            import traceback
            print(f"Erro ao ligar camada vegetação: {e}")
            traceback.print_exc()
    
    def _carregar_vegetacao_direto(self):
        """Carrega a camada de vegetação diretamente do GeoPackage."""
        try:
            gpkg_path = self.gpkg_path.text()
            print(f"Tentando carregar vegetação direto do GPKG: {gpkg_path}")
            
            if not gpkg_path or not os.path.exists(gpkg_path):
                print("  ⚠ GPKG não encontrado")
                return
            
            # Procurar camada de vegetação no GeoPackage
            layer_names = self._get_gpkg_layer_names(gpkg_path)
            print(f"  Camadas no GPKG: {layer_names}")
            
            veg_name = None
            for name in layer_names:
                name_lower = name.lower()
                if 'vegeta' in name_lower or 'nativa' in name_lower or 'rvn' in name_lower or 'remanescente' in name_lower:
                    veg_name = name
                    break
            
            if not veg_name:
                print("  ⚠ Camada de vegetação não existe no GeoPackage")
                return
            
            print(f"  Encontrada camada: {veg_name}")
            
            # Remover camada existente com mesmo nome
            for existing_layer in list(QgsProject.instance().mapLayers().values()):
                if existing_layer.name() == veg_name or 'Vegeta' in existing_layer.name():
                    print(f"  Removendo camada existente: {existing_layer.name()}")
                    QgsProject.instance().removeMapLayer(existing_layer.id())
            
            # Carregar camada
            uri = f"{gpkg_path}|layername={veg_name}"
            layer = QgsVectorLayer(uri, veg_name, "ogr")
            
            if layer.isValid():
                # Aplicar estilo verde transparente SEM contorno
                symbol = QgsFillSymbol.createSimple({
                    'color': '34,139,34,120',
                    'outline_style': 'no'
                })
                layer.renderer().setSymbol(symbol)
                layer.triggerRepaint()
                
                # Adicionar ao projeto e canvas
                QgsProject.instance().addMapLayer(layer, False)
                current_layers = list(self.map_canvas.layers())
                current_layers.insert(0, layer)
                self.map_canvas.setLayers(current_layers)
                self.map_canvas.refresh()
                
                print(f"✓ Vegetação Nativa carregada diretamente: {layer.featureCount()} polígonos")
            else:
                print(f"  ⚠ Camada inválida: {uri}")
                
        except Exception as e:
            import traceback
            print(f"Erro ao carregar vegetação direto: {e}")
            traceback.print_exc()
    
    def _reload_layer_on_map(self, layer_key):
        """Recarrega uma camada no mapa (para atualizar dados)."""
        try:
            gpkg_path = self.gpkg_path.text()
            if not gpkg_path:
                return
            
            layer_info = self.config.get('camadas_referencia', {}).get(layer_key, {})
            display_name = layer_info.get('nome', layer_key)
            if 'Remanescente' in display_name or layer_key == 'rvn':
                display_name = "Vegetacao Nativa"
            
            # Remover camada existente
            for layer in QgsProject.instance().mapLayers().values():
                if layer.name() == display_name or display_name in layer.name():
                    QgsProject.instance().removeMapLayer(layer.id())
            
            # Recarregar
            self._load_layer_from_gpkg(layer_key)
            
        except Exception as e:
            print(f"Erro ao recarregar camada: {e}")
    
    def _atualizar_item_quad(self, quad_id, status):
        """Atualiza o visual de um item na lista de quads."""
        for i in range(self.quads_list.count()):
            item = self.quads_list.item(i)
            quad = item.data(Qt.UserRole)
            
            if quad and quad.get('id') == quad_id:
                item.setData(Qt.UserRole + 1, status)
                
                if status == 'processado':
                    item.setText(f"✅ {quad_id}")
                    item.setBackground(QColor(200, 255, 200))
                elif status == 'erro':
                    item.setText(f"❌ {quad_id}")
                    item.setBackground(QColor(255, 200, 200))
                elif status == 'processando':
                    item.setText(f"⏳ {quad_id}")
                    item.setBackground(QColor(255, 255, 200))
                break
    
    def _limpar_temporarios(self, temp_dir):
        """Remove arquivos temporários."""
        import gc
        gc.collect()  # Liberar referências
        
        try:
            if os.path.exists(temp_dir):
                # Tentar remover arquivos um por um
                for arquivo in os.listdir(temp_dir):
                    caminho = os.path.join(temp_dir, arquivo)
                    try:
                        if os.path.isfile(caminho):
                            os.remove(caminho)
                    except Exception:
                        pass  # Ignorar arquivos em uso
                
                # Tentar remover a pasta
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass  # Ignorar se não conseguir
        except Exception as e:
            # Silencioso - arquivos em uso serão limpos pelo SO depois
            pass
    
    def _process_rvn(self):
        """Processa o mapeamento de vegetação nativa (legado)."""
        QMessageBox.information(
            self,
            "Mapeamento RVN",
            "Use o grupo 'Mapear Vegetação' para:\n\n"
            "1. Conectar ao Planet\n"
            "2. Buscar Quads dos Imóveis\n"
            "3. Processar Seleção ou Todos\n\n"
            "O sistema processará automaticamente cada quad."
        )
    
    def _validate_database(self):
        """Valida se todas as camadas obrigatórias estão carregadas."""
        camadas = self.config.get('camadas_referencia', {})
        missing = []
        
        for key, info in camadas.items():
            if info.get('obrigatoria', False):
                if key not in self.loaded_layers:
                    missing.append(info.get('nome', key))
        
        if missing:
            QMessageBox.warning(
                self,
                "Validação",
                f"Camadas obrigatorias pendentes:\n\n- " + 
                "\n- ".join(missing)
            )
        else:
            QMessageBox.information(
                self,
                "Validação",
                "Todas as camadas obrigatorias estao carregadas!"
            )
    
    def _export_to_gpkg(self):
        """Exporta todas as camadas carregadas para o GeoPackage."""
        gpkg_path = self.gpkg_path.text()
        if not gpkg_path:
            QMessageBox.warning(
                self,
                "Aviso",
                "Defina o caminho do GeoPackage de saída primeiro."
            )
            return
        
        if not self.loaded_layers:
            QMessageBox.warning(
                self,
                "Aviso",
                "Nenhuma camada foi carregada para exportar."
            )
            return
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(self.loaded_layers))
        
        exported = 0
        errors = []
        
        for i, (key, layer) in enumerate(self.loaded_layers.items()):
            self.progress_bar.setValue(i)
            self._update_status(f"Exportando {key}...")
            
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = key
            
            if os.path.exists(gpkg_path):
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] == QgsVectorFileWriter.NoError:
                exported += 1
            else:
                errors.append(f"{key}: {error[1]}")
        
        self.progress_bar.setVisible(False)
        
        if errors:
            QMessageBox.warning(
                self,
                "Exportação",
                f"Exportadas {exported} camadas.\n\n"
                f"Erros:\n" + "\n".join(errors)
            )
        else:
            QMessageBox.information(
                self,
                "Exportação",
                f"{exported} camadas exportadas com sucesso para:\n{gpkg_path}"
            )
        
        self._update_status(f"Exportação concluída: {exported} camadas")
    
    def _change_basemap(self, basemap_name):
        """Muda o mapa base."""
        if not basemap_name:
            return
        
        url = None
        
        # Verificar se é um mosaico Planet
        if hasattr(self, 'planet_basemap_urls') and basemap_name in self.planet_basemap_urls:
            url = self.planet_basemap_urls[basemap_name]
        else:
            # Procurar nos basemaps do config
            basemaps = self.config.get('basemaps', {})
            for key, info in basemaps.items():
                if info.get('nome') == basemap_name:
                    url = info.get('url', '')
                    break
        
        if not url:
            self._update_status(f"URL não encontrada para: {basemap_name}")
            return
        
        # Criar camada XYZ
        uri = f"type=xyz&zmin=0&zmax=19&url={url}"
        layer = QgsRasterLayer(uri, basemap_name, "wms")
        
        if layer.isValid():
            # Remove basemap anterior se existir
            if hasattr(self, 'basemap_layer') and self.basemap_layer:
                try:
                    layers = [l for l in self.map_canvas.layers() 
                             if l.id() != self.basemap_layer.id()]
                    self.map_canvas.setLayers(layers)
                except:
                    pass
            
            self.basemap_layer = layer
            layers = list(self.map_canvas.layers())
            layers.append(layer)  # Adiciona no fundo
            self.map_canvas.setLayers(layers)
            self.map_canvas.refresh()
            self._update_status(f"Basemap: {basemap_name}")
        else:
            self._update_status(f"Erro ao carregar basemap: {basemap_name}")
    
    def _zoom_in(self):
        """Aproxima o mapa."""
        self.map_canvas.zoomIn()
    
    def _zoom_out(self):
        """Afasta o mapa."""
        self.map_canvas.zoomOut()
    
    def _zoom_to_full_extent(self):
        """Zoom para extensão total."""
        if self.map_canvas.layers():
            self.map_canvas.zoomToFullExtent()
    
    def _zoom_to_brasil(self):
        """Centraliza o mapa no Brasil/Amazonia Legal."""
        self._zoom_to_amazonia_legal()
    
    def _zoom_to_amazonia_legal(self):
        """Centraliza o mapa na Amazônia Legal (foco principal do plugin)."""
        # Extent aproximado da Amazônia Legal
        # Lon: -74 a -44 | Lat: -18 a 5
        amzl_extent = QgsRectangle(-74.0, -18.0, -44.0, 5.5)
        self.map_canvas.setExtent(amzl_extent)
        self.map_canvas.refresh()
    
    def _load_default_basemap(self):
        """Carrega OpenStreetMap como basemap padrao."""
        try:
            # URL do OpenStreetMap
            osm_url = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            uri = f"type=xyz&zmin=0&zmax=19&url={osm_url}"
            
            osm_layer = QgsRasterLayer(uri, "OpenStreetMap", "wms")
            
            if osm_layer.isValid():
                self.basemap_layer = osm_layer
                self.map_canvas.setLayers([osm_layer])
                self.map_canvas.refresh()
        except Exception as e:
            print(f"Erro ao carregar basemap: {e}")
    
    def _add_layer_to_canvas(self, layer):
        """Adiciona uma camada ao canvas do mapa."""
        if layer and layer.isValid():
            layers = self.map_canvas.layers()
            layers.insert(0, layer)
            self.map_canvas.setLayers(layers)
            # Sempre zoom na Amazônia Legal (foco do plugin)
            self._zoom_to_amazonia_legal()
    
    def _update_status(self, message):
        """Atualiza a mensagem de status."""
        self.status_label.setText(message)
    
    # ==========================================================================
    #                              ESTILOS
    # ==========================================================================
    
    def _get_tab_style(self):
        """Estilo das abas."""
        return f"""
            QTabWidget::pane {{
                border: none;
                background-color: {self.COLORS['branco']};
            }}
            QTabBar::tab {{
                background-color: {self.COLORS['cinza']};
                color: {self.COLORS['texto']};
                padding: 14px 40px;
                margin-right: 3px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-weight: bold;
                font-size: 13px;
                min-width: 100px;
            }}
            QTabBar::tab:selected {{
                background-color: {self.COLORS['verde_medio']};
                color: white;
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {self.COLORS['verde_lima']};
            }}
        """
    
    def _get_groupbox_style(self):
        """Estilo dos grupos."""
        return f"""
            QGroupBox {{
                font-weight: bold;
                font-size: 13px;
                border: 2px solid {self.COLORS['verde_lima']};
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
                background-color: {self.COLORS['branco']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: {self.COLORS['verde_escuro']};
            }}
        """
    
    def _get_button_style(self, tipo="primary"):
        """Estilos de botões."""
        cores = {
            "primary": (self.COLORS['verde_medio'], self.COLORS['verde_escuro'], "white"),
            "secondary": (self.COLORS['cinza'], "#bdc3c7", self.COLORS['texto']),
            "success": (self.COLORS['verde_claro'], self.COLORS['verde_medio'], "white"),
            "danger": ("#e74c3c", "#c0392b", "white"),
            "warning": ("#f39c12", "#d68910", "white"),
        }
        bg, hover, text = cores.get(tipo, cores["primary"])
        return f"""
            QPushButton {{
                background-color: {bg};
                color: {text};
                border: none;
                padding: 10px 18px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:disabled {{
                background-color: #bdc3c7;
                color: #7f8c8d;
            }}
        """
    
    def _get_input_style(self):
        """Estilo dos inputs."""
        return f"""
            QLineEdit, QSpinBox, QDoubleSpinBox {{
                padding: 4px 6px;
                border: 1px solid {self.COLORS['cinza']};
                border-radius: 3px;
                background-color: white;
                font-size: 11px;
            }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {self.COLORS['verde_medio']};
            }}
        """
    
    def _get_combo_style(self):
        """Estilo dos combobox."""
        return f"""
            QComboBox {{
                padding: 8px;
                border: 2px solid {self.COLORS['cinza']};
                border-radius: 5px;
                background-color: white;
                font-size: 12px;
                min-width: 100px;
            }}
            QComboBox:focus {{
                border-color: {self.COLORS['verde_medio']};
            }}
            QComboBox::drop-down {{
                border: none;
                padding-right: 10px;
            }}
        """
    
    def _get_table_style(self):
        """Estilo da tabela."""
        return f"""
            QTableWidget {{
                border: 1px solid {self.COLORS['cinza']};
                border-radius: 5px;
                gridline-color: {self.COLORS['cinza']};
                font-size: 11px;
            }}
            QTableWidget::item {{
                padding: 2px;
            }}
            QTableWidget::item:selected {{
                background-color: {self.COLORS['verde_lima']};
                color: {self.COLORS['texto']};
            }}
            QHeaderView::section {{
                background-color: {self.COLORS['verde_medio']};
                color: white;
                padding: 4px;
                border: none;
                font-weight: bold;
                font-size: 10px;
            }}
        """
    
    def _get_progress_style(self):
        """Estilo da barra de progresso."""
        return f"""
            QProgressBar {{
                border: 2px solid {self.COLORS['cinza']};
                border-radius: 5px;
                text-align: center;
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                background-color: {self.COLORS['verde_claro']};
                border-radius: 3px;
            }}
        """
