import { test, expect, request as playwrightRequest } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Personalização da prancha: formulário, cabeçalho, cores do polígono, envio, histórico e mapa.pdf.
test.describe.configure({ mode: "serial" });

const AUTH = (name) => fileURLToPath(new URL(`../.auth/${name}`, import.meta.url));
const fixture = (name) => fileURLToPath(new URL(`../../worker/tests/fixtures/${name}`, import.meta.url));
const REPO = fileURLToPath(new URL("../../", import.meta.url));
const CSRF = { "X-GeoLume-CSRF": "1" };
const TILE = /^https:\/\/tile\.openstreetmap\.org\//;
// PNG 1×1 válido (logo pequena) e o mesmo usado nos tiles falsos.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
  "base64",
);
const TITULO_PADRAO = "GeoLume — Mapa de Localização";
const SVG = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>');

const polygon = (page) => page.getByTestId("preview-map").locator("path.geolume-poligono");
const row = (page, taskId) => page.locator(`[data-testid="jobs-row"][data-task-id="${taskId}"]`);
const campo = (page, nome) => page.getByTestId(`prancha-input-${nome}`);
const layout = (page, valor) => page.getByTestId(`prancha-layout-${valor}`);
const miniatura = (page) => page.getByTestId("prancha-miniatura");
const estilo = (page, id) => page.locator(`[data-testid="estilo-card"][data-estilo="${id}"] input`);

async function stubTiles(page) {
  await page.route(TILE, (route) => route.fulfill({ status: 200, contentType: "image/png", body: PNG }));
}

async function abrir(page) {
  await stubTiles(page);
  await page.goto("/");
  await expect(page.getByTestId("app")).toBeVisible();
}

async function personalizar(page) {
  const form = page.getByTestId("prancha-form");
  if (!(await form.evaluate((el) => el.open))) await form.locator("summary").click();
  await expect(campo(page, "projeto")).toBeVisible();
}

/** Espera o job terminar pela API real e devolve o registro público. */
async function concluido(ctx, taskId) {
  await expect.poll(async () => (await (await ctx.get(`/jobs/${taskId}`)).json()).status, { timeout: 90_000 }).toBe("completed");
  return (await ctx.get(`/jobs/${taskId}`)).json();
}

/** Poppler da imagem do worker, lendo o PDF do stdin: nenhum caminho interno envolvido. */
function poppler(args, pdf) {
  return execFileSync("docker", ["compose", "exec", "-T", "api", ...args], { input: pdf, cwd: REPO, maxBuffer: 64 * 1024 * 1024 });
}

function rasterizar(pdf) {
  const ppm = poppler(["pdftoppm", "-r", "150", "-"], pdf);
  const header = ppm.subarray(0, 64).toString("latin1").match(/^P6\s+(\d+)\s+(\d+)\s+255\s/);
  return { pixels: ppm.subarray(header[0].length), largura: Number(header[1]) };
}

const hex = (cor) => [1, 3, 5].map((i) => parseInt(cor.slice(i, i + 2), 16));
const sobreBranco = (rgb, alfa) => rgb.map((c) => Math.round(255 * (1 - alfa) + c * alfa));

function contarPixels({ pixels }, alvo, tolerancia) {
  let n = 0;
  for (let i = 0; i + 2 < pixels.length; i += 3) {
    if (Math.abs(pixels[i] - alvo[0]) <= tolerancia && Math.abs(pixels[i + 1] - alvo[1]) <= tolerancia &&
      Math.abs(pixels[i + 2] - alvo[2]) <= tolerancia) n += 1;
  }
  return n;
}

let a;
let antigo; // job sem personalização (como os jobs anteriores à prancha)

test.beforeAll(async ({ baseURL }) => {
  a = await playwrightRequest.newContext({ baseURL, storageState: AUTH("a.json") });
  const res = await a.post("/jobs/async", {
    headers: CSRF,
    multipart: { file: { name: "lote_simples.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("lote_simples.geojson")) } },
  });
  expect(res.status()).toBe(202);
  antigo = (await res.json()).task_id;
  await concluido(a, antigo);
});

test.afterAll(async () => a?.dispose());

test("sem personalização: formulário fechado, prancha padrão e envio só com o GeoJSON", async ({ page }) => {
  await abrir(page);
  await expect(page.getByTestId("prancha-form")).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("prancha-head")).toBeHidden();

  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  await expect(page.getByTestId("prancha-head")).toBeVisible();
  await expect(page.getByTestId("prancha-title")).toHaveText(TITULO_PADRAO);
  await expect(page.getByTestId("prancha-responsavel")).toBeHidden();
  await expect(page.getByTestId("prancha-logo")).toBeHidden();
  await expect(page.getByTestId("prancha-legenda")).toBeHidden();
  await expect(polygon(page)).toHaveAttribute("stroke", "#C80000");
  await expect(polygon(page)).toHaveAttribute("fill", "#FFC800");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", String(89 / 255)); // alfa8(0.35) / 255, igual ao mapa.pdf

  const envio = page.waitForRequest((req) => req.url().endsWith("/jobs/async") && req.method() === "POST");
  await page.getByTestId("upload-submit").click();
  const corpo = (await envio).postDataBuffer().toString("latin1");
  expect(corpo).toContain('name="file"');
  expect(corpo).not.toMatch(/name="(projeto|responsavel|cor_contorno|cor_preenchimento|legenda|logo|estilo|alfa_preenchimento)"/);
  await expect(page.getByTestId("upload-message")).toContainText("enviado");
  const taskId = await page.getByTestId("detail-task-id").textContent();
  expect((await (await a.get(`/jobs/${taskId}`)).json()).prancha).toBeNull();
});

test("personalizada: cabeçalho e polígono seguem o formulário; PDF sai com as mesmas cores e textos literais", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await campo(page, "projeto").fill("Loteamento Sol [% 1+1 %]");
  await campo(page, "responsavel").fill("Eng. Ana env('PATH')");
  await campo(page, "contorno").fill("#123456");
  await campo(page, "preenchimento").fill("#2e8b57");
  await layout(page, "lateral").check();
  await campo(page, "logo").setInputFiles({ name: "marca.png", mimeType: "image/png", buffer: PNG });
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);

  await expect(page.getByTestId("prancha-title")).toHaveText("Loteamento Sol [% 1+1 %] — Mapa de Localização");
  await expect(page.getByTestId("prancha-responsavel")).toHaveText("Responsável técnico: Eng. Ana env('PATH')");
  await expect(page.getByTestId("prancha-logo")).toBeVisible();
  await expect(page.getByTestId("prancha-logo")).toHaveAttribute("src", /^blob:/);
  await expect(page.getByTestId("prancha-legenda")).toBeVisible();
  await expect(polygon(page)).toHaveAttribute("stroke", "#123456");
  await expect(polygon(page)).toHaveAttribute("fill", "#2E8B57");

  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("upload-message")).toContainText("enviado");
  const taskId = await page.getByTestId("detail-task-id").textContent();
  const job = await concluido(a, taskId);
  expect(job.prancha).toEqual({
    projeto: "Loteamento Sol [% 1+1 %]", responsavel: "Eng. Ana env('PATH')", cor_contorno: "#123456",
    cor_preenchimento: "#2E8B57", legenda: "lateral", logo: `/jobs/${taskId}/logo`, estilo: "padrao", alfa_preenchimento: 0.35,
  });

  // Campos continuam preenchidos para o próximo envio.
  await expect(campo(page, "projeto")).toHaveValue("Loteamento Sol [% 1+1 %]");

  // Job selecionado: o cabeçalho passa a vir da API e a logo da rota autenticada.
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  await expect(page.getByTestId("prancha-logo")).toHaveAttribute("src", `/jobs/${taskId}/logo`);
  await expect.poll(() => page.getByTestId("prancha-logo").evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);

  // Cor exibida = cor exportada: o que o navegador pinta aparece no mapa.pdf rasterizado.
  const stroke = await polygon(page).getAttribute("stroke");
  const fill = await polygon(page).getAttribute("fill");
  const alfa = Number(await polygon(page).getAttribute("fill-opacity"));
  const pdf = await (await a.get(`/jobs/${taskId}/files/mapa`)).body();
  const raster = rasterizar(pdf);
  expect(contarPixels(raster, hex(stroke), 12), "contorno no PDF").toBeGreaterThan(200);
  expect(contarPixels(raster, sobreBranco(hex(fill), alfa), 4), "preenchimento no PDF").toBeGreaterThan(5_000);

  const texto = poppler(["pdftotext", "-layout", "-", "-"], pdf).toString("utf-8");
  expect(texto).toContain("Loteamento Sol [% 1+1 %] — Mapa de Localização");
  expect(texto).toContain("Responsável técnico: Eng. Ana env('PATH')");
  expect(texto).toContain("Limite do imóvel");
});

test("histórico: job sem prancha usa o padrão; job personalizado usa a prancha dele", async ({ page }) => {
  const res = await a.post("/jobs/async", {
    headers: CSRF,
    multipart: {
      file: { name: "lote_boa_vista.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("lote_boa_vista.geojson")) },
      projeto: "Condomínio Norte",
      cor_contorno: "#0000FF",
      cor_preenchimento: "#00FF00",
    },
  });
  expect(res.status()).toBe(202);
  const taskId = (await res.json()).task_id;
  await concluido(a, taskId);

  await abrir(page);
  await row(page, taskId).click();
  await expect(polygon(page)).toHaveCount(1);
  await expect(page.getByTestId("prancha-title")).toHaveText("Condomínio Norte — Mapa de Localização");
  await expect(polygon(page)).toHaveAttribute("stroke", "#0000FF");
  await expect(polygon(page)).toHaveAttribute("fill", "#00FF00");
  await expect(page.getByTestId("prancha-logo")).toBeHidden();

  await row(page, antigo).click();
  await expect(page.getByTestId("prancha-title")).toHaveText(TITULO_PADRAO);
  await expect(polygon(page)).toHaveAttribute("stroke", "#C80000");
  await expect(polygon(page)).toHaveAttribute("fill", "#FFC800");
  await expect(page.getByTestId("prancha-responsavel")).toBeHidden();
});

test("HTML nos campos vira texto: nada é interpretado", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  const projeto = `<img src=x onerror="window.__xss=1">`;
  const responsavel = `<script>window.__xss=2</script>`;
  await campo(page, "projeto").fill(projeto);
  await campo(page, "responsavel").fill(responsavel);
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("prancha-title")).toHaveText(`${projeto} — Mapa de Localização`);
  await expect(page.getByTestId("prancha-responsavel")).toHaveText(`Responsável técnico: ${responsavel}`);
  await expect(page.getByTestId("prancha-head").locator("script, img:not([data-testid=prancha-logo])")).toHaveCount(0);
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});

test("validação no navegador: texto longo e logo inválida bloqueiam Processar com aviso", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("upload-submit")).toBeEnabled();

  // maxlength só ajuda quem digita; valor colado por script ainda é conferido.
  await campo(page, "projeto").evaluate((el) => {
    el.value = "a".repeat(101);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.getByTestId("prancha-message")).toHaveText("Nome do projeto deve ter no máximo 100 caracteres.");
  await expect(page.getByTestId("upload-submit")).toBeDisabled();
  await campo(page, "projeto").fill("a".repeat(100));
  await expect(page.getByTestId("prancha-message")).toHaveText("");
  await expect(page.getByTestId("upload-submit")).toBeEnabled();

  await campo(page, "logo").setInputFiles({ name: "marca.svg", mimeType: "image/svg+xml", buffer: SVG });
  await expect(page.getByTestId("prancha-message")).toHaveText("Logo deve ser uma imagem PNG ou JPEG.");
  await expect(page.getByTestId("upload-submit")).toBeDisabled();
  await expect(page.getByTestId("prancha-logo")).toBeHidden();

  await campo(page, "logo").setInputFiles({ name: "grande.png", mimeType: "image/png", buffer: Buffer.alloc(2 * 1024 * 1024 + 1) });
  await expect(page.getByTestId("prancha-message")).toHaveText("Logo maior que 2 MB.");
  await expect(page.getByTestId("upload-submit")).toBeDisabled();

  await page.getByTestId("prancha-logo-remove").click();
  await expect(page.getByTestId("prancha-message")).toHaveText("");
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  await expect(page.getByTestId("prancha-logo-remove")).toBeHidden();
});

test("logo falsa passa no navegador e o servidor recusa pelo conteúdo, sem criar job", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await campo(page, "logo").setInputFiles({ name: "falsa.png", mimeType: "image/png", buffer: Buffer.from("não sou png") });
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  const resposta = page.waitForResponse((res) => res.url().endsWith("/jobs/async"));
  await page.getByTestId("upload-submit").click();
  expect((await resposta).status()).toBe(422);
  await expect(page.getByTestId("upload-message")).toHaveText("Não foi possível enviar: Logo deve ser uma imagem PNG ou JPEG válida.");
  await expect(page.getByTestId("detail-task-id")).toHaveText("");
});

test("logo de outro usuário ou sem sessão não é entregue", async ({ baseURL }) => {
  const res = await a.post("/jobs/async", {
    headers: CSRF,
    multipart: {
      file: { name: "lote_simples.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("lote_simples.geojson")) },
      logo: { name: "marca.png", mimeType: "image/png", buffer: PNG },
    },
  });
  expect(res.status()).toBe(202);
  const taskId = (await res.json()).task_id;
  const dono = await a.get(`/jobs/${taskId}/logo`);
  expect(dono.status()).toBe(200);
  expect(dono.headers()["content-type"]).toBe("image/png");

  const b = await playwrightRequest.newContext({ baseURL, storageState: AUTH("b.json") });
  const outro = await b.get(`/jobs/${taskId}/logo`);
  expect(outro.status()).toBe(404);
  expect(await outro.json()).toEqual({ detail: "Job não encontrado" });
  await b.dispose();

  const anonimo = await playwrightRequest.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  expect((await anonimo.get(`/jobs/${taskId}/logo`)).status()).toBe(401);
  await anonimo.dispose();

  const semLogo = await a.get(`/jobs/${antigo}/logo`);
  expect(semLogo.status()).toBe(404);
  expect(await semLogo.json()).toEqual({ detail: "Logo não disponível" });
});

test("restaurar padrão e sair limpam a personalização", async ({ browser, baseURL }) => {
  const { users } = JSON.parse(readFileSync(AUTH("users.json"), "utf8"));
  // Sessão própria: o logout não derruba a sessão compartilhada dos outros testes.
  const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await stubTiles(page);
  await page.goto("/");
  await page.getByTestId("login-email").fill(users.a.email);
  await page.getByTestId("login-password").fill(users.a.password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("app")).toBeVisible();
  await personalizar(page);
  await campo(page, "projeto").fill("Projeto X");
  await campo(page, "contorno").fill("#123456");
  await campo(page, "logo").setInputFiles({ name: "marca.png", mimeType: "image/png", buffer: PNG });
  await layout(page, "inferior").check();
  await estilo(page, "pb").check();
  await campo(page, "alfa").fill("80");
  await page.getByTestId("prancha-reset").click();
  await expect(layout(page, "nenhuma")).toBeChecked();
  await expect(estilo(page, "padrao")).toBeChecked();
  await expect(campo(page, "alfa")).toHaveValue("35");
  await expect(page.getByTestId("prancha-alfa-valor")).toHaveText("35%");
  await expect(campo(page, "projeto")).toHaveValue("");
  await expect(campo(page, "contorno")).toHaveValue("#c80000");
  await expect(page.getByTestId("prancha-logo-remove")).toBeHidden();

  await campo(page, "responsavel").fill("Fulano");
  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(campo(page, "responsavel")).toHaveValue("");
  await expect(page.getByTestId("prancha-form")).not.toHaveAttribute("open", "");
  await context.close();
});

for (const [width, height] of [[1440, 900], [390, 844], [320, 640]]) {
  test(`prancha sem rolagem horizontal e com alvos de toque ${width}x${height}`, async ({ page }) => {
    const errors = [];
    page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
    await page.setViewportSize({ width, height });
    await abrir(page);
    await personalizar(page);
    await campo(page, "projeto").fill("W".repeat(100));
    await campo(page, "responsavel").fill("Engenheira ".repeat(9));
    await campo(page, "logo").setInputFiles({ name: "marca.png", mimeType: "image/png", buffer: PNG });
    await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
    await expect(page.getByTestId("prancha-head")).toBeVisible();

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    for (const id of ["prancha-head", "prancha-title", "prancha-responsavel"]) {
      const el = page.getByTestId(id);
      expect(await el.evaluate((n) => n.scrollWidth - n.clientWidth), id).toBeLessThanOrEqual(0);
    }
    const alvos = page.getByTestId("prancha-form").locator("summary, button:visible, input:visible:not([type=radio]), .prancha-layout-card");
    for (let i = 0; i < (await alvos.count()); i += 1) {
      const box = await alvos.nth(i).boundingBox();
      expect(box.x, `alvo ${i}`).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, `alvo ${i}`).toBeLessThanOrEqual(width);
      expect(box.height, `alvo ${i}`).toBeGreaterThanOrEqual(44);
    }
    expect(errors).toEqual([]);
  });
}

test("cartões de layout: três rádios nomeados no grupo Layout da prancha, navegáveis por seta", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  const grupo = page.getByRole("group", { name: "Layout da prancha" });
  for (const nome of ["Padrão", "Legenda lateral", "Legenda inferior"]) {
    await expect(grupo.getByRole("radio", { name: nome, exact: true })).toHaveCount(1);
  }
  await expect(grupo.getByRole("radio")).toHaveCount(3);
  await expect(layout(page, "nenhuma")).toBeChecked();
  await expect(layout(page, "inferior")).toHaveAccessibleDescription("Legenda abaixo do mapa, ao lado da tabela.");
  await layout(page, "nenhuma").focus();
  await page.keyboard.press("ArrowRight");
  await expect(layout(page, "lateral")).toBeChecked();
  await expect(layout(page, "lateral")).toBeFocused();
});

test("miniatura acompanha o layout; legenda só em lateral e inferior", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("prancha-head")).toBeVisible();
  await expect(miniatura(page)).toHaveAttribute("data-layout", "nenhuma");
  await expect(page.getByTestId("prancha-legenda")).toBeHidden();
  // Bloco de legenda da miniatura: só aparece onde o layout tem legenda.
  await expect(miniatura(page).locator(".mini-legenda")).toBeHidden();
  const cartao = (valor) => page.locator(`.prancha-layout-card:has([data-testid="prancha-layout-${valor}"]) .mini-legenda`);
  await expect(cartao("nenhuma")).toBeHidden();
  await expect(cartao("lateral")).toBeVisible();
  await expect(cartao("inferior")).toBeVisible();
  for (const valor of ["lateral", "inferior"]) {
    await layout(page, valor).check();
    await expect(miniatura(page)).toHaveAttribute("data-layout", valor);
    await expect(miniatura(page).locator(".mini-legenda")).toBeVisible();
    await expect(page.getByTestId("prancha-legenda")).toBeVisible();
    await expect(page.getByTestId("prancha-legenda")).toHaveAttribute("data-posicao", valor);
  }
  await layout(page, "nenhuma").check();
  await expect(miniatura(page)).toHaveAttribute("data-layout", "nenhuma");
  await expect(page.getByTestId("prancha-legenda")).toBeHidden();
});

test("envio leva a legenda escolhida e o job guarda o layout", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await layout(page, "inferior").check();
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  const envio = page.waitForRequest((req) => req.url().endsWith("/jobs/async") && req.method() === "POST");
  await page.getByTestId("upload-submit").click();
  const corpo = (await envio).postDataBuffer().toString("latin1");
  expect(corpo).toMatch(/name="legenda"\r\n\r\ninferior\r\n/);
  await expect(page.getByTestId("upload-message")).toContainText("enviado");
  const taskId = await page.getByTestId("detail-task-id").textContent();
  expect((await concluido(a, taskId)).prancha.legenda).toBe("inferior");
});

test("histórico mostra o layout do job; job antigo fica no Padrão", async ({ page }) => {
  const res = await a.post("/jobs/async", {
    headers: CSRF,
    multipart: {
      file: { name: "lote_simples.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("lote_simples.geojson")) },
      legenda: "inferior",
    },
  });
  expect(res.status()).toBe(202);
  const taskId = (await res.json()).task_id;
  await concluido(a, taskId);

  await abrir(page);
  await row(page, taskId).click();
  await expect(polygon(page)).toHaveCount(1);
  await expect(miniatura(page)).toHaveAttribute("data-layout", "inferior");
  await expect(page.getByTestId("prancha-legenda")).toHaveAttribute("data-posicao", "inferior");
  await row(page, antigo).click();
  await expect(miniatura(page)).toHaveAttribute("data-layout", "nenhuma");
  await expect(page.getByTestId("prancha-legenda")).toBeHidden();
});

test("rádio adulterado bloqueia o envio com mensagem fixa e nenhum POST sai", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  let posts = 0;
  const errors = [];
  page.on("request", (req) => req.url().endsWith("/jobs/async") && req.method() === "POST" && (posts += 1));
  page.on("pageerror", (err) => errors.push(err.message));
  await layout(page, "nenhuma").evaluate((el) => {
    el.value = "topo";
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(page.getByTestId("prancha-message")).toHaveText("Layout da prancha inválido.");
  await expect(page.getByTestId("upload-submit")).toBeDisabled();
  // Envio forçado por script também passa pela validação e mostra a mensagem de novo.
  await page.getByTestId("prancha-message").evaluate((el) => { el.textContent = ""; });
  await page.getByTestId("upload-submit").evaluate((botao) => {
    botao.disabled = false;
    botao.click();
  });
  await expect(page.getByTestId("prancha-message")).toHaveText("Layout da prancha inválido.");
  expect(posts).toBe(0);
  expect(errors).toEqual([]);
});

test("mapa.pdf e memorial.pdf reais por layout: mapa em 1 página, memorial com todos os vértices", async () => {
  test.slow();
  const longo = (letra) => `${letra} `.repeat(50).trim().padEnd(100, letra);
  for (const legenda of ["nenhuma", "lateral", "inferior"]) {
    const res = await a.post("/jobs/async", {
      headers: CSRF,
      multipart: {
        file: { name: "poligono_30_longo.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("poligono_30_longo.geojson")) },
        logo: { name: "marca.png", mimeType: "image/png", buffer: PNG },
        projeto: longo("P"),
        responsavel: longo("R"),
        cor_contorno: "#123456",
        cor_preenchimento: "#2E8B57",
        legenda,
      },
    });
    expect(res.status(), legenda).toBe(202);
    const taskId = (await res.json()).task_id;
    await concluido(a, taskId);
    const mapa = await (await a.get(`/jobs/${taskId}/files/mapa`)).body();
    const memorial = await (await a.get(`/jobs/${taskId}/files/memorial`)).body();

    expect(poppler(["pdfinfo", "-"], mapa).toString("utf-8"), legenda).toMatch(/^Pages:\s+1$/m);
    const textoMapa = poppler(["pdftotext", "-layout", "-", "-"], mapa).toString("utf-8").replace(/\s+/g, " ");
    expect(textoMapa, legenda).toMatch(/Exibidos \d+ de 30 vértices\. Demais vértices no memorial descritivo\./);
    expect(textoMapa.includes("Limite do imóvel"), legenda).toBe(legenda !== "nenhuma");

    const textoMemorial = poppler(["pdftotext", "-layout", "-", "-"], memorial).toString("utf-8");
    const vertices = [...textoMemorial.matchAll(/^\s*(V\d+)\s/gm)].map((m) => m[1]);
    expect(vertices, legenda).toEqual(Array.from({ length: 30 }, (_, i) => `V${i + 1}`));
    expect(textoMemorial).not.toContain("…");
  }
});

// ---- Task 7: estilos e opacidade do preenchimento na prancha -------------------------

const ALFA_INVALIDO = "Opacidade do preenchimento deve estar entre 0 e 1.";

test("estilos Padrão, Técnico e Preto e branco; escolher um estilo preenche as cores dele", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  const grupo = page.getByRole("group", { name: "Estilo do polígono" });
  await expect(grupo.getByTestId("estilo-card")).toHaveCount(3);
  for (const nome of ["Padrão", "Técnico", "Preto e branco"]) {
    await expect(grupo.getByRole("radio", { name: nome, exact: true })).toHaveCount(1);
  }
  await expect(estilo(page, "padrao")).toBeChecked();
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);

  await estilo(page, "tecnico").check();
  await expect(campo(page, "contorno")).toHaveValue("#1f2937");
  await expect(campo(page, "preenchimento")).toHaveValue("#9ca3af");
  await expect(polygon(page)).toHaveAttribute("stroke", "#1F2937");
  await expect(polygon(page)).toHaveAttribute("stroke-width", "2");

  await estilo(page, "pb").check();
  await expect(campo(page, "contorno")).toHaveValue("#000000");
  await expect(campo(page, "preenchimento")).toHaveValue("#ffffff");
  await expect(polygon(page)).toHaveAttribute("stroke-width", "2");

  await estilo(page, "padrao").check();
  await expect(campo(page, "contorno")).toHaveValue("#c80000");
  await expect(polygon(page)).toHaveAttribute("stroke-width", "3");

  // Pelo teclado: seta troca o estilo dentro do grupo.
  await estilo(page, "padrao").focus();
  await page.keyboard.press("ArrowRight");
  await expect(estilo(page, "tecnico")).toBeChecked();
  await expect(campo(page, "contorno")).toHaveValue("#1f2937");
});

test("opacidade do preenchimento (entra no PDF): 0%, 35%, 60% e 100% na tela com alfa8/255", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  const alfa = page.getByLabel("Opacidade do preenchimento (entra no PDF)");
  await expect(alfa).toHaveValue("35");
  await expect(page.getByTestId("prancha-alfa-valor")).toHaveText("35%");
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  for (const [percent, esperado] of [["0", "0"], ["35", String(89 / 255)], ["60", "0.6"], ["100", "1"]]) {
    await alfa.fill(percent);
    await expect(page.getByTestId("prancha-alfa-valor")).toHaveText(`${percent}%`);
    await expect(polygon(page)).toHaveAttribute("fill-opacity", esperado);
    await expect(polygon(page)).toHaveAttribute("stroke-opacity", "1"); // contorno sempre visível, inclusive com 0%
  }
  // Amostra da legenda da prancha segue o mesmo alfa.
  await layout(page, "lateral").check();
  await alfa.fill("0");
  await expect(page.getByTestId("prancha-swatch")).toHaveCSS("background-color", "rgba(255, 200, 0, 0)");
});

test("estilo Técnico + cor alterada + alfa 60% ⇒ tela, envio, job e PDF coerentes", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await estilo(page, "tecnico").check();
  await campo(page, "contorno").fill("#ff00ff");
  await campo(page, "alfa").fill("60");
  await layout(page, "lateral").check();
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  await expect(polygon(page)).toHaveAttribute("stroke", "#FF00FF");
  await expect(polygon(page)).toHaveAttribute("stroke-width", "2"); // a cor não muda a espessura do estilo
  await expect(polygon(page)).toHaveAttribute("fill-opacity", "0.6");

  const envio = page.waitForRequest((req) => req.url().endsWith("/jobs/async") && req.method() === "POST");
  await page.getByTestId("upload-submit").click();
  const corpo = (await envio).postDataBuffer().toString("utf-8");
  expect(corpo).toMatch(/name="estilo"\r\n\r\ntecnico\r\n/);
  expect(corpo).toMatch(/name="alfa_preenchimento"\r\n\r\n0\.6\r\n/);
  await expect(page.getByTestId("upload-message")).toContainText("enviado");
  const taskId = await page.getByTestId("detail-task-id").textContent();
  const job = await concluido(a, taskId);
  expect(job.prancha).toMatchObject({ estilo: "tecnico", alfa_preenchimento: 0.6, cor_contorno: "#FF00FF", cor_preenchimento: "#9CA3AF", legenda: "lateral" });

  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  await expect(polygon(page)).toHaveAttribute("stroke-width", "2");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", "0.6");
  const pdf = await (await a.get(`/jobs/${taskId}/files/mapa`)).body();
  const texto = poppler(["pdftotext", "-layout", "-", "-"], pdf).toString("utf-8");
  expect(texto).toContain("Limite do imóvel");
  expect(texto).not.toContain("OpenStreetMap"); // OSM só na pré-visualização
  const raster = rasterizar(pdf);
  expect(contarPixels(raster, hex("#FF00FF"), 12), "contorno no PDF").toBeGreaterThan(200);
  expect(contarPixels(raster, sobreBranco(hex("#9CA3AF"), 0.6), 4), "preenchimento no PDF").toBeGreaterThan(5_000);
});

test("alfa inválido forçado no DOM (150) bloqueia o envio com a mensagem fixa", async ({ page }) => {
  await abrir(page);
  await personalizar(page);
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  let posts = 0;
  page.on("request", (req) => req.url().endsWith("/jobs/async") && req.method() === "POST" && (posts += 1));
  await campo(page, "alfa").evaluate((el) => {
    el.max = "200";
    el.value = "150";
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await expect(page.getByTestId("prancha-message")).toHaveText(ALFA_INVALIDO);
  await expect(page.getByTestId("upload-submit")).toBeDisabled();
  await page.getByTestId("upload-submit").click({ force: true });
  await page.waitForTimeout(300);
  expect(posts).toBe(0);
});

test("job antigo sem prancha abre com estilo padrão e fill-opacity 89/255; job com estilo usa o dele", async ({ page }) => {
  const res = await a.post("/jobs/async", {
    headers: CSRF,
    multipart: {
      file: { name: "lote_boa_vista.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("lote_boa_vista.geojson")) },
      estilo: "pb",
      alfa_preenchimento: "0",
    },
  });
  expect(res.status()).toBe(202);
  const comEstilo = (await res.json()).task_id;
  await concluido(a, comEstilo);

  await abrir(page);
  await row(page, antigo).click();
  await expect(polygon(page)).toHaveCount(1);
  await expect(polygon(page)).toHaveAttribute("stroke-width", "3");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", String(89 / 255));

  await row(page, comEstilo).click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(comEstilo);
  await expect(polygon(page)).toHaveAttribute("stroke-width", "2");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", "0");
  await expect(polygon(page)).toHaveAttribute("stroke-opacity", "1");
});

test("sem catálogo os cartões de estilo não aparecem e o envio segue com o padrão", async ({ page }) => {
  await page.route(/\/camadas$/, (route) => route.fulfill({ status: 500, contentType: "application/json", body: "{}" }));
  await abrir(page);
  await personalizar(page);
  await expect(page.getByTestId("estilo-card")).toHaveCount(0);
  await expect(page.getByTestId("prancha-estilos")).toContainText("Estilos indisponíveis no momento; o polígono usa o estilo Padrão.");
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveAttribute("stroke-width", "3");
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
});
