import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./global-setup.mjs",
  timeout: 120_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.GEOLUME_URL ?? "http://localhost:8000",
    channel: "chrome",
    storageState: fileURLToPath(new URL(".auth/a.json", import.meta.url)), // usuário A por padrão; auth.spec usa contexto anônimo
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chrome-desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "chrome-mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
