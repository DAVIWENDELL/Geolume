# GeoLume — PoC do worker PyQGIS

Recebe um GeoJSON com 1 polígono, reprojeta para SIRGAS 2000 / UTM, gera `mapa.pdf` + `resultado.json` e mede tempo por fase e memória. Sem API, fila ou banco: `geolume_worker.job.run_job()` é o ponto de entrada que uma task Celery vai chamar depois.

Plano: `docs/superpowers/plans/2026-09-26-poc-worker-pyqgis.md`.

## Pré-requisitos (Windows)

- Docker Desktop com backend WSL2 (Settings → Resources: anote CPUs e memória alocadas; elas entram na interpretação dos números).
- Todos os comandos abaixo rodam no PowerShell, a partir de `D:\Projetos\Geolume`.

## Comandos

```powershell
# 1. Construir a imagem (o primeiro pull de qgis/qgis:3.44.14-noble é grande)
docker compose build worker

# 2. Rodar todos os testes dentro do container
docker compose run --rm worker

# 2b. Integração com o PostgreSQL real (com a API no ar; cria e apaga um usuário temporário)
docker compose exec -e GEOLUME_DB_INTEGRATION=1 api python3 -m pytest -q tests/test_db_integration.py

# 3. Gerar um mapa de exemplo (resultado em .\saida\<job_id>\)
docker compose run --rm worker python3 -m geolume_worker run --input tests/fixtures/lote_simples.geojson --output /saida

# 4. Benchmark: 1 inicialização do QGIS + 20 jobs quentes (relatório em .\saida\benchmark-*.json)
docker compose run --rm worker python3 scripts/benchmark.py --input tests/fixtures/lote_simples.geojson --runs 20 --output /saida

# 5. Partida a frio completa (container + QGIS + 1 job), medida pelo Windows
Measure-Command { docker compose run --rm worker python3 -m geolume_worker run --input tests/fixtures/lote_simples.geojson --output /saida } | Select-Object TotalSeconds

# 6. Subir a API HTTP da PoC
docker compose build worker
docker compose up api

# Em outro PowerShell: health check (público; só {"status":"ok","service":"geolume-worker"})
Invoke-RestMethod http://localhost:8000/health

# 7. Subir API + Redis + PostgreSQL + worker Celery
# Execute em outro terminal após parar o modo API isolado:
# docker compose up api redis postgis celery
```

Fora `/health`, toda rota da API exige sessão (ver [Autenticação](#autenticação)):
envie jobs pela interface web. `POST /jobs` (síncrono) é só do administrador;
`POST /jobs/async` aceita qualquer usuário autenticado.

## Interface web

A tela de operação é servida pela própria API (`worker/web/`, HTML/CSS/JS puro, sem build).

```powershell
# Subir API + Redis + PostgreSQL + worker Celery
docker compose up -d api redis postgis celery

# Abrir no navegador
start http://localhost:8000
```

Na tela: enviar um `.geojson` (até 10 MB), acompanhar o job (`job_id`, `task_id`,
estado consultado a cada 2 s em `GET /jobs/{task_id}`), baixar `mapa.pdf`,
`memorial.pdf` e `resultado.json`, e abrir jobs anteriores pelo histórico
(últimos 20, atualizado a cada 5 s).

Pré-visualização: ao escolher o arquivo, o navegador valida o polígono (mesmas
regras do worker, só como aviso — Processar continua habilitado) e o desenha em
um mapa Leaflet com zoom na extensão. Depois do processamento, área e perímetro
(preliminares) vêm de `resultado.json`. Jobs do histórico (qualquer estado)
recuperam o GeoJSON original em `GET /jobs/{task_id}/input` e o desenham no mesmo
mapa; se o arquivo não existir mais, a tela avisa em vez de quebrar.

- `jobs.input_path` guarda onde o upload assíncrono salvou a entrada
  (`/saida/inputs/{job_id}-{input_filename}`). O `init_db` cria a coluna e preenche
  os jobs antigos pela mesma convenção, de forma idempotente e sob o advisory lock.
- O endpoint só serve um `.geojson` que, resolvido (inclusive links simbólicos),
  esteja diretamente em `/saida/inputs`; qualquer outro caso responde 404. O arquivo
  é aberto uma vez (`O_NOFOLLOW`), o descritor é revalidado (caminho real, arquivo
  regular, sem outros hard links) e o corpo sai desse descritor em streaming.

- Leaflet 1.9.4 (BSD-2) fica copiado em `web/vendor/leaflet/` (ver `VERSION.txt`);
  se não carregar, a tela funciona sem o mapa.
- Mapa-base: tiles de `tile.openstreetmap.org`, sem chave, com atribuição
  "© Contribuidores do OpenStreetMap". A política de uso do OSM não cobre tráfego
  de produção em escala: antes de abrir para clientes, trocar por um provedor de
  tiles contratado ou próprio.
- Os testes E2E substituem só os tiles por um PNG local; a API é sempre a real.

Camadas do mapa (painel acima do mapa, só aparece quando há geometria):

- O catálogo vem só do servidor: `GET /camadas` (exige sessão, 401 sem ela; mesma
  resposta para qualquer usuário; `Cache-Control: private, max-age=300`) devolve
  `camadas`, `estilos` e `alfa_padrao` de `geolume_worker/camadas.py`. O navegador
  não tem lista própria e só o pede depois do login.
- Grupos: "Disponíveis" (Mapa de ruas OSM, Sem mapa-base, Polígono do imóvel),
  "Dependem de fonte oficial" (Satélite, Hidrografia, Rodovias, Limites municipais)
  e "Planejadas" (Curvas de nível, Edificações). As indisponíveis aparecem
  desabilitadas, com o aviso do catálogo, e não têm URL. Satélite fica adiado até
  existir provedor com licença comercial confirmada.
- OSM só na pré-visualização: atribuição "© Contribuidores do OpenStreetMap" e aviso
  "Somente na pré-visualização — não entra no PDF". O mapa.pdf não tem camada raster
  nem faz acesso de rede (só o polígono, em camada vetorial de memória).
- Se o catálogo falhar (500, rede, JSON inválido ou URL não `https:`), a tela avisa
  "Camadas indisponíveis no momento…", fica sem mapa-base (nenhum tile é pedido) e o
  polígono continua desenhado. 401 no catálogo volta ao login normalmente.
- Polígono do imóvel: mostrar/ocultar; cores, espessura e opacidade vêm da prancha
  (do formulário ou do job). O contorno tem sempre opacidade 1.
- "Enquadrar polígono" volta o zoom para a geometria atual, mesmo com ela oculta.
- Legenda com o mapa-base ativo e o polígono. Se os tiles falharem, a tela avisa
  "Mapa-base indisponível no momento; o polígono continua visível." — o polígono,
  o envio e os downloads não dependem dos tiles.
- As escolhas ficam só em memória (sem `localStorage`), valem ao trocar de job e
  voltam ao padrão no logout. Lógica em `web/js/layers.js` (sem DOM) e `web/js/map.js`.

Personalização da prancha ("Personalizar prancha (opcional)", no Novo processamento):

- Campos, todos opcionais: nome do projeto e responsável técnico (até 100
  caracteres, sem quebras de linha nem caracteres de controle), logo da empresa
  (PNG ou JPEG, até 2 MB), cor principal (contorno) e cor de preenchimento
  (`#RRGGBB`), layout da prancha ("Padrão", "Legenda lateral" ou "Legenda
  inferior", escolhido em cartões com miniatura), estilo do polígono e opacidade do
  preenchimento.
- Estilos (cartões; escolher um preenche as cores, que podem ser trocadas depois e
  mantêm a espessura do estilo): "Padrão" #C80000/#FFC800, 0,6 mm no PDF e 3 px na
  tela; "Técnico" #1F2937/#9CA3AF, 0,35 mm/2 px; "Preto e branco" #000000/#FFFFFF,
  0,5 mm/2 px.
- "Opacidade do preenchimento (entra no PDF)": 0 a 100 %, padrão 35 %. Conversão
  única `alfa8 = floor(alfa × 255 + 0,5)` (metade para cima): o QGIS usa `alfa8` e o
  Leaflet `alfa8 / 255`, então tela e PDF coincidem (35 % ⇒ 89/255, 60 % ⇒ 153,
  100 % ⇒ 255). Com 0 % o preenchimento some e o contorno continua visível.
- No mapa.pdf: título "{projeto} — Mapa de Localização" (sem projeto, o título
  atual), linha "Responsável técnico: …", logo no canto superior direito, cores do
  polígono e, nos layouts com legenda, a amostra "Limite do imóvel". Texto do
  usuário ou do GeoJSON sai literal no PDF (`[% 1+1 %]` não é avaliado pelo QGIS).
- Sem nenhum campo preenchido o envio é igual ao de antes (só o GeoJSON), o job
  fica com `prancha` nula e o PDF sai como sempre. Jobs antigos e tarefas já
  enfileiradas continuam funcionando: `prancha` nula ou sem as chaves novas vale
  `estilo="padrao"` e `alfa_preenchimento=0.35` (sem migração de banco).
- A tela mostra acima do mapa o cabeçalho (título, responsável, logo) e pinta o
  polígono com as cores da prancha: do formulário enquanto há arquivo escolhido,
  do job quando um job está selecionado. Os campos valem para os próximos envios e
  voltam ao padrão em "Restaurar padrão" e no logout.
- Servidor (a validação do navegador é só conveniência): `POST /jobs/async` aceita
  os campos `projeto`, `responsavel`, `cor_contorno`, `cor_preenchimento`, `legenda`,
  `estilo` (`padrao`, `tecnico`, `pb`), `alfa_preenchimento` (número de 0 a 1 com
  ponto; `"0,5"`, `NaN` e fora da faixa são recusados, nunca corrigidos) e o arquivo
  `logo`; campo inválido responde 422 e nada é gravado (ex.: "alfa_invalido:
  Opacidade do preenchimento deve estar entre 0 e 1.", "estilo_invalido: Estilo do
  polígono inválido."). A logo é
  validada pelo conteúdo (só PNG e JPEG; SVG, arquivo falso e imagem inválida são
  recusados; dimensões acima de 10 000 px ou 25 Mpx são recusadas antes de
  decodificar), gravada só normalizada (PNG, lado ≤ 1000 px) com nome derivado do
  job, nunca do nome enviado. O worker revalida as opções e a logo antes do PDF.
- `GET /jobs/{task_id}/logo` exige sessão e o mesmo acesso do job (404 "Job não
  encontrado" para outro usuário ou tenant; 404 "Logo não disponível" se o job não
  tem logo). A resposta de `/jobs` traz `prancha` com a URL da logo, sem caminhos.

Layouts da prancha (mapa.pdf):

- Sempre 1 página A4 paisagem, com todo o conteúdo a pelo menos 5 mm da borda e
  sem rótulos sobrepostos, nos três layouts. "Padrão" é o mapa de antes; "Legenda
  lateral" põe a legenda na coluna direita, abaixo da escala; "Legenda inferior"
  põe a legenda abaixo do mapa, ao lado da tabela. As fontes não diminuem.
- Tabela de vértices: entram só as linhas que cabem (medidas com a fonte real);
  se sobrar vértice, a tabela termina com "Exibidos K de M vértices. Demais
  vértices no memorial descritivo." A lista completa fica sempre no memorial.
- Propriedades longas do GeoJSON são cortadas com "…" na largura da coluna, só no
  mapa; no memorial saem inteiras (quebradas em linhas) e o `resultado.json`
  guarda o valor original, sem alteração.
- Layout inválido é recusado com mensagem fixa, sem ecoar o valor: 422 "Legenda
  deve ser 'nenhuma', 'lateral' ou 'inferior'." na API e "Layout da prancha
  inválido." no navegador (nenhum envio sai). O worker revalida.

Memorial paginado (memorial.pdf):

- A4 retrato, área útil 18–192 × 15–260 mm; toda página tem o rodapé "Documento
  preliminar gerado automaticamente pelo Geolume.". Memorial pequeno (6 vértices)
  mantém as posições de antes.
- Quadro de vértices e descrição continuam na página seguinte quando não cabem:
  todos os vértices, em ordem, sem repetir nem perder; a continuação do quadro
  tem o título "Quadro de vértices (continuação)" e o cabeçalho da tabela. Um
  título de seção nunca fica sozinho no fim da página. Com 300 vértices são 5
  páginas.

Textos no PDF (mapa e memorial):

- Nome do projeto e responsável técnico são recusados (422) com quebra de linha,
  tab, caractere de controle, separador de linha/parágrafo (U+2028/U+2029) ou
  controle bidi (embutir/isolar U+202A–202E e U+2066–2069, marcas U+200E, U+200F e
  U+061C).
- Propriedades do GeoJSON não são recusadas: saem em uma linha. Controle (quebra,
  CR, tab) vira espaço, caractere de formatação (bidi, largura zero) é removido e
  espaços repetidos viram um. ZWJ e ZWNJ ficam (emoji compostos, escritas que os
  usam). Valor nulo (`null`) ou que fica vazio depois da limpeza conta como não
  informado: some do mapa e aparece como "Não informado" no memorial.

## Autenticação

Sessão própria com cookie opaco, guardada no PostgreSQL. Sem sessão, só `/health`
e os arquivos estáticos da tela respondem; a autorização é feita no backend.
`/docs`, `/redoc` e `/openapi.json` estão desligados (404).

- Usuários são criados pela linha de comando, dentro do contêiner `api`. A senha
  é pedida no terminal (duas vezes, mínimo 12 caracteres) e nunca é argumento;
  só o hash scrypt é gravado:

  ```powershell
  docker compose exec api python3 scripts/users.py create --email <email> --role admin
  docker compose exec api python3 scripts/users.py create --email <email> --role member
  docker compose exec api python3 scripts/users.py reset-password --email <email>
  docker compose exec api python3 scripts/users.py disable --email <email>
  ```

  `reset-password` e `disable` encerram as sessões abertas do usuário. Para
  automação, `--password-stdin` lê a senha da primeira linha do stdin.
- Acesso: o membro vê e baixa só os próprios jobs; o administrador vê todos,
  inclusive os jobs antigos (`owner_id` nulo, anteriores à autenticação). Job
  inexistente ou de outro usuário responde 404 "Job não encontrado" em
  `/jobs/{task_id}`, `/files/{mapa,memorial,resultado}`, `/input` e `/logo`.
- Download (`/files/{mapa,memorial,resultado}`): o caminho gravado no banco só é
  servido se for exatamente `mapa.pdf`, `memorial.pdf` ou `resultado.json` na pasta
  do próprio job (`/saida/{id}`, id só com letras, dígitos, `_` e `-`; pasta real,
  não link). O arquivo é aberto uma vez sem seguir link, precisa ser regular e sem
  outro hard link, e o envio lê desse descritor (o caminho não é reaberto). Caso
  contrário, 404 "Arquivo não encontrado". O mesmo vale para `/input` e `/logo`.
- Cookie `geolume_session`: HttpOnly, Secure, SameSite=Lax, Path=/. O banco guarda
  só o SHA-256 do token. A sessão expira após 2 h sem uso ou 12 h após o login,
  o que vier primeiro; aí a API responde 401 e a tela volta ao login, limpando
  mapa, histórico e detalhe.
- Login: 5 senhas erradas seguidas bloqueiam o usuário por 15 min; a mensagem é
  sempre "E-mail ou senha inválidos".
- CSRF: todo `POST` exige o cabeçalho `X-GeoLume-CSRF: 1` e, se houver `Origin`,
  que ele seja a própria API; senão 403 "Requisição recusada".
- Demonstração só em `localhost`: o `docker-compose.yml` publica a API em
  `127.0.0.1:8000` (o navegador aceita cookie Secure em `http://localhost`).
  Acesso pela rede local fica para a etapa com HTTPS.
- Upload sem sessão: `POST /jobs` e `POST /jobs/async` sem cookie de sessão válido
  recebem 401 "Autenticação necessária" antes de qualquer byte do corpo ser lido
  (vale também sem o cabeçalho de CSRF, com `Content-Length` ou chunked); nada é
  gravado nem registrado. Com sessão, o CSRF e o limite abaixo continuam valendo.
- Upload: o GeoJSON tem limite de 10 MB e a logo de 2 MB. `POST /jobs` recusa com
  413 "Arquivo maior que 10 MB."; `POST /jobs/async` (GeoJSON + logo) limita o
  envio inteiro a 12 MB ("Envio maior que 12 MB.") e cada arquivo ao seu limite
  ("Arquivo maior que 10 MB.", "Logo maior que 2 MB."), no servidor, sem depender
  do navegador. `Content-Length` acima do limite
  é recusado antes de ler o corpo; sem ele (ou com valor falso) a leitura para no
  primeiro bloco além do limite. Upload interrompido ou recusado não deixa job nem
  arquivo em `/saida/inputs`.
- Erros para o cliente: só as mensagens de validação do worker (`codigo: mensagem`)
  saem como vieram; qualquer outra falha aparece como "Falha no processamento do
  job." (status e histórico) ou "Não foi possível processar o arquivo." (`POST /jobs`),
  sem exceção nem caminho interno. O texto original fica no log e no banco.
- Respostas dos jobs (`GET /jobs`, `GET /jobs/{task_id}`) só têm `job_id`, `task_id`,
  `status`, `input_filename`, `created_at`, `completed_at`, `erro` e `arquivos`
  (URLs `/jobs/{task_id}/files/{mapa|memorial|resultado}`, só quando concluído).
  Caminhos do servidor, `owner_id` e `tenant_id` ficam no banco. `POST /jobs/async`
  devolve `status`, `job_id` e `task_id`; `POST /jobs` devolve `status`, `job_id`,
  `input_filename` e `fases_ms` (os arquivos do job síncrono ficam só em `/saida`).

O worker Celery mantém uma única sessão QGIS por processo: encerrar e reabrir
o QGIS no mesmo processo derruba o worker (SIGSEGV).

### Testes da interface (`tests-web/`)

Requer Node 24 e o Google Chrome instalado. Os testes E2E rodam contra a API real
em `http://localhost:8000` (ou `GEOLUME_URL`, recusado se não for localhost) e
enviam jobs de verdade. O `global-setup.mjs` cria ou redefine três usuários
exclusivos do teste local (`e2e-admin@`, `e2e-a@` e `e2e-b@geolume.test`) com
senhas aleatórias a cada execução, passadas ao `users.py` pelo stdin, e grava
sessões e senhas em `tests-web/.auth/`, que fica fora do Git.

```powershell
cd tests-web
npm install
npm run test:unit   # lógica pura (node:test)
npm run test:e2e    # Playwright com o Chrome do sistema
```

## Entrada aceita

GeoJSON (EPSG:4326 ou EPSG:4674) com exatamente 1 polígono simples, sem furos, até 10 MB e 5 000 vértices, com todos os vértices dentro dos fusos UTM 17N–22N / 18S–25S. Coordenadas fora de -90°..90° (latitude) ou -180°..180° (longitude) saem como `coordenada_invalida`; vértice fora dos fusos, como `fora_da_cobertura`; CRS declarado não aceito ou não reconhecido, como `crs_nao_suportado`. Erros saem com código 2 e JSON no stderr, por exemplo `{"status": "erro", "codigo": "poligono_com_furos", ...}`.

## Métricas

| Campo | Significado |
|---|---|
| `inicializacao_qgis` | import do PyQGIS + `initQgis()` — pago 1 vez por processo |
| `carregar` / `processar` / `renderizar_pdf` | fases de um job |
| `total_job` | soma do job (sem a inicialização) |
| `pico_rss_mb` | pico de memória do processo desde o início (não por job) |
| `rss_por_job_mb` | RSS após cada job — crescimento contínuo indica vazamento |
| `cgroup_pico_mb` | pico de memória do container inteiro (limite: `mem_limit: 2g` no compose) |
