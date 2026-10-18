(async function () {
  const view = document.getElementById("view");
  const nav = document.getElementById("nav");
  const desks = await S.desks();

  document.addEventListener("click", e => {
    const box = e.target.closest(".embed-preview");
    if (!box || !box.dataset.frame) return;
    box.outerHTML = box.dataset.frame;
  });

  function navHTML(route) {
    const a = (h, l, on) => `<a href="${h}" class="${on ? "on" : ""}">${l}</a>`;
    nav.innerHTML = a("#/", "Home", route === "home")
      + desks.map(d => a(`#/desk/${d.id}`, `<span class="dot ${d.status !== "active" ? "pending" : ""}"></span>${S.esc(d.id.toUpperCase())}`, route === d.id)).join("")
      + a("#/briefs", "Briefs", route === "briefs");
  }

  async function stamp() {
    let best = null;
    for (const d of desks) {
      if (!d.log) continue;
      const log = await S.get(d.log);
      const dt = log && log.asof ? new Date(log.asof) : null;
      if (dt && !isNaN(dt) && (!best || dt > best)) best = dt;
    }
    const el = document.getElementById("stamp");
    if (el) el.textContent = best ? "Data through " + S.when(best.toISOString()) : "";
  }

  function deskCard(d, books, log) {
    const s = S.score(books);
    return `<a class="panel span-6 desk-card" href="#/desk/${d.id}">
      <div class="row"><span class="name"><span class="dot ${d.status !== "active" ? "pending" : ""}"></span>${S.esc(d.name)}</span><span class="muted small">${S.esc(d.bot || "")}</span></div>
      <div class="hl">${S.esc(log?.headline || "No run yet.")}</div>
      <div class="cards">${S.metricCards(s)}</div>
      <div class="muted small" style="margin-top:8px">${books.length} book(s) · last run ${S.when(log?.asof)}</div>
    </a>`;
  }

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
      const b = (await S.briefsOf(d))[0];
      if (b) briefs.push({ b, d });
      books.forEach(bk => (bk.json.rules || []).forEach(r => rules.push({ ...r, desk: d.id, book: bk.json.book })));
      cards.push(deskCard(d, books, log));
    }
    const open = await S.allOpen(desks);
    rules.sort((a, b) => String(b.added_at || "").localeCompare(String(a.added_at || "")));
    const sb = S.registry.scoreboard
      ? await S.registry.scoreboard.render({ data: allBooks.map(b => ({ json: b.json, path: b.paper })), opt: {} })
      : `<p class="muted">Scoreboard panel isn't installed.</p>`;
    const tickets = open.length
      ? open.map(x => S.ticketCard
        ? S.ticketCard(x.t, x.kd, `<span class="muted small">${S.esc(x.desk.id)} · ${S.esc(x.book.json.book || "")}</span>`)
        : "").join("")
      : `<p class="muted">No open tickets.</p>`;
    const briefBlock = (briefs.length ? briefs.map(x => S.briefHTML(x.b, x.d.id)).join("") : `<p class="muted">No briefs yet.</p>`)
      + desks.filter(d => !briefs.find(x => x.d === d)).map(d => `<p class="muted small">${S.esc(d.id)}: no brief published yet</p>`).join("");
    view.innerHTML = `<h1>All desks</h1>
      <p class="sub">Combined view across ${desks.length} desk${desks.length === 1 ? "" : "s"}. Paper books only, sized in each book’s own unit.</p>
      <div class="grid">${cards.join("")}
        <section class="panel span-12"><h3>Combined scoreboard<span class="tag">scoreboard</span></h3>${sb}</section>
        <section class="panel span-8"><h3>Open tickets · closest to kill first<span class="tag">tickets</span></h3>${tickets}</section>
        <section class="panel span-4"><h3>Latest briefs<span class="tag">briefs</span></h3>${briefBlock}</section>
        <section class="panel span-12"><h3>Recently learned rules<span class="tag">rules</span></h3>${rules.length ? `<ul class="list">${rules.slice(0, 6).map(r => `<li>${r.id ? `<b class="warn">${S.esc(r.id)}</b> ` : ""}${S.esc(r.rule)}<div class="meta">${S.esc(r.desk)} · ${S.esc(r.book || "")} · ${S.when(r.added_at)}</div></li>`).join("")}</ul>` : `<p class="muted">No rules yet.</p>`}</section>
      </div>`;
  }

  async function deskPage(id) {
    const d = desks.find(x => x.id === id);
    navHTML(id);
    if (!d) { document.title = "Sightline"; view.innerHTML = `<p class="muted">Unknown desk.</p>`; return; }
    document.title = "Sightline · " + d.name;
    const { panels, custom } = await S.panelsFor(d);
    const html = await Promise.all(panels.map(p => S.renderPanel(d, p)));
    const layout = custom
      ? `layout from <code>${S.esc(d.dir)}panels.json</code> (${panels.length} panel${panels.length === 1 ? "" : "s"})`
      : "default layout (no panels.json)";
    view.innerHTML = `<h1><span class="dot ${d.status !== "active" ? "pending" : ""}"></span>${S.esc(d.name)}</h1>
      <p class="sub">Run by ${S.esc(d.bot || "—")} · ${layout}</p>
      <div class="grid">${html.join("")}</div>`;
  }

  async function briefsPage() {
    navHTML("briefs");
    document.title = "Sightline · Briefs";
    const all = [];
    for (const d of desks) (await S.briefsOf(d)).forEach(b => all.push({ b, d }));
    all.sort((a, b) => String(b.b.date).localeCompare(String(a.b.date)));
    const body = (all.length ? all.map(x => S.briefHTML(x.b, x.d.name)).join("") : `<p class="muted">No briefs yet.</p>`)
      + desks.filter(d => !all.find(x => x.d === d)).map(d => `<p class="muted small">${S.esc(d.name)}: no briefs published yet.</p>`).join("");
    view.innerHTML = `<h1>Daily briefs</h1><p class="sub">Archive across all desks, newest first. The index is built from each desk's briefs folder.</p><div class="grid"><section class="panel span-12">${body}</section></div>`;
  }

  async function route() {
    const h = location.hash.replace(/^#\/?/, "");
    view.innerHTML = `<div class="loading">Loading…</div>`;
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
    document.body.dataset.ready = "1";
  }
  window.addEventListener("hashchange", route);
  route();
})();
