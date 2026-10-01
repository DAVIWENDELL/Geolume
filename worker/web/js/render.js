// Renderização: dados da API entram no DOM só por textContent e atributos.
import { formatMetrics } from "./geo.js";
import { camadaAtivavel, grupos, legendItems } from "./layers.js";
import { fileLinks, formatBytes, isTerminal, mensagemErro, normalizeStatus, statusLabel } from "./model.js";
import { pranchaTitle, responsavelLine } from "./prancha.js";

const STEPS = ["queued", "started", "end"];
const STEP_INDEX = { queued: 0, started: 1, completed: 2, failed: 2 };

function el(tag, props = {}, text) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) node.setAttribute(key, value);
  if (text != null) node.textContent = text;
  return node;
}

export function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function renderHealth(root, ok) {
  root.dataset.state = ok ? "ok" : "down";
  root.querySelector(".health-text").textContent = ok ? "API online" : "API indisponível";
}

export function renderUploadFile(root, file) {
  if (file) {
    root.textContent = `${file.name} (${formatBytes(file.size)})`;
    root.dataset.selected = "";
  } else {
    root.textContent = "Nenhum arquivo selecionado";
    delete root.dataset.selected;
  }
}

export function renderSession(root, user) {
  root.hidden = !user;
  const name = root.querySelector('[data-testid="session-user"]');
  name.textContent = user ? user.email : "";
  if (user) name.dataset.role = user.role;
  else delete name.dataset.role;
}

export function renderMessage(root, text, kind) {
  root.textContent = text ?? "";
  if (kind) root.dataset.kind = kind;
  else delete root.dataset.kind;
}

function renderScalebar(bar, status) {
  bar.dataset.status = status;
  const reached = STEP_INDEX[status] ?? -1;
  bar.querySelectorAll("li").forEach((li, i) => {
    if (i < reached || (i === reached && isTerminal(status))) li.dataset.state = "done";
    else if (i === reached) li.dataset.state = "current";
    else delete li.dataset.state;
  });
  bar.querySelector('[data-step="end"] span').textContent = status === "failed" ? "Falhou" : "Concluído";
}

/** Cartão de download: título e descrição para quem lê; o link (o cartão inteiro, por CSS) tem o nome do arquivo. */
function docCard({ kind, filename, href, titulo, descricao }) {
  const card = el("div", { class: "doc", "data-kind": kind });
  const tituloId = `doc-${kind}-titulo`;
  const descId = `doc-${kind}-desc`;
  card.append(
    el("span", { class: "doc-titulo", id: tituloId }, titulo),
    el("span", { class: "doc-desc", id: descId }, descricao),
    el("a", { class: "doc-link", href, download: filename, "data-kind": kind, "aria-describedby": `${tituloId} ${descId}` }, filename),
  );
  return card;
}

/**
 * job: { task_id, job_id?, input_filename?, status (normalizado), erro? } | null
 * view: { loading, warning }
 */
export function renderDetail(root, job, view = {}) {
  const q = (sel) => root.querySelector(sel);
  q("[data-detail-empty]").hidden = Boolean(job);
  q("[data-detail-body]").hidden = !job;
  q("[data-activity]").hidden = !view.loading;
  if (!job) {
    // Nada do job anterior fica no DOM, nem escondido (troca de usuário na mesma aba).
    for (const sel of ["[data-detail-file]", '[data-testid="detail-job-id"]', '[data-testid="detail-task-id"]',
      '[data-testid="detail-status"]', "[data-status-announcer]", "[data-poll-warning]", '[data-testid="detail-error"]',
      "[data-docs-hint]"]) {
      q(sel).textContent = "";
    }
    q('[data-testid="detail-links"]').replaceChildren();
    return;
  }

  q("[data-detail-file]").textContent = job.input_filename ?? "";
  q('[data-testid="detail-job-id"]').textContent = job.job_id ?? "—";
  q('[data-testid="detail-task-id"]').textContent = job.task_id;

  const status = q('[data-testid="detail-status"]');
  status.textContent = statusLabel(job.status);
  status.dataset.status = job.status;
  // Única região viva do painel: só muda quando o estado muda, não a cada consulta.
  const announcer = q("[data-status-announcer]");
  const announcement = `Estado: ${statusLabel(job.status)}`;
  if (announcer.textContent !== announcement) announcer.textContent = announcement;
  renderScalebar(q("[data-scalebar]"), job.status);

  const warning = q("[data-poll-warning]");
  warning.hidden = !view.warning;
  warning.textContent = view.warning
    ? `Não foi possível atualizar o estado (${view.warning}). Mostrando o último estado conhecido.`
    : "";

  const error = q('[data-testid="detail-error"]');
  const showError = job.status === "failed";
  error.hidden = !showError;
  error.textContent = showError ? mensagemErro(job.erro) || "O worker não informou o motivo da falha." : "";

  const links = q('[data-testid="detail-links"]');
  const completed = job.status === "completed";
  links.replaceChildren(...(completed ? fileLinks(job.task_id).map(docCard) : []));
  q("[data-docs-hint]").textContent = completed
    ? ""
    : job.status === "failed"
      ? "Nenhum documento gerado."
      : "Os documentos aparecem aqui quando o processamento terminar.";
}

/**
 * Painel de pré-visualização (o mapa em si fica com map.js).
 * view: { message, kind?, metrics?: { areaHa, perimetroM } | null }
 */
export function renderPreview(root, view) {
  const message = root.querySelector('[data-testid="preview-message"]');
  // Região viva: só reescreve quando o texto muda, para não repetir o anúncio.
  if (message.textContent !== view.message || message.dataset.kind !== view.kind) {
    renderMessage(message, view.message, view.kind);
  }

  const box = root.querySelector('[data-testid="preview-metrics"]');
  box.hidden = !view.metrics;
  const { area, perimetro } = view.metrics ? formatMetrics(view.metrics) : { area: "", perimetro: "" };
  root.querySelector('[data-testid="metric-area"]').textContent = area;
  root.querySelector('[data-testid="metric-perimetro"]').textContent = perimetro;
}

/** "#RRGGBB" validado → rgba com o alfa do preenchimento. */
function rgba(hex, alpha) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Cabeçalho da prancha acima do mapa, como sai no mapa.pdf.
 * view: null (oculto) | { projeto, responsavel, legenda, logo: URL | null, colors: { contorno, preenchimento }, opacity }
 */
export function renderPranchaHead(root, view) {
  root.hidden = !view;
  root.querySelector('[data-testid="prancha-title"]').textContent = view ? pranchaTitle(view.projeto) : "";
  const responsavel = root.querySelector('[data-testid="prancha-responsavel"]');
  responsavel.textContent = view ? responsavelLine(view.responsavel) : "";
  responsavel.hidden = !responsavel.textContent;

  const logo = root.querySelector('[data-testid="prancha-logo"]');
  if (!view?.logo) {
    logo.hidden = true;
    logo.removeAttribute("src");
  } else if (logo.getAttribute("src") !== view.logo) {
    logo.hidden = false; // o handler de erro da imagem volta a esconder
    logo.src = view.logo;
  }

  const layout = view?.legenda || "nenhuma";
  root.querySelector('[data-testid="prancha-miniatura"]').dataset.layout = layout;
  const legenda = root.querySelector('[data-testid="prancha-legenda"]');
  legenda.hidden = layout === "nenhuma";
  legenda.dataset.posicao = layout;
  const swatch = root.querySelector('[data-testid="prancha-swatch"]');
  swatch.style.borderColor = view ? view.colors.contorno : "";
  swatch.style.backgroundColor = view ? rgba(view.colors.preenchimento, view.opacity) : "";
}

/** Amostra "Polígono do imóvel" da legenda do mapa com o estilo que o mapa aplicou (polygonStyle). */
export function renderAmostraPoligono(root, { color, fillColor, fillOpacity }) {
  root.style.setProperty("--poligono-contorno", color);
  root.style.setProperty("--poligono-preenchimento", rgba(fillColor, fillOpacity));
}

/** Miniatura da prancha: blocos na posição de mapa.pdf; a legenda segue data-layout. */
function miniatura(layout) {
  const mini = el("span", { class: "prancha-mini", "data-layout": layout, "aria-hidden": "true" });
  for (const parte of ["mini-mapa", "mini-coluna", "mini-tabela", "mini-legenda"]) mini.append(el("span", { class: parte }));
  return mini;
}

/** Cartões de layout, uma vez: um rádio por layout, nome como rótulo e descrição acessível. */
export function renderLayoutCards(root, layouts, selecionado) {
  for (const { valor, nome, descricao } of layouts) {
    const card = el("label", { class: "prancha-layout-card" });
    const radio = el("input", {
      type: "radio",
      name: "prancha-legenda",
      value: valor,
      "data-testid": `prancha-layout-${valor}`,
      "aria-labelledby": `prancha-layout-nome-${valor}`,
      "aria-describedby": `prancha-layout-desc-${valor}`,
    });
    radio.checked = valor === selecionado;
    const texto = el("span", { class: "prancha-layout-texto" });
    texto.append(
      el("span", { class: "prancha-layout-nome", id: `prancha-layout-nome-${valor}` }, nome),
      el("span", { class: "prancha-layout-desc", id: `prancha-layout-desc-${valor}` }, descricao),
    );
    card.append(radio, miniatura(valor), texto);
    root.append(card);
  }
}

const TILES_FAILED ="Mapa-base indisponível no momento; o polígono continua visível.";

const CATALOGO_ERRO = "Camadas indisponíveis no momento. O polígono continua visível, sem mapa-base.";
const GRUPOS = [
  ["disponiveis", "disponiveis", "Disponíveis"],
  ["dependemFonte", "fonte-oficial", "Dependem de fonte oficial"],
  ["planejadas", "planejadas", "Planejadas"],
];

/** Um item do painel: mapa-base é rádio (escolha única), sobreposição é caixa de seleção.
 *  O que não está disponível fica desabilitado, com o aviso do catálogo ligado ao controle. */
function camadaItem(camada) {
  const ativavel = camadaAtivavel(camada);
  const item = el("div", {
    class: "camada-item",
    "data-testid": "camada-item",
    "data-camada-id": camada.id,
    "aria-disabled": String(!ativavel),
  });
  const input = camada.grupo === "base"
    ? el("input", { type: "radio", name: "map-base", value: camada.id })
    : el("input", { type: "checkbox", value: camada.id });
  if (camada.id === "poligono") input.dataset.testid = "polygon-toggle";
  input.disabled = !ativavel;
  const label = el("label", { class: "map-choice" });
  label.append(input, ` ${camada.nome}`);
  item.append(label);
  if (typeof camada.aviso === "string" && camada.aviso) {
    const id = `camada-aviso-${camada.id}`;
    input.setAttribute("aria-describedby", id);
    item.append(el("span", { class: "camada-aviso", id }, camada.aviso));
  }
  return item;
}

/**
 * Painel "Camadas do mapa", a cada catálogo novo (não a cada mudança do mapa, para não perder o foco).
 * catalogo: o de parseCatalogo; erro: GET /camadas falhou e o mapa está sem mapa-base.
 */
export function renderCamadasPanel(root, catalogo, { erro = false } = {}) {
  const aviso = root.querySelector('[data-testid="camadas-erro"]');
  aviso.hidden = !erro;
  aviso.textContent = erro ? CATALOGO_ERRO : "";
  const porGrupo = grupos(catalogo);
  const montarGrupo = ([chave, testid, titulo]) => {
    const grupo = el("div", {
      class: "camadas-grupo",
      role: "group",
      "data-testid": `camadas-grupo-${testid}`,
      "aria-labelledby": `camadas-grupo-${testid}-titulo`,
    });
    const itens = el("div", { class: "camadas-itens" });
    itens.append(...porGrupo[chave].map(camadaItem));
    grupo.append(el("p", { class: "camadas-grupo-titulo", id: `camadas-grupo-${testid}-titulo` }, titulo), itens);
    return grupo;
  };
  const comItens = ([chave]) => porGrupo[chave].length;
  // Disponíveis à vista; o que ainda não existe fica recolhido em "Outras camadas", sem perder o aviso.
  root.querySelector("[data-camadas-grupos]").replaceChildren(...GRUPOS.slice(0, 1).filter(comItens).map(montarGrupo));
  const outros = GRUPOS.slice(1).filter(comItens);
  root.querySelector("[data-camadas-mais]").replaceChildren(...outros.map(montarGrupo));
  root.querySelector('[data-testid="camadas-mais"]').hidden = !outros.length;
}

/**
 * Controles e legenda do mapa a partir do estado do mapView.
 * mapState: { visible, view: { base, polygon }, tilesFailed }; catalogo: o mesmo do mapView.
 */
export function renderMapTools(root, { visible, view, tilesFailed }, catalogo) {
  root.querySelector('[data-testid="map-tools"]').hidden = !visible;
  root.querySelector('[data-testid="map-info"]').hidden = !visible;

  for (const radio of root.querySelectorAll('input[name="map-base"]')) radio.checked = radio.value === view.base;
  const toggle = root.querySelector('[data-testid="polygon-toggle"]');
  if (toggle) toggle.checked = view.polygon;

  root.querySelector('[data-testid="map-legend"]').replaceChildren(
    ...legendItems(view, catalogo, { tilesFailed }).map((item) => el("li", { "data-kind": item.kind }, item.text)),
  );
  // Região viva: só reescreve quando muda, para não repetir o anúncio a cada tile.
  const status = root.querySelector('[data-testid="map-tiles-status"]');
  const base = catalogo.camadas.find((camada) => camada.id === view.base && camada.grupo === "base");
  const text = tilesFailed && camadaAtivavel(base) && base.tipo === "raster" ? TILES_FAILED : "";
  if (status.textContent !== text) status.textContent = text;
}

const ESTILOS_INDISPONIVEIS = "Estilos indisponíveis no momento; o polígono usa o estilo Padrão.";
const mm = (valor) => (Number.isFinite(valor) && valor > 0 ? `Contorno ${String(valor).replace(".", ",")} mm` : "");

/** Cartões de estilo do polígono (estilos do catálogo): rádio, amostra com as cores e a espessura, nome. */
export function renderEstiloCards(root, estilos, selecionado) {
  const cards = root.querySelector("[data-estilos-cards]");
  if (!estilos.length) {
    cards.replaceChildren(el("p", { class: "hint" }, ESTILOS_INDISPONIVEIS));
    return;
  }
  cards.replaceChildren(
    ...estilos.map((estilo) => {
      const card = el("label", { class: "estilo-card", "data-testid": "estilo-card", "data-estilo": estilo.id });
      const radio = el("input", {
        type: "radio",
        name: "prancha-estilo",
        value: estilo.id,
        "aria-labelledby": `prancha-estilo-nome-${estilo.id}`,
      });
      radio.checked = estilo.id === selecionado;
      // Cores e espessura já conferidas por parseCatalogo (#RRGGBB e inteiro positivo).
      const amostra = el("span", { class: "estilo-amostra", "aria-hidden": "true" });
      amostra.style.borderColor = estilo.contorno;
      amostra.style.borderWidth = `${estilo.espessura_px}px`;
      amostra.style.backgroundColor = estilo.preenchimento;
      const texto = el("span", { class: "estilo-texto" });
      texto.append(el("span", { class: "estilo-nome", id: `prancha-estilo-nome-${estilo.id}` }, estilo.nome));
      const descricao = mm(estilo.espessura_mm);
      if (descricao) texto.append(el("span", { class: "estilo-desc" }, descricao));
      card.append(radio, amostra, texto);
      return card;
    }),
  );
}

/** Etapa 2 (arquivo e validação): etapa = etapaArquivo(...) de model.js. */
export function renderArquivo(root, etapa) {
  root.dataset.estado = etapa.estado;
  root.querySelector('[data-testid="arquivo-status"]').textContent = etapa.texto;
}

/** Etapa 4: resumo das escolhas (resumoEnvio de prancha.js), por textContent. */
export function renderResumo(root, itens) {
  root.replaceChildren(...itens.map(({ rotulo, valor }) => {
    const linha = el("div", { class: "resumo-item" });
    linha.append(el("dt", {}, rotulo), el("dd", {}, valor));
    return linha;
  }));
}

export function renderSummary(root, counts) {
  for (const [status, count] of Object.entries(counts)) {
    const target = root.querySelector(`[data-status="${status}"] [data-count]`);
    if (target) target.textContent = String(count);
  }
}

function jobRow(job, selectedTaskId, onSelect) {
  const status = normalizeStatus(job.status);
  const row = el("tr", {
    class: "job-row",
    "data-testid": "jobs-row",
    "data-task-id": job.task_id ?? "",
    tabindex: "0",
    "aria-current": String(job.task_id === selectedTaskId),
  });

  const docs = el("td", { "data-label": "Documentos" });
  if (status === "completed" && job.task_id) {
    const wrap = el("div", { class: "row-links" });
    for (const link of fileLinks(job.task_id)) {
      wrap.append(el("a", { href: link.href, download: link.filename, "data-kind": link.kind }, link.filename));
    }
    docs.append(wrap);
  } else {
    docs.append(el("span", { class: "muted" }, "—"));
  }

  const statusCell = el("td", { "data-label": "Estado" });
  statusCell.append(el("span", { class: "status", "data-status": status }, statusLabel(status)));

  row.append(
    el("td", { "data-label": "Arquivo" }, job.input_filename ?? "—"),
    statusCell,
    el("td", { "data-label": "Criado em" }, formatDate(job.created_at)),
    docs,
  );

  row.addEventListener("click", (event) => {
    if (event.target.closest("a")) return; // download não troca a seleção
    onSelect(job);
  });
  row.addEventListener("keydown", (event) => {
    if (event.target !== row || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    onSelect(job);
  });
  return row;
}

/** Reconstrói as linhas preservando o foco do teclado na mesma linha. */
export function renderJobs(tbody, jobs, selectedTaskId, onSelect) {
  const active = document.activeElement;
  const focusedId = active?.dataset?.testid === "jobs-row" ? active.dataset.taskId : undefined;
  tbody.replaceChildren(...jobs.map((job) => jobRow(job, selectedTaskId, onSelect)));
  if (focusedId) {
    const again = [...tbody.children].find((row) => row.dataset.taskId === focusedId);
    again?.focus();
  }
}

export function markSelectedRow(tbody, selectedTaskId) {
  for (const row of tbody.children) {
    if (row.dataset.taskId !== undefined) row.setAttribute("aria-current", String(row.dataset.taskId === selectedTaskId));
  }
}
