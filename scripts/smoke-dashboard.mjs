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
// pages.js installs nav helpers and returns when there is no document (this sandbox).
vm.runInNewContext(fs.readFileSync(path.join(root, "dashboard/pages.js"), "utf8"), sandbox);
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

const pulse = load("scripts/fixtures/pulse-example.json");
const now = Date.parse("2026-10-18T16:00:00Z");
const fresh = S.ageInfo("2026-10-18T15:48:00Z", now);
assert(fresh.text === "updated 12m ago", "12m age text, got " + fresh.text);
assert(fresh.cls === "age-fresh", "12m stays fresh");
const amber = S.ageInfo(new Date(now - 90 * 60 * 1000).toISOString(), now);
assert(amber.cls === "age-amber", "90m is amber, got " + amber.cls);
const red = S.ageInfo(new Date(now - 6 * 60 * 60 * 1000).toISOString(), now);
assert(red.cls === "age-red", "6h is red, got " + red.cls);
assert(S.ageInfo(null, now).text === "updated —", "missing stamp");
assert(S.ageInfo(new Date(now - 89 * 60 * 1000).toISOString(), now).cls === "age-fresh", "89m stays fresh");
const newest = S.freshestAt({ asof: "2026-10-18T10:00:00Z" }, { updated_at: "2026-10-18T15:48:00Z" });
assert(newest === new Date("2026-10-18T15:48:00Z").toISOString(), "freshest is the newer of log and pulse, got " + newest);
assert(S.freshestAt(null, null) == null, "no stamps");
assert(S.freshestAt({ asof: "not-a-date" }, null) == null, "bad stamp is ignored");

const kdPulse = S.killDistance(cate, pulse);
assert(kdPulse && kdPulse.cur === 100000 && kdPulse.lim === 97082, "pulse metrics override the mark, got " + JSON.stringify(kdPulse));
assert(/holder/.test(kdPulse.what), "pulse still names the holders kill");
assert(JSON.stringify(S.seriesOf(cate, pulse)) === JSON.stringify([120000, 110000, 100000]), "sparkline uses pulse points, got " + S.seriesOf(cate, pulse));
assert(S.killDistance(cate).cur === 108078, "without pulse the mark is unchanged");
const pulseCard = S.ticketCard(cate, kdPulse, "", pulse);
assert(pulseCard.includes("spark"), "pulse ticket draws a sparkline");
assert(pulseCard.includes(S.fmt(100000, 4)), "pulse ticket shows the pulse reading");

const radarHtml = await S.registry.radar.render({
  desk: { id: "example", dir: "example/" },
  data: pulse,
  opt: { now },
  spec: {}
});
function cardOf(html, ticker) {
  const parts = String(html).split('<article class="rc"').slice(1);
  const hit = parts.find(p => p.includes(`title="${ticker}"`));
  assert(hit, "radar card for " + ticker);
  return '<article class="rc"' + hit.split("</article>")[0] + "</article>";
}
function tilesOf(card) {
  const m = card.match(/<div class="tiles">([\s\S]*?)<\/div>/);
  return m ? m[1] : "";
}
function hasTile(tiles, label) {
  return tiles.includes(">" + label + "<") || tiles.includes(">" + label + "<span");
}
assert(radarHtml.indexOf(">NEW<") !== -1 && radarHtml.indexOf(">NEW<") < radarHtml.indexOf(">OLD<"), "radar ranks the higher score first");
assert(radarHtml.includes("burst") && radarHtml.includes("new pool"), "factors-absent radar still shows the prose");
assert(radarHtml.includes("rawsig"), "factors-absent radar keeps a raw-text face");
assert(!radarHtml.includes("sbar"), "radar no longer draws the old score bar");
assert(radarHtml.includes(">100<") && radarHtml.includes("raw 81"), "top score fills the ring, raw score stays under it");
assert(radarHtml.includes(">49<") && radarHtml.includes("raw 40"), "lower score is normalized to the desk top");
assert(radarHtml.includes("4h old"), "radar shows first-seen age");
assert(!radarHtml.includes("seen 4h ago"), "age chip replaced the old seen line");
assert(radarHtml.includes(">example<"), "example pulse is labelled");
assert(radarHtml.includes("spark"), "radar sparkline uses pulse points");
assert(radarHtml.includes("Momentum map"), "radar draws the momentum map");
assert(!hasTile(tilesOf(cardOf(radarHtml, "NEW")), "holders/h"), "factors-absent card hides metric tiles");
const quiet = await S.registry.radar.render({ desk: { id: "solada", dir: "solada/" }, data: null, opt: {}, spec: {} });
assert(quiet.includes("No early runners yet"), "missing pulse degrades quietly");
assert(!quiet.includes("example"), "missing pulse is not labelled example");

const factPulse = load("scripts/fixtures/pulse-factors.json");
const factHtml = await S.registry.radar.render({
  desk: { id: "example", dir: "example/" },
  data: factPulse,
  opt: { now },
  spec: {}
});
const fact = cardOf(factHtml, "FACT");
const factTiles = tilesOf(fact);
assert(fact.includes(">50<") && fact.includes("raw 20"), "score_max scales the ring, got " + (fact.match(/<b>\d+<\/b><i>[^<]+/) || [])[0]);
assert(hasTile(factTiles, "holders/h") && factTiles.includes("+1.5"), "structured holders/h wins over the signal prose");
assert(!factTiles.includes("21.3"), "regex holder rate does not override a measured factor");
assert(!hasTile(factTiles, "accel"), "null accel is hidden");
assert(hasTile(factTiles, "curve · done") && factTiles.includes("minswap-v2") && factTiles.includes("100%"), "graduated minswap-v2 draws a finished curve");
assert(factTiles.includes(">0<") && factTiles.includes("buys / sells"), "a real zero buy count stays on the card");
assert(!hasTile(factTiles, "price h6"), "omitted price_h6_pct is not a tile");
assert(fact.includes("SWARM") && fact.includes('class="bdg warn"'), "known flag uses its short label and tone");
assert(fact.includes("mystery_flag") && fact.includes('class="bdg bad"'), "unknown flag code is shown as its own text");
const youngTiles = tilesOf(cardOf(factHtml, "YOUNG"));
assert(!hasTile(youngTiles, "holders/h") && !hasTile(youngTiles, "native/h") && !hasTile(youngTiles, "accel"), "names under 1h hide missing velocity tiles");
assert(!youngTiles.includes("price h6") && hasTile(youngTiles, "price h1"), "SER-style omit of price_h6_pct leaves price h1");
assert(!cardOf(factHtml, "YOUNG").includes('class="bdg'), "an empty flags list does not invent badges");
const flatTiles = tilesOf(cardOf(factHtml, "FLAT"));
assert(hasTile(flatTiles, "holders/h") && flatTiles.includes("flat"), "holders_per_h 0 is a flat reading, not a hidden tile");

const rulesHtml = S.registry.rules.render({
  data: [
    { json: { book: "solada", rules: [
      { id: "j", rule: "body that is not the title", added_at: "2026-10-17T12:00:00Z" },
      { id: "z", rule: "buy the dip now", title: "Structured title wins", category: "kill", evidence: ["BOLT"], added_at: "2026-10-18T12:00:00Z" }
    ] } },
    { json: { book: "pumpfun", rules: [
      { id: "j", rule: "pump copy", title: "Pump copy of the sniper exit", category: "kill", mirrors: "solada:j", evidence: ["FTR"], added_at: "2026-10-18T11:00:00Z" },
      { id: "x", rule: "no such twin", title: "Unresolved mirror stays text", category: "entry", mirrors: "solada:nope", added_at: "2026-10-16T12:00:00Z" }
    ] } }
  ],
  opt: { limit: 2 }
});
assert(rulesHtml.includes("<b>Sniper exit = kill</b>"), "id without a title still uses the book table");
assert(rulesHtml.includes("<b>Structured title wins</b>"), "structured title wins over the rule body");
assert(!rulesHtml.includes("<b>buy the dip"), "titles do not keep a leading buy");
assert(rulesHtml.includes("solada:j · Sniper exit = kill"), "a mirror resolves to the other book's title");
assert(rulesHtml.includes("solada:nope") && !rulesHtml.includes("solada:nope ·"), "an unresolved mirror is plain text");
assert(rulesHtml.includes("Show all 4 rules"), "limit hides the rest behind Show all");
assert(rulesHtml.includes('class="rchip'), "rules draw filter chips");

const wired = S.runButton({ id: "solada", run: { url: "https://github.com/Zirqo-Dev/sightline/actions/workflows/run-solada.yml", label: "RUN NOW" } });
assert(wired.includes('target="_blank"') && wired.includes("run-solada.yml"), "run button opens the workflow");
assert(!/WEBHOOK|SOLADA_RUN|NFT_RUN|Authorization|secret/i.test(wired), "run button embeds no secret");
assert(wired.includes("hidden"), "hint stays hidden until the click");
const asked = S.runButton({ id: "solada", run: { url: "https://example.invalid/run", label: "RUN NOW" } }, { pending: true });
assert(asked.includes("run requested, refresh in a few minutes") && !asked.includes("hidden"), "pending run shows the hint");
const unwired = S.runButton({ id: "later" });
assert(unwired.includes("disabled") && unwired.includes("not wired yet"), "missing run.url is disabled");
assert(!unwired.includes("href="), "disabled run button is not a link");

const desksIdx = load("desks.json");
const byId = Object.fromEntries(desksIdx.desks.map(d => [d.id, d]));
assert(byId.solada.run.url.endsWith("/run-solada.yml"), "solada run url");
assert(byId.nft.run.url.endsWith("/run-nft.yml"), "nft run url");
assert(S.pulsePath({ id: "solada", dir: "solada/", pulse: "solada/pulse.json" }) === "solada/pulse.json", "pulse path comes from desks.json");
assert(S.pulsePath({ id: "nft", dir: "nft/" }) === "nft/pulse.json", "missing pulse field falls back to <desk>/pulse.json");
assert(byId.solada.pulse === "solada/pulse.json", "solada lists its pulse file");
assert(byId.nft.pulse === "nft/pulse.json", "nft lists its pulse file");
assert(S.pulsePath({ id: "nft", dir: "nft/", pulse: byId.nft.pulse }) === "nft/pulse.json", "nft pulse path is kept");
const solPulse = load("solada/pulse.json");
const solRadar = await S.registry.radar.render({ desk: { id: "solada", dir: "solada/", pulse: byId.solada.pulse }, data: solPulse, opt: {}, spec: {} });
assert(!/>example</.test(solRadar), "real solada pulse is not labelled example");
assert(Array.isArray(solPulse.radar) && solPulse.radar.length > 0 && solRadar.includes(solPulse.radar[0].ticker), "solada radar renders a real name");
assert(solRadar.includes("Momentum map") && !solRadar.includes("sbar"), "live solada radar uses the card view");
for (const row of solPulse.radar) {
  const card = cardOf(solRadar, row.ticker);
  const tiles = tilesOf(card);
  const f = row.factors || {};
  const holderLabel = String(f.holders_basis || "").toLowerCase() === "native" ? "native/h" : "holders/h";
  if (f.holders_per_h == null) assert(!hasTile(tiles, "holders/h") && !hasTile(tiles, "native/h"), row.ticker + " hides a missing holders rate");
  else assert(hasTile(tiles, holderLabel), row.ticker + " shows its holders rate");
  if (f.accel_per_h == null) assert(!hasTile(tiles, "accel"), row.ticker + " hides a missing accel");
  if (f.vol_x == null) assert(!hasTile(tiles, "vol ×h-avg"), row.ticker + " hides a missing volume multiple");
  if (f.price_h6_pct == null) assert(!hasTile(tiles, "price h6"), row.ticker + " does not invent a price h6 tile");
  if (f.graduated && f.curve_pct == null) assert(tiles.includes("curve · done") && tiles.includes(String(f.graduated)), row.ticker + " shows graduated " + f.graduated);
  if (Array.isArray(row.flags)) {
    for (const fl of row.flags) assert(card.includes(String(fl.code)) || card.includes("bdg"), row.ticker + " renders its flags");
  }
}
const nftPulse = load("nft/pulse.json");
const nftRadar = await S.registry.radar.render({ desk: { id: "nft", dir: "nft/", pulse: byId.nft.pulse }, data: nftPulse, opt: {}, spec: {} });
assert(nftPulse.radar && nftPulse.radar[0] && nftRadar.includes(nftPulse.radar[0].ticker), "nft radar renders a real name");
assert(nftRadar.includes("Momentum map") && (nftRadar.includes("BROAD") || nftRadar.includes("FRESH") || nftRadar.includes("FIRST")), "nft flags use short labels");
assert(S.panelAsRadar({ id: "nft", dir: "nft/" }, { type: "table", data: "pulse.json", path: "radar" }) === true, "nft table path radar maps to the radar view");
assert(S.panelAsRadar({ id: "nft", dir: "nft/", pulse: "nft/pulse.json" }, { type: "table", data: ["nft/pulse.json"], path: "radar" }) === true, "listed pulse path still maps");
assert(S.panelAsRadar({ id: "nft", dir: "nft/" }, { type: "table", data: "pulse.json", path: "latest" }) === false, "tracked-names table stays a table");
const liveRules = S.registry.rules.render({
  data: [{ json: load("solada/paper.json") }, { json: load("solada/pumpfun/paper.json") }],
  opt: { limit: 8 }
});
const solJ = (load("solada/paper.json").rules || []).find(x => x.id === "j");
assert(solJ && solJ.title && liveRules.includes("solada:j · " + solJ.title), "pump.fun mirror solada:j resolves");
assert(!liveRules.includes("mirror not in this list</span>solada:nope") && liveRules.includes("Show all"), "live rules render with Show all");
const nftRadarTable = S.registry.table.render({
  data: nftPulse.radar,
  opt: { columns: ["ticker", "chain", "score", "first_seen", "note"], labels: { ticker: "Collection", first_seen: "First seen" }, limit: 10 }
});
assert(nftRadarTable.includes("tbl"), "nft radar table renders");
if (nftPulse.radar && nftPulse.radar[0]) assert(nftRadarTable.includes(nftPulse.radar[0].ticker), "nft radar table shows a real name");
const nftLatest = S.registry.table.render({
  data: nftPulse.latest,
  opt: { columns: ["ticker", "chain", "role", "floor", "unit"], badge: "role" }
});
assert(nftLatest.includes("tbl") && nftPulse.latest && nftLatest.includes(nftPulse.latest[0].ticker), "nft tracked-names table renders");
for (const name of ["run-solada.yml", "run-nft.yml"]) {
  const yml = fs.readFileSync(path.join(root, ".github/workflows", name), "utf8");
  assert(yml.includes("workflow_dispatch"), name + " is manual");
  assert(!yml.includes("pull_request") && !yml.includes("push:"), name + " does not run on its own");
  if (name === "run-solada.yml") {
    assert(yml.includes('Authorization: Bearer $KEY'), name + " sends the bearer header");
    assert(yml.includes("exit 1"), name + " fails loudly when unset");
  } else {
    assert(yml.includes('Authorization: $AUTH'), name + " sends the auth header");
    assert(yml.includes("exit 0"), name + " exits cleanly when unset");
    assert(yml.includes("Authorization:' prefix"), name + " documents the header paste");
  }
  assert(!yml.includes("X-Webhook-Key"), name + " does not use the old header");
}
assert(fs.readFileSync(path.join(root, ".github/workflows/run-solada.yml"), "utf8").includes("SOLADA_WEBHOOK_URL"));
assert(fs.readFileSync(path.join(root, ".github/workflows/run-solada.yml"), "utf8").includes("SOLADA_WEBHOOK_KEY"));
assert(fs.readFileSync(path.join(root, ".github/workflows/run-nft.yml"), "utf8").includes("NFT_RUN_WEBHOOK_URL"));
assert(fs.readFileSync(path.join(root, ".github/workflows/run-nft.yml"), "utf8").includes("NFT_RUN_WEBHOOK_AUTH"));

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

/* Desk data is read from the desk-data branch (raw.githubusercontent), main's copy is the fallback. */
for (const p of ["solada/log.json", "nft/log.json", "solada/pulse.json", "nft/pulse.json", "solada/paper.json",
  "solada/pumpfun/paper.json", "nft/paper.json", "solada/briefs/2026-10-19.json", "nft/site/intel.json", "solada/forensics/panel.json"]) {
  assert(S.isDeskData(p), p + " should be read from desk-data");
  assert(S.deskDataUrl(p).startsWith("https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/" + p + "?ts="), p + " desk-data url");
}
for (const p of ["desks.json", "solada/panels.json", "nft/panels.json", "solada/briefs/index.json", "dashboard/icons/icons.json",
  "schema/run-log.schema.json", "solada/forensics/config.json", "solada/README.md"]) {
  assert(!S.isDeskData(p), p + " must stay on main");
}
for (const d of load("desks.json").desks) {
  assert((d.log == null || S.isDeskData(d.log)) && S.isDeskData(d.pulse), d.id + " log and pulse come from desk-data");
  for (const b of d.books || []) assert(S.isDeskData(b.paper), d.id + " paper comes from desk-data");
}
assert(S.pickNewer({ asof: "2026-10-25T14:00:00Z" }, { asof: "2026-10-23T09:00:00Z" }).asof.startsWith("2026-10-25"), "desk-data newer wins");
assert(S.pickNewer(null, { asof: "2026-10-23T09:00:00Z" }).asof.startsWith("2026-10-23"), "missing on desk-data falls back to main");
assert(S.pickNewer({ asof: "2026-10-20T00:00:00Z" }, { asof: "2026-10-23T09:00:00Z" }).asof.startsWith("2026-10-23"), "strictly newer main copy wins");
assert(S.pickNewer({ x: 1 }, { x: 2 }).x === 1, "no stamps: desk-data wins");
{
  const seen = [];
  sandbox.fetch = async u => {
    seen.push(u);
    if (u.startsWith("https://raw.githubusercontent.com/") && u.includes("solada/log.json")) return { ok: true, json: async () => ({ asof: "2026-10-25T14:00:00Z" }) };
    if (u.startsWith("https://raw.githubusercontent.com/")) return { ok: false, json: async () => null };
    return { ok: true, json: async () => ({ asof: "2026-10-23T09:00:00Z", from: "main" }) };
  };
  const solLog = await S.get("solada/log.json");
  assert(solLog.asof === "2026-10-25T14:00:00Z", "get() returns the desk-data log");
  const nftLog = await S.get("nft/log.json");
  assert(nftLog.from === "main", "get() falls back to main when desk-data 404s");
  await S.get("desks.json");
  assert(!seen.some(u => u.includes("raw.githubusercontent") && u.includes("desks.json")), "desks.json never comes from desk-data");
  assert(S.freshestAt(solLog, null) === "2026-10-25T14:00:00.000Z", "freshness stamp uses the desk-data asof");
  delete sandbox.fetch;
}
const nftHtml = fs.readFileSync(path.join(root, "nft/site/index.html"), "utf8");
assert(nftHtml.includes("raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/nft/site/") && nftHtml.includes("sightlineData"), "nft site reads desk-data");
for (const f of ["intel-boot.js", "watchlist-boot.js", "desk-guards.js"]) {
  assert(fs.readFileSync(path.join(root, "nft/site", f), "utf8").includes("window.sightlineData"), f + " uses sightlineData");
}

// solada-intel panel: fixtures for the cardano_top10 field Snek adds tonight, flags.json, and empty states.
{
  const si = load("scripts/fixtures/solada-intel.json");
  const def = S.registry["solada-intel"];
  assert(def, "solada-intel panel registered");
  const data = j => [{ path: "log.json", json: j.log }, { path: "pulse.json", json: j.pulse }, { path: "paper.json", json: null }, { path: "/solada-prefilter/flags.json", json: j.flags }];
  const html = def.render({ data: data(si), opt: { now: si.now } });
  for (const want of ["CLOVE", "ZKOWL", "identical size buys", "sev-high", "Whale", "Cluster heat grid", "Pattern scoreboard", "Research only, not advice.", "exited", "held", "▼6", "▲12", "net -4,536 ADA"])
    assert(html.includes(want), "solada-intel shows " + want);
  assert(html.indexOf(">1<") < html.indexOf("CLOVE") || html.indexOf("ZKOWL") < html.indexOf("CLOVE"), "top 10 sorted by rank");
  assert(html.indexOf("ASCEND: no trades") < html.indexOf("NIGHT: sold"), "whale card puts ASCEND/STRIKE first");
  assert(!html.includes("coordinated buys</span><span class=\"ts\""), "flag strip only shows the last 24h");
  assert(/<svg class="spark/.test(html), "drill-down sparkline from pulse");
  assert(html.includes("Outcomes not tracked yet"), "scoreboard empty outcome state");
  // empty: no cardano_top10, no flags.json, no founder whale
  const empty = def.render({ data: [{ path: "log.json", json: { asof: "x" } }, { path: "pulse.json", json: null }, { path: "paper.json", json: null }, { path: "/solada-prefilter/flags.json", json: null }], opt: {} });
  assert(empty.includes("Top 10 not published yet") && empty.includes("flags.json not published yet") && !empty.includes("Founder whale"), "solada-intel empty states");
  // privacy: full keys in a flag never render, only tags
  const leak = JSON.parse(JSON.stringify(si));
  const fakeKey = "stake1u" + "x".repeat(52);
  leak.flags.new.push({ kind: "coordinated_buys", ticker: "LEAK", wallets: [fakeKey, "addr1q" + "y".repeat(90)], at: si.now });
  leak.log.cardano_top10[0].whale_tags.push(fakeKey);
  const lh = def.render({ data: data(leak), opt: { now: si.now } });
  assert(!lh.includes("xxxxxxxxxx") && !lh.includes("yyyyyyyyyy"), "solada-intel never renders full addresses or stake keys");
  const sp = load("solada/panels.json").panels.find(p => p.type === "solada-intel");
  assert(sp && sp.data.includes("/solada-prefilter/flags.json") && S.isDeskData("solada-prefilter/flags.json"), "flags.json is read via the desk-data loader");
}

// solana-desk panel: health, leads, paper book and runs from fixtures; empty states before fills.
{
  const def = S.registry["solana-desk"];
  assert(def, "solana-desk panel registered");
  const fx = n => fs.readFileSync(path.join(root, "scripts/fixtures/solana-desk", n), "utf8");
  const wrap = h => [{ path: "/solana-desk/health.json", json: h }, { path: "/solana-desk/state.json", json: null }, { path: "/solana-desk/paper.json", json: null }];
  const h = JSON.parse(fx("health.json"));
  const full = await def.render({ data: wrap(h), opt: { now: "2026-10-25T20:10:00Z", csv: { tasks: fx("tasks.csv"), trades: fx("trades.csv") } } });
  for (const want of ["20 / 48", "3.3%", "void ≥ 10%", "57.1 min", "v1-draft-v6.5", "draft", "run 20261025T2000", "dexscreener.com/solana/FakeMintCCCC", "geckoterminal.com/solana/tokens/", "no sol pair", "eligibility 1", "pass 2", "Open positions", "Closed trades", "50%", "+0.046", "FAKEC", "2 runs", "Research only, not advice."])
    assert(full.includes(want), "solana-desk shows " + want);
  assert(S.solanaCsv(fx("tasks.csv")).length === 6, "repeated CSV header rows are dropped");
  const fillsH = await def.render({ data: wrap(h).concat([{ path: "/solana-desk/paper.json", json: JSON.parse(fx("paper-fills.json")) }]), opt: { now: "2026-11-01T03:30:00Z", csv: { tasks: fx("tasks.csv"), trades: fx("trades-fills.csv") } } });
  for (const want of ["Open positions", "Closed trades", "T1", "T3", "take_profit_40", "hard_stop"])
    assert(fillsH.includes(want), `solana-desk fill-step fixture renders: ${want}`);
  assert(!fillsH.includes("No paper fills yet"), "fill-step fixture is not the empty state");
  const voidH = await def.render({ data: wrap(JSON.parse(fx("health-void.json"))), opt: { csv: { tasks: "", trades: "" } } });
  assert(voidH.includes("void day") && voidH.includes("frozen") && voidH.includes("12.5%"), "void day and frozen state");
  assert(voidH.includes("No paper fills yet") && voidH.includes("tasks.csv not published yet"), "solana-desk empty states before fills");
  const none = await def.render({ data: wrap(null), opt: { csv: { tasks: null, trades: null } } });
  assert(none.includes("health.json not published yet"), "solana-desk with no data");
  const sd = load("desks.json").desks.find(d => d.id === "solana");
  assert(sd && sd.run.url.endsWith("solana-desk-scan.yml") && sd.bot === "Grok Bot", "solana desk listed with its run button");
  const sp = load("solana/panels.json").panels[0];
  assert(sp.type === "solana-desk" && sp.data.every(p => S.isDeskData(p.slice(1))), "solana desk reads desk-data");
  const css = fs.readFileSync(path.join(root, "dashboard/app.css"), "utf8");
  assert(css.includes("#9945FF") && css.includes("#14F195") && css.includes("#0033AD"), "per-desk themes present");
}

// live-site fixes: binary asset names, pre-fix raw-unit whale moves, health-based home card, cache-busting.
{
  const def = S.registry["solada-intel"];
  const garbled = "KR4UT^:WIS\nB<VR[J(\u0003";
  const fj = { new: [
    { kind: "founder_whale_move", ticker: "FLOW", tag: "w-f3186187", delta: 224026586, ada_equiv: 68736323, at: "2026-10-25T20:08:43Z" },
    { kind: "founder_whale_move", ticker: garbled, tag: "w-f3186187", delta: 11042717, at: "2026-10-25T20:08:43Z" },
    { kind: "founder_whale_move", ticker: "NIGHT", tag: "w-f3186187", delta: -25000, ada_equiv: 4536, value_basis: "ada_leg", at: "2026-10-25T20:08:43Z" }] };
  const html = def.render({ data: [{ path: "log.json", json: {} }, { path: "pulse.json", json: null }, { path: "paper.json", json: null }, { path: "/solada-prefilter/flags.json", json: fj }], opt: { now: "2026-10-25T20:30:00Z" } });
  assert(!html.includes("KR4UT") && html.includes("LP·4b52…"), "binary asset name shown as LP·hex");
  assert(!html.includes("68,736,323") && html.includes("raw units · unvalued"), "pre-fix raw-unit valuation hidden");
  assert(html.includes("≈4,536₳"), "valued moves keep their ADA equivalent");
  const h = JSON.parse(fs.readFileSync(path.join(root, "scripts/fixtures/solana-desk/health.json"), "utf8"));
  assert(S.healthAt(h) === "2026-10-25T20:00:00Z" && S.freshestAt(null, null, h) === "2026-10-25T20:00:00.000Z", "health.json drives freshness");
  const sd = load("desks.json").desks.find(d => d.id === "solana");
  assert(sd.health && S.isDeskData(sd.health), "solana desk health.json comes from desk-data");
  const pages = fs.readFileSync(path.join(root, "dashboard/pages.js"), "utf8");
  assert(pages.includes("healthLine(health)") && pages.includes("scan only"), "home card uses health.json");
  const build = fs.readFileSync(path.join(root, "scripts/build-site.py"), "utf8");
  assert(build.includes("def bust(") && build.includes("?v="), "build adds cache-busting asset versions");
}

// Top nav: label and order only. solada route, theme key, status dots, and desk title stay.
{
  assert(typeof S.navTabs === "function" && typeof S.navMarkup === "function" && typeof S.canonDesk === "function", "nav helpers installed");
  const idx = load("desks.json");
  const solada = idx.desks.find(d => d.id === "solada");
  assert(solada && solada.name === "Snek Sentinel: Solada/Cardano", "solada desk title is unchanged");
  assert(solada.log === "solada/log.json" && solada.briefs === "solada/briefs/" && solada.pulse === "solada/pulse.json", "solada data paths unchanged");
  assert(idx.desks.map(d => d.id).join(",") === "solada,nft,solana", "desks.json order is unchanged");
  const live = idx.desks.map(d => ({ id: d.id, status: d.status }));
  const html = S.navMarkup("solada", live);
  const labels = [...html.matchAll(/>(?:<span class="dot[^"]*"><\/span>)?([^<]+)<\/a>/g)].map(m => m[1]);
  assert(labels.join(",") === "HOME,CARDANO,SOLANA,NFT,BRIEFS", "nav labels and order, got " + labels.join(","));
  assert(html.includes('href="#/desk/solada"') && html.includes('data-d="solada"'), "CARDANO tab keeps the solada route and theme key");
  assert(html.includes('href="#/desk/solada" class="on" data-d="solada"><span class="dot "></span>CARDANO'), "active solada tab is CARDANO with a live dot");
  assert(!html.includes(">SOLADA<") && !html.includes("#/desk/cardano"), "nav does not rename the route");
  assert(S.canonDesk("cardano") === "solada" && S.canonDesk("solada") === "solada", "cardano alias resolves to solada");
  assert(S.canonDesk("nft") === "nft" && S.canonDesk("solana") === "solana" && S.canonDesk("home") === "home" && S.canonDesk("briefs") === "briefs", "other routes unchanged");
  assert(S.navMarkup("cardano", live).includes('href="#/desk/solada" class="on"'), "cardano alias highlights the CARDANO tab");
  assert(S.navMarkup("home", live).includes('href="#/" class="on" data-d="home">HOME'), "home tab highlights");
  assert(S.navMarkup("briefs", live).includes('href="#/briefs" class="on" data-d="briefs">BRIEFS'), "briefs tab highlights");
  const pending = S.navMarkup("home", [{ id: "solada", status: "paused" }, { id: "nft", status: "active" }, { id: "solana", status: "active" }]);
  assert(pending.includes('data-d="solada"><span class="dot pending"></span>CARDANO'), "status dot still follows desk status");
  assert(pending.includes('data-d="solana"><span class="dot "></span>SOLANA') && pending.includes('data-d="nft"><span class="dot "></span>NFT'), "active desks keep the live dot");
  const extra = S.navTabs([{ id: "nft", status: "active" }, { id: "zeta", status: "active" }, { id: "solada", status: "active" }, { id: "solana", status: "active" }]);
  assert(extra.map(t => t.label).join(",") === "HOME,CARDANO,SOLANA,NFT,ZETA,BRIEFS", "unknown desks stay after the fixed order, got " + extra.map(t => t.label).join(","));
  assert(extra.map(t => t.href).join(",") === "#/,#/desk/solada,#/desk/solana,#/desk/nft,#/desk/zeta,#/briefs", "hrefs stay on desk ids");
  const pages = fs.readFileSync(path.join(root, "dashboard/pages.js"), "utf8");
  assert(pages.includes("canonDesk(id)") && pages.includes("navMarkup(id, desks)"), "desk page resolves aliases before render");
  const css = fs.readFileSync(path.join(root, "dashboard/app.css"), "utf8");
  assert(css.includes('body[data-desk="solada"]') && css.includes('nav a[data-d="solada"]') && css.includes("#0033AD"), "Cardano-blue theme stays keyed to solada");
}

{
  const previewJs = path.join(root, "preview/app.js");
  const boardJs = path.join(root, "preview/cardano-v2.js");
  for (const file of [previewJs, boardJs]) {
    const parsed = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
    if (parsed.status !== 0) throw new Error(file + " failed to parse\n" + (parsed.stderr || parsed.stdout));
  }
  const pbox = { console, matchMedia: () => ({ matches: true }) };
  pbox.window = pbox;
  pbox.globalThis = pbox;
  vm.runInNewContext(fs.readFileSync(boardJs, "utf8"), pbox);
  vm.runInNewContext(fs.readFileSync(previewJs, "utf8"), pbox);
  const P = pbox.Preview;
  assert(P && P.safeTag("w-0123abcd") === "w-0123abcd", "w- hash is kept");
  assert(P.safeTag("W-ABCDEF01") === "w-abcdef01", "w- hash is normalized");
  assert(P.safeTag("dev") === "dev" && P.safeTag("whale-1") === "whale-1", "role labels are kept");
  assert(P.safeTag("founder_whale") === "founder_whale", "known role label is kept");
  const stake = "stake1u" + "x".repeat(52);
  const addr = "addr1q" + "y".repeat(90);
  assert(P.safeTag(stake) == null && P.safeTag(addr) == null && P.safeTag("u8a2m6") == null, "stake keys, addresses, and prefixes are dropped");
  assert(!P.sanitize("paid " + stake + " and " + addr).includes("stake1") && !P.sanitize(addr).includes("addr1"), "prose drops wallet ids");
  const whale = P.whaleHtml({
    ada_balance: 123456.78,
    positions: [{ token: "SECRETBAG", qty: 999 }],
    whale_tag: "founder_whale",
    rows: [{ time: "2026-10-28T00:00:00Z", kind: "swap", token: "SNEK", ada_leg: 12, qty: 1000, counterparty_tag: addr }]
  });
  assert(!whale.includes("123456") && !whale.includes("SECRETBAG") && !whale.includes("addr1") && !whale.includes("999"), "whale ledger hides balances, bags, and addresses");
  assert(!whale.includes("founder") && whale.includes("SNEK"), "whale chart keeps the token and drops founder wording");
  const dev = P.devHtml({ entries: [
    { ticker: "SNEK", risk: "caution", dev_tag: "dev", fresh_funded: true, dev_sold_ada: 10 },
    { ticker: "NOPE", dev_tag: stake, fresh_funded: false }
  ] });
  assert(dev.includes("fresh funded") && dev.includes(">dev<"), "fresh funded chip and dev role render");
  assert(!dev.includes("stake1"), "dev tag that is a stake key is not rendered");
  const cardano = P.cardanoHtml({
    log: {
      founder_whale: { handle: "$cockwhale", address: "w-f3186187", stake_prefix: "uylu74g0", net_flow_ada: 12, summary: "the founder sold SNEK founder_pct", line: "ASCEND flat " + "B".repeat(44), cock_order: "order " + addr + " " + stake },
      learned_or_changed: ["A gift from an outside wallet is not a buy."],
      history: [
        { asof: "2026-10-28T00:00:00Z", founder_whale: { net_flow_ada: -4, handle: "Grumpy Walrus" } },
        { asof: "2026-10-29T00:00:00Z", founder_whale: { net_flow_ada: 12, handle: "Grumpy Walrus" } }
      ],
      whale_watch: { wallets: [{ tag: "w-f3186187", codename: "Grumpy Walrus", prefix: stake, label: "$cockwhale", latest: "pool " + addr }] },
      risk_check: { SNEK: { risk: "High risk", tags: ["Dev sold"], source: "scanner" }, ASCEND: { risk: "Low risk", tags: [], source: "dev_checks" } },
      cardano_top10: [{ rank: 1, ticker: "SNEK", ada_side_depth: 9000, whale_tags: ["w-0123abcd", stake] }, { rank: 2, ticker: "NIGHT", ada_side_depth: 5000 }],
      flag_outcomes: { sniper_full_exit: { pump: 2, rug: 1 }, founder_whale_move: { pump: 0, rug: 0 } },
      kill_hits: ["BOLT holders under 265"],
      trigger: "manual"
    },
    flags: { new: [{ kind: "coordinated_buys", ticker: "COOKED" }, { kind: "sniper_full_exit", ticker: "COOKED" }] },
    kills: { BOLT: { hit: true, line: 265, value: 200, metric: "holders" } },
    pump: { open: [], closed: [{ ticker: "X", mint: "C".repeat(44), result: { pnl: -1, pct: -1 } }], unit: "ADA-equivalent" }
  });
  assert(!cardano.includes("addr1") && !cardano.includes("stake1") && !cardano.includes("uylu74g0") && !cardano.includes("cock_order"), "address, stake prefix, and order stay off the page");
  const retired = String.fromCharCode(115, 117, 114, 105);
  assert(!/founder|cockwhale|Lead whale|COCK LP|Snek depth/i.test(cardano), "identifying whale wording and old panel names stay off the page");
  assert(!cardano.toLowerCase().includes(retired) && !cardano.includes("on the name"), "retired scanner name and old panel subtitle stay off the page");
  assert(cardano.includes("Grumpy Walrus") && cardano.includes("Pool ADA") && !cardano.includes(stake), "codename and pool label render, and the stake key stays hidden");
  assert(cardano.indexOf("BOLT") < cardano.indexOf("SNEK") && cardano.includes("% under") && !cardano.includes(">Core<"), "closest kill line leads the board");
  assert(cardano.includes("ASCEND") && cardano.includes("NIGHT") && cardano.includes("GOOD TOKENS BOARD"), "names and the kill-line board still render");
  assert(cardano.includes("Flagged this run") && cardano.includes("COOKED") && cardano.includes("Risk score") && cardano.includes("risk scanner") && cardano.includes("dev_checks"), "flag row, risk score legend, and both sources render");
  const legacyLog = {
    cardano_top10: [{ rank: 1, ticker: "SNEK", ada_side_depth: 1000 }]
  };
  legacyLog[retired] = { SNEK: { risk: "Low risk", tags: ["Dev locked"], source: retired } };
  const legacy = P.cardanoHtml({ log: legacyLog });
  assert(legacy.includes("Risk score") && legacy.includes("risk scanner") && legacy.includes("SNEK") && !legacy.toLowerCase().includes(retired), "old snapshots still render a risk score");
  const both = { cardano_top10: [{ ticker: "SNEK", ada_side_depth: 1 }] };
  both.risk_check = { SNEK: { risk: "High risk", tags: [], source: "scanner" } };
  both[retired] = { SNEK: { risk: "Low risk", tags: [], source: retired } };
  const prefer = P.cardanoHtml({ log: both });
  assert(prefer.includes("Risk score: High risk") && !prefer.includes("Risk score: Low risk"), "risk_check wins when an old snapshot key is also present");
  assert(cardano.includes("Grumpy Walrus move") && !/founder_whale|founder_whale_move|founder_pct|\bfounder\b/i.test(cardano), "whale-move flag uses the codename, not the data key");
  assert(cardano.includes("What changed this run") && cardano.includes("outside wallet is not a buy"), "learned lines render as their own block");
  assert(cardano.includes("net +12 ADA") && cardano.includes("cv2-spark"), "net flow and its history render on the whale card");
  assert(cardano.includes("FLAG OUTCOMES") && cardano.includes("2 pump") && cardano.includes("1 rug") && cardano.includes("coordinated buys"), "each flag outcome shows its counts");
  const preferred = P.cardanoHtml({ log: {
    whales: [{ codename: "Sleepy Otter", role: "watch", status: "candidate", net_flow_ada: -5 }],
    founder_whale: { handle: "Grumpy Walrus", net_flow_ada: 99 }
  }});
  assert(preferred.includes("Sleepy Otter") && preferred.includes("net -5 ADA") && !preferred.includes("net +99") && !/founder/i.test(preferred), "whales[] wins and the old object is not required");
  assert(!cardano.includes("B".repeat(32)) && !cardano.includes("C".repeat(32)), "forensics and pumpfun address-like values stay hidden");
  const v2 = load("scripts/fixtures/cardano-v2.json");
  const full = P.cardanoHtml(v2);
  const cock = full.split('data-tk="COCK"')[1].split("</article>")[0];
  assert(cock.includes('data-fill="54"') && cock.includes('data-tick="50"') && cock.includes("only 9% above") && cock.includes("🔴 Pass"), "COCK kill bar matches the approved mockup");
  assert(full.indexOf('data-tk="COCK"') < full.indexOf('data-tk="STRIKE"') && full.indexOf('data-tk="STRIKE"') < full.indexOf('data-tk="ASCEND"') && full.indexOf('data-tk="ASCEND"') < full.indexOf('data-tk="CLOVE"'), "board sorts by closeness to the kill line");
  assert(full.includes("Lottery-size candidate") && full.includes('data-cv2-filter="g"') && full.includes("New launch other"), "verdict chips and launchpad badge render");
  assert(full.includes("WHERE THE MONEY IS HEADING") && full.includes("MONEY OUT") && full.includes("MONEY IN") && full.includes("Selling SNEK") && full.includes("ASCEND accumulating") && full.includes("SNEK exiting"), "rotation card renders out, in, and trends");
  assert(full.includes("Swarm group A") && full.includes("repeat early buyers") && full.includes("FOSHIZLE"), "swarm group card renders");
  assert(full.includes("Sniper exits") && full.includes("CHEESE, INTEL"), "sniper card renders");
  assert(full.includes("Hot right now") && full.includes("MEPE"), "hot overlap renders");
  assert(full.includes("NEW LAUNCH RADAR") && full.includes("74 / 5") && full.includes("Getting traction") && full.includes("Swarm launch → Pass") && full.includes("65% of the way"), "launch radar stats, traction, and watching states render");
  assert(full.includes("EARLY-BUYER CLUSTERS") && full.includes("show wallets (4)") && full.includes("w-8315deec") && full.includes("Coordinated buys"), "cluster cards group wallets behind a tap");
  assert(full.includes("HEAT GRID") && full.includes("Show all wallets (10)") && full.includes("cv2-more") && full.includes("c66a") && full.includes("ring"), "heat grid pins labels and hides rows past the top 8");
  assert(!/founder|suri|suriradar/i.test(full) && !full.includes("stake1") && !full.includes("addr1"), "fixture stays free of wallet ids and retired names");
  const quiet = P.cardanoHtml({});
  assert(quiet.includes("GOOD TOKENS BOARD") && quiet.includes("No data yet") && quiet.includes("NEW LAUNCH RADAR") && quiet.includes("HEAT GRID"), "missing fields render an empty state");
  const partial = P.cardanoHtml({ log: { verdicts: [{ ticker: "COCK", verdict: "pass", launchpad: "minswap", pool_ada: 163300, kill_metric: "pool_ada", kill_value: 163300, kill_limit: 150000, reasons: ["swarm key uylu74g0 sold"], whale_activity: "none tracked" }] } });
  assert(partial.includes("only 9% above") && partial.includes("New launch other") && !partial.includes("uylu74g0") && partial.includes("No data yet"), "a verdict renders its bar, and a stake-like token in a reason is dropped");
  const paper = load("scripts/fixtures/solana-desk/paper-fills.json");
  const trades = P.parseCsv(fs.readFileSync(path.join(root, "scripts/fixtures/solana-desk/trades-fills.csv"), "utf8"));
  const model = P.solanaModel(paper, trades, null);
  assert(model.openCount === 3, "open paper positions, got " + model.openCount);
  assert(model.closed === 3 && Math.round(model.winRate * 100) === 33, "win rate uses closed fills");
  closeTo(model.net, -0.26215, "net pnl");
  const desk = P.solanaHtml(model);
  assert(!desk.includes("Mint01") && desk.includes("T1"), "positions show tickers, not mints");
  assert(desk.includes("hard stop") && (desk.includes("take profit") || desk.includes("pass")), "trade reasons render");
  const index = fs.readFileSync(path.join(root, "index.html"), "utf8");
  const preview = fs.readFileSync(path.join(root, "preview.html"), "utf8");
  const legacyPage = fs.readFileSync(path.join(root, "legacy.html"), "utf8");
  const logo = '<a class="logo" href="#/">&gt;sight<span>line</span><i>_</i></a>';
  assert(index.includes(logo) && preview.includes(logo) && legacyPage.includes(logo), "logo markup stays on the live site, the preview, and the legacy dashboard");
  assert(index.includes('href="preview/app.css"') && index.includes('src="preview/app.js"') && index.includes('src="preview/cardano-v2.js"'), "live page serves the redesigned shell");
  assert(!index.includes("preview.html") && !index.includes("preview-tag"), "live page is not the design sandbox");
  assert(index.includes("legacy.html"), "live page points at the legacy fallback");
  assert(preview.includes('href="preview/app.css"') && preview.includes('src="preview/app.js"') && preview.includes("preview-tag"), "preview shell still iterates on its own");
  assert(legacyPage.includes("dashboard/core.js") && legacyPage.includes("<!-- panels:begin -->") && legacyPage.includes("dashboard/pages.js"), "legacy dashboard keeps the old shell");
  assert(!legacyPage.includes("preview/app.js"), "legacy page does not load the redesigned shell");
  const css = fs.readFileSync(path.join(root, "preview/app.css"), "utf8");
  assert(css.includes(".logo span{color:var(--accent)") && css.includes(".logo i{font-style:normal;color:var(--accent)"), "logo color still follows --accent");
  assert(css.includes('body[data-desk="nft"]') && css.includes("#ff4fd8"), "NFT accent reaches the logo token");
  assert(css.includes("#14F195") && css.includes("#9945FF") && css.includes("#3d8bff"), "Solana and Cardano palettes stay");
  assert(css.includes("prefers-reduced-motion"), "reduced motion is respected");
  assert(css.includes("@keyframes blink") && css.includes("@keyframes pulse") && css.includes("@keyframes shimmer"), "logo blink and shell motion stay");
  const build = fs.readFileSync(path.join(root, "scripts/build-site.py"), "utf8");
  assert(build.includes("def bust(") && build.includes("?v="), "build adds cache-busting asset versions");
  assert(build.includes("def bust_preview(") && build.includes("def preview_version("), "preview assets are cache-busted on their own");
  assert(build.includes("Kept off asset_version()"), "preview hash does not retint the legacy dashboard assets");
  assert(build.includes("bust_preview(index)"), "published index cache-busts the redesigned assets");
  assert(build.includes("legacy.html"), "build publishes the legacy dashboard");
  assert(P.isDeskData("x_buzz.json"), "x_buzz.json is read from desk-data");
  assert(!P.isDeskData("desks.json") && !P.isDeskData("schema/x-buzz.schema.json"), "root config and schema stay on main");
  const appSrc = fs.readFileSync(previewJs, "utf8");
  assert(appSrc.includes('p === "x_buzz.json"') && appSrc.includes("x_buzz_history.jsonl") && appSrc.includes("SightlineBuzz") && appSrc.includes("previewPage"), "buzz snapshot and history load only on the preview page");
  assert(!index.includes("buzz.js") && !index.includes("buzz.css"), "live page does not load the buzz preview");
  assert(!legacyPage.includes("buzz.js") && !legacyPage.includes("buzz.css"), "legacy page does not load the buzz preview");
  assert(preview.includes('src="preview/buzz.js"') && preview.includes('href="preview/buzz.css"'), "preview page loads the buzz assets");
}

{
  const buzzSrc = fs.readFileSync(path.join(root, "preview/buzz.js"), "utf8");
  const buzzCss = fs.readFileSync(path.join(root, "preview/buzz.css"), "utf8");
  assert(!buzzSrc.includes("setInterval") && !buzzSrc.includes("Math.random"), "buzz does not simulate a live tick");
  assert(!/live tick|simulated/i.test(buzzSrc), "buzz copy does not describe a simulated tick");
  assert(buzzCss.includes("prefers-reduced-motion") && buzzCss.includes(".buzz-static"), "reduced motion and static home charts are styled");
  assert(buzzCss.includes(".logo span{color:var(--accent)") === false, "buzz css does not replace the accent logo rule");
  const box = { console };
  box.window = box;
  box.globalThis = box;
  vm.runInNewContext(buzzSrc, box);
  const B = box.SightlineBuzz;
  const mint = "A".repeat(44);
  const hours = (a, b) => { const row = Array(24).fill(0); row[22] = a; row[23] = b; return row; };
  const flat = () => Array(24).fill(0);
  const doc = {
    asof: "2026-10-29T01:49:00Z",
    definitions: { bot_like: "account under 30 days old OR under 50 followers" },
    tokens: [
      { ticker: "RDOG", chain: "solana", id_short: "5kUx…nbwo", query: mint, mentions_24h: 7, mentions_1h: 5, hourly_24h: hours(2, 5), velocity: { ratio_1h_vs_prev_hr: 2.5 }, sample: { posts: 7, unique_authors: 7, median_followers: 1178, bot_like_share: 0, authors_over_10k: 1 }, confidence: "high" },
      { ticker: "FLOATER", chain: "solana", id_short: "J7cq…pump", query: mint, mentions_24h: 30, mentions_1h: 22, hourly_24h: hours(10, 20), velocity: { ratio_1h_vs_prev_hr: 2.2 }, sample: { posts: 25, unique_authors: 15, median_followers: 1571, bot_like_share: 0.13, authors_over_10k: 1 }, confidence: "high" },
      { ticker: "RSTR", chain: "solana", id_short: "9QT2…pump", query: mint, mentions_24h: 32, mentions_1h: 17, hourly_24h: hours(16, 16), velocity: { ratio_1h_vs_prev_hr: 1.06 }, sample: { posts: 25, unique_authors: 15, median_followers: 2120, bot_like_share: 0.2, authors_over_10k: 0 }, confidence: "high" },
      { ticker: "BIO", chain: "solana", id_short: "7AxJ…pump", query: mint, mentions_24h: 140, mentions_1h: 45, hourly_24h: hours(121, 19), velocity: { ratio_1h_vs_prev_hr: 0.37 }, sample: { posts: 25, unique_authors: 14, median_followers: 1371, bot_like_share: 0.14, authors_over_10k: 1 }, confidence: "high" },
      { ticker: "YIELDMOS", chain: "solana", id_short: "7tLn…rray", query: mint, mentions_24h: 3, mentions_1h: 0, hourly_24h: hours(3, 0), velocity: { ratio_1h_vs_prev_hr: 0 }, sample: { posts: 3, unique_authors: 3, median_followers: 1178, bot_like_share: 0.33, authors_over_10k: 0 }, confidence: "high" },
      { ticker: "WIDE", chain: "solana", id_short: mint, query: mint, mentions_24h: 1, mentions_1h: 1, hourly_24h: hours(0, 1), velocity: { ratio_1h_vs_prev_hr: 0.1 }, sample: { posts: 1, unique_authors: 1, median_followers: 10, bot_like_share: 0, authors_over_10k: 0 }, confidence: "high" },
      { ticker: "NIGHT", chain: "cardano", id_short: "0691b2fecc", query: "NIGHT cardano", mentions_24h: 1133, mentions_1h: 44, hourly_24h: flat(), velocity: { ratio_1h_vs_prev_hr: 1.02 }, sample: { posts: 25, unique_authors: 25, median_followers: 117, bot_like_share: 0.36, authors_over_10k: 1 }, confidence: "low" },
      { ticker: "SNEK", chain: "cardano", id_short: "279c909f34", query: "SNEK cardano", mentions_24h: 44, mentions_1h: 1, hourly_24h: flat(), velocity: { ratio_1h_vs_prev_hr: null }, sample: { posts: 25, unique_authors: 14, median_followers: 817.5, bot_like_share: 0.14, authors_over_10k: 1 }, confidence: "medium" },
      { ticker: "SOLADA", chain: "cardano", id_short: "e7c77b2784", query: "SOLADA", mentions_24h: 60, mentions_1h: 0, hourly_24h: flat(), velocity: { ratio_1h_vs_prev_hr: 0 }, sample: { posts: 25, unique_authors: 21, median_followers: 202, bot_like_share: 0.29, authors_over_10k: 0 }, confidence: "medium", low: true },
      { ticker: "CLOVE", chain: "cardano", id_short: "c1b89ed48d", query: "CLOVE", mentions_24h: 6, mentions_1h: 0, hourly_24h: flat(), velocity: { ratio_1h_vs_prev_hr: 0 }, sample: { posts: 5, unique_authors: 3, median_followers: 102, bot_like_share: 0.33, authors_over_10k: 0 }, low: true }
    ]
  };
  assert(B.velocityText(null) === "n/a" && B.velocityText(2.5) === "2.5x" && B.velocityText(2.2) === "2.2x" && B.velocityText(1.06) === "1.06x" && B.velocityText(0) === "0x" && B.velocityText(2) === "2x" && B.velocityText(10) === "10x", "velocity text");
  assert(B.shortId("5kUx…nbwo") === "5kUx…nbwo" && B.shortId("eb7a93ebc3") === "eb7a93…", "short ids stay short");
  assert(B.shortId(mint) === "AAAA…AAAA" && !B.shortId(mint).includes(mint.slice(0, 20)), "a full id is shortened");
  const model = B.model(doc);
  assert(model && model.hottest.ticker === "RDOG" && B.logoColor("solana", model) === "#ff7a45", "logo follows the hottest Solana ticker");
  assert(B.logoColor("cardano", model) === "" && B.logoColor("home", model) === "" && B.logoColor("solana", null) === "", "other desks keep the accent logo");
  const sol = B.solanaPanel(model);
  assert(sol.includes("measured, not a signal") && sol.includes("no buzz data yet") === false, "solana buzz is labeled and filled");
  assert(sol.indexOf("solana:RDOG") < sol.indexOf("solana:FLOATER") && sol.indexOf("solana:FLOATER") < sol.indexOf("solana:RSTR") && sol.indexOf("solana:RSTR") < sol.indexOf("solana:BIO"), "solana rows sort by velocity");
  assert((sol.match(/class="vchip hot"/g) || []).length === 2, "chips pulse only at 2x or more");
  assert(sol.includes('data-buzz-final="2.5x">2.5x<') && sol.includes('data-buzz-final="1.06x">1.06x<'), "velocity chip shows the file ratio");
  const rdog = sol.split('data-buzz-token="solana:RDOG"')[1].split("</button>")[0];
  assert((rdog.match(/<i/g) || []).length === 24 && (rdog.match(/class="z"/g) || []).length === 22, "each row is 24 recorded hours");
  assert(!sol.includes(mint) && !sol.includes("A".repeat(20)) && sol.includes("5kUx…nbwo") && sol.includes("AAAA…AAAA"), "query and full ids stay off the solana panel");
  assert(!/live tick|simulated|setInterval|Math\.random/i.test(sol), "solana panel is not a live tick");
  const ada = B.cardanoPanel(model);
  assert(ada.includes("buzz layer") && ada.includes("measured, not a signal"), "cardano buzz layer is labeled");
  assert(/class="buzz-cell lowc" data-buzz-token="cardano:NIGHT"/.test(ada), "low confidence is hatched");
  assert(/class="buzz-cell" data-buzz-token="cardano:SNEK"/.test(ada), "medium confidence is not hatched");
  assert(/class="buzz-cell" data-buzz-token="cardano:SOLADA"/.test(ada), "schema confidence wins over a legacy low flag");
  assert(/class="buzz-cell lowc" data-buzz-token="cardano:CLOVE"/.test(ada), "legacy low flag still hatches when confidence is absent");
  const h2 = [...ada.matchAll(/<h2>([\s\S]*?)<\/h2>/g)].map(m => m[1].replace(/<[^>]+>/g, ""));
  assert(h2.length === 1 && !/snek/i.test(h2[0]) && !/suri/i.test(ada) && !/\bfounder\b/i.test(ada), "buzz panel names stay neutral");
  assert(ada.indexOf("cardano:NIGHT") < ada.indexOf("cardano:SNEK"), "cardano cells sort by 1h mentions");
  assert(!ada.includes(mint) && ada.includes("0691b2…"), "cardano cells use a short id");
  const home = B.homePanel(model);
  assert(home === B.homePanel(model), "home charts are deterministic");
  assert(home.includes("buzz-static") && home.includes("measured, not a signal"), "home buzz is static and labeled");
  assert(!home.includes("data-buzz-count") && !/animation=/.test(home), "home charts do not animate");
  assert((home.match(/stroke-dasharray="4 4"/g) || []).length === 3 && (home.match(/EXAMPLE PRICE/g) || []).length === 3, "price line is a dashed example placeholder");
  assert(home.includes("Buzz: 5/h, 2.5x") && home.includes("Buzz: 22/h, 2.2x") && home.includes("Buzz: 17/h, 1.06x"), "chart stamps use the file");
  assert(!home.includes(mint), "home charts hide full ids");
  const floater = model.byKey["solana:FLOATER"];
  const q = B.quality(floater);
  assert(q.basis === "mixed" && q.segments[0].text === "bot-like 13%" && q.segments[1].text === "normal 80%" && q.segments[2].text === ">10k 1", "floater bar matches the sample");
  assert(q.note.includes("1 of 15") && q.note.includes("unique authors") && q.note.includes("posts sample"), "shares say which base they use");
  const detail = B.detailInner(floater, model);
  assert(detail.includes("measured, not a signal") && detail.includes("data-w=\"13%\"") && detail.includes("data-w=\"80%\"") && detail.includes("data-w=\"7%\"") && detail.includes("&gt;10k 1"), "detail bar is the author mix");
  assert(detail.includes("1,571") && detail.includes("posts sample") && !detail.includes(mint), "detail keeps the median and drops the query");
  const snekDetail = B.detailInner(model.byKey["cardano:SNEK"], model);
  assert(snekDetail.includes("817.5"), "median keeps a fractional follower count");
  const quiet = B.quality({ sample: { posts: 0, unique_authors: 0, median_followers: null, bot_like_share: null, authors_over_10k: 0, skipped: "too few mentions to sample" } });
  assert(quiet.basis === "none" && quiet.note.includes("too few"), "a skipped sample is labeled");
  const authorsOnly = B.quality({ sample: { posts: 4, unique_authors: 4, median_followers: 10, bot_like_share: null, authors_over_10k: 1 } });
  assert(authorsOnly.basis === "unique authors" && authorsOnly.note.includes("shares of 4 unique authors"), "author counts are used when the file has them");
  const shellSol = '<div class="pagehead"></div><div class="stack"><p>books</p></div>';
  const drawnSol = B.decorate("solana", shellSol, doc);
  assert(drawnSol.indexOf("buzz sparkline") < drawnSol.indexOf("books") && drawnSol.includes("solana:RDOG"), "solana tab gains the sparkline");
  const shellAda = '<div class="stack"><section class="panel"><h3><span class="ttl">Cluster heat</span></h3></section></div>';
  const drawnAda = B.decorate("cardano", shellAda, doc);
  assert(drawnAda.indexOf("buzz layer") < drawnAda.indexOf("Cluster heat</span>"), "buzz layer sits on the heat grid");
  const shellHome = '<div class="stack"><section class="panel"><h3><span class="ttl">Latest briefs</span></h3><article class="feed-item"><div class="meta">desk</div></article></section></div>';
  const drawnHome = B.decorate("home", shellHome, doc);
  assert(drawnHome.indexOf("buzz vs price") < drawnHome.indexOf("Latest briefs</span>"), "home charts sit above briefs");
  assert(drawnHome.includes('class="feed-item"><div class="meta"><span class="buzz-stamp"') && drawnHome.includes("Buzz: 5/h, 2.5x"), "briefs get the fastest stamp");
  const missing = B.decorate("solana", shellSol, null);
  assert(missing.includes("no buzz data yet") && !/\berror\b/i.test(missing), "a missing buzz file is an empty state");
  assert(B.decorate("cardano", shellAda, { tokens: [] }).includes("no buzz data yet"), "an empty token list is an empty state");
  assert(B.decorate("nft", "<p>nft</p>", doc) === "<p>nft</p>", "nft tab is left alone");
  const moving = { console, matchMedia: () => ({ matches: false }) };
  moving.window = moving;
  moving.globalThis = moving;
  vm.runInNewContext(buzzSrc, moving);
  const started = moving.SightlineBuzz.solanaPanel(moving.SightlineBuzz.model(doc));
  assert(started.includes('data-buzz-final="2.5x">0x<'), "motion grows the chip from zero");
  const reduced = { console, matchMedia: () => ({ matches: true }) };
  reduced.window = reduced;
  reduced.globalThis = reduced;
  vm.runInNewContext(buzzSrc, reduced);
  const still = reduced.SightlineBuzz.solanaPanel(reduced.SightlineBuzz.model(doc));
  assert(still.includes('data-buzz-final="2.5x">2.5x<'), "reduced motion paints the final ratio");
  const noneHist = B.alertsPanel(B.historyModel(null));
  assert(noneHist.includes("recent buzz alerts") && noneHist.includes("no buzz history yet") && noneHist.includes("measured, not a signal") && !/\berror\b/i.test(noneHist), "missing history is an empty state");
  assert(B.detailInner(model.byKey["solana:RDOG"], model).includes("no buzz history yet"), "detail without history stays quiet");
  const stakeBook = "stake1u" + "y".repeat(40);
  const hist = [
    { asof: "2026-10-28T20:00:00Z", ticker: "RDOG", chain: "solana", id_short: mint, mentions_1h: 2, mentions_24h: 2, ratio_1h_vs_prev_hr: null, alert: false, paper_position: stakeBook },
    { asof: "2026-10-29T01:49:00Z", ticker: "RDOG", chain: "solana", id_short: "5kUx…nbwo", mentions_1h: 5, mentions_24h: 7, ratio_1h_vs_prev_hr: 2.5, alert: true, paper_position: "F" },
    { asof: "2026-10-29T01:49:00Z", ticker: "FLOATER", chain: "solana", id_short: "J7cq…pump", mentions_1h: 22, mentions_24h: 30, ratio_1h_vs_prev_hr: 2.2, alert: false, paper_position: null },
    { asof: "2026-10-29T02:00:00Z", ticker: "BIO", chain: "solana", id_short: "7AxJ…pump", mentions_1h: 9, mentions_24h: 9, ratio_1h_vs_prev_hr: 1.1, alert: true, paper_position: null }
  ].map(row => JSON.stringify(row)).join("\n\n");
  const parsed = B.historyModel(hist);
  assert(parsed.missing === false && parsed.rows.length === 4 && parsed.rows[0].book === "" && parsed.rows[1].book === "F", "a book letter is kept and anything longer is dropped");
  const alerts = B.alertsPanel(parsed);
  assert(alerts.includes("buzz-static") && alerts.includes("measured, not a signal") && !alerts.includes("data-buzz-count"), "alert list is static and labeled");
  assert(alerts.indexOf(">BIO<") < alerts.indexOf(">RDOG<"), "newest alert is listed first");
  assert(alerts.includes("5/h") && alerts.includes("2.5x") && alerts.includes("buzz-book\">F<"), "alert row shows mentions, velocity, and the paper book");
  assert(!alerts.includes("FLOATER") && !alerts.includes(mint) && !alerts.includes("stake1"), "non-alerts and full ids stay off the alert list");
  assert(alerts.includes("ET"), "alert time is labeled ET");
  const quietHist = B.historyModel(JSON.stringify({ asof: "2026-10-29T01:49:00Z", ticker: "SNEK", chain: "cardano", id_short: "279c90…", mentions_1h: 1, mentions_24h: 44, ratio_1h_vs_prev_hr: null, alert: false, paper_position: null }));
  assert(B.alertsPanel(quietHist).includes("no buzz alerts yet"), "history with no alert runs says so");
  const timeline = B.detailInner(model.byKey["solana:RDOG"], model, parsed);
  assert(timeline.includes("Buzz timeline") && timeline.includes("dot marks an alert") && (timeline.match(/class="run alert"/g) || []).length === 1, "the token timeline marks the alert run");
  assert(timeline.indexOf('class="run"') < timeline.indexOf('class="run alert"'), "timeline runs stay in time order");
  assert(!timeline.includes(mint) && !timeline.includes("stake1"), "timeline does not render a full id");
  const homeHist = B.decorate("home", shellHome, doc, hist);
  assert(homeHist.includes("recent buzz alerts") && homeHist.indexOf("buzz vs price") < homeHist.indexOf("recent buzz alerts") && homeHist.indexOf("recent buzz alerts") < homeHist.indexOf("Latest briefs</span>"), "home shows alerts under the charts");
  assert(B.decorate("home", shellHome, null, null).includes("no buzz history yet"), "home without a history file does not error");
}

console.log("smoke-dashboard: OK");
console.log("  fixture ADA", sc.byUnit.ADA.pnl, "ADA-equivalent", sc.byUnit["ADA-equivalent"].pnl, "USD", sc.byUnit.USD.pnl);
console.log("  live books", live.length, "open", live.reduce((n, b) => n + b.json.open.length, 0), "closed", live.reduce((n, b) => n + b.json.closed.length, 0));
