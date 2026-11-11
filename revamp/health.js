/* Pipeline health strip. Reads health.json from the health-data branch.
   Screenshot states (?health=green|red|empty) are fixtures. A missing file
   stays empty: this script never invents a count, age, or status. */
(function (root) {
  var STATUSES = { green: 1, amber: 1, red: 1 };
  var URL = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/health-data/health.json";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function iso(ms) {
    return new Date(ms).toISOString().replace(/\.\d+Z$/, "Z");
  }
  function ageLabel(value, now) {
    var t = Date.parse(value);
    if (!isFinite(t)) return "no run";
    var min = Math.max(0, Math.round((now - t) / 60000));
    if (min < 90) return min + " min ago";
    var h = Math.round(min / 60);
    if (h < 48) return h + " h ago";
    return Math.round(h / 24) + " d ago";
  }
  function row(name, status, minutesAgo, now) {
    return {
      name: name,
      status: status,
      last_run_at: iso(now - minutesAgo * 60000)
    };
  }
  function sample(kind, now) {
    var stamp = now || Date.now();
    if (kind === "green") {
      return {
        generated_at: iso(stamp),
        status: "green",
        workflows: [
          row("Solana desk scan", "green", 12, stamp),
          row("wallet races", "green", 18, stamp),
          row("desk-data-guard", "green", 40, stamp),
          row("UA-F1", "green", 60 * 26, stamp)
        ]
      };
    }
    if (kind === "red") {
      return {
        generated_at: iso(stamp),
        status: "red",
        workflows: [
          row("Solana desk scan", "red", 95, stamp),
          row("wallet races", "red", 20, stamp),
          row("Solana forensics", "red", 60 * 5, stamp),
          row("Daily status", "red", 60 * 24 * 51, stamp)
        ]
      };
    }
    if (kind === "amber") {
      return {
        generated_at: iso(stamp),
        status: "amber",
        workflows: [row("Daily status", "amber", 60 * 20, stamp)]
      };
    }
    if (kind === "stale") {
      return {
        generated_at: iso(stamp - 8 * 60 * 60 * 1000),
        status: "green",
        workflows: [
          row("Solana desk scan", "green", 12, stamp),
          row("wallet races", "green", 18, stamp)
        ]
      };
    }
    if (kind === "nodate") {
      return {
        status: "green",
        workflows: [row("Solana desk scan", "green", 12, stamp)]
      };
    }
    if (kind === "badtime") {
      return {
        generated_at: "not-a-time",
        status: "green",
        workflows: [row("Solana desk scan", "green", 12, stamp)]
      };
    }
    return null;
  }
  function empty() {
    return '<section id="pipeline-health" class="health" aria-label="Pipeline health">' +
      '<p class="health-empty">Pipeline health is not published yet.</p></section>';
  }
  function render(doc, now) {
    if (!doc || typeof doc !== "object" || !STATUSES[doc.status] || !Array.isArray(doc.workflows)) {
      return empty();
    }
    var generated = Date.parse(doc.generated_at);
    var known = isFinite(generated);
    var stale = !known || (now - generated) > 6 * 60 * 60 * 1000;
    var overall = stale ? "red" : doc.status;
    var chips = doc.workflows.filter(function (item) {
      return item && STATUSES[item.status] && item.name;
    }).map(function (item) {
      var label = item.status + " " + item.name + " " + (item.detail || ageLabel(item.last_run_at, now));
      var wait = item.detail ? '<span class="wait">' + esc(item.detail) + "</span>" : "";
      return '<li><div class="health-chip is-' + esc(item.status) + '" role="group" aria-label="' + esc(label) + '">' +
        '<span class="st">' + esc(item.status) + "</span>" +
        '<span class="nm">' + esc(item.name) + "</span>" +
        '<span class="age">' + esc(item.detail ? "" : ageLabel(item.last_run_at, now)) + "</span>" +
        wait +
        "</div></li>";
    }).join("");
    var note = "";
    if (stale && known) {
      var hours = Math.round((now - generated) / 3600000);
      note = '<p class="health-stale">health check stale, last ' + hours + "h ago</p>";
    } else if (stale) {
      note = '<p class="health-stale">health check stale</p>';
    }
    var body = chips
      ? '<ul class="health-strip">' + chips + "</ul>"
      : '<p class="health-empty">No workflow rows in this health file.</p>';
    return '<section id="pipeline-health" class="health" aria-label="Pipeline health">' +
      '<p class="health-overall">Pipeline <strong class="is-' + esc(overall) + '">' + esc(overall) + "</strong></p>" +
      note + body + "</section>";
  }

  root.SightlineHealth = { render: render, sample: sample, URL: URL, empty: empty };
})(typeof globalThis !== "undefined" ? globalThis : this);
