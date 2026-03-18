"""
-------------------------------------------------------------------------------
Nome:      Classificador Local de Vegetação Planet
Proposito: Classificar imagens Planet em Vegetação/Não Vegetação usando
           amostras do CSV gerado pelo GEE
Autor:     Denilson Passo
Data:      17/01/2026
Uso:       Para integração com plugin QGIS
-------------------------------------------------------------------------------
"""

import os
import math
import sys
import io

# Garantir stderr e stdout válidos (fix para NumPy no QGIS)
if sys.stderr is None:
    sys.stderr = io.StringIO()
if sys.stdout is None:
    sys.stdout = io.StringIO()

import warnings
warnings.filterwarnings('ignore')

# Imports críticos
try:
    import numpy as np
    import pandas as pd
    import rasterio
    from rasterio.features import shapes
    from rasterio.mask import mask
    from sklearn.ensemble import RandomForestClassifier
    from scipy.ndimage import binary_erosion, binary_dilation
    import geopandas as gpd
    from shapely.geometry import shape, Point
except ImportError as e:
    print(f"Erro ao importar bibliotecas do classificador: {e}")
    raise


def wgs84_to_web_mercator(lon, lat):
    """
    Converte coordenadas WGS84 (graus) para Web Mercator (metros).
    EPSG:4326 -> EPSG:3857
    """
    x = lon * 20037508.34 / 180.0
    y = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * 20037508.34 / 180.0
    return x, y


def extrair_quad_id(caminho_imagem):
    """
    Extrai o quad_id do nome do arquivo.
    Exemplo: '682-1053.tif' -> '682-1053'
    """
    nome_arquivo = os.path.basename(caminho_imagem)
    quad_id = os.path.splitext(nome_arquivo)[0]
    return quad_id


def carregar_amostras(caminho_csv, quad_id):
    """
    Carrega as amostras do CSV e filtra pelo quad_id.
    Retorna amostras de vegetação e não vegetação separadas.
    """
    print(f"      Carregando CSV: {caminho_csv}")
    df = pd.read_csv(caminho_csv)
    print(f"      Total de linhas no CSV: {len(df)}")
    
    # Mostrar alguns quad_ids disponíveis para debug
    quads_unicos = df['quad_id'].unique()
    print(f"      Quad IDs únicos no CSV: {len(quads_unicos)}")
    print(f"      Exemplos de quad_ids: {list(quads_unicos[:5])}")
    print(f"      Procurando por: '{quad_id}'")
    
    # Filtrar pelo quad_id
    df_quad = df[df['quad_id'] == quad_id].copy()
    
    if len(df_quad) == 0:
        # Tentar encontrar quad_id similar (contém)
        similar = [q for q in quads_unicos if quad_id in str(q) or str(q) in quad_id]
        if similar:
            print(f"      ⚠ Quad IDs similares encontrados: {similar[:5]}")
            # Usar o primeiro similar se encontrado
            quad_id_alt = similar[0]
            print(f"      → Usando quad_id alternativo: {quad_id_alt}")
            df_quad = df[df['quad_id'] == quad_id_alt].copy()
    
    if len(df_quad) == 0:
        # Se ainda não encontrou, criar amostras sintéticas genéricas
        print(f"      ⚠ Nenhuma amostra encontrada para '{quad_id}'")
        print(f"      → Retornando DataFrames vazios (será classificado por regra padrão)")
        # Retornar DataFrames vazios - o classificador usará regra padrão
        empty_df = pd.DataFrame(columns=['longitude', 'latitude', 'classe', 'quad_id'])
        return empty_df, empty_df
    
    print(f"      Amostras encontradas para {quad_id}: {len(df_quad)}")
    
    # Separar por classe
    amostras_veg = df_quad[df_quad['classe'] == 'veg']
    amostras_nveg = df_quad[df_quad['classe'] == 'nveg']
    
    return amostras_veg, amostras_nveg


def carregar_imagem_planet(caminho_imagem):
    """
    Carrega a imagem Planet e retorna as bandas e metadados.
    Bandas Planet NICFI: B (Blue), G (Green), R (Red), N (NIR)
    """
    with rasterio.open(caminho_imagem) as src:
        # Ler todas as bandas
        bandas = src.read().astype(np.float32)
        perfil = src.profile.copy()
        transform = src.transform
        crs = src.crs
        
    # Assumindo ordem: B, G, R, N (índices 0, 1, 2, 3)
    B = bandas[0]
    G = bandas[1]
    R = bandas[2]
    N = bandas[3]
    
    return B, G, R, N, perfil, transform, crs


def calcular_indices_espectrais(B, G, R, N):
    """
    Calcula os índices espectrais usados na classificação.
    """
    # Evitar divisão por zero
    epsilon = 1e-10
    
    # NDVI = (NIR - Red) / (NIR + Red)
    ndvi = (N - R) / (N + R + epsilon)
    
    # NDWI = (Green - NIR) / (Green + NIR)
    ndwi = (G - N) / (G + N + epsilon)
    
    # Ratio = Red / NIR
    ratio = R / (N + epsilon)
    
    # Brilho = (B + G + R + N) / 4
    brilho = (B + G + R + N) / 4
    
    return ndvi, ndwi, ratio, brilho


def extrair_valores_amostras(B, G, R, N, ndvi, ndwi, ratio, brilho, 
                              amostras_df, transform, crs_imagem):
    """
    Extrai os valores das bandas/índices nas posições das amostras.
    Converte coordenadas de WGS84 (EPSG:4326) para o CRS da imagem se necessário.
    """
    valores = []
    
    # Verificar se precisa converter coordenadas (imagem em Web Mercator)
    converter_mercator = False
    if crs_imagem:
        crs_str = str(crs_imagem).upper()
        if 'MERCATOR' in crs_str or '3857' in crs_str:
            converter_mercator = True
    
    for _, row in amostras_df.iterrows():
        # Coordenadas originais (WGS84)
        lon, lat = row['longitude'], row['latitude']
        
        # Converter para Web Mercator se necessário
        if converter_mercator:
            lon, lat = wgs84_to_web_mercator(lon, lat)
        
        # Transformação inversa: coordenadas -> pixel
        col, lin = ~transform * (lon, lat)
        col, lin = int(col), int(lin)
        
        # Verificar se está dentro dos limites da imagem
        if 0 <= lin < B.shape[0] and 0 <= col < B.shape[1]:
            valores.append({
                'B': B[lin, col],
                'G': G[lin, col],
                'R': R[lin, col],
                'N': N[lin, col],
                'NDVI': ndvi[lin, col],
                'NDWI': ndwi[lin, col],
                'Ratio': ratio[lin, col],
                'Brilho': brilho[lin, col],
                'classe': 1 if row['classe'] == 'veg' else 2
            })
    
    return pd.DataFrame(valores)


def filtrar_outliers_ndvi(df_amostras, fator_desvio=1.0):
    """
    Remove amostras outliers baseado no NDVI (mesma lógica do GEE).
    """
    # Separar por classe
    veg = df_amostras[df_amostras['classe'] == 1].copy()
    nveg = df_amostras[df_amostras['classe'] == 2].copy()
    
    # Filtrar vegetação
    if len(veg) > 0:
        mean_veg = veg['NDVI'].mean()
        std_veg = veg['NDVI'].std()
        limite_inf_veg = mean_veg - std_veg * fator_desvio
        limite_sup_veg = mean_veg + std_veg * fator_desvio
        veg = veg[(veg['NDVI'] >= limite_inf_veg) & (veg['NDVI'] <= limite_sup_veg)]
    
    # Filtrar não-vegetação
    if len(nveg) > 0:
        mean_nveg = nveg['NDVI'].mean()
        std_nveg = nveg['NDVI'].std()
        limite_inf_nveg = mean_nveg - std_nveg * fator_desvio
        limite_sup_nveg = mean_nveg + std_nveg * fator_desvio
        nveg = nveg[(nveg['NDVI'] >= limite_inf_nveg) & (nveg['NDVI'] <= limite_sup_nveg)]
    
    return pd.concat([veg, nveg], ignore_index=True)


def aplicar_filtro_maioria(classificacao, tamanho_kernel=3):
    """
    Aplica filtro de maioria para suavizar ruídos.
    Versão OTIMIZADA usando operações morfológicas (abertura).
    
    MUITO mais rápido que generic_filter:
    - Antes: ~210 segundos
    - Agora: ~0.5 segundos
    
    IMPORTANTE: border_value=True preserva as bordas da imagem,
    evitando gaps de pixels não classificados entre quads adjacentes.
    """
    try:
        from scipy.ndimage import binary_erosion, binary_dilation
        
        print(f"      ⚡ Usando filtro morfológico otimizado...")
        
        # Para classificação binária (1=veg, 2=não veg), usar morfologia
        # Criar máscara binária (True = vegetação)
        mascara_veg = (classificacao == 1)
        
        # Número de iterações baseado no tamanho do kernel
        iteracoes = max(1, tamanho_kernel // 2)
        
        # Aplicar ABERTURA morfológica (erosão + dilatação)
        # Remove pequenos "ruídos" de vegetação isolada
        # border_value=True: trata bordas da imagem como vegetação (preserva bordas!)
        mascara_limpa = binary_erosion(mascara_veg, iterations=iteracoes, border_value=True)
        mascara_limpa = binary_dilation(mascara_limpa, iterations=iteracoes)
        
        # Aplicar FECHAMENTO morfológico (dilatação + erosão)  
        # Preenche pequenos "buracos" dentro de áreas de vegetação
        mascara_limpa = binary_dilation(mascara_limpa, iterations=iteracoes)
        mascara_limpa = binary_erosion(mascara_limpa, iterations=iteracoes, border_value=True)
        
        # Reconstruir classificação: 1=vegetação, 2=não vegetação
        resultado = np.where(mascara_limpa, 1, 2).astype(np.uint8)
        
        return resultado
        
    except Exception as e:
        print(f"      ⚠ Erro no filtro morfológico, usando original: {e}")
        return classificacao


def raster_para_vetor(classificacao, transform, crs, crs_saida='EPSG:4674'):
    """
    Converte o raster classificado para vetor (polígonos).
    Reprojeta para EPSG:4674 (SIRGAS 2000).
    
    Parâmetros:
    -----------
    classificacao : numpy array
        Raster classificado
    transform : Affine
        Transformação geográfica do raster
    crs : CRS
        Sistema de coordenadas do raster original
    crs_saida : str
        CRS de saída (padrão: EPSG:4674 - SIRGAS 2000)
    """
    print(f"      Vetorizando classificação...")
    
    # Criar máscara para pixels válidos
    mask_valido = classificacao > 0
    
    # Extrair shapes
    resultados = []
    for geom, valor in shapes(classificacao, mask=mask_valido, transform=transform):
        resultados.append({
            'geometry': shape(geom),
            'classe': 'veg' if int(valor) == 1 else 'nveg',
            'valor': int(valor)
        })
    
    print(f"      - Polígonos extraídos: {len(resultados)}")
    
    if len(resultados) == 0:
        print("      ⚠ Nenhum polígono extraído!")
        # Criar GeoDataFrame vazio com CRS de saída
        gdf = gpd.GeoDataFrame(columns=['geometry', 'classe', 'valor'], crs=crs_saida)
        return gdf
    
    # Verificar e normalizar o CRS original
    # Imagens Planet vêm como LOCAL_CS["WGS 84 / Pseudo-Mercator"...] que não é reconhecido
    crs_original = crs
    crs_str = str(crs).upper() if crs else ""
    
    # Se for LOCAL_CS com Pseudo-Mercator ou mencionar 3857, forçar EPSG:3857
    if 'LOCAL_CS' in crs_str or 'PSEUDO' in crs_str or 'MERCATOR' in crs_str:
        print(f"      - CRS detectado como Pseudo-Mercator (LOCAL_CS)")
        print(f"      - Forçando CRS para EPSG:3857...")
        crs_original = 'EPSG:3857'
    
    # Criar GeoDataFrame com CRS normalizado
    gdf = gpd.GeoDataFrame(resultados, crs=crs_original)
    
    print(f"      - CRS normalizado: {gdf.crs}")
    
    # REPROJETAR para EPSG:4674 (SIRGAS 2000)
    try:
        if gdf.crs is not None and len(gdf) > 0:
            crs_atual = str(gdf.crs).upper()
            if crs_saida.upper() not in crs_atual:
                print(f"      - Reprojetando para {crs_saida}...")
                gdf = gdf.to_crs(crs_saida)
                print(f"      - CRS final: {gdf.crs}")
            else:
                print(f"      - CRS já é {crs_saida}")
    except Exception as e:
        print(f"      ⚠ Erro na reprojeção: {e}")
        # Tentar uma última vez com CRS explícito
        try:
            print(f"      - Tentando reprojeção forçada...")
            gdf = gdf.set_crs('EPSG:3857', allow_override=True)
            gdf = gdf.to_crs(crs_saida)
            print(f"      - CRS final (forçado): {gdf.crs}")
        except Exception as e2:
            print(f"      ⚠ Reprojeção forçada também falhou: {e2}")
    
    # Dissolver por classe para simplificar
    try:
        gdf_dissolvido = gdf.dissolve(by='classe', as_index=False)
        print(f"      - Polígonos após dissolve: {len(gdf_dissolvido)}")
        return gdf_dissolvido
    except Exception as e:
        print(f"      ⚠ Erro no dissolve, retornando sem dissolve: {e}")
        return gdf


def classificar_imagem_planet(caminho_imagem, caminho_csv, caminho_saida=None, 
                               n_arvores=10, fator_desvio=1.0):
    """
    Função principal que executa todo o processo de classificação.
    
    Parâmetros:
    -----------
    caminho_imagem : str
        Caminho para a imagem Planet (.tif)
    caminho_csv : str
        Caminho para o CSV com as amostras
    caminho_saida : str, opcional
        Caminho para salvar o vetor de saída. Se None, usa o mesmo diretório da imagem
    n_arvores : int
        Número de árvores do Random Forest (padrão: 10)
    fator_desvio : float
        Fator de desvio padrão para filtro de outliers (padrão: 1.0)
    
    Retorna:
    --------
    gdf : GeoDataFrame
        GeoDataFrame com a classificação vetorizada
    """
    import time
    
    try:
        print("=" * 60)
        print("CLASSIFICADOR LOCAL DE VEGETAÇÃO PLANET")
        print("=" * 60)
        
        tempo_inicio_total = time.time()
        tempos = {}  # Dicionário para guardar tempos de cada etapa
        
        # Verificar arquivos
        if not os.path.exists(caminho_imagem):
            raise FileNotFoundError(f"Imagem não encontrada: {caminho_imagem}")
        if not os.path.exists(caminho_csv):
            raise FileNotFoundError(f"CSV de amostras não encontrado: {caminho_csv}")
        
        print(f"\nArquivos verificados:")
        print(f"  - Imagem: {caminho_imagem}")
        print(f"  - CSV: {caminho_csv}")
        
        # 1. Extrair quad_id do nome da imagem
        t1 = time.time()
        try:
            quad_id = extrair_quad_id(caminho_imagem)
            print(f"\n[1/8] Quad ID identificado: {quad_id}")
        except Exception as e:
            print(f"❌ Erro ao extrair quad_id: {e}")
            raise
        tempos['1_quad_id'] = time.time() - t1
    
        # 2. Carregar amostras
        t2 = time.time()
        print(f"[2/8] Carregando amostras do CSV...")
        try:
            amostras_veg, amostras_nveg = carregar_amostras(caminho_csv, quad_id)
            print(f"      - Amostras Vegetação: {len(amostras_veg)}")
            print(f"      - Amostras Não Vegetação: {len(amostras_nveg)}")
        except Exception as e:
            print(f"❌ Erro ao carregar amostras: {e}")
            raise
        tempos['2_carregar_amostras'] = time.time() - t2
        print(f"      ⏱️ Tempo: {tempos['2_carregar_amostras']:.2f}s")
        
        # 3. Carregar imagem Planet
        t3 = time.time()
        print(f"[3/8] Carregando imagem Planet...")
        try:
            B, G, R, N, perfil, transform, crs = carregar_imagem_planet(caminho_imagem)
            print(f"      - Dimensões: {B.shape[1]} x {B.shape[0]} pixels")
            print(f"      - CRS da imagem: {crs}")
        except Exception as e:
            print(f"❌ Erro ao carregar imagem: {e}")
            raise
        tempos['3_carregar_imagem'] = time.time() - t3
        print(f"      ⏱️ Tempo: {tempos['3_carregar_imagem']:.2f}s")
        
        # 4. Calcular índices espectrais
        t4 = time.time()
        print(f"[4/8] Calculando índices espectrais (NDVI, NDWI, Ratio, Brilho)...")
        try:
            ndvi, ndwi, ratio, brilho = calcular_indices_espectrais(B, G, R, N)
        except Exception as e:
            print(f"❌ Erro ao calcular índices: {e}")
            raise
        tempos['4_indices'] = time.time() - t4
        print(f"      ⏱️ Tempo: {tempos['4_indices']:.2f}s")
        
        # 5. Verificar se há amostras de ambas as classes
        t5 = time.time()
        num_veg = len(amostras_veg)
        num_nveg = len(amostras_nveg)
        
        print(f"[5/8] Verificando amostras...")
        print(f"      - Vegetação: {num_veg}")
        print(f"      - Não Vegetação: {num_nveg}")
        
        if num_veg == 0 and num_nveg == 0:
            # Sem NENHUMA amostra → usar classificação por NDVI
            print(f"      ⚠ ATENÇÃO: Sem amostras de treinamento!")
            print(f"      → Usando classificação por NDVI (threshold 0.3)")
            # NDVI > 0.3 = vegetação, caso contrário = não vegetação
            classificacao = np.where(ndvi > 0.3, 1, 2).astype(np.uint8)
            tempos['5_verificar_amostras'] = time.time() - t5
            
        elif num_nveg == 0:
            # Sem amostras de Não Vegetação → toda a carta é Vegetação
            print(f"      ⚠ ATENÇÃO: Sem amostras de Não Vegetação")
            print(f"      → Classificando toda a carta como VEGETAÇÃO")
            classificacao = np.ones(B.shape, dtype=np.uint8)
            tempos['5_verificar_amostras'] = time.time() - t5
            
        elif num_veg == 0:
            # Sem amostras de Vegetação → toda a carta é Não Vegetação
            print(f"      ⚠ ATENÇÃO: Sem amostras de Vegetação")
            print(f"      → Classificando toda a carta como NÃO VEGETAÇÃO")
            classificacao = np.full(B.shape, 2, dtype=np.uint8)
            tempos['5_verificar_amostras'] = time.time() - t5
            
        else:
            # Tem ambas as classes → processamento normal
            print(f"[5/8] Extraindo valores das amostras na imagem...")
            
            # Combinar amostras
            todas_amostras = pd.concat([amostras_veg, amostras_nveg], ignore_index=True)
            
            # Extrair valores
            t5a = time.time()
            df_treino = extrair_valores_amostras(
                B, G, R, N, ndvi, ndwi, ratio, brilho, 
                todas_amostras, transform, crs
            )
            tempos['5a_extrair_valores'] = time.time() - t5a
            print(f"      ⏱️ Extração de valores: {tempos['5a_extrair_valores']:.2f}s")
            
            if len(df_treino) == 0:
                raise ValueError("Nenhuma amostra válida encontrada dentro da imagem!")
            
            print(f"      - Amostras válidas extraídas: {len(df_treino)}")
            
            # 6. Filtrar outliers
            t6 = time.time()
            print(f"[6/8] Filtrando outliers por NDVI (fator: {fator_desvio})...")
            df_treino_filtrado = filtrar_outliers_ndvi(df_treino, fator_desvio)
            tempos['6_filtrar_outliers'] = time.time() - t6
            print(f"      - Amostras após filtro: {len(df_treino_filtrado)}")
            print(f"      ⏱️ Tempo: {tempos['6_filtrar_outliers']:.2f}s")
            
            # Verificar novamente se há amostras de ambas as classes após filtro
            veg_filtrado = len(df_treino_filtrado[df_treino_filtrado['classe'] == 1])
            nveg_filtrado = len(df_treino_filtrado[df_treino_filtrado['classe'] == 2])
            
            if nveg_filtrado == 0:
                print(f"      → Após filtro, sem Não Vegetação. Classificando tudo como VEGETAÇÃO")
                classificacao = np.ones(B.shape, dtype=np.uint8)
            elif veg_filtrado == 0:
                print(f"      → Após filtro, sem Vegetação. Classificando tudo como NÃO VEGETAÇÃO")
                classificacao = np.full(B.shape, 2, dtype=np.uint8)
            else:
                # 7. Treinar classificador Random Forest
                t7 = time.time()
                print(f"[7/8] Treinando Random Forest ({n_arvores} árvores)...")
                
                bandas_classificacao = ['B', 'G', 'R', 'N', 'NDVI', 'NDWI', 'Ratio', 'Brilho']
                X_treino = df_treino_filtrado[bandas_classificacao].values
                y_treino = df_treino_filtrado['classe'].values
                
                clf = RandomForestClassifier(n_estimators=n_arvores, random_state=42, n_jobs=-1)
                clf.fit(X_treino, y_treino)
                tempos['7_treinar_rf'] = time.time() - t7
                print(f"      ⏱️ Tempo treino: {tempos['7_treinar_rf']:.2f}s")
                
                # Preparar dados para classificação
                t8_prep = time.time()
                altura, largura = B.shape
                
                # Stack de todas as bandas/índices
                stack = np.stack([B, G, R, N, ndvi, ndwi, ratio, brilho], axis=0)
                
                # Reshape para (n_pixels, n_bandas)
                X_imagem = stack.reshape(8, -1).T
                tempos['8a_prep_dados'] = time.time() - t8_prep
                print(f"      ⏱️ Prep dados: {tempos['8a_prep_dados']:.2f}s")
                
                # Classificar
                t8_class = time.time()
                print(f"[8/8] Classificando imagem ({altura}x{largura} = {altura*largura:,} pixels)...")
                y_pred = clf.predict(X_imagem)
                tempos['8b_predict'] = time.time() - t8_class
                print(f"      ⏱️ Tempo predict: {tempos['8b_predict']:.2f}s")
                
                # Reshape para dimensões originais
                classificacao = y_pred.reshape(altura, largura).astype(np.uint8)
        
        # Aplicar filtro de maioria
        t_filtro = time.time()
        print(f"      Aplicando filtro de maioria...")
        try:
            classificacao_filtrada = aplicar_filtro_maioria(classificacao, tamanho_kernel=3)
        except Exception as e:
            print(f"⚠ Erro no filtro de maioria, usando original: {e}")
            classificacao_filtrada = classificacao
        tempos['9_filtro_maioria'] = time.time() - t_filtro
        print(f"      ⏱️ Tempo filtro maioria: {tempos['9_filtro_maioria']:.2f}s")
        
        # Converter para vetor e REPROJETAR para EPSG:4674
        t_vetor = time.time()
        print(f"      Convertendo para vetor e reprojetando para EPSG:4674...")
        try:
            gdf = raster_para_vetor(classificacao_filtrada, transform, crs, crs_saida='EPSG:4674')
        except Exception as e:
            print(f"❌ Erro ao vetorizar/reprojetar: {e}")
            raise
        tempos['10_vetorizar'] = time.time() - t_vetor
        print(f"      ⏱️ Tempo vetorização: {tempos['10_vetorizar']:.2f}s")
        
        if gdf is None or len(gdf) == 0:
            print("⚠ GeoDataFrame vazio após vetorização!")
        
        # Salvar resultado
        t_salvar = time.time()
        if caminho_saida is None:
            diretorio = os.path.dirname(caminho_imagem)
            nome_base = os.path.splitext(os.path.basename(caminho_imagem))[0]
            caminho_saida = os.path.join(diretorio, f"{nome_base}_classificacao.gpkg")
        
        try:
            gdf.to_file(caminho_saida, driver='GPKG')
            tempos['11_salvar'] = time.time() - t_salvar
            print(f"      ⏱️ Tempo salvar: {tempos['11_salvar']:.2f}s")
        except Exception as e:
            print(f"❌ Erro ao salvar arquivo: {e}")
            raise
        
        # RESUMO DE TEMPOS DO CLASSIFICADOR
        tempo_total_class = time.time() - tempo_inicio_total
        print(f"\n{'─'*60}")
        print(f"⏱️  RESUMO TIMING CLASSIFICADOR: {tempo_total_class:.2f}s")
        print(f"{'─'*60}")
        for etapa, tempo in sorted(tempos.items()):
            pct = (tempo / tempo_total_class * 100) if tempo_total_class > 0 else 0
            print(f"   {etapa}: {tempo:6.2f}s ({pct:5.1f}%)")
        print(f"{'─'*60}")
        
        print(f"\n{'=' * 60}")
        print(f"CLASSIFICAÇÃO CONCLUÍDA!")
        print(f"Arquivo salvo em: {caminho_saida}")
        print(f"CRS de saída: {gdf.crs}")
        print(f"{'=' * 60}")
        
        return gdf
        
    except FileNotFoundError as e:
        print(f"\n❌ ERRO: Arquivo não encontrado!")
        print(f"   {e}")
        raise
    except ValueError as e:
        print(f"\n❌ ERRO: Valor inválido!")
        print(f"   {e}")
        raise
    except Exception as e:
        print(f"\n❌ ERRO INESPERADO na classificação!")
        print(f"   Tipo: {type(e).__name__}")
        print(f"   Mensagem: {e}")
        import traceback
        traceback.print_exc()
        raise


# =============================================================================
# EXEMPLO DE USO
# =============================================================================
if __name__ == "__main__":
    # Caminhos de exemplo (ajuste conforme necessário)
    CAMINHO_IMAGEM = r"D:\dados\planet\682-1053.tif"
    CAMINHO_CSV = r"D:\dados\amostras_todas_quads_AMZL.csv"
    CAMINHO_SAIDA = r"D:\dados\resultados\682-1053_classificacao.gpkg"
    
    # Executar classificação
    resultado = classificar_imagem_planet(
        caminho_imagem=CAMINHO_IMAGEM,
        caminho_csv=CAMINHO_CSV,
        caminho_saida=CAMINHO_SAIDA,
        n_arvores=10,
        fator_desvio=1.0
    )
    
    print("\nResumo do resultado:")
    print(resultado)
