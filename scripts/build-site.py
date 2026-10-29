#!/usr/bin/env python3
"""Assemble the static Sightline site into dist/. Python stdlib only.

The dashboard reads desks.json and each desk's JSON from the same origin.
dist/ is what GitHub Pages and (later) Cloudflare Pages publish.
"""
import json
import pathlib
import re
import shutil
import sys

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
    for rel in ("preview/app.css", "preview/app.js"):
        p = ROOT / rel
        if not p.is_file():
            continue
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
        dates = sorted(p.stem for p in bdir.glob("*.json") if p.name != "index.json")
        (bdir / "index.json").write_text(json.dumps(dates, indent=2) + "\n", encoding="utf-8")
        found[d["id"]] = dates
    return found


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
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    shutil.copy(ROOT / "desks.json", DIST / "desks.json")
    desks = json.loads((ROOT / "desks.json").read_text(encoding="utf-8"))["desks"]
    for d in desks:
        copy_desk(d["id"])
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
