/**
 * Dependency-free checks for P&L-by-unit and distance-to-kill.
 * Run from the repo root: node scripts/smoke-dashboard.mjs
 */
import fs from "fs";
import vm from "vm";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sandbox = { console };
sandbox.window = sandbox;
vm.runInNewContext(fs.readFileSync(path.join(root, "dashboard/core.js"), "utf8"), sandbox);
vm.runInNewContext(fs.readFileSync(path.join(root, "dashboard/panels/tickets.js"), "utf8"), sandbox);
const S = sandbox.S;

function load(p) { return JSON.parse(fs.readFileSync(path.join(root, p), "utf8")); }
function closeTo(actual, expected, label) {
  if (Math.abs(actual - expected) > 0.02) {
    throw new Error(`${label}: expected ${expected}, got ${actual}`);
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const solada = load("solada/paper.json");
const pump = load("solada/pumpfun/paper.json");
const nft = load("nft/paper.json");
const sc = S.score([solada, pump, nft]);

assert(!("pnl" in sc), "score() must not expose a cross-unit pnl total");
closeTo(sc.byUnit.ADA.pnl, -33.3, "ADA pnl");
closeTo(sc.byUnit["ADA-equivalent"].pnl, -77.8, "ADA-equivalent pnl");
closeTo(sc.byUnit.USD.pnl, 0, "USD pnl");
assert(sc.open === 2, "open count");
assert(sc.closed === 5, "closed count");
assert(sc.winRate === 0, "win rate");
const summed = sc.byUnit.ADA.pnl + sc.byUnit["ADA-equivalent"].pnl;
assert(Math.abs(summed - (-111.1)) < 0.02, "fixture still sums to -111.1 if someone adds the units");
assert(!S.metricCards(sc).includes("-111") && !S.metricCards(sc).includes("111.1"), "cards must not show a mixed total");
assert(S.metricCards(sc).includes("ADA-equivalent"), "cards label each unit");
assert(S.unitNote(sc).includes("not added together"), "mixed units get an explanation");

const cate = pump.open.find(t => t.ticker === "CATE");
const kd = S.killDistance(cate);
assert(kd && /holder/.test(kd.what), "CATE closest kill is holders, got " + JSON.stringify(kd));
closeTo(kd.pct, (108078 - 97082) / 108078 * 100, "CATE holders distance");
assert(kd.cur === 108078 && kd.lim === 97082, "CATE uses the latest mark and the numeric limit");

const stripped = JSON.parse(JSON.stringify(cate));
stripped.kill_line = { text: stripped.kill_line.text };
const kdText = S.killDistance(stripped);
assert(kdText && /holder/.test(kdText.what), "text fallback still picks holders, got " + JSON.stringify(kdText));
closeTo(kdText.pct, kd.pct, "text fallback matches numeric distance");

const argo = nft.open.find(t => t.ticker === "argonauts");
const kdA = S.killDistance(argo);
assert(kdA && kdA.what === "floor", "argonauts text parse uses floor, got " + JSON.stringify(kdA));
closeTo(kdA.pct, (0.793 - 0.70) / 0.793 * 100, "argonauts floor distance");
closeTo(kdA.lim, 0.70, "argonauts floor limit");

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
assert(html.includes("isn't installed yet"), "unknown panel placeholder");
assert(html.includes("dashboard/panels/heatmap.js"), "placeholder names the missing file");
assert(html.includes("Swarm heatmap"), "placeholder keeps the title");

const card = S.ticketCard(argo, kdA, "<span>nft</span>");
assert(card.includes("11.7%") || card.includes(S.fmt(kdA.pct)), "ticket card shows the distance");
assert(!card.includes("stake1") && !card.includes("addr1"), "ticket card does not invent wallet text");

assert(S.when("2026-10-18") === "Oct 18", "date-only stays on that calendar day, got " + S.when("2026-10-18"));
assert(S.when("2026-10-18T09:50:08-04:00") === "Oct 18, 9:50 AM ET", "timestamp keeps ET, got " + S.when("2026-10-18T09:50:08-04:00"));

console.log("smoke-dashboard: OK");
console.log("  ADA", sc.byUnit.ADA.pnl, "ADA-equivalent", sc.byUnit["ADA-equivalent"].pnl, "USD", sc.byUnit.USD.pnl);
console.log("  CATE", kd.what, S.fmt(kd.pct) + "%", "argonauts", kdA.what, S.fmt(kdA.pct) + "%");
