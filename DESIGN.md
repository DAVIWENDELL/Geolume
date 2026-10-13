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

## Composição da operação

- A tela principal segue o fluxo visual `arquivo → validação → pré-visualização → prancha opcional → processamento → resultado → histórico`.
- Em desktop, a coluna da esquerda tem o fluxo (arquivo, prancha, Confira e processe) e, abaixo dele, o histórico resumido; a pré-visualização e o resultado ficam à direita.
- No desktop o histórico usa os mesmos cartões do celular (arquivo e estado; data), sem a coluna Documentos: a coluna do fluxo é estreita demais para a tabela com links. O cartão abre o job e os downloads ficam no Resultado. Entre 640 e 1023 px a tabela com links continua.
- Em desktop (≥ 1024 px), a coluna da direita (pré-visualização + resultado) é `position: sticky`: fica na tela enquanto o fluxo da esquerda rola. Ela nunca passa da altura da viewport — se o conteúdo for maior, rola por dentro — e a altura do mapa sai da viewport para mapa e resultado caberem juntos. Abaixo de 1024 px nada fica fixo.
- O histórico é resumido: mostra os 5 processamentos mais recentes (mais o selecionado, se estiver fora deles) e o botão "Ver mais (N)" / "Ver menos" (`data-testid="jobs-toggle"`, com `aria-expanded`) mostra os 20 que a API já entrega. É só apresentação: a consulta continua `GET /jobs?limit=20`.
- Em telas menores, a ordem passa a ser arquivo, mapa, prancha recolhida, processamento, resultado e histórico.
- A personalização da prancha permanece em `<details>` para não dominar o estado vazio.
- O botão de processamento permanece no fluxo normal da página, sem `position: sticky`, para continuar alcançável quando a prancha estiver aberta e for mais alta que a viewport.
- Identificadores técnicos ficam em detalhes expansíveis; o usuário final vê primeiro estado, documentos e orientações acionáveis.
- Abaixo de 1024 px (tablet e celular), todo controle tocável tem alvo de pelo menos 44 px, inclusive Sair, Atualizar, "Outras camadas", "Detalhes técnicos" e Copiar.
- Abaixo de 640 px, o cartão do histórico mostra arquivo, estado e data e o cartão inteiro abre o job; os downloads ficam no Resultado. Links dentro do cartão ocupavam o centro dele e transformavam o toque para abrir em download.
- A falha de um job mostra o motivo sem o código interno do início (`geometria_invalida: ...`).

## Mapa

- Mapa-base padrão: OpenStreetMap, com atribuição.
- Opção sem mapa-base.
- Satélite permanece desabilitado até existir provedor com licença adequada.
- Catálogo de camadas vem do servidor (`GET /camadas`), em três grupos: disponíveis, dependem de fonte oficial e planejadas; camadas indisponíveis ficam desabilitadas com aviso.
- OSM aparece só na pré-visualização, com o aviso "Somente na pré-visualização — não entra no PDF"; se o catálogo falhar, a tela fica sem mapa-base e o polígono continua visível.
- Polígono pode ser mostrado/ocultado; estilo (Padrão, Técnico, Preto e branco) e opacidade do preenchimento ficam na personalização da prancha, valem igual na tela e no PDF, e o contorno nunca fica transparente.
- A amostra "Polígono do imóvel" da legenda usa as mesmas cores e opacidade aplicadas ao polígono no mapa.
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
