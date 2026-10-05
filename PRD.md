# GeoLume — Documento de produto atual

## Problema

Profissionais precisam transformar arquivos geográficos em mapas de localização e documentos técnicos preliminares sem repetir manualmente o mesmo processamento e layout.

## Usuário e resultado

O usuário envia uma geometria aceita, acompanha o processamento e baixa documentos e dados técnicos. O produto deve esconder a complexidade do QGIS e apresentar uma operação web simples.

## Escopo implementado

- Login e sessão local.
- Upload autenticado de GeoJSON.
- Validação de formato, geometria, CRS, limites e cobertura.
- Processamento síncrono e assíncrono.
- Histórico e acompanhamento de jobs.
- Mapa de pré-visualização.
- Personalização opcional da prancha.
- Download de mapa, memorial preliminar e JSON.

## Fora do escopo confirmado

- Documento legal definitivo.
- KML/Shapefile já suportados.
- Billing, clientes e multi-tenancy de produção.
- Satélite e outros provedores de tiles de produção.
- Escala de concorrência de produção.
- APIs comerciais que gerem custo antes de existir apoio ou financiamento.
- Editor completo de mapas equivalente ao QGIS.

## Estratégia de entrega atual

O primeiro objetivo é finalizar um MVP demonstrável com custo operacional mínimo, usando GeoJSON, dados fictícios, QGIS headless e pré-visualização controlada. O MVP será apresentado a possíveis apoiadores, parceiros e instituições interessadas em financiar ou apoiar o projeto. APIs comerciais de mapas/satélite, novos formatos, cobrança, revisão paga e criação completa de mapas dentro do sistema serão avaliadas somente depois de existir apoio financeiro ou institucional e uma decisão de licença/provedor.

O material de demonstração deve separar explicitamente:

- **Implementado agora:** upload/processamento de GeoJSON, pré-visualização, mapa PDF, memorial preliminar e resultado técnico.
- **Visão futura:** criação de mapas dentro do GeoLume, fontes oficiais integradas, imagens de satélite licenciadas, revisão profissional e modelo comercial.

## Critérios de qualidade

- O mesmo input produz artefatos verificáveis.
- Falhas de entrada são comunicadas sem expor internals.
- Usuários não acessam jobs ou arquivos de terceiros.
- O worker roda no Docker real com QGIS.
- Mudanças no PDF não introduzem clipping ou sobreposição.
- O frontend continua funcional quando tiles estão indisponíveis.

Este documento descreve o estado confirmado no código; metas comerciais e requisitos profissionais precisam ser aprovados antes de ampliar o produto.
