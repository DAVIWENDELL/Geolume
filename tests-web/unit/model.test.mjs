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
  etapaArquivo,
  motivoProcessar,
  mensagemErro,
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

// ---- Tela do cliente: downloads e etapa do arquivo -------------------------------------

test("fileLinks traz título e descrição de cada documento; memorial sempre preliminar", () => {
  const links = fileLinks("t");
  assert.deepEqual(links.map((l) => l.titulo), ["Mapa PDF", "Memorial descritivo preliminar", "Resultado JSON"]);
  for (const l of links) assert.ok(l.descricao.length > 0, l.kind);
  const memorial = links.find((l) => l.kind === "memorial");
  assert.match(memorial.descricao, /preliminar/i);
  assert.doesNotMatch(`${memorial.titulo} ${memorial.descricao}`, /definitiv|oficial|legal/i);
});

test("etapaArquivo: sem arquivo convida a escolher um GeoJSON", () => {
  assert.deepEqual(etapaArquivo({ file: null }), { estado: "vazio", texto: "Escolha um arquivo GeoJSON para começar." });
});

test("etapaArquivo: arquivo recusado pelo navegador mostra o motivo", () => {
  const file = { name: "x.json", size: 10 };
  assert.deepEqual(etapaArquivo({ file, check: validateUpload(file) }), { estado: "invalido", texto: "Selecione um arquivo .geojson." });
});

test("etapaArquivo: lendo, válido e polígono com problema", () => {
  const file = { name: "a.geojson", size: 10 };
  const check = { ok: true };
  assert.deepEqual(etapaArquivo({ file, check, preview: null }), { estado: "lendo", texto: "Conferindo o polígono…" });
  assert.deepEqual(etapaArquivo({ file, check, preview: { ok: true } }), { estado: "valido", texto: "Polígono válido, pronto para processar." });
  assert.deepEqual(etapaArquivo({ file, check, preview: { ok: false, message: "O polígono se cruza." } }), {
    estado: "alerta",
    texto: "O polígono se cruza. O processamento provavelmente falhará; você ainda pode enviar para confirmar.",
  });
});

test("motivoProcessar explica por que o botão está desabilitado; vazio quando pode enviar", () => {
  assert.equal(motivoProcessar({ sending: true, arquivoOk: true, pranchaOk: true }), "Enviando o arquivo…");
  assert.equal(motivoProcessar({ sending: false, arquivoOk: false, pranchaOk: true }), "Escolha um arquivo GeoJSON válido para processar.");
  assert.equal(motivoProcessar({ sending: false, arquivoOk: true, pranchaOk: false }), "Corrija a personalização da prancha para processar.");
  assert.equal(motivoProcessar({ sending: false, arquivoOk: true, pranchaOk: true }), "");
});

test("mensagemErro tira o código interno do motivo e mantém o texto para quem lê", () => {
  assert.equal(mensagemErro("geometria_invalida: O polígono é inválido (ex.: autointerseção)."), "O polígono é inválido (ex.: autointerseção).");
  assert.equal(mensagemErro("O worker parou."), "O worker parou.");
  assert.equal(mensagemErro("Erro: algo"), "Erro: algo"); // só código minúsculo com sublinhado é removido
  assert.equal(mensagemErro(""), "");
  assert.equal(mensagemErro(undefined), "");
});

// Textos exatos que a API grava para poligono_muito_pequeno (worker/geolume_worker/processing.py).
const PEQUENO = "O polígono é pequeno demais: após arredondar as coordenadas para milímetros, ";

test("mensagemErro mantém inteira a mensagem de poligono_muito_pequeno com 1 vértice (singular)", () => {
  const texto = `${PEQUENO}resta 1 vértice; é necessário pelo menos 3.`;
  assert.equal(mensagemErro(`poligono_muito_pequeno: ${texto}`), texto);
});

test("mensagemErro mantém inteira a mensagem de poligono_muito_pequeno com 2 vértices (plural)", () => {
  const texto = `${PEQUENO}restam 2 vértices; são necessários pelo menos 3.`;
  assert.equal(mensagemErro(`poligono_muito_pequeno: ${texto}`), texto);
});

test("mensagemErro tira só o código do início: os dois-pontos do texto ficam", () => {
  assert.equal(mensagemErro("codigo_x: Parte 1: parte 2: parte 3."), "Parte 1: parte 2: parte 3.");
});

test("falha interna (artefato_invalido) chega pela API como a mensagem genérica e aparece igual", () => {
  // _erro_publico troca qualquer código fora da lista pública por este texto (coberto no pytest).
  const generica = "Falha no processamento do job.";
  assert.equal(mensagemErro(generica), generica);
  assert.doesNotMatch(mensagemErro(generica), /artefato_invalido|\.pdf|\.json|=/);
});
