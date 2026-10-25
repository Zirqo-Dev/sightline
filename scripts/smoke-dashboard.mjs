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
  "solada/pumpfun/paper.json", "nft/paper.json", "solada/briefs/2026-10-19.json", "nft/site/intel.json"]) {
  assert(S.isDeskData(p), p + " should be read from desk-data");
  assert(S.deskDataUrl(p).startsWith("https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/" + p + "?ts="), p + " desk-data url");
}
for (const p of ["desks.json", "solada/panels.json", "nft/panels.json", "solada/briefs/index.json", "dashboard/icons/icons.json",
  "schema/run-log.schema.json", "solada/forensics/config.json", "solada/README.md"]) {
  assert(!S.isDeskData(p), p + " must stay on main");
}
for (const d of load("desks.json").desks) {
  assert(S.isDeskData(d.log) && S.isDeskData(d.pulse), d.id + " log and pulse come from desk-data");
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
  for (const want of ["CLOVE", "ZKOWL", "identical size buys", "sev-high", "Founder whale", "Cluster heat grid", "Pattern scoreboard", "Research only, not advice.", "exited", "held", "▼6", "▲12", "net -4,536 ADA"])
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

console.log("smoke-dashboard: OK");
console.log("  fixture ADA", sc.byUnit.ADA.pnl, "ADA-equivalent", sc.byUnit["ADA-equivalent"].pnl, "USD", sc.byUnit.USD.pnl);
console.log("  live books", live.length, "open", live.reduce((n, b) => n + b.json.open.length, 0), "closed", live.reduce((n, b) => n + b.json.closed.length, 0));
