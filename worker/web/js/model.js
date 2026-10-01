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

const FILES = [
  ["mapa", "mapa.pdf"],
  ["memorial", "memorial.pdf"],
  ["resultado", "resultado.json"],
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
  return FILES.map(([kind, filename]) => ({ kind, filename, href: base + kind }));
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
