// Lógica pura da tela de operação: sem DOM, sem rede.

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

const STATUS_MAP = {
  queued: "queued",
  pending: "queued",
  received: "queued",
  retry: "queued",
  started: "started",
  completed: "completed",
  success: "completed",
  failed: "failed",
  failure: "failed",
  revoked: "failed",
};

const LABELS = {
  queued: "Na fila",
  started: "Em execução",
  completed: "Concluído",
  failed: "Falhou",
  unknown: "Desconhecido",
};

// O memorial é sempre preliminar: nada na tela o apresenta como documento definitivo.
const FILES = [
  ["mapa", "mapa.pdf", "Mapa PDF", "Prancha A4 com o polígono, a tabela de vértices e a legenda."],
  ["memorial", "memorial.pdf", "Memorial descritivo preliminar", "Descrição perimetral preliminar, para conferência técnica."],
  ["resultado", "resultado.json", "Resultado JSON", "Área, perímetro e vértices calculados, para outros sistemas."],
];

export function normalizeStatus(raw) {
  if (typeof raw !== "string") return "unknown";
  return Object.hasOwn(STATUS_MAP, raw.toLowerCase()) ? STATUS_MAP[raw.toLowerCase()] : "unknown";
}

export function statusLabel(status) {
  return LABELS[status] ?? LABELS.unknown;
}

export function isTerminal(status) {
  return status === "completed" || status === "failed";
}

export function fileLinks(taskId) {
  const base = "/jobs/" + encodeURIComponent(taskId) + "/files/";
  return FILES.map(([kind, filename, titulo, descricao]) => ({ kind, filename, titulo, descricao, href: base + kind }));
}

export function summarize(jobs) {
  const counts = { queued: 0, started: 0, completed: 0, failed: 0 };
  for (const job of jobs) {
    const status = normalizeStatus(job?.status);
    if (status in counts) counts[status] += 1;
  }
  return counts;
}

export function validateUpload(file) {
  if (!file || !/\.geojson$/i.test(file.name ?? "")) {
    return { ok: false, message: "Selecione um arquivo .geojson" };
  }
  if (!(file.size > 0)) return { ok: false, message: "O arquivo está vazio" };
  if (file.size > MAX_UPLOAD_BYTES) return { ok: false, message: "O arquivo excede 10 MB" };
  return { ok: true };
}

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  const [value, unit] = n < 1024 * 1024 ? [n / 1024, "KB"] : [n / (1024 * 1024), "MB"];
  return `${value.toFixed(1).replace(".", ",")} ${unit}`;
}

export function isCurrent(selectedTaskId, responseTaskId) {
  return selectedTaskId != null && selectedTaskId === responseTaskId;
}

/** Etapa "arquivo" do envio: o que dizer sobre o arquivo escolhido e a leitura do polígono no navegador.
 *  preview: null enquanto lê; { ok } ou { ok: false, message } quando terminou. */
export function etapaArquivo({ file, check, preview } = {}) {
  if (!file) return { estado: "vazio", texto: "Escolha um arquivo GeoJSON para começar." };
  if (!check?.ok) return { estado: "invalido", texto: `${check?.message ?? "Arquivo inválido"}.` };
  if (!preview) return { estado: "lendo", texto: "Conferindo o polígono…" };
  if (preview.ok) return { estado: "valido", texto: "Polígono válido, pronto para processar." };
  return { estado: "alerta", texto: `${preview.message} O processamento provavelmente falhará; você ainda pode enviar para confirmar.` };
}

/** Motivo de falha para quem lê: sem o código interno do início ("geometria_invalida: ..."). */
export function mensagemErro(erro) {
  return typeof erro === "string" ? erro.replace(/^[a-z_]+: /, "") : "";
}

/** Por que "Processar" está desabilitado, em uma frase; vazio quando pode enviar. */
export function motivoProcessar({ sending, arquivoOk, pranchaOk }) {
  if (sending) return "Enviando o arquivo…";
  if (!arquivoOk) return "Escolha um arquivo GeoJSON válido para processar.";
  if (!pranchaOk) return "Corrija a personalização da prancha para processar.";
  return "";
}
