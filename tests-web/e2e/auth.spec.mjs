import { test, expect, request as playwrightRequest } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Contexto anônimo: cada teste entra (ou não) por conta própria.
test.use({ storageState: { cookies: [], origins: [] } });
test.describe.configure({ mode: "serial" });

const AUTH = (name) => fileURLToPath(new URL(`../.auth/${name}`, import.meta.url));
const { users, legacyTaskId } = JSON.parse(readFileSync(AUTH("users.json"), "utf8"));
const fixture = (name) => fileURLToPath(new URL(`../../worker/tests/fixtures/${name}`, import.meta.url));
const CSRF = { "X-GeoLume-CSRF": "1" };
const EXPIRED = "Sua sessão expirou. Entre novamente.";

async function asUser(baseURL, key) {
  return playwrightRequest.newContext({ baseURL, storageState: AUTH(`${key}.json`) });
}

async function login(page, { email, password }) {
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
}

/** Os 5 recursos de um job; todos exigem dono ou administrador. */
const RESOURCES = (id) => [`/jobs/${id}`, `/jobs/${id}/files/mapa`, `/jobs/${id}/files/memorial`, `/jobs/${id}/files/resultado`, `/jobs/${id}/input`];

let taskOfB;

test("sem login mostra só o formulário e a API responde 401", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.getByTestId("app")).toBeHidden();
  await expect(page.getByTestId("logout")).toBeHidden();
  await expect(page.getByTestId("health")).toContainText("API online"); // /health continua público

  for (const url of ["/jobs", "/auth/me", ...RESOURCES("qualquer")]) {
    expect((await request.get(url)).status(), url).toBe(401);
  }
  const file = { name: "lote.geojson", mimeType: "application/geo+json", buffer: Buffer.from("{}") };
  // Upload sem sessão é recusado antes do corpo, com ou sem o header de CSRF.
  for (const url of ["/jobs/async", "/jobs"]) {
    expect((await request.post(url, { headers: CSRF, multipart: { file } })).status(), url).toBe(401);
    expect((await request.post(url, { multipart: { file } })).status(), `${url} sem CSRF`).toBe(401);
  }
  const health = await (await request.get("/health")).json();
  expect(health).toEqual({ status: "ok", service: "geolume-worker" });
});

test("senha errada mostra erro genérico", async ({ page }) => {
  await page.goto("/");
  await login(page, { email: "ninguem@geolume.test", password: "nao-existe-12345" });
  await expect(page.getByTestId("login-message")).toHaveText("E-mail ou senha inválidos");
  await login(page, { email: users.b.email, password: "senha-errada-123" });
  await expect(page.getByTestId("login-message")).toHaveText("E-mail ou senha inválidos");
  await expect(page.getByTestId("app")).toBeHidden();
});

test("login e logout", async ({ page }) => {
  await page.goto("/");
  await login(page, users.a);
  await expect(page.getByTestId("app")).toBeVisible();
  await expect(page.getByTestId("login")).toBeHidden();
  await expect(page.getByTestId("session-user")).toHaveText(users.a.email);
  await expect(page.getByTestId("login-password")).toHaveValue(""); // senha não fica no campo

  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.getByTestId("app")).toBeHidden();
  expect((await page.request.get("/jobs")).status()).toBe(401);
  expect((await page.context().cookies()).find((c) => c.name === "geolume_session")).toBeUndefined();
});

test("cookie HttpOnly, Secure, SameSite=Lax", async ({ page }) => {
  await page.goto("/");
  await login(page, users.a);
  await expect(page.getByTestId("app")).toBeVisible();
  const cookie = (await page.context().cookies()).find((c) => c.name === "geolume_session");
  expect(cookie).toMatchObject({ httpOnly: true, secure: true, sameSite: "Lax", path: "/" });
  expect(await page.evaluate(() => document.cookie)).not.toContain("geolume_session");
});

test("A não vê job de B e a URL de B responde 404", async ({ page, baseURL }) => {
  const b = await asUser(baseURL, "b");
  const res = await b.post("/jobs/async", {
    headers: CSRF,
    multipart: { file: { name: "lote_de_b.geojson", mimeType: "application/geo+json", buffer: readFileSync(fixture("lote_simples.geojson")) } },
  });
  expect(res.status()).toBe(202);
  taskOfB = (await res.json()).task_id;
  expect((await b.get(`/jobs/${taskOfB}`)).status()).toBe(200);
  await b.dispose();

  const a = await asUser(baseURL, "a");
  for (const url of RESOURCES(taskOfB)) {
    const r = await a.get(url);
    expect(r.status(), url).toBe(404);
    expect(await r.json(), url).toEqual({ detail: "Job não encontrado" });
  }
  const { jobs } = await (await a.get("/jobs?limit=100")).json();
  expect(jobs.some((j) => j.task_id === taskOfB)).toBe(false);
  await a.dispose();

  await page.goto("/");
  await login(page, users.a);
  await expect(page.getByTestId("app")).toBeVisible();
  await expect(page.locator(`[data-task-id="${taskOfB}"]`)).toHaveCount(0);
});

test("admin vê jobs legados; membro não", async ({ baseURL }) => {
  expect(legacyTaskId, "há jobs legados no banco").toBeTruthy();
  const admin = await asUser(baseURL, "admin");
  const a = await asUser(baseURL, "a");
  expect((await admin.get(`/jobs/${legacyTaskId}`)).status()).toBe(200);
  expect((await admin.get(`/jobs/${legacyTaskId}/input`)).status()).not.toBe(401);
  expect((await admin.get(`/jobs/${taskOfB}`)).status()).toBe(200); // admin vê job de membro
  for (const url of RESOURCES(legacyTaskId)) {
    expect((await a.get(url)).status(), url).toBe(404);
  }
  const { jobs } = await (await a.get("/jobs?limit=100")).json();
  expect(jobs.some((j) => j.task_id === legacyTaskId)).toBe(false);
  await admin.dispose();
  await a.dispose();
});

test("sessão revogada no meio do uso volta ao login e limpa mapa e histórico", async ({ page, baseURL }) => {
  await page.route("https://tile.openstreetmap.org/**", (route) => route.fulfill({ status: 204 }));
  await page.goto("/");
  await login(page, users.a);
  await expect(page.getByTestId("app")).toBeVisible();

  // A envia um job; mapa, detalhe e histórico ficam com dados dele.
  await page.getByTestId("upload-input").setInputFiles(fixture("lote_simples.geojson"));
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(/^[0-9a-f-]{36}$/);
  const taskOfA = await page.getByTestId("detail-task-id").textContent();
  await expect(page.getByTestId("preview-map")).toBeVisible();
  await expect(page.locator(`[data-testid="jobs-row"][data-task-id="${taskOfA}"]`)).toBeVisible({ timeout: 15_000 });

  // Outro contexto com o mesmo cookie encerra a sessão.
  const other = await playwrightRequest.newContext({ baseURL, storageState: await page.context().storageState() });
  expect((await other.post("/auth/logout", { headers: CSRF })).status()).toBe(204);
  await other.dispose();

  await expect(page.getByTestId("login")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("login-message")).toHaveText(EXPIRED);
  await expect(page.getByTestId("app")).toBeHidden();
  await expect(page.getByTestId("jobs-row")).toHaveCount(0);
  await expect(page.getByTestId("preview-map")).toBeHidden();
  await expect(page.getByTestId("detail-task-id")).toHaveText("");

  // B entra na mesma aba: nada de A aparece.
  await login(page, users.b);
  await expect(page.getByTestId("session-user")).toHaveText(users.b.email);
  await expect(page.locator(`[data-testid="jobs-row"][data-task-id="${taskOfB}"]`)).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(`[data-task-id="${taskOfA}"]`)).toHaveCount(0);
  await expect(page.getByTestId("detail-task-id")).toHaveText("");
  await expect(page.getByTestId("preview-map")).toBeHidden();
  await expect(page.getByTestId("upload-file")).toHaveText("Nenhum arquivo selecionado");
});

test("resposta atrasada depois do logout não restaura dados antigos", async ({ page }) => {
  await page.route("https://tile.openstreetmap.org/**", (route) => route.fulfill({ status: 204 }));
  await page.goto("/");
  await login(page, users.a);
  await page.getByTestId("upload-input").setInputFiles(fixture("lote_simples.geojson"));
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("detail-task-id")).toHaveText(/^[0-9a-f-]{36}$/);
  const taskOfA = await page.getByTestId("detail-task-id").textContent();
  await expect(page.locator(`[data-testid="jobs-row"][data-task-id="${taskOfA}"]`)).toBeVisible({ timeout: 15_000 });

  // A partir daqui, toda consulta de jobs recebe a resposta real (com dados de A), mas só depois do logout.
  let release;
  const held = new Promise((resolve) => (release = resolve));
  const seguradas = [];
  const entregues = [];
  await page.route(/\/jobs(\?|\/|$)/, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const resposta = await route.fetch();
    seguradas.push(new URL(route.request().url()).pathname);
    await held;
    await route.fulfill({ response: resposta });
    entregues.push(1);
  });
  await expect.poll(() => seguradas.includes("/jobs"), { timeout: 12_000 }).toBe(true); // histórico em voo

  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login")).toBeVisible();
  release();
  await expect.poll(() => entregues.length).toBe(seguradas.length);
  await page.evaluate(() => new Promise((r) => setTimeout(r, 300)));

  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.getByTestId("app")).toBeHidden();
  await expect(page.getByTestId("session-user")).toHaveText("");
  await expect(page.getByTestId("jobs-row")).toHaveCount(0);
  await expect(page.getByTestId("detail-task-id")).toHaveText("");
  await expect(page.getByTestId("preview-map")).toBeHidden();

  // B entra na mesma aba e nada de A reaparece.
  await login(page, users.b);
  await expect(page.getByTestId("session-user")).toHaveText(users.b.email);
  await expect(page.getByTestId("jobs-row").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(`[data-task-id="${taskOfA}"]`)).toHaveCount(0);
});

// Revisão Codex (refutada): não há como entrar com o GET /auth/me da abertura ainda
// pendente, porque o formulário só aparece depois dessa resposta. Este teste fixa isso.
test("formulário de login só aparece depois da resposta de /auth/me", async ({ page }) => {
  let release;
  const held = new Promise((resolve) => (release = resolve));
  await page.route("**/auth/me", async (route) => {
    await held;
    await route.continue();
  });
  await page.goto("/");
  await expect(page.getByTestId("health")).toContainText("API online");
  await expect(page.getByTestId("login")).toBeHidden();
  await expect(page.getByTestId("app")).toBeHidden();
  release();
  await expect(page.getByTestId("login")).toBeVisible();
});

for (const [width, height] of [[1440, 900], [375, 812]]) {
  test(`login sem rolagem horizontal ${width}x${height}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(page.getByTestId("login")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await login(page, users.a);
    await expect(page.getByTestId("logout")).toBeVisible();
    const after = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(after).toBeLessThanOrEqual(0);
  });
}
