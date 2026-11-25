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
assert(css.includes(".row-btn,.chip,.q,.feed,.sort{min-height:32px}"), "tap controls are at least 32px");
assert(css.includes(".q{min-width:32px;padding:4px 0"), "header legend button keeps its square padding");
assert(css.includes(".legend-close{padding:4px 12px}"), "close has horizontal padding");
assert(html.includes('class="q legend-close"'), "close uses its own class");
assert(css.includes(".mark img{position:absolute;inset:0"), "logo covers the letter");
assert(css.includes(".legend svg{width:16px;height:16px"), "legend icons are 16px");
assert(css.includes(".detail{padding:8px 10px 10px 74px"), "detail lines up with the name");
assert(css.includes(".subz{font-family:Archivo,ui-sans-serif,sans-serif;font-size:12px}"), "subscript zeros stay at least 12px");
assert(html.includes("&gt;sight<span>line</span><i>_</i>"), "logo marks line and the cursor");
assert(html.includes("Research only"), "page says research only");
assert(!/<button[^>]*>\s*buy\b/i.test(html + js), "no buy control");
assert(!/connect wallet|wallet-connect|connectWallet/i.test(html + js), "no wallet connect control");
assert(js.includes("Heat is a ranking, not a buy signal."), "heat caption is present");
assert(js.includes("No Base coin feed is published. This cut stays empty."), "Base empty state is present");
assert(js.includes("Not a Hood tier and not a Solana pick."), "desk legend matches the Cardano flag");
assert(js.includes("more than 2 hours old"), "stale snapshot banner is present");
assert(!js.includes("next_read") && !js.includes("Next read"), "the next read is not hardcoded");
assert(js.includes("Snapshot is unavailable.") && html.includes("Snapshot is unavailable."), "a missing snapshot says it is unavailable");
assert(!js.includes("Loading the latest") && !html.includes("Loading the latest"), "a missing snapshot does not stay on the loading line");
for (const phrase of ["Seen at", "New pool", "Desk flag", "Snipers out", "Coordinated", "Top-heavy", "Thin start", "Kill line", "Buy streak", "Second leg", "LP locked", "Dev sold", "Holders up", "Floor sweep", "Whale count", "Not live"]) {
  assert(js.includes(phrase), "legend is missing " + phrase);
}
assert(!/NIX|LOOM|FERN|BRINE/.test(js + html), "page does not ship invented Solana tickers");

const py = spawnSync("python3", ["scripts/tests/test_radar_snapshot.py"], { cwd: root, encoding: "utf8" });
if (py.status !== 0) throw new Error(py.stdout + py.stderr);

const sandbox = { console, Date, Math, URLSearchParams, isFinite, Number, String, Array, Intl };
sandbox.globalThis = sandbox;
vm.runInNewContext(js, sandbox);
const radar = sandbox.SightlineRadar;
const now = Date.parse("2026-11-24T18:00:00Z");
const doc = {
  built_at: "2026-11-24T17:30:00Z",
  last_scan: "2026-11-24T17:50:00Z",
  lead_count: 2,
  x: { asof: "2026-11-05T01:39:00Z" },
  feeds: [{ id: "X", label: "X", status: "green", at: "2026-11-05T01:39:00Z", detail: "X buzz" }],
  rows: [
    {
      id: "sol-1", name: "SOL #1", seen_at: "2026-11-24T17:00:00Z", chain: "SOL", kind: "coin",
      price: 0.0016, price_unit: "SOL", change: 60, size: 1.6, size_unit: "SOL", size_kind: "liq",
      stat: "h1 buys 75%", heat: 90, buy_pct: 0.75, spark: [1, 1.5, 0.9, 1.6],
      chips: [{ id: "seen", label: "seen" }, { id: "streak", label: "buy streak" }, { id: "leg", label: "second leg" }],
      facts: ["Buys beat sells on every 30-minute snap in this window."],
      why: "SOL #1", source: "Solana 30-minute snaps"
    },
    {
      id: "nft-1", name: "Cash Cats", chain: "RH", kind: "nft",
      price: 1.25, price_unit: "ETH", change: null, size: null, size_unit: "ETH",
      stat: "1 sales", heat: 40, chips: [],
      facts: ["Floor change is blank, so the pill is an em dash."],
      why: "Cash Cats", source: "HoodScout NFT"
    }
  ],
  gaps: []
};
const view = radar.renderHtml(doc, { filter: "All", sort: "heat", open: "sol-1" }, now);
assert(!view.includes('id="hidden-note"'), "a zero hide count omits the note");
const noted = radar.renderHtml(Object.assign({}, doc, {
  hidden_note: "3 collections hidden for creator attribution"
}), { filter: "All", sort: "heat", open: "sol-1" }, now);
assert(noted.includes('id="hidden-note"'), "a hide count renders one note line");
assert(noted.includes("3 collections hidden for creator attribution"), "the note is the count only");
assert(!noted.includes("hidden_note"), "the field name is not shown");
assert(view.includes('class="pill flat">—<'), "null change renders an em dash, got a missing pill");
assert(view.includes('class="size">—<'), "missing size renders an em dash");
assert(!/>\s*[+-]?0%<\/span>/.test(view), "null change is not rendered as 0%");
assert(view.includes("seen 60m") && view.includes("second leg") && view.includes("buy streak"), "age and derived chips render from the timestamp");
assert(view.includes("is-hollow"), "stale X feed is hollow even if the stored status is green");
assert(!view.includes("more than 2 hours old"), "a fresh build does not show the stale banner");
assert(!/onset|d_bps|nh04|verdict|captures\.jsonl|next_read|Next read/.test(view), "rendered view has no embargoed field or next read");
assert(!/[1-9A-HJ-NP-Za-km-z]{32,44}/.test(view), "rendered view has no mint string");
const base = radar.renderHtml(doc, { filter: "BASE", sort: "heat", open: null }, now);
assert(base.includes(radar.BASE_EMPTY), "BASE filter explains the empty cut");
assert(!base.includes("SOL #1"), "BASE filter does not keep other rows");
const scan = radar.scanLine(doc, now);
assert(scan.includes("xmark") && !scan.includes("filled"), "header X stays hollow when the feed is stale");
assert(scan.includes("Built Nov 24, 12:30 PM"), "the build time is shown in Toronto");
assert(!scan.includes("17:30:00Z"), "the build clock is not a UTC stamp");
assert(scan.includes("last scan 10m"), "last scan age is computed from the timestamp");
const old = Object.assign({}, doc, { built_at: "2026-11-24T10:00:00Z" });
assert(radar.renderHtml(old, { filter: "All", sort: "heat" }, now).includes("more than 2 hours old"), "an old snapshot shows a banner");
const bare = { rows: [], feeds: [], x: {}, lead_count: 0, gaps: [] };
assert(radar.renderHtml(bare, { filter: "All", sort: "heat" }, now).includes("This snapshot is stale."), "a missing build time is stale");
assert(radar.renderHtml(Object.assign({}, bare, { built_at: "not-a-time" }), { filter: "All", sort: "heat" }, now).includes("This snapshot is stale."), "an invalid build time is stale");
const future = new Date(now + 11 * 60 * 1000).toISOString().replace(".000Z", "Z");
assert(radar.renderHtml(Object.assign({}, bare, { built_at: future }), { filter: "All", sort: "heat" }, now).includes("This snapshot is stale."), "a build time more than 10 minutes ahead is stale");
const soon = new Date(now + 5 * 60 * 1000).toISOString().replace(".000Z", "Z");
assert(!radar.renderHtml(Object.assign({}, bare, { built_at: soon }), { filter: "All", sort: "heat" }, now).includes("This snapshot is stale."), "a build time a few minutes ahead is still fresh");
const legend = radar.legendHtml();
assert(legend.includes("not a buy signal") && legend.includes("Not a Hood tier"), "legend matches the desk flag");
assert(legend.includes('class="leg-heat"') && legend.includes('class="leg-skull"'), "legend icons keep their chip ids");
const oldView = radar.renderHtml(old, { filter: "All", sort: "heat" }, now);
assert(oldView.includes("Built Nov 24, 5:00 AM"), "the stale banner clock is Toronto time");
const fmt = radar.renderHtml({
  built_at: "2026-11-24T17:30:00Z",
  last_scan: "2026-11-24T17:50:00Z",
  lead_count: 4,
  x: { asof: "2026-11-05T01:39:00Z" },
  feeds: [],
  rows: [
    {
      id: "fmt-1", name: "FIONA", chain: "RH", kind: "coin",
      price: 2.8e-5, price_unit: "SOL", change: 116045.9, size: 0.51, size_unit: "ETH",
      image: "https://cdn.example/fiona.png", stat: "buyers", heat: 9
    },
    {
      id: "sol-x", name: "SOL #1", chain: "SOL", kind: "coin",
      price: 5.5e-7, price_unit: "USD", change: -1200, size: 2, size_unit: "SOL",
      image: "dashboard/icons/coin-cock.png", stat: "snaps", heat: 8
    },
    {
      id: "nft-x", name: "Cash Cats", chain: "ETH", kind: "nft",
      price: 1, price_unit: "ETH", change: null, image: null, stat: "floor", heat: 7
    },
    {
      id: "ada-x", name: "COCK", chain: "ADA", kind: "coin",
      price: 0.004, price_unit: "ADA", change: 10,
      image: "dashboard/icons/coin-cock.png", stat: "12 holders", heat: 6
    },
    {
      id: "zz-x", name: "Mystery", chain: "ZZ", kind: "coin",
      price: 1, price_unit: "USD", change: 1000, image: null, stat: "x", heat: 1
    }
  ],
  gaps: []
}, { filter: "All", sort: "heat", open: "fmt-1" }, now);
assert(fmt.includes('aria-hidden="true">0.0<span class="subz">₄</span>28 SOL'), "tiny prices use subscript zeros");
assert(fmt.includes('aria-label="0.000028 SOL"'), "subscript prices keep a plain-decimal name");
assert(fmt.includes('aria-hidden="true">$0.0<span class="subz">₆</span>55'), "a dollar price under 0.0001 uses subscript zeros");
assert(fmt.includes('aria-label="$0.00000055"'), "the dollar price keeps a plain-decimal name");
assert(!/e-[0-9]/.test(fmt), "rendered prices and sizes are not scientific notation");
assert(fmt.includes("0.51 ETH"), "a size under 1 is a plain decimal");
assert(fmt.includes("+1160×"), "a move above 1000% is a multiple of the percent");
assert(fmt.includes("-12×"), "a large drop is a negative multiple");
assert(fmt.includes("+10%"), "a move of 1000% or below stays a percent");
assert(fmt.includes("+1000%"), "1000% itself stays a percent");
function chunk(id) {
  return fmt.split('data-id="' + id + '"')[1].split("</article>")[0];
}
const fiona = chunk("fmt-1");
assert(fiona.includes('referrerpolicy="no-referrer"') && fiona.includes('loading="lazy"'), "remote logos do not send a referrer");
assert(fiona.includes("https://cdn.example/fiona.png"), "a remote coin logo is still used when no local file matches");
const solChunk = chunk("sol-x");
assert(solChunk.includes("dashboard/icons/chains/solana.svg"), "SOL #N uses the chain logo");
assert(!solChunk.includes("coin-cock"), "SOL #N does not keep a token logo");
const nftChunk = chunk("nft-x");
assert(nftChunk.includes("dashboard/icons/chains/ethereum.svg"), "an NFT with no image uses the chain logo");
assert(!nftChunk.includes("http"), "the NFT mark does not publish a remote image");
const adaChunk = chunk("ada-x");
assert(adaChunk.includes("dashboard/icons/coin-cock.png"), "a coin image is the local file");
const letter = chunk("zz-x");
assert(!letter.includes("<img"), "a chain with no logo keeps the letter");
assert(letter.includes(">M</span>"), "the letter is the name initial");
const scanEl = { textContent: "Loading the latest snapshot…" };
const appEl = { innerHTML: "" };
sandbox.document = {
  getElementById(id) {
    if (id === "scan") return scanEl;
    if (id === "app") return appEl;
    return null;
  }
};
assert(typeof radar.paint === "function", "paint is exported");
radar.paint();
assert(scanEl.textContent === "Snapshot is unavailable.", "paint with no radar.json says the snapshot is unavailable, got " + scanEl.textContent);
assert(!String(scanEl.textContent).includes("Loading"), "paint does not leave the loading line");

console.log("radar smoke ok");
