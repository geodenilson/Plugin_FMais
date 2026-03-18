# -*- coding: utf-8 -*-
"""
Cliente para API Planet Basemaps
Baseado no SDK oficial planet-client-python usado pelo Planet Explorer
"""

import os
import json
import base64
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class PlanetClient:
    """Cliente para acesso a API Planet Basemaps - replica o comportamento do SDK oficial."""
    
    BASE_URL = "https://api.planet.com/"
    BASEMAPS_URL = "https://api.planet.com/basemaps/v1"
    TILES_URL = "https://tiles.planet.com/basemaps/v1"
    
    # Serie de interesse
    TARGET_SERIES = "PS Tropical Normalized Analytic Monthly Monitoring"
    
    def __init__(self):
        self.api_key = None
        self.user_email = None
        self.session = None
        self._logged_in = False
        self._user_data = None
    
    @property
    def is_logged_in(self):
        return self._logged_in and self.api_key is not None
    
    def _create_session(self):
        """Cria sessao igual ao SDK oficial do Planet."""
        session = requests.Session()
        
        # Headers EXATAMENTE como o SDK oficial planet-client-python
        session.headers.update({
            'User-Agent': 'planet-client-python/1.5.2',
            'X-Planet-App': 'python-client'
        })
        
        # Retry para rate limiting
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503])
        session.mount('https://', HTTPAdapter(max_retries=retries))
        
        return session
    
    def login(self, email, password):
        """
        Faz login usando o mesmo metodo do SDK oficial Planet.
        
        Endpoint: POST https://api.planet.com/v0/auth/login
        
        Args:
            email: Email da conta Planet
            password: Senha da conta Planet
            
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            # Criar sessao como o SDK oficial
            session = self._create_session()
            
            # Fazer login - EXATAMENTE como o SDK oficial (client.py linha 78)
            url = f"{self.BASE_URL}v0/auth/login"
            result = session.post(url, json={
                'email': email,
                'password': password
            })
            
            status = result.status_code
            
            if status == 200:
                # Sucesso - decodificar JWT como o SDK oficial
                jwt = result.text
                payload = jwt.split('.')[1]
                rem = len(payload) % 4
                if rem > 0:
                    payload += '=' * (4 - rem)
                payload = base64.urlsafe_b64decode(payload.encode('utf-8'))
                user_data = json.loads(payload.decode('utf-8'))
                
                # Armazenar dados
                self.api_key = user_data.get('api_key')
                self.user_email = email
                self._user_data = user_data
                self.session = session
                self._logged_in = True
                
                return True, "Login realizado com sucesso!"
                
            elif status == 400:
                return False, "Parametros invalidos - processo de login pode ter mudado"
            elif status == 401:
                # Tentar obter mensagem detalhada
                try:
                    msg = json.loads(result.text).get('message', 'Credenciais invalidas')
                except:
                    msg = result.text or 'Credenciais invalidas'
                return False, f"Credenciais invalidas: {msg}"
            elif status == 403:
                # ERRO 403 - tentar metodo alternativo com Basic Auth
                return self._try_basic_auth_login(email, password)
            else:
                return False, f"Erro {status}: {result.text}"
                
        except requests.exceptions.Timeout:
            return False, "Timeout - servidor nao respondeu"
        except requests.exceptions.ConnectionError:
            return False, "Erro de conexao - verifique sua internet"
        except Exception as e:
            return False, f"Erro: {str(e)}"
    
    def _try_basic_auth_login(self, email, password):
        """
        Metodo alternativo usando Basic Auth diretamente.
        Usado se o endpoint v0/auth/login retornar 403.
        """
        try:
            session = self._create_session()
            
            # Usar email:password como Basic Auth
            session.auth = (email, password)
            
            # Testar se funciona acessando mosaicos
            url = f"{self.BASEMAPS_URL}/mosaics"
            response = session.get(url, params={"_page_size": 1}, timeout=20)
            
            if response.status_code == 200:
                self.api_key = email  # Usar email como identificador
                self.user_email = email
                self.session = session
                self._logged_in = True
                return True, "Login realizado com sucesso (Basic Auth)!"
            elif response.status_code == 401:
                return False, "Credenciais invalidas"
            else:
                return False, f"Erro {response.status_code}: Acesso negado"
                
        except Exception as e:
            return False, f"Erro no login alternativo: {str(e)}"
    
    def login_with_api_key(self, api_key):
        """
        Login direto com API key.
        
        Args:
            api_key: Planet API key
            
        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            session = self._create_session()
            
            # API key como Basic Auth (api_key:'' senha vazia)
            session.auth = (api_key.strip(), '')
            
            # Testar se a API key e valida
            url = f"{self.BASEMAPS_URL}/mosaics"
            response = session.get(url, params={"_page_size": 1}, timeout=15)
            
            if response.status_code == 200:
                self.api_key = api_key.strip()
                self.session = session
                self._logged_in = True
                return True, "API Key valida!"
            elif response.status_code == 401:
                return False, "API Key invalida"
            else:
                return False, f"Erro: {response.status_code}"
                
        except Exception as e:
            return False, f"Erro: {str(e)}"
    
    def logout(self):
        """Encerra a sessao."""
        self.api_key = None
        self.user_email = None
        self._logged_in = False
        self._user_data = None
        if self.session:
            self.session.close()
            self.session = None
    
    def _get_auth(self):
        """Retorna tupla de autenticacao para requisicoes."""
        if self.api_key:
            return (self.api_key, '')
        return None
    
    def _request(self, url, params=None):
        """Faz requisicao autenticada."""
        if not self.is_logged_in or not self.session:
            return None
        
        try:
            # Se session nao tem auth configurado, usar api_key
            if not self.session.auth:
                self.session.auth = self._get_auth()
            
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except:
            return None
    
    def list_tropical_series(self):
        """
        Lista APENAS a serie 'PS Tropical Normalized Analytic Monthly Monitoring'.
        
        Returns:
            list: Lista de series encontradas (filtrada)
        """
        if not self.is_logged_in:
            return []
        
        try:
            # Buscar serie especifica
            url = f"{self.BASEMAPS_URL}/series/"
            params = {"name__contains": "PS Tropical Normalized Analytic Monthly"}
            data = self._request(url, params)
            
            if data:
                series_list = data.get("series", [])
                # Filtrar EXATAMENTE a serie desejada
                target_name = "PS Tropical Normalized Analytic Monthly Monitoring"
                filtered = [s for s in series_list if target_name in s.get("name", "")]
                
                if filtered:
                    return filtered
                
                # Se nao encontrou, retornar lista completa
                return series_list
            return []
        except Exception as e:
            print(f"Erro ao listar series: {e}")
            return []
    
    def get_mosaics_for_series(self, series_id):
        """
        Obtem mosaicos de uma serie.
        
        Args:
            series_id: ID da serie
            
        Returns:
            list: Lista de mosaicos
        """
        if not self.is_logged_in:
            return []
        
        try:
            url = f"{self.BASEMAPS_URL}/series/{series_id}/mosaics"
            params = {"v": "1.5"}
            all_mosaics = []
            
            data = self._request(url, params)
            if not data:
                return []
            
            all_mosaics.extend(data.get("mosaics", []))
            
            # Paginacao
            while "_next" in data.get("_links", {}):
                next_url = data["_links"]["_next"]
                response = self.session.get(next_url, timeout=30)
                if response.status_code != 200:
                    break
                data = response.json()
                all_mosaics.extend(data.get("mosaics", []))
            
            # Ordenar por data (mais recente primeiro)
            all_mosaics.sort(key=lambda x: x.get("last_acquired", ""), reverse=True)
            return all_mosaics
            
        except Exception as e:
            print(f"Erro ao obter mosaicos: {e}")
            return []
    
    def list_all_mosaics(self, name_contains=None):
        """
        Lista todos os mosaicos disponiveis.
        
        Args:
            name_contains: Filtro por nome
            
        Returns:
            list: Lista de mosaicos
        """
        if not self.is_logged_in:
            return []
        
        try:
            url = f"{self.BASEMAPS_URL}/mosaics"
            params = {"v": "1.5", "_page_size": 100}
            if name_contains:
                params["name__contains"] = name_contains
            
            data = self._request(url, params)
            if not data:
                return []
            
            all_mosaics = data.get("mosaics", [])
            
            # Paginacao (limitar a 500)
            while "_next" in data.get("_links", {}) and len(all_mosaics) < 500:
                next_url = data["_links"]["_next"]
                response = self.session.get(next_url, timeout=30)
                if response.status_code != 200:
                    break
                data = response.json()
                all_mosaics.extend(data.get("mosaics", []))
            
            return all_mosaics
            
        except Exception as e:
            print(f"Erro ao listar mosaicos: {e}")
            return []
    
    def get_quads(self, mosaic_id, bbox):
        """
        Busca quads que intersectam o bbox.
        
        Args:
            mosaic_id: ID do mosaico
            bbox: tuple (lon_min, lat_min, lon_max, lat_max)
            
        Returns:
            list: Lista de quads
        """
        if not self.is_logged_in:
            return []
        
        try:
            # Limitar coordenadas aos limites validos
            bbox = (
                max(-180, min(180, bbox[0])),
                max(-84.99, min(84.99, bbox[1])),
                max(-180, min(180, bbox[2])),
                max(-84.99, min(84.99, bbox[3]))
            )
            
            url = f"{self.BASEMAPS_URL}/mosaics/{mosaic_id}/quads"
            params = {"bbox": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"}
            
            data = self._request(url, params)
            if not data:
                return []
            
            all_quads = data.get("items", [])
            
            # Paginação - buscar até 50000 quads (AMZL toda pode ter milhares)
            max_quads = 50000
            page_count = 1
            while "_next" in data.get("_links", {}) and len(all_quads) < max_quads:
                page_count += 1
                if page_count % 50 == 0:
                    print(f"  Buscando quads... página {page_count}, total: {len(all_quads)}")
                next_url = data["_links"]["_next"]
                response = self.session.get(next_url, timeout=30)
                if response.status_code != 200:
                    break
                data = response.json()
                all_quads.extend(data.get("items", []))
            
            print(f"Total de quads obtidos da API: {len(all_quads)}")
            return all_quads
            
        except Exception as e:
            print(f"Erro ao obter quads: {e}")
            return []
    
    def get_tile_url(self, mosaic_name):
        """
        Retorna URL do tile XYZ para usar como basemap.
        
        Args:
            mosaic_name: Nome do mosaico
            
        Returns:
            str: URL do tile
        """
        if not self.is_logged_in or not self.api_key:
            return None
        
        # URL padrao para tiles Planet
        return f"{self.TILES_URL}/planet-tiles/{mosaic_name}/gmap/{{z}}/{{x}}/{{y}}.png?api_key={self.api_key}"
    
    def get_quad_by_id(self, mosaic_id, quad_id):
        """
        Busca um quad específico pelo ID.
        
        Args:
            mosaic_id: ID/nome do mosaico
            quad_id: ID do quad (ex: '766-984')
            
        Returns:
            dict: Dados do quad com _links para download, ou None se não encontrado
        """
        if not self.is_logged_in:
            return None
        
        try:
            url = f"{self.BASEMAPS_URL}/mosaics/{mosaic_id}/quads/{quad_id}"
            
            response = self.session.get(url, timeout=30)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Quad {quad_id} não encontrado no mosaico {mosaic_id}: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Erro ao buscar quad {quad_id}: {e}")
            return None
    
    def download_quad(self, quad, output_path, callback=None):
        """
        Baixa um quad para o caminho especificado.
        
        Args:
            quad: dict do quad com _links
            output_path: Caminho completo do arquivo de saida
            callback: Funcao callback(bytes_downloaded, total_bytes)
            
        Returns:
            tuple: (success: bool, message: str)
        """
        if not self.is_logged_in:
            return False, "Nao autenticado"
        
        try:
            download_url = quad.get("_links", {}).get("download")
            if not download_url:
                return False, "URL de download nao disponivel"
            
            response = self.session.get(download_url, stream=True, timeout=300)
            
            if response.status_code != 200:
                return False, f"Erro no download: {response.status_code}"
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            # Criar pasta se nao existir
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if callback:
                            callback(downloaded, total_size)
            
            return True, output_path
            
        except Exception as e:
            return False, f"Erro: {str(e)}"
    
    def get_mosaic_display_name(self, mosaic):
        """
        Retorna nome amigavel do mosaico para exibicao.
        
        Args:
            mosaic: dict do mosaico
            
        Returns:
            str: Nome formatado (ex: "Janeiro 2024")
        """
        try:
            # Extrair data do nome ou first_acquired
            first_acquired = mosaic.get("first_acquired", "")
            if first_acquired:
                from datetime import datetime
                dt = datetime.fromisoformat(first_acquired.replace("Z", "+00:00"))
                meses = ["Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
                        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
                return f"{meses[dt.month - 1]} {dt.year}"
        except:
            pass
        
        return mosaic.get("name", "Mosaico")


# Instancia global (singleton)
planet_client = PlanetClient()
