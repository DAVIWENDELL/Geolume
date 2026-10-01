import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Demonstração só em localhost: a API não pode ser publicada nas outras interfaces do host.
test("docker-compose publica a API só em 127.0.0.1", () => {
  const compose = readFileSync(new URL("../../docker-compose.yml", import.meta.url), "utf8");
  const portas = [...compose.matchAll(/^\s*-\s*"?([^"\s]*:?\d+:\d+)"?\s*$/gm)].map((m) => m[1]);
  assert.ok(portas.length > 0, "nenhuma porta publicada encontrada");
  for (const porta of portas) assert.match(porta, /^127\.0\.0\.1:/, `porta publicada fora do localhost: ${porta}`);
  assert.ok(portas.includes("127.0.0.1:8000:8000"));
});
