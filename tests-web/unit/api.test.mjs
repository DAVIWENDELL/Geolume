import { test } from "node:test";
import assert from "node:assert/strict";
import { createApi, ApiError } from "../../worker/web/js/api.js";

function fakeFetch(respond) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body, headers: new Headers(init.headers) });
    return respond(url, init);
  };
  return { impl, calls };
}

const json = (status, data) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

test("health faz GET /health", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { status: "ok" }));
  assert.deepEqual(await createApi(impl).health(), { status: "ok" });
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [["GET", "/health"]]);
});

test("enqueue faz POST /jobs/async com FormData no campo file", async () => {
  const { impl, calls } = fakeFetch(() => json(202, { status: "queued", job_id: "j", task_id: "t" }));
  const file = new File(["{}"], "lote.geojson", { type: "application/geo+json" });
  const body = await createApi(impl).enqueue(file);
  assert.equal(body.task_id, "t");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].url, "/jobs/async");
  assert.ok(calls[0].body instanceof FormData);
  assert.equal(calls[0].body.get("file").name, "lote.geojson");
});

test("enqueue sem prancha manda só o campo file", async () => {
  const { impl, calls } = fakeFetch(() => json(202, { task_id: "t" }));
  await createApi(impl).enqueue(new File(["{}"], "lote.geojson"));
  assert.deepEqual([...calls[0].body.keys()], ["file"]);
});

test("enqueue com prancha anexa os campos e a logo", async () => {
  const { impl, calls } = fakeFetch(() => json(202, { task_id: "t" }));
  const logo = new File([new Uint8Array([137, 80])], "marca.png", { type: "image/png" });
  await createApi(impl).enqueue(new File(["{}"], "lote.geojson"), {
    fields: [["projeto", "Sol"], ["legenda", "lateral"]],
    logo,
  });
  const body = calls[0].body;
  assert.deepEqual([...body.keys()], ["file", "projeto", "legenda", "logo"]);
  assert.equal(body.get("projeto"), "Sol");
  assert.equal(body.get("logo").name, "marca.png");
});

test("getJob codifica o task_id", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { status: "started" }));
  await createApi(impl).getJob("a/b");
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [["GET", "/jobs/a%2Fb"]]);
});

test("listJobs faz GET /jobs?limit=20 e retorna o array", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { jobs: [{ task_id: "x" }] }));
  assert.deepEqual(await createApi(impl).listJobs(), [{ task_id: "x" }]);
  assert.equal(calls[0].url, "/jobs?limit=20");
});

test("listJobs sem jobs retorna array vazio", async () => {
  const { impl } = fakeFetch(() => json(200, {}));
  assert.deepEqual(await createApi(impl).listJobs(), []);
});

test("erro com detail vira ApiError com a mensagem", async () => {
  const { impl } = fakeFetch(() => json(400, { detail: "A PoC aceita somente arquivos .geojson" }));
  await assert.rejects(createApi(impl).enqueue(new File(["x"], "a.json")), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.status, 400);
    assert.equal(err.message, "A PoC aceita somente arquivos .geojson");
    return true;
  });
});

test("erro com detail nao textual usa HTTP status", async () => {
  const { impl } = fakeFetch(() => json(422, { detail: [{ msg: "field required" }] }));
  await assert.rejects(createApi(impl).health(), { message: "HTTP 422", status: 422 });
});

test("erro com corpo nao JSON", async () => {
  const { impl } = fakeFetch(() => new Response("<html>Bad Gateway</html>", { status: 502 }));
  await assert.rejects(createApi(impl).listJobs(), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.message, "HTTP 502");
    assert.equal(err.status, 502);
    return true;
  });
});

test("fetch rejeitado vira ApiError de rede", async () => {
  const impl = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(createApi(impl).getJob("t"), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.status, 0);
    assert.equal(err.message, "API indisponível");
    return true;
  });
});

test("getResult faz GET do resultado.json do job", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { area_ha: 1.5, perimetro_m: 500 }));
  assert.deepEqual(await createApi(impl).getResult("a/b"), { area_ha: 1.5, perimetro_m: 500 });
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [["GET", "/jobs/a%2Fb/files/resultado"]]);
});

test("getInput devolve o GeoJSON original como texto, sem reinterpretar", async () => {
  const raw = '{ "type": "FeatureCollection", "features": [] }\n';
  const { impl, calls } = fakeFetch(() => new Response(raw, { status: 200, headers: { "content-type": "application/geo+json" } }));
  assert.equal(await createApi(impl).getInput("a/b"), raw);
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [["GET", "/jobs/a%2Fb/input"]]);
});

test("getInput 404 vira ApiError com o detail da API", async () => {
  const { impl } = fakeFetch(() => json(404, { detail: "Arquivo original não disponível" }));
  await assert.rejects(createApi(impl).getInput("t"), { message: "Arquivo original não disponível", status: 404 });
});

test("getInput com fetch rejeitado vira ApiError de rede", async () => {
  const impl = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(createApi(impl).getInput("t"), { message: "API indisponível", status: 0 });
});

// ---- Sessão -------------------------------------------------------------

test("enqueue envia o header CSRF", async () => {
  const { impl, calls } = fakeFetch(() => json(202, { task_id: "t" }));
  await createApi(impl).enqueue(new File(["{}"], "lote.geojson"));
  assert.equal(calls[0].headers.get("X-GeoLume-CSRF"), "1");
});

test("GET nao envia o header CSRF", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { jobs: [] }));
  await createApi(impl).listJobs();
  assert.equal(calls[0].headers.get("X-GeoLume-CSRF"), null);
});

test("login envia JSON e o header", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { email: "ana@geolume.test", role: "member" }));
  const user = await createApi(impl).login("ana@geolume.test", "senha-longa-12");
  assert.deepEqual(user, { email: "ana@geolume.test", role: "member" });
  assert.deepEqual([calls[0].method, calls[0].url], ["POST", "/auth/login"]);
  assert.equal(calls[0].headers.get("X-GeoLume-CSRF"), "1");
  assert.equal(calls[0].headers.get("Content-Type"), "application/json");
  assert.deepEqual(JSON.parse(calls[0].body), { email: "ana@geolume.test", password: "senha-longa-12" });
});

test("logout faz POST /auth/logout com o header", async () => {
  const { impl, calls } = fakeFetch(() => new Response(null, { status: 204 }));
  await createApi(impl).logout();
  assert.deepEqual([calls[0].method, calls[0].url], ["POST", "/auth/logout"]);
  assert.equal(calls[0].headers.get("X-GeoLume-CSRF"), "1");
});

test("me devolve email e role", async () => {
  const { impl, calls } = fakeFetch(() => json(200, { email: "ana@geolume.test", role: "admin" }));
  assert.deepEqual(await createApi(impl).me(), { email: "ana@geolume.test", role: "admin" });
  assert.deepEqual([calls[0].method, calls[0].url], ["GET", "/auth/me"]);
});

test("401 chama onUnauthorized uma vez", async () => {
  let chamadas = 0;
  const { impl } = fakeFetch(() => json(401, { detail: "Autenticação necessária" }));
  const api = createApi(impl, { onUnauthorized: () => chamadas++ });
  await assert.rejects(api.listJobs(), (err) => err instanceof ApiError && err.status === 401);
  assert.equal(chamadas, 1);
});

test("401 no getInput tambem chama onUnauthorized", async () => {
  let chamadas = 0;
  const { impl } = fakeFetch(() => json(401, { detail: "Autenticação necessária" }));
  await assert.rejects(createApi(impl, { onUnauthorized: () => chamadas++ }).getInput("t"));
  assert.equal(chamadas, 1);
});

test("401 no login não chama onUnauthorized", async () => {
  let chamadas = 0;
  const { impl } = fakeFetch(() => json(401, { detail: "E-mail ou senha inválidos" }));
  const api = createApi(impl, { onUnauthorized: () => chamadas++ });
  await assert.rejects(api.login("a@b.test", "x"), (err) => err.status === 401 && err.message === "E-mail ou senha inválidos");
  assert.equal(chamadas, 0);
});

test("getCamadas faz GET /camadas e devolve o corpo", async () => {
  const corpo = { camadas: [], estilos: [], alfa_padrao: 0.35 };
  const { impl, calls } = fakeFetch(() => json(200, corpo));
  assert.deepEqual(await createApi(impl).getCamadas(), corpo);
  assert.equal(calls[0].url, "/camadas");
  assert.equal(calls[0].method, "GET");
});

test("getCamadas 401 chama onUnauthorized (sessão expirada, não 'catálogo indisponível')", async () => {
  let chamadas = 0;
  const { impl } = fakeFetch(() => json(401, { detail: "Autenticação necessária" }));
  const api = createApi(impl, { onUnauthorized: () => chamadas++ });
  await assert.rejects(api.getCamadas(), (err) => err instanceof ApiError && err.status === 401);
  assert.equal(chamadas, 1);
});

test("getCamadas com falha de rede vira ApiError status 0", async () => {
  let chamadas = 0;
  const impl = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(createApi(impl, { onUnauthorized: () => chamadas++ }).getCamadas(), (err) => {
    assert.ok(err instanceof ApiError);
    assert.equal(err.status, 0);
    return true;
  });
  assert.equal(chamadas, 0);
});

test("403 nao chama onUnauthorized", async () => {
  let chamadas = 0;
  const { impl } = fakeFetch(() => json(403, { detail: "Requisição recusada" }));
  await assert.rejects(createApi(impl, { onUnauthorized: () => chamadas++ }).listJobs());
  assert.equal(chamadas, 0);
});
