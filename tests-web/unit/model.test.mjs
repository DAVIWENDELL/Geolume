import { test } from "node:test";
import assert from "node:assert/strict";
import {
  normalizeStatus,
  statusLabel,
  isTerminal,
  fileLinks,
  summarize,
  validateUpload,
  formatBytes,
  isCurrent,
} from "../../worker/web/js/model.js";

test("normalizeStatus mapeia estados da fila", () => {
  for (const raw of ["queued", "pending", "received", "retry", "PENDING"]) {
    assert.equal(normalizeStatus(raw), "queued", raw);
  }
});

test("normalizeStatus mapeia execucao, conclusao e falha", () => {
  assert.equal(normalizeStatus("started"), "started");
  assert.equal(normalizeStatus("completed"), "completed");
  assert.equal(normalizeStatus("SUCCESS"), "completed");
  for (const raw of ["failed", "failure", "revoked"]) {
    assert.equal(normalizeStatus(raw), "failed", raw);
  }
});

test("normalizeStatus desconhecido", () => {
  assert.equal(normalizeStatus("xyz"), "unknown");
  assert.equal(normalizeStatus(null), "unknown");
  assert.equal(normalizeStatus(undefined), "unknown");
  assert.equal(normalizeStatus(42), "unknown");
});

test("statusLabel usa rotulos em portugues", () => {
  assert.equal(statusLabel("queued"), "Na fila");
  assert.equal(statusLabel("started"), "Em execução");
  assert.equal(statusLabel("completed"), "Concluído");
  assert.equal(statusLabel("failed"), "Falhou");
  assert.equal(statusLabel("unknown"), "Desconhecido");
});

test("isTerminal so para concluido e falhou", () => {
  assert.equal(isTerminal("started"), false);
  assert.equal(isTerminal("queued"), false);
  assert.equal(isTerminal("unknown"), false);
  assert.equal(isTerminal("completed"), true);
  assert.equal(isTerminal("failed"), true);
});

test("fileLinks codifica task_id e segue a ordem mapa, memorial, resultado", () => {
  const links = fileLinks("a/b c");
  assert.equal(links[0].href, "/jobs/a%2Fb%20c/files/mapa");
  assert.deepEqual(
    links.map((l) => [l.kind, l.filename]),
    [
      ["mapa", "mapa.pdf"],
      ["memorial", "memorial.pdf"],
      ["resultado", "resultado.json"],
    ],
  );
});

test("summarize conta por estado normalizado", () => {
  assert.deepEqual(
    summarize([{ status: "completed" }, { status: "pending" }, { status: "failure" }]),
    { queued: 1, started: 0, completed: 1, failed: 1 },
  );
});

test("validateUpload exige arquivo .geojson", () => {
  assert.deepEqual(validateUpload(null), { ok: false, message: "Selecione um arquivo .geojson" });
  assert.deepEqual(validateUpload({ name: "x.json", size: 10 }), {
    ok: false,
    message: "Selecione um arquivo .geojson",
  });
  assert.deepEqual(validateUpload({ name: "X.GEOJSON", size: 10 }), { ok: true });
});

test("validateUpload rejeita vazio e acima de 10 MB", () => {
  assert.deepEqual(validateUpload({ name: "a.geojson", size: 0 }), {
    ok: false,
    message: "O arquivo está vazio",
  });
  assert.deepEqual(validateUpload({ name: "a.geojson", size: 10_485_761 }), {
    ok: false,
    message: "O arquivo excede 10 MB",
  });
  assert.deepEqual(validateUpload({ name: "a.geojson", size: 10_485_760 }), { ok: true });
});

test("formatBytes em pt-BR com uma casa", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(12595), "12,3 KB");
  assert.equal(formatBytes(1572864), "1,5 MB");
});

test("isCurrent compara a selecao com a resposta", () => {
  assert.equal(isCurrent("a", "b"), false);
  assert.equal(isCurrent("a", "a"), true);
  assert.equal(isCurrent(null, "a"), false);
});
