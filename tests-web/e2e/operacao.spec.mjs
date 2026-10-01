import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const fixture = (name) => fileURLToPath(new URL(`../../worker/tests/fixtures/${name}`, import.meta.url));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const isEnqueue = (req) => req.method() === "POST" && new URL(req.url()).pathname === "/jobs/async";

// Jobs reais na API: os testes compartilham o task_id do envio bem-sucedido.
test.describe.configure({ mode: "serial" });

let completedTaskId;

async function submit(page, files) {
  await page.getByTestId("upload-input").setInputFiles(files);
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(UUID);
  return page.getByTestId("detail-task-id").textContent();
}

test("abre a tela", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/GeoLume/);
  const logo = page.locator('img[src$="geolume-logo-transparente.png"]');
  await expect(logo).toBeVisible();
  expect(await logo.evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByTestId("health")).toContainText("API online");
});

test("envia GeoJSON e acompanha ate concluir", async ({ page }) => {
  const polled = [];
  page.on("response", (res) => {
    if (/\/jobs\/[0-9a-f-]{36}$/.test(new URL(res.url()).pathname)) polled.push(res.status());
  });
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
  await expect(page.getByTestId("upload-file")).toContainText("gleba_rural_exemplo.geojson");
  await page.getByTestId("upload-submit").click();

  await expect(page.getByTestId("detail-job-id")).toHaveText(/^[0-9a-f]{32}$/);
  await expect(page.getByTestId("detail-task-id")).toHaveText(UUID);
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  expect(polled.length).toBeGreaterThan(0);
  expect(polled.every((s) => s === 200)).toBe(true);
  completedTaskId = await page.getByTestId("detail-task-id").textContent();
});

test("links dos arquivos", async ({ page, request }) => {
  await page.goto("/");
  await page.locator(`[data-testid="jobs-row"][data-task-id="${completedTaskId}"]`).click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(completedTaskId);
  const links = page.getByTestId("detail-links").locator("a");
  await expect(links).toHaveText(["mapa.pdf", "memorial.pdf", "resultado.json"]);

  const expected = ["application/pdf", "application/pdf", "application/json"];
  for (let i = 0; i < 3; i++) {
    const res = await request.get(await links.nth(i).getAttribute("href"));
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toContain(expected[i]);
  }
});

test("historico lista o job", async ({ page, request }) => {
  await page.goto("/");
  const row = page.locator(`[data-testid="jobs-row"][data-task-id="${completedTaskId}"]`);
  await expect(row).toContainText("Concluído");

  await expect
    .poll(async () => {
      const { jobs } = await (await request.get("/jobs?limit=20")).json();
      const count = (s) => String(jobs.filter((j) => j.status === s).length);
      const shown = (s) => page.getByTestId("summary").locator(`[data-status="${s}"] [data-count]`).textContent();
      return (await shown("completed")) === count("completed") && (await shown("failed")) === count("failed");
    })
    .toBe(true);
});

test("job com falha", async ({ page }) => {
  await page.goto("/");
  await submit(page, fixture("autointersecao.geojson"));
  await expect(page.getByTestId("detail-status")).toHaveText("Falhou", { timeout: 90_000 });
  await expect(page.getByTestId("detail-error")).not.toBeEmpty();
  // Quem lê vê o motivo, não o código interno ("geometria_invalida: ...").
  await expect(page.getByTestId("detail-error")).not.toHaveText(/^[a-z_]+:/);
});

test("selecionar job do historico", async ({ page }) => {
  await page.goto("/");
  await page.locator(`[data-testid="jobs-row"][data-task-id="${completedTaskId}"]`).click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(completedTaskId);

  const other = page.locator(`[data-testid="jobs-row"]:not([data-task-id="${completedTaskId}"])`).first();
  const otherId = await other.getAttribute("data-task-id");
  await other.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("detail-task-id")).toHaveText(otherId);
});

test("escape de nome malicioso", async ({ page, request }) => {
  const name = '<img src=x onerror="window.__xss=1">.geojson';
  await page.goto("/");
  const taskId = await submit(page, {
    name,
    mimeType: "application/geo+json",
    buffer: readFileSync(fixture("lote_simples.geojson")),
  });
  // O Chrome codifica '"' como %22 no nome do multipart; comparar com o nome gravado pela API.
  const { jobs } = await (await request.get("/jobs?limit=20")).json();
  const stored = jobs.find((j) => j.task_id === taskId).input_filename;
  expect(stored).toContain("<img src=x onerror=");
  const row = page.locator(`[data-testid="jobs-row"][data-task-id="${taskId}"]`);
  await expect(row).toContainText(stored);
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
  await expect(row.locator("img")).toHaveCount(0);
});

test("valida extensao", async ({ page }) => {
  let posts = 0;
  page.on("request", (req) => isEnqueue(req) && posts++);
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles({
    name: "dados.json",
    mimeType: "application/json",
    buffer: readFileSync(fixture("lote_simples.geojson")),
  });
  await expect(page.getByTestId("upload-message")).toHaveText("Selecione um arquivo .geojson");
  await expect(page.getByTestId("upload-submit")).toBeDisabled();
  expect(posts).toBe(0);
});

test("envio unico com duplo clique", async ({ page }) => {
  let posts = 0;
  page.on("request", (req) => isEnqueue(req) && posts++);
  await page.goto("/");
  await page.getByTestId("upload-input").setInputFiles(fixture("lote_simples.geojson"));
  await page.getByTestId("upload-submit").dblclick();
  await expect(page.getByTestId("detail-task-id")).toHaveText(UUID);
  expect(posts).toBe(1);
});

test("api indisponivel", async ({ page }) => {
  await page.route(/\/jobs\?limit=/, (route) => route.abort());
  await page.route(/\/health$/, (route) => route.fulfill({ status: 500, body: "erro" }));
  await page.goto("/");
  await expect(page.getByTestId("jobs-error")).toBeVisible();
  await expect(page.getByTestId("jobs-error").getByRole("button", { name: "Tentar novamente" })).toBeVisible();
  await expect(page.getByTestId("health")).toContainText("API indisponível");

  await page.unrouteAll();
  await page.getByTestId("jobs-error").getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.getByTestId("jobs-error")).toBeHidden();
  await expect(page.getByTestId("jobs-row").first()).toBeVisible();
});

test("resposta atrasada de consulta reiniciada nao volta o estado", async ({ page }) => {
  const taskId = "00000000-0000-4000-8000-000000000001";
  await page.route(/\/jobs\/async$/, (route) =>
    route.fulfill({ status: 202, json: { task_id: taskId, job_id: "f".repeat(32), status: "queued" } }),
  );
  let calls = 0;
  await page.route(new RegExp(`/jobs/${taskId}$`), async (route) => {
    calls += 1;
    if (calls === 1) {
      await new Promise((r) => setTimeout(r, 1500));
      return route.fulfill({ json: { task_id: taskId, status: "started" } });
    }
    return route.fulfill({ json: { task_id: taskId, status: "completed" } });
  });
  await page.goto("/");
  await submit(page, fixture("lote_simples.geojson"));
  await expect.poll(() => calls).toBe(1);

  // Aba oculta e visível de novo enquanto a 1ª consulta ainda está em andamento.
  const setHidden = (hidden) =>
    page.evaluate((h) => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => h });
      document.dispatchEvent(new Event("visibilitychange"));
    }, hidden);
  await setHidden(true);
  await setHidden(false);

  await expect(page.getByTestId("detail-status")).toHaveText("Concluído");
  await page.waitForTimeout(2500); // a resposta atrasada (started) chega depois
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído");
});

test("leitor de tela ouve so mudancas de estado", async ({ page }) => {
  const taskId = "00000000-0000-4000-8000-000000000002";
  await page.route(/\/jobs\/async$/, (route) =>
    route.fulfill({ status: 202, json: { task_id: taskId, job_id: "e".repeat(32), status: "queued" } }),
  );
  let status = "started";
  await page.route(new RegExp(`/jobs/${taskId}$`), (route) => route.fulfill({ json: { task_id: taskId, status } }));
  await page.goto("/");
  await submit(page, fixture("lote_simples.geojson"));
  await expect(page.getByTestId("detail-status")).toHaveText("Em execução");

  // Mutações dentro de regiões vivas do painel enquanto o estado não muda.
  await page.evaluate(() => {
    window.__liveMutations = 0;
    const live = '[aria-live], [role="status"], [role="alert"]';
    const detail = document.querySelector('[data-testid="detail"]');
    new MutationObserver((records) => {
      for (const r of records) {
        const node = r.target.nodeType === 1 ? r.target : r.target.parentElement;
        if (node?.closest(live)) window.__liveMutations += 1;
      }
    }).observe(detail, { subtree: true, childList: true, characterData: true, attributes: true });
  });
  await page.waitForTimeout(4500); // ao menos 2 consultas com o mesmo estado
  expect(await page.evaluate(() => window.__liveMutations)).toBe(0);

  status = "completed";
  await expect(page.getByTestId("detail").getByRole("status")).toHaveText("Estado: Concluído");
});

test("sem erros no console", async ({ page }) => {
  const errors = [];
  page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
  page.on("pageerror", (err) => errors.push(err.message));
  await page.goto("/");
  await expect(page.getByTestId("health")).toContainText("API online");
  await submit(page, fixture("lote_simples.geojson"));
  await expect(page.getByTestId("detail-status")).toHaveText("Concluído", { timeout: 90_000 });
  await expect(page.getByTestId("detail-links").locator("a")).toHaveCount(3);
  expect(errors).toEqual([]);
});

for (const [width, height] of [[1440, 900], [768, 1024], [375, 812]]) {
  test(`layout sem rolagem horizontal ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const firstRow = page.getByTestId("jobs-row").first();
    await expect(firstRow).toBeVisible();

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);

    for (const target of [page.getByTestId("upload-submit"), firstRow]) {
      const box = await target.boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
    }

    const detail = await page.getByTestId("detail").boundingBox();
    const table = await page.getByTestId("jobs-table").boundingBox();
    // O redesign aprovado deixa o histórico compacto abaixo da área de trabalho
    // também no desktop; ele não ocupa mais uma terceira coluna lateral.
    expect(table.y).toBeGreaterThan(detail.y + detail.height - 1);
  });
}

// ---- Varredura de UX: alvos de toque de 44 px no celular e no tablet ----------------------

for (const [width, height] of [[768, 1024], [390, 844], [320, 640]]) {
  test(`controles secundários com alvo de toque de 44 px ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await page.locator(`[data-testid="jobs-row"][data-task-id="${completedTaskId}"] td`).first().click();
    await expect(page.getByTestId("detail-status")).toHaveText("Concluído");
    await page.locator(".detalhes-tecnicos summary").click();
    await page.getByTestId("upload-input").setInputFiles(fixture("gleba_rural_exemplo.geojson"));
    await expect(page.getByTestId("map-tools")).toBeVisible();

    const alvos = {
      sair: page.getByTestId("logout"),
      atualizar: page.getByTestId("jobs-refresh"),
      outrasCamadas: page.getByTestId("camadas-mais").locator("summary"),
      detalhesTecnicos: page.locator(".detalhes-tecnicos summary"),
      copiar: page.locator("[data-copy]").first(),
    };
    // Tablet: tabela com a coluna Documentos. Celular: o cartão inteiro abre o job, sem links no meio dele.
    const linha = page.locator(`[data-testid="jobs-row"][data-task-id="${completedTaskId}"]`);
    if (width >= 640) alvos.linkHistorico = linha.locator(".row-links a").first();
    else {
      alvos.cartaoHistorico = linha;
      await expect(linha.locator(".row-links")).toBeHidden();
    }
    for (const [nome, alvo] of Object.entries(alvos)) {
      const box = await alvo.boundingBox();
      expect(box?.height, nome).toBeGreaterThanOrEqual(44);
    }
  });
}
