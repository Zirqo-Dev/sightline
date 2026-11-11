import path from "path";
import { fileURLToPath } from "url";
import { test, expect } from "@playwright/test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const outDir = path.join(root, "output");

async function openStrip(page, state) {
  await page.route("**/*", function (route) {
    var url = route.request().url();
    if (url.indexOf("127.0.0.1") !== -1 || url.indexOf("localhost") !== -1) return route.continue();
    return route.abort();
  });
  await page.goto("/index.html?fixtures=1&health=" + state + "#/", { waitUntil: "domcontentloaded" });
  const strip = page.locator("#pipeline-health");
  await expect(strip).toBeVisible();
  return strip;
}

async function assertFit(page, strip) {
  const fit = await page.evaluate(() => {
    const doc = document.documentElement;
    const node = document.getElementById("pipeline-health");
    return {
      page: doc.scrollWidth - doc.clientWidth,
      strip: node.scrollWidth - node.clientWidth,
    };
  });
  expect(fit.page).toBeLessThanOrEqual(1);
  expect(fit.strip).toBeLessThanOrEqual(1);
  const targets = strip.locator(".health-chip, .health-empty");
  const count = await targets.count();
  expect(count).toBeGreaterThan(0);
  for (let i = 0; i < count; i++) {
    const box = await targets.nth(i).evaluate((el) => {
      const style = getComputedStyle(el);
      return {
        height: el.getBoundingClientRect().height,
        font: parseFloat(style.fontSize),
      };
    });
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.font).toBeGreaterThanOrEqual(14);
  }
}

for (const state of ["green", "red", "empty"]) {
  test("health strip " + state + " at 360x800", async ({ page }) => {
    const strip = await openStrip(page, state);
    if (state === "empty") {
      await expect(strip).toContainText("not published yet");
      await expect(strip.locator(".health-chip")).toHaveCount(0);
      const text = await strip.innerText();
      expect(text).not.toMatch(/\d/);
    } else {
      await expect(strip.locator(".health-overall")).toContainText(state);
      await expect(strip.locator(".health-chip").first()).toBeVisible();
      const chips = strip.locator(".health-chip");
      const count = await chips.count();
      expect(count).toBeGreaterThan(0);
      for (let i = 0; i < count; i++) {
        await expect(chips.nth(i).locator(".st")).toHaveText(state);
      }
    }
    await assertFit(page, strip);
    await page.screenshot({
      path: path.join(outDir, "health-strip-" + state + ".png"),
      fullPage: false,
    });
  });
}
