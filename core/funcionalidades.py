# -*- coding: utf-8 -*-
"""
Módulo de funcionalidades do plugin Floresta+ Amazônia
Contém a lógica de processamento e análise de elegibilidade.
"""

import os
from qgis.core import (
    QgsVectorLayer, QgsRasterLayer, QgsProject,
    QgsProcessingFeedback, QgsCoordinateReferenceSystem,
    QgsFeature, QgsGeometry, QgsField, QgsFields,
    QgsVectorFileWriter, QgsWkbTypes, QgsExpression,
    QgsFeatureRequest, QgsSpatialIndex
)
from qgis.PyQt.QtCore import QVariant
import processing


class GerenciadorCamadas:
    """Classe para gerenciar camadas do projeto."""
    
    def __init__(self, config):
        self.config = config
        self.camadas = {}
        self.crs_padrao = QgsCoordinateReferenceSystem(
            config.get('default_crs', 'EPSG:102033')
        )
    
    def carregar_camada_local(self, caminho, nome):
        """Carrega uma camada vetorial local.
        
        Args:
            caminho: Caminho do arquivo
            nome: Nome da camada
            
        Returns:
            QgsVectorLayer ou None se inválida
        """
        layer = QgsVectorLayer(caminho, nome, "ogr")
        if layer.isValid():
            self.camadas[nome] = layer
            return layer
        return None
    
    def carregar_camada_wfs(self, url, layer_name, nome):
        """Carrega uma camada de serviço WFS.
        
        Args:
            url: URL do serviço WFS
            layer_name: Nome da camada no serviço
            nome: Nome para a camada no QGIS
            
        Returns:
            QgsVectorLayer ou None se inválida
        """
        uri = f"{url}?service=WFS&version=2.0.0&request=GetFeature&typeName={layer_name}"
        layer = QgsVectorLayer(uri, nome, "WFS")
        if layer.isValid():
            self.camadas[nome] = layer
            return layer
        return None
    
    def reprojetar_camada(self, layer, crs_destino=None):
        """Reprojeta uma camada para o CRS padrão.
        
        Args:
            layer: Camada a ser reprojetada
            crs_destino: CRS de destino (usa padrão se None)
            
        Returns:
            Camada reprojetada
        """
        if crs_destino is None:
            crs_destino = self.crs_padrao
        
        if layer.crs() == crs_destino:
            return layer
        
        params = {
            'INPUT': layer,
            'TARGET_CRS': crs_destino,
            'OUTPUT': 'memory:'
        }
        
        result = processing.run("native:reprojectlayer", params)
        return result['OUTPUT']


class ProcessadorRVN:
    """Classe para processamento de Remanescente de Vegetação Nativa."""
    
    def __init__(self, config):
        self.config = config
    
    def calcular_indice(self, raster_path, indice='NDVI', 
                        banda_red=3, banda_nir=4):
        """Calcula índice de vegetação.
        
        Args:
            raster_path: Caminho do raster
            indice: Tipo de índice (NDVI, EVI, SAVI, NDWI)
            banda_red: Número da banda vermelha
            banda_nir: Número da banda NIR
            
        Returns:
            Caminho do raster de índice calculado
        """
        # Fórmulas dos índices
        formulas = {
            'NDVI': f'(B{banda_nir} - B{banda_red}) / (B{banda_nir} + B{banda_red})',
            'EVI': f'2.5 * ((B{banda_nir} - B{banda_red}) / (B{banda_nir} + 6*B{banda_red} - 7.5*B1 + 1))',
            'SAVI': f'((B{banda_nir} - B{banda_red}) / (B{banda_nir} + B{banda_red} + 0.5)) * 1.5',
            'NDWI': f'(B2 - B{banda_nir}) / (B2 + B{banda_nir})'
        }
        
        formula = formulas.get(indice, formulas['NDVI'])
        
        params = {
            'INPUT_A': raster_path,
            'BAND_A': banda_nir,
            'INPUT_B': raster_path,
            'BAND_B': banda_red,
            'FORMULA': formula,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        
        # Usar calculadora raster
        result = processing.run("gdal:rastercalculator", params)
        return result['OUTPUT']
    
    def aplicar_threshold(self, raster_indice, threshold=0.4):
        """Aplica threshold para binarizar o índice.
        
        Args:
            raster_indice: Raster do índice calculado
            threshold: Valor de corte
            
        Returns:
            Raster binarizado
        """
        params = {
            'INPUT_A': raster_indice,
            'BAND_A': 1,
            'FORMULA': f'A >= {threshold}',
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        
        result = processing.run("gdal:rastercalculator", params)
        return result['OUTPUT']
    
    def vetorizar(self, raster_binario, area_minima=0.5):
        """Vetoriza o raster e filtra por área mínima.
        
        Args:
            raster_binario: Raster binarizado
            area_minima: Área mínima em hectares
            
        Returns:
            Camada vetorial de RVN
        """
        # Poligonizar
        params = {
            'INPUT': raster_binario,
            'BAND': 1,
            'FIELD': 'DN',
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        result = processing.run("gdal:polygonize", params)
        poligonos = result['OUTPUT']
        
        # Filtrar apenas vegetação (DN = 1) e área mínima
        area_m2 = area_minima * 10000  # Converter ha para m²
        
        params = {
            'INPUT': poligonos,
            'EXPRESSION': f'"DN" = 1 AND $area >= {area_m2}',
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        result = processing.run("native:extractbyexpression", params)
        
        return result['OUTPUT']


class AnalisadorElegibilidade:
    """Classe para análise de elegibilidade de imóveis."""
    
    def __init__(self, config):
        self.config = config
        self.tolerancias = config.get('tolerancias', {})
        self.percentuais_rvn = config.get('percentuais_rvn', {})
    
    def calcular_sobreposicao(self, layer_imovel, layer_referencia, 
                               feedback=None):
        """Calcula área de sobreposição entre imóvel e camada de referência.
        
        Args:
            layer_imovel: Camada do imóvel (CAR)
            layer_referencia: Camada de referência (UC, TI, etc.)
            feedback: Feedback de processamento
            
        Returns:
            Dicionário {id_imovel: area_sobreposicao}
        """
        # Intersect
        params = {
            'INPUT': layer_imovel,
            'OVERLAY': layer_referencia,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        result = processing.run("native:intersection", params, feedback=feedback)
        intersect = result['OUTPUT']
        
        # Calcular áreas
        sobreposicoes = {}
        for feat in intersect.getFeatures():
            id_imovel = feat['OBJECTID']  # ou outro campo identificador
            area = feat.geometry().area() / 10000  # m² para ha
            sobreposicoes[id_imovel] = sobreposicoes.get(id_imovel, 0) + area
        
        return sobreposicoes
    
    def verificar_localizacao_amzl(self, layer_imovel, layer_amzl):
        """Verifica se imóveis estão na Amazônia Legal.
        
        Args:
            layer_imovel: Camada dos imóveis
            layer_amzl: Camada da Amazônia Legal
            
        Returns:
            Set de IDs dos imóveis dentro da AMZL
        """
        params = {
            'INPUT': layer_imovel,
            'PREDICATE': [6],  # Are within
            'INTERSECT': layer_amzl,
            'OUTPUT': 'TEMPORARY_OUTPUT'
        }
        result = processing.run("native:extractbylocation", params)
        
        ids_dentro = set()
        for feat in result['OUTPUT'].getFeatures():
            ids_dentro.add(feat.id())
        
        return ids_dentro
    
    def verificar_municipios_prioritarios(self, layer_imovel, layer_prioritarios):
        """Verifica se imóveis estão em municípios prioritários.
        
        Args:
            layer_imovel: Camada dos imóveis
            layer_prioritarios: Camada dos municípios prioritários
            
        Returns:
            Set de IDs dos imóveis em municípios prioritários
        """
        return self.verificar_localizacao_amzl(layer_imovel, layer_prioritarios)
    
    def avaliar_floresta_publica(self, sobreposicao, area_imovel, 
                                  tem_documento=False):
        """Avalia elegibilidade quanto a Floresta Pública Tipo B.
        
        Args:
            sobreposicao: Área de sobreposição em ha
            area_imovel: Área total do imóvel em ha
            tem_documento: Se possui documento de propriedade válido
            
        Returns:
            tuple (elegivel: bool, percentual: float)
        """
        tolerancia = self.tolerancias.get('floresta_publica_tipo_b', 0.05)
        percentual = sobreposicao / area_imovel if area_imovel > 0 else 0
        
        elegivel = percentual <= tolerancia or tem_documento
        return (elegivel, percentual)
    
    def avaliar_terra_indigena(self, sobreposicao, area_imovel):
        """Avalia elegibilidade quanto a Terra Indígena.
        
        Args:
            sobreposicao: Área de sobreposição em ha
            area_imovel: Área total do imóvel em ha
            
        Returns:
            tuple (elegivel: bool, percentual: float)
        """
        # Tolerância 0% para TI
        percentual = sobreposicao / area_imovel if area_imovel > 0 else 0
        elegivel = sobreposicao == 0
        return (elegivel, percentual)
    
    def avaliar_quilombola(self, sobreposicao, area_imovel):
        """Avalia elegibilidade quanto a Território Quilombola.
        
        Args:
            sobreposicao: Área de sobreposição em ha
            area_imovel: Área total do imóvel em ha
            
        Returns:
            tuple (elegivel: bool, percentual: float)
        """
        # Tolerância 0% para Quilombola
        percentual = sobreposicao / area_imovel if area_imovel > 0 else 0
        elegivel = sobreposicao == 0
        return (elegivel, percentual)
    
    def avaliar_unidade_conservacao(self, sobreposicao, area_imovel,
                                     categoria_uc=None):
        """Avalia elegibilidade quanto a Unidade de Conservação.
        
        Args:
            sobreposicao: Área de sobreposição em ha
            area_imovel: Área total do imóvel em ha
            categoria_uc: Categoria da UC (APA, RPPN são permitidas)
            
        Returns:
            tuple (elegivel: bool, percentual: float)
        """
        categorias_excluidas = self.config.get('categorias_uc_excluidas', [])
        
        if categoria_uc in categorias_excluidas:
            # APAs e RPPNs são permitidas
            return (True, 0)
        
        tolerancia = self.tolerancias.get('unidade_conservacao', 0.05)
        percentual = sobreposicao / area_imovel if area_imovel > 0 else 0
        elegivel = percentual <= tolerancia
        return (elegivel, percentual)
    
    def avaliar_prodes(self, area_desmatamento, fase='fase1'):
        """Avalia elegibilidade quanto ao desmatamento PRODES.
        
        Args:
            area_desmatamento: Área de desmatamento em ha
            fase: 'fase1' ou 'fase2'
            
        Returns:
            tuple (elegivel: bool, area: float)
        """
        tolerancia = self.tolerancias.get(f'prodes_{fase}', 6.25)
        elegivel = area_desmatamento <= tolerancia
        return (elegivel, area_desmatamento)
    
    def avaliar_rvn(self, area_rvn, area_imovel, fitofisionomia):
        """Avalia elegibilidade quanto ao RVN.
        
        Args:
            area_rvn: Área de RVN em ha
            area_imovel: Área total do imóvel em ha
            fitofisionomia: Tipo de fitofisionomia (Floresta, Cerrado, Campos Gerais)
            
        Returns:
            tuple (elegivel: bool, percentual: float, minimo_exigido: float)
        """
        percentual_exigido = self.percentuais_rvn.get(fitofisionomia, 0.50)
        percentual = area_rvn / area_imovel if area_imovel > 0 else 0
        minimo_ha = area_imovel * percentual_exigido
        
        elegivel = area_rvn >= 1 and percentual >= percentual_exigido
        return (elegivel, percentual, minimo_ha)
    
    def avaliar_modulos_fiscais(self, soma_modulos):
        """Avalia elegibilidade quanto aos módulos fiscais.
        
        Args:
            soma_modulos: Soma de módulos fiscais do proprietário
            
        Returns:
            tuple (elegivel: bool, soma: float)
        """
        elegivel = soma_modulos <= 4
        return (elegivel, soma_modulos)


class CalculadoraPagamento:
    """Classe para cálculo de valores de pagamento."""
    
    def __init__(self, config):
        self.config = config
        self.pagamento = config.get('pagamento', {})
    
    def calcular_valor_fase1(self):
        """Retorna valor fixo da Fase 1.
        
        Returns:
            Valor em R$
        """
        return self.pagamento.get('valor_fixo_fase1', 1500)
    
    def calcular_valor_fase2(self, area_rvn, areas_por_fito):
        """Calcula valor de pagamento da Fase 2.
        
        Args:
            area_rvn: Área total de RVN em ha
            areas_por_fito: Dict {fitofisionomia: area_ha}
            
        Returns:
            Dict com detalhamento do cálculo
        """
        # Parâmetros
        faixa1_max = self.pagamento.get('faixa1_max_ha', 60)
        faixa1_valor = self.pagamento.get('faixa1_valor_ha', 200)
        faixa2_max = self.pagamento.get('faixa2_max_ha', 20)
        faixa2_valor = self.pagamento.get('faixa2_valor_ha', 800)
        valor_minimo = self.pagamento.get('valor_minimo', 1500)
        
        # Percentuais para cálculo do mínimo legal (80% para F2)
        percentuais = {
            'Floresta': 0.80,
            'Cerrado': 0.35,
            'Campos Gerais': 0.20
        }
        
        # Calcular RVN mínimo legal
        rvn_minimo = sum(
            areas_por_fito.get(fito, 0) * perc 
            for fito, perc in percentuais.items()
        )
        
        # Faixa 1: até o mínimo legal (máx 60 ha)
        faixa1_area = min(area_rvn, rvn_minimo)
        faixa1_area_paga = min(faixa1_area, faixa1_max)
        faixa1_total = faixa1_area_paga * faixa1_valor
        
        # Faixa 2: excedente ao mínimo legal (máx 20 ha)
        excedente = max(area_rvn - rvn_minimo, 0)
        faixa2_area_paga = min(excedente, faixa2_max)
        faixa2_total = faixa2_area_paga * faixa2_valor
        
        # Total (mínimo R$ 1.500)
        total = max(faixa1_total + faixa2_total, valor_minimo)
        
        return {
            'faixa1_area': faixa1_area,
            'faixa1_area_paga': faixa1_area_paga,
            'faixa1_valor': faixa1_total,
            'faixa2_area': excedente,
            'faixa2_area_paga': faixa2_area_paga,
            'faixa2_valor': faixa2_total,
            'rvn_minimo_legal': rvn_minimo,
            'total': total
        }
