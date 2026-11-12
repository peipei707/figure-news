#!/usr/bin/env python3
"""Build the figure-news web page for GitHub Pages from data/items.csv.

Writes build/, which the daily workflow uploads to GitHub Pages:

    index.html              page/template.html with app.css, app.js and the data inline
    version.json            when the data was collected, for a page that was left open
    thumbs/<source>/<file>  the thumbnails the page shows, copied from thumbs/
    (page/static/*)         icons and the web app manifest, copied as they are

The CSV has one row per source listing. The page shows one card per product,
so rows are tidied up on the way:

  * the same product listed by two sources (same JAN barcode, or the same
    Japanese name from the same maker) becomes one product with several offers;
  * type, scale, maker and series are put into one Chinese vocabulary, using
    page/aliases.json for the names the sources spell differently;
  * prices get a renminbi figure, release dates a sortable month, and HPOI's
    progress notes a stage ("企划公布", "11/12 开订", "预订中").

The page covers the latest 30 days, plus older products whose pre-order
deadline has not passed yet, so the deadline view stays complete.
"""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # only used to look at thumbnails; the page still builds without it
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "page"
OUT = ROOT / "build"
DAYS = 30
BEIJING = timezone(timedelta(hours=8))
FALLBACK_JPY_TO_CNY = 0.047
USD_TO_CNY = 7.1  # a fixed, rough figure: only Solaris Japan prices in dollars, and the page marks it as an estimate
BACKFILL = "旧品补录"
TILE = 3 / 4  # the page's picture tiles, width over height: product shots are mostly upright

# Whose wording wins when sources disagree: HPOI writes Chinese, Good Smile is
# the maker's own Japanese listing, Sofmap a Japanese shop's, HLJ and Solaris
# Japan are English shop listings.
SOURCES = ["HPOI", "GoodSmile", "Sofmap", "HLJ", "Solaris"]
PRICE_FROM = ["GoodSmile", "HPOI", "Sofmap", "HLJ", "Solaris"]  # the official price before a shop's
IMAGE_FROM = ["GoodSmile", "HPOI", "Sofmap", "Solaris", "HLJ"]
# What each shop's price means, shown beside it.
PRICE_NOTE = {"GoodSmile": "含税", "Sofmap": "含税", "HLJ": "不含日本消费税", "Solaris": "美元价，预订期优惠价"}

# Product types, in the order the page lists them.
TYPES = {
    "scale": "比例手办",
    "figure": "无比例手办",
    "chibi": "Q版 · 粘土人",
    "action": "可动",
    "kit": "拼装模型",
    "plush": "毛绒 · 娃娃",
    "goods": "周边 · 其它",
}


# --------------------------------------------------------------------------
# Text helpers


def squash(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def norm(text: str) -> str:
    """Case, width and spacing folded away, for comparing names."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip().lower()


def bare(text: str) -> str:
    """norm() without spaces and punctuation: 'PLAMAX Flower.' -> 'plamaxflower'."""
    return "".join(ch for ch in norm(text) if ch.isalnum())


def uniq(values) -> list[str]:
    seen, out = set(), []
    for v in values:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


class Aliases:
    """page/aliases.json: one display name for the spellings each source uses."""

    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.ip: list[tuple[str, str, re.Pattern | None]] = []
        for name, words in data.get("ip", {}).items():
            for word in [name, *words]:
                word = norm(word)
                if word:
                    self.ip.append((name, word, self._ascii_pattern(word)))
        self.ip.sort(key=lambda entry: -len(entry[1]))  # 'unicorn overlord' before 'overlord'
        self.maker: dict[str, str] = {}
        for name, words in data.get("maker", {}).items():
            for word in [name, *words]:
                self.maker[bare(word)] = name
        self.line: dict[str, str] = {norm(k): v for k, v in data.get("line", {}).items()}

    @staticmethod
    def _ascii_pattern(word: str) -> re.Pattern | None:
        """Latin spellings match whole words only, so 'overlord' is not found in 'overlords'."""
        if not word.isascii():
            return None
        head = r"(?<![a-z0-9])" if word[0].isalnum() else ""
        tail = r"(?![a-z0-9])" if word[-1].isalnum() else ""
        return re.compile(head + re.escape(word) + tail)

    def ip_of(self, series: str, shortest: int = 1) -> str:
        """The display name of the series a text names. `shortest` leaves out spellings shorter than
        that, for free text such as product names, where ワンピース may just mean a dress."""
        text = norm(series)
        if not text:
            return ""
        for name, word, pattern in self.ip:
            if len(word) >= shortest and (pattern.search(text) if pattern else word in text):
                return name
        return ""

    def maker_of(self, maker: str) -> str:
        """'FuRyu(フリュー)' and 'フリュー' -> 'FuRyu'; a name the table does not know stays as written."""
        plain = re.sub(r"株式会社|有限会社|\(株\)|（株）|co\.,? ?ltd\.?|inc\.?$", "", maker, flags=re.I)
        short = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", plain)  # a reading in brackets after the name
        return self.maker.get(bare(plain)) or self.maker.get(bare(short)) or short.strip() or maker

    def line_of(self, category: str) -> str:
        return self.line.get(norm(category), category)


# --------------------------------------------------------------------------
# One row -> tidy facts


def tidy_name(name: str, source: str, maker: str = "") -> str:
    name = squash(name)
    if source == "HLJ":  # "1/7 Azur Lane: Hakuhou (Rerelease)" -> "Azur Lane: Hakuhou"
        name = re.sub(r"\s*\((?:re-?release|re-?issue|re-?run)\)\s*$", "", name, flags=re.I)
        name = re.sub(r"^1/\d+\s+(?:scale\s+)?", "", name, flags=re.I)
    elif source == "Sofmap":  # "【再販】塗装済み完成品 1/7 魔法騎士レイアース 龍咲海" -> "魔法騎士レイアース 龍咲海"
        name = re.sub(r"^(?:【[^】]*】\s*)+", "", name)
        name = re.sub(r"^(?:塗装済み?完成品|塗装済み?可動フィギュア|完成品フィギュア)\s*", "", name)
        name = re.sub(r"^1/\d+\s*(?:スケール)?\s+", "", name)
    elif source == "Solaris":  # "Azur Lane - Cheshire - 1/7 (AniGame)" -> "Azur Lane - Cheshire"
        name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name)
        if maker and name.endswith(")") and bare(maker) in bare(name.rsplit("(", 1)[-1]):
            name = name.rsplit("(", 1)[0].strip()
        name = re.sub(r"\s+-\s+1/\d+\s*$", "", name)
    return name


def tidy_series(series: str) -> str:
    """'TVアニメ『超巡！超条先輩』' -> '超巡！超条先輩'; '神乐钵（动画）' -> '神乐钵'."""
    series = re.sub(r"^(?:TV\s*アニメ|アニメ|劇場版|映画|ゲーム)\s*[『「](.+)[』」]$", r"\1", series.strip())
    return re.sub(r"\s*[（(](?:动画|漫画|游戏|小说|电影)[）)]$", "", series)


def scale_of(text: str, loose: bool = True) -> str:
    """'1/7', '无比例' or ''. With loose=False a bare fraction is not enough: it must say it is a scale."""
    m = re.search(r"1\s*/\s*(\d{1,3})" + ("" if loose else r"\s*(?:比例|スケール|scale)"), text or "", re.I)
    if m:
        return f"1/{int(m.group(1))}"
    return "无比例" if re.search(r"ノンスケール|non-?scale|无比例|無比例", text or "", re.I) else ""


def release_of(text: str) -> tuple[str, str]:
    """('2027-06', '') for a month; ('2027-03', '2027 Q1') for a quarter; ('', '') when unknown.

    The first value sorts and groups, the second is shown when the month alone would mislead.
    """
    text = squash(text)
    m = re.fullmatch(r"(20\d{2})-(\d{2})", text)
    if m:
        return text, ""
    m = re.search(r"(20\d{2})\s*年?\s*第?\s*([一二三四1-4])\s*季度?", text) or re.search(r"(20\d{2})\s*Q([1-4])", text, re.I)
    if m:
        quarter = "一二三四".index(m.group(2)) + 1 if m.group(2) in "一二三四" else int(m.group(2))
        return f"{m.group(1)}-{quarter * 3:02d}", f"{m.group(1)} Q{quarter}"
    m = re.fullmatch(r"(20\d{2})\s*年?", text)
    if m:
        return f"{m.group(1)}-12", f"{m.group(1)} 年内"
    if not text or text in ("未知", "未定", "不明", "TBA", "TBD"):
        return "", ""
    return "", text


def height_mm(size: str) -> int | None:
    """Height in millimetres when the size text states one: '全高：約230mm', '高约22.7cm', 'H 23cm'."""
    text = unicodedata.normalize("NFKC", size or "").lower()
    m = re.search(r"(?:全高|总高|總高|身高|頭頂高|头顶高|高さ|高度|(?<![\u3040-\u30ff\u4e00-\u9fff])高|\bh)"
                  r"\s*[:：]?\s*(?:約|约|大约)?\s*(\d+(?:\.\d+)?)\s*(mm|cm|毫米|厘米)", text)
    if not m:
        return None
    value = float(m.group(1)) * (10 if m.group(2) in ("cm", "厘米") else 1)
    return round(value) if 10 <= value <= 3000 else None


def day_of(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def open_date(note: str, seen: date | None) -> str:
    """'11月12日开订', '11/10开订', '明日10点开订' -> ISO date, read relative to the day HPOI was read."""
    if not seen:
        return ""
    for word, days in (("今日", 0), ("今天", 0), ("明日", 1), ("明天", 1), ("后天", 2)):
        if word in note:
            return (seen + timedelta(days=days)).isoformat()
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", note) or re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", note)
    if not m:
        return ""
    try:
        when = date(seen.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return ""
    if when < seen - timedelta(days=120):  # "1月5日" read in December means next January
        when = when.replace(year=seen.year + 1)
    return when.isoformat()


def hpoi_stage(progress: str, seen: date | None) -> dict:
    """What HPOI's 进程 notes say about where a product stands.

    The notes are newest first, 'label：text | label：text'. Returns the stage to
    show, the day pre-orders open when one is announced, and whether pre-orders
    are open or a rerun was confirmed.
    """
    out = {"stage": "", "open": "", "pre": False, "re": False}
    for event in [e for e in progress.split(" | ") if e]:
        label, _, text = event.partition("：")
        text = text or label
        if "再版" in label or "再贩" in label or "再版" in text:
            out["re"] = True
            out["stage"] = out["stage"] or "再版确定"
            continue
        if "官图" in label or "图片" in text:  # picture updates say nothing about the stage
            continue
        if out["stage"]:
            continue
        if "开订" in text:
            out["open"] = open_date(text, seen)
            out["stage"] = text
        elif "预订" in label:
            out["pre"] = True
            out["stage"] = "预订中"
        elif "出货" in text or "出荷" in text:
            out["stage"] = "制作决定"
        else:
            out["stage"] = text
    return out


GOODS = re.compile(
    r"グッズ|タペストリー|アクリル|キーホルダー|缶バッジ|クリアファイル|ポスター|マグカップ|tシャツ|デカール|"
    r"コレクションケース|棚板|care parts|オプションパーツ|option parts|acrylic|keychain|key ?holder|tapestry|"
    r"挂画|立牌|钥匙扣|徽章|海报|展示盒|收纳")
KIT = re.compile(r"プラスチックモデル|プラモデル|組み立て式|plamax|moderoid|plastic model|model kit|拼装|积木|模型套件")
PLUSH = re.compile(r"ぬいぐるみ|plush|毛绒|玩偶|棉花娃娃|ブライス|blythe|harmonia bloom|ドール|(?<![a-z])doll(?![a-z])|\bbjd\b")
CHIBI = re.compile(
    r"ねんどろいど|nendoroid|粘土人|q版|qset|q posket|huggy good smile|look up|るかっぷ|mega cat project|デフォルメ|"
    r"chibi|盲盒|potepotte|mini memory|yurayura head|摇头|cu-poche|トレーディング|trading figure|さぷらいず|ガシャ|扭蛋")
ACTION = re.compile(
    r"アクションフィギュア|可動フィギュア|figma|figuarts|フィギュアーツ|可动|可動|action figure|漫威传奇|marvel legends|"
    r"mafex|revoltech|リボルテック|robot魂|robot spirits|metal build|超合金|chogokin|变形金刚|transformers")


def classify(categories: list[str], names: list[str], spec: str, scale: str) -> str:
    """One of TYPES, from the source's own category first and the name second."""
    bases = [c.split(" · ")[0] for c in categories]  # HPOI: "比例人形 · STRAVENTO"
    cats = norm(" ".join(categories))
    text = norm(" ".join([*categories, *names]))
    if "配件" in bases or GOODS.search(text):
        return "goods"
    if KIT.search(text) or KIT.search(norm(spec)):
        return "kit"
    if PLUSH.search(text):
        return "plush"
    if "Q版人形" in bases or CHIBI.search(text):
        return "chibi"
    if ACTION.search(text) or (ACTION.search(norm(spec)) and "ねんどろいど" not in text) or "action" in cats.split():
        return "action"
    if any(b in ("其它", "生物/幻灵", "场景") for b in bases) or cats in ("その他", "その他(グッズ)"):
        return "goods"
    return "scale" if scale.startswith("1/") else "figure"


def cny_of(price: int | None, currency: str, note: str, rate: float) -> int | None:
    if price is None:
        return None
    if currency == "CNY":
        return price
    m = re.search(r"([\d,]+)\s*元", note)
    if m:
        return int(m.group(1).replace(",", ""))
    if currency == "USD":
        return round(price * USD_TO_CNY)
    return round(price * rate) if currency == "JPY" else None


def jpy_to_cny(rows: list[dict[str, str]]) -> float:
    """Median of HPOI's own 人民币 reference prices against their yen prices."""
    ratios = []
    for r in rows:
        cny = re.search(r"([\d,]+)\s*元", r.get("price_note", ""))
        if r.get("source") == "HPOI" and r.get("currency") == "JPY" and r.get("price", "").isdigit() and cny:
            ratios.append(int(cny.group(1).replace(",", "")) / int(r["price"]))
    ratios = sorted(x for x in ratios if 0.02 < x < 0.1)
    return round(ratios[len(ratios) // 2], 4) if ratios else FALLBACK_JPY_TO_CNY


# --------------------------------------------------------------------------
# Rows -> products


def group_rows(rows: list[dict[str, str]], aliases: Aliases) -> list[list[dict[str, str]]]:
    """Rows that describe the same product, grouped.

    Two rows are the same product when they share a JAN barcode, or when an
    HPOI entry's Japanese name equals a Good Smile listing's name and the maker
    agrees. Names shorter than six characters are too easy to share by accident.
    """
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b and rows[a]["source"] != rows[b]["source"]:
            parent[max(a, b)] = min(a, b)

    by_jan: dict[str, int] = {}
    by_name: dict[tuple[str, str], int] = {}
    for i, r in enumerate(rows):
        jan = r["jan"]
        if re.fullmatch(r"\d{8,14}", jan) and int(jan):
            join(by_jan.setdefault(jan, i), i)
        name = bare(r["name_alt"] if r["source"] == "HPOI" else r["name"])
        maker = bare(aliases.maker_of(r["maker"]))
        if r["source"] in ("HPOI", "GoodSmile") and len(name) >= 6 and maker:
            join(by_name.setdefault((name, maker), i), i)
    groups: dict[int, list[dict[str, str]]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())


def first_of(members: list[dict[str, str]], field: str) -> str:
    return next((m[field] for m in members if m[field]), "")


def product_of(members: list[dict[str, str]], aliases: Aliases, rate: float) -> dict:
    members = sorted(members, key=lambda r: SOURCES.index(r["source"]) if r["source"] in SOURCES else 9)
    by_source = {m["source"]: m for m in reversed(members)}
    adult = any(m["adult"] for m in members)

    names = uniq(tidy_name(m["name"], m["source"], m["maker"]) for m in members)
    name = names[0] if names else members[0]["id"]
    alts = uniq([*names[1:], *(squash(m["name_alt"]) for m in members)])
    alts = [a for a in alts if bare(a) != bare(name)]

    series = uniq(tidy_series(part) for m in members for part in re.split(r"[、，]\s*", m["series"]) if part)
    ip = next((hit for hit in (aliases.ip_of(s) for s in series) if hit), "") or (series[0] if series else "")
    if not ip:  # a listing with no series field often names the series in the product name
        ip = next((hit for hit in (aliases.ip_of(m["name"], shortest=6) for m in members) if hit), "")
    makers = uniq(m["maker"] for m in members)
    maker = aliases.maker_of(makers[0]) if makers else ""

    categories = uniq(m["category"] for m in members)
    spec = " ".join(m["extra"] for m in members if m["source"] == "GoodSmile")
    size = first_of(members, "size")
    scale = next((s for s in (scale_of(m["scale"]) for m in members) if s), "") \
        or scale_of(spec) or scale_of(size, loose=False)
    kind = classify(categories, [m["name"] for m in members], spec, scale)
    # HPOI writes "比例人形 · STRAVENTO": the first half is its own type, the second the product line.
    line = uniq(aliases.line_of(m["category"].rpartition(" · ")[2]) for m in members
                if m["category"] and (m["source"] != "HPOI" or " · " in m["category"]))

    release = next((r for r in (release_of(m["release"]) for m in members) if r[0] and not r[1]), None) \
        or next((r for r in (release_of(m["release"]) for m in members) if r[0] or r[1]), ("", ""))

    offers, flags = [], set()
    stage = {"stage": "", "open": "", "pre": False, "re": False}
    for m in members:
        price = int(m["price"]) if m["price"].isdigit() else None
        status = [s for s in m["status"].split(" · ") if s]
        offer = {"s": m["source"], "id": m["id"], "url": m["url"]}
        if price is not None:
            offer |= {"price": price, "cur": m["currency"] or "JPY"}
            cny = cny_of(price, m["currency"], m["price_note"], rate)
            if cny is not None and m["currency"] != "CNY":
                offer["cny"] = cny
        if m["price_note"]:
            offer["note"] = m["price_note"]
        elif price is not None and m["source"] in PRICE_NOTE:
            offer["note"] = PRICE_NOTE[m["source"]]
        for key, field in (("ps", "preorder_start"), ("pe", "preorder_end")):
            if day_of(m[field]):
                offer[key] = m[field][:10]
        cancel = re.search(r"取消截止：(\d{4}-\d{2}-\d{2})", m["extra"])
        if cancel:
            offer["cancel"] = cancel.group(1)
        if status:
            offer["st"] = [s for s in status if s != BACKFILL]
        offers.append(offer)

        if "预订中" in status:
            flags.add("pre")
        if any("再贩" in s for s in status) or re.search(r"\((?:re-?release|re-?issue|re-?run)\)", m["name"], re.I):
            flags.add("re")
        if any("限定" in s for s in status):
            flags.add("ltd")
        if any("特典" in s for s in status) or m["bonus"]:
            flags.add("bonus")
        if any("受注" in s for s in status):
            flags.add("mto")
        if BACKFILL in status:
            flags.add("old")
        if m["source"] == "HPOI" and m["progress"]:
            stage = hpoi_stage(m["progress"], day_of(m["stats_date"]) or day_of(m["first_seen"]))
    if stage["pre"]:
        flags.add("pre")
    if stage["re"]:
        flags.add("re")

    priced = next((o for src in PRICE_FROM for o in offers if o["s"] == src and "price" in o), None)
    deadline = min((o["pe"] for o in offers if "pe" in o), default="")
    opened = min((o["ps"] for o in offers if "ps" in o), default="")

    item: dict = {
        "k": f"{members[0]['source']}/{members[0]['id']}",
        "keys": [f"{m['source']}/{m['id']}" for m in members],
        "day": min(m["first_seen"] for m in members),
        "name": name,
        "type": kind,
        "offers": offers,
    }

    def put(key: str, value) -> None:
        if value:
            item[key] = value

    put("alts", alts)
    put("ip", ip)
    put("series", [s for s in series if s != ip])
    put("chr", first_of(members, "character"))
    put("maker", maker)
    put("makers", [m for m in makers if m != maker])
    put("line", line)
    put("scale", scale)
    put("size", size)
    put("h", height_mm(size))
    if priced:
        item["price"], item["cur"] = priced["price"], priced["cur"]
        put("cny", priced.get("cny"))
    put("rel", release[0])
    put("relt", release[1])
    put("ps", opened)
    put("pe", deadline)
    put("open", stage["open"])
    put("stage", stage["stage"])
    put("flags", sorted(flags))
    put("adult", 1 if adult else 0)
    put("jan", next((m["jan"] for m in members if re.fullmatch(r"\d{8,14}", m["jan"])), ""))
    for key, field in (("material", "material"), ("sculptor", "sculptor"), ("painter", "painter"),
                       ("bonus", "bonus"), ("rating", "rating")):
        put(key, first_of(members, field))
    for m in members:
        for part in m["extra"].split("；"):
            label, _, value = part.partition("：")
            if value and label in ("发行", "原画", "仕様", "发售记录"):
                item.setdefault({"发行": "publisher", "原画": "artist", "仕様": "spec", "发售记录": "history"}[label], value)

    hpoi = by_source.get("HPOI")
    if hpoi and hpoi["stats_date"]:
        hot = {"at": hpoi["stats_date"]}
        for key, field in (("f", "follows"), ("w", "wants"), ("p", "preorders"), ("o", "owned"), ("n", "score_count")):
            if hpoi[field].isdigit() and int(hpoi[field]):
                hot[key] = int(hpoi[field])
        try:
            if hot.get("n") and float(hpoi["score"]):
                hot["s"] = round(float(hpoi["score"]), 2)
        except ValueError:
            pass
        put("views", hpoi["views"])
        item["hot"] = hot
        put("prog", [e for e in hpoi["progress"].split(" | ") if e])

    for src in IMAGE_FROM:
        thumb = by_source.get(src, {}).get("thumb")
        if thumb and (ROOT / thumb).is_file():
            item["img"] = thumb
            break
    return item


# --------------------------------------------------------------------------
# Thumbnails


def frame_of(path: Path) -> dict:
    """How a thumbnail sits in the page's upright tile.

    A picture close to the tile's shape fills it. One on a plain background is
    shown whole on that colour, so a cut-out on white keeps a white tile. The
    rest fill the tile while the crop stays under about a quarter; past that
    they are shown whole, and the bands left over take the colours of the
    picture's own edges.
    """
    if Image is None:
        return {}

    def tone(rgb, box) -> str:
        return "#%02x%02x%02x" % rgb.crop(box).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))

    try:
        with Image.open(path) as im:
            w, h = im.size
            shape = (w / h) / TILE  # 1 = the tile's shape, above 1 = wider
            if 0.88 <= shape <= 1.15:
                return {}
            rgb = im.convert("RGB")
            corners = [rgb.getpixel(xy) for xy in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))]
            if max(max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)) <= 24:
                return {"fit": 1, "bg": "#%02x%02x%02x" % tuple(round(sum(c[i] for c in corners) / 4) for i in range(3))}
            if 0.75 <= shape <= 1.4:
                return {}
            edge = max(2, round(min(w, h) * 0.04))
            if shape > 1:  # bands above and below
                return {"fit": 1, "bg": [tone(rgb, (0, 0, w, edge)), tone(rgb, (0, h - edge, w, h)), "v"]}
            return {"fit": 1, "bg": [tone(rgb, (0, 0, edge, h)), tone(rgb, (w - edge, 0, w, h)), "h"]}
    except Exception:  # an unreadable picture is shown as it is
        return {}


# --------------------------------------------------------------------------
# Build


def load_rows() -> list[dict[str, str]]:
    with (ROOT / "data" / "items.csv").open(newline="", encoding="utf-8-sig") as f:
        rows = [{k: squash(v) if k not in ("url", "image_url", "thumb") else (v or "") for k, v in row.items()}
                for row in csv.DictReader(f)]
    return [r for r in rows if r.get("first_seen") and r.get("source") and r.get("id")]


def build_data(rows: list[dict[str, str]], today: date, last_run: dict) -> dict:
    aliases = Aliases(PAGE / "aliases.json")
    rate = jpy_to_cny(rows)
    products = [product_of(group, aliases, rate) for group in group_rows(rows, aliases)]
    days = sorted({p["day"] for p in products}, reverse=True)[:DAYS]
    recent = set(days)
    items = [p for p in products if p["day"] in recent or p.get("pe", "") >= today.isoformat()]
    items.sort(key=lambda p: (p["day"], p["k"]), reverse=True)
    # The run record counts what that one run added. The page shows the day's total instead, so a
    # second run on the same day does not make a source look as if it had nothing new.
    run_day = last_run.get("time", "")[:10]
    added = Counter(r["source"] for r in rows if r["first_seen"] == run_day)
    runs = {key: run | {"new": added[key]} for key, run in last_run.get("sources", {}).items()}
    return {
        "rate": rate,
        "usd": USD_TO_CNY,
        "updated": last_run.get("time", ""),
        "runs": runs,
        "types": TYPES,
        "days": [{"day": d, "count": sum(1 for p in items if p["day"] == d)} for d in days],
        "items": items,
    }


def main() -> None:
    rows = load_rows()
    last_run_path = ROOT / "data" / "last_run.json"
    last_run = json.loads(last_run_path.read_text(encoding="utf-8")) if last_run_path.exists() else {}
    data = build_data(rows, datetime.now(BEIJING).date(), last_run)

    OUT.mkdir(exist_ok=True)
    shutil.rmtree(OUT / "thumbs", ignore_errors=True)
    copied = 0
    for item in data["items"]:
        src = item.get("img")
        if not src:
            continue
        dest = OUT / src
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / src, dest)
        item |= frame_of(dest)
        copied += dest.stat().st_size
    static = PAGE / "static"
    if static.is_dir():
        shutil.copytree(static, OUT, dirs_exist_ok=True)

    page = (PAGE / "template.html").read_text(encoding="utf-8")
    parts = {
        "__APP_CSS__": (PAGE / "app.css").read_text(encoding="utf-8"),
        "__APP_JS__": (PAGE / "app.js").read_text(encoding="utf-8"),
        # "<" never appears raw, so nothing in the data can close the script element
        "__FIGURE_DATA__": json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
    }
    for mark, text in parts.items():
        if page.count(mark) != 1:
            raise SystemExit(f"page/template.html must contain {mark} exactly once")
        if mark != "__FIGURE_DATA__" and re.search(r"</(script|style)", text, re.I):
            raise SystemExit(f"{mark}: the inlined file must not contain a closing script or style tag")
    for mark, text in parts.items():
        page = page.replace(mark, text)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    # A page that was left open asks for this to find out whether a newer build is out.
    (OUT / "version.json").write_text(json.dumps({"updated": data["updated"]}) + "\n", encoding="utf-8")

    merged = sum(1 for item in data["items"] if len(item["keys"]) > 1)
    print(f"build/index.html  {(OUT / 'index.html').stat().st_size / 1024:.0f} KB, "
          f"{len(data['items'])} products from {len(rows)} rows ({merged} listed by more than one source), "
          f"{len(data['days'])} days")
    print(f"build/thumbs      {copied / 1024:.0f} KB, {sum(1 for i in data['items'] if i.get('img'))} thumbnails")


if __name__ == "__main__":
    main()
