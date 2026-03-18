# -*- coding: utf-8 -*-
"""
Modulo core do plugin Floresta+ Amazonia
Contem a logica de negocio e processamento.
"""

from .funcionalidades import GerenciadorCamadas

try:
    from .planet_client import PlanetClient, planet_client
except ImportError:
    # Dependencias nao disponiveis (requests)
    PlanetClient = None
    planet_client = None
