import { test, expect, request as playwrightRequest } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Camadas do mapa: mapa-base, polígono, transparência, enquadrar, legenda e falha dos tiles.
test.describe.configure({ mode: "serial" });

const AUTH = (name) => fileURLToPath(new URL(`../.auth/${name}`, import.meta.url));
const fixture = (name) => fileURLToPath(new URL(`../../worker/tests/fixtures/${name}`, import.meta.url));
const CSRF = { "X-GeoLume-CSRF": "1" };
const TILE = /^https:\/\/tile\.openstreetmap\.org\//;
const BLANK_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
  "base64",
);
const TILES_FALHARAM = "Mapa-base indisponível no momento; o polígono continua visível.";

async function stubTiles(page) {
  const tiles = [];
  await page.route(TILE, (route) => {
    tiles.push(route.request().url());
    return route.fulfill({ status: 200, contentType: "image/png", body: BLANK_PNG });
  });
  return tiles;
}

/** Toda requisição que sai do localhost (tiles e qualquer outro provedor). */
function externas(page) {
  const urls = [];
  page.on("request", (req) => {
    if (!new URL(req.url()).hostname.match(/^(localhost|127\.0\.0\.1)$/)) urls.push(req.url());
  });
  return urls;
}

const polygon = (page) => page.getByTestId("preview-map").locator("path.geolume-poligono");
const tilesNaTela = (page) => page.getByTestId("preview-map").locator("img.leaflet-tile");
const row = (page, taskId) => page.locator(`[data-testid="jobs-row"][data-task-id="${taskId}"]`);
const legenda = (page) => page.getByTestId("map-legend").locator("li");

async function comGeometria(page, nome = "gleba_rural_exemplo.geojson") {
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture(nome));
  await expect(polygon(page)).toHaveCount(1);
}

/** O polígono está dentro do mapa e ocupa boa parte dele. */
async function expectFitted(page) {
  await expect
    .poll(async () => {
      const map = await page.getByTestId("preview-map").boundingBox();
      const shape = await polygon(page).boundingBox();
      if (!map || !shape) return "sem caixa";
      const inside = shape.x >= map.x - 1 && shape.y >= map.y - 1 &&
        shape.x + shape.width <= map.x + map.width + 1 && shape.y + shape.height <= map.y + map.height + 1;
      const fills = shape.width >= map.width * 0.4 || shape.height >= map.height * 0.4;
      return inside && fills ? "ok" : JSON.stringify({ map, shape });
    })
    .toBe("ok");
}

/** Afasta o zoom N vezes; espera cada passo, pois o Leaflet ignora zoom durante a animação. */
async function afastar(page, vezes = 4) {
  for (let i = 0; i < vezes; i += 1) {
    const antes = (await polygon(page).boundingBox())?.width ?? 0;
    await page.locator(".leaflet-control-zoom-out").click();
    await expect
      .poll(async () => {
        const animando = await page.getByTestId("preview-map").locator(".leaflet-zoom-anim").count();
        const largura = (await polygon(page).boundingBox())?.width ?? 0;
        return animando === 0 && largura < antes * 0.6;
      })
      .toBe(true);
  }
}

let jobs = [];

test.beforeAll(async ({ baseURL }) => {
  // Dois jobs concluídos do usuário A para a troca rápida pelo histórico.
  const a = await playwrightRequest.newContext({ baseURL, storageState: AUTH("a.json") });
  for (const nome of ["lote_boa_vista.geojson", "gleba_rural_exemplo.geojson"]) {
    const res = await a.post("/jobs/async", {
      headers: CSRF,
      multipart: { file: { name: nome, mimeType: "application/geo+json", buffer: readFileSync(fixture(nome)) } },
    });
    expect(res.status()).toBe(202);
    jobs.push((await res.json()).task_id);
  }
  for (const taskId of jobs) {
    await expect.poll(async () => (await (await a.get(`/jobs/${taskId}`)).json()).status, { timeout: 90_000 }).toBe("completed");
  }
  await a.dispose();
});

test("sem geometria o painel de camadas fica oculto e nenhum tile é pedido", async ({ page }) => {
  const tiles = await stubTiles(page);
  await page.goto("/");
  await expect(page.getByTestId("app")).toBeVisible();
  await expect(page.getByTestId("map-tools")).toBeHidden();
  expect(tiles).toHaveLength(0);
});

test("padrão: mapa de ruas do OSM, polígono visível, alfa do mapa.pdf e legenda com o aviso", async ({ page }) => {
  const tiles = await stubTiles(page);
  await comGeometria(page);
  await expect(page.getByTestId("map-tools")).toBeVisible();
  await expect(page.getByRole("group", { name: "Camadas do mapa" })).toBeVisible();
  await expect(page.getByLabel("Mapa de ruas (OpenStreetMap)")).toBeChecked();
  await expect(page.getByLabel("Sem mapa-base")).not.toBeChecked();
  await expect(page.getByLabel("Polígono do imóvel")).toBeChecked();
  await expect(page.getByTestId("polygon-opacity")).toHaveCount(0); // a opacidade existe só na prancha
  await expect(polygon(page)).toHaveAttribute("fill-opacity", String(89 / 255));
  await expect(legenda(page)).toHaveText(["Mapa-base: Mapa de ruas (OpenStreetMap)", "Somente na pré-visualização — não entra no PDF", "Polígono do imóvel"]);
  await expect.poll(() => tiles.length).toBeGreaterThan(0);
  await expect(page.locator(".leaflet-control-attribution")).toContainText("OpenStreetMap");
  await expect(page.getByTestId("map-tiles-status")).toHaveText("");
});

test("panes: tiles em geolume-base, polígono em geolume-poligono, acima do mapa-base", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  await expect(tilesNaTela(page).first()).toBeAttached();
  const mapa = page.getByTestId("preview-map");
  await expect(mapa.locator(".leaflet-geolume-poligono-pane path.geolume-poligono")).toHaveCount(1);
  await expect(mapa.locator(".leaflet-geolume-base-pane img.leaflet-tile").first()).toBeAttached();
  const z = (pane) => mapa.locator(`.leaflet-${pane}-pane`).evaluate((el) => Number(getComputedStyle(el).zIndex));
  expect(await z("geolume-poligono")).toBeGreaterThan(await z("geolume-base"));
  // Contorno visível: opaco e com espessura do estilo padrão.
  await expect(polygon(page)).toHaveAttribute("stroke-opacity", "1");
  await expect(polygon(page)).toHaveAttribute("stroke-width", "3");
});

test("satélite aparece desabilitado e não faz nenhuma requisição", async ({ page }) => {
  await stubTiles(page);
  const saidas = externas(page);
  await comGeometria(page);
  const satelite = page.locator('[data-camada-id="satelite"] input');
  await expect(satelite).toBeDisabled();
  await satelite.click({ force: true });
  await expect(satelite).not.toBeChecked();
  await expect(page.getByLabel("Mapa de ruas (OpenStreetMap)")).toBeChecked();
  await page.waitForTimeout(500);
  expect(saidas.every((url) => TILE.test(url))).toBe(true);
});

test("sem mapa-base remove os tiles, para de pedir tiles e mantém o polígono", async ({ page }) => {
  const tiles = await stubTiles(page);
  await comGeometria(page);
  await expect.poll(() => tiles.length).toBeGreaterThan(0);

  await page.getByLabel("Sem mapa-base").check();
  await expect(tilesNaTela(page)).toHaveCount(0);
  await expect(polygon(page)).toHaveCount(1);
  await expect(legenda(page).first()).toHaveText("Mapa-base: nenhum");
  const antes = tiles.length;
  await page.locator(".leaflet-control-zoom-out").click();
  await page.locator(".leaflet-control-zoom-in").click();
  await page.waitForTimeout(500);
  expect(tiles.length).toBe(antes);

  await page.getByLabel("Mapa de ruas (OpenStreetMap)").check();
  await expect.poll(() => tiles.length).toBeGreaterThan(antes);
  await expect(tilesNaTela(page).first()).toBeAttached();
});

test("ocultar e mostrar o polígono atualiza mapa e legenda", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  await page.getByLabel("Polígono do imóvel").uncheck();
  await expect(polygon(page)).toHaveCount(0);
  await expect(legenda(page)).toHaveText(["Mapa-base: Mapa de ruas (OpenStreetMap)", "Somente na pré-visualização — não entra no PDF"]);
  await page.getByLabel("Polígono do imóvel").check();
  await expect(polygon(page)).toHaveCount(1);
  await expect(legenda(page)).toHaveCount(3);
});

test("transparência muda só o preenchimento; o contorno continua visível", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  await page.getByTestId("prancha-form").locator("summary").click();
  const slider = page.getByTestId("prancha-input-alfa");
  await slider.fill("0");
  await expect(page.getByTestId("prancha-alfa-valor")).toHaveText("0%");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", "0");
  await expect(polygon(page)).toHaveAttribute("stroke-opacity", "1");
  await expect(polygon(page)).toHaveAttribute("stroke-width", "3");
  await expect(legenda(page).last()).toHaveText("Polígono do imóvel");
  await slider.fill("100");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", "1");
});

test("enquadrar polígono volta a geometria para a tela", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  await expectFitted(page);
  const enquadrado = (await polygon(page).boundingBox()).width;
  await afastar(page);
  expect((await polygon(page).boundingBox()).width).toBeLessThan(enquadrado / 4);
  await page.getByRole("button", { name: "Enquadrar polígono" }).click();
  await expectFitted(page);
});

test("controles funcionam pelo teclado", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  await page.getByLabel("Mapa de ruas (OpenStreetMap)").focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByLabel("Sem mapa-base")).toBeChecked(); // satélite desabilitado fica fora da navegação
  await expect(tilesNaTela(page)).toHaveCount(0);

  await page.getByLabel("Polígono do imóvel").focus();
  await page.keyboard.press("Space");
  await expect(polygon(page)).toHaveCount(0);
  await page.keyboard.press("Space");
  await expect(polygon(page)).toHaveCount(1);

  await afastar(page);
  await page.getByRole("button", { name: "Enquadrar polígono" }).focus();
  await page.keyboard.press("Enter");
  await expectFitted(page);
});

test("falha dos tiles avisa, mantém o polígono e não afeta o processamento nem os downloads", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.route(TILE, (route) => route.abort());
  await comGeometria(page, "lote_simples.geojson");
  await expect(page.getByTestId("map-tiles-status")).toHaveText(TILES_FALHARAM);
  await expect(legenda(page).first()).toHaveText("Mapa-base: Mapa de ruas (OpenStreetMap) — indisponível");
  await expect(polygon(page)).toHaveCount(1);
  await expect(page.getByTestId("preview-message")).toContainText("polígono pronto para processar");

  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  const links = page.getByTestId("detail").locator("a");
  await expect(links).toHaveCount(3);
  const res = await page.request.get(await links.first().getAttribute("href"));
  expect(res.status()).toBe(200);

  // Sem mapa-base o aviso some: não há o que carregar.
  await page.getByLabel("Sem mapa-base").check();
  await expect(page.getByTestId("map-tiles-status")).toHaveText("");
  expect(errors).toEqual([]);
});

test("troca rápida de job mantém as escolhas e não mistura geometrias", async ({ page }) => {
  await stubTiles(page);
  await page.goto("/");
  const [jobA, jobB] = jobs;
  await row(page, jobA).click();
  await expect(polygon(page)).toHaveCount(1);
  await page.getByLabel("Sem mapa-base").check();
  const shapeA = await polygon(page).getAttribute("d");

  await row(page, jobB).click();
  await row(page, jobA).click();
  await row(page, jobB).click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(jobB);
  await expect(page.getByTestId("preview-message")).toHaveText("Job selecionado: gleba_rural_exemplo.geojson.");
  await expect(polygon(page)).toHaveCount(1);
  await expect.poll(() => polygon(page).getAttribute("d")).not.toBe(shapeA);
  await expect(polygon(page)).toHaveAttribute("fill-opacity", String(89 / 255)); // job sem prancha: alfa padrão
  await expect(page.getByLabel("Sem mapa-base")).toBeChecked();
  await expect(tilesNaTela(page)).toHaveCount(0);

  await page.getByLabel("Polígono do imóvel").uncheck();
  await row(page, jobA).click();
  await expect(page.getByTestId("preview-message")).toHaveText("Job selecionado: lote_boa_vista.geojson.");
  await expect(polygon(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Enquadrar polígono" }).click(); // enquadra mesmo oculto
  await page.getByLabel("Polígono do imóvel").check();
  await expect.poll(() => polygon(page).getAttribute("d")).toBe(shapeA);
  await expectFitted(page);
});

test("logout volta as camadas ao padrão", async ({ browser, baseURL }) => {
  const { users } = JSON.parse(readFileSync(AUTH("users.json"), "utf8"));
  // Sessão própria: o logout não derruba a sessão compartilhada dos outros testes.
  const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await stubTiles(page);
  await page.goto("/");
  const entrar = async () => {
    await page.getByTestId("login-email").fill(users.a.email);
    await page.getByTestId("login-password").fill(users.a.password);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("app")).toBeVisible();
  };
  await entrar();
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  await page.getByLabel("Sem mapa-base").check();
  await page.getByLabel("Polígono do imóvel").uncheck();
  await page.getByTestId("prancha-form").locator("summary").click();
  await page.getByTestId("prancha-input-alfa").fill("75");

  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.getByTestId("map-tools")).toBeHidden();
  await entrar();
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(polygon(page)).toHaveCount(1);
  await expect(page.getByLabel("Mapa de ruas (OpenStreetMap)")).toBeChecked();
  await expect(page.getByLabel("Polígono do imóvel")).toBeChecked();
  await expect(page.getByTestId("prancha-input-alfa")).toHaveValue("35");
  await expect(polygon(page)).toHaveAttribute("fill-opacity", String(89 / 255)); // alfa8(0.35) / 255, igual ao mapa.pdf
  await expect(tilesNaTela(page).first()).toBeAttached();
  await context.close();
});

test("falha ao carregar o Leaflet mantém o painel de camadas oculto", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.route(/\/vendor\/leaflet\/leaflet\.js$/, (route) => route.abort());
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture("lote_simples.geojson"));
  await expect(page.getByTestId("preview-message")).toContainText("Mapa indisponível");
  await expect(page.getByTestId("map-tools")).toBeHidden();
  await expect(page.getByTestId("upload-submit")).toBeEnabled();
  expect(errors).toEqual([]);
});

for (const [width, height] of [[1440, 900], [390, 844], [320, 640]]) {
  test(`camadas sem rolagem horizontal e com alvos de toque ${width}x${height}`, async ({ page }) => {
    const errors = [];
    page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
    await stubTiles(page);
    await page.setViewportSize({ width, height });
    await comGeometria(page);
    await expect(page.getByTestId("map-tools")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const alvos = page.getByTestId("map-tools").locator("label, button, input[type=range]");
    for (let i = 0; i < (await alvos.count()); i += 1) {
      const box = await alvos.nth(i).boundingBox();
      expect(box.x, `alvo ${i}`).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, `alvo ${i}`).toBeLessThanOrEqual(width);
      expect(box.height, `alvo ${i}`).toBeGreaterThanOrEqual(44);
    }
    expect(errors).toEqual([]);
  });
}

// ---- Task 7: catálogo real (GET /camadas) no painel ----------------------------------

const CATALOGO = JSON.parse(readFileSync(fixture("catalogo_publico.json"), "utf-8"));
const CAMADAS = /\/camadas$/;
const item = (page, id) => page.locator(`[data-testid="camada-item"][data-camada-id="${id}"]`);
const idsDo = (page, grupo) => page.getByTestId(grupo).getByTestId("camada-item").evaluateAll((els) => els.map((e) => e.dataset.camadaId));
const ERRO_CATALOGO = "Camadas indisponíveis no momento. O polígono continua visível, sem mapa-base.";

test("painel mostra três grupos e satélite desabilitado com aviso", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  const painel = page.getByTestId("camadas-painel");
  await expect(painel).toBeVisible();
  await expect(page.getByTestId("camadas-erro")).toBeHidden();
  await expect(page.getByTestId("camadas-grupo-disponiveis")).toContainText("Disponíveis");
  await expect(page.getByTestId("camadas-grupo-fonte-oficial")).toContainText("Dependem de fonte oficial");
  await expect(page.getByTestId("camadas-grupo-planejadas")).toContainText("Planejadas");
  expect(await idsDo(page, "camadas-grupo-disponiveis")).toEqual(["ruas_osm", "nenhum", "poligono"]);
  expect(await idsDo(page, "camadas-grupo-fonte-oficial")).toEqual(["satelite", "hidrografia", "rodovias", "limites_municipais"]);
  expect(await idsDo(page, "camadas-grupo-planejadas")).toEqual(["topografia", "edificacoes"]);

  await expect(item(page, "satelite")).toHaveAttribute("aria-disabled", "true");
  await expect(item(page, "satelite").locator("input")).toBeDisabled();
  await expect(item(page, "satelite")).toContainText("Sem provedor licenciado");
  for (const id of ["limites_municipais", "hidrografia", "rodovias"]) {
    await expect(item(page, id)).toHaveAttribute("aria-disabled", "true");
    await expect(item(page, id)).toContainText("Fonte oficial não definida");
  }
  for (const id of ["topografia", "edificacoes"]) {
    await expect(item(page, id)).toHaveAttribute("aria-disabled", "true");
    await expect(item(page, id)).toContainText("Planejada");
  }
  for (const id of ["ruas_osm", "nenhum", "poligono"]) await expect(item(page, id)).toHaveAttribute("aria-disabled", "false");
  // Nenhum endereço de tile no DOM do painel: só a camada ativa vira requisição.
  expect(await painel.evaluate((el) => el.innerHTML)).not.toMatch(/https?:|\{z\}/);
});

test("ruas OSM com aviso de pré-visualização e atribuição só enquanto ativa", async ({ page }) => {
  await stubTiles(page);
  await comGeometria(page);
  await expect(item(page, "ruas_osm")).toContainText("Somente na pré-visualização — não entra no PDF");
  const atribuicao = page.getByTestId("preview-map").locator(".leaflet-control-attribution");
  const link = atribuicao.locator('a[href="https://www.openstreetmap.org/copyright"]');
  await expect(link).toHaveText("© Contribuidores do OpenStreetMap");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await page.getByLabel("Sem mapa-base").check();
  await expect(atribuicao).not.toContainText("OpenStreetMap");
  await page.getByLabel("Mapa de ruas (OpenStreetMap)").check();
  await expect(atribuicao).toContainText("© Contribuidores do OpenStreetMap");
});

const FALHAS = {
  "500": (route) => route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"erro"}' }),
  "resposta inválida": (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"camadas":"x"}' }),
  "corpo que não é JSON": (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<html>oops</html>" }),
  "catálogo vazio": (route) => route.fulfill({ status: 200, contentType: "application/json", body: '{"camadas":[],"estilos":[],"alfa_padrao":0.35}' }),
  "falha de rede": (route) => route.abort(),
};

for (const [nome, responder] of Object.entries(FALHAS)) {
  test(`catálogo com ${nome} ⇒ camadas-erro, polígono desenhado e zero requisições de tile`, async ({ page }) => {
    const tiles = await stubTiles(page);
    const saidas = externas(page);
    await page.route(CAMADAS, responder);
    await comGeometria(page);
    await expect(page.getByTestId("camadas-erro")).toBeVisible();
    await expect(page.getByTestId("camadas-erro")).toHaveText(ERRO_CATALOGO);
    await expect(polygon(page)).toHaveCount(1);
    await expect(polygon(page)).toHaveAttribute("stroke-width", "3");
    await expect(page.getByLabel("Sem mapa-base")).toBeChecked();
    await expect(item(page, "ruas_osm")).toHaveCount(0);
    await expect(legenda(page).first()).toHaveText("Mapa-base: nenhum");
    await page.waitForTimeout(500);
    expect(tiles).toHaveLength(0);
    expect(saidas).toEqual([]);
    await expect(page.getByTestId("app")).toBeVisible(); // continua logado
  });
}

test("catálogo com 401 segue o fluxo de sessão expirada, sem aviso de catálogo", async ({ page }) => {
  await stubTiles(page);
  await page.route(CAMADAS, (route) => route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"Autenticação necessária"}' }));
  await page.goto("/");
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.getByTestId("login-message")).toHaveText("Sua sessão expirou. Entre novamente.");
  await expect(page.getByTestId("camadas-erro")).toBeHidden();
  await expect(page.getByTestId("camadas-erro")).toHaveText("");
});

test("catálogo só é pedido depois da autenticação", async ({ browser, baseURL }) => {
  const { users } = JSON.parse(readFileSync(AUTH("users.json"), "utf8"));
  const context = await browser.newContext({ baseURL, storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await stubTiles(page);
  const pedidos = [];
  page.on("request", (req) => CAMADAS.test(new URL(req.url()).pathname) && pedidos.push(req.url()));
  await page.goto("/");
  await expect(page.getByTestId("login")).toBeVisible();
  await page.waitForTimeout(300);
  expect(pedidos).toHaveLength(0);
  await page.getByTestId("login-email").fill(users.a.email);
  await page.getByTestId("login-password").fill(users.a.password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("app")).toBeVisible();
  await expect.poll(() => pedidos.length).toBe(1);
  await context.close();
});

test("catálogo com HTML nos textos vira texto; atribuição escapada no Leaflet", async ({ page }) => {
  await stubTiles(page);
  const corpo = structuredClone(CATALOGO);
  const osm = corpo.camadas.find((c) => c.id === "ruas_osm");
  osm.nome = `<img src=x onerror="window.__xss=1">Ruas`;
  osm.aviso = `<b onmouseover="window.__xss=2">aviso</b>`;
  osm.fonte.atribuicao = { texto: `<img src=x onerror="window.__xss=3">OSM`, url: "javascript:alert(1)" };
  corpo.estilos[1].nome = `<svg onload="window.__xss=4">`;
  await page.route(CAMADAS, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(corpo) }));
  await comGeometria(page);
  await expect(item(page, "ruas_osm")).toContainText(`<img src=x onerror="window.__xss=1">Ruas`);
  await expect(item(page, "ruas_osm")).toContainText(`<b onmouseover="window.__xss=2">aviso</b>`);
  const atribuicao = page.getByTestId("preview-map").locator(".leaflet-control-attribution");
  await expect(atribuicao).toContainText(`<img src=x onerror="window.__xss=3">OSM`);
  await expect(atribuicao.locator("img, a[href^='javascript']")).toHaveCount(0);
  await expect(page.locator(`[data-testid="estilo-card"][data-estilo="tecnico"]`)).toContainText(`<svg onload="window.__xss=4">`);
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
  expect(await page.locator("img[src=x], svg[onload]").count()).toBe(0);
});
