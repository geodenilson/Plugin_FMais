# -*- coding: utf-8 -*-
"""
Módulo de Processamento de Elegibilidade para o Plugin Floresta+
Conversão completa do script ArcPy para PyQGIS/GDAL

Este módulo realiza a análise de elegibilidade de imóveis rurais para o programa
Floresta+ Conservação, verificando diversos critérios estabelecidos no edital.

Autor: Plugin Floresta+
Data: Janeiro 2026
"""

import os
import re
import tempfile
import traceback
from typing import Dict, List, Set, Tuple, Optional, Any
from dataclasses import dataclass, field

# QGIS imports
try:
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsField, QgsFields,
        QgsGeometry, QgsProject, QgsCoordinateReferenceSystem,
        QgsCoordinateTransform, QgsFeatureRequest, QgsSpatialIndex,
        QgsVectorFileWriter, QgsWkbTypes, QgsProcessingFeedback,
        QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
        QgsDistanceArea, QgsUnitTypes, NULL
    )
    from qgis.PyQt.QtCore import QVariant
except ImportError:
    pass


@dataclass
class ConfigProcessamento:
    """Configuração dos parâmetros de processamento."""
    
    # Caminhos das camadas de referência
    gpkg_path: str = ""
    
    # Nomes das camadas no GeoPackage
    camada_imoveis: str = "imoveis_analisar"
    camada_cnfp: str = "cnfp"
    camada_ucs: str = "ucs"
    camada_quilombolas: str = "quilombolas"
    camada_terras_indigenas: str = "terras_indigenas"
    camada_embargos_icmbio: str = "embargos_icmbio"
    camada_embargos_ibama: str = "embargos_ibama"
    camada_car_amazonia: str = "car_amazonia"
    camada_prodes: str = "prodes"
    camada_fitofisionomia: str = "fitofisionomia"
    camada_rvn: str = "vegetacao_nativa"
    camada_amazonia_legal: str = "amazonia_legal"
    camada_municipios: str = "municipios"  # Camada de municípios (para extrair prioritários)
    
    # Geocódigos dos municípios prioritários (Fase 1)
    geocodigos_prioritarios: Set[str] = field(default_factory=lambda: {
        "5101852", "1505064", "1100338", "5103858", "5108907", "5106240", "1302702",
        "5105101", "5107578", "5108303", "1200302", "1100452", "1506708", "5103254",
        "1302405", "1100130", "1507300", "1200401", "5106422", "5106158", "1502939",
        "1503606", "1500602", "1300904", "1505486", "1300144", "1503754", "1302603",
        "1303304", "1500859", "1508050", "1400472", "1508126", "1505502", "1302009",
        "5104104", "1505031", "5106299", "5100805", "1100809", "1400308", "5107354",
        "5101407", "1504208", "1302900", "1300706", "1506187", "1504752", "1505809",
        "1200609", "1501253", "1506195", "1506583", "5105150", "5103379", "5106307",
        "5107180", "5106802", "5107065", "5103353", "1100205", "5105580", "1301704",
        "5107776", "5103700", "5103056", "5107859", "5103361", "5103304", "1200500",
        "1200344", "1100940", "1508159", "1507805", "1504703", "1506005", "5105507",
        "1504455", "1505650", "1502764", "1503705"
    })
    
    # Lista de códigos CAR a remover da análise de sobreposição
    lista_car_remover: str = ""  # Códigos separados por quebra de linha
    
    # Tipo de fonte de dados e mapeamento de colunas
    tipo_fonte: str = "completa"  # 'completa', 'geoserver', 'consulta_publica'
    mapeamento_colunas: Dict[str, str] = field(default_factory=dict)
    
    # Tolerâncias e thresholds
    tolerancia_fp_uc: float = 0.05  # 5% para Florestas Públicas e UCs
    tolerancia_sobreposicao_car: float = 0.50  # 50% para sobreposição entre CARs
    limite_modulos_fiscais: int = 4  # Máximo de módulos fiscais
    limite_prodes_1ha: float = 1.0  # 1 hectare
    limite_prodes_6ha: float = 6.25  # 6,25 hectares
    
    # Thresholds de RVN por fitofisionomia (Fase 1)
    threshold_rvn_floresta: float = 0.50  # 50%
    threshold_rvn_cerrado: float = 0.35  # 35%
    threshold_rvn_campos: float = 0.20  # 20%
    
    # Thresholds de RVN por fitofisionomia (Fase 2 - 80%)
    threshold_rvn_floresta_f2: float = 0.80
    threshold_rvn_cerrado_f2: float = 0.35
    threshold_rvn_campos_f2: float = 0.20
    
    # Valores de pagamento
    faixa1_limite_ha: float = 60.0  # Até 60 hectares
    faixa1_valor_ha: float = 200.0  # R$ 200/ha
    faixa2_limite_ha: float = 20.0  # Até 20 hectares excedentes
    faixa2_valor_ha: float = 800.0  # R$ 800/ha
    valor_minimo: float = 1500.0  # R$ 1.500,00 mínimo
    valor_fase1: float = 1500.0  # Fase 1 sempre R$ 1.500,00
    
    # Sistema de referência para cálculo de área
    crs_area: str = "ESRI:102033"  # South America Albers Equal Area Conic
    
    # Categorias de UC que admitem uso privado (tolerância 5%)
    ucs_admitem_uso_privado: List[str] = field(default_factory=lambda: [
        "Área de Proteção Ambiental",
        "Monumento Natural",
        "Área de Relevante Interesse Ecológico",
        "Reserva Particular do Patrimônio Natural",
        "Refúgio de Vida Silvestre"
    ])
    
    # Tipos de documento válidos para CNFP
    documentos_validos_cnfp: Set[str] = field(default_factory=lambda: {
        "Escritura",
        "Certidão de registro",
        "Concessão real de direito de uso",
        "Título de propriedade sob condição resolutiva",
        "Título de domínio",
        "Título definitivo, com reserva florestal, em condomínio",
        "Título definitivo sujeito a re-ratificação",
        "Título definitivo transferido, com anuência do Órgão Fundiário (Estadual ou Federal)",
        "Contrato de assentamento do Órgão Fundiário (Estadual ou Federal)"
    })
    
    # Condições inválidas para Fase 1 (imóvel já analisado)
    condicoes_invalidas_f1: Set[str] = field(default_factory=lambda: {
        "Analisado, em regularização ambiental (Lei nº 12.651/2012)",
        "Analisado, em conformidade com a Lei nº 12.651/2012, com ativos ambientais",
        "Analisado, aguardando regularização ambiental (Lei nº 12.651/2012)",
        "Analisado, em conformidade com a Lei nº 12.651/2012",
        "Analisado sem pendências"
    })


class ProcessamentoElegiveis:
    """
    Classe principal para processamento de elegibilidade.
    Converte toda a lógica do script ArcPy para PyQGIS.
    """
    
    def __init__(self, config: ConfigProcessamento, callback_progress=None, callback_log=None):
        """
        Inicializa o processamento.
        
        Args:
            config: Configuração dos parâmetros
            callback_progress: Função callback para atualizar progresso (value, max)
            callback_log: Função callback para log de mensagens
        """
        self.config = config
        self.callback_progress = callback_progress
        self.callback_log = callback_log
        
        # Camada de saída (elegíveis)
        self.output_layer: Optional[QgsVectorLayer] = None
        
        # Dicionários para armazenar resultados intermediários
        self.area_dict: Dict[int, float] = {}  # {fid: area_ha}
        self.cnfp_dict: Dict[int, float] = {}
        self.uc_dict: Dict[int, float] = {}
        self.quil_dict: Dict[int, float] = {}
        self.indi_dict: Dict[int, float] = {}
        self.emb_icm_dict: Dict[int, float] = {}
        self.emb_iba_dict: Dict[int, float] = {}
        self.sobrep_dict: Dict[int, float] = {}
        self.prodes_dict: Dict[int, float] = {}
        self.fit_dict: Dict[int, Set[str]] = {}
        self.rvn_dict: Dict[int, float] = {}
        self.rvn_fito_dict: Dict[str, Dict[int, float]] = {
            "Floresta": {},
            "Cerrado": {},
            "Campos Gerais": {}
        }
        
        # Conjuntos para verificações
        self.cpf_embargos_icmbio: Set[str] = set()
        self.cpf_embargos_ibama: Set[str] = set()
        self.municipios_prioritarios: Set[str] = set()
        self.imoveis_amazonia_legal: Set[int] = set()
        
        # CPF -> {CAR -> módulo} para soma real
        self.cpf_modulos_dict: Dict[str, Dict[str, float]] = {}
        
        # CRS para cálculos de área
        self.crs_area = QgsCoordinateReferenceSystem(config.crs_area)
        
    def log(self, msg: str):
        """Envia mensagem para o log."""
        print(f"[Processamento] {msg}")
        if self.callback_log:
            self.callback_log(msg)
            
    def progress(self, value: int, max_value: int = 100):
        """Atualiza barra de progresso."""
        if self.callback_progress:
            self.callback_progress(value, max_value)
            
    def _carregar_camada(self, nome_camada: str) -> Optional[QgsVectorLayer]:
        """Carrega uma camada do GeoPackage."""
        if not self.config.gpkg_path or not os.path.exists(self.config.gpkg_path):
            self.log(f"❌ GeoPackage não encontrado: {self.config.gpkg_path}")
            return None
            
        uri = f"{self.config.gpkg_path}|layername={nome_camada}"
        layer = QgsVectorLayer(uri, nome_camada, "ogr")
        
        if not layer.isValid():
            self.log(f"⚠ Camada '{nome_camada}' não encontrada no GeoPackage")
            return None
            
        return layer
    
    def _get_attr_safe(self, feat: QgsFeature, field_names: List[str], default=None):
        """
        Obtém atributo de forma segura, tentando múltiplos nomes de campo.
        
        Args:
            feat: Feature do QGIS
            field_names: Lista de possíveis nomes de campo (em ordem de prioridade)
            default: Valor padrão se nenhum campo for encontrado
            
        Returns:
            Valor do primeiro campo encontrado ou default
        """
        # Obter nomes dos campos disponíveis na feature
        campos_disponiveis = [f.name() for f in feat.fields()]
        
        for nome in field_names:
            if nome in campos_disponiveis:
                valor = feat.attribute(nome)
                if valor is not None:
                    return valor
        
        return default
    
    def _calcular_area_ha(self, geom: QgsGeometry, crs_origem: QgsCoordinateReferenceSystem) -> float:
        """Calcula a área de uma geometria em hectares."""
        if geom.isEmpty():
            return 0.0
            
        # Criar calculadora de distância/área
        da = QgsDistanceArea()
        da.setSourceCrs(crs_origem, QgsProject.instance().transformContext())
        da.setEllipsoid('WGS84')
        
        # Calcular área em metros quadrados e converter para hectares
        area_m2 = da.measureArea(geom)
        area_ha = da.convertAreaMeasurement(area_m2, QgsUnitTypes.AreaHectares)
        
        return round(area_ha, 2)
    
    def _calcular_interseccao(self, layer_a: QgsVectorLayer, layer_b: QgsVectorLayer,
                              fid_field: str = None) -> Dict[int, float]:
        """
        Calcula área de interseção entre duas camadas.
        
        Args:
            layer_a: Camada de imóveis (com FIDs)
            layer_b: Camada de referência
            fid_field: Campo com ID do imóvel (se None, usa FID)
            
        Returns:
            Dicionário {fid: area_intersecao_ha}
        """
        result = {}
        
        if not layer_a or not layer_b:
            return result
            
        # Criar índice espacial para layer_b
        index_b = QgsSpatialIndex(layer_b.getFeatures())
        
        # Iterar sobre features de layer_a
        for feat_a in layer_a.getFeatures():
            geom_a = feat_a.geometry()
            if geom_a.isEmpty():
                continue
                
            fid = feat_a.id() if fid_field is None else feat_a[fid_field]
            
            # Buscar features de layer_b que intersectam
            candidates = index_b.intersects(geom_a.boundingBox())
            
            area_total = 0.0
            for fid_b in candidates:
                feat_b = layer_b.getFeature(fid_b)
                geom_b = feat_b.geometry()
                
                if geom_a.intersects(geom_b):
                    intersection = geom_a.intersection(geom_b)
                    if not intersection.isEmpty():
                        area = self._calcular_area_ha(intersection, layer_a.crs())
                        area_total += area
                        
            if area_total > 0:
                result[fid] = round(area_total, 2)
                
        return result
    
    def _criar_camada_saida(self, layer_entrada: QgsVectorLayer) -> QgsVectorLayer:
        """
        Cria a camada de saída (elegíveis) com todos os campos necessários.
        """
        self.log("Criando camada de saída...")
        
        # Definir campos adicionais
        campos_adicionais = [
            # Área do imóvel
            ("area_car", QVariant.Double),
            
            # Módulos Fiscais
            ("modulos_imovel", QVariant.String),
            ("soma_modulos", QVariant.String),
            ("soma_modulos_real", QVariant.Double),
            
            # CNFP (Florestas Públicas Tipo B)
            ("area_cnfp", QVariant.Double),
            ("percent_cnfp", QVariant.Double),
            ("cnfp", QVariant.String),
            
            # Unidades de Conservação
            ("area_uc", QVariant.Double),
            ("percent_uc", QVariant.Double),
            ("uc", QVariant.String),
            
            # Quilombolas
            ("area_quil", QVariant.Double),
            ("percent_quil", QVariant.Double),
            ("quilombola", QVariant.String),
            
            # Terras Indígenas
            ("area_indi", QVariant.Double),
            ("percent_indi", QVariant.Double),
            ("terra_indi", QVariant.String),
            
            # Embargos ICMBio
            ("area_emb_icm", QVariant.Double),
            ("percent_emb_icm", QVariant.Double),
            ("embargo_icmbio", QVariant.String),
            ("cpf_emb_icmbio", QVariant.String),
            
            # Embargos IBAMA
            ("area_emb_iba", QVariant.Double),
            ("percent_emb_iba", QVariant.Double),
            ("embargo_ibama", QVariant.String),
            ("cpf_emb_ibama", QVariant.String),
            
            # Sobreposição CAR
            ("percent_sobrep", QVariant.Double),
            ("sobrep_car", QVariant.String),
            
            # PRODES
            ("area_prodes", QVariant.Double),
            ("prodes_1ha", QVariant.String),
            ("prodes_6ha", QVariant.String),
            
            # Fitofisionomia e RVN
            ("fitofisionomia", QVariant.String),
            ("RVN_area", QVariant.Double),
            ("percent_rvn", QVariant.Double),
            ("RVN_min_lei", QVariant.Double),
            ("rvn_minima", QVariant.String),
            ("rvn_floresta", QVariant.Double),
            ("rvn_cerrado", QVariant.Double),
            ("rvn_campo", QVariant.Double),
            ("rvn_floresta_p", QVariant.Double),
            ("rvn_cerrado_p", QVariant.Double),
            ("rvn_campo_p", QVariant.Double),
            
            # Elegibilidade
            ("em_prioritarios", QVariant.String),
            ("chek_status_f1", QVariant.String),
            ("condicao_Fase1", QVariant.String),
            ("dentro_da_amzl", QVariant.String),
            ("chek_status_f2", QVariant.String),
            ("condicao_Fase2", QVariant.String),
            ("Elegivel_F1", QVariant.String),
            ("Elegivel_F2", QVariant.String),
            ("elegibilidade", QVariant.String),
            ("cert_mpf", QVariant.String),
            
            # Valores e Faixas
            ("Floresta_ha", QVariant.Double),
            ("Floresta_per", QVariant.Double),
            ("Cerrado_ha", QVariant.Double),
            ("Cerrado_per", QVariant.Double),
            ("Campos_Gerais_ha", QVariant.Double),
            ("Campos_Gerais_per", QVariant.Double),
            ("RVN_Min_calc_f2", QVariant.Double),
            ("Faixa_1_ha", QVariant.Double),
            ("Faixa_1_val", QVariant.Double),
            ("Faixa_2_ha", QVariant.Double),
            ("Faixa_2_val", QVariant.Double),
            ("Total_rec", QVariant.Double),
            
            # Parecer
            ("parecer", QVariant.String),
        ]
        
        # Criar camada em memória com campos da entrada + adicionais
        fields = QgsFields()
        for field in layer_entrada.fields():
            fields.append(field)
        for nome, tipo in campos_adicionais:
            fields.append(QgsField(nome, tipo))
            
        # Criar camada de memória
        crs_str = layer_entrada.crs().authid()
        self.output_layer = QgsVectorLayer(
            f"Polygon?crs={crs_str}",
            "elegiveis",
            "memory"
        )
        
        provider = self.output_layer.dataProvider()
        provider.addAttributes(fields)
        self.output_layer.updateFields()
        
        # Copiar features
        features = []
        for feat in layer_entrada.getFeatures():
            new_feat = QgsFeature(fields)
            new_feat.setGeometry(feat.geometry())
            
            # Copiar atributos existentes
            for i, field in enumerate(layer_entrada.fields()):
                new_feat.setAttribute(field.name(), feat[field.name()])
                
            features.append(new_feat)
            
        provider.addFeatures(features)
        
        self.log(f"✓ Camada de saída criada com {self.output_layer.featureCount()} feições")
        return self.output_layer
    
    # =========================================================================
    # ETAPA 1: Calcular área dos imóveis
    # =========================================================================
    def _etapa_calcular_areas(self):
        """Calcula a área de cada imóvel em hectares."""
        self.log("\n1. Calculando áreas dos imóveis...")
        
        if not self.output_layer:
            return
            
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            geom = feat.geometry()
            area = self._calcular_area_ha(geom, self.output_layer.crs())
            self.area_dict[feat.id()] = area
            
            # Atualizar campo
            self.output_layer.changeAttributeValue(
                feat.id(),
                self.output_layer.fields().indexOf("area_car"),
                area
            )
            
        self.output_layer.commitChanges()
        self.log(f"   ✓ Áreas calculadas para {len(self.area_dict)} imóveis")
    
    # =========================================================================
    # ETAPA 2: Avaliação de Módulos Fiscais
    # =========================================================================
    def _etapa_modulos_fiscais(self):
        """Avalia elegibilidade por módulos fiscais."""
        self.log("\n2. Checagem de Módulos Fiscais...")
        
        if not self.output_layer:
            return
        
        # Verificar se análise é completa (tem CPF disponível)
        analise_completa = getattr(self, 'analise_completa', True)
        cpf_soma_real = {}
        
        # Lista de possíveis nomes de campo para módulo fiscal
        campos_modulo = ["modulo_f", "m_fiscal", "mod_fiscal"]
        campos_car = ["n_do_car", "cod_imovel"]
        
        if analise_completa:
            # Coletar dados por CPF (considerando CARs únicos)
            self.log("   2.1 Calculando soma real de módulos por CPF (ignorando CARs duplicados)")
            
            for feat in self.output_layer.getFeatures():
                cpf = self._get_attr_safe(feat, ["cpf_cnpj"])
                car = self._get_attr_safe(feat, campos_car)
                modulo = self._get_attr_safe(feat, campos_modulo, 0)
                
                if cpf and car:
                    if cpf not in self.cpf_modulos_dict:
                        self.cpf_modulos_dict[cpf] = {}
                    # Armazena módulo para cada CAR único (sobrescreve duplicados)
                    self.cpf_modulos_dict[cpf][car] = modulo
                    
            # Calcular soma real para cada CPF
            for cpf, cars_dict in self.cpf_modulos_dict.items():
                cpf_soma_real[cpf] = sum(cars_dict.values())
                
            self.log(f"   2.2 Total de CPFs processados: {len(cpf_soma_real)}")
        else:
            self.log("   ⚠ CPF não disponível - soma por CPF será ignorada (análise preliminar)")
        
        # Atualizar camada
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            # Tentar diferentes nomes de campo para módulo fiscal
            mod_val = self._get_attr_safe(feat, campos_modulo, 0)
            
            # Avaliação do imóvel individual
            mod_flag = "Elegível" if mod_val <= self.config.limite_modulos_fiscais else "Não Elegível"
            
            # Avaliação da soma por CPF (apenas se análise completa)
            if analise_completa:
                cpf = self._get_attr_safe(feat, ["cpf_cnpj"])
                soma_real = cpf_soma_real.get(cpf, 0) if cpf else 0
                soma_flag = "Elegível" if soma_real <= self.config.limite_modulos_fiscais else "Não Elegível"
            else:
                soma_real = 0
                soma_flag = "N/D"  # Não Disponível
            
            # Atualizar campos
            idx_mod = self.output_layer.fields().indexOf("modulos_imovel")
            idx_soma = self.output_layer.fields().indexOf("soma_modulos")
            idx_soma_real = self.output_layer.fields().indexOf("soma_modulos_real")
            
            self.output_layer.changeAttributeValue(fid, idx_mod, mod_flag)
            self.output_layer.changeAttributeValue(fid, idx_soma, soma_flag)
            self.output_layer.changeAttributeValue(fid, idx_soma_real, round(soma_real, 2))
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Módulos Fiscais preenchidos")
    
    # =========================================================================
    # ETAPA 3: Checagem de Florestas Públicas TIPO B (CNFP)
    # =========================================================================
    def _etapa_cnfp(self):
        """Verifica sobreposição com Florestas Públicas Tipo B."""
        self.log("\n3. Checagem de Florestas Públicas TIPO B...")
        
        layer_cnfp = self._carregar_camada(self.config.camada_cnfp)
        if not layer_cnfp:
            self.log("   ⚠ Camada CNFP não disponível, pulando...")
            return
        
        # Filtrar apenas Florestas Públicas TIPO B
        self.log("   3.1 Filtrando apenas TIPO B...")
        expr_str = "\"tipo\" = 'TIPO B'"
        request = QgsFeatureRequest().setFilterExpression(expr_str)
        
        # Criar camada temporária com apenas TIPO B
        cnfp_tipo_b = QgsVectorLayer(
            f"Polygon?crs={layer_cnfp.crs().authid()}",
            "cnfp_tipo_b",
            "memory"
        )
        provider = cnfp_tipo_b.dataProvider()
        provider.addAttributes(layer_cnfp.fields())
        cnfp_tipo_b.updateFields()
        
        features = [f for f in layer_cnfp.getFeatures(request)]
        provider.addFeatures(features)
        
        self.log(f"   → Florestas Públicas TIPO B encontradas: {len(features)}")
            
        # Calcular interseção apenas com TIPO B
        self.cnfp_dict = self._calcular_interseccao(self.output_layer, cnfp_tipo_b)
        
        # Atualizar camada
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_cnfp = self.cnfp_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_cnfp / area_car, 4) if area_car else 0
            
            # Verificar documentos válidos (apenas se análise completa)
            analise_completa = getattr(self, 'analise_completa', True)
            tem_doc_valido = False
            
            if analise_completa:
                docs = self._get_attr_safe(feat, ["documentos"], "")
                if docs:
                    lista_docs = [doc.strip() for doc in str(docs).split(',')]
                    tem_doc_valido = any(doc in self.config.documentos_validos_cnfp for doc in lista_docs)
            
            # Status: Não Elegível se >= 5% E não tem documento válido
            # Em análise preliminar (sem documentos), considera apenas o percentual
            if analise_completa:
                status = "Não Elegível" if (pct >= self.config.tolerancia_fp_uc and not tem_doc_valido) else "Elegível"
            else:
                # Sem acesso a documentos, qualquer sobreposição >= 5% é inelegível
                status = "Não Elegível" if pct >= self.config.tolerancia_fp_uc else "Elegível"
            
            # Atualizar campos
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_cnfp"), area_cnfp)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_cnfp"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("cnfp"), status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Florestas Públicas tipo B preenchidos")
    
    # =========================================================================
    # ETAPA 4: Checagem de Unidades de Conservação
    # =========================================================================
    def _etapa_ucs(self):
        """Verifica sobreposição com Unidades de Conservação."""
        self.log("\n4. Checagem de Unidades de Conservação...")
        
        layer_ucs = self._carregar_camada(self.config.camada_ucs)
        if not layer_ucs:
            self.log("   ⚠ Camada UCs não disponível, pulando...")
            return
            
        # Filtrar UCs que NÃO admitem uso privado (criar camada temporária)
        # Criar expressão para excluir categorias permitidas
        excl_cats = self.config.ucs_admitem_uso_privado
        expr_parts = [f"\"categoria\" != '{cat}'" for cat in excl_cats]
        expr_str = " AND ".join(expr_parts)
        
        request = QgsFeatureRequest().setFilterExpression(expr_str)
        
        # Calcular interseção apenas com UCs filtradas
        # Criar camada temporária com UCs filtradas
        ucs_filtradas = QgsVectorLayer(
            f"Polygon?crs={layer_ucs.crs().authid()}",
            "ucs_filtradas",
            "memory"
        )
        provider = ucs_filtradas.dataProvider()
        provider.addAttributes(layer_ucs.fields())
        ucs_filtradas.updateFields()
        
        features = [f for f in layer_ucs.getFeatures(request)]
        provider.addFeatures(features)
        
        self.uc_dict = self._calcular_interseccao(self.output_layer, ucs_filtradas)
        
        # Atualizar camada
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_uc = self.uc_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_uc / area_car, 4) if area_car else 0
            status = "Não Elegível" if pct > self.config.tolerancia_fp_uc else "Elegível"
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_uc"), area_uc)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_uc"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("uc"), status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Unidades de Conservação preenchidos")
    
    # =========================================================================
    # ETAPA 5: Checagem de Quilombolas
    # =========================================================================
    def _etapa_quilombolas(self):
        """Verifica sobreposição com Territórios Quilombolas."""
        self.log("\n5. Checagem de Quilombolas...")
        
        layer_quil = self._carregar_camada(self.config.camada_quilombolas)
        if not layer_quil:
            self.log("   ⚠ Camada Quilombolas não disponível, pulando...")
            return
            
        self.quil_dict = self._calcular_interseccao(self.output_layer, layer_quil)
        
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_quil = self.quil_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_quil / area_car, 4) if area_car else 0
            # Qualquer sobreposição = Não Elegível
            status = "Não Elegível" if area_quil > 0 else "Elegível"
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_quil"), area_quil)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_quil"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("quilombola"), status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Quilombolas preenchidos")
    
    # =========================================================================
    # ETAPA 6: Checagem de Terras Indígenas
    # =========================================================================
    def _etapa_terras_indigenas(self):
        """Verifica sobreposição com Terras Indígenas."""
        self.log("\n6. Checagem de Terras Indígenas...")
        
        layer_indi = self._carregar_camada(self.config.camada_terras_indigenas)
        if not layer_indi:
            self.log("   ⚠ Camada Terras Indígenas não disponível, pulando...")
            return
            
        self.indi_dict = self._calcular_interseccao(self.output_layer, layer_indi)
        
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_indi = self.indi_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_indi / area_car, 4) if area_car else 0
            status = "Não Elegível" if area_indi > 0 else "Elegível"
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_indi"), area_indi)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_indi"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("terra_indi"), status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Terras Indígenas preenchidos")
    
    # =========================================================================
    # ETAPA 7: Checagem de Embargos ICMBio
    # =========================================================================
    def _etapa_embargos_icmbio(self):
        """Verifica sobreposição e CPF com embargos ICMBio."""
        self.log("\n7. Checagem de Embargos ICMBio...")
        
        layer_emb = self._carregar_camada(self.config.camada_embargos_icmbio)
        if not layer_emb:
            self.log("   ⚠ Camada Embargos ICMBio não disponível, pulando...")
            return
        
        analise_completa = getattr(self, 'analise_completa', True)
            
        self.log("   7.1 Checagem de sobreposição")
        self.emb_icm_dict = self._calcular_interseccao(self.output_layer, layer_emb)
        
        # Verificação de CPF apenas se análise completa
        if analise_completa:
            self.log("   7.2 Checagem de CPF/CNPJ associado")
            # Coletar todos CPF/CNPJ embargados
            for feat in layer_emb.getFeatures():
                cpf = self._get_attr_safe(feat, ["cpf_cnpj", "cpf_cnpj_infrator", "cpf_cnpj_i", "cpfcnpj"])
                if cpf:
                    # Normalizar: remover tudo que não for dígito
                    cpf_norm = re.sub(r"\D", "", str(cpf))
                    self.cpf_embargos_icmbio.add(cpf_norm)
        else:
            self.log("   7.2 ⚠ CPF não disponível - checagem de CPF em embargos ignorada")
                
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_emb = self.emb_icm_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_emb / area_car, 4) if area_car else 0
            emb_status = "Não Elegível" if area_emb > 0 else "Elegível"
            
            # Verificar CPF (apenas se análise completa)
            if analise_completa:
                cpf = self._get_attr_safe(feat, ["cpf_cnpj"])
                cpf_norm = re.sub(r"\D", "", str(cpf or ""))
                cpf_status = "Não Elegível" if cpf_norm in self.cpf_embargos_icmbio else "Elegível"
            else:
                cpf_status = "N/D"  # Não Disponível
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_emb_icm"), area_emb)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_emb_icm"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("embargo_icmbio"), emb_status)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("cpf_emb_icmbio"), cpf_status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Embargos ICMBio preenchidos")
    
    # =========================================================================
    # ETAPA 8: Checagem de Embargos IBAMA
    # =========================================================================
    def _etapa_embargos_ibama(self):
        """Verifica sobreposição e CPF com embargos IBAMA."""
        self.log("\n8. Checagem de Embargos IBAMA...")
        
        layer_emb = self._carregar_camada(self.config.camada_embargos_ibama)
        if not layer_emb:
            self.log("   ⚠ Camada Embargos IBAMA não disponível, pulando...")
            return
        
        analise_completa = getattr(self, 'analise_completa', True)
            
        self.log("   8.1 Checagem de sobreposição")
        self.emb_iba_dict = self._calcular_interseccao(self.output_layer, layer_emb)
        
        # Verificação de CPF apenas se análise completa
        if analise_completa:
            self.log("   8.2 Checagem de CPF/CNPJ associado")
            # Coletar todos CPF/CNPJ embargados (campo pode ter diferentes nomes no IBAMA)
            campos_possiveis = ["cpf_cnpj_infrator", "cpf_cnpj_i", "cpf_cnpj", "cpfcnpj"]
            campos_disponiveis = [f.name() for f in layer_emb.fields()]
            campo_cpf = None
            for campo in campos_possiveis:
                if campo in campos_disponiveis:
                    campo_cpf = campo
                    break
            
            if campo_cpf:
                self.log(f"   → Usando campo: {campo_cpf}")
                for feat in layer_emb.getFeatures():
                    cpf = feat.attribute(campo_cpf)
                    if cpf:
                        cpf_norm = re.sub(r"\D", "", str(cpf))
                        self.cpf_embargos_ibama.add(cpf_norm)
            else:
                self.log("   ⚠ Campo de CPF/CNPJ não encontrado na camada de embargos IBAMA")
        else:
            self.log("   8.2 ⚠ CPF não disponível - checagem de CPF em embargos ignorada")
                
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_emb = self.emb_iba_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_emb / area_car, 4) if area_car else 0
            emb_status = "Não Elegível" if area_emb > 0 else "Elegível"
            
            # Verificar CPF (apenas se análise completa)
            if analise_completa:
                cpf = self._get_attr_safe(feat, ["cpf_cnpj"])
                cpf_norm = re.sub(r"\D", "", str(cpf or ""))
                cpf_status = "Não Elegível" if cpf_norm in self.cpf_embargos_ibama else "Elegível"
            else:
                cpf_status = "N/D"  # Não Disponível
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_emb_iba"), area_emb)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_emb_iba"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("embargo_ibama"), emb_status)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("cpf_emb_ibama"), cpf_status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para Embargos IBAMA preenchidos")
    
    # =========================================================================
    # ETAPA 9: Sobreposição entre imóveis CAR
    # =========================================================================
    def _etapa_sobreposicao_car(self):
        """Verifica sobreposição com outros cadastros CAR (AT/PE)."""
        self.log("\n9. Checagem de sobreposição com outros cadastros (AT/PE)...")
        
        layer_car = self._carregar_camada(self.config.camada_car_amazonia)
        if not layer_car:
            self.log("   ⚠ Camada CAR Amazônia não disponível, pulando...")
            return
        
        # =====================================================================
        # FILTROS APLICADOS À CAMADA CAR AMAZÔNIA
        # =====================================================================
        
        # 1) Carregar lista de cod_imovel a remover (da configuração)
        lista_remover = set()
        if self.config.lista_car_remover:
            for linha in self.config.lista_car_remover.strip().split('\n'):
                cod = linha.strip()
                if cod:
                    lista_remover.add(cod)
        self.log(f"   9.1 Lista de CARs a remover: {len(lista_remover)} códigos")
        
        # Identificar campo de status (pode ter nomes diferentes)
        campos_disponiveis = [f.name() for f in layer_car.fields()]
        self.log(f"   9.2 Campos disponíveis na camada CAR: {len(campos_disponiveis)}")
        
        # Procurar campo de status
        campo_status = None
        campos_status_possiveis = ['ind_status', 'status_imo', 'status', 'ind_stat', 'situacao']
        for campo in campos_status_possiveis:
            if campo in campos_disponiveis:
                campo_status = campo
                break
        
        # Procurar campo de tipo
        campo_tipo = None
        campos_tipo_possiveis = ['tipo_imove', 'ind_tipo', 'tipo', 'tipo_imovel']
        for campo in campos_tipo_possiveis:
            if campo in campos_disponiveis:
                campo_tipo = campo
                break
        
        if campo_status:
            self.log(f"   → Campo de status: {campo_status}")
        else:
            self.log(f"   ⚠ Campo de status não encontrado! Campos: {campos_disponiveis[:10]}...")
        
        if campo_tipo:
            self.log(f"   → Campo de tipo: {campo_tipo}")
        else:
            self.log(f"   ⚠ Campo de tipo não encontrado!")
        
        # Criar camada filtrada aplicando os filtros
        car_filtrado = QgsVectorLayer(
            f"Polygon?crs={layer_car.crs().authid()}",
            "car_filtrado",
            "memory"
        )
        provider = car_filtrado.dataProvider()
        provider.addAttributes(layer_car.fields())
        car_filtrado.updateFields()
        
        # Contadores para log
        total_processados = 0
        total_at_pe = 0
        removidos_lista = 0
        removidos_modulos = 0
        removidos_ast = 0
        
        # Valores de status válidos (AT = Ativo, PE = Pendente)
        status_validos = {'AT', 'PE', 'at', 'pe', 'At', 'Pe', 'Ativo', 'Pendente', 'ATIVO', 'PENDENTE'}
        
        features_filtradas = []
        for feat in layer_car.getFeatures():
            total_processados += 1
            
            # Verificar status (se campo existir)
            if campo_status:
                status_val = str(feat.attribute(campo_status) or "").strip()
                if status_val not in status_validos:
                    continue
            
            total_at_pe += 1
            cod_imovel = self._get_attr_safe(feat, ["cod_imovel", "n_do_car"], "")
            
            # FILTRO 1: Remover se cod_imovel está na lista
            if cod_imovel in lista_remover:
                removidos_lista += 1
                continue
            
            # FILTRO 2: area / m_fiscal > 111 → remover
            area = self._get_attr_safe(feat, ["area", "area_imove", "num_area"], 0)
            m_fiscal = self._get_attr_safe(feat, ["m_fiscal", "modulo_f", "mod_fiscal"], 0)
            if m_fiscal > 0 and area > 0:
                modulos_calc = area / m_fiscal
                if modulos_calc > 111:
                    removidos_modulos += 1
                    continue
            
            # FILTRO 3: tipo = 'AST' e m_fiscal > 10 → remover
            tipo_imove = ""
            if campo_tipo:
                tipo_imove = str(feat.attribute(campo_tipo) or "").strip()
            if tipo_imove == "AST" and m_fiscal > 10:
                removidos_ast += 1
                continue
            
            # Passou em todos os filtros
            features_filtradas.append(feat)
        
        provider.addFeatures(features_filtradas)
        
        self.log(f"   9.3 Filtros aplicados:")
        self.log(f"       - Total de registros na camada: {total_processados}")
        self.log(f"       - Com status AT/PE: {total_at_pe}")
        self.log(f"       - Removidos por lista: {removidos_lista}")
        self.log(f"       - Removidos por área/m_fiscal > 111: {removidos_modulos}")
        self.log(f"       - Removidos por AST com m_fiscal > 10: {removidos_ast}")
        self.log(f"       - Restantes para análise: {len(features_filtradas)}")
        
        # Criar índice espacial
        index_car = QgsSpatialIndex(car_filtrado.getFeatures())
        
        # Calcular sobreposição (excluindo o próprio imóvel)
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            geom = feat.geometry()
            car_atual = self._get_attr_safe(feat, ["n_do_car", "cod_imovel"])
            
            if geom.isEmpty():
                continue
                
            candidates = index_car.intersects(geom.boundingBox())
            
            area_sobrep = 0.0
            for fid_car in candidates:
                feat_car = car_filtrado.getFeature(fid_car)
                car_outro = feat_car.attribute("cod_imovel") or feat_car.attribute("n_do_car")
                
                # Ignorar se for o mesmo CAR
                if car_atual == car_outro:
                    continue
                    
                geom_car = feat_car.geometry()
                if geom.intersects(geom_car):
                    intersection = geom.intersection(geom_car)
                    if not intersection.isEmpty():
                        area = self._calcular_area_ha(intersection, self.output_layer.crs())
                        area_sobrep += area
                        
            if area_sobrep > 0:
                self.sobrep_dict[fid] = round(area_sobrep, 2)
                
        # Atualizar camada
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_sobrep = self.sobrep_dict.get(fid, 0)
            area_car = self.area_dict.get(fid, 0)
            
            pct = round(area_sobrep / area_car, 4) if area_car else 0
            status = "Não Elegível" if pct > self.config.tolerancia_sobreposicao_car else "Elegível"
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_sobrep"), pct)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("sobrep_car"), status)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para sobreposição com outros cadastros preenchidos")
    
    # =========================================================================
    # ETAPA 10: Checagem do PRODES
    # =========================================================================
    def _etapa_prodes(self):
        """Verifica sobreposição com desmatamento PRODES."""
        self.log("\n10. Checagem do PRODES...")
        
        layer_prodes = self._carregar_camada(self.config.camada_prodes)
        if not layer_prodes:
            self.log("   ⚠ Camada PRODES não disponível, pulando...")
            return
            
        self.prodes_dict = self._calcular_interseccao(self.output_layer, layer_prodes)
        
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_prodes = self.prodes_dict.get(fid, 0)
            
            status_1ha = "Não Elegível" if area_prodes >= self.config.limite_prodes_1ha else "Elegível"
            status_6ha = "Não Elegível" if area_prodes >= self.config.limite_prodes_6ha else "Elegível"
            
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("area_prodes"), area_prodes)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("prodes_1ha"), status_1ha)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("prodes_6ha"), status_6ha)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para PRODES preenchidos")
    
    # =========================================================================
    # ETAPA 11: Fitofisionomia e RVN
    # =========================================================================
    def _etapa_fitofisionomia_rvn(self):
        """Calcula fitofisionomia e RVN para cada imóvel."""
        self.log("\n11. Fitofisionomia e RVN...")
        
        layer_fit = self._carregar_camada(self.config.camada_fitofisionomia)
        layer_rvn = self._carregar_camada(self.config.camada_rvn)
        
        # 1) Fitofisionomia por imóvel
        if layer_fit:
            self.log("   11.1 Identificando fitofisionomias por imóvel...")
            index_fit = QgsSpatialIndex(layer_fit.getFeatures())
            
            for feat in self.output_layer.getFeatures():
                fid = feat.id()
                geom = feat.geometry()
                
                if geom.isEmpty():
                    continue
                    
                candidates = index_fit.intersects(geom.boundingBox())
                classes = set()
                
                for fid_fit in candidates:
                    feat_fit = layer_fit.getFeature(fid_fit)
                    geom_fit = feat_fit.geometry()
                    
                    if geom.intersects(geom_fit):
                        classe = feat_fit.attribute("Classe")
                        if classe:
                            classes.add(classe)
                            
                if classes:
                    self.fit_dict[fid] = classes
        else:
            self.log("   ⚠ Camada Fitofisionomia não disponível")
            
        # 2) RVN total por imóvel
        if layer_rvn:
            self.log("   11.2 Calculando área de RVN por imóvel...")
            self.rvn_dict = self._calcular_interseccao(self.output_layer, layer_rvn)
            
            # 3) RVN por fitofisionomia
            if layer_fit:
                self.log("   11.3 Calculando RVN por fitofisionomia...")
                for fito_nome in ["Floresta", "Cerrado", "Campos Gerais"]:
                    # Filtrar fitofisionomia
                    expr = f"\"Classe\" = '{fito_nome}'"
                    request = QgsFeatureRequest().setFilterExpression(expr)
                    
                    fito_layer = QgsVectorLayer(
                        f"Polygon?crs={layer_fit.crs().authid()}",
                        f"fito_{fito_nome}",
                        "memory"
                    )
                    provider = fito_layer.dataProvider()
                    provider.addAttributes(layer_fit.fields())
                    fito_layer.updateFields()
                    
                    features = [f for f in layer_fit.getFeatures(request)]
                    if not features:
                        continue
                    provider.addFeatures(features)
                    
                    # Calcular interseção tripla: imóvel x rvn x fito
                    index_fito = QgsSpatialIndex(fito_layer.getFeatures())
                    index_rvn = QgsSpatialIndex(layer_rvn.getFeatures())
                    
                    for feat in self.output_layer.getFeatures():
                        fid = feat.id()
                        geom = feat.geometry()
                        
                        if geom.isEmpty():
                            continue
                            
                        # Encontrar interseção com fito
                        candidates_fito = index_fito.intersects(geom.boundingBox())
                        
                        area_rvn_fito = 0.0
                        for fid_fito in candidates_fito:
                            feat_fito = fito_layer.getFeature(fid_fito)
                            geom_fito = feat_fito.geometry()
                            
                            if not geom.intersects(geom_fito):
                                continue
                                
                            # Área do imóvel dentro dessa fito
                            geom_imovel_fito = geom.intersection(geom_fito)
                            
                            if geom_imovel_fito.isEmpty():
                                continue
                                
                            # Encontrar RVN dentro dessa área
                            candidates_rvn = index_rvn.intersects(geom_imovel_fito.boundingBox())
                            
                            for fid_rvn in candidates_rvn:
                                feat_rvn = layer_rvn.getFeature(fid_rvn)
                                geom_rvn = feat_rvn.geometry()
                                
                                if geom_imovel_fito.intersects(geom_rvn):
                                    intersection = geom_imovel_fito.intersection(geom_rvn)
                                    if not intersection.isEmpty():
                                        area = self._calcular_area_ha(intersection, self.output_layer.crs())
                                        area_rvn_fito += area
                                        
                        if area_rvn_fito > 0:
                            self.rvn_fito_dict[fito_nome][fid] = round(area_rvn_fito, 2)
        else:
            self.log("   ⚠ Camada RVN não disponível")
            
        # Atualizar camada
        self.output_layer.startEditing()
        
        thresholds = {
            "Floresta": self.config.threshold_rvn_floresta,
            "Cerrado": self.config.threshold_rvn_cerrado,
            "Campos Gerais": self.config.threshold_rvn_campos
        }
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_car = self.area_dict.get(fid, 0)
            area_imovel = self._get_attr_safe(feat, ["area_imove", "area", "num_area"], area_car)
            
            # Classes de fitofisionomia
            classes = self.fit_dict.get(fid, set())
            fit_text = ", ".join(sorted(classes))
            
            # Área e percentual RVN (limitado ao tamanho do imóvel)
            rvn_area = self.rvn_dict.get(fid, 0)
            if rvn_area > area_imovel:
                rvn_area = area_imovel
            pct_rvn = (rvn_area / area_car) if area_car else 0
            
            # Maior threshold exigido
            thresh = max((thresholds.get(c, 0) for c in classes), default=0)
            rvn_min = area_car * thresh
            
            # Julgamento
            status = "Elegível" if (rvn_area >= 1 and pct_rvn >= thresh) else "Não Elegível"
            
            # RVN por fito
            rvn_floresta = self.rvn_fito_dict["Floresta"].get(fid, 0)
            rvn_cerrado = self.rvn_fito_dict["Cerrado"].get(fid, 0)
            rvn_campo = self.rvn_fito_dict["Campos Gerais"].get(fid, 0)
            
            rvn_floresta_p = (rvn_floresta / rvn_area) if rvn_area else 0
            rvn_cerrado_p = (rvn_cerrado / rvn_area) if rvn_area else 0
            rvn_campo_p = (rvn_campo / rvn_area) if rvn_area else 0
            
            # Atualizar campos
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("fitofisionomia"), fit_text)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("RVN_area"), round(rvn_area, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("percent_rvn"), round(pct_rvn, 4))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("RVN_min_lei"), round(rvn_min, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_minima"), status)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_floresta"), round(rvn_floresta, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_cerrado"), round(rvn_cerrado, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_campo"), round(rvn_campo, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_floresta_p"), round(rvn_floresta_p, 4))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_cerrado_p"), round(rvn_cerrado_p, 4))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("rvn_campo_p"), round(rvn_campo_p, 4))
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos para RVN e fitofisionomia preenchidos")
    
    # =========================================================================
    # ETAPA 12: Verificação de Elegibilidade (Fase 1 e Fase 2)
    # =========================================================================
    def _etapa_elegibilidade(self):
        """Verifica elegibilidade combinada para Fase 1 e Fase 2."""
        self.log("\n12. Verificação combinada de elegibilidade...")
        
        # Usar geocódigos prioritários da configuração
        geocodigos_prio = self.config.geocodigos_prioritarios
        self.log(f"   12.1 Geocódigos prioritários configurados: {len(geocodigos_prio)}")
        
        # Carregar camada de municípios para obter nomes
        layer_mun = self._carregar_camada(self.config.camada_municipios)
        index_mun = None
        
        # Criar mapeamento geocódigo -> nome do município (normalizado)
        nomes_municipios_prioritarios = set()
        geocodigo_para_nome = {}
        
        if layer_mun:
            index_mun = QgsSpatialIndex(layer_mun.getFeatures())
            self.log(f"   12.2 Camada de municípios carregada: {layer_mun.featureCount()} municípios")
            
            # Identificar campos de geocódigo e nome na camada de municípios
            campos_mun = [f.name().lower() for f in layer_mun.fields()]
            campo_geo_mun = None
            campo_nome_mun = None
            
            for campo in ["geocodigo", "cod_municipio", "codmun", "cd_mun", "ibge", "geocodigo"]:
                if campo in campos_mun:
                    campo_geo_mun = campo
                    break
            
            for campo in ["nome", "nm_mun", "municipio", "nome_municipio", "nm_municip"]:
                if campo in campos_mun:
                    campo_nome_mun = campo
                    break
            
            if campo_geo_mun and campo_nome_mun:
                self.log(f"   → Campo geocódigo: {campo_geo_mun}, Campo nome: {campo_nome_mun}")
                
                # Criar conjunto de nomes de municípios prioritários
                for feat_mun in layer_mun.getFeatures():
                    geo = str(feat_mun.attribute(campo_geo_mun) or "").strip()
                    nome = str(feat_mun.attribute(campo_nome_mun) or "").strip()
                    
                    if geo and nome:
                        geocodigo_para_nome[geo] = nome
                        # Se este geocódigo está na lista de prioritários, adicionar o nome
                        if geo in geocodigos_prio:
                            # Normalizar nome (maiúsculo, sem acentos extras)
                            nome_norm = nome.upper().strip()
                            nomes_municipios_prioritarios.add(nome_norm)
                
                self.log(f"   12.3 Municípios prioritários identificados por nome: {len(nomes_municipios_prioritarios)}")
            else:
                self.log(f"   ⚠ Campos de geocódigo ou nome não encontrados na camada de municípios")
        
        # Verificar campo 'municipio' nos imóveis
        campos_imoveis = [f.name().lower() for f in self.output_layer.fields()]
        campo_municipio_imovel = None
        for campo in ["municipio", "nm_municipio", "nome_municipio", "mun"]:
            if campo in campos_imoveis:
                campo_municipio_imovel = campo
                break
        
        if campo_municipio_imovel:
            self.log(f"   → Campo de município no imóvel: {campo_municipio_imovel}")
        
        # Campos para geocódigo no imóvel (fallback)
        campos_geocodigo = ["geocodigo", "cod_municipio", "codmun", "cd_mun", "ibge"]
        campo_geo_imovel = None
        for campo in campos_geocodigo:
            if campo in campos_imoveis:
                campo_geo_imovel = campo
                break
        
        # Mapear imóvel -> se está em município prioritário
        imovel_em_prioritario = {}
        metodo_usado = {"nome": 0, "geocodigo": 0, "espacial": 0}
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            in_prio = False
            
            # OPÇÃO 1: Verificar pelo campo 'municipio' do imóvel
            if campo_municipio_imovel and nomes_municipios_prioritarios:
                nome_mun_imovel = str(feat.attribute(campo_municipio_imovel) or "").upper().strip()
                if nome_mun_imovel and nome_mun_imovel in nomes_municipios_prioritarios:
                    in_prio = True
                    metodo_usado["nome"] += 1
            
            # OPÇÃO 2: Verificar pelo geocódigo do próprio imóvel
            if not in_prio and campo_geo_imovel:
                geocodigo = str(feat.attribute(campo_geo_imovel) or "").strip()
                if geocodigo and geocodigo in geocodigos_prio:
                    in_prio = True
                    metodo_usado["geocodigo"] += 1
            
            # OPÇÃO 3: Verificar por interseção espacial com municípios
            if not in_prio and index_mun:
                geom = feat.geometry()
                if not geom.isEmpty():
                    centroid = geom.centroid()
                    candidates = index_mun.intersects(centroid.boundingBox())
                    for fid_mun in candidates:
                        feat_mun = layer_mun.getFeature(fid_mun)
                        if centroid.intersects(feat_mun.geometry()):
                            # Buscar geocódigo do município
                            for campo in campos_geocodigo:
                                geo = feat_mun.attribute(campo)
                                if geo:
                                    geocodigo = str(geo).strip()
                                    if geocodigo in geocodigos_prio:
                                        in_prio = True
                                        metodo_usado["espacial"] += 1
                                    break
                            break
            
            imovel_em_prioritario[fid] = in_prio
        
        total_prioritarios = sum(1 for v in imovel_em_prioritario.values() if v)
        self.log(f"   12.4 Imóveis em municípios prioritários: {total_prioritarios}")
        self.log(f"       - Por nome do município: {metodo_usado['nome']}")
        self.log(f"       - Por geocódigo no imóvel: {metodo_usado['geocodigo']}")
        self.log(f"       - Por interseção espacial: {metodo_usado['espacial']}")
            
        # Verificar quais imóveis estão dentro da Amazônia Legal
        layer_amzl = self._carregar_camada(self.config.camada_amazonia_legal)
        if layer_amzl:
            index_amzl = QgsSpatialIndex(layer_amzl.getFeatures())
            
            for feat in self.output_layer.getFeatures():
                geom = feat.geometry()
                if geom.isEmpty():
                    continue
                    
                candidates = index_amzl.intersects(geom.boundingBox())
                for fid_amzl in candidates:
                    feat_amzl = layer_amzl.getFeature(fid_amzl)
                    if geom.intersects(feat_amzl.geometry()):
                        self.imoveis_amazonia_legal.add(feat.id())
                        break
                        
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            
            # Dados do imóvel
            status_val = self._get_attr_safe(feat, ["status", "ind_status", "status_imo"], "")
            cond_val = self._get_attr_safe(feat, ["condicao", "des_condic"], "")
            
            # Verificar se está em município prioritário (já calculado acima)
            in_prio = imovel_em_prioritario.get(fid, False)
            
            # Flags Fase 1
            stat_f1 = status_val in ("AT", "PE")
            cond_f1 = cond_val not in self.config.condicoes_invalidas_f1
            
            # Flags Fase 2
            in_amzl = fid in self.imoveis_amazonia_legal
            stat_f2 = status_val == "AT"
            cond_f2 = cond_val in self.config.condicoes_invalidas_f1
            
            # Converter para texto
            prio_flag = "Elegível" if in_prio else "Não Elegível"
            stat1_flag = "Elegível" if stat_f1 else "Não Elegível"
            cond1_flag = "Elegível" if cond_f1 else "Não Elegível"
            amzl_flag = "Elegível" if in_amzl else "Não Elegível"
            stat2_flag = "Elegível" if stat_f2 else "Não Elegível"
            cond2_flag = "Elegível" if cond_f2 else "Não Elegível"
            
            # Motivos de inelegibilidade
            reasons_f1 = []
            if not in_prio:
                reasons_f1.append("está fora dos municípios prioritários")
            if not stat_f1:
                reasons_f1.append("o status SICAR não corresponde AT ou PE")
            if not cond_f1:
                reasons_f1.append("imóvel já analisado")
                
            reasons_f2 = []
            if not in_amzl:
                reasons_f2.append("está fora da Amazônia Legal")
            if not stat_f2:
                reasons_f2.append("o status SICAR não corresponde AT")
            if not cond_f2:
                reasons_f2.append("imóvel não analisado")
                
            # Verificar outros critérios
            mod_flag = feat.attribute("modulos_imovel")
            soma_flag = feat.attribute("soma_modulos")
            soma_real = feat.attribute("soma_modulos_real") or 0
            cnfp_flag = feat.attribute("cnfp")
            uc_flag = feat.attribute("uc")
            quil_flag = feat.attribute("quilombola")
            indi_flag = feat.attribute("terra_indi")
            icm_flag = feat.attribute("embargo_icmbio")
            cpf_icm_flag = feat.attribute("cpf_emb_icmbio")
            iba_flag = feat.attribute("embargo_ibama")
            cpf_iba_flag = feat.attribute("cpf_emb_ibama")
            sobrep_flag = feat.attribute("sobrep_car")
            prodes_flag = feat.attribute("prodes_6ha")
            rvn_flag = feat.attribute("rvn_minima")
            
            mod_val = self._get_attr_safe(feat, ["modulo_f", "m_fiscal", "mod_fiscal"], 0)
            
            # Adicionar motivos adicionais
            if mod_flag == "Não Elegível":
                msg = f"imóvel com {mod_val} Módulos Fiscais"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if soma_flag == "Não Elegível":
                msg = f"soma de {soma_real:.2f} Módulos Fiscais por CPF"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if cnfp_flag == "Não Elegível":
                msg = "possui mais de 5% de sobreposição com Floresta Pública Tipo B"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if uc_flag == "Não Elegível":
                msg = "possui mais de 5% de sobreposição com Unidade de Conservação"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if quil_flag == "Não Elegível":
                msg = "possui sobreposição com Território Remanescente de Quilombola"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if indi_flag == "Não Elegível":
                msg = "possui sobreposição com Terra Indígena"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if icm_flag == "Não Elegível":
                msg = "área com embargo do ICMBio"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if cpf_icm_flag == "Não Elegível":
                reasons_f1.append("CPF com algum embargo na base do ICMBio")
                reasons_f2.append("CPF com algum embargo na base do ICMBio")
                
            if iba_flag == "Não Elegível":
                msg = "área com embargo do IBAMA"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if cpf_iba_flag == "Não Elegível":
                reasons_f1.append("CPF com algum embargo na base do IBAMA")
                reasons_f2.append("CPF com algum embargo na base do IBAMA")
                
            if sobrep_flag == "Não Elegível":
                msg = "possui mais de 50% de sobreposição com outro imóvel AT ou PE da base SICAR"
                reasons_f1.append(msg)
                
            if prodes_flag == "Não Elegível":
                msg = "possui mais de 6,25ha de sobreposição com PRODES após 22/07/2008"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            if rvn_flag == "Não Elegível":
                msg = "não possui RVN suficiente, conforme fitofisionomia"
                reasons_f1.append(msg)
                reasons_f2.append(msg)
                
            # Resultado final
            elegivel_f1 = "SIM" if not reasons_f1 else ", ".join(reasons_f1)
            elegivel_f2 = "SIM" if not reasons_f2 else ", ".join(reasons_f2)
            
            # Atualizar campos
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("em_prioritarios"), prio_flag)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("chek_status_f1"), stat1_flag)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("condicao_Fase1"), cond1_flag)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("dentro_da_amzl"), amzl_flag)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("chek_status_f2"), stat2_flag)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("condicao_Fase2"), cond2_flag)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Elegivel_F1"), elegivel_f1)
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Elegivel_F2"), elegivel_f2)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Campos de elegibilidade F1 e F2 atualizados")
    
    # =========================================================================
    # ETAPA 13: Julgamento Final de Elegibilidade
    # =========================================================================
    def _etapa_julgamento(self):
        """Define elegibilidade final (Fase 1, Fase 2 ou Inelegível)."""
        self.log("\n13. Julgamento de elegibilidade...")
        
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            elegivel_f1 = feat.attribute("Elegivel_F1")
            elegivel_f2 = feat.attribute("Elegivel_F2")
            
            if elegivel_f1 == "SIM":
                elegibilidade = "Fase 1"
            elif elegivel_f2 == "SIM":
                elegibilidade = "Fase 2"
            else:
                elegibilidade = "Inelegível"
                
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("elegibilidade"), elegibilidade)
            # Cert MPF - por enquanto sempre Elegível
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("cert_mpf"), "Elegível")
            
        self.output_layer.commitChanges()
        self.log("   ✓ Julgamento de elegibilidade concluído")
    
    # =========================================================================
    # ETAPA 14: Cálculo de Valores
    # =========================================================================
    def _etapa_calcular_valores(self):
        """Calcula valores de pagamento por faixa."""
        self.log("\n14. Cálculo de valores...")
        
        thresholds_f2 = {
            "Floresta": self.config.threshold_rvn_floresta_f2,
            "Cerrado": self.config.threshold_rvn_cerrado_f2,
            "Campos Gerais": self.config.threshold_rvn_campos_f2
        }
        
        # Calcular área por classe de fitofisionomia
        layer_fit = self._carregar_camada(self.config.camada_fitofisionomia)
        class_areas = {"Floresta": {}, "Cerrado": {}, "Campos Gerais": {}}
        
        if layer_fit:
            for cls in class_areas.keys():
                expr = f"\"Classe\" = '{cls}'"
                request = QgsFeatureRequest().setFilterExpression(expr)
                
                fito_layer = QgsVectorLayer(
                    f"Polygon?crs={layer_fit.crs().authid()}",
                    f"fito_{cls}",
                    "memory"
                )
                provider = fito_layer.dataProvider()
                provider.addAttributes(layer_fit.fields())
                fito_layer.updateFields()
                
                features = [f for f in layer_fit.getFeatures(request)]
                if features:
                    provider.addFeatures(features)
                    areas = self._calcular_interseccao(self.output_layer, fito_layer)
                    class_areas[cls] = areas
                    
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            area_car = self.area_dict.get(fid, 0)
            
            # Áreas por classe
            floresta_ha = class_areas["Floresta"].get(fid, 0)
            cerrado_ha = class_areas["Cerrado"].get(fid, 0)
            campos_ha = class_areas["Campos Gerais"].get(fid, 0)
            
            floresta_per = (floresta_ha / area_car) if area_car else 0
            cerrado_per = (cerrado_ha / area_car) if area_car else 0
            campos_per = (campos_ha / area_car) if area_car else 0
            
            # RVN total (já limitado ao tamanho do imóvel)
            rvn_tot = feat.attribute("RVN_area") or 0
            
            # Mínima legal (Fase 2 - 80% Floresta)
            rvn_min = (
                floresta_ha * thresholds_f2["Floresta"] +
                cerrado_ha * thresholds_f2["Cerrado"] +
                campos_ha * thresholds_f2["Campos Gerais"]
            )
            
            # Faixa 1: até RVN mínima
            f1 = min(rvn_tot, rvn_min)
            f1_l = min(f1, self.config.faixa1_limite_ha)
            f1_v = f1_l * self.config.faixa1_valor_ha
            
            # Faixa 2: excedente
            ex = max(rvn_tot - rvn_min, 0)
            f2_l = min(ex, self.config.faixa2_limite_ha)
            f2_v = f2_l * self.config.faixa2_valor_ha
            
            # Total
            elegivel_f1 = feat.attribute("Elegivel_F1")
            elegivel_f2 = feat.attribute("Elegivel_F2")
            
            if elegivel_f1 == "SIM":
                total = self.config.valor_fase1
            else:
                total = max(f1_v + f2_v, self.config.valor_minimo)
                
            # Atualizar campos
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Floresta_ha"), round(floresta_ha, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Floresta_per"), round(floresta_per, 4))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Cerrado_ha"), round(cerrado_ha, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Cerrado_per"), round(cerrado_per, 4))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Campos_Gerais_ha"), round(campos_ha, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Campos_Gerais_per"), round(campos_per, 4))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("RVN_Min_calc_f2"), round(rvn_min, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Faixa_1_ha"), round(f1, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Faixa_1_val"), round(f1_v, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Faixa_2_ha"), round(ex, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Faixa_2_val"), round(f2_v, 2))
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("Total_rec"), round(total, 2))
            
        self.output_layer.commitChanges()
        self.log("   ✓ Valores calculados")
    
    # =========================================================================
    # ETAPA 15: Geração do Parecer
    # =========================================================================
    def _etapa_parecer(self):
        """Gera o parecer consolidado para cada imóvel."""
        self.log("\n15. Geração do parecer...")
        
        self.output_layer.startEditing()
        
        for feat in self.output_layer.getFeatures():
            fid = feat.id()
            
            eleg = feat.attribute("elegibilidade")
            f1_ha = feat.attribute("Faixa_1_ha") or 0
            f1_val = feat.attribute("Faixa_1_val") or 0
            f2_ha = feat.attribute("Faixa_2_ha") or 0
            f2_val = feat.attribute("Faixa_2_val") or 0
            total_val = feat.attribute("Total_rec") or 0
            motivo_f1 = feat.attribute("Elegivel_F1") or ""
            motivo_f2 = feat.attribute("Elegivel_F2") or ""
            fit_txt = feat.attribute("fitofisionomia") or ""
            
            fl_ha = feat.attribute("rvn_floresta") or 0
            fl_per = feat.attribute("rvn_floresta_p") or 0
            ce_ha = feat.attribute("rvn_cerrado") or 0
            ce_per = feat.attribute("rvn_cerrado_p") or 0
            cg_ha = feat.attribute("rvn_campo") or 0
            cg_per = feat.attribute("rvn_campo_p") or 0
            
            # Gerar texto do parecer
            if eleg == "Inelegível":
                texto = (
                    f"       O imóvel encontra-se INELEGÍVEL, pois não atendeu critérios da Fase 1: "
                    f"{motivo_f1}. E da Fase 2: {motivo_f2}."
                )
            elif eleg == "Fase 1":
                texto = (
                    "       O imóvel encontra-se ELEGÍVEL, atendendo aos critérios dos itens 1 a 17 da FASE 1. "
                    "O Valor do pagamento corresponde a R$ 1.500,00."
                )
            elif eleg == "Fase 2":
                total_str = f"{total_val:,.2f}".replace(",", "v").replace(".", ",").replace("v", ".")
                f1_val_str = f"{f1_val:,.2f}".replace(",", "v").replace(".", ",").replace("v", ".")
                f2_val_str = f"{f2_val:,.2f}".replace(",", "v").replace(".", ",").replace("v", ".")
                texto = (
                    "       O imóvel encontra-se ELEGÍVEL, atendendo aos critérios dos itens 1 a 16 da FASE 2. "
                    f"O Valor do pagamento é de R$ {total_str}, resultado da soma de: "
                    f"1) R$ {f1_val_str}, correspondente a 80% do Remanescente de Vegetação Nativa ou {f1_ha:.2f}ha; e "
                    f"2) R$ {f2_val_str}, correspondente ao excedente do Remanescente de Vegetação Nativa, acima de 80% ou {f2_ha:.2f}ha."
                )
            else:
                texto = None
                
            # Complemento por fitofisionomia
            if texto and eleg in ("Fase 1", "Fase 2") and fit_txt:
                fitos = []
                areas = []
                pers = []
                
                if "Floresta" in fit_txt and fl_ha > 0:
                    fitos.append("Floresta")
                    areas.append(f"{fl_ha:.2f} ha")
                    pers.append(f"({fl_per*100:.2f}%)")
                if "Cerrado" in fit_txt and ce_ha > 0:
                    fitos.append("Cerrado")
                    areas.append(f"{ce_ha:.2f} ha")
                    pers.append(f"({ce_per*100:.2f}%)")
                if "Campos Gerais" in fit_txt and cg_ha > 0:
                    fitos.append("Campos Gerais")
                    areas.append(f"{cg_ha:.2f} ha")
                    pers.append(f"({cg_per*100:.2f}%)")
                    
                if fitos:
                    if len(fitos) == 1:
                        frase = (
                            f"O Remanescente de Vegetação Nativa está 100% concentrado sobre a Fitofisionomia {fitos[0]}, "
                            f"com área de {areas[0]}."
                        )
                    else:
                        fitos_frase = " e ".join([", ".join(fitos[:-1]), fitos[-1]]) if len(fitos) > 2 else " e ".join(fitos)
                        detalhes = [f"{a}, {p}" for a, p in zip(areas, pers)]
                        detalhes_frase = ", ".join(detalhes[:-1]) + f" e {detalhes[-1]}" if len(detalhes) > 1 else detalhes[0]
                        frase = (
                            f"O Remanescente de Vegetação Nativa está sobre as Fitofisionomias {fitos_frase} "
                            f"com área de {detalhes_frase}, respectivamente."
                        )
                    texto += " " + frase
                    
            self.output_layer.changeAttributeValue(fid, self.output_layer.fields().indexOf("parecer"), texto)
            
        self.output_layer.commitChanges()
        self.log("   ✓ Parecer gerado para todos os imóveis")
    
    # =========================================================================
    # MÉTODO PRINCIPAL: Executar Processamento
    # =========================================================================
    def executar(self) -> Optional[QgsVectorLayer]:
        """
        Executa o processamento completo de elegibilidade.
        
        Returns:
            Camada de saída com todos os campos preenchidos ou None em caso de erro.
        """
        try:
            self.log("=" * 60)
            self.log("INICIANDO PROCESSAMENTO DE ELEGIBILIDADE")
            self.log("=" * 60)
            
            # Informar tipo de análise
            tipo_fonte = getattr(self.config, 'tipo_fonte', 'completa')
            if tipo_fonte == 'completa':
                self.log("📊 Tipo de análise: COMPLETA (dados do Metabase/SICAR)")
            elif tipo_fonte == 'geoserver':
                self.log("📊 Tipo de análise: PRELIMINAR (dados do Geoserver)")
                self.log("   ⚠ Análises indisponíveis: soma módulos por CPF, embargos por CPF, documentos CNFP")
            elif tipo_fonte == 'consulta_publica':
                self.log("📊 Tipo de análise: PRELIMINAR (dados da Consulta Pública)")
                self.log("   ⚠ Análises indisponíveis: soma módulos por CPF, embargos por CPF, documentos CNFP")
            else:
                self.log("📊 Tipo de análise: PADRÃO")
            
            # Armazenar tipo para uso nas etapas
            self.tipo_fonte = tipo_fonte
            self.analise_completa = (tipo_fonte == 'completa')
            
            # Carregar camada de imóveis
            layer_imoveis = self._carregar_camada(self.config.camada_imoveis)
            if not layer_imoveis:
                self.log("❌ Camada de imóveis não encontrada!")
                return None
                
            self.log(f"✓ Camada de imóveis carregada: {layer_imoveis.featureCount()} feições")
            
            # Criar camada de saída
            self._criar_camada_saida(layer_imoveis)
            
            total_etapas = 15
            
            # Executar etapas
            self.progress(1, total_etapas)
            self._etapa_calcular_areas()
            
            self.progress(2, total_etapas)
            self._etapa_modulos_fiscais()
            
            self.progress(3, total_etapas)
            self._etapa_cnfp()
            
            self.progress(4, total_etapas)
            self._etapa_ucs()
            
            self.progress(5, total_etapas)
            self._etapa_quilombolas()
            
            self.progress(6, total_etapas)
            self._etapa_terras_indigenas()
            
            self.progress(7, total_etapas)
            self._etapa_embargos_icmbio()
            
            self.progress(8, total_etapas)
            self._etapa_embargos_ibama()
            
            self.progress(9, total_etapas)
            self._etapa_sobreposicao_car()
            
            self.progress(10, total_etapas)
            self._etapa_prodes()
            
            self.progress(11, total_etapas)
            self._etapa_fitofisionomia_rvn()
            
            self.progress(12, total_etapas)
            self._etapa_elegibilidade()
            
            self.progress(13, total_etapas)
            self._etapa_julgamento()
            
            self.progress(14, total_etapas)
            self._etapa_calcular_valores()
            
            self.progress(15, total_etapas)
            self._etapa_parecer()
            
            self.log("=" * 60)
            self.log("PROCESSAMENTO CONCLUÍDO COM SUCESSO!")
            self.log("=" * 60)
            
            return self.output_layer
            
        except Exception as e:
            self.log(f"❌ ERRO NO PROCESSAMENTO: {str(e)}")
            traceback.print_exc()
            return None
    
    def salvar_resultado(self, caminho_saida: str) -> bool:
        """
        Salva o resultado no GeoPackage.
        
        Args:
            caminho_saida: Caminho completo para salvar (gpkg|layername)
            
        Returns:
            True se salvou com sucesso, False caso contrário.
        """
        if not self.output_layer:
            self.log("❌ Nenhum resultado para salvar")
            return False
            
        try:
            # Extrair caminho do gpkg e nome da camada
            if "|layername=" in caminho_saida:
                gpkg_path, layer_name = caminho_saida.split("|layername=")
            else:
                gpkg_path = caminho_saida
                layer_name = "elegiveis"
                
            # Salvar
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
            options.layerName = layer_name
            options.driverName = "GPKG"
            
            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                self.output_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )
            
            if error[0] == QgsVectorFileWriter.NoError:
                self.log(f"✓ Resultado salvo em: {gpkg_path}|layername={layer_name}")
                return True
            else:
                self.log(f"❌ Erro ao salvar: {error[1]}")
                return False
                
        except Exception as e:
            self.log(f"❌ Erro ao salvar: {str(e)}")
            return False
