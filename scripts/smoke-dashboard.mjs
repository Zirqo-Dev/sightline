/**
 * Dashboard smoke checks. Dependency-free. Run from the repo root:
 *   node scripts/smoke-dashboard.mjs
 *
 * Exact P&L, win rate, and distance-to-kill numbers are checked against
 * scripts/fixtures/dashboard-smoke.json only. Live desk files change several
 * times a day; those get shape checks, so opening or closing a ticket cannot
 * fail a deploy. A broken script, a book that is not JSON, or a score that
 * no longer matches its own tickets still fails.
 */
import fs from "fs";
import vm from "vm";
import path from "path";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sandbox = { console };
sandbox.window = sandbox;

function load(rel) {
  return JSON.parse(fs.readFileSync(path.join(root, rel), "utf8"));
}
function closeTo(actual, expected, label) {
  if (Math.abs(actual - expected) > 0.02) throw new Error(`${label}: expected ${expected}, got ${actual}`);
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

function jsFiles() {
  const panels = fs.readdirSync(path.join(root, "dashboard/panels")).filter(n => n.endsWith(".js")).sort();
  return ["dashboard/core.js", "dashboard/pages.js", ...panels.map(n => "dashboard/panels/" + n)];
}
for (const rel of jsFiles()) {
  const r = spawnSync(process.execPath, ["--check", path.join(root, rel)], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(rel + " failed to parse\n" + (r.stderr || r.stdout));
}

vm.runInNewContext(fs.readFileSync(path.join(root, "dashboard/core.js"), "utf8"), sandbox);
for (const rel of jsFiles()) {
  if (rel === "dashboard/pages.js" || rel === "dashboard/core.js") continue;
  vm.runInNewContext(fs.readFileSync(path.join(root, rel), "utf8"), sandbox);
}
const S = sandbox.S;
assert(S && typeof S.score === "function" && typeof S.killDistance === "function", "dashboard core did not load");

function ticket(books, ticker) {
  for (const b of books) {
    const hit = (b.open || []).find(t => t.ticker === ticker);
    if (hit) return hit;
  }
  throw new Error("fixture is missing " + ticker);
}

const fx = load("scripts/fixtures/dashboard-smoke.json");
const sc = S.score(fx.books);
assert(!("pnl" in sc), "score() must not expose a cross-unit pnl total");
closeTo(sc.byUnit.ADA.pnl, -33.3, "fixture ADA pnl");
closeTo(sc.byUnit["ADA-equivalent"].pnl, -77.8, "fixture ADA-equivalent pnl");
closeTo(sc.byUnit.USD.pnl, 0, "fixture USD pnl");
assert(sc.open === 2, "fixture open count");
assert(sc.closed === 5, "fixture closed count");
assert(sc.winRate === 0, "fixture win rate");
const summed = sc.byUnit.ADA.pnl + sc.byUnit["ADA-equivalent"].pnl;
assert(Math.abs(summed - (-111.1)) < 0.02, "fixture units still sum to -111.1 if someone adds them");
const cards = S.metricCards(sc);
assert(!cards.includes("-111") && !cards.includes("111.1"), "cards must not show a mixed total");
assert(cards.includes("ADA-equivalent") && cards.includes("USD"), "cards label each unit");
assert(S.unitNote(sc).includes("never added together"), "mixed units get an explanation");

const cate = ticket(fx.books, "CATE");
const kd = S.killDistance(cate);
assert(kd && /holder/.test(kd.what), "fixture CATE closest kill is holders, got " + JSON.stringify(kd));
closeTo(kd.pct, (108078 - 97082) / 108078 * 100, "fixture CATE holders distance");
assert(kd.cur === 108078 && kd.lim === 97082, "fixture CATE uses the latest mark and the numeric limit");

const stripped = JSON.parse(JSON.stringify(cate));
stripped.kill_line = { text: stripped.kill_line.text };
const kdText = S.killDistance(stripped);
assert(kdText && /holder/.test(kdText.what), "text fallback still picks holders, got " + JSON.stringify(kdText));
closeTo(kdText.pct, kd.pct, "text fallback matches numeric distance");

const argo = ticket(fx.books, "argonauts");
const kdA = S.killDistance(argo);
assert(kdA && kdA.what === "floor", "fixture argonauts text parse uses floor, got " + JSON.stringify(kdA));
closeTo(kdA.pct, (0.793 - 0.70) / 0.793 * 100, "fixture argonauts floor distance");
closeTo(kdA.lim, 0.70, "fixture argonauts floor limit");

const synthetic = {
  entry: { mcap_ada: 500000, holders: 260, ada_side: 30000, floor_eth: 0.9 },
  kill_line: { text: "mcap < 450k ADA or holders < 250 or ADA side < 28k" }
};
const kdS = S.killDistance(synthetic);
assert(kdS && kdS.what === "holders", "synthetic closest is holders, got " + JSON.stringify(kdS));
closeTo(kdS.pct, (260 - 250) / 260 * 100, "synthetic holders distance");
assert(kdS.cur === 260, "synthetic does not match the floor field to a holders limit");

const none = S.killDistance({ ticker: "X", kill_line: { text: "exit if the tape feels wrong" }, entry: { holders: 10 } });
assert(none == null, "prose with no comparison stays unparsed");

const html = await S.renderPanel({ id: "solada", dir: "solada/" }, { type: "heatmap", title: "Swarm heatmap" });
assert(html.includes("not installed"), "unknown panel placeholder");
assert(html.includes("dashboard/panels/heatmap.js"), "placeholder names the missing file");
assert(html.includes("Swarm heatmap"), "placeholder keeps the title");

const snippet = "addr1q9ac..u9wwut";
const snippetEllipsis = "addr1q9ac…u9wwut";
const fragment = "u8a2m6";
assert(S.walletLabel(snippet) === "addr1q9…wwut", "long snippet compacts to first 7, ellipsis, last 4, got " + S.walletLabel(snippet));
assert(S.walletLabel(snippet) === S.walletLabel(snippetEllipsis), "dot and ellipsis forms share a tag label");
assert(S.walletHue(snippet) === S.walletHue(snippetEllipsis), "normalized forms share a color");
assert(S.walletHue(snippet) === S.walletHue(snippet.toUpperCase()), "color ignores case");
assert(S.walletTag(snippet) === S.walletTag(snippet), "same value renders the same tag");
const tagA = S.walletTag(snippet);
const tagB = S.walletTag(snippetEllipsis);
const hueA = tagA.match(/--h:(\d+)/);
const hueB = tagB.match(/--h:(\d+)/);
assert(hueA && hueB && hueA[1] === hueB[1], "tag color is stable across the same wallet");
assert(tagA.includes("addr1q9…wwut") && tagB.includes("addr1q9…wwut"), "both tags show the compact text");
assert(tagA.includes("title=\"" + snippet + "\""), "hover title is the snippet as it appears");
assert(tagB.includes("title=\"" + snippetEllipsis + "\""), "ellipsis form keeps its own full snippet");
assert(S.walletLabel(fragment) === fragment, "bare fragment stays whole");
assert(!S.walletTag(fragment).includes("…"), "short fragment is not shortened");
assert(S.walletHue(fragment) === S.walletHue(fragment.toUpperCase()), "fragment color ignores case");
const prose = S.rich("repeat buyer " + snippet + " and " + fragment + " still hold");
assert(prose.includes("addr1q9…wwut") && prose.includes(fragment), "prose tags the address and keeps the fragment");
assert(!prose.includes("‹") && S.esc(snippet) === snippet, "esc does not mask third-party snippets");
const css = fs.readFileSync(path.join(root, "dashboard/app.css"), "utf8");
assert(/\.wtag\{[^}]*white-space:\s*nowrap/.test(css), "wallet tags do not wrap mid-tag");

const card = S.ticketCard(argo, kdA, "<span>nft</span>");
assert(card.includes("11.7%") || card.includes(S.fmt(kdA.pct)), "ticket card shows the distance");
assert(!card.includes("stake1") && !card.includes("addr1"), "ticket card does not invent wallet text");

assert(S.when("2026-10-18") === "Oct 18", "date-only stays on that calendar day, got " + S.when("2026-10-18"));
assert(S.when("2026-10-18T09:50:08-04:00") === "Oct 18, 9:50 AM ET", "timestamp keeps ET, got " + S.when("2026-10-18T09:50:08-04:00"));

function liveBooks() {
  const idx = load("desks.json");
  if (!idx || !Array.isArray(idx.desks)) throw new Error("desks.json has no desks array");
  const books = [];
  for (const d of idx.desks) {
    for (const b of d.books || []) {
      let j;
      try { j = load(b.paper); }
      catch (e) { throw new Error("cannot read " + b.paper + ": " + e.message); }
      if (!j || typeof j !== "object" || !Array.isArray(j.open) || !Array.isArray(j.closed)) {
        throw new Error(b.paper + " is not a paper book");
      }
      books.push({ json: j, path: b.paper });
    }
  }
  if (!books.length) throw new Error("desks.json lists no paper books");
  return books;
}

function assertLiveShape(books, label) {
  let scLive;
  try { scLive = S.score(books); }
  catch (e) { throw new Error(label + ": score() threw: " + e.message); }
  const open = books.reduce((n, b) => n + (b.json.open || []).length, 0);
  const closed = books.reduce((n, b) => n + (b.json.closed || []).length, 0);
  assert(scLive.open === open, label + ": open count does not match the tickets");
  assert(scLive.closed === closed, label + ": closed count does not match the tickets");
  assert(Number.isInteger(scLive.wins) && scLive.wins >= 0 && scLive.wins <= closed, label + ": wins out of range");
  assert(scLive.losses === closed - scLive.wins, label + ": losses do not match");
  if (closed === 0) assert(scLive.winRate == null, label + ": win rate should be empty with no closes");
  else assert(typeof scLive.winRate === "number" && scLive.winRate >= 0 && scLive.winRate <= 100, label + ": win rate out of range");
  assert(!("pnl" in scLive), label + ": score() exposed a cross-unit pnl total");
  const units = Object.keys(scLive.byUnit);
  for (const unit of units) {
    const row = scLive.byUnit[unit];
    assert(row && typeof row.pnl === "number" && Number.isFinite(row.pnl), label + ": bad pnl for " + unit);
  }
  let rendered = "";
  try { rendered = S.metricCards(scLive) + S.unitNote(scLive); }
  catch (e) { throw new Error(label + ": scoreboard render threw: " + e.message); }
  for (const unit of units) assert(rendered.includes(unit), label + ": scoreboard omitted " + unit);
  if (units.length >= 2) assert(rendered.includes("never added together"), label + ": mixed units were not labeled");
  for (const b of books) {
    for (const t of b.json.open) {
      let kdLive;
      try { kdLive = S.killDistance(t); }
      catch (e) { throw new Error(label + ": killDistance threw on " + (t.ticker || t.id || b.path) + ": " + e.message); }
      if (kdLive == null) continue;
      assert(typeof kdLive.what === "string" && kdLive.what.length > 0, label + ": kill distance missing a label");
      assert(typeof kdLive.pct === "number" && Number.isFinite(kdLive.pct), label + ": kill distance pct is not a number");
      assert(typeof kdLive.cur === "number" && typeof kdLive.lim === "number", label + ": kill distance readings are not numbers");
    }
  }
}

const live = liveBooks();
assertLiveShape(live, "live");

const changed = JSON.parse(JSON.stringify(live));
for (const b of changed) {
  b.json.open.push({ ticker: "NEW", entry: { holders: 10 }, kill_line: { text: "holders < 4" } });
  b.json.open = b.json.open.filter(t => t.ticker !== "CATE" && t.ticker !== "argonauts");
  b.json.closed.push({ ticker: "GONE", result: { pnl: 1.5, pct: 1.5, unit: b.json.unit || "ADA" } });
}
assertLiveShape(changed, "after tickets open and close");
assert(!changed.some(b => (b.json.open || []).some(t => t.ticker === "CATE" || t.ticker === "argonauts")), "mutation did not drop named tickets");

console.log("smoke-dashboard: OK");
console.log("  fixture ADA", sc.byUnit.ADA.pnl, "ADA-equivalent", sc.byUnit["ADA-equivalent"].pnl, "USD", sc.byUnit.USD.pnl);
console.log("  live books", live.length, "open", live.reduce((n, b) => n + b.json.open.length, 0), "closed", live.reduce((n, b) => n + b.json.closed.length, 0));
