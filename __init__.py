# -*- coding: utf-8 -*-
"""
Plugin Floresta+ Amazônia - Análise de Elegibilidade
Plugin QGIS para automatização da análise técnica de verificação de 
elegibilidade de imóveis rurais para a Modalidade Conservação do 
Projeto Floresta+ Amazônia.
"""

def classFactory(iface):
    """Função obrigatória chamada pelo QGIS ao carregar o plugin."""
    from .plugin_main import FlorestaMaisPlugin
    return FlorestaMaisPlugin(iface)
