(async function () {
  const view = document.getElementById("view");
  const nav = document.getElementById("nav");
  const [desks] = await Promise.all([S.desks(), S.loadIcons()]);
  const DESK_ICON = { solada: () => S.coinIcon("SOLADA", "lg"), nft: () => S.nftIcon("pudgy-penguins", "lg") };
  const deskIcon = d => (DESK_ICON[d.id] ? DESK_ICON[d.id]() : S.mono(d.id, "lg"));

  document.addEventListener("click", e => {
    const tag = e.target.closest(".wtag");
    if (tag) {
      e.preventDefault();
      e.stopPropagation();
      const open = tag.classList.toggle("open");
      tag.textContent = tag.getAttribute(open ? "data-full" : "data-short") || "";
      const row = tag.closest("[data-x]");
      if (row && open) row.classList.add("open");
      return;
    }
    const run = e.target.closest("a.run-now");
    if (run) {
      const id = run.dataset.desk || "";
      try { sessionStorage.setItem("sightline-run:" + id, String(Date.now())); } catch (err) {}
      const hint = run.parentElement && run.parentElement.querySelector(".run-hint");
      if (hint) hint.hidden = false;
      return;
    }
    const box = e.target.closest(".embed-preview");
    if (box && box.dataset.frame) { box.outerHTML = box.dataset.frame; return; }
    if (e.target.closest("a,summary,iframe")) return;
    const x = e.target.closest("[data-x]");
    if (x) x.classList.toggle("open");
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const tag = e.target.closest && e.target.closest(".wtag");
    if (!tag) return;
    e.preventDefault();
    tag.click();
  });

  function navHTML(route) {
    document.body.dataset.desk = desks.some(d => d.id === route) ? route : route;
    const a = (h, l, on) => `<a href="${h}" class="${on ? "on" : ""}" data-d="${S.esc(h.split("/").pop() || "home")}">${l}</a>`;
    nav.innerHTML = a("#/", "HOME", route === "home")
      + desks.map(d => a(`#/desk/${d.id}`, `<span class="dot ${d.status !== "active" ? "pending" : ""}"></span>${S.esc(d.id.toUpperCase())}`, route === d.id)).join("")
      + a("#/briefs", "BRIEFS", route === "briefs");
  }

  function runPending(id) {
    try {
      const t = Number(sessionStorage.getItem("sightline-run:" + id));
      return Number.isFinite(t) && Date.now() - t < 15 * 60 * 1000;
    } catch (err) { return false; }
  }
  function paintAges() {
    const now = Date.now();
    document.querySelectorAll("[data-age]").forEach(el => {
      const info = S.ageInfo(el.getAttribute("data-age"), now);
      el.classList.remove("age-fresh", "age-amber", "age-red", "age-muted");
      el.classList.add(info.cls);
      el.textContent = info.text;
    });
  }
  async function stamp() {
    let best = null;
    for (const d of desks) {
      const log = d.log ? await S.get(d.log) : null;
      const pulse = await S.get(S.pulsePath(d));
      const health = d.health ? await S.get(d.health) : null;
      const at = S.freshestAt(log, pulse, health);
      const ms = at ? new Date(at).getTime() : NaN;
      if (isFinite(ms) && (best == null || ms > new Date(best).getTime())) best = at;
    }
    const el = document.getElementById("stamp");
    if (el) el.innerHTML = `<span class="live"></span>${S.ageTag(best)}`;
  }

  function healthLine(h) {
    const at = S.healthAt(h);
    const dfr = typeof h.data_failure_rate === "number" ? ` · data failures ${S.fmt(h.data_failure_rate * 100)}%${h.void_day ? " (void day)" : ""}` : "";
    return `Last scan ${S.when(at)} · ${S.fmt(h.runs, 0)}/${S.fmt(h.expected_runs, 0)} runs today · ${S.fmt(h.leads_this_run, 0)} leads${dfr} · ${h.cfg_version || ""}${h.frozen ? " frozen" : " draft"}`;
  }
  function deskCard(d, books, log, pulse, health) {
    const s = S.score(books);
    const at = S.freshestAt(log, pulse, health);
    const headline = log?.headline ? S.rich(log.headline) : health ? S.esc(healthLine(health)) : "No run yet.";
    const bookNote = books.length ? `${books.length} book${books.length === 1 ? "" : "s"}` : health ? "scan only · paper fills pending" : "0 books";
    return `<section class="panel span-6 desk-card" data-desk="${S.esc(d.id)}">
      <div class="row">${deskIcon(d)}<a class="name" href="#/desk/${d.id}"><span class="dot ${d.status !== "active" ? "pending" : ""}"></span>${S.esc(d.name)}</a><span class="bot">${S.esc(d.bot || "")} ▸</span></div>
      ${S.runButton(d, { pending: runPending(d.id) })}
      <a class="hl clamp2" href="#/desk/${d.id}">${headline}</a>
      <div class="cards">${S.metricCards(s)}</div>
      <div class="foot-row"><span>${bookNote}</span>${S.ageTag(at)}</div>
    </section>`;
  }
  const sec = (span, title, tag, body, desk) => `<section class="panel span-${span}"${desk ? ` data-desk="${desk}"` : ""}><h3><span class="ttl">${title}</span><span class="tag">${tag}</span></h3>${body}</section>`;

  async function home() {
    navHTML("home");
    document.title = "Sightline · All desks";
    const cards = [];
    let allBooks = [];
    const briefs = [];
    const rules = [];
    for (const d of desks) {
      const books = await S.bookFiles(d);
      allBooks = allBooks.concat(books);
      const log = d.log ? await S.get(d.log) : null;
      const pulse = await S.get(S.pulsePath(d));
      const health = d.health ? await S.get(d.health) : null;
      const b = (await S.briefsOf(d))[0];
      if (b) briefs.push({ b, d });
      books.forEach(bk => (bk.json.rules || []).forEach(r => rules.push({ ...r, desk: d.id, book: bk.json.book })));
      cards.push(deskCard(d, books, log, pulse, health));
    }
    const open = await S.allOpen(desks);
    rules.sort((a, b) => String(b.added_at || "").localeCompare(String(a.added_at || "")));
    const sb = S.registry.scoreboard
      ? await S.registry.scoreboard.render({ data: allBooks.map(b => ({ json: b.json, path: b.paper })), opt: {} })
      : `<p class="muted">Scoreboard panel isn't installed.</p>`;
    const tickets = open.length
      ? `<div class="tickets">${open.map(x => S.ticketCard
        ? S.ticketCard(x.t, x.kd, `<span class="book" data-desk="${S.esc(x.desk.id)}">${S.esc(x.book.json.book && x.book.json.book !== x.desk.id ? x.desk.id + "·" + x.book.json.book : x.desk.id)}</span>`, x.pulse)
        : "").join("")}</div>`
      : `<p class="muted">No open tickets.</p>`;
    const briefBlock = (briefs.length ? briefs.map(x => S.briefHTML(x.b, x.d.id)).join("") : `<p class="muted">No briefs yet.</p>`)
      + desks.filter(d => !briefs.find(x => x.d === d)).map(d => `<div class="nobrief">${S.esc(d.id)} · no brief published yet</div>`).join("");
    view.innerHTML = `<div class="pagehead"><h1>All desks</h1><span class="sub">${desks.length} desks · paper books only · each book in its own unit</span></div>
      <div class="grid">${cards.join("")}
        ${sec(12, "Combined scoreboard", "scoreboard", sb)}
        ${sec(8, "Open tickets · closest to kill first", "tickets", tickets)}
        ${sec(4, "Latest briefs", "briefs", `<div class="briefs">${briefBlock}</div>`)}
        ${sec(12, "Recently learned rules", "rules", rules.length ? `<ul class="rules">${rules.slice(0, 8).map(r => S.ruleRow(r, r.desk + "·" + (r.book || ""))).join("")}</ul>` : `<p class="muted">No rules yet.</p>`)}
      </div>`;
  }

  /* Two panels pointing at the same type + data + path render once. */
  function dedupe(panels) {
    const seen = new Set(); const out = []; let dropped = 0;
    for (const p of panels) {
      const k = JSON.stringify([p.type, p.data, p.path || ""]);
      if (seen.has(k)) { dropped++; continue; }
      seen.add(k); out.push(p);
    }
    return { out, dropped };
  }

  async function deskPage(id) {
    const d = desks.find(x => x.id === id);
    navHTML(id);
    if (!d) { document.title = "Sightline"; view.innerHTML = `<p class="muted">Unknown desk.</p>`; return; }
    document.title = "Sightline · " + d.name;
    const log = d.log ? await S.get(d.log) : null;
    const pulse = await S.get(S.pulsePath(d));
    const health = d.health ? await S.get(d.health) : null;
    const got = await S.panelsFor(d);
    const { out: panels, dropped } = dedupe(got.panels);
    const html = await Promise.all(panels.map(p => S.renderPanel(d, p)));
    const layout = got.custom
      ? `<code>${S.esc(d.dir)}panels.json</code> · ${panels.length} panels${dropped ? ` · ${dropped} duplicate hidden` : ""}`
      : "default layout";
    view.innerHTML = `<div class="pagehead">${deskIcon(d)}<div class="titles"><h1>${S.esc(d.name)}</h1><span class="sub">run by ${S.esc(d.bot || "—")} · ${layout}</span></div>${S.ageTag(S.freshestAt(log, pulse, health))}${S.runButton(d, { pending: runPending(d.id) })}</div>
      <div class="grid">${html.join("")}</div>`;
  }

  async function briefsPage() {
    navHTML("briefs");
    document.title = "Sightline · Briefs";
    const all = [];
    for (const d of desks) (await S.briefsOf(d)).forEach(b => all.push({ b, d }));
    all.sort((a, b) => String(b.b.date).localeCompare(String(a.b.date)));
    const body = (all.length ? all.map(x => `<div data-desk="${S.esc(x.d.id)}">${S.briefHTML(x.b, x.d.name)}</div>`).join("") : `<p class="muted">No briefs yet.</p>`)
      + desks.filter(d => !all.find(x => x.d === d)).map(d => `<div class="nobrief" data-desk="${S.esc(d.id)}">${deskIcon(d).replace("lg", "sm")}${S.esc(d.name)} · no briefs published yet</div>`).join("");
    view.innerHTML = `<div class="pagehead"><h1>Daily briefs</h1><span class="sub">all desks · newest first · index built from each desk's briefs/ folder</span></div><div class="grid"><section class="panel span-12"><h3><span class="ttl">Archive</span><span class="tag">${all.length} brief${all.length === 1 ? "" : "s"}</span></h3><div class="briefs">${body}</div></section></div>`;
  }

  async function route() {
    const h = location.hash.replace(/^#\/?/, "");
    view.innerHTML = `<div class="loading">Loading<span class="cursor">_</span></div>`;
    try {
      if (!desks.length) {
        view.innerHTML = `<h1>All desks</h1><p class="muted">Couldn’t read <code>desks.json</code> from this site.</p>`;
      } else if (h.startsWith("desk/")) {
        await deskPage(decodeURIComponent(h.slice(5).replace(/\/+$/, "")));
      } else if (h === "briefs") {
        await briefsPage();
      } else {
        await home();
      }
    } catch (e) {
      view.innerHTML = `<p class="placeholder">This page couldn't load (${S.esc(e.message)}).</p>`;
    }
    await stamp();
    paintAges();
    document.body.dataset.ready = "1";
  }
  setInterval(paintAges, 15000);
  window.addEventListener("hashchange", route);
  route();
})();
