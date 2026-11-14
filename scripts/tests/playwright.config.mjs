import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "@playwright/test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

export default defineConfig({
  testDir: path.dirname(fileURLToPath(import.meta.url)),
  testMatch: "health-strip.spec.mjs",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  outputDir: path.join(root, "output/playwright"),
  use: {
    viewport: { width: 360, height: 800 },
    screenshot: "off",
    trace: "off",
  },
  webServer: {
    command: "python3 -m http.server 8765 --bind 127.0.0.1",
    cwd: root,
    port: 8765,
    reuseExistingServer: !process.env.CI,
  },
});
