// Usuários exclusivos do E2E local (@geolume.test). As senhas são geradas a cada
// execução, entram no users.py só pelo stdin e ficam em tests-web/.auth/ (fora do Git).
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { request } from "@playwright/test";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const AUTH_DIR = fileURLToPath(new URL(".auth/", import.meta.url));
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export const USERS = {
  admin: { email: "e2e-admin@geolume.test", role: "admin" },
  a: { email: "e2e-a@geolume.test", role: "member" },
  b: { email: "e2e-b@geolume.test", role: "member" },
};

function run(args, stdin) {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", ["compose", ...args], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, out, err }));
    child.stdin.end(stdin ?? "");
  });
}

async function ensureUser({ email, role }, password) {
  const base = ["exec", "-T", "api", "python3", "scripts/users.py"];
  let res = await run([...base, "create", "--email", email, "--role", role, "--password-stdin"], `${password}\n`);
  if (res.code === 3) res = await run([...base, "reset-password", "--email", email, "--password-stdin"], `${password}\n`);
  if (res.code !== 0) throw new Error(`users.py falhou para ${email} (exit ${res.code}): ${res.err.trim()}`);
}

async function legacyTaskId() {
  const sql = "select task_id from jobs where owner_id is null and task_id is not null order by created_at desc limit 1";
  const res = await run(["exec", "-T", "postgis", "psql", "-U", "geolume", "-d", "geolume", "-tAc", sql]);
  if (res.code !== 0) throw new Error(`consulta do job legado falhou: ${res.err.trim()}`);
  return res.out.trim() || null;
}

export default async function globalSetup(config) {
  const baseURL = config.projects[0].use.baseURL;
  if (!LOCAL_HOSTS.has(new URL(baseURL).hostname)) {
    throw new Error(`E2E com usuários de teste só roda contra localhost (baseURL: ${baseURL}).`);
  }
  mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });

  const credentials = {};
  for (const [key, user] of Object.entries(USERS)) {
    const password = randomBytes(24).toString("base64url");
    await ensureUser(user, password);
    const ctx = await request.newContext({ baseURL });
    const res = await ctx.post("/auth/login", {
      headers: { "X-GeoLume-CSRF": "1" },
      data: { email: user.email, password },
    });
    if (res.status() !== 200) throw new Error(`login de ${user.email} falhou: HTTP ${res.status()}`);
    await ctx.storageState({ path: `${AUTH_DIR}${key}.json` });
    await ctx.dispose();
    credentials[key] = { ...user, password };
  }

  const file = `${AUTH_DIR}users.json`;
  writeFileSync(file, JSON.stringify({ users: credentials, legacyTaskId: await legacyTaskId() }, null, 2));
  chmodSync(file, 0o600);
}
