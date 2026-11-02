#!/usr/bin/env python3
"""Assemble the static Sightline site into dist/. Python stdlib only.

The dashboard reads desks.json and each desk's JSON from the same origin.
dist/ is what GitHub Pages and (later) Cloudflare Pages publish.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
MARKER = re.compile(r"<!-- panels:begin -->.*?<!-- panels:end -->", re.S)


def panel_tags():
    names = sorted(p.name for p in (ROOT / "dashboard" / "panels").glob("*.js"))
    if not names:
        sys.exit("build-site: no dashboard/panels/*.js files")
    lines = [f'<script src="dashboard/panels/{name}"></script>' for name in names]
    return names, "<!-- panels:begin -->\n" + "\n".join(lines) + "\n<!-- panels:end -->"


def asset_version():
    """Short content hash of dashboard/ code so each deploy gets new asset URLs (phones keep old CSS/JS otherwise)."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted((ROOT / "dashboard").rglob("*")):
        if p.is_file() and p.suffix in (".css", ".js"):
            h.update(p.relative_to(ROOT).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def bust(html):
    """Append ?v=<hash> to local dashboard/*.css|js references in the published legacy.html only."""
    v = asset_version()
    return re.sub(r'((?:href|src)="dashboard/[^"?]+\.(?:css|js))"', lambda m: f'{m.group(1)}?v={v}"', html)


def preview_version():
    """Content hash of the preview stylesheet and script.

    Kept off asset_version() so a preview edit does not change the legacy
    dashboard's cache-bust query. index.html and preview.html share this hash.
    """
    import hashlib
    h = hashlib.sha256()
    for p in sorted((ROOT / "preview").glob("*")):
        if not p.is_file() or p.suffix not in (".css", ".js"):
            continue
        rel = p.relative_to(ROOT).as_posix()
        h.update(rel.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def bust_preview(html):
    """Append ?v=<hash> to preview/*.css|js in published index.html and preview.html."""
    v = preview_version()
    return re.sub(
        r'((?:href|src)="preview/[^"?]+\.(?:css|js))"',
        lambda m: f'{m.group(1)}?v={v}"',
        html,
    )


def copy_desk(desk_id):
    src = ROOT / desk_id
    dest = DIST / desk_id
    if not src.is_dir():
        return
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("*.md", "index.json"),
    )


def write_brief_indexes(desks):
    found = {}
    for d in desks:
        rel = d.get("briefs")
        if not rel:
            continue
        bdir = DIST / rel
        bdir.mkdir(parents=True, exist_ok=True)
        dates = sorted(
            p.stem for p in bdir.glob("*.json")
            if p.name not in ("index.json", "manifest.json") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)
        )
        (bdir / "index.json").write_text(json.dumps(dates, indent=2) + "\n", encoding="utf-8")
        found[d["id"]] = dates
    return found


DESK_DATA = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/"
# Desk-data has x_buzz.json at the branch root and whale_ledger.json / kill_status.json
# under solada-prefilter/. The first publish put them only at those paths, so
# /whale_ledger.json, /kill_status.json, and /solada/<name>.json 404 on Pages.
# The fetch itself succeeds. FEED_ALIASES copies the same bytes to the URLs that 404.
_CARDANO_ID = re.compile(r"stake1[0-9a-z]{20,}|addr1[0-9a-z]{20,}")
FEED_ALIASES = {
    "x_buzz.json": ("solada/x_buzz.json",),
    "solada-prefilter/whale_ledger.json": ("whale_ledger.json", "solada/whale_ledger.json"),
    "solada-prefilter/kill_status.json": ("kill_status.json", "solada/kill_status.json"),
}
FEEDS = [
    "x_buzz.json",
    "x_buzz_history.jsonl",
    "solada-prefilter/whale_ledger.json",
    "solada-prefilter/kill_status.json",
    "solada-prefilter/flags.json",
    "solada-prefilter/dev_checks.json",
    "solada-prefilter/vetoes.json",
    "solada-prefilter/liquidity_moves.json",
    "solada-prefilter/big_moves.json",
    "solada-prefilter/whale_roster.json",
    "solada/log.json",
    "solada/paper.json",
    "solada/pulse.json",
    "solada/pumpfun/paper.json",
    "nft/log.json",
    "nft/pulse.json",
    "nft/paper.json",
    "solana-desk/health.json",
    "solana-desk/paper.json",
    "solana-desk/positions.json",
    "solana-desk/trades.csv",
]
REQUIRED_FEEDS = (
    "x_buzz.json",
    "solada-prefilter/whale_ledger.json",
    "solada-prefilter/kill_status.json",
)


def platform_addresses():
    """Allowlisted platform addresses. They may stay in a published feed."""
    path = ROOT / "scripts" / "privacy-public-keys.txt"
    out = set()
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line)
    return out


def scrub_public_feed(data, platform=None):
    """Replace stake keys and non-platform addresses with a stable w- id."""
    keep = platform if platform is not None else platform_addresses()
    text = data.decode("utf-8")

    def repl(match):
        token = match.group(0)
        if token in keep:
            return token
        return "w-" + hashlib.sha256(token.encode()).hexdigest()[:8]

    return _CARDANO_ID.sub(repl, text).encode("utf-8")


def publish_aliases(dest, rel, data):
    for alias in FEED_ALIASES.get(rel, ()):
        target = dest / alias
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def overlay_feeds(dest):
    """Copy live desk-data files into the published tree. Stdlib only."""
    for rel in FEEDS:
        url = DESK_DATA + rel
        target = dest / rel
        try:
            req = urllib.request.Request(url, headers={"user-agent": "sightline-build"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = scrub_public_feed(resp.read())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            publish_aliases(dest, rel, data)
            print(f"  feed {rel} ({len(data)} bytes)")
        except Exception as exc:
            print(f"  feed {rel} skipped ({exc})")
    missing = [rel for rel in REQUIRED_FEEDS if not (dest / rel).is_file()]
    if os.environ.get("SIGHTLINE_REQUIRE_FEEDS") == "1":
        for rel in REQUIRED_FEEDS:
            for alias in FEED_ALIASES.get(rel, ()):
                if not (dest / alias).is_file():
                    missing.append(alias)
    if missing and os.environ.get("SIGHTLINE_REQUIRE_FEEDS") == "1":
        sys.exit("build-site: required feeds missing from dist: " + ", ".join(missing))


def publish_generated(dest):
    sys.path.insert(0, str(ROOT))
    from scripts.publish_briefs import publish
    from scripts.fx_prices import refresh
    found = publish(str(dest))
    for desk_id, info in found.items():
        print(f"  generated briefs {desk_id}: {info['written'] or '(none)'}")
    refresh(str(dest / "dashboard" / "prices.json"))


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    names, block = panel_tags()
    legacy_path = ROOT / "legacy.html"
    if not legacy_path.is_file():
        sys.exit("build-site: legacy.html is missing")
    legacy = legacy_path.read_text(encoding="utf-8")
    html2, n = MARKER.subn(block, legacy, count=1)
    if n != 1:
        sys.exit("build-site: legacy.html is missing <!-- panels:begin --> ... <!-- panels:end -->")
    (DIST / "legacy.html").write_text(bust(html2), encoding="utf-8")
    if html2 != legacy:
        legacy_path.write_text(html2, encoding="utf-8")
        print("  refreshed legacy.html panel list")
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    if "preview/app.css" not in index or "preview/app.js" not in index:
        sys.exit("build-site: index.html must load preview/app.css and preview/app.js")
    if "<!-- panels:begin -->" in index:
        sys.exit("build-site: panel script tags belong in legacy.html, not index.html")
    (DIST / "index.html").write_text(bust_preview(index), encoding="utf-8")
    preview = ROOT / "preview.html"
    src_preview = ROOT / "preview"
    if not preview.is_file() or not (src_preview / "app.js").is_file() or not (src_preview / "app.css").is_file():
        sys.exit("build-site: preview.html, preview/app.js, and preview/app.css are required")
    (DIST / "preview.html").write_text(
        bust_preview(preview.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    shutil.copytree(src_preview, DIST / "preview")
    print(f"  preview assets ?v={preview_version()}")
    shutil.copytree(ROOT / "dashboard", DIST / "dashboard")
    archive = ROOT / "archive" / "v75"
    if not (archive / "index.html").is_file():
        sys.exit("build-site: archive/v75/index.html is missing (frozen PR #75 dashboard)")
    shutil.copytree(archive, DIST / "archive" / "v75")
    print("  archive v75")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copy(ROOT / "desks.json", DIST / "desks.json")
    desks = json.loads((ROOT / "desks.json").read_text(encoding="utf-8"))["desks"]
    for d in desks:
        copy_desk(d["id"])
    overlay_feeds(DIST)
    publish_generated(DIST)
    sys.path.insert(0, str(ROOT))
    from scripts.score_picks import write as write_picks
    write_picks(str(DIST), str(DIST / "dashboard" / "picks.json"))
    print("  picks dashboard/picks.json")
    briefs = write_brief_indexes(desks)
    # The legacy NFT site lives inside nft/ and is copied with that desk.
    if not (DIST / "nft" / "site" / "index.html").is_file():
        sys.exit("build-site: nft/site/index.html was not copied")
    if not (DIST / "nft" / "site" / "watchlist.json").is_file() or not (DIST / "nft" / "site" / "intel.json").is_file():
        sys.exit("build-site: legacy watchlist.json or intel.json missing from nft/site/")
    print(f"build-site: {DIST}")
    print(f"  panels: {', '.join(names)}")
    for desk_id, dates in briefs.items():
        print(f"  briefs {desk_id}: {dates or '(none)'}")


if __name__ == "__main__":
    main()
