#!/usr/bin/env python3
"""Build the figure-news web page from data/items.csv.

Writes build/index.html (page/template.html with the latest 30 days of items
inline) and build/thumbs/YYYY-MM-DD.json (each day's thumbnails as data: URIs,
which the page loads as you scroll). The page is published as a claude.ai
artifact, and an artifact can only show images that ship with it.
"""

from __future__ import annotations

import base64
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "page" / "template.html"
OUT = ROOT / "build"
DAYS = 30
FIELDS = [
    "first_seen", "source", "id", "name", "name_alt", "maker", "series", "character", "category",
    "scale", "price", "currency", "price_note", "release", "preorder_end", "status", "adult", "jan", "url",
]


def main() -> None:
    with (ROOT / "data" / "items.csv").open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    days = sorted({r["first_seen"] for r in rows if r.get("first_seen")}, reverse=True)[:DAYS]
    bundles: dict[str, dict[str, str]] = {day: {} for day in days}
    items = []
    for r in rows:
        day = r.get("first_seen")
        if day not in bundles:
            continue
        item = {k: r[k] for k in FIELDS if r.get(k)}
        thumb = ROOT / r["thumb"] if r.get("thumb") else None
        if thumb and thumb.is_file() and not r.get("adult"):
            bundles[day][f"{r['source']}/{r['id']}"] = (
                "data:image/jpeg;base64," + base64.b64encode(thumb.read_bytes()).decode("ascii"))
            item["thumb"] = 1
        items.append(item)

    last_run_path = ROOT / "data" / "last_run.json"
    last_run = json.loads(last_run_path.read_text(encoding="utf-8")) if last_run_path.exists() else {}
    data = {
        "updated": last_run.get("time", ""),
        "runs": last_run.get("sources", {}),
        "days": [{"day": d, "count": sum(1 for i in items if i["first_seen"] == d)} for d in days],
        "items": items,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("__FIGURE_DATA__") != 1:
        raise SystemExit("page/template.html must contain __FIGURE_DATA__ exactly once")

    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(template.replace("__FIGURE_DATA__", payload), encoding="utf-8")
    for old in (OUT / "thumbs").glob("*.json"):
        if old.stem not in bundles:
            old.unlink()
    for day, bundle in bundles.items():
        (OUT / "thumbs" / f"{day}.json").write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")

    size = (OUT / "index.html").stat().st_size
    print(f"build/index.html  {size / 1024:.0f} KB, {len(items)} items over {len(days)} days")
    for day in days:
        path = OUT / "thumbs" / f"{day}.json"
        print(f"build/thumbs/{day}.json  {path.stat().st_size / 1024:.0f} KB, {len(bundles[day])} thumbnails")


if __name__ == "__main__":
    main()
