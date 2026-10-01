import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const fixture = (name) => fileURLToPath(new URL(`../../worker/tests/fixtures/${name}`, import.meta.url));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TILE = /^https:\/\/tile\.openstreetmap\.org\//;
// PNG 1x1 transparente: os testes não dependem da rede do OSM, só a API é real.
const BLANK_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
  "base64",
);

// Jobs reais na API: o teste de histórico usa o task_id do envio concluído.
test.describe.configure({ mode: "serial" });

let completedTaskId;

async function stubTiles(page) {
  const tiles = [];
  await page.route(TILE, (route) => {
    tiles.push(route.request().url());
    return route.fulfill({ status: 200, contentType: "image/png", body: BLANK_PNG });
  });
  return tiles;
}

const polygon = (page) => page.getByTestId("preview-map").locator("path.geolume-poligono");

/** O polígono está dentro do mapa e ocupa boa parte dele (zoom na extensão). */
async function expectFitted(page) {
  await expect(polygon(page)).toHaveCount(1);
  await expect
    .poll(async () => {
      const map = await page.getByTestId("preview-map").boundingBox();
      const shape = await polygon(page).boundingBox();
      if (!map || !shape) return "sem caixa";
      const inside =
        shape.x >= map.x - 1 &&
        shape.y >= map.y - 1 &&
        shape.x + shape.width <= map.x + map.width + 1 &&
        shape.y + shape.height <= map.y + map.height + 1;
      const fills = shape.width >= map.width * 0.4 || shape.height >= map.height * 0.4;
      return inside && fills ? "ok" : JSON.stringify({ map, shape });
    })
    .toBe("ok");
}

test("pre-visualiza GeoJSON valido e enquadra a geometria", async ({ page }) => {
  const tiles = await stubTiles(page);
  await page.goto("/");
  await expect(page.getByTestId("health")).toContainText("API online");
  await expect(page.getByTestId("preview-message")).toHaveText("Selecione um GeoJSON para ver o polígono no mapa.");
  await expect(page.getByTestId("preview-map")).toBeHidden();
  expect(tiles).toHaveLength(0); // mapa só é criado com uma geometria

  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("preview-map")).toBeVisible();
  await expect(page.getByTestId("preview-message")).toContainText("gleba_rural_exemplo.geojson");
  await expectFitted(page);
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  expect(tiles.length).toBeGreaterThan(0);

  const attribution = page.getByTestId("preview-map").locator(".leaflet-control-attribution");
  await expect(attribution).toContainText("OpenStreetMap");
  await expect(attribution.locator('a[href="https://www.openstreetmap.org/copyright"]')).toHaveCount(1);
});

test("geometria invalida mostra mensagem clara e permite processar", async ({ page }) => {
  await stubTiles(page);
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture("autointersecao.geojson"));
  const message = page.getByTestId("preview-message");
  await expect(message).toContainText("autointerseção");
  await expect(message).toHaveAttribute("data-kind", "error");
  await expect(polygon(page)).toHaveCount(0);

  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(UUID);
});

test("arquivo vazio e FeatureCollection sem feicoes", async ({ page }) => {
  await stubTiles(page);
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles({
    name: "zero.geojson",
    mimeType: "application/geo+json",
    buffer: Buffer.alloc(0),
  });
  await expect(page.getByTestId("upload-message")).toHaveText("O arquivo está vazio");
  await expect(page.getByTestId("upload-submit")).toBeDisabled();
  await expect(page.getByTestId("preview-message")).toHaveText("Selecione um GeoJSON para ver o polígono no mapa.");
  await expect(polygon(page)).toHaveCount(0);

  await page.getByTestId("upload-input").setInputFiles(fixture("vazio.geojson"));
  await expect(page.getByTestId("preview-message")).toContainText("não contém feições");
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  await expect(polygon(page)).toHaveCount(0);
});

test("multiplos arquivos: so a ultima geometria fica no mapa", async ({ page }) => {
  await stubTiles(page);
  await page.goto("/");
  const input = page.getByTestId("upload-input");
  const message = page.getByTestId("preview-message");

  await input.setInputFiles(fixture("lote_simples.geojson"));
  await expect(message).toContainText("lote_simples.geojson");
  await expectFitted(page);

  await input.setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(message).toContainText("gleba_rural_exemplo.geojson");
  await expectFitted(page);

  await input.setInputFiles(fixture("linha.geojson"));
  await expect(message).toContainText("precisa ser um polígono");
  await expect(polygon(page)).toHaveCount(0);

  await input.setInputFiles(fixture("lote_simples.geojson"));
  await expect(message).toContainText("lote_simples.geojson");
  await expectFitted(page);
});

/** Segura o POST /jobs/async até `release(outcome)`; "continue" envia de verdade à API. */
async function holdEnqueue(page) {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  await page.route(/\/jobs\/async$/, async (route) => {
    const outcome = await gate;
    return outcome === "continue" ? route.continue() : route.abort();
  });
  return release;
}

for (const outcome of ["continue", "abort"]) {
  test(`trocar de arquivo durante o envio preserva a nova selecao (${outcome})`, async ({ page }) => {
    await stubTiles(page);
    const release = await holdEnqueue(page);
    await page.goto("/");
    const input = page.getByTestId("upload-input");
    const message = page.getByTestId("preview-message");

    await input.setInputFiles(fixture("lote_simples.geojson"));
    await expectFitted(page);
    await page.getByTestId("upload-submit").click();
    await expect(page.getByTestId("upload-submit")).toHaveText("Enviando…");

    await input.setInputFiles(fixture("gleba_rural_exemplo.geojson"));
    await expect(message).toContainText("gleba_rural_exemplo.geojson");
    release(outcome);

    if (outcome === "continue") await expect(page.getByTestId("detail-task-id")).toHaveText(UUID);
    else await expect(page.getByTestId("upload-message")).toContainText("Não foi possível enviar");
    await expect(page.getByTestId("upload-submit")).toHaveText("Processar");
    await expect(page.getByTestId("upload-file")).toContainText("gleba_rural_exemplo.geojson");
    await expect(message).toContainText("gleba_rural_exemplo.geojson");
    await expect(page.getByTestId("upload-submit")).toBeEnabled();
    await expectFitted(page);
  });

  test(`arquivo invalido escolhido durante o envio mantem o aviso (${outcome})`, async ({ page }) => {
    await stubTiles(page);
    const release = await holdEnqueue(page);
    await page.goto("/");
    const input = page.getByTestId("upload-input");
    const upload = page.getByTestId("upload-message");

    await input.setInputFiles(fixture("lote_simples.geojson"));
    await expectFitted(page);
    await page.getByTestId("upload-submit").click();
    await expect(page.getByTestId("upload-submit")).toHaveText("Enviando…");

    await input.setInputFiles({ name: "zero.geojson", mimeType: "application/geo+json", buffer: Buffer.alloc(0) });
    await expect(upload).toHaveText("O arquivo está vazio");
    release(outcome);

    await expect(page.getByTestId("upload-submit")).toHaveText("Processar");
    await expect(upload).toContainText(outcome === "continue" ? "lote_simples.geojson enviado" : "Não foi possível enviar");
    await expect(upload).toContainText("O arquivo está vazio");
    await expect(upload).toHaveAttribute("data-kind", "error");
    await expect(page.getByTestId("upload-file")).toContainText("zero.geojson");
    await expect(page.getByTestId("upload-submit")).toBeDisabled();
  });
}

test("area e perimetro aparecem junto do mapa depois do processamento", async ({ page, request }) => {
  await stubTiles(page);
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture("lote_simples.geojson"));
  await expectFitted(page);
  await expect(page.getByTestId("preview-metrics")).toBeHidden();
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(UUID);
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  completedTaskId = await page.getByTestId("detail-task-id").textContent();

  const resultado = await (await request.get(`/jobs/${completedTaskId}/files/resultado`)).json();
  const area = `${resultado.area_ha.toLocaleString("pt-BR", { maximumFractionDigits: 4 })} ha`;
  const perimetro = `${resultado.perimetro_m.toLocaleString("pt-BR", { maximumFractionDigits: 2 })} m`;
  const metrics = page.getByTestId("preview-metrics");
  await expect(metrics).toBeVisible();
  await expect(page.getByTestId("metric-area")).toHaveText(area);
  await expect(page.getByTestId("metric-perimetro")).toHaveText(perimetro);
  await expect(metrics).toContainText("preliminar");

  // A geometria enviada continua no mapa, agora identificada como o job selecionado.
  await expectFitted(page);
  await expect(page.getByTestId("preview-message")).toContainText("lote_simples.geojson");
});

const row = (page, taskId) => page.locator(`[data-testid="jobs-row"][data-task-id="${taskId}"]`);

/** Envia e espera o estado final; devolve o task_id. */
async function processFile(page, name, final = "Concluído") {
  await page.getByTestId("upload-input").setInputFiles(fixture(name));
  const posted = page.waitForResponse((res) => res.url().endsWith("/jobs/async") && res.status() === 202);
  await page.getByTestId("upload-submit").click();
  const { task_id: taskId } = await (await posted).json();
  await expect(page.getByTestId("detail-task-id")).toHaveText(taskId);
  await expect(page.getByTestId("detail-status")).toHaveText(final, { timeout: 90_000 });
  return taskId;
}

async function expectMetricsOf(page, request, taskId) {
  const resultado = await (await request.get(`/jobs/${taskId}/files/resultado`)).json();
  await expect(page.getByTestId("preview-metrics")).toBeVisible();
  await expect(page.getByTestId("metric-area")).toHaveText(
    `${resultado.area_ha.toLocaleString("pt-BR", { maximumFractionDigits: 4 })} ha`,
  );
  await expect(page.getByTestId("metric-perimetro")).toHaveText(
    `${resultado.perimetro_m.toLocaleString("pt-BR", { maximumFractionDigits: 2 })} m`,
  );
}

let jobA; // lote_boa_vista
let jobB; // gleba_rural_exemplo

test("dois jobs consecutivos: cada um recupera a propria geometria do historico", async ({ page, request }) => {
  const errors = [];
  page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
  page.on("pageerror", (err) => errors.push(err.message));
  await stubTiles(page);
  await page.goto("/");
  jobA = await processFile(page, "lote_boa_vista.geojson");
  jobB = await processFile(page, "gleba_rural_exemplo.geojson");

  // Nova sessão: nada em memória, a geometria só pode vir de GET /jobs/{task_id}/input.
  await page.reload();
  const inputs = [];
  page.on("response", (res) => res.url().endsWith("/input") && inputs.push([res.url(), res.status(), res.headers()["content-type"]]));
  const message = page.getByTestId("preview-message");

  await row(page, jobA).click();
  await expect(message).toHaveText("Job selecionado: lote_boa_vista.geojson.");
  await expectFitted(page);
  await expectMetricsOf(page, request, jobA);
  const shapeA = await polygon(page).getAttribute("d");

  await row(page, jobB).click();
  await expect(message).toHaveText("Job selecionado: gleba_rural_exemplo.geojson.");
  await expectFitted(page);
  await expectMetricsOf(page, request, jobB);
  expect(await polygon(page).getAttribute("d")).not.toBe(shapeA);

  expect(inputs).toEqual([
    [expect.stringContaining(`/jobs/${jobA}/input`), 200, "application/geo+json"],
    [expect.stringContaining(`/jobs/${jobB}/input`), 200, "application/geo+json"],
  ]);
  // Voltar ao A usa o cache: mesma geometria, sem nova requisição.
  await row(page, jobA).click();
  await expect(message).toHaveText("Job selecionado: lote_boa_vista.geojson.");
  await expect.poll(() => polygon(page).getAttribute("d")).toBe(shapeA);
  expect(inputs).toHaveLength(2);
  expect(errors).toEqual([]);
});

test("troca rapida entre jobs nao mistura geometrias", async ({ page }) => {
  await stubTiles(page);
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  await page.route(new RegExp(`/jobs/${jobA}/input$`), async (route) => {
    await gate;
    return route.continue();
  });
  await page.goto("/");
  const message = page.getByTestId("preview-message");

  await row(page, jobA).click();
  await expect(message).toHaveText("Carregando a geometria de lote_boa_vista.geojson…");
  await expect(polygon(page)).toHaveCount(0);

  await row(page, jobB).click();
  await expect(message).toHaveText("Job selecionado: gleba_rural_exemplo.geojson.");
  await expectFitted(page);
  const shapeB = await polygon(page).getAttribute("d");

  const late = page.waitForResponse(new RegExp(`/jobs/${jobA}/input$`));
  release();
  await late;
  await page.evaluate(() => new Promise((r) => setTimeout(r, 300)));
  await expect(message).toHaveText("Job selecionado: gleba_rural_exemplo.geojson.");
  await expect(polygon(page)).toHaveCount(1);
  expect(await polygon(page).getAttribute("d")).toBe(shapeB);

  await row(page, jobA).click();
  await expect(message).toHaveText("Job selecionado: lote_boa_vista.geojson.");
  await expectFitted(page);
  expect(await polygon(page).getAttribute("d")).not.toBe(shapeB);
});

test("job falho mostra o motivo pela geometria original", async ({ page, request }) => {
  await stubTiles(page);
  await page.goto("/");
  const failed = await processFile(page, "autointersecao.geojson", "Falhou");
  const res = await request.get(`/jobs/${failed}/input`);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toBe("application/geo+json");

  await page.reload();
  await row(page, failed).click();
  const message = page.getByTestId("preview-message");
  await expect(message).toContainText("autointersecao.geojson: O polígono é inválido");
  await expect(message).toHaveAttribute("data-kind", "error");
  await expect(polygon(page)).toHaveCount(0);
  await expect(page.getByTestId("preview-metrics")).toBeHidden();
});

test("job inexistente e arquivo original ausente", async ({ page, request }) => {
  const missing = await request.get("/jobs/nao-existe/input");
  expect(missing.status()).toBe(404);
  expect((await missing.json()).detail).toBe("Job não encontrado");

  await stubTiles(page);
  // Mesma resposta que a API real dá quando o arquivo sumiu de /saida/inputs (coberto no pytest).
  await page.route(new RegExp(`/jobs/${jobA}/input$`), (route) =>
    route.fulfill({ status: 404, json: { detail: "Arquivo original não disponível" } }),
  );
  await page.goto("/");
  await row(page, jobA).click();
  const message = page.getByTestId("preview-message");
  await expect(message).toHaveText("O GeoJSON original deste job não está disponível no servidor.");
  await expect(polygon(page)).toHaveCount(0);
  await expect(page.getByTestId("preview-metrics")).toBeVisible(); // resultado.json continua valendo
});

test("falha de rede ao buscar a geometria permite tentar de novo", async ({ page }) => {
  await stubTiles(page);
  await page.route(new RegExp(`/jobs/${jobA}/input$`), (route) => route.abort());
  await page.goto("/");
  await row(page, jobA).click();
  const message = page.getByTestId("preview-message");
  await expect(message).toContainText("Não foi possível carregar a geometria deste job (API indisponível)");
  await expect(message).toHaveAttribute("data-kind", "error");

  await page.unroute(new RegExp(`/jobs/${jobA}/input$`));
  await row(page, jobB).click();
  await expectFitted(page);
  await row(page, jobA).click();
  await expect(message).toHaveText("Job selecionado: lote_boa_vista.geojson.");
  await expectFitted(page);
});

for (const [width, height] of [[1440, 900], [375, 812]]) {
  test(`historico com mapa ${width}x${height}`, async ({ page }) => {
    await stubTiles(page);
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await row(page, jobB).click();
    await expect(page.getByTestId("preview-message")).toHaveText("Job selecionado: gleba_rural_exemplo.geojson.");
    await expectFitted(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("job do historico mostra geometria e metricas depois de recarregar", async ({ page, request }) => {
  await stubTiles(page);
  await page.goto("/");
  await row(page, completedTaskId).click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(completedTaskId);
  await expect(page.getByTestId("preview-message")).toHaveText("Job selecionado: lote_simples.geojson.");
  await expectFitted(page);
  await expectMetricsOf(page, request, completedTaskId);

  // Selecionar um arquivo novo volta o painel para a pré-visualização do arquivo.
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("preview-message")).toContainText("gleba_rural_exemplo.geojson");
  await expect(page.getByTestId("preview-metrics")).toBeHidden();
  await expectFitted(page);
});

test("propriedades e nome maliciosos nao viram HTML", async ({ page }) => {
  await stubTiles(page);
  const data = JSON.parse(readFileSync(fixture("lote_simples.geojson"), "utf8"));
  data.features[0].properties = { nome: '<img src=x onerror="window.__xss=1">' };
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles({
    name: '<img src=x onerror="window.__xss=2">.geojson',
    mimeType: "application/geo+json",
    buffer: Buffer.from(JSON.stringify(data)),
  });
  await expect(page.getByTestId("preview-message")).toContainText("<img src=x");
  await expectFitted(page);
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
  // Única imagem própria do painel: a logo da prancha (sem src aqui).
  await expect(page.getByTestId("preview").locator("img:not(.leaflet-tile):not([data-testid=prancha-logo])")).toHaveCount(0);
  await expect(page.getByTestId("prancha-logo")).not.toHaveAttribute("src", /./);
});

test("falha ao carregar o Leaflet nao quebra a tela", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.route(/\/vendor\/leaflet\/leaflet\.js$/, (route) => route.abort());
  await page.goto("/");
  await expect(page.getByTestId("health")).toContainText("API online");
  await expect(page.getByTestId("jobs-row").first()).toBeVisible();

  await page.getByTestId("upload-input").setInputFiles(fixture("lote_simples.geojson"));
  await expect(page.getByTestId("preview-message")).toContainText("Mapa indisponível");
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  expect(errors).toEqual([]);
});

test("sem erros no console com mapa e metricas", async ({ page }) => {
  const errors = [];
  page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
  page.on("pageerror", (err) => errors.push(err.message));
  await stubTiles(page);
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expectFitted(page);
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  await expect(page.getByTestId("preview-metrics")).toBeVisible();
  expect(errors).toEqual([]);
});

for (const [width, height] of [[1440, 900], [375, 812]]) {
  test(`mapa responsivo ${width}x${height}`, async ({ page }) => {
    await stubTiles(page);
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
    await expectFitted(page);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const map = await page.getByTestId("preview-map").boundingBox();
    expect(map.x).toBeGreaterThanOrEqual(0);
    expect(map.x + map.width).toBeLessThanOrEqual(width);
    expect(map.height).toBeGreaterThanOrEqual(200);
  });
}
