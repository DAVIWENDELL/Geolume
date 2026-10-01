// Cliente HTTP da API do worker. `fetch` injetável para testes.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status; // 0 = falha de rede
  }
}

const CSRF_HEADER = "X-GeoLume-CSRF";

/**
 * `onUnauthorized` é chamado quando a sessão não vale mais (401), exceto no login,
 * onde 401 significa credencial errada.
 */
export function createApi(fetchImpl = globalThis.fetch.bind(globalThis), { onUnauthorized } = {}) {
  /** `text: true` devolve o corpo bruto em caso de sucesso (erros seguem o formato JSON da API). */
  async function request(url, init = {}, { text = false, isLogin = false } = {}) {
    let response;
    if (init.method === "POST") {
      // Todo POST leva o header próprio: formulário de outro site não consegue enviá-lo.
      init = { ...init, headers: { ...init.headers, [CSRF_HEADER]: "1" } };
    }
    try {
      response = await fetchImpl(url, init);
    } catch {
      throw new ApiError("API indisponível", 0);
    }
    if (text && response.ok) {
      try {
        return await response.text();
      } catch {
        throw new ApiError("API indisponível", 0);
      }
    }
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!response.ok) {
      if (response.status === 401 && !isLogin) onUnauthorized?.();
      const detail = body?.detail;
      throw new ApiError(typeof detail === "string" ? detail : `HTTP ${response.status}`, response.status);
    }
    return body ?? {};
  }

  return {
    health: () => request("/health"),
    login: (email, password) =>
      request(
        "/auth/login",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) },
        { isLogin: true },
      ),
    logout: () => request("/auth/logout", { method: "POST" }),
    me: () => request("/auth/me"),
    /** Catálogo de camadas e estilos; 401 passa pelo onUnauthorized como qualquer outra rota. */
    getCamadas: () => request("/camadas"),
    /** prancha: { fields: [[campo, valor]], logo: File | null }; sem ela, só o GeoJSON vai. */
    enqueue(file, { fields = [], logo = null } = {}) {
      const form = new FormData();
      form.append("file", file, file.name);
      for (const [campo, valor] of fields) form.append(campo, valor);
      if (logo) form.append("logo", logo, logo.name);
      return request("/jobs/async", { method: "POST", body: form });
    },
    getJob: (taskId) => request(`/jobs/${encodeURIComponent(taskId)}`),
    getResult: (taskId) => request(`/jobs/${encodeURIComponent(taskId)}/files/resultado`),
    getInput: (taskId) => request(`/jobs/${encodeURIComponent(taskId)}/input`, undefined, { text: true }),
    async listJobs(limit = 20) {
      const body = await request(`/jobs?limit=${limit}`);
      return Array.isArray(body.jobs) ? body.jobs : [];
    },
  };
}
