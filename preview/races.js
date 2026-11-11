/* Wallet-race leaderboard. Reads races-data only, never desk-data. */
(function (root) {
  const BANNER = "Display only, untested. A lead in the race is luck until tested.";
  const REMOTE = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/races-data/summary.json";

  function esc(s) {
    return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function pct(n) {
    if (n == null || !isFinite(Number(n))) return "–";
    const v = Number(n);
    return (v < 0 ? "−" : "+") + Math.abs(v).toFixed(1) + "%";
  }
  function interval(row) {
    if (row.ci95_low == null || row.ci95_high == null) return "95% n/a";
    return "95% " + pct(row.ci95_low) + " to " + pct(row.ci95_high);
  }

  function page(summary) {
    const doc = summary || {};
    const wallets = doc.wallets || [];
    const banner = esc(doc.banner || BANNER);
    const rows = wallets.length ? wallets : [
      { id: "R1", name: "Random meme picker", label: "close variant of v6.5 R; random among discoverable graduates (coverage logged)", note: "Close in style to the frozen test's meme books. This race cannot change that test." },
      { id: "R2", name: "Filtered meme flipper", label: "close variant of v6.5 F", note: "Close in style to the frozen test's meme books. This race cannot change that test." },
      { id: "R3", name: "Big meme basket" },
      { id: "R4", name: "Listing bounce" },
      { id: "R5", name: "Established breakout (memes included)", label: "R5 Established breakout (memes included) — close variant of killed Trend/momentum (time-series breakout); display only, untested" },
      { id: "SOL", name: "SOL hold", label: "Benchmark. Official net % in SOL is 0." }
    ];
    const items = rows.map(row => {
      const lead = row.leader_label ? `<span class="p a">${esc(row.leader_label)}</span>` : "";
      const window = row.basis === "trailing_30d" ? "per 30 days" : (row.window_label || "so far");
      const deployed = row.avg_pct_deployed == null ? "–" : (row.avg_pct_deployed * 100).toFixed(1) + "%";
      const usdc = row.id === "SOL" && row.usdc_mark != null ? `<div class="sub">USDC mark ${esc(Number(row.usdc_mark).toFixed(2))}${row.usdc_stale ? " · stale" : ""} · not used for rank</div>` : "";
      const note = row.note ? `<p class="race-note">${esc(row.note)}</p>` : "";
      const label = row.label ? `<div class="sub">${esc(row.label)}</div>` : "";
      return `<li>
        <div class="race-top"><span class="sub">${esc(row.rank || "–")}</span><strong>${esc(row.id)} · ${esc(row.name || "")}</strong>${lead}</div>
        ${label}
        <div class="race-fig ${Number(row.official_net_pct) < 0 ? "dn" : ""}">${esc(pct(row.official_net_pct))}</div>
        <div class="sub">${esc(window)} · n ${esc(row.n_trades == null ? "0" : row.n_trades)} · ${esc(interval(row))} · avg deployed ${esc(deployed)}</div>
        ${usdc}${note}
      </li>`;
    }).join("");
    const split = doc.r5_split || {};
    const bit = name => {
      const side = split[name] || {};
      return esc(name) + " n " + esc(side.n == null ? 0 : side.n) + " · " + esc(pct(side.net_pct));
    };
    const overlap = doc.r5_overlap || {};
    const line = overlap.line || "R5 holdings also in R3: 0 of 0";
    const overlapNote = overlap.note || "R3 and R5 will move together in a meme rally; two leads there are one bet, not two.";
    return `<div class="race-banner">${banner}</div>
      <ol class="race-list">${items}</ol>
      <section class="c" style="margin-top:12px"><div class="h"><h2>R5 split</h2><span class="k">Monday freeze tag</span></div>
        <p class="t">${bit("meme")} · ${bit("non_meme")} · ${bit("untagged")}</p>
        <p class="t">${esc(line)}</p>
        <p class="race-note">${esc(overlapNote)}</p>
      </section>`;
  }

  function load() {
    const remote = REMOTE + "?ts=" + Date.now();
    return fetch(remote, { cache: "no-cache" }).then(r => (r.ok ? r.json() : null)).catch(() => null);
  }

  root.SightlineRaces = { page, load, BANNER };
})(typeof window !== "undefined" ? window : globalThis);
