# Plugin Floresta+ Amazônia - Análise de Elegibilidade

## Sobre o Projeto

O **Projeto Floresta+ Amazônia** é uma iniciativa coordenada pelo Ministério do Meio Ambiente e Mudança do Clima (MMA) e executada pelo Programa das Nações Unidas para o Desenvolvimento (PNUD), com recursos provenientes do Fundo Verde para o Clima (GCF).

Este plugin QGIS foi desenvolvido para automatizar a análise técnica de verificação de elegibilidade de imóveis rurais para a **Modalidade Conservação**, conforme estabelecido na **Chamada Pública 02/2024**.

## Funcionalidades

### Aba 1: Preparação da Base de Referência
- Carregamento de camadas de referência (locais ou via WFS)
- Criação e exportação de GeoPackage consolidado
- Ferramenta auxiliar para mapeamento de Vegetação Nativa (RVN)
- Validação da completude da base de dados

### Aba 2: Processamento (Em desenvolvimento)
- Verificação de localização (Amazônia Legal / Municípios Prioritários)
- Checagem de sobreposições (TI, Quilombos, UC, CNFP)
- Análise de embargos (IBAMA/ICMBio)
- Cálculo de módulos fiscais
- Verificação de PRODES
- Avaliação de RVN por fitofisionomia
- Classificação Fase 1 / Fase 2 / Inelegível

### Aba 3: Laudos (Em desenvolvimento)
- Geração de parecer individual por imóvel
- Mapas de localização e situação
- Exportação em PDF
- Planilha consolidada de resultados

## Camadas de Referência Necessárias

| Camada | Fonte | Obrigatória |
|--------|-------|-------------|
| Amazônia Legal | IBGE | Sim |
| Municípios Prioritários | MMA | Sim |
| CAR Amazônia Legal | SICAR/MGI | Sim |
| Floresta Pública Tipo B | SFB | Sim |
| Unidades de Conservação | CNUC/MMA | Sim |
| Terras Indígenas | FUNAI | Sim |
| Territórios Quilombolas | INCRA | Sim |
| Embargos IBAMA | IBAMA | Sim |
| Embargos ICMBio | ICMBio | Sim |
| Fitofisionomia | IBGE | Sim |
| PRODES | INPE | Sim |
| RVN | Mapeamento próprio | Sim |

## Critérios de Elegibilidade

### Fase 1 - Municípios Prioritários
- Localização em municípios prioritários
- CAR com status AT ou PE
- Sem sobreposição com TI (0%) e Quilombos (0%)
- Máximo 5% de sobreposição com UC e Floresta Pública Tipo B
- Máximo 50% de sobreposição com outros CARs
- Desmatamento PRODES ≤ 6,25 ha (após 22/07/2008)
- RVN conforme percentuais por fitofisionomia
- Soma de módulos fiscais ≤ 4

### Fase 2 - Amazônia Legal
- Localização na Amazônia Legal
- CAR analisado e em conformidade
- Mesmas restrições de sobreposição da Fase 1
- Cálculo de pagamento por faixas de RVN

## Instalação

1. Copie a pasta `Plugin_FMais` para o diretório de plugins do QGIS:
   - **Windows**: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux**: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Mac**: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

2. Reinicie o QGIS

3. Ative o plugin em: **Plugins > Gerenciar e Instalar Plugins > Instalados**

4. Clique no ícone do Floresta+ na barra de ferramentas

## Requisitos

- QGIS 3.22 ou superior
- Python 3.x
- Bibliotecas padrão do QGIS (PyQt5, qgis.core, qgis.gui)

## Sistema de Coordenadas

Todas as análises são realizadas utilizando o sistema de coordenadas **South America Albers Equal Area Conic** (EPSG:102033), que:
- Preserva áreas (projeção equivalente)
- Possui cobertura adequada para toda a América do Sul
- É padrão em análises ambientais na região amazônica

## Estrutura do Plugin

```
Plugin_FMais/
├── __init__.py              # Inicialização do módulo
├── plugin_main.py           # Classe principal do plugin
├── metadata.txt             # Metadados obrigatórios
├── icone/
│   └── Logo.png             # Ícone do plugin
├── config/
│   └── config.json          # Configurações
├── core/
│   ├── __init__.py
│   └── funcionalidades.py   # Lógica de processamento
├── ui/
│   ├── __init__.py
│   └── main_window.py       # Interface principal
└── docs/
    └── README.md            # Esta documentação
```

## Contato

- **Projeto**: Floresta+ Amazônia
- **Coordenação**: Ministério do Meio Ambiente e Mudança do Clima (MMA)
- **Execução**: Programa das Nações Unidas para o Desenvolvimento (PNUD)
- **Financiamento**: Fundo Verde para o Clima (GCF)
- **Website**: https://www.florestamaisamazonia.org.br/

## Licença

Este plugin é parte do Projeto Floresta+ Amazônia e está sujeito aos termos de uso estabelecidos pelo MMA/PNUD.

---
© 2024-2026 Projeto Floresta+ Amazônia
