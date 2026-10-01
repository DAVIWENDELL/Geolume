# GeoLume — Sistema visual atual

## Princípios

- A interface deve parecer uma operação técnica clara, não um clone do QGIS.
- O usuário deve entender o estado do job, a entrada e os documentos sem conhecer a implementação.
- Erros devem ser acionáveis e não revelar caminhos internos ou stack traces.
- O mapa ajuda a confirmar a geometria; não substitui a validação server-side.
- O PDF deve ser legível e tecnicamente organizado, sempre com indicação preliminar quando aplicável.

## Componentes existentes

- Centro de operação servido por `worker/web/index.html`.
- CSS central em `worker/web/styles.css`.
- Módulos JS separados para API, modelo, mapa, camadas, polling, renderização e prancha.
- Leaflet 1.9.4 local, com fallback quando os tiles não carregam.
- Estados de autenticação, upload, fila, processamento, conclusão e falha.
- Histórico de jobs e painel de documentos.

## Mapa

- Mapa-base padrão: OpenStreetMap, com atribuição.
- Opção sem mapa-base.
- Satélite permanece desabilitado até existir provedor com licença adequada.
- Polígono pode ser mostrado/ocultado e ter transparência ajustada.
- O botão de enquadrar deve continuar funcionando mesmo quando o polígono estiver oculto.
- Falha de tiles não pode impedir upload, processamento ou downloads.

## Prancha e PDF

- Mapa: A4 paisagem, margem mínima de 5 mm, sem sobreposição.
- Memorial: A4 retrato, páginas adicionais quando necessário, rodapé em todas as páginas.
- Layouts da prancha: padrão, legenda lateral e legenda inferior.
- Logo: PNG/JPEG validado e normalizado pelo servidor.
- Texto fornecido pelo usuário ou GeoJSON deve ser literal; não pode ser avaliado como expressão QGIS.
- A tabela do mapa pode ser truncada com aviso; o memorial mantém todos os vértices.

## Regras para novas telas

- Preserve o fluxo de login → novo processamento → acompanhamento → resultado/histórico.
- Mostre estados vazios e falhas de rede sem quebrar a tela.
- Não dependa de `localStorage` para decisões críticas ou segurança.
- Adicione testes de unidade para lógica pura e E2E para o fluxo visual/real.
- Inspecione visualmente PDFs e telas quando alterar layout.

## Licenciamento e produção

Não ativar novos mapas-base, imagens de satélite, fontes ou bibliotecas sem registrar licença e condições de uso. Desenvolvimento local com OSM não autoriza automaticamente tráfego comercial em escala.
