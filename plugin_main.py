# -*- coding: utf-8 -*-
"""
Classe principal do plugin Floresta+ Amazonia
Registra acoes no QGIS e gerencia a janela principal.
"""

from qgis.PyQt.QtWidgets import QAction, QMessageBox, QToolBar
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QSize, QTimer
import os


class FlorestaMaisPlugin:
    """Classe principal do plugin - registra acoes no QGIS."""
    
    def __init__(self, iface):
        """Inicializa o plugin.
        
        Args:
            iface: Interface do QGIS (QgisInterface)
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = "&Floresta+ Amazonia"
        self.toolbar = None
        self.main_window = None
        self.plugin_name = "Floresta+ Amazonia"
        self.icon_size = QSize(68, 32)  # Mais largo que alto
        self._deps_checked = False
    
    def initGui(self):
        """
        Chamado quando o plugin e ativado.
        Cria toolbar DEDICADA que pode ser movida/ocultada independentemente.
        """
        # =====================================================================
        # Remover toolbar antiga se existir (para evitar conflito de configs)
        # =====================================================================
        main_window = self.iface.mainWindow()
        for toolbar in main_window.findChildren(QToolBar):
            if toolbar.objectName() == "FlorestaMaisToolbar":
                main_window.removeToolBar(toolbar)
                toolbar.deleteLater()
                break
        
        # =====================================================================
        # CRIAR TOOLBAR NOVA
        # =====================================================================
        self.toolbar = QToolBar("Floresta+ Amazonia", main_window)
        self.toolbar.setObjectName("FlorestaMaisToolbar")
        self.toolbar.setIconSize(self.icon_size)
        main_window.addToolBar(self.toolbar)
        
        # Caminho do icone
        icon_path = os.path.join(self.plugin_dir, 'icone', 'Logo.png')
        
        # Criar a acao principal
        self.action_main = QAction(
            QIcon(icon_path),
            "Floresta+ Amazonia",
            main_window
        )
        self.action_main.setStatusTip("Abre a janela de Analise de Elegibilidade")
        self.action_main.setWhatsThis(
            "Plugin para analise tecnica automatizada de elegibilidade "
            "de imoveis rurais - Modalidade Conservacao"
        )
        self.action_main.triggered.connect(self.run)
        
        # Adicionar a TOOLBAR
        self.toolbar.addAction(self.action_main)
        
        # Garantir tamanho do icone apos um delay (para sobrescrever restauracao do QGIS)
        QTimer.singleShot(500, self._force_icon_size)
        QTimer.singleShot(1000, self._force_icon_size)
        QTimer.singleShot(2000, self._force_icon_size)
        
        # Tambem adicionar ao MENU Sketches/Complementos
        self.iface.addPluginToMenu(self.menu, self.action_main)
        self.actions.append(self.action_main)
        
        # =====================================================================
        # ACAO EXTRA: Sobre
        # =====================================================================
        self.action_about = QAction(
            QIcon.fromTheme("help-about"),
            "Sobre",
            main_window
        )
        self.action_about.triggered.connect(self.show_about)
        self.iface.addPluginToMenu(self.menu, self.action_about)
        self.actions.append(self.action_about)
    
    def _force_icon_size(self):
        """Forca o tamanho do icone na toolbar."""
        if self.toolbar:
            self.toolbar.setIconSize(self.icon_size)
    
    def unload(self):
        """Chamado quando o plugin e desativado."""
        # Remover acoes do menu
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
        
        # Remover toolbar dedicada
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None
        
        # Fechar janela se aberta
        if self.main_window:
            self.main_window.close()
            self.main_window = None
    
    def _check_dependencies(self):
        """Verifica dependências na primeira execução e oferece instalação."""
        if self._deps_checked:
            return
        self._deps_checked = True

        try:
            from .dependency_manager import verificar_e_instalar_interativo
            verificar_e_instalar_interativo(parent_widget=self.iface.mainWindow())
        except Exception as e:
            print(f"[Floresta+] Erro ao verificar dependências: {e}")

    def run(self):
        """Abre a janela principal do plugin."""
        self._check_dependencies()

        if self.main_window is None:
            from .ui.main_window import MainWindow
            self.main_window = MainWindow(self.iface, self.plugin_dir)
        
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()
    
    def show_about(self):
        """Exibe informacoes sobre o plugin."""
        QMessageBox.about(
            self.iface.mainWindow(),
            "Sobre - Floresta+ Amazonia",
            "<h2>Floresta+ Amazonia</h2>"
            "<h3>Analise de Elegibilidade</h3>"
            "<p><b>Versao:</b> 1.0.0</p>"
            "<p>Plugin QGIS para analise tecnica automatizada de elegibilidade "
            "de imoveis rurais para a Modalidade Conservacao do Projeto "
            "Floresta+ Amazonia.</p>"
            "<hr>"
            "<p><b>Projeto:</b> Floresta+ Amazonia</p>"
            "<p><b>Coordenacao:</b> MMA - Ministerio do Meio Ambiente</p>"
            "<p><b>Execucao:</b> PNUD - Programa das Nacoes Unidas</p>"
            "<p><b>Financiamento:</b> GCF - Fundo Verde para o Clima</p>"
        )
