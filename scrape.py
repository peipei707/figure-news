#!/usr/bin/env python3
"""figure-news: a daily digest of new figures from HPOI, Good Smile and HLJ.

Each run reads every source's "new items" listing, opens the detail page of
items it has not seen before, and writes:

    data/items.csv            every item seen so far, one row per item
    data/last_run.json        how each source did on the latest run
    daily/YYYY-MM-DD.md       the items first seen that day, with thumbnails
    thumbs/<source>/<id>.jpg  small thumbnails used by the daily pages
    README.md                 the latest day and an index of recent days

The crawler says who it is, follows robots.txt, waits between requests to
the same host, and skips a source whose site answers with a bot challenge
instead of trying to get past it.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parent
ITEMS_CSV = ROOT / "data" / "items.csv"
LAST_RUN = ROOT / "data" / "last_run.json"
DAILY_DIR = ROOT / "daily"
THUMB_DIR = ROOT / "thumbs"
README = ROOT / "README.md"
PAGES_URL = "https://peipei707.github.io/figure-news/"  # the web page built by build_site.py

USER_AGENT = "figure-news/0.1 (personal hobby tracker)"
BEIJING = timezone(timedelta(hours=8))
TIMEOUT = 30
MAX_IMAGE_BYTES = 15 * 1024 * 1024
THUMB_SIZE = (200, 200)

# Seconds between two requests to the same host. HPOI's robots.txt asks for 5.
DEFAULT_DELAY = float(os.environ.get("FIGURE_NEWS_DELAY", "3"))
HOST_DELAY = {"www.hpoi.net": 5.0, "rfx.hpoi.net": 5.0}
# Detail pages opened per source per run; the rest wait for the next run.
MAX_DETAILS = int(os.environ.get("FIGURE_NEWS_MAX_DETAILS", "40"))
# HPOI categories to follow (100 = 手办), comma-separated, e.g. "100,500".
HPOI_CATEGORIES = [c.strip() for c in os.environ.get("FIGURE_NEWS_HPOI_CATEGORIES", "100").split(",") if c.strip()]
# HPOI also catalogues products that came out long ago. Anything released
# this many months back or earlier is folded away on the daily page.
BACKFILL_MONTHS = 3
BACKFILL = "旧品补录"
# HPOI 属性 words that mark an adult-oriented item, and the listing filter that
# shows only 观感成熟 items.
ADULT_WORDS = ("观感成熟", "R18", "R-18", "18禁", "成人")
HPOI_ADULT_FILTER = "r18=315"

SOURCES = {"HPOI": "HPOI", "GoodSmile": "Good Smile 官网", "HLJ": "HLJ"}

CSV_FIELDS = [
    "first_seen", "source", "id", "name", "name_alt", "maker", "series", "character", "category",
    "scale", "price", "currency", "price_note", "release", "preorder_start",
    "preorder_end", "status", "rating", "adult", "jan", "image_url", "thumb", "url",
    "size", "material", "sculptor", "painter", "bonus", "progress", "extra",
    "follows", "wants", "preorders", "owned", "score", "score_count", "views", "stats_date",
]
# Fields a detail page fills in; refresh_rows() copies these back onto stored rows.
DETAIL_FIELDS = [
    "name", "name_alt", "maker", "series", "character", "category", "scale", "price", "currency",
    "price_note", "release", "preorder_start", "preorder_end", "rating", "adult", "jan", "image_url",
    "size", "material", "sculptor", "painter", "bonus", "progress", "extra",
    "follows", "wants", "preorders", "owned", "score", "score_count", "views", "stats_date",
]
# Days after an HPOI item is first seen when its page is read again for popularity.
STATS_AGES = (2, 7)


@dataclass
class Item:
    source: str
    id: str
    url: str
    name: str = ""
    name_alt: str = ""
    maker: str = ""
    series: str = ""
    character: str = ""
    category: str = ""
    scale: str = ""
    price: str = ""
    currency: str = ""
    price_note: str = ""
    release: str = ""
    preorder_start: str = ""
    preorder_end: str = ""
    status: str = ""
    rating: str = ""  # HPOI's 观感 level: 观感安心 / 观感温和 / 观感成熟
    adult: str = ""  # "1" for adult-oriented items; they are recorded without images
    jan: str = ""
    image_url: str = ""
    thumb: str = ""
    first_seen: str = ""
    size: str = ""
    material: str = ""
    sculptor: str = ""
    painter: str = ""
    bonus: str = ""  # 特典
    progress: str = ""  # HPOI 进程: latest events, newest first
    extra: str = ""  # other facts, "label：value；label：value"
    follows: str = ""  # HPOI 关注
    wants: str = ""  # HPOI 想买
    preorders: str = ""  # HPOI 预订
    owned: str = ""  # HPOI 已入
    score: str = ""  # HPOI rating out of 5
    score_count: str = ""
    views: str = ""  # HPOI 条目访问 bucket, e.g. 少于1000
    stats_date: str = ""  # when the HPOI counts were read
    thumb_src: str = ""  # where the thumbnail is downloaded from; not stored

    def row(self) -> dict[str, str]:
        data = asdict(self)
        return {k: data[k] for k in CSV_FIELDS}


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# HTTP


class Blocked(Exception):
    """The site said no: robots.txt disallows the page, or a bot challenge."""


def looks_like_challenge(resp: requests.Response) -> bool:
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    if resp.status_code in (403, 429, 503):
        head = resp.text[:5000]
        return any(s in head for s in ("Just a moment", "Attention Required", "challenge-platform", "cf-chl"))
    return False


class Fetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.robots: dict[str, RobotFileParser] = {}
        self.last_hit: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        delay = HOST_DELAY.get(host, DEFAULT_DELAY)
        rules = self.robots.get(host)
        if rules is not None:
            delay = max(delay, float(rules.crawl_delay(USER_AGENT) or 0))
        wait = self.last_hit.get(host, float("-inf")) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_hit[host] = time.monotonic()

    def _get(self, url: str, **kwargs) -> requests.Response:
        host = urlparse(url).netloc
        for attempt in (1, 2):
            self._throttle(host)
            try:
                resp = self.session.get(url, timeout=TIMEOUT, **kwargs)
            except requests.RequestException:
                if attempt == 2:
                    raise
                continue
            if resp.status_code >= 500 and attempt == 1 and not looks_like_challenge(resp):
                resp.close()
                continue
            return resp
        raise AssertionError("unreachable")

    def _allow(self, url: str) -> None:
        parts = urlparse(url)
        rules = self.robots.get(parts.netloc)
        if rules is None:
            rules = RobotFileParser()
            resp = self._get(f"{parts.scheme}://{parts.netloc}/robots.txt")
            if looks_like_challenge(resp):
                raise Blocked(f"{parts.netloc} 返回人机验证")
            if resp.status_code in (401, 403):
                rules.disallow_all = True
            elif resp.status_code >= 400:
                rules.allow_all = True
            else:
                rules.parse(resp.text.splitlines())
            self.robots[parts.netloc] = rules
        if not rules.can_fetch(USER_AGENT, url):
            raise Blocked(f"robots.txt 不允许抓取 {url}")

    def text(self, url: str) -> str:
        self._allow(url)
        resp = self._get(url)
        if looks_like_challenge(resp):
            raise Blocked(f"{urlparse(url).netloc} 返回人机验证（HTTP {resp.status_code}）")
        resp.raise_for_status()
        declared = "charset=" in resp.headers.get("Content-Type", "").lower()
        return resp.content.decode(resp.encoding if declared else "utf-8", errors="replace")

    def image(self, url: str) -> bytes:
        self._allow(url)
        with self._get(url, stream=True) as resp:
            resp.raise_for_status()
            data = io.BytesIO()
            for chunk in resp.iter_content(64 * 1024):
                data.write(chunk)
                if data.tell() > MAX_IMAGE_BYTES:
                    raise ValueError("image too large")
            return data.getvalue()


# --------------------------------------------------------------------------
# Parsing helpers

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
CURRENCIES = {"yen": "JPY", "jpy": "JPY", "日元": "JPY", "円": "JPY", "cny": "CNY", "rmb": "CNY",
              "元": "CNY", "人民币": "CNY", "usd": "USD", "美元": "USD"}


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def first(value):
    """ld+json fields may hold one value or a list of them."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def name_of(value) -> str:
    value = first(value)
    if isinstance(value, dict):
        return clean(value.get("name"))
    return clean(value) if isinstance(value, str) else ""


def ld_product(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "Product":
                return node
            main = node.get("mainEntity")
            if isinstance(main, dict) and main.get("@type") == "Product":
                return main
    return {}


def label_pairs(soup: BeautifulSoup) -> dict[str, str]:
    """<dt>label</dt><dd>value</dd> and <th>/<td> pairs, first one wins."""
    pairs: dict[str, str] = {}
    for label in soup.find_all(["dt", "th"]):
        value = label.find_next_sibling(["dd", "td"])
        if value is not None:
            pairs.setdefault(clean(label.get_text(" ")), clean(value.get_text(" ")))
    return pairs


def key_values(text: str) -> dict[str, str]:
    """'定价: 7,480日元, 作品: [{link=works/1, value=A}, {link=works/2, value=B}]' -> dict.

    Splits on ', ' outside brackets; HPOI's multi-valued fields become 'A、B'.
    """
    text = text or ""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(depth - 1, 0)
        elif ch == "," and depth == 0 and text[i + 1:i + 2] == " ":
            parts.append(text[start:i])
            start = i + 2
    parts.append(text[start:])
    out: dict[str, str] = {}
    last = None
    for part in parts:
        m = re.match(r"\s*([^:：\[\]{}]{1,12})[:：]\s*(.*)", part, re.S)
        if m:
            last = m.group(1).strip()
            out[last] = m.group(2).strip()
        elif last:  # a ', ' that belongs to the previous value
            out[last] += ", " + part.strip()
    for key, value in out.items():
        if value.startswith("[") and "value=" in value:
            out[key] = "、".join(v.strip() for v in re.findall(r"value=([^}]*)\}", value))
    return out


def price_digits(value) -> str:
    """'7480.0', '￥9,800', 11000 -> '7480', '9800', '11000'."""
    m = re.search(r"\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return str(int(float(m.group(0)))) if m else ""


def currency_of(declared, price_text: str = "") -> str:
    code = CURRENCIES.get(str(declared or "").strip().lower())
    if code:
        return code
    for word in ("日元", "円", "美元", "人民币", "元"):
        if word in price_text:
            return CURRENCIES[word]
    return str(declared or "").upper()


def month_of(text: str) -> str:
    """Release dates as YYYY-MM where the text allows it."""
    text = clean(text)
    m = re.search(r"(20\d{2})\s*[年/.-]\s*(\d{1,2})", text)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(20\d{2})\b", text)
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(2)}-{MONTHS.index(m.group(1).lower()) + 1:02d}"
    return text


def is_backfill(release: str, today: date) -> bool:
    m = re.fullmatch(r"(\d{4})-(\d{2})", release)
    if not m:
        return False
    months = (today.year - int(m.group(1))) * 12 + today.month - int(m.group(2))
    return months >= BACKFILL_MONTHS


STATUS_ZH = (("予約受付中", "预订中"), ("ショップ限定", "官网限定"), ("予約受注", "受注"),
             ("特典付き", "附特典"), ("同梱不可", "不可同捆"), ("販", "贩"))


def zh_status(text: str) -> str:
    for ja, zh in STATUS_ZH:
        text = text.replace(ja, zh)
    return text


# --------------------------------------------------------------------------
# HPOI (www.hpoi.net)

HPOI_BASE = "https://www.hpoi.net/"


def hpoi_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.find_all("li"):
        link = li.find("a", class_="hpoi-detail-grid-left", href=re.compile(r"hobby/\d+$"))
        if link is None:
            continue
        hid = re.search(r"(\d+)$", link["href"]).group(1)
        title = li.select_one(".hpoi-detail-grid-title a")
        info = {}
        for span in li.select(".hpoi-detail-grid-info span"):
            label = span.find("em")
            if label is not None:
                key = clean(label.get_text()).rstrip("：:")
                info[key] = clean(span.get_text().replace(label.get_text(), "", 1))
        img = link.find("img")
        items.append(Item(
            source="HPOI", id=hid, url=f"{HPOI_BASE}hobby/{hid}",
            name=clean(title.get_text()) if title else clean(img.get("alt") if img else ""),
            maker=info.get("厂商", ""), release=month_of(info.get("出荷", "")),
            thumb_src=img.get("src", "") if img else "",
        ))
    return items


def hpoi_info(soup: BeautifulSoup) -> dict[str, str]:
    """The 名称/出货/原型/... list; a row with an empty label continues the one above."""
    out: dict[str, str] = {}
    last = None
    for row in soup.select(".hpoi-infoList-item"):
        label, value = row.find("span"), row.find("p")
        label = clean(label.get_text()) if label else ""
        value = clean(value.get_text(" ")) if value else ""
        if label:
            last = label
            out[label] = value
        elif last and value:
            out[last] += "；" + value
    return out


def hpoi_stats(soup: BeautifulSoup, it: Item) -> None:
    """Popularity counts, rating, visits and the 进程 timeline."""
    counts = {}
    for button in soup.select(".hpoi-collection-box button[value]"):
        badge = button.select_one(".badge")
        counts[button["value"]] = re.sub(r"\D", "", badge.get_text()) if badge else ""
    it.follows = counts.get("care") or "0"
    it.wants = counts.get("want") or "0"
    it.preorders = counts.get("preorder") or "0"
    it.owned = counts.get("buy") or "0"
    text = clean(soup.get_text(" "))
    score = re.search(r"评分：\s*([\d.]+)\s*共有(\d+)个评分", text)
    it.score, it.score_count = (score.group(1), score.group(2)) if score else ("", "0")
    views = re.search(r"条目访问：\s*(\S+)", text)
    it.views = views.group(1) if views else ""
    events = []
    for slide in soup.select(".hpoi-process-box .items-process"):
        spans = [clean(s.get_text()) for s in slide.select(".item-time span")]
        detail = slide.select_one(".item-detail")
        desc = clean(detail.get_text(" ")) if detail else ""
        if spans and spans[0]:
            events.append(f"{spans[0]}：{desc}" if desc else spans[0])
    it.progress = " | ".join(events[:3])


def hpoi_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    hpoi_stats(soup, it)
    labels = hpoi_info(soup)
    it.size = labels.get("尺寸", "")
    it.material = labels.get("材质", "")
    it.sculptor = labels.get("原型", "")
    it.painter = labels.get("涂装", "")
    it.extra = "；".join(f"{k}：{labels[k]}" for k in ("发行", "原画") if labels.get(k))
    bonus = soup.find(string=re.compile(r"^\s*特典[：:]"))
    it.bonus = clean(re.sub(r"^\s*特典[：:]", "", bonus)) if bonus else ""
    prod = ld_product(soup)
    if not prod:
        return
    info = key_values(prod.get("description", ""))
    it.name = clean(prod.get("name")) or it.name
    it.name_alt = clean(first(prod.get("alternateName")) or "")
    it.maker = name_of(prod.get("manufacturer")) or info.get("制作", "") or it.maker
    it.series = info.get("作品", "")
    it.character = info.get("角色", "")
    it.category = " · ".join(x for x in (clean(prod.get("category")), info.get("系列", "")) if x)
    it.scale = info.get("比例", "") or labels.get("比例", "")
    it.image_url = clean(first(prod.get("image")) or "")
    price_text = info.get("定价", "")
    offer = first(prod.get("offers")) or {}
    it.price = price_digits(offer.get("price")) or price_digits(price_text)
    if it.price:
        it.currency = currency_of(offer.get("priceCurrency"), price_text)
    note = re.search(r"[（(]([^）)]*)[）)]", price_text)
    it.price_note = clean(note.group(1)) if note else ""
    attrs = info.get("属性", "")
    rating = re.search(r"观感\S+", attrs)
    it.rating = rating.group(0) if rating else ""
    if any(word in attrs for word in ADULT_WORDS):
        it.adult = "1"
    it.release = month_of(info.get("出货日") or info.get("发售日") or prod.get("releaseDate") or "") or it.release


# --------------------------------------------------------------------------
# Good Smile Company official shop (www.goodsmile.com)

GSC_HOME = "https://www.goodsmile.com/ja"


def gsc_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for card in soup.select("a.c-top-product-list__item[href*='/ja/product/']"):
        m = re.search(r"/ja/product/(\d+)", card["href"])
        if not m or m.group(1) in seen:
            continue
        pid = m.group(1)
        seen.add(pid)
        section = card.find_parent("section")
        heading = section.find(["h2", "h3"]) if section else None
        tags = [clean(heading.get_text(" "))] if heading else []
        tags += [clean(li.get_text()) for li in card.select(".c-product-item__label li") if clean(li.get_text())]
        maker = card.select_one(".c-product-item__maker")
        title = card.select_one(".c-product-item__title")
        price = card.select_one(".c-product-item__price")
        img = card.find("img")
        items.append(Item(
            source="GoodSmile", id=pid, url=f"https://www.goodsmile.com/ja/product/{pid}",
            name=clean(title.get_text()) if title else "",
            maker=clean(maker.get_text()) if maker else "",
            price=price_digits(price.get_text()) if price else "", currency="JPY",
            status=zh_status(" · ".join(tags)),
            thumb_src=urljoin(GSC_HOME, img["src"]) if img and img.get("src") else "",
        ))
    return items


def gsc_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    prod = ld_product(soup)
    if prod:
        # The card title is tidier; ld+json names carry 【再販】 prefixes and variant suffixes.
        it.name = it.name or clean(prod.get("name"))
        it.maker = it.maker or name_of(prod.get("brand"))
        it.category = clean(prod.get("category"))
        it.jan = clean(str(prod.get("gtin") or prod.get("gtin13") or ""))
        image = first(prod.get("image"))
        if isinstance(image, str) and image:
            it.image_url = urljoin(GSC_HOME, image)
        offer = first(prod.get("offers")) or {}
        it.price = price_digits(offer.get("price")) or it.price
        it.currency = currency_of(offer.get("priceCurrency")) or it.currency
        it.preorder_start = clean(offer.get("availabilityStarts"))[:10]
        it.preorder_end = clean(offer.get("availabilityEnds"))[:10]
    specs = label_pairs(soup)
    it.series = specs.get("作品名", "")
    it.character = specs.get("キャラクター名") or specs.get("キャラクター") or ""
    it.maker = specs.get("メーカー") or it.maker
    it.sculptor = specs.get("原型制作", "")
    it.painter = specs.get("彩色", "")
    spec = specs.get("仕様", "")
    size = re.search(r"(全高|全長|高さ|サイズ)[：:]?\s*約?\s*[\d.]+\s*(mm|cm)", spec)
    it.size = size.group(0) if size else ""
    it.extra = f"仕様：{spec}" if spec else ""
    scale = re.search(r"1/\d+", spec)
    it.scale = scale.group(0) if scale else ("ノンスケール" if "ノンスケール" in spec else "")
    for label, value in specs.items():
        if any(word in label for word in ("発売", "発送", "出荷")):
            it.release = month_of(value)
            break


# --------------------------------------------------------------------------
# HobbyLink Japan (www.hlj.com): figures added in the last 7 days, preorders

# HLJ shows adult items only to signed-in, age-verified accounts, so anonymous
# results are the same with or without its AdultItem=0 filter.
HLJ_SEARCH = ("https://www.hlj.com/search/?GenreCode2=Figures&MacroType2=Completed+Models"
              "&StockLevel=All+Future+Release&dateAdded2=-7&Sort=std+desc&Page={page}")


def hlj_total(html: str) -> str | None:
    """The '37 results' figure. Past the last page HLJ silently drops the filters."""
    m = re.search(r"([\d,]+)\s*results", html)
    return m.group(1) if m else None


def hlj_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for block in soup.select(".search-widget-block"):
        link = block.select_one(".product-item-name a[href]")
        if link is None:
            continue
        stock = block.find(id=re.compile(r"_stock$"))
        code = stock["id"][: -len("_stock")] if stock else link["href"].rstrip("/").rsplit("-", 1)[-1].upper()
        name = clean(link.get_text())
        img = block.find("img")
        thumb = img.get("src", "") if img else ""
        tags = ["预订中"] + (["再贩"] if "(Rerelease)" in name else [])
        items.append(Item(
            source="HLJ", id=code, url=urljoin("https://www.hlj.com/", link["href"]), name=name,
            status=" · ".join(tags), thumb_src=urljoin("https://www.hlj.com/", thumb) if thumb else "",
        ))
    return items


def hlj_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    prod = ld_product(soup)
    if prod:
        it.name = clean(prod.get("name")) or it.name
        it.maker = name_of(prod.get("brand"))
        it.jan = clean(str(prod.get("gtin13") or prod.get("gtin") or ""))
        image = first(prod.get("image"))
        it.image_url = image if isinstance(image, str) else ""
        offer = first(prod.get("offers")) or {}
        it.price = price_digits(offer.get("price"))
        it.currency = currency_of(offer.get("priceCurrency")) if it.price else ""
    for li in soup.find_all("li"):
        text = clean(li.get_text(" "))
        m = re.fullmatch(r"(Release Date|Order Deadline|Cancellation Deadline|Series|Manufacturer)\s*:\s*(.+)", text)
        if m:
            key, value = m.groups()
            if key == "Release Date":
                it.release = month_of(value)
            elif key == "Order Deadline":
                it.preorder_end = value
            elif key == "Cancellation Deadline":
                when = re.match(r"\d{4}-\d{2}-\d{2}", value)
                it.extra = f"取消截止：{when.group(0) if when else value}"
            elif key == "Series":
                it.series = value
            else:
                it.maker = value
        elif re.fullmatch(r"1/\d+ Scale", text):
            it.scale = text.split()[0]
    if not it.scale:
        scale = re.match(r"(1/\d+)\s", it.name)
        it.scale = scale.group(1) if scale else ""


# --------------------------------------------------------------------------
# Running a source


@dataclass
class Source:
    key: str
    feeds: list[list[str]]  # each feed: listing page URLs, first page first
    parse_list: Callable[[str], list[Item]]
    parse_detail: Callable[[str, Item], None]
    newest_first: bool  # stop paging once a page shows items we already have
    total_of: Callable[[str], str | None] | None = None  # result count, must stay the same on every page


SOURCE_DEFS = [
    Source("HPOI",
           [[f"{HPOI_BASE}hobby/all?order=add&category={c}"]
            + [f"{HPOI_BASE}hobby/all?order=add&category={c}&page={p}" for p in (2, 3)]
            for c in HPOI_CATEGORIES]
           # The full listing already includes 观感成熟 items; this one shows only them,
           # so they are not missed when a busy day pushes them off the first pages.
           + [[f"{HPOI_BASE}hobby/all?order=add&category={c}&{HPOI_ADULT_FILTER}"] for c in HPOI_CATEGORIES],
           hpoi_list, hpoi_detail, newest_first=True),
    Source("GoodSmile", [[GSC_HOME]], gsc_list, gsc_detail, newest_first=False),
    Source("HLJ", [[HLJ_SEARCH.format(page=p) for p in (1, 2, 3, 4)]], hlj_list, hlj_detail,
           newest_first=False, total_of=hlj_total),
]


def list_new(src: Source, fetcher: Fetcher, known: set[tuple[str, str]]) -> list[Item]:
    first_run = not any(key == src.key for key, _ in known)
    found: dict[str, Item] = {}
    for pages in src.feeds:
        previous: list[str] = []
        total, page_size = None, 0
        for n, url in enumerate(pages):
            html = fetcher.text(url)
            if src.total_of is not None:
                count = src.total_of(html)
                if n == 0:
                    total = count
                elif count != total:  # the site answered with a different result set
                    break
            listed = src.parse_list(html)
            if HPOI_ADULT_FILTER in url:  # known adult before the detail page says so
                for it in listed:
                    it.adult = "1"
            ids = [it.id for it in listed]
            if not listed or ids == previous:  # empty page, or the site ignored the page number
                break
            previous = ids
            fresh = [it for it in listed if (src.key, it.id) not in known and it.id not in found]
            for it in fresh:
                found[it.id] = it
            if src.newest_first and (first_run or len(fresh) < len(listed)):
                break
            page_size = max(page_size, len(listed))
            if len(listed) < page_size:  # a short page is the last one
                break
    return list(found.values())


def save_thumb(fetcher: Fetcher, it: Item) -> None:
    url = it.thumb_src or it.image_url
    if not url or it.adult:  # adult items are recorded without images
        return
    dest = THUMB_DIR / it.source / (re.sub(r"[^A-Za-z0-9_-]", "_", it.id) + ".jpg")
    if not dest.exists():
        try:
            img = Image.open(io.BytesIO(fetcher.image(url)))
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                img = Image.new("RGB", rgba.size, "white")
                img.paste(rgba, mask=rgba.getchannel("A"))
            img = img.convert("RGB")
            img.thumbnail(THUMB_SIZE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, "JPEG", quality=80, optimize=True, progressive=True)
        except Exception as exc:  # a missing thumbnail is not worth failing the item over
            log(f"   thumbnail {it.source} {it.id}: {exc}")
            return
    it.thumb = dest.relative_to(ROOT).as_posix()


def run_source(src: Source, fetcher: Fetcher, known: set[tuple[str, str]], today: date) -> tuple[list[Item], dict]:
    try:
        listed = list_new(src, fetcher, known)
    except Blocked as exc:
        return [], {"status": "blocked", "new": 0, "note": str(exc)}
    except Exception as exc:
        return [], {"status": "error", "new": 0, "note": f"{type(exc).__name__}: {exc}"}
    done, note, blocked = [], "", False
    for it in listed[:MAX_DETAILS]:
        try:
            src.parse_detail(fetcher.text(it.url), it)
        except Blocked as exc:
            note, blocked = str(exc), True
            break
        except Exception as exc:  # keep what the listing gave us
            log(f"   detail {src.key} {it.id}: {type(exc).__name__}: {exc}")
        if src.key == "HPOI":
            it.stats_date = today.isoformat()
            if is_backfill(it.release, today):
                it.status = BACKFILL
        save_thumb(fetcher, it)
        it.first_seen = today.isoformat()
        done.append(it)
        log(f"   + {it.id} {it.name[:50]}")
    left = len(listed) - len(done)
    if left and not blocked:
        note = f"还有 {left} 件留到下次"
    return done, {"status": "blocked" if blocked else "ok", "new": len(done), "note": note}


SOURCE_BY_KEY = {src.key: src for src in SOURCE_DEFS}


def age_days(row: dict[str, str], today: date) -> int:
    try:
        return (today - date.fromisoformat(row["first_seen"])).days
    except ValueError:
        return -1


def refresh_rows(fetcher: Fetcher, rows: list[dict[str, str]], pick: Callable[[dict[str, str]], bool],
                 today: date) -> int:
    """Read the detail page of each picked row again and keep what it now says."""
    done = 0
    for row in rows:
        if not pick(row):
            continue
        it = Item(**{k: row.get(k, "") for k in CSV_FIELDS})
        try:
            SOURCE_BY_KEY[row["source"]].parse_detail(fetcher.text(it.url), it)
        except Blocked as exc:
            log(f"   refresh stopped: {exc}")
            break
        except Exception as exc:  # one bad page should not stop the rest
            log(f"   refresh {row['source']} {row['id']}: {type(exc).__name__}: {exc}")
            continue
        if row["source"] == "HPOI":
            it.stats_date = today.isoformat()
        fresh = it.row()
        for key in DETAIL_FIELDS:
            if fresh[key]:
                row[key] = fresh[key]
        done += 1
    return done


# --------------------------------------------------------------------------
# Output


def load_rows() -> list[dict[str, str]]:
    if not ITEMS_CSV.exists():
        return []
    with ITEMS_CSV.open(newline="", encoding="utf-8-sig") as f:
        return [{k: row.get(k) or "" for k in CSV_FIELDS} for row in csv.DictReader(f)]


def save_rows(rows: list[dict[str, str]]) -> None:
    ITEMS_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = ITEMS_CSV.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads it as UTF-8
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(ITEMS_CSV)


def md(text: str) -> str:
    """Make text safe inside a Markdown table cell."""
    text = clean(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\|\[\]*_`])", r"\\\1", text)


CURRENCY_LABEL = {"JPY": "日元", "CNY": "元", "USD": "美元"}


def item_cell(r: dict[str, str]) -> str:
    lines = [f"**[{md(r['name']) or r['id']}]({r['url']})**"]
    if r["name_alt"]:
        lines.append(f"<sub>{md(r['name_alt'])}</sub>")
    meta = " · ".join(md(x) for x in (r["maker"], r["series"], r["character"]) if x)
    if meta:
        lines.append(meta)
    tags = " · ".join(md(x) for x in (r["category"], r["scale"], r["status"], "成人向" * bool(r["adult"])) if x)
    if tags:
        lines.append(f"<sub>{tags}</sub>")
    return "<br>".join(lines)


def price_cell(r: dict[str, str]) -> str:
    if not r["price"]:
        return "—"
    amount = f"{int(r['price']):,}" if r["price"].isdigit() else md(r["price"])
    text = f"{amount} {CURRENCY_LABEL.get(r['currency'], md(r['currency']))}".strip()
    if r["price_note"]:
        text += f"<br><sub>{md(r['price_note'])}</sub>"
    return text


def date_cell(r: dict[str, str]) -> str:
    parts = [md(r["release"]) or "未定"]
    if r["preorder_end"]:
        parts.append(f"<sub>截订 {md(r['preorder_end'])}</sub>")
    return "<br>".join(parts)


def table(rows: list[dict[str, str]]) -> list[str]:
    out = ["| 图 | 商品 | 价格 | 发售 |", "|---|---|---|---|"]
    for r in rows:
        img = f'<img src="../{r["thumb"]}" width="80">' if r["thumb"] else ""
        out.append(f"| {img} | {item_cell(r)} | {price_cell(r)} | {date_cell(r)} |")
    return out


def render_day(day: str, rows: list[dict[str, str]]) -> str:
    groups = {key: [r for r in rows if r["source"] == key] for key in SOURCES}
    summary = " · ".join(f"{SOURCES[k]} {len(v)}" for k, v in groups.items() if v)
    out = [f"# {day} 手办新品", "", f"共 {len(rows)} 件（{summary}）。点名称打开原页面。", ""]
    for key, items in groups.items():
        if not items:
            continue
        adult = [r for r in items if r["adult"]]
        fresh = [r for r in items if not r["adult"] and r["status"] != BACKFILL]
        old = [r for r in items if not r["adult"] and r["status"] == BACKFILL]
        out += [f"## {SOURCES[key]}（{len(items)}）", ""]
        if fresh:
            out += table(fresh) + [""]
        if old:
            out += [f"<details><summary>{BACKFILL} {len(old)} 件：HPOI 新收录、但早已发售的老商品</summary>", "",
                    *table(old), "", "</details>", ""]
        if adult:
            out += [f"<details open><summary>成人向 {len(adult)} 件：只记录文字和链接，不保存图片</summary>", "",
                    *table(adult), "", "</details>", ""]
    return "\n".join(out)


def render_readme(rows: list[dict[str, str]], last_run: dict) -> str:
    counts = Counter(r["first_seen"] for r in rows if r["first_seen"])
    days = sorted(counts, reverse=True)
    out = ["# figure-news 手办新品日报", "",
           "每天早上（北京时间 7:40 左右）自动抓一次 HPOI、Good Smile 官网、HLJ 的新品，按天汇总成表。", ""]
    if days:
        out += [f"**最新一期：[{days[0]}](daily/{days[0]}.md)（{counts[days[0]]} 件）**", ""]
    out += [f"**公开网页：[{PAGES_URL}]({PAGES_URL})**（GitHub Pages，每天抓取后自动更新，最近 30 天）", ""]
    out += ["## 上次运行", "", f"{last_run['time']}（北京时间）", "",
            "| 来源 | 结果 | 新增 | 备注 |", "|---|---|---|---|"]
    labels = {"ok": "✅ 正常", "blocked": "⛔ 被网站拦截", "error": "⚠️ 出错"}
    for key, label in SOURCES.items():
        s = last_run["sources"].get(key, {})
        out.append(f"| {label} | {labels.get(s.get('status'), '—')} | {s.get('new', 0)} | {md(s.get('note', ''))} |")
    out += ["", "## 最近 30 天", ""]
    out += [f"- [{d}](daily/{d}.md) · {counts[d]} 件" for d in days[:30]] or ["（还没有数据）"]
    out += [
        "", "## 文件", "",
        "- `daily/日期.md`：当天第一次出现的商品，带缩略图",
        "- `data/items.csv`：抓到过的全部商品，一行一件，可以直接用 Excel 打开",
        "- `thumbs/`：缩略图",
        "- `scrape.py`：抓取脚本；`.github/workflows/daily.yml`：每天定时运行",
        "- `build_site.py` 和 `page/template.html`：生成网页版（输出到 build/，不进仓库）",
        "", "## 字段", "",
        "| 字段 | 含义 |", "|---|---|",
        "| first_seen | 第一次抓到的日期 |",
        "| source / id / url | 来源站 / 站内编号 / 原页面 |",
        "| name / name_alt | 名称 / 日文原名（HPOI 提供） |",
        "| maker / series / character | 厂商 / 作品 / 角色 |",
        "| category / scale | 类型、产品线 / 比例 |",
        "| price / currency / price_note | 价格 / 币种 / 备注（如 HPOI 的人民币参考价） |",
        "| release | 发售（出货）月份 |",
        "| preorder_start / preorder_end | 预订开始 / 截止 |",
        "| status | 预订中、再贩、官网限定、旧品补录等 |",
        "| rating / adult | HPOI 的观感等级 / 成人向标记（1 = 是） |",
        "| size / material / sculptor / painter | 尺寸 / 材质 / 原型 / 涂装 |",
        "| bonus / progress / extra | 特典 / HPOI 进程（开订、出荷等最新动态）/ 其他信息（如 HLJ 取消截止日） |",
        "| follows / wants / preorders / owned | HPOI 的关注 / 想买 / 预订 / 已入人数 |",
        "| score / score_count / views | HPOI 评分 / 评分人数 / 访问量 |",
        "| stats_date | 人气数据的读取日期（收录当天、第 2 天、第 7 天各读一次） |",
        "| jan | JAN 条码 |",
        "| image_url / thumb | 原图链接 / 缩略图路径 |",
        "", "## 手动运行", "",
        "GitHub 仓库页 → Actions → daily → Run workflow。",
        "", "## 抓取规矩", "",
        "如实标明身份（`figure-news/0.1`），遵守各站 robots.txt，"
        "同一网站两次请求至少隔 3 秒（HPOI 按它的要求隔 5 秒），"
        "遇到人机验证直接跳过、不绕过。AmiAmi、HobbySearch、电击 Hobby 因此没有收录。",
        "", "## 成人向", "",
        "HPOI 里观感成熟的条目照常录入并标为成人向，只记录文字和链接，不保存图片。"
        "HLJ 的成人向商品要登录并通过年龄验证才会显示，匿名访问看不到，所以没有收录。",
        "", "_本文件由 scrape.py 自动生成，手动修改会在下次运行时被覆盖。_", "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect new figures from HPOI, Good Smile and HLJ.")
    parser.add_argument("--refresh-days", type=int, default=0, metavar="N",
                        help="also re-read the detail page of every item first seen in the last N days")
    args = parser.parse_args()
    now = datetime.now(BEIJING)
    today = now.date()
    rows = load_rows()
    known = {(r["source"], r["id"]) for r in rows}
    fetcher = Fetcher()
    status: dict[str, dict] = {}
    new_rows: list[dict[str, str]] = []
    for src in SOURCE_DEFS:
        log(f"== {src.key}")
        items, status[src.key] = run_source(src, fetcher, known, now.date())
        known |= {(src.key, it.id) for it in items}
        new_rows += [it.row() for it in items]
        log(f"   {status[src.key]}")
    rows += new_rows

    just_added = {(r["source"], r["id"]) for r in new_rows}

    def pick(row: dict[str, str]) -> bool:
        if (row["source"], row["id"]) in just_added:
            return False
        if args.refresh_days:
            return 0 <= age_days(row, today) < args.refresh_days
        # Popularity grows after an announcement, so HPOI items are read again a few days on.
        return row["source"] == "HPOI" and row["status"] != BACKFILL and age_days(row, today) in STATS_AGES

    log("== refresh")
    log(f"   {refresh_rows(fetcher, rows, pick, today)} items re-read")
    save_rows(rows)
    last_run = {"time": now.strftime("%Y-%m-%d %H:%M"), "sources": status}
    LAST_RUN.write_text(json.dumps(last_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for day in sorted({r["first_seen"] for r in new_rows}):
        DAILY_DIR.mkdir(exist_ok=True)
        (DAILY_DIR / f"{day}.md").write_text(render_day(day, [r for r in rows if r["first_seen"] == day]),
                                             encoding="utf-8")
    README.write_text(render_readme(rows, last_run), encoding="utf-8")
    return 0 if any(s["status"] == "ok" for s in status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
