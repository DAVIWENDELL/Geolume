# GeoLume — Tarefas e próximos passos

Legenda: `[ ]` pendente · `[x]` verificado no repositório · `[?]` precisa de decisão ou validação externa.

## Estado confirmado

- [x] Worker PyQGIS headless com validação de um polígono GeoJSON.
- [x] Reprojeção para SIRGAS 2000 / UTM dentro da cobertura suportada.
- [x] Geração de `mapa.pdf`, `memorial.pdf` preliminar e `resultado.json`.
- [x] API FastAPI síncrona e assíncrona.
- [x] Redis + Celery com uma sessão QGIS por processo.
- [x] PostgreSQL para usuários, sessões e jobs.
- [x] Upload, histórico, polling, downloads e mapa Leaflet local.
- [x] Personalização opcional da prancha com cores, logo e três layouts.
- [x] Catálogo de camadas servido por `GET /camadas`, estilos do polígono (Padrão, Técnico, Preto e branco) e opacidade do preenchimento iguais na pré-visualização e no `mapa.pdf` (verificação final em 2026-10-01: suítes completas verdes, PDFs reais inspecionados, geração em container sem rede).
- [x] Tela principal reorganizada para o fluxo de cliente: arquivo e configuração à esquerda, mapa e resultado à direita, histórico compacto abaixo; desktop e mobile verificados pelo Playwright.
- [x] Botão Processar mantido alcançável com a prancha aberta, removendo o sticky da coluna de fluxo e cobrindo o comportamento com E2E.
- [x] Varredura final de UX da tela principal (2026-10-01): amostra da legenda segue o estilo do polígono, alvos de 44 px em tablet e celular, histórico mobile sem links no cartão e falha sem código interno; cobertos por unitário e E2E.
- [x] Quadro de coordenadas e fontes no `mapa.pdf` (2026-10-05, 1ª fatia do layout profissional): SRC completo com fuso/hemisfério e EPSG UTM, EPSG:4674 para latitude/longitude, centroide em GMS (S/N, L/O) e UTM, e "Fonte da geometria: GeoJSON fornecido pelo usuário."; suíte do worker verde, PDFs reais inspecionados, geração em container `--network none`, `memorial.pdf` e `resultado.json` inalterados.
- [x] Validação de coordenadas e cobertura (2026-10-08): latitude/longitude fora dos limites (`coordenada_invalida`, código novo liberado na API), cobertura SIRGAS 2000 / UTM conferida por vértice, CRS declarado desconhecido ou `link` recusado antes do OGR; mensagens em português no formato `codigo: mensagem`; entradas inválidas não deixam pasta do job; suíte do worker verde normal e `--network none`; `mapa.pdf`, `memorial.pdf` e `resultado.json` de entradas válidas inalterados.
- [x] Fluxo assíncrono completo validado contra o stack local (2026-10-08): login → upload → `queued` → `started` → `completed`/`failed` → exatamente `mapa.pdf`, `memorial.pdf` e `resultado.json` → polling → 3 downloads → histórico; outro usuário recebe 404 nos 5 recursos do job; falha sem artefato parcial nem upload; Celery parado deixa o job `queued` e ele conclui quando o Celery volta. Banco fora ao marcar `started` agora também limpa os uploads e tenta marcar `failed`.
- [x] Testes unitários, worker, API e Playwright versionados.
- [x] Proteções de autenticação, autorização, CSRF, limites e arquivos.

## Próxima prioridade — produto

- [ ] Demonstrar o fluxo atual a um usuário real e registrar feedback sobre entrada, mapa e PDFs.
- [ ] Confirmar requisitos profissionais do memorial antes de chamá-lo de documento final.
- [ ] Validar arquivos reais maiores e variados sem colocá-los no Git.
- [ ] Definir critérios mensuráveis para tempo de processamento e concorrência.
- [ ] Decidir se KML e Shapefile entram no MVP ou em uma etapa posterior.
- [ ] Layout profissional do `mapa.pdf`, uma fatia por vez: escala numérica, grade de coordenadas GMS na moldura, rosa dos ventos configurável (muda `Prancha`/API/frontend — exige coordenação), moldura e marca de autoria "Gerado pelo GeoLume" (feita em 2026-10-06; não é selo técnico — ver `PROJECT_MEMORY.md`).
- [?] Mapa complementar de localização: bloqueado até existir contrato de dados local e licenciado (ex.: malha de UFs do IBGE versionada, com atribuição). Não desenhar mapa sem fonte.

## Próxima prioridade — experiência

- [ ] Melhorar acessibilidade e UX pendentes sem alterar o contrato de jobs.
- [ ] Confirmar o provedor de tiles para uso comercial antes de ativar satélite ou abrir tráfego de produção.
- [ ] Refinar estados de erro e recuperação de jobs presos.
- [ ] Separar claramente dados de demonstração de dados de cliente.

## Depois da validação do MVP

- [ ] Modelar clientes/imóveis somente após confirmar o fluxo com usuários.
- [ ] Definir autorização e multi-tenancy de produção.
- [ ] Planejar object storage, backups, HTTPS, secrets e observabilidade.
- [ ] Benchmarkar frio/quente com fixture determinística, RSS e limites do container.
- [ ] Adicionar formatos de entrada com testes de contrato específicos.

## Regras de execução

1. Não começar pelo frontend ou por infraestrutura de produção quando a decisão de produto ainda estiver aberta.
2. Cada tarefa de código deve ter escopo pequeno, teste e critério de aceitação.
3. Mudanças em PDF exigem geração real e inspeção visual.
4. Mudanças no worker QGIS exigem execução dentro do Docker.
5. Não apagar volume PostgreSQL ou artefatos existentes como forma de reset.
6. Marcar como concluída apenas após evidência real, não apenas após editar o arquivo.
