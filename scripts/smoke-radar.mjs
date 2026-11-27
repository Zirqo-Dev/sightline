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
assert(css.includes(".chip,.sort,.row-btn,.dex,.note-x{min-height:44px}"), "filter, sort, row, and link controls are at least 44px");
assert(css.includes(".q{min-width:44px;padding:8px 0"), "header legend button keeps a square tap target");
assert(css.includes(".legend-close{padding:8px 12px}"), "close has horizontal padding");
assert(html.includes('class="q legend-close"'), "close uses its own class");
assert(css.includes(".mark img.logo{position:absolute;inset:0"), "the logo sits in the mark");
assert(css.includes("opacity:0"), "the logo stays hidden until it decodes");
assert(css.includes(".mark .initial"), "the initial is its own element");
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
assert(js.includes("This snapshot is stale. Built "), "stale snapshot sentence is present");
assert(js.includes("It does not describe current collection health."), "a stale snapshot does not describe current collection health");
assert(js.includes("Release status unavailable"), "release status stays unavailable");
assert(js.includes("Wallet races"), "wallet races are named as workflow health");
assert(js.includes("Paper Solana token pools observed in 30-minute snapshots."), "solana rows are paper observations");
assert(js.includes('rel="noopener noreferrer"'), "dex links do not grant the new tab access");
assert(!/v6\.5|sealed outcome|Snek/i.test(js + html), "the page does not surface a sealed outcome or a banned label");
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
assert(!view.includes("This snapshot is stale."), "a fresh list does not repeat the stale sentence");
assert(view.includes("2 of 2 leads"), "the header counts the filtered rows and the total");
assert(view.includes("Paper observations"), "solana rows sit under the paper heading");
assert(view.includes("Last-recorded details"), "healthy detail is behind the expansion");
assert(!/onset|d_bps|nh04|verdict|captures\.jsonl|next_read|Next read/.test(view), "rendered view has no embargoed field or next read");
assert(!/[1-9A-HJ-NP-Za-km-z]{32,44}/.test(view), "rendered view has no mint string");
const base = radar.renderHtml(doc, { filter: "BASE", sort: "heat", open: null }, now);
assert(base.includes(radar.BASE_EMPTY), "BASE filter explains the empty cut");
assert(!base.includes("SOL #1"), "BASE filter does not keep other rows");
const scan = radar.scanLine(doc, now);
assert(scan.includes("Snapshot age 30m"), "the header has one snapshot age, got " + scan);
assert(!scan.includes("17:30:00Z"), "the build clock is not a UTC stamp");
assert(!scan.includes("last scan"), "the header age is not also a last-scan age");
assert(view.includes("Last scan 10m"), "last scan age is in the details");
assert(view.includes("is-hollow") && view.includes("X buzz stale"), "stale X stays in the details");
const old = Object.assign({}, doc, { built_at: "2026-11-24T10:00:00Z" });
const oldSummary = radar.summaryHtml(old, now);
assert(oldSummary.includes("This snapshot is stale. Built Nov 24, 5:00 AM ET. It does not describe current collection health."), "an old snapshot says it is stale and is not current health, got " + oldSummary);
const bare = { rows: [], feeds: [], x: {}, lead_count: 0, gaps: [] };
assert(radar.summaryHtml(bare, now).includes("This snapshot is stale. It does not describe current collection health."), "a missing build time is stale");
assert(radar.summaryHtml(Object.assign({}, bare, { built_at: "not-a-time" }), now).includes("This snapshot is stale."), "an invalid build time is stale");
const future = new Date(now + 11 * 60 * 1000).toISOString().replace(".000Z", "Z");
assert(radar.summaryHtml(Object.assign({}, bare, { built_at: future }), now).includes("This snapshot is stale."), "a build time more than 10 minutes ahead is stale");
const soon = new Date(now + 5 * 60 * 1000).toISOString().replace(".000Z", "Z");
assert(!radar.summaryHtml(Object.assign({}, bare, { built_at: soon }), now).includes("This snapshot is stale."), "a build time a few minutes ahead is still fresh");
assert(radar.summaryHtml(doc, now).includes("Release status unavailable"), "release status is on the summary");
const legend = radar.legendHtml();
assert(legend.includes("not a buy signal") && legend.includes("Not a Hood tier"), "legend matches the desk flag");
assert(legend.includes('class="leg-heat"') && legend.includes('class="leg-skull"'), "legend icons keep their chip ids");
assert(oldSummary.includes("Built Nov 24, 5:00 AM ET"), "the stale clock is Toronto time");
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
      id: "sol-x", name: "WIDGET", chain: "SOL", kind: "coin",
      price: 5.5e-7, price_unit: "USD", change: -99.99, size: 2, size_unit: "SOL",
      image: "dashboard/icons/coin-cock.png", stat: "snaps", heat: 8
    },
    {
      id: "dust-x", name: "DUST", chain: "RH", kind: "coin",
      price: 4e-13, price_unit: "USD", change: 1, size: 1, size_unit: "USD",
      stat: "x", heat: 2
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
assert(fmt.includes("+1161×"), "a move above 1000% is the price multiple");
assert(fmt.includes("-99.9%"), "a drop just above a total loss is not rounded to -100%");
assert(!fmt.includes("-100%"), "that drop is not shown as a total loss");
assert(!/-[\d.]+×/.test(fmt), "a drop is not shown as a negative multiple");
assert(fmt.includes("+10%"), "a move of 1000% or below stays a percent");
assert(fmt.includes("+1000%"), "1000% itself stays a percent");
assert(fmt.includes('aria-label="$0.0000000000004"'), "a tiny price keeps an accurate screen-reader decimal");
function pillText(change) {
  const html = radar.renderHtml({
    built_at: "2026-11-24T17:30:00Z",
    last_scan: "2026-11-24T17:50:00Z",
    lead_count: 1,
    x: {},
    feeds: [],
    rows: [{ id: "p", name: "P", chain: "RH", kind: "coin", price: 1, price_unit: "USD", change, size: 1, size_unit: "USD", stat: "x", heat: 1 }],
    gaps: []
  }, { filter: "All", sort: "heat" }, now);
  const match = html.match(/class="pill [^"]*">([^<]*)</);
  return match ? match[1] : "";
}
assert(pillText(999.9) === "+999.9%", "999.9% stays a percent, got " + pillText(999.9));
assert(pillText(1000) === "+1000%", "1000% stays a percent, got " + pillText(1000));
assert(pillText(1000.1) === "+11×", "1000.1% is an 11× price, got " + pillText(1000.1));
assert(pillText(9999) === "+101×", "9999% is a 101× price, got " + pillText(9999));
assert(pillText(116045.9) === "+1161×", "116045.9% is a 1161× price, got " + pillText(116045.9));
const justBelow = Math.round(1 + 999.9 / 100);
const atCap = Math.round(1 + 1000 / 100);
const justAbove = Number(pillText(1000.1).replace(/[+×]/g, ""));
assert(justAbove >= justBelow && justAbove >= atCap, "the first multiple is smaller than the move just under the cap");
let previousMultiple = 0;
for (let n = 1000.1; n <= 12000; n += 37) {
  const shown = Number(pillText(n).replace("+", "").replace("×", ""));
  assert(shown >= previousMultiple, "multiple stepped backwards at " + n);
  previousMultiple = shown;
}
function chunk(id) {
  return fmt.split('data-id="' + id + '"')[1].split("</article>")[0];
}
const fiona = chunk("fmt-1");
assert(fiona.includes('referrerpolicy="no-referrer"') && fiona.includes('loading="lazy"'), "remote logos do not send a referrer");
assert(fiona.includes("https://cdn.example/fiona.png"), "a remote coin logo is still used when no local file matches");
const solChunk = chunk("sol-x");
assert(solChunk.includes("dashboard/icons/chains/solana.svg"), "any SOL row uses the chain logo");
assert(!solChunk.includes("coin-cock"), "a SOL row does not keep a token logo");
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

const mixed = radar.renderHtml(doc, { filter: "All", sort: "vol" }, now);
assert(mixed.includes('data-sort="vol" aria-pressed="false" aria-disabled="true"'), "mixed volume units are not a sort, got a live vol control");
assert(mixed.includes("SOL reserve · 1 missing") || mixed.includes("vol · mixed units"), "the unavailable sort says why");
assert(mixed.includes('data-active="heat"'), "an unavailable sort falls back to one live sort");
assert((mixed.match(/aria-pressed="true"/g) || []).length === 2, "exactly one filter and one sort are pressed");

const solOnly = {
  built_at: doc.built_at,
  last_scan: doc.last_scan,
  x: doc.x,
  feeds: doc.feeds,
  gaps: [],
  rows: [1, 2, 3].map(function (n) {
    return {
      id: "s" + n, name: "SOL #" + n, chain: "SOL", kind: "coin",
      price: n, price_unit: "SOL", change: n, size: n * 10, size_unit: "SOL", size_kind: "liq",
      stat: "reserve", heat: n, buyers: null
    };
  })
};
const solView = radar.renderHtml(solOnly, { filter: "SOL", sort: "vol" }, now);
assert(solView.includes(">SOL reserve<"), "a solana cut sorts by SOL reserve, got " + solView.slice(solView.indexOf("data-sort=\"vol\""), solView.indexOf("data-sort=\"vol\"") + 80));
assert(solView.includes('data-sort="vol" aria-pressed="true"'), "SOL reserve is the active sort");
assert(solView.includes("buyers · 3 missing"), "buyers with missing values stay unavailable");
assert(solView.includes("0 of 2 leads") === false, "the solana cut is not the empty count");
assert(solView.includes("3 of 3 leads"), "the solana cut counts its own rows");
assert(solView.indexOf("SOL #3") < solView.indexOf("SOL #1"), "SOL reserve orders the larger reserve first");

const named = radar.renderHtml({
  built_at: doc.built_at, last_scan: doc.last_scan, x: {}, feeds: [], gaps: [],
  rows: [{
    id: "named", name: "WGT", token_name: "Widget", chain: "SOL", kind: "coin",
    price: 1, price_unit: "SOL", change: 2, size: 9, size_unit: "SOL", size_kind: "liq",
    heat: 4, stat: "snaps",
    dex_url: "https://dexscreener.com/solana/11111111111111111111111111111111"
  }, {
    id: "plain", name: "CAT", chain: "ADA", kind: "coin",
    price: 1, price_unit: "ADA", change: 1, size: 4, size_unit: "ADA", size_kind: "vol",
    buyers: 8, heat: 3, stat: "holders",
    dex_url: "https://dexscreener.com/cardano/" + "ab".repeat(28)
  }]
}, { filter: "All", sort: "heat" }, now);
assert(named.includes(">WGT<"), "a recorded symbol replaces the placeholder");
assert(named.includes("Widget"), "the recorded name is still visible");
assert(named.includes('href="https://dexscreener.com/solana/11111111111111111111111111111111"'), "the solana row links to the pair");
assert(named.includes('href="https://dexscreener.com/cardano/'), "the named coin links with its chain slug");
assert(named.includes('target="_blank"'), "the dex link opens a new tab");
assert((named.match(/class="dex"/g) || []).length === 2, "each of these rows has one dex link");

const walDoc = {
  built_at: "2026-11-24T17:30:00Z",
  last_scan: "2026-11-24T17:50:00Z",
  x: { asof: "2026-11-05T01:39:00Z" },
  feeds: [
    { id: "WAL", label: "WAL", status: "red", at: "2026-11-24T12:00:00Z" },
    { id: "SOL", label: "SOL", status: "green", at: "2026-11-24T17:40:00Z" },
    { id: "X", label: "X", status: "green", at: "2026-11-05T01:39:00Z" }
  ],
  rows: doc.rows,
  gaps: []
};
const walSummary = radar.summaryHtml(walDoc, now);
assert(walSummary.includes("Wallet races · last recorded run failed · 6h"), "a failed wallet run is last-recorded workflow health, got " + walSummary);
assert(!walSummary.includes("Solana desk scan"), "a healthy collector stays out of the summary");
assert(!walSummary.includes("X buzz"), "secondary X context stays out of the summary");
assert(walSummary.includes("Release status unavailable"), "release status does not invent a finding");
let walState = radar.reduce({ filter: "All", sort: "heat" }, { type: "feed", id: "WAL" }, walDoc, now);
assert(walState.feedNote && walState.feedNote.text.includes("Wallet races · last recorded run failed · 6h"), "the feed note uses the wallet sentence");
assert(walState.detailsOpen === true, "opening a feed keeps the details open");
const walView = radar.renderHtml(walDoc, walState, now);
assert(walView.includes('data-for="WAL"'), "the note is tied to the WAL feed");
assert(walView.includes("Dismiss"), "the note can be dismissed");
walState = radar.reduce(walState, { type: "filter", id: "All" }, walDoc, now);
assert(walState.feedNote, "the same filter does not clear the note");
walState = radar.reduce(walState, { type: "filter", id: "SOL" }, walDoc, now);
assert(walState.feedNote == null, "a filter change clears the note");
walState = radar.reduce(walState, { type: "filter", id: "ADA" }, walDoc, now);
walState = radar.reduce(walState, { type: "filter", id: "SOL" }, walDoc, now);
assert(walState.filter === "SOL" && walState.sort === "heat", "A to B to A leaves one filter");
walState = radar.reduce(walState, { type: "sort", id: "buyers" }, walDoc, now);
assert(walState.sort === "heat", "an unavailable sort does not become active");
const sorts = ["heat", "move", "vol", "buyers"];
for (let i = 0; i < 12; i++) {
  walState = radar.reduce(walState, { type: "filter", id: ["All", "SOL", "ADA", "All"][i % 4] }, walDoc, now);
  walState = radar.reduce(walState, { type: "sort", id: sorts[i % 4] }, walDoc, now);
}
const afterRapid = radar.renderHtml(walDoc, walState, now);
assert((afterRapid.match(/aria-pressed="true"/g) || []).length === 2, "rapid taps leave one filter and one sort");
const shown = afterRapid.split('data-chain="').slice(1).map(function (part) { return part.slice(0, part.indexOf('"')); });
const activeFilter = walState.filter;
shown.forEach(function (chain) {
  const ok = activeFilter === "All" || chain === activeFilter;
  assert(ok, "a shown card does not match the active filter " + activeFilter + ": " + chain);
});

console.log("radar smoke ok");
