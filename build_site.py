#!/usr/bin/env python3
"""Build the figure-news web page for GitHub Pages from data/items.csv.

Writes build/index.html (page/template.html with the latest 30 days of items
inline, as a complete HTML document) and build/thumbs/YYYY-MM-DD.json (each
day's thumbnails as data: URIs, which the page loads as you scroll). The daily
workflow uploads build/ to GitHub Pages.
"""

from __future__ import annotations

import base64
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "page" / "template.html"
OUT = ROOT / "build"
DAYS = 30
FIELDS = [
    "first_seen", "source", "id", "name", "name_alt", "maker", "series", "character", "category",
    "scale", "price", "currency", "price_note", "release", "preorder_start", "preorder_end", "status",
    "rating", "adult", "jan", "url", "size", "material", "sculptor", "painter", "bonus", "progress", "extra",
    "follows", "wants", "preorders", "owned", "score", "score_count", "views", "stats_date",
]
FALLBACK_JPY_TO_CNY = 0.047
SUMMARY = "每天汇总 HPOI、Good Smile 官网和 HLJ 的手办新品：价格、发售、预订期、人气和规格。"
HEAD = (
    '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    f'<meta name="description" content="{SUMMARY}">'
    '<meta property="og:title" content="手办新品日报">'
    f'<meta property="og:description" content="{SUMMARY}">'
    "<style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);"
    "padding-bottom:env(safe-area-inset-bottom,0px)}html{scroll-padding-top:env(safe-area-inset-top,0px)}"
    "body{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif}img{max-width:100%}"
    "[hidden]{display:none!important}</style></head><body>\n"
)
TAIL = "\n</body></html>\n"


def jpy_to_cny(rows: list[dict[str, str]]) -> float:
    """Median of HPOI's own 人民币 reference prices against their yen prices."""
    ratios = []
    for r in rows:
        cny = re.search(r"([\d,]+)\s*元", r.get("price_note", ""))
        if r.get("source") == "HPOI" and r.get("currency") == "JPY" and r.get("price", "").isdigit() and cny:
            ratios.append(int(cny.group(1).replace(",", "")) / int(r["price"]))
    ratios = sorted(x for x in ratios if 0.02 < x < 0.1)
    return round(ratios[len(ratios) // 2], 4) if ratios else FALLBACK_JPY_TO_CNY


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
        "rate": jpy_to_cny(rows),
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
    (OUT / "index.html").write_text(HEAD + template.replace("__FIGURE_DATA__", payload) + TAIL, encoding="utf-8")
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
