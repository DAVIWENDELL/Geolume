import { test, expect, request as playwrightRequest } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import net from "node:net";
import { fileURLToPath } from "node:url";

// Limite de upload no servidor, upload interrompido e documentação fechada, contra a API real.
test.use({ storageState: { cookies: [], origins: [] } });

const AUTH = (name) => fileURLToPath(new URL(`../.auth/${name}`, import.meta.url));
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const CSRF = { "X-GeoLume-CSRF": "1" };
const MB = 1024 * 1024;
const LIMITE = 10 * MB;
const GRANDE = { detail: "Arquivo maior que 10 MB." };
const lote = readFileSync(fileURLToPath(new URL("../../worker/tests/fixtures/lote_simples.geojson", import.meta.url)));

/** GeoJSON válido com espaços no fim até ter exatamente `tamanho` bytes. */
const geojsonCom = (tamanho) => Buffer.concat([lote, Buffer.alloc(tamanho - lote.length, " ")]);
const unico = (prefixo) => `${prefixo}_${randomBytes(4).toString("hex")}.geojson`;

async function asUser(baseURL, key) {
  return playwrightRequest.newContext({ baseURL, storageState: AUTH(`${key}.json`) });
}

function cookieDe(key) {
  const { cookies } = JSON.parse(readFileSync(AUTH(`${key}.json`), "utf8"));
  return `geolume_session=${cookies.find((c) => c.name === "geolume_session").value}`;
}

/** Arquivos com esse nome em /saida/inputs dentro do contêiner. */
function arquivosNoServidor(nome) {
  const r = spawnSync("docker", ["compose", "exec", "-T", "api", "ls", "/saida/inputs"], { cwd: ROOT, encoding: "utf8" });
  expect(r.status, r.stderr).toBe(0);
  return r.stdout.split("\n").filter((linha) => linha.endsWith(`-${nome}`));
}

async function jobsCom(ctx, nome) {
  const { jobs } = await (await ctx.get("/jobs?limit=100")).json();
  return jobs.filter((j) => j.input_filename === nome);
}

/**
 * HTTP cru: manda cabeçalhos e só `enviar` bytes do corpo. Com `fechar`, derruba a conexão
 * no meio; senão espera a resposta. Devolve a primeira linha da resposta (ou null).
 */
function httpCru(baseURL, { path, headers, corpo, enviar, fechar }) {
  const { hostname, port } = new URL(baseURL);
  return new Promise((resolve, reject) => {
    const socket = net.connect(Number(port), hostname === "localhost" ? "127.0.0.1" : hostname);
    let resposta = "";
    socket.on("data", (d) => (resposta += d));
    socket.on("error", (e) => (fechar ? resolve(null) : reject(e)));
    socket.on("close", () => resolve(resposta || null));
    socket.on("connect", () => {
      const linhas = [`POST ${path} HTTP/1.1`, `Host: ${hostname}:${port}`, ...Object.entries(headers).map(([k, v]) => `${k}: ${v}`)];
      socket.write(`${linhas.join("\r\n")}\r\n\r\n`);
      if (enviar) socket.write(corpo.subarray(0, enviar));
      if (fechar) setTimeout(() => socket.destroy(), 300);
      else setTimeout(() => socket.destroy(), 10_000);
    });
  });
}

function multipart(nome, conteudo) {
  const limite = `geolume${randomBytes(6).toString("hex")}`;
  const corpo = Buffer.concat([
    Buffer.from(`--${limite}\r\nContent-Disposition: form-data; name="file"; filename="${nome}"\r\nContent-Type: application/geo+json\r\n\r\n`),
    conteudo,
    Buffer.from(`\r\n--${limite}--\r\n`),
  ]);
  return { corpo, tipo: `multipart/form-data; boundary=${limite}` };
}

test("documentação da API não é pública", async ({ request }) => {
  for (const url of ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]) {
    expect((await request.get(url)).status(), url).toBe(404);
  }
});

test("arquivo de exatamente 10 MB é aceito", async ({ baseURL }) => {
  const a = await asUser(baseURL, "a");
  const nome = unico("no_limite");
  const res = await a.post("/jobs/async", { headers: CSRF, multipart: { file: { name: nome, mimeType: "application/geo+json", buffer: geojsonCom(LIMITE) } } });
  expect(res.status()).toBe(202);
  expect(await jobsCom(a, nome)).toHaveLength(1);
  expect(arquivosNoServidor(nome)).toHaveLength(1);
  await a.dispose();
});

test("1 byte acima de 10 MB recebe 413 em /jobs/async e /jobs, sem job nem arquivo", async ({ baseURL }) => {
  for (const [key, rota] of [["a", "/jobs/async"], ["admin", "/jobs"]]) {
    const ctx = await asUser(baseURL, key);
    const nome = unico("acima_do_limite");
    const res = await ctx.post(rota, { headers: CSRF, multipart: { file: { name: nome, mimeType: "application/geo+json", buffer: geojsonCom(LIMITE + 1) } } });
    expect(res.status(), rota).toBe(413);
    expect(await res.json(), rota).toEqual(GRANDE);
    expect(await jobsCom(ctx, nome), rota).toHaveLength(0);
    expect(arquivosNoServidor(nome), rota).toHaveLength(0);
    await ctx.dispose();
  }
});

test("Content-Length de 50 MB é recusado antes de o corpo ser enviado", async ({ baseURL }) => {
  for (const rota of ["/jobs/async", "/jobs"]) {
    const resposta = await httpCru(baseURL, {
      path: rota,
      headers: { Cookie: cookieDe(rota === "/jobs" ? "admin" : "a"), "X-GeoLume-CSRF": "1", "Content-Type": "multipart/form-data; boundary=x", "Content-Length": 50 * MB },
      enviar: 0,
    });
    expect(resposta, rota).toMatch(/^HTTP\/1\.1 413 /);
    // /jobs/async aceita GeoJSON + logo: o corpo inteiro vai até 12 MB.
    const detalhe = rota === "/jobs" ? "Arquivo maior que 10 MB." : "Envio maior que 12 MB.";
    expect(resposta, rota).toContain(`"detail":"${detalhe}"`);
    expect(resposta, rota).not.toMatch(/Traceback|\/saida|\/app\//);
  }
});

test("upload interrompido não cria job nem arquivo", async ({ baseURL }) => {
  const a = await asUser(baseURL, "a");
  const nome = unico("interrompido");
  const { corpo, tipo } = multipart(nome, geojsonCom(5 * MB));
  await httpCru(baseURL, {
    path: "/jobs/async",
    headers: { Cookie: cookieDe("a"), "X-GeoLume-CSRF": "1", "Content-Type": tipo, "Content-Length": corpo.length },
    corpo,
    enviar: MB,
    fechar: true,
  });
  await new Promise((r) => setTimeout(r, 1500));
  expect(await jobsCom(a, nome)).toHaveLength(0);
  expect(arquivosNoServidor(nome)).toHaveLength(0);
  expect((await a.get("/auth/me")).status()).toBe(200); // o servidor segue atendendo
  await a.dispose();
});

test("upload anônimo recebe 401 antes de o corpo ser enviado (Content-Length e chunked)", async ({ baseURL }) => {
  const nome = unico("anonimo");
  const { corpo, tipo } = multipart(nome, geojsonCom(2 * MB));
  for (const rota of ["/jobs/async", "/jobs"]) {
    for (const cookie of [null, "geolume_session=token-que-nao-existe"]) {
      for (const tamanho of [{ "Content-Length": corpo.length }, { "Content-Length": 50 * MB }, { "Transfer-Encoding": "chunked" }]) {
        const caso = `${rota} ${cookie ? "cookie inválido" : "sem cookie"} ${JSON.stringify(tamanho)}`;
        // Só os cabeçalhos saem: se a resposta chega, o servidor decidiu sem nenhum byte do corpo.
        const resposta = await httpCru(baseURL, {
          path: rota,
          headers: { ...(cookie ? { Cookie: cookie } : {}), "X-GeoLume-CSRF": "1", "Content-Type": tipo, ...tamanho },
          enviar: 0,
        });
        expect(resposta, caso).toMatch(/^HTTP\/1\.1 401 /);
        expect(resposta, caso).toContain('{"detail":"Autenticação necessária"}');
        expect(resposta, caso).not.toMatch(/Traceback|\/saida|\/app\//);
      }
    }
  }
  const admin = await asUser(baseURL, "admin");
  expect(await jobsCom(admin, nome)).toHaveLength(0);
  expect(arquivosNoServidor(nome)).toHaveLength(0);
  await admin.dispose();
});

test("upload anônimo com corpo inteiro também não cria job nem arquivo", async ({ request, baseURL }) => {
  const nome = unico("anonimo_completo");
  for (const rota of ["/jobs/async", "/jobs"]) {
    const res = await request.post(rota, { headers: CSRF, multipart: { file: { name: nome, mimeType: "application/geo+json", buffer: lote } } });
    expect(res.status(), rota).toBe(401);
    expect(await res.json(), rota).toEqual({ detail: "Autenticação necessária" });
  }
  const admin = await asUser(baseURL, "admin");
  expect(await jobsCom(admin, nome)).toHaveLength(0);
  expect(arquivosNoServidor(nome)).toHaveLength(0);
  await admin.dispose();
});

const CAMPOS = ["arquivos", "completed_at", "created_at", "erro", "input_filename", "job_id", "prancha", "status", "task_id"];
const INTERNO = /\/saida|\/app\/|\/tmp\/|owner_id|tenant_id|_path"/;

test("respostas dos jobs só têm campos públicos e os links de download funcionam", async ({ baseURL }) => {
  const a = await asUser(baseURL, "a");
  const admin = await asUser(baseURL, "admin");
  const criado = await a.post("/jobs/async", { headers: CSRF, multipart: { file: { name: unico("publico"), mimeType: "application/geo+json", buffer: lote } } });
  expect(criado.status()).toBe(202);
  const fila = await criado.json();
  expect(Object.keys(fila).sort()).toEqual(["job_id", "status", "task_id"]);
  const url = `/jobs/${fila.task_id}`;
  await expect.poll(async () => (await (await a.get(url)).json()).status, { timeout: 90_000 }).toBe("completed");

  const texto = await (await a.get(url)).text();
  expect(texto).not.toMatch(INTERNO);
  const job = JSON.parse(texto);
  expect(Object.keys(job).sort()).toEqual(CAMPOS);
  expect(job.job_id).toBe(fila.job_id);
  expect(job.prancha).toBeNull(); // sem personalização
  expect(job.arquivos).toEqual({ mapa: `${url}/files/mapa`, memorial: `${url}/files/memorial`, resultado: `${url}/files/resultado` });
  const tipos = { mapa: "application/pdf", memorial: "application/pdf", resultado: "application/json" };
  for (const [tipo, link] of Object.entries(job.arquivos)) {
    const res = await a.get(link);
    expect(res.status(), link).toBe(200);
    expect(res.headers()["content-type"], link).toContain(tipos[tipo]);
  }

  for (const ctx of [a, admin]) {
    const lista = await (await ctx.get("/jobs?limit=100")).text();
    expect(lista).not.toMatch(INTERNO);
    for (const linha of JSON.parse(lista).jobs) expect(Object.keys(linha).sort()).toEqual(CAMPOS);
  }
  await a.dispose();
  await admin.dispose();
});

test("usuário logado sem o header de CSRF continua recebendo 403 no upload", async ({ baseURL }) => {
  const nome = unico("sem_csrf");
  for (const [key, rota] of [["a", "/jobs/async"], ["admin", "/jobs"]]) {
    const ctx = await asUser(baseURL, key);
    const res = await ctx.post(rota, { multipart: { file: { name: nome, mimeType: "application/geo+json", buffer: lote } } });
    expect(res.status(), rota).toBe(403);
    expect(await jobsCom(ctx, nome), rota).toHaveLength(0);
    await ctx.dispose();
  }
  expect(arquivosNoServidor(nome)).toHaveLength(0);
});
