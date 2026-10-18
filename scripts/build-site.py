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
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    html2, n = MARKER.subn(block, html, count=1)
    if n != 1:
        sys.exit("build-site: index.html is missing <!-- panels:begin --> ... <!-- panels:end -->")
    (DIST / "index.html").write_text(html2, encoding="utf-8")
    if html2 != html:
        (ROOT / "index.html").write_text(html2, encoding="utf-8")
        print("  refreshed index.html panel list")
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
