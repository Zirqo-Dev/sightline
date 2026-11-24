/**
 * Radar preview smoke checks. From the repo root:
 *   node scripts/smoke-radar.mjs
 *
 * Font sizes, controls, embargoed fields, Solana placeholders, and the
 * Base empty state. Derived chips are checked by scripts/tests/test_radar_snapshot.py.
 */
import fs from "fs";
import path from "path";
import vm from "vm";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const css = fs.readFileSync(path.join(root, "radar/radar.css"), "utf8");
const html = fs.readFileSync(path.join(root, "radar.html"), "utf8");
const js = fs.readFileSync(path.join(root, "radar/radar.js"), "utf8");

const sizes = [];
const fontRe = /font-size:\s*([0-9.]+)px|font:\s*(?:[0-9]+\s+)?([0-9.]+)px/g;
for (const text of [css, html, js]) {
  fontRe.lastIndex = 0;
  let match;
  while ((match = fontRe.exec(text))) {
    sizes.push(Number(match[1] || match[2]));
  }
}
assert(sizes.length > 0, "radar styles declare a font size");
for (const size of sizes) {
  assert(size >= 12, "font size under 12px: " + size);
}
assert(css.includes(".row-btn,.chip,.q,.feed{min-height:32px}"), "tap controls are at least 32px");
assert(html.includes("&gt;sight<span>line</span><i>_</i>"), "logo marks line and the cursor");
assert(html.includes("Research only"), "page says research only");
assert(!/<button[^>]*>\s*buy\b/i.test(html + js), "no buy control");
assert(!/connect wallet|wallet-connect|connectWallet/i.test(html + js), "no wallet connect control");
assert(js.includes("Heat is a ranking, not a buy signal."), "heat caption is present");
assert(js.includes("No Base coin feed is published. This cut stays empty."), "Base empty state is present");
for (const phrase of ["Seen at", "New pool", "Desk flag", "Snipers out", "Coordinated", "Top-heavy", "Thin start", "Kill line", "Buy streak", "Second leg", "LP locked", "Dev sold", "Holders up", "Floor sweep", "Whale count", "Not live"]) {
  assert(js.includes(phrase), "legend is missing " + phrase);
}
assert(!/NIX|LOOM|FERN|BRINE/.test(js + html), "page does not ship invented Solana tickers");

const py = spawnSync("python3", ["scripts/tests/test_radar_snapshot.py"], { cwd: root, encoding: "utf8" });
if (py.status !== 0) throw new Error(py.stdout + py.stderr);

const sandbox = { console, Date, Math, URLSearchParams, isFinite, Number, String, Array };
sandbox.globalThis = sandbox;
vm.runInNewContext(js, sandbox);
const radar = sandbox.SightlineRadar;
const now = Date.parse("2026-11-24T18:00:00Z");
const doc = {
  last_scan_age: "5m",
  lead_count: 2,
  next_read: "2026-11-25T15:00:00Z",
  x: { hollow: true, stale: true, label: "X buzz stale · 19d" },
  feeds: [{ id: "X", label: "X", status: "hollow", age: "19d", detail: "X buzz stale" }],
  rows: [
    {
      id: "sol-1", name: "SOL #1", age: "10m", chain: "SOL", kind: "coin",
      price: 0.0016, price_unit: "SOL", change: 60, size: 1.6, size_unit: "SOL", size_kind: "liq",
      stat: "h1 buys 75%", heat: 90, buy_pct: 0.75, spark: [1, 1.5, 0.9, 1.6],
      chips: [{ id: "streak", label: "buy streak" }, { id: "leg", label: "second leg" }],
      facts: ["Buys beat sells on every 30-minute snap in this window."],
      why: "SOL #1 · 10m", source: "Solana 30-minute snaps"
    },
    {
      id: "nft-1", name: "Cash Cats", chain: "RH", kind: "nft",
      price: 1.25, price_unit: "ETH", change: null, size: 2, size_unit: "ETH",
      stat: "1 sales", heat: 40, chips: [],
      facts: ["Floor change is blank, so the pill is an em dash."],
      why: "Cash Cats", source: "HoodScout NFT"
    }
  ],
  gaps: []
};
const view = radar.renderHtml(doc, { filter: "All", sort: "heat", open: "sol-1" }, now);
assert(view.includes('class="pill flat">—<'), "null change renders an em dash, got a missing pill");
assert(!/>\s*[+-]?0%<\/span>/.test(view), "null change is not rendered as 0%");
assert(view.includes("SOL #1") && view.includes("second leg") && view.includes("buy streak"), "derived chips render");
assert(view.includes("is-hollow"), "stale X feed is hollow");
assert(!/onset|d_bps|nh04|verdict|captures\.jsonl/.test(view), "rendered view has no embargoed field");
assert(!/[1-9A-HJ-NP-Za-km-z]{32,44}/.test(view), "rendered view has no mint string");
const base = radar.renderHtml(doc, { filter: "BASE", sort: "heat", open: null }, now);
assert(base.includes(radar.BASE_EMPTY), "BASE filter explains the empty cut");
assert(!base.includes("SOL #1"), "BASE filter does not keep other rows");
const scan = radar.scanLine(doc, now);
assert(scan.includes("xmark") && !scan.includes("filled"), "header X stays hollow when the feed is stale");
assert(scan.includes("Next read Nov 25, 15:00Z"), "next read is the Nov 25 checkpoint");
const legend = radar.legendHtml();
assert(legend.includes("not a buy signal") && legend.includes("Not live"), "legend covers heat and the off-row chips");

console.log("radar smoke ok");
