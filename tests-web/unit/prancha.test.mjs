import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  LAYOUTS,
  LOGO_MAX_BYTES,
  PRANCHA_PADRAO,
  alfa8,
  alfaFromPercent,
  coresDoEstilo,
  pranchaFields,
  pranchaTitle,
  polygonColors,
  polygonStyle,
  responsavelLine,
  validateAlfa,
  validateLogo,
  validatePrancha,
  resumoEnvio,
} from "../../worker/web/js/prancha.js";

const campos = (extra = {}) => ({ ...PRANCHA_PADRAO, ...extra });
const arquivo = (nome, tipo, tamanho = 10) => new File([new Uint8Array(tamanho)], nome, { type: tipo });

test("padrão: sem texto, cores atuais do PDF e sem legenda", () => {
  assert.deepEqual(PRANCHA_PADRAO, {
    projeto: "", responsavel: "", cor_contorno: "#C80000", cor_preenchimento: "#FFC800", legenda: "nenhuma",
    estilo: "padrao", alfa_preenchimento: 0.35,
  });
  assert.equal(LOGO_MAX_BYTES, 2 * 1024 * 1024);
});

test("título: com projeto vira '{projeto} — Mapa de Localização'; vazio mantém o atual", () => {
  assert.equal(pranchaTitle(""), "GeoLume — Mapa de Localização");
  assert.equal(pranchaTitle("   "), "GeoLume — Mapa de Localização");
  assert.equal(pranchaTitle(null), "GeoLume — Mapa de Localização");
  assert.equal(pranchaTitle("  Loteamento Sol "), "Loteamento Sol — Mapa de Localização");
});

test("responsável: linha só quando preenchido", () => {
  assert.equal(responsavelLine("Eng. Ana"), "Responsável técnico: Eng. Ana");
  assert.equal(responsavelLine(" "), "");
  assert.equal(responsavelLine(null), "");
});

test("validatePrancha normaliza: apara textos e cores em maiúsculas", () => {
  const r = validatePrancha(campos({ projeto: "  Sol ", responsavel: "", cor_contorno: "#12abef", legenda: "lateral" }));
  assert.equal(r.ok, true);
  assert.deepEqual(r.values, {
    projeto: "Sol", responsavel: "", cor_contorno: "#12ABEF", cor_preenchimento: "#FFC800", legenda: "lateral",
    estilo: "padrao", alfa_preenchimento: 0.35,
  });
});

test("validatePrancha aceita 100 caracteres e recusa 101", () => {
  assert.equal(validatePrancha(campos({ projeto: "a".repeat(100) })).ok, true);
  assert.deepEqual(validatePrancha(campos({ responsavel: "a".repeat(101) })), {
    ok: false, field: "responsavel", message: "Responsável técnico deve ter no máximo 100 caracteres.",
  });
});

for (const ruim of ["a\nb", "a\tb", "a\u0000b", "a‮b", "a⁦b"]) {
  test(`validatePrancha recusa caractere de controle ${JSON.stringify(ruim)}`, () => {
    const r = validatePrancha(campos({ projeto: ruim }));
    assert.equal(r.ok, false);
    assert.equal(r.field, "projeto");
    assert.match(r.message, /^Nome do projeto não pode ter/);
  });
}

test("validatePrancha deixa texto com cara de HTML ou expressão como está (vira texto literal)", () => {
  const texto = `<img src=x onerror=alert(1)> [% 1+1 %] env('PATH')`;
  assert.equal(validatePrancha(campos({ projeto: texto })).values.projeto, texto);
});

for (const cor of ["#12345", "red", "#GGGGGG", "#1234567", "12345F"]) {
  test(`validatePrancha recusa cor ${cor}`, () => {
    assert.deepEqual(validatePrancha(campos({ cor_preenchimento: cor })), {
      ok: false, field: "cor_preenchimento", message: "Cor deve estar no formato #RRGGBB.",
    });
  });
}

test("LAYOUTS: os três layouts, na ordem e com os valores de prancha.py", () => {
  assert.deepEqual(LAYOUTS.map((l) => l.valor), ["nenhuma", "lateral", "inferior"]);
  assert.deepEqual(LAYOUTS.map((l) => l.nome), ["Padrão", "Legenda lateral", "Legenda inferior"]);
  assert.equal(LAYOUTS[0].descricao, "Mapa, dados do imóvel, norte, escala e tabela de vértices.");
  assert.equal(LAYOUTS[1].descricao, "Legenda na coluna direita, abaixo da escala.");
  assert.equal(LAYOUTS[2].descricao, "Legenda abaixo do mapa, ao lado da tabela.");
  assert.ok(Object.isFrozen(LAYOUTS) && LAYOUTS.every(Object.isFrozen));
});

test("validatePrancha aceita os três layouts; vazio vira o padrão", () => {
  for (const legenda of ["nenhuma", "lateral", "inferior"]) {
    assert.equal(validatePrancha(campos({ legenda })).values.legenda, legenda);
  }
  assert.equal(validatePrancha(campos({ legenda: "" })).values.legenda, "nenhuma");
});

test("validatePrancha recusa layout desconhecido com mensagem fixa", () => {
  for (const legenda of ["topo", " lateral", "LATERAL", "superior"]) {
    assert.deepEqual(validatePrancha(campos({ legenda })), {
      ok: false, field: "legenda", message: "Layout da prancha inválido.",
    });
  }
});

test("validateLogo: sem logo é válido; PNG e JPEG até 2 MB passam", () => {
  assert.deepEqual(validateLogo(null), { ok: true });
  assert.deepEqual(validateLogo(arquivo("a.png", "image/png")), { ok: true });
  assert.deepEqual(validateLogo(arquivo("a.jpg", "image/jpeg", LOGO_MAX_BYTES)), { ok: true });
});

test("validateLogo recusa maior que 2 MB, vazio e tipos fora de PNG/JPEG", () => {
  assert.deepEqual(validateLogo(arquivo("a.png", "image/png", LOGO_MAX_BYTES + 1)), { ok: false, message: "Logo maior que 2 MB." });
  assert.deepEqual(validateLogo(arquivo("a.png", "image/png", 0)), { ok: false, message: "A logo está vazia." });
  for (const [nome, tipo] of [["a.svg", "image/svg+xml"], ["a.gif", "image/gif"], ["a.png", ""], ["a.webp", "image/webp"]]) {
    assert.deepEqual(validateLogo(arquivo(nome, tipo)), { ok: false, message: "Logo deve ser uma imagem PNG ou JPEG." }, nome);
  }
});

test("pranchaFields manda só o que difere do padrão (sem personalização, nada)", () => {
  assert.deepEqual(pranchaFields(PRANCHA_PADRAO), []);
  assert.deepEqual(pranchaFields(campos({ cor_contorno: "#c80000" })), []); // input color devolve minúsculas
  assert.deepEqual(pranchaFields(campos({ projeto: "Sol", cor_preenchimento: "#00ff00", legenda: "lateral" })), [
    ["projeto", "Sol"],
    ["cor_preenchimento", "#00FF00"],
    ["legenda", "lateral"],
  ]);
  assert.deepEqual(pranchaFields(campos({ legenda: "inferior" })), [["legenda", "inferior"]]);
  assert.deepEqual(pranchaFields(campos({ legenda: "nenhuma" })), []);
});

test("polygonColors: prancha do job ou padrão; cor inválida cai no padrão", () => {
  assert.deepEqual(polygonColors(null), { contorno: "#C80000", preenchimento: "#FFC800" });
  assert.deepEqual(polygonColors({ cor_contorno: "#112233", cor_preenchimento: "#445566" }), {
    contorno: "#112233", preenchimento: "#445566",
  });
  assert.deepEqual(polygonColors({ cor_contorno: "red", cor_preenchimento: "url(x)" }), {
    contorno: "#C80000", preenchimento: "#FFC800",
  });
});

// ---- Estilo e alfa do preenchimento: mesma tabela do Python --------------------------

const fixture = (nome) =>
  JSON.parse(readFileSync(new URL(`../../worker/tests/fixtures/${nome}`, import.meta.url), "utf-8"));
const CASOS = fixture("alfa_casos.json"); // também lida por worker/tests/test_camadas.py
const CATALOGO = fixture("catalogo_publico.json"); // igual a catalogo_publico(), conferido no Python
const ESTILOS = CATALOGO.estilos;
const ESPECIAIS = { nan: NaN, inf: Infinity, "-inf": -Infinity };
const valorDoCaso = (caso) =>
  caso && typeof caso === "object" && !Array.isArray(caso) && Object.keys(caso).join() === "especial"
    ? ESPECIAIS[caso.especial]
    : caso;

test("tabela do alfa: mensagem e padrão iguais aos do Python", () => {
  assert.equal(CASOS.mensagem, "Opacidade do preenchimento deve estar entre 0 e 1.");
  assert.equal(CASOS.padrao, 0.35);
  assert.equal(PRANCHA_PADRAO.alfa_preenchimento, CASOS.padrao);
});

for (const [valor, esperado] of CASOS.aceitos) {
  test(`validateAlfa aceita ${JSON.stringify(valor)}`, () => {
    assert.deepEqual(validateAlfa(valor), { ok: true, value: esperado });
  });
}

for (const valor of [...CASOS.ausentes, undefined]) {
  test(`validateAlfa: ausente ${JSON.stringify(valor)} vira o padrão`, () => {
    assert.deepEqual(validateAlfa(valor), { ok: true, value: 0.35 });
  });
}

for (const caso of CASOS.recusados) {
  test(`validateAlfa recusa ${JSON.stringify(caso)} com a mensagem fixa`, () => {
    assert.deepEqual(validateAlfa(valorDoCaso(caso)), { ok: false, message: CASOS.mensagem });
  });
}

test("alfa8: toda a faixa do controle (0–100 %) igual à do PDF, metade para cima", () => {
  for (const [percentual, esperado] of CASOS.alfa8) {
    assert.equal(alfa8(percentual / 100), esperado, `${percentual}%`);
  }
  assert.equal(alfa8(0.35), 89);
  assert.equal(alfa8(0.3), 77); // 76,5: o round() antigo do Python daria 76
});

test("alfaFromPercent: inteiro 0–100 vira 0–1; o resto é recusado, nunca corrigido", () => {
  assert.deepEqual(alfaFromPercent("0"), { ok: true, value: 0 });
  assert.deepEqual(alfaFromPercent("35"), { ok: true, value: 0.35 });
  assert.deepEqual(alfaFromPercent(100), { ok: true, value: 1 });
  for (const ruim of ["-1", "101", "150", "50.5", "abc", "", " ", null, undefined, NaN, true, "1e2"]) {
    assert.deepEqual(alfaFromPercent(ruim), { ok: false, message: CASOS.mensagem }, JSON.stringify(ruim));
  }
});

test("estilos: os três do catálogo, com as cores e espessuras da spec", () => {
  assert.deepEqual(
    ESTILOS.map((e) => [e.id, e.contorno, e.preenchimento, e.espessura_px]),
    [["padrao", "#C80000", "#FFC800", 3], ["tecnico", "#1F2937", "#9CA3AF", 2], ["pb", "#000000", "#FFFFFF", 2]],
  );
});

test("coresDoEstilo: escolher um estilo preenche as cores dele", () => {
  assert.deepEqual(coresDoEstilo("tecnico", ESTILOS), { cor_contorno: "#1F2937", cor_preenchimento: "#9CA3AF" });
  assert.deepEqual(coresDoEstilo("pb", ESTILOS), { cor_contorno: "#000000", cor_preenchimento: "#FFFFFF" });
  assert.deepEqual(coresDoEstilo("padrao", ESTILOS), { cor_contorno: "#C80000", cor_preenchimento: "#FFC800" });
  for (const ruim of ["urbano", "__proto__", "constructor", "", null]) assert.equal(coresDoEstilo(ruim, ESTILOS), null);
  assert.equal(coresDoEstilo("tecnico", []), null);
});

for (const estilo of ["padrao", "tecnico", "pb"]) {
  test(`polygonStyle do estilo ${estilo}: cores, espessura, alfa8/255 e contorno opaco`, () => {
    const e = ESTILOS.find((x) => x.id === estilo);
    const prancha = { estilo, ...coresDoEstilo(estilo, ESTILOS), alfa_preenchimento: 0.35 };
    assert.deepEqual(polygonStyle(prancha, ESTILOS), {
      color: e.contorno, fillColor: e.preenchimento, fillOpacity: 89 / 255, weight: e.espessura_px, opacity: 1,
    });
  });
}

test("polygonStyle: editar a cor não muda a espessura do estilo", () => {
  const estilo = polygonStyle({ estilo: "tecnico", cor_contorno: "#112233", cor_preenchimento: "#9CA3AF" }, ESTILOS);
  assert.equal(estilo.color, "#112233");
  assert.equal(estilo.weight, 2);
});

test("polygonStyle: alfa 0 esconde só o preenchimento; o contorno continua opaco", () => {
  for (const [alfa, fill] of [[0, 0], [0.35, 89 / 255], [1, 1]]) {
    const estilo = polygonStyle({ alfa_preenchimento: alfa }, ESTILOS);
    assert.equal(estilo.fillOpacity, fill);
    assert.equal(estilo.opacity, 1);
    assert.ok(estilo.weight > 0);
  }
});

test("polygonStyle: job antigo (sem prancha ou sem as chaves novas) usa padrao e 0.35", () => {
  const padrao = { color: "#C80000", fillColor: "#FFC800", fillOpacity: 89 / 255, weight: 3, opacity: 1 };
  assert.deepEqual(polygonStyle(null, ESTILOS), padrao);
  assert.deepEqual(polygonStyle(undefined, ESTILOS), padrao);
  const antiga = { cor_contorno: "#C80000", cor_preenchimento: "#FFC800", legenda: "lateral", logo: null };
  assert.deepEqual(polygonStyle(antiga, ESTILOS), padrao);
});

test("polygonStyle: valores adulterados caem no padrão, sem quebrar o mapa", () => {
  const estilo = polygonStyle({ estilo: "urbano", alfa_preenchimento: "2", cor_contorno: "red" }, ESTILOS);
  assert.deepEqual(estilo, { color: "#C80000", fillColor: "#FFC800", fillOpacity: 89 / 255, weight: 3, opacity: 1 });
});

test("polygonStyle sem catálogo: estilo padrão com espessura 3 px", () => {
  assert.equal(polygonStyle({ estilo: "tecnico" }, []).weight, 3);
  assert.equal(polygonStyle(null).weight, 3);
});

test("validatePrancha aceita estilo do catálogo e recusa o resto com mensagem fixa", () => {
  for (const estilo of ["padrao", "tecnico", "pb"]) {
    assert.equal(validatePrancha(campos({ estilo }), ESTILOS).values.estilo, estilo);
  }
  assert.equal(validatePrancha(campos({ estilo: "" }), ESTILOS).values.estilo, "padrao");
  for (const estilo of ["urbano", "Tecnico", " pb", "__proto__", "<script>"]) {
    assert.deepEqual(validatePrancha(campos({ estilo }), ESTILOS), {
      ok: false, field: "estilo", message: "Estilo do polígono inválido.",
    });
  }
});

test("validatePrancha sem catálogo só aceita o estilo padrão", () => {
  assert.equal(validatePrancha(campos({ estilo: "padrao" })).ok, true);
  assert.equal(validatePrancha(campos({ estilo: "tecnico" })).ok, false);
});

test("validatePrancha recusa alfa inválido com a mensagem do Python", () => {
  for (const alfa of ["0,5", "2", "-0.1", "NaN", true]) {
    assert.deepEqual(validatePrancha(campos({ alfa_preenchimento: alfa }), ESTILOS), {
      ok: false, field: "alfa_preenchimento", message: CASOS.mensagem,
    });
  }
  assert.equal(validatePrancha(campos({ alfa_preenchimento: "0.6" }), ESTILOS).values.alfa_preenchimento, 0.6);
});

test("pranchaFields: estilo e alfa só quando diferem do padrão; alfa com ponto, sem expoente", () => {
  assert.deepEqual(pranchaFields(PRANCHA_PADRAO, ESTILOS), []);
  assert.deepEqual(pranchaFields(campos({ estilo: "padrao", alfa_preenchimento: 0.35 }), ESTILOS), []);
  const tecnico = campos({ estilo: "tecnico", ...coresDoEstilo("tecnico", ESTILOS), alfa_preenchimento: 0.6 });
  assert.deepEqual(pranchaFields(tecnico, ESTILOS), [
    ["cor_contorno", "#1F2937"],
    ["cor_preenchimento", "#9CA3AF"],
    ["estilo", "tecnico"],
    ["alfa_preenchimento", "0.6"],
  ]);
  assert.deepEqual(pranchaFields(campos({ alfa_preenchimento: 0 }), ESTILOS), [["alfa_preenchimento", "0"]]);
  const [[, minusculo]] = pranchaFields(campos({ alfa_preenchimento: 1e-7 }), ESTILOS);
  assert.doesNotMatch(minusculo, /e/i);
  assert.equal(Number(minusculo), 1e-7);
  assert.deepEqual(validateAlfa(minusculo), { ok: true, value: 1e-7 }); // o servidor aceita o que vai
});

test("pranchaFields com estilo ou alfa inválido não manda nada", () => {
  assert.deepEqual(pranchaFields(campos({ estilo: "urbano" }), ESTILOS), []);
  assert.deepEqual(pranchaFields(campos({ alfa_preenchimento: "2" }), ESTILOS), []);
});

// ---- Resumo das escolhas antes de processar --------------------------------------------

const ESTILOS_RESUMO = JSON.parse(readFileSync(new URL("../../worker/tests/fixtures/catalogo_publico.json", import.meta.url), "utf-8")).estilos;
const mapaResumo = (itens) => Object.fromEntries(itens.map(({ rotulo, valor }) => [rotulo, valor]));

test("resumoEnvio sem personalização: título padrão, estilo Padrão, 35% e layout Padrão", () => {
  assert.deepEqual(resumoEnvio(PRANCHA_PADRAO, { estilos: ESTILOS_RESUMO }), [
    { rotulo: "Título", valor: "GeoLume — Mapa de Localização" },
    { rotulo: "Estilo", valor: "Padrão" },
    { rotulo: "Opacidade", valor: "35%" },
    { rotulo: "Layout", valor: "Padrão" },
  ]);
});

test("resumoEnvio com projeto, responsável, logo, Técnico, 60% e legenda lateral", () => {
  const values = { ...PRANCHA_PADRAO, projeto: "Fazenda Boa Vista", responsavel: "Ana Souza", estilo: "tecnico",
    cor_contorno: "#1F2937", cor_preenchimento: "#9CA3AF", alfa_preenchimento: 0.6, legenda: "lateral" };
  assert.deepEqual(mapaResumo(resumoEnvio(values, { estilos: ESTILOS_RESUMO, logo: true })), {
    "Título": "Fazenda Boa Vista — Mapa de Localização",
    "Responsável": "Ana Souza",
    "Estilo": "Técnico",
    "Opacidade": "60%",
    "Layout": "Legenda lateral",
    "Logo": "Incluída",
  });
});

test("resumoEnvio avisa quando as cores diferem das do estilo", () => {
  const values = { ...PRANCHA_PADRAO, estilo: "tecnico", cor_contorno: "#0055AA", cor_preenchimento: "#9CA3AF" };
  assert.equal(mapaResumo(resumoEnvio(values, { estilos: ESTILOS_RESUMO })).Estilo, "Técnico, com cores personalizadas");
});

test("resumoEnvio sem catálogo: estilo Padrão; valor fora da regra não vira número inventado", () => {
  const resumo = mapaResumo(resumoEnvio({ ...PRANCHA_PADRAO, estilo: "tecnico", alfa_preenchimento: "abc" }, {}));
  assert.equal(resumo.Estilo, "Padrão");
  assert.equal(resumo.Opacidade, "Inválida");
});
