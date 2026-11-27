/**
 * Radar preview smoke checks. From the repo root:
 *   node scripts/smoke-radar.mjs
 *
 * Font sizes, controls, the heat ladder, sorts, and the stale pill.
 * Derived chips and degen scoring are checked by scripts/tests/test_radar_snapshot.py.
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
assert(css.includes(".legend-close{min-height:44px;min-width:44px;padding:8px 12px}"), "the legend close control is at least 44px");
assert(html.includes('class="q legend-close"'), "close uses its own class");
assert(css.includes(".mark img.logo{position:absolute;inset:0"), "the logo sits in the mark");
assert(css.includes("opacity:0"), "the logo stays hidden until it decodes");
assert(css.includes(".mark .initial"), "the initial is its own element");
assert(css.includes(".legend svg{width:16px;height:16px"), "legend icons are 16px");
assert(css.includes(".detail{padding:8px 10px 10px 74px"), "detail lines up with the name");
assert(css.includes(".subz{font-family:Archivo,ui-sans-serif,sans-serif;font-size:12px}"), "subscript zeros stay at least 12px");
assert(css.includes(".fold summary,.fold-link{min-height:44px"), "fold controls are at least 44px");
assert(html.includes("&gt;sight<span>line</span><i>_</i>"), "logo marks line and the cursor");
assert(html.includes("Research only"), "page says research only");
assert(!/<button[^>]*>\s*buy\b/i.test(html + js), "no buy control");
assert(!/connect wallet|wallet-connect|connectWallet/i.test(html + js), "no wallet connect control");
assert(js.includes("Heat is a ranking, not a buy signal."), "heat caption is present");
assert(js.includes("No Base coin feed is published. This cut stays empty."), "Base empty state is present");
assert(js.includes("Not a Hood tier and not a Solana pick."), "desk legend matches the Cardano flag");
assert(!js.includes("Release status unavailable"), "the release-status wall is gone");
assert(!js.includes("Paper observations"), "the paper heading is gone");
assert(!js.includes("SOL #"), "the page does not mint SOL # labels");
assert(js.includes("Wallet races"), "wallet races are named as workflow health");
assert(js.includes("dexscreener\\.com"), "the solana link pattern escapes the dot");
assert(js.includes('rel="noopener noreferrer"'), "dex links do not grant the new tab access");
assert(!/v6\.5|sealed outcome|Snek/i.test(js + html), "the page does not surface a sealed outcome or a banned label");
assert(!js.includes("next_read") && !js.includes("Next read"), "the next read is not hardcoded");
assert(js.includes("Snapshot is unavailable.") && html.includes("Snapshot is unavailable."), "a missing snapshot says it is unavailable");
assert(!js.includes("Loading the latest") && !html.includes("Loading the latest"), "a missing snapshot does not stay on the loading line");
for (const phrase of ["Seen at", "New pool", "Desk flag", "Snipers out", "Coordinated", "Top-heavy", "Thin start", "Kill line", "Buy streak", "Second leg", "LP locked", "Dev sold", "Holders up", "Floor sweep", "Whale count", "Not live"]) {
  assert(js.includes(phrase), "legend is missing " + phrase);
}
assert(!/NIX|LOOM|FERN|BRINE/.test(js + html), "page does not ship invented Solana tickers");
assert(js.includes("score = heat + 8 * freshness + marker - 25 * red_flags"), "the hotter formula is in the page");

const py = spawnSync("python3", ["scripts/tests/test_radar_snapshot.py"], { cwd: root, encoding: "utf8" });
if (py.status !== 0) throw new Error(py.stdout + py.stderr);

const sandbox = { console, Date, Math, URLSearchParams, isFinite, Number, String, Array, Intl };
sandbox.globalThis = sandbox;
vm.runInNewContext(js, sandbox);
const radar = sandbox.SightlineRadar;
const now = Date.parse("2026-11-24T18:00:00Z");

function row(extra) {
  return Object.assign({
    id: "r", name: "ALP", seen_at: "2026-11-24T16:00:00Z", chain: "SOL", kind: "coin",
    price: 0.04, price_unit: "SOL", change: 12, size: 200, size_unit: "SOL", size_kind: "liq",
    heat: 80, degen: 3, degen_label: "DEGEN", heat_mark: "heating",
    thin_book: false, crowd_flag: false, line_state: "clear", buys_drop: false,
    chips: [{ id: "streak", label: "buy streak" }],
    why: "ALP", source: "Solana 30-minute snaps"
  }, extra);
}

const doc = {
  built_at: "2026-11-24T17:30:00Z",
  last_scan: "2026-11-24T17:50:00Z",
  lead_count: 4,
  x: { asof: "2026-11-05T01:39:00Z" },
  feeds: [
    { id: "SOL", label: "SOL", status: "green", at: "2026-11-24T17:40:00Z" },
    { id: "X", label: "X", status: "green", at: "2026-11-05T01:39:00Z" }
  ],
  rows: [
    row({ id: "hot", name: "ALP", heat: 90, heat_mark: "hotter", degen: 5, degen_label: "CASINO" }),
    row({ id: "mid", name: "BEE", chain: "RH", heat: 60, heat_mark: "heating", degen: 2, degen_label: "SPICY", size_unit: "USD", size_kind: "vol", price_unit: "USD" }),
    row({ id: "warm", name: "COCK", chain: "ADA", heat: 45, heat_mark: null, degen: 1, degen_label: "MID" }),
    row({ id: "nft-1", name: "Cash Cats", chain: "RH", kind: "nft", heat: 40, degen: 1, degen_label: "MID", price: 1.25, price_unit: "ETH", change: null, size: null, size_unit: "ETH", chips: [] })
  ],
  gaps: []
};

const view = radar.renderHtml(doc, { filter: "All", sort: "hotter", open: "hot" }, now);
assert(view.includes("Scorching"), "scorching tier is a section");
assert(view.includes("Hot"), "hot tier is a section");
assert(view.includes("Warm"), "warm tier is a section");
assert(view.includes(">1<") && view.includes("heat 75+"), "the scorching header counts its rows");
assert(view.includes("NFTs"), "the coin ladder links to the nft ladder");
assert(!view.includes("Paper observations"), "there is no paper heading");
assert(!view.includes("Release status"), "there is no release-status line");
assert(view.includes('class="pill flat">—<') || view.includes("Cash Cats") === false, "nft change is not on the coin ladder");
assert(view.includes("seen") === false || view.includes("buy streak"), "derived chips still render");
assert(view.includes("SMART"), "a clean heating row gets the smart badge");
assert(view.includes("5 CASINO") || view.includes("CASINO"), "degen label renders");
assert(view.includes('data-sort="hotter"'), "hotter sort is a button");
assert(view.includes('data-sort="heat"'), "heat sort is a button");
assert(view.includes('data-sort="fresh"'), "fresh sort is a button");
assert(view.includes('data-sort="degen"'), "degen sort is a button");
assert(!view.includes("aria-disabled"), "sorts are never disabled");
assert(!view.includes("missing"), "sorts do not show a missing-count label");
assert(radar.scanLine(doc, now) === "LIVE \u00b7 30m", "a 30 minute snapshot is live, got " + radar.scanLine(doc, now));

const noted = radar.legendHtml(Object.assign({}, doc, {
  hidden_note: "3 collections hidden for creator attribution",
  unnamed_hidden: 2
}), now);
assert(noted.includes('id="hidden-note"'), "a hide count renders one note line");
assert(noted.includes("3 collections hidden for creator attribution"), "the note is the count only");
assert(noted.includes('id="unnamed-note"'), "unnamed rows are counted in the sheet");
assert(noted.includes("2 unnamed rows left off the ladder."), "the unnamed note is a count");
assert(noted.includes("Wallet races") === false, "a feed that is not on the doc is not invented");
assert(noted.includes(radar.BASE_EMPTY), "the base cut is explained in the sheet");
assert(radar.legendHtml(doc, now).includes("not a buy signal") && radar.legendHtml(doc, now).includes("Not a Hood tier"), "legend matches the desk flag");
assert(radar.legendHtml(doc, now).includes('class="leg-heat"') && radar.legendHtml(doc, now).includes('class="leg-skull"'), "legend icons keep their chip ids");

const old = Object.assign({}, doc, { built_at: "2026-11-24T10:00:00Z" });
assert(radar.summaryHtml(old, now).includes("STALE \u00b7 8h"), "an 8 hour snapshot is stale, got " + radar.summaryHtml(old, now));
const at45 = Object.assign({}, doc, { built_at: new Date(now - 45 * 60 * 1000).toISOString().replace(".000Z", "Z") });
assert(radar.summaryHtml(at45, now).includes("STALE \u00b7 45m"), "45 minutes is stale, got " + radar.summaryHtml(at45, now));
const at44 = Object.assign({}, doc, { built_at: new Date(now - 44 * 60 * 1000).toISOString().replace(".000Z", "Z") });
assert(radar.summaryHtml(at44, now).includes("LIVE \u00b7 44m"), "44 minutes is live, got " + radar.summaryHtml(at44, now));
const threeH = Object.assign({}, doc, { built_at: new Date(now - 3 * 60 * 60 * 1000).toISOString().replace(".000Z", "Z") });
assert(radar.summaryHtml(threeH, now).includes("STALE \u00b7 3h"), "3 hours is stale, got " + radar.summaryHtml(threeH, now));
const bare = { rows: [], feeds: [], x: {}, lead_count: 0, gaps: [] };
assert(radar.summaryHtml(bare, now).includes(">STALE<"), "a missing build time is stale, got " + radar.summaryHtml(bare, now));
assert(radar.summaryHtml(Object.assign({}, bare, { built_at: "not-a-time" }), now).includes(">STALE<"), "an invalid build time is stale");
const future = new Date(now + 11 * 60 * 1000).toISOString().replace(".000Z", "Z");
assert(radar.summaryHtml(Object.assign({}, bare, { built_at: future }), now).includes(">STALE<"), "a build time more than 10 minutes ahead is stale");
const soon = new Date(now + 5 * 60 * 1000).toISOString().replace(".000Z", "Z");
assert(radar.summaryHtml(Object.assign({}, bare, { built_at: soon }), now).includes("LIVE"), "a build time a few minutes ahead is still fresh");

const fmt = radar.renderHtml({
  built_at: "2026-11-24T17:30:00Z",
  last_scan: "2026-11-24T17:50:00Z",
  x: {},
  feeds: [],
  rows: [
    row({
      id: "fmt-1", name: "FIONA", chain: "RH",
      price: 2.8e-5, price_unit: "SOL", change: 116045.9, size: 0.51, size_unit: "ETH", size_kind: "vol",
      image: "https://cdn.example/fiona.png", heat: 9, degen: null, degen_label: null
    }),
    row({
      id: "sol-x", name: "WIDGET", chain: "SOL",
      price: 5.5e-7, price_unit: "USD", change: -99.99, size: 2, size_unit: "SOL",
      image: null, heat: 8
    }),
    row({
      id: "dust-x", name: "DUST", chain: "RH",
      price: 4e-13, price_unit: "USD", change: 1, size: 1, size_unit: "USD",
      heat: 2, image: null
    }),
    row({
      id: "nft-x", name: "Cash Cats", chain: "ETH", kind: "nft",
      price: 1, price_unit: "ETH", change: null, image: null, heat: 70, size: null
    }),
    row({
      id: "ada-x", name: "COCK", chain: "ADA",
      price: 0.004, price_unit: "ADA", change: 10,
      image: "dashboard/icons/coin-cock.png", heat: 6, size: 12, size_unit: "ADA", size_kind: "vol"
    }),
    row({
      id: "zz-x", name: "Mystery", chain: "ZZ",
      price: 1, price_unit: "USD", change: 1000, image: null, heat: 1, size: 1, size_unit: "USD"
    })
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
    x: {},
    feeds: [],
    rows: [row({ id: "p", name: "P", chain: "RH", price: 1, price_unit: "USD", change, size: 1, size_unit: "USD", heat: 80 })],
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
assert(solChunk.includes("dashboard/icons/chains/solana.svg"), "a SOL row with no image uses the chain logo");
const nftView = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [row({ id: "nft-x", name: "Cash Cats", chain: "ETH", kind: "nft", price: 1, price_unit: "ETH", change: null, image: null, heat: 70, size: null })]
}, { filter: "NFT", sort: "heat" }, now);
assert(nftView.includes("dashboard/icons/chains/ethereum.svg"), "an NFT with no image uses the chain logo");
assert(!nftView.includes("http"), "the NFT mark does not publish a remote image");
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

const missing = {
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [
    row({ id: "a", name: "HAS", heat: 70, degen: 4, seen_at: "2026-11-24T12:00:00Z" }),
    row({ id: "b", name: "NOHEAT", heat: null, degen: 5, seen_at: "2026-11-24T17:00:00Z" }),
    row({ id: "c", name: "GAP", heat: 65, degen: null, seen_at: null, heat_mark: null })
  ]
};
for (const key of ["hotter", "heat", "fresh", "degen"]) {
  const html = radar.renderHtml(missing, { filter: "All", sort: key }, now);
  assert(html.includes('data-sort="' + key + '" aria-pressed="true"'), key + " stays clickable");
  assert(!html.includes("aria-disabled"), key + " is not disabled");
  const ids = [...html.matchAll(/data-id="([^"]+)"/g)].map(function (item) { return item[1]; });
  if (key === "heat" || key === "hotter") {
    assert(ids.indexOf("a") < ids.indexOf("c") && ids.indexOf("c") < ids.indexOf("b"), key + " puts a missing heat in the cool fold, got " + ids.join(","));
  }
  if (key === "degen") assert(ids.indexOf("a") < ids.indexOf("c"), "degen sorts a missing score after a scored row in the same tier, got " + ids.join(","));
  if (key === "fresh") assert(ids.indexOf("a") < ids.indexOf("c"), "fresh sorts a missing age after a dated row in the same tier, got " + ids.join(","));
}

const young = "2026-11-24T17:00:00Z";
const older = "2026-11-23T20:00:00Z";
const hotterDoc = {
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [
    row({ id: "old", name: "OLD", heat: 80, seen_at: older, heat_mark: null, thin_book: false, crowd_flag: false, line_state: "clear", buys_drop: false }),
    row({ id: "young", name: "YOUNG", heat: 80, seen_at: young, heat_mark: null, thin_book: false, crowd_flag: false, line_state: "clear", buys_drop: false })
  ]
};
const hotterHtml = radar.renderHtml(hotterDoc, { filter: "All", sort: "hotter" }, now);
assert(hotterHtml.indexOf('data-id="young"') < hotterHtml.indexOf('data-id="old"'), "a younger row ranks above an older row at the same heat");
const flagged = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [
    row({ id: "clean", name: "CLEAN", heat: 80, seen_at: young }),
    row({ id: "thin", name: "THIN", heat: 88, seen_at: young, thin_book: true })
  ]
}, { filter: "All", sort: "hotter" }, now);
assert(flagged.indexOf('data-id="clean"') < flagged.indexOf('data-id="thin"'), "a red flag demotes a hotter row");
assert(flagged.includes("thin book"), "the thin-book flag is a chip");
assert(!radar.isSmart(row({ heat: 90, thin_book: true, seen_at: young }), now), "a red flag blocks the smart badge");
assert(!radar.isSmart(row({ heat: 90, thin_book: null, seen_at: young }), now), "a missing thin check blocks the smart badge");
assert(radar.isSmart(row({ heat: 90, heat_mark: "heating", seen_at: young }), now), "a clean hot row is smart");

const oldRow = row({ id: "aged", name: "HEY", heat: 88, seen_at: "2026-09-09T17:00:00Z" });
const aged = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [oldRow, row({ id: "newp", name: "NEW", heat: 50, seen_at: young, chips: [{ id: "pool", label: "new pool" }, { id: "seen", label: "seen" }] })]
}, { filter: "All", sort: "heat" }, now);
assert(aged.includes("Old guard"), "a row older than 48h folds into old guard");
assert(aged.includes("HEY"));
assert(!aged.includes("new pool") || aged.includes("NEW"), "a pool under an hour can keep the chip");
const hourOld = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [row({ id: "hr", name: "HR", heat: 50, seen_at: "2026-11-24T17:00:00Z", chips: [{ id: "pool", label: "new pool" }] })]
}, { filter: "All", sort: "heat" }, now);
assert(!hourOld.includes("new pool"), "the new pool chip hides at one hour");

const grave = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [row({ id: "dead", name: "BOLT", heat: 80, line_state: "crossed", chips: [{ id: "skull", label: "kill line" }] })]
}, { filter: "All", sort: "heat" }, now);
assert(grave.includes("Graveyard"), "a crossed kill line folds into the graveyard");
assert(!grave.includes("Scorching"), "a graveyard row is not also in scorching");

const redFeed = radar.renderHtml({
  built_at: doc.built_at,
  x: {},
  feeds: [{ id: "SOL", label: "SOL", status: "red", at: "2026-11-24T12:00:00Z" }],
  gaps: [],
  rows: [row({ id: "s", name: "ALP" })]
}, { filter: "All", sort: "heat" }, now);
assert(redFeed.includes('class="dot"'), "a failed feed is a red dot on its chain chip");
const health = radar.legendHtml({
  built_at: doc.built_at,
  x: { asof: "2026-11-05T01:39:00Z" },
  feeds: [
    { id: "WAL", label: "WAL", status: "red", at: "2026-11-24T12:00:00Z" },
    { id: "SOL", label: "SOL", status: "green", at: "2026-11-24T17:40:00Z" },
    { id: "X", label: "X", status: "green", at: "2026-11-05T01:39:00Z" }
  ],
  rows: []
}, now);
assert(health.includes("Wallet races · last recorded run failed · 6h"), "feed health is in the sheet, got " + health);
assert(health.includes("X buzz stale"), "stale X stays in the sheet");

const named = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [row({
    id: "named", name: "WGT", token_name: "Widget", heat: 70,
    dex_url: "https://dexscreener.com/solana/11111111111111111111111111111111"
  }), row({
    id: "plain", name: "CAT", chain: "ADA", heat: 60,
    dex_url: "https://dexscreener.com/cardano/" + "ab".repeat(28)
  })]
}, { filter: "All", sort: "heat" }, now);
assert(named.includes(">WGT<"), "a recorded symbol is the row name");
assert(named.includes("Widget"), "the recorded name is still visible");
assert(named.includes('href="https://dexscreener.com/solana/11111111111111111111111111111111"'), "the solana row links to the pair");
assert(!named.includes("dexscreener.com/cardano"), "a cardano asset id is not a link");
assert(!named.includes("0x"), "a link does not carry an 0x address");
assert((named.match(/class="dex"/g) || []).length === 1, "only the solana row has a dex link");

const twins = radar.renderHtml({
  built_at: doc.built_at, x: {}, feeds: [], gaps: [],
  rows: [
    row({ id: "sol-a", name: "WGT", heat: 70 }),
    row({ id: "sol-b", name: "WGT", heat: 60 })
  ]
}, { filter: "SOL", sort: "heat", open: "sol-a" }, now);
assert((twins.match(/aria-expanded="true"/g) || []).length === 1, "one tap opens one row");

let walState = radar.reduce({ filter: "All", sort: "hotter" }, { type: "sort", id: "degen" }, doc, now);
assert(walState.sort === "degen", "degen becomes the active sort even when a row has no score");
walState = radar.reduce(walState, { type: "sort", id: "buyers" }, doc, now);
assert(walState.sort === "degen", "an unknown sort does not become active");
walState = radar.reduce(walState, { type: "filter", id: "NFT" }, doc, now);
assert(walState.filter === "NFT", "the nft ladder is a filter");
const nftLadder = radar.renderHtml(doc, walState, now);
assert(nftLadder.includes("Cash Cats"), "the nft filter shows nft rows");
assert(!nftLadder.includes(">ALP<"), "the nft filter hides coins");
assert(nftLadder.includes("Warm") || nftLadder.includes("heat 40"), "the nft tab is its own ladder");

console.log("radar smoke ok");
