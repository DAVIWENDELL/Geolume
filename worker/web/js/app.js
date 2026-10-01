// Estado e ligações da tela principal (envio, pré-visualização, resultado e histórico).
import { createApi } from "./api.js";
import { parseGeoJsonPreview, parseMetrics } from "./geo.js";
import { CATALOGO_VAZIO, parseCatalogo } from "./layers.js";
import { createMapView } from "./map.js";
import { etapaArquivo, isCurrent, mensagemErro, motivoProcessar, isTerminal, normalizeStatus, summarize, validateUpload } from "./model.js";
import { createPoller } from "./poller.js";
import {
  LAYOUTS,
  PRANCHA_PADRAO,
  alfaFromPercent,
  coresDoEstilo,
  pranchaFields,
  polygonStyle,
  resumoEnvio,
  validateLogo,
  validatePrancha,
} from "./prancha.js";
import {
  markSelectedRow,
  renderAmostraPoligono,
  renderArquivo,
  renderCamadasPanel,
  renderDetail,
  renderEstiloCards,
  renderHealth,
  renderJobs,
  renderLayoutCards,
  renderMapTools,
  renderMessage,
  renderPranchaHead,
  renderPreview,
  renderResumo,
  renderSession,
  renderSummary,
  renderUploadFile,
} from "./render.js";

const JOB_INTERVAL = 2_000;
const LIST_INTERVAL = 5_000;
const HEALTH_INTERVAL = 15_000;
const MAX_INTERVAL = 15_000;

const EXPIRED = "Sua sessão expirou. Entre novamente.";

// 401 fora do login: a sessão acabou no servidor (expirou, logout em outra aba, usuário desativado).
const api = createApi(undefined, { onUnauthorized: () => state.user && signOut(EXPIRED) });
const $ = (testid) => document.querySelector(`[data-testid="${testid}"]`);

const ui = {
  health: $("health"),
  input: $("upload-input"),
  drop: $("upload-drop"),
  file: $("upload-file"),
  message: $("upload-message"),
  submit: $("upload-submit"),
  detail: $("detail"),
  summary: $("summary"),
  jobsBody: document.querySelector("[data-jobs-body]"),
  jobsEmpty: $("jobs-empty"),
  jobsError: $("jobs-error"),
  jobsErrorText: document.querySelector("[data-jobs-error-text]"),
  jobsRetry: document.querySelector("[data-jobs-retry]"),
  refresh: $("jobs-refresh"),
  preview: $("preview"),
  app: $("app"),
  login: $("login"),
  loginForm: document.querySelector("[data-login-form]"),
  loginEmail: $("login-email"),
  loginPassword: $("login-password"),
  loginSubmit: $("login-submit"),
  loginMessage: $("login-message"),
  session: $("session"),
  logout: $("logout"),
  pranchaForm: $("prancha-form"),
  pranchaHead: $("prancha-head"),
  pranchaMessage: $("prancha-message"),
  pranchaLogo: $("prancha-input-logo"),
  pranchaLogoRemove: $("prancha-logo-remove"),
  pranchaReset: $("prancha-reset"),
  pranchaAlfa: $("prancha-input-alfa"),
  pranchaAlfaValor: $("prancha-alfa-valor"),
  pranchaEstilos: $("prancha-estilos"),
  camadas: $("camadas-painel"),
  arquivo: document.querySelector("[data-arquivo]"),
  resumo: $("envio-resumo"),
  envioMotivo: $("envio-motivo"),
};

// Campos da prancha pelo nome que a API espera.
const PRANCHA_INPUTS = {
  projeto: $("prancha-input-projeto"),
  responsavel: $("prancha-input-responsavel"),
  cor_contorno: $("prancha-input-contorno"),
  cor_preenchimento: $("prancha-input-preenchimento"),
};
// Layout: rádios montados a partir de LAYOUTS; o marcado vai como "legenda".
renderLayoutCards($("prancha-layouts"), LAYOUTS, PRANCHA_PADRAO.legenda);
const LAYOUT_RADIOS = [...document.querySelectorAll('input[name="prancha-legenda"]')];

const jobsLoadingRow = ui.jobsBody.querySelector("[data-jobs-loading]").cloneNode(true);

// Catálogo de GET /camadas, pedido só depois do login. Até chegar (ou se falhar), CATALOGO_VAZIO:
// polígono sem mapa-base e nenhum tile. Escolhas de camadas só em memória, até o logout.
let catalogo = CATALOGO_VAZIO;
const mapView = createMapView($("preview-map"), { onChange: (mapState) => renderMapTools(ui.preview, mapState, catalogo) });

const estiloEscolhido = () => ui.pranchaEstilos.querySelector('input[name="prancha-estilo"]:checked')?.value ?? "";

/** Painel, cartões de estilo e mapa a partir do catálogo; erro = GET /camadas falhou (não 401). */
function showCatalogo(next, { erro = false } = {}) {
  catalogo = next;
  renderCamadasPanel(ui.camadas, catalogo, { erro });
  renderEstiloCards(ui.pranchaEstilos, catalogo.estilos, estiloEscolhido() || PRANCHA_PADRAO.estilo);
  mapView.setCatalogo(catalogo);
  renderMapTools(ui.preview, mapView.state, catalogo);
  showEnvio(); // os nomes dos estilos do resumo vêm do catálogo
}

const state = {
  user: null, // { email, role } da sessão atual
  session: 0, // muda a cada saída: respostas de antes dela são descartadas
  file: null,
  sending: false,
  selectedTaskId: null,
  job: null, // último estado conhecido do job selecionado
  warning: null, // falha da última consulta do job selecionado
  jobPoller: null,
  jobsKey: null,
  preview: null, // { file, result } do arquivo selecionado, depois de lido
  previewGen: 0, // descarta leituras superadas por outro arquivo
  shapes: new Map(), // task_id -> { name, geometry, bounds } (envios desta sessão ou GET /input)
  inputs: new Map(), // task_id -> { loading } | { message, retry? } enquanto não há shape
  metrics: new Map(), // task_id -> { areaHa, perimetroM } | null
  logo: null, // { file, url } da logo escolhida; url (de objeto) só se passou na checagem
};

showCatalogo(CATALOGO_VAZIO);

async function loadCatalogo() {
  const session = state.session;
  let corpo;
  try {
    corpo = await api.getCamadas();
  } catch (err) {
    if (session !== state.session || err.status === 401) return; // 401: onUnauthorized já levou ao login
    showCatalogo(CATALOGO_VAZIO, { erro: true });
    return;
  }
  if (session !== state.session) return;
  const parsed = parseCatalogo(corpo);
  if (!parsed || !parsed.camadas.length) showCatalogo(CATALOGO_VAZIO, { erro: true });
  else showCatalogo(parsed);
  pranchaChanged(); // estilo escolhido passa a ser conferido contra os estilos do catálogo
}

// ---- Saúde da API -------------------------------------------------------

const healthPoller = createPoller({
  interval: HEALTH_INTERVAL,
  maxInterval: MAX_INTERVAL,
  task: async () => {
    try {
      await api.health();
      renderHealth(ui.health, true);
    } catch (err) {
      renderHealth(ui.health, false);
      throw err;
    }
    return true;
  },
});

// ---- Histórico ----------------------------------------------------------

async function refreshJobs({ isStale }) {
  try {
    const jobs = await api.listJobs(20);
    if (isStale()) return false; // lista recarregada enquanto esta consulta rodava
    ui.jobsError.hidden = true;
    renderSummary(ui.summary, summarize(jobs));
    ui.jobsEmpty.hidden = jobs.length > 0;

    const key = JSON.stringify(jobs);
    if (key !== state.jobsKey) {
      state.jobsKey = key;
      renderJobs(ui.jobsBody, jobs, state.selectedTaskId, (job) => selectJob(job));
    }
  } catch (err) {
    if (isStale()) return false;
    ui.jobsErrorText.textContent = `Não foi possível carregar o histórico: ${err.message}.`;
    ui.jobsError.hidden = false;
    if (state.jobsKey === null) ui.jobsBody.replaceChildren();
    throw err;
  }
  return true;
}

const listPoller = createPoller({ task: refreshJobs, interval: LIST_INTERVAL, maxInterval: MAX_INTERVAL });

function reloadJobs() {
  listPoller.stop();
  listPoller.start();
}

// ---- Job selecionado ----------------------------------------------------

function toJob(data, fallback = {}) {
  return {
    task_id: data.task_id ?? fallback.task_id,
    job_id: data.job_id ?? data.id ?? fallback.job_id,
    input_filename: data.input_filename ?? fallback.input_filename,
    status: normalizeStatus(data.status),
    erro: data.erro ?? fallback.erro,
    prancha: data.prancha !== undefined ? data.prancha : fallback.prancha ?? null,
  };
}

function pollSelected(taskId) {
  return createPoller({
    interval: JOB_INTERVAL,
    maxInterval: MAX_INTERVAL,
    task: async ({ isStale }) => {
      renderDetail(ui.detail, state.job, { loading: true, warning: state.warning });
      let data;
      try {
        data = await api.getJob(taskId);
      } catch (err) {
        if (!isStale() && isCurrent(state.selectedTaskId, taskId)) {
          state.warning = err.message;
          renderDetail(ui.detail, state.job, { warning: state.warning });
        }
        throw err;
      }
      if (isStale() || !isCurrent(state.selectedTaskId, taskId)) return false; // resposta superada
      const previous = state.job.status;
      state.warning = null;
      state.job = toJob(data, state.job);
      renderDetail(ui.detail, state.job);
      if (state.job.status !== previous) reloadJobs();
      if (state.job.status === "completed" && previous !== "completed") loadMetrics(taskId);
      return !isTerminal(state.job.status);
    },
  });
}

function selectJob(raw) {
  state.jobPoller?.stop();
  state.selectedTaskId = raw.task_id;
  state.warning = null;
  state.job = toJob(raw);
  renderDetail(ui.detail, state.job);
  markSelectedRow(ui.jobsBody, state.selectedTaskId);
  showPreview();
  loadInput(raw.task_id, state.job.input_filename);
  if (state.job.status === "completed") loadMetrics(raw.task_id);

  state.jobPoller = isTerminal(state.job.status) ? null : pollSelected(raw.task_id);
  if (!document.hidden) state.jobPoller?.start();
}

// ---- Pré-visualização ---------------------------------------------------

const PREVIEW_IDLE = "Selecione um GeoJSON para ver o polígono no mapa.";

function drawOrNotice(shape, message) {
  if (mapView.show(shape.geometry, shape.bounds)) return message;
  return `${message} Mapa indisponível no momento; o processamento não é afetado.`;
}

/** Cabeçalho e estilo do polígono; prancha nula = padrão (jobs sem personalização e jobs antigos). */
function showPrancha(prancha, visible = true) {
  mapView.setStyle(prancha);
  const estilo = polygonStyle(prancha, catalogo.estilos); // o mesmo que o mapa acabou de aplicar
  const colors = { contorno: estilo.color, preenchimento: estilo.fillColor };
  renderAmostraPoligono(ui.preview, estilo);
  renderPranchaHead(ui.pranchaHead, visible ? { ...prancha, colors, opacity: estilo.fillOpacity } : null);
}

/** Prancha do job vinda da API: a logo só pela rota autenticada do próprio job. */
function jobPrancha(prancha) {
  if (!prancha || typeof prancha !== "object") return null;
  const logo = typeof prancha.logo === "string" && prancha.logo.startsWith("/jobs/") ? prancha.logo : null;
  return { ...prancha, logo };
}

/** Etapas 2 e 4 do envio: situação do arquivo escolhido e resumo do que vai para o mapa.pdf. */
function showEnvio() {
  const file = state.file;
  const preview = state.preview?.file === file ? state.preview.result : null;
  renderArquivo(ui.arquivo, etapaArquivo({ file, check: validateUpload(file), preview }));
  renderResumo(ui.resumo, resumoEnvio(pranchaValues(), { estilos: catalogo.estilos, logo: Boolean(state.logo?.url) }));
}

/** Arquivo selecionado tem prioridade; senão, o job selecionado; senão, o convite. */
function showPreview() {
  showEnvio();
  const file = state.file;
  if (file) {
    const readable = validateUpload(file).ok;
    showPrancha({ ...pranchaValues(), logo: state.logo?.url ?? null }, readable);
    const ready = state.preview?.file === file ? state.preview.result : null;
    if (!readable || !ready) {
      mapView.clear();
      return renderPreview(ui.preview, { message: readable ? `Lendo ${file.name}…` : PREVIEW_IDLE });
    }
    if (!ready.ok) {
      mapView.clear();
      return renderPreview(ui.preview, {
        message: `${file.name}: ${ready.message} O processamento provavelmente falhará; você ainda pode enviar para confirmar.`,
        kind: "error",
      });
    }
    return renderPreview(ui.preview, { message: drawOrNotice(ready, `${file.name}: polígono pronto para processar.`) });
  }

  const taskId = state.selectedTaskId;
  showPrancha(jobPrancha(state.job?.prancha), taskId != null);
  if (taskId == null) {
    mapView.clear();
    return renderPreview(ui.preview, { message: PREVIEW_IDLE });
  }
  const metrics = state.metrics.get(taskId) ?? null;
  const shape = state.shapes.get(taskId);
  if (shape) return renderPreview(ui.preview, { message: drawOrNotice(shape, `Job selecionado: ${shape.name}.`), metrics });
  mapView.clear();
  const input = state.inputs.get(taskId);
  if (!input || input.loading) {
    const name = state.job?.input_filename;
    return renderPreview(ui.preview, { message: name ? `Carregando a geometria de ${name}…` : "Carregando a geometria do job…", metrics });
  }
  return renderPreview(ui.preview, { message: input.message, kind: "error", metrics });
}

/** Geometria original de um job do histórico; o cache por task_id impede misturar respostas. */
async function loadInput(taskId, name = "arquivo") {
  const known = state.inputs.get(taskId);
  if (state.shapes.has(taskId) || (known && !known.retry)) return;
  const session = state.session;
  state.inputs.set(taskId, { loading: true });
  try {
    const result = parseGeoJsonPreview(await api.getInput(taskId));
    if (session !== state.session) return;
    if (result.ok) {
      state.shapes.set(taskId, { name, geometry: result.geometry, bounds: result.bounds });
      state.inputs.delete(taskId);
    } else {
      state.inputs.set(taskId, { message: `${name}: ${result.message}` });
    }
  } catch (err) {
    if (session !== state.session) return;
    state.inputs.set(
      taskId,
      err.status === 404
        ? { message: "O GeoJSON original deste job não está disponível no servidor." }
        : { message: `Não foi possível carregar a geometria deste job (${err.message}). Selecione o job de novo para tentar.`, retry: true },
    );
  }
  if (isCurrent(state.selectedTaskId, taskId)) showPreview();
}

async function readPreview(file) {
  const gen = ++state.previewGen;
  let result;
  try {
    result = parseGeoJsonPreview(await file.text());
  } catch {
    result = { ok: false, code: "leitura", message: "Não foi possível ler o arquivo." };
  }
  if (gen !== state.previewGen || state.file !== file) return; // outro arquivo foi escolhido
  state.preview = { file, result };
  showPreview();
}

async function loadMetrics(taskId) {
  if (state.metrics.has(taskId)) return;
  const session = state.session;
  let metrics;
  try {
    metrics = parseMetrics(await api.getResult(taskId));
  } catch {
    return; // sem métricas: o painel segue sem elas e os downloads continuam no job
  }
  if (session !== state.session) return;
  state.metrics.set(taskId, metrics);
  if (isCurrent(state.selectedTaskId, taskId)) showPreview();
}

// Itens do painel são refeitos a cada catálogo: um ouvinte só, no painel.
ui.camadas.addEventListener("change", (event) => {
  const input = event.target;
  if (input.dataset.testid === "polygon-toggle") mapView.setPolygonVisible(input.checked);
  // Camada recusada (desabilitada ou desconhecida): o controle volta para a atual.
  else if (input.name === "map-base" && !mapView.setBase(input.value)) renderMapTools(ui.preview, mapView.state, catalogo);
});
$("polygon-fit").addEventListener("click", () => mapView.fit());

// ---- Prancha ------------------------------------------------------------

function pranchaValues() {
  const campos = Object.fromEntries(Object.entries(PRANCHA_INPUTS).map(([campo, input]) => [campo, input.value]));
  campos.legenda = LAYOUT_RADIOS.find((radio) => radio.checked)?.value ?? "";
  campos.estilo = estiloEscolhido();
  // Controle adulterado (fora de 0–100) vira NaN: validatePrancha recusa com a mensagem fixa.
  const alfa = alfaFromPercent(ui.pranchaAlfa.value);
  campos.alfa_preenchimento = alfa.ok ? alfa.value : Number.NaN;
  return campos;
}

/** Formulário e logo: { ok, values } ou { ok: false, message }. O servidor confere tudo de novo. */
function pranchaCheck() {
  const campos = validatePrancha(pranchaValues(), catalogo.estilos);
  if (!campos.ok) return campos;
  const logo = validateLogo(state.logo?.file);
  return logo.ok ? campos : logo;
}

function updateSubmit() {
  const motivo = motivoProcessar({ sending: state.sending, arquivoOk: validateUpload(state.file).ok, pranchaOk: pranchaCheck().ok });
  ui.submit.disabled = Boolean(motivo);
  ui.envioMotivo.textContent = state.sending ? "" : motivo; // durante o envio o próprio botão diz "Enviando…"
}

function pranchaChanged() {
  const check = pranchaCheck();
  ui.pranchaAlfaValor.textContent = `${ui.pranchaAlfa.value}%`;
  renderMessage(ui.pranchaMessage, check.ok ? "" : check.message);
  ui.pranchaLogoRemove.hidden = !state.logo;
  updateSubmit();
  showPreview();
}

function setLogo(file) {
  if (state.logo?.url) URL.revokeObjectURL(state.logo.url);
  state.logo = file ? { file, url: validateLogo(file).ok ? URL.createObjectURL(file) : null } : null;
  if (!file) ui.pranchaLogo.value = "";
  pranchaChanged();
}

function resetPrancha() {
  for (const [campo, input] of Object.entries(PRANCHA_INPUTS)) input.value = PRANCHA_PADRAO[campo];
  for (const radio of LAYOUT_RADIOS) radio.checked = radio.value === PRANCHA_PADRAO.legenda;
  for (const radio of ui.pranchaEstilos.querySelectorAll('input[name="prancha-estilo"]')) {
    radio.checked = radio.value === PRANCHA_PADRAO.estilo;
  }
  ui.pranchaAlfa.value = String(Math.round(PRANCHA_PADRAO.alfa_preenchimento * 100));
  setLogo(null);
}

for (const input of Object.values(PRANCHA_INPUTS)) input.addEventListener("input", pranchaChanged);
for (const radio of LAYOUT_RADIOS) radio.addEventListener("change", pranchaChanged);
ui.pranchaAlfa.addEventListener("input", pranchaChanged);
// Escolher um estilo preenche as cores dele; depois, as cores podem ser editadas sem mudar a espessura.
ui.pranchaEstilos.addEventListener("change", (event) => {
  const cores = coresDoEstilo(event.target.value, catalogo.estilos);
  if (cores) {
    PRANCHA_INPUTS.cor_contorno.value = cores.cor_contorno.toLowerCase();
    PRANCHA_INPUTS.cor_preenchimento.value = cores.cor_preenchimento.toLowerCase();
  }
  pranchaChanged();
});
ui.pranchaLogo.addEventListener("change", () => setLogo(ui.pranchaLogo.files[0] ?? null));
ui.pranchaLogoRemove.addEventListener("click", () => setLogo(null));
ui.pranchaReset.addEventListener("click", resetPrancha);
$("prancha-logo").addEventListener("error", (event) => {
  event.target.hidden = true; // logo indisponível: o cabeçalho segue sem ela
});

// ---- Upload -------------------------------------------------------------

function setFile(file) {
  state.file = file ?? null;
  renderUploadFile(ui.file, state.file);
  const check = validateUpload(state.file);
  if (state.file && !check.ok) renderMessage(ui.message, check.message, "error");
  else renderMessage(ui.message, "");
  updateSubmit();

  if (state.preview?.file !== state.file) {
    state.preview = null;
    if (state.file && check.ok) readPreview(state.file);
    else state.previewGen += 1;
  }
  showPreview();
}

/** Resultado do envio sem esconder o aviso de um arquivo inválido escolhido durante ele. */
function uploadNotice(text, kind) {
  const check = validateUpload(state.file);
  if (state.file && !check.ok) renderMessage(ui.message, `${text} ${state.file.name}: ${check.message}.`, "error");
  else renderMessage(ui.message, text, kind);
}

async function submit() {
  if (state.sending) return;
  const check = validateUpload(state.file);
  if (!check.ok) {
    renderMessage(ui.message, check.message, "error");
    return;
  }
  const prancha = pranchaCheck();
  if (!prancha.ok) {
    renderMessage(ui.pranchaMessage, prancha.message);
    return;
  }
  const fields = pranchaFields(prancha.values, catalogo.estilos);
  const logo = state.logo?.file ?? null;
  const file = state.file;
  // O seletor continua ativo durante o envio: guarda a prévia deste arquivo agora
  // e, no fim, só mexe na seleção se ela ainda for este arquivo.
  const previewOf = () => (state.preview?.file === file ? state.preview.result : null);
  const before = previewOf();
  const session = state.session;
  state.sending = true;
  ui.submit.disabled = true;
  ui.submit.textContent = "Enviando…";
  renderMessage(ui.message, "");
  try {
    const queued = await api.enqueue(file, { fields, logo });
    if (session !== state.session) return; // saiu durante o envio: nada disso é do usuário atual
    const sent = before ?? previewOf();
    if (sent?.ok) state.shapes.set(queued.task_id, { name: file.name, geometry: sent.geometry, bounds: sent.bounds });
    state.sending = false;
    if (state.file === file) {
      ui.input.value = "";
      setFile(null);
    } else {
      setFile(state.file);
    }
    uploadNotice(`${file.name} enviado. Acompanhe o andamento em Resultado.`, "ok");
    // Sem personalização o job fica com prancha nula, como no servidor.
    const enviada = fields.length || logo
      ? { ...prancha.values, logo: logo ? `/jobs/${encodeURIComponent(queued.task_id)}/logo` : null }
      : null;
    selectJob({ ...queued, input_filename: file.name, prancha: enviada });
    reloadJobs();
  } catch (err) {
    if (session !== state.session) return;
    state.sending = false;
    setFile(state.file);
    // detail de validação vem como "codigo: mensagem."; o código não ajuda quem lê.
    const motivo = mensagemErro(err.message).replace(/\.$/, "");
    uploadNotice(`Não foi possível enviar: ${motivo}.`, "error");
  } finally {
    ui.submit.textContent = "Processar";
  }
}

ui.input.addEventListener("change", () => setFile(ui.input.files[0]));
ui.submit.addEventListener("click", submit);

for (const type of ["dragenter", "dragover"]) {
  ui.drop.addEventListener(type, (event) => {
    event.preventDefault();
    ui.drop.classList.add("is-dragging");
  });
}
for (const type of ["dragleave", "drop"]) {
  ui.drop.addEventListener(type, (event) => {
    event.preventDefault();
    ui.drop.classList.remove("is-dragging");
  });
}
ui.drop.addEventListener("drop", (event) => setFile(event.dataTransfer.files[0]));

// ---- Copiar ids ---------------------------------------------------------

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = $(button.dataset.copy).textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copiado";
    } catch {
      button.textContent = "Falhou ao copiar";
    }
    setTimeout(() => (button.textContent = "Copiar"), 1500);
  });
});

// ---- Sessão --------------------------------------------------------------

function start(user) {
  state.user = user;
  renderSession(ui.session, user);
  renderMessage(ui.loginMessage, "");
  ui.login.hidden = true;
  ui.app.hidden = false;
  renderDetail(ui.detail, null);
  showPreview();
  listPoller.start();
  loadCatalogo();
}

/** Volta ao login sem deixar nada do usuário anterior na memória nem no DOM. */
function signOut(message = "", kind = "error") {
  state.user = null;
  state.session += 1;
  listPoller.stop();
  state.jobPoller?.stop();
  state.jobPoller = null;
  state.selectedTaskId = null;
  state.job = null;
  state.warning = null;
  state.jobsKey = null;
  state.sending = false;
  state.shapes.clear();
  state.inputs.clear();
  state.metrics.clear();
  state.preview = null;

  resetPrancha(); // personalização não passa para o próximo usuário
  ui.pranchaForm.open = false;
  ui.input.value = "";
  setFile(null); // descarta leitura pendente e limpa pré-visualização e mapa
  mapView.reset(); // camadas voltam ao padrão para a próxima sessão
  showCatalogo(CATALOGO_VAZIO); // o próximo login pede o catálogo de novo
  ui.submit.textContent = "Processar";
  renderMessage(ui.message, "");
  renderDetail(ui.detail, null);
  ui.jobsBody.replaceChildren(jobsLoadingRow.cloneNode(true));
  renderSummary(ui.summary, summarize([]));
  ui.jobsEmpty.hidden = true;
  ui.jobsError.hidden = true;
  ui.jobsErrorText.textContent = "";

  renderSession(ui.session, null);
  ui.app.hidden = true;
  ui.login.hidden = false;
  renderMessage(ui.loginMessage, message, message ? kind : undefined);
}

ui.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (ui.loginSubmit.disabled) return;
  ui.loginSubmit.disabled = true;
  renderMessage(ui.loginMessage, "");
  try {
    const user = await api.login(ui.loginEmail.value, ui.loginPassword.value);
    ui.loginPassword.value = "";
    start(user);
  } catch (err) {
    ui.loginPassword.value = "";
    renderMessage(ui.loginMessage, err.status === 401 ? err.message : `Não foi possível entrar: ${err.message}.`, "error");
  } finally {
    ui.loginSubmit.disabled = false;
  }
});

ui.logout.addEventListener("click", async () => {
  ui.logout.disabled = true;
  let message = "Você saiu.";
  let kind = "ok";
  try {
    await api.logout();
  } catch (err) {
    if (err.status !== 401) {
      message = `Você saiu desta tela, mas o servidor não confirmou o encerramento da sessão (${err.message}).`;
      kind = "error";
    }
  } finally {
    ui.logout.disabled = false;
  }
  signOut(message, kind);
});

// ---- Ciclo de vida ------------------------------------------------------

ui.refresh.addEventListener("click", reloadJobs);
ui.jobsRetry.addEventListener("click", reloadJobs);

document.addEventListener("visibilitychange", () => {
  const pollers = [healthPoller, state.user && listPoller, state.jobPoller].filter(Boolean);
  if (document.hidden) pollers.forEach((p) => p.stop());
  else pollers.forEach((p) => p.start());
});

renderDetail(ui.detail, null);
showPreview();
healthPoller.start();
api.me().then(start, (err) => signOut(err.status === 401 ? "" : `Não foi possível verificar a sessão: ${err.message}.`));
