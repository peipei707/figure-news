#!/usr/bin/env python3
"""figure-news: a daily digest of new figures from 12 figure websites and shops.

Each run reads every source's "new items" listing, opens the detail page of
items it has not seen before, and writes:

    data/items.csv            every item seen so far, one row per item
    data/last_run.json        how each source did on the latest run
    daily/YYYY-MM-DD.md       the items first seen that day, with thumbnails
    thumbs/<source>/<id>.webp the product pictures, scaled down, for the pages
    README.md                 the latest day and an index of recent days

The crawler says who it is, follows robots.txt, waits between requests to
the same host, and skips a source whose site answers with a bot challenge
instead of trying to get past it.
"""

from __future__ import annotations

import argparse
import csv
import html as html_entities
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
# Thumbnails fit inside this box. Product pictures are mostly upright, and the
# web page shows them in 3:4 tiles about 200px wide on screens of 2x density.
THUMB_BOX = (400, 560)
THUMB_QUALITY = 76
# Earlier versions saved 200px JPEG thumbnails. Each run replaces up to this
# many of them, newest first, with the larger picture; older ones are left.
THUMB_UPGRADES = int(os.environ.get("FIGURE_NEWS_THUMB_UPGRADES", "80"))
THUMB_UPGRADE_DAYS = 60

# Seconds between two requests to the same host. HPOI's robots.txt asks for 5.
DEFAULT_DELAY = float(os.environ.get("FIGURE_NEWS_DELAY", "3"))
HOST_DELAY = {"www.hpoi.net": 5.0, "rfx.hpoi.net": 5.0, "a.sofmap.com": 15.0, "solarisjapan.com": 6.0}
# Sofmap answers 403 to the sixth product page read within a few minutes, so its
# listing is the source of record and only a few product pages are read per run.
SOFMAP_DETAILS = int(os.environ.get("FIGURE_NEWS_SOFMAP_DETAILS", "3"))
# Detail pages opened per source per run; the rest wait for the next run.
MAX_DETAILS = int(os.environ.get("FIGURE_NEWS_MAX_DETAILS", "40"))
# HPOI categories to follow (100 = 手办), comma-separated, e.g. "100,500".
HPOI_CATEGORIES = [c.strip() for c in os.environ.get("FIGURE_NEWS_HPOI_CATEGORIES", "100").split(",") if c.strip()]
# HPOI also catalogues products that came out long ago. Anything released
# this many months back or earlier is folded away on the daily page.
BACKFILL_MONTHS = 3
BACKFILL = "旧品补录"
# HPOI 属性 words that mark an adult-oriented item, and the listing filter that
# shows only 观感成熟 items. HPOI lists these like any other entry, cover picture
# included, so they are recorded the same way and carry the mark.
ADULT_WORDS = ("观感成熟", "R18", "R-18", "18禁", "成人")
HPOI_ADULT_FILTER = "r18=315"

SOURCES = {
    "HPOI": "HPOI",
    "GoodSmile": "Good Smile 官网",
    "Native": "Native 官网",
    "MegaHobby": "MegaHouse 官网",
    "KDcolle": "KDcolle 官网",
    "TokyoFigure": "Tokyo Figure",
    "Sofmap": "Sofmap",
    "HLJ": "HLJ",
    "HobbyGenki": "Hobby-Genki",
    "NinNinGame": "Nin-Nin-Game",
    "Solaris": "Solaris Japan",
    "Figuya": "Figuya",
}

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
# Days after an HPOI item is first seen when its page is read again for popularity, and how many
# days after the last of them a run that missed it may still take the reading.
STATS_AGES = (2, 7)
STATS_CATCH_UP = 3


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
    adult: str = ""  # "1" for items HPOI rates 观感成熟
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
    if resp.headers.get("x-amzn-waf-action") in ("challenge", "captcha"):  # AWS WAF: HTTP 202, empty body
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
        self.refused: dict[str, str] = {}  # hosts that said no during this run, and how

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
        if host in self.refused:  # it already said no: do not knock again in this run
            raise Blocked(self.refused[host])
        for attempt in (1, 2, 3):
            self._throttle(host)
            try:
                resp = self.session.get(url, timeout=TIMEOUT, **kwargs)
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2)
                continue
            if resp.status_code >= 500 and attempt < 3 and not looks_like_challenge(resp):
                resp.close()
                time.sleep(2)
                continue
            return resp
        raise AssertionError("unreachable")

    def _allow(self, url: str, referer: str | None = None) -> None:
        parts = urlparse(url)
        rules = self.robots.get(parts.netloc)
        if rules is None:
            rules = RobotFileParser()
            hdrs = {"Referer": referer} if referer else None
            resp = self._get(f"{parts.scheme}://{parts.netloc}/robots.txt", headers=hdrs)
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
        if resp.status_code in (403, 429):  # refused or asked to slow down: stop asking for this run
            host = urlparse(url).netloc
            self.refused[host] = f"{host} 拒绝了请求（HTTP {resp.status_code}），这次先停"
            raise Blocked(self.refused[host])
        resp.raise_for_status()
        declared = "charset=" in resp.headers.get("Content-Type", "").lower()
        encoding = resp.encoding if declared and resp.encoding else "utf-8"
        if encoding.lower().replace("-", "_") in ("shift_jis", "sjis", "x_sjis", "windows_31j", "ms932"):
            encoding = "cp932"  # what Japanese sites that say Shift_JIS actually send (①, ～, ㈱ ...)
        return resp.content.decode(encoding, errors="replace")

    def image(self, url: str, referer: str | None = None) -> bytes:
        self._allow(url, referer=referer)
        hdrs = {"Referer": referer} if referer else None
        with self._get(url, stream=True, headers=hdrs) as resp:
            if resp.status_code == 429:  # for a picture a 403 can just mean "no such file"; a 429 cannot
                host = urlparse(url).netloc
                self.refused[host] = f"{host} 要求放慢（HTTP 429），这次先停"
                raise Blocked(self.refused[host])
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
CURRENCIES = {"yen": "JPY", "jpy": "JPY", "日元": "JPY", "円": "JPY", "¥": "JPY",
              "cny": "CNY", "rmb": "CNY", "元": "CNY", "人民币": "CNY",
              "usd": "USD", "美元": "USD", "$": "USD", "us$": "USD",
              "eur": "EUR", "欧元": "EUR", "€": "EUR"}


def norm_img_url(url: str, base: str = "") -> str:
    url = clean(url)
    if not url or url.startswith("data:"):
        return ""
    if base:
        url = urljoin(base, url)
    elif url.startswith("//"):
        url = "https:" + url
    return re.sub(r"(https?://[^/]+)//+", r"\1/", url)


def og_image(soup: BeautifulSoup, base: str = "") -> str:
    for attrs in ({"property": "og:image"}, {"name": "twitter:image"}, {"property": "og:image:secure_url"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            url = norm_img_url(tag["content"], base)
            if url:
                return url
    return ""


def parse_ymd(text: str) -> str:
    """'2026年11月20日', '2026/11/5 12:00', '2027-01-12' -> 'YYYY-MM-DD' or ''."""
    m = re.search(r"(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})", clean(text))
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def has_adult_hint(text: str) -> bool:
    return bool(re.search(r"(?:18歳以上|R-?18|18禁|成人向|(?<![A-Za-z0-9])18\+|nsfw|cast-?off|adult only)", text or "", re.I))


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


def unescaped(value):
    """ld+json is script text, so its strings still hold entities: Heaven&#x27;s -> Heaven's."""
    if isinstance(value, str):
        return html_entities.unescape(value)
    if isinstance(value, list):
        return [unescaped(v) for v in value]
    if isinstance(value, dict):
        return {k: unescaped(v) for k, v in value.items()}
    return value


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
                return unescaped(node)
            main = node.get("mainEntity")
            if isinstance(main, dict) and main.get("@type") == "Product":
                return unescaped(main)
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


SALES_ZH = {"販売": "首发", "再販": "再贩", "再販予約受注": "再贩受注", "予約受注": "受注"}


def sales_history(text: str) -> str:
    """'【販売：2024年06月】【再販：2027年05月】' -> '首发 2024年06月、再贩 2027年05月'."""
    parts = re.findall(r"【([^：:】]+)[：:]\s*([^】]+)】", text)
    if not parts:
        return clean(text)
    return "、".join(f"{SALES_ZH.get(clean(k), zh_status(clean(k)))} {clean(v)}" for k, v in parts)


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
    if prod:
        info = key_values(prod.get("description", ""))
        it.name = clean(prod.get("name")) or it.name
        it.name_alt = clean(first(prod.get("alternateName")) or "")
        it.maker = name_of(prod.get("manufacturer")) or info.get("制作", "") or it.maker
        it.series = info.get("作品", "")
        it.character = info.get("角色", "")
        it.category = " · ".join(x for x in (clean(prod.get("category")), info.get("系列", "")) if x)
        it.scale = info.get("比例", "") or labels.get("比例", "")
        it.image_url = norm_img_url(first(prod.get("image")) or "", HPOI_BASE)
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
    if not it.image_url:
        cover = soup.select_one('.hpoi-ibox-img img[src], img[src*="/gk/cover/n/"], img[src*="/gk/cover/"]')
        it.image_url = norm_img_url(cover.get("src", "") if cover else "", HPOI_BASE) or og_image(soup, HPOI_BASE) or norm_img_url(it.thumb_src, HPOI_BASE)


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
    scale = re.search(r"1/\d+", spec)
    it.scale = scale.group(0) if scale else ("ノンスケール" if "ノンスケール" in spec else "")
    for label, value in specs.items():
        if any(word in label for word in ("発売", "発送", "出荷")):
            it.release = month_of(value)
            break
    # The shop does not say when a new item ships. A rerun has a 関連情報 row,
    # 【販売：2024年06月】【再販：2027年05月】, and the latest month in it is this run's.
    history = specs.get("関連情報", "")
    months = [month_of(m) for m in re.findall(r"20\d{2}\s*年\s*\d{1,2}\s*月", history)]
    if months and not re.fullmatch(r"\d{4}-\d{2}", it.release):
        it.release = max(months)
    notes = [f"仕様：{spec}" if spec else "", f"发售记录：{sales_history(history)}" if history else ""]
    it.extra = "；".join(note for note in notes if note)


# --------------------------------------------------------------------------
# HobbyLink Japan (www.hlj.com): figures added in the last 7 days, preorders

# HLJ shows adult items only to signed-in, age-verified accounts, so anonymous
# results are the same with or without its AdultItem=0 filter.
HLJ_SEARCH = ("https://www.hlj.com/search/?GenreCode2=Figures&MacroType2=Completed+Models"
              "&StockLevel=All+Future+Release&dateAdded2=-7&Sort=std+desc&Page={page}")


HLJ_RERUN = re.compile(r"\((?:re-?release|re-?issue|re-?run)\)", re.I)


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
        tags = ["预订中"] + (["再贩"] if HLJ_RERUN.search(name) else [])
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
# Sofmap (a.sofmap.com): figures and dolls whose pre-orders opened in the last 14 days.
# A large Japanese retailer; prices include tax and every listing carries its JAN.

# The calendar page fills itself from this list fragment, newest pre-order start first.
SOFMAP_LIST = ("https://a.sofmap.com/calendar_list_parts.aspx?pno={page}&search_type=reserv&product_type=ALL"
               "&dispcnt=100&date_from_add=-14&date_to_add=0&gid=002140020")
SOFMAP_OPEN, SOFMAP_FULL = "预订中", "预订已满"


def sofmap_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for li in soup.select("ul.product_list > li"):
        link = li.select_one("a.product_name[name]")
        sku = re.search(r"_(\d+)$", link["name"]) if link else None
        if not sku or sku.group(1) in seen:  # the fragment holds every item twice, once per layout
            continue
        seen.add(sku.group(1))
        img = li.find("img")
        src = img.get("src", "") if img else ""
        jan = re.search(r"/(\d{13})\.", src)  # .../product/large/4573102739810.jpg
        brand, price, opened = li.select_one(".brand"), li.select_one(".price"), li.select_one(".date")
        day = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", opened.get_text() if opened else "")
        name = clean(link.get_text())
        scale = re.search(r"(?<![\d/])1/\d+(?!\d)", name)
        items.append(Item(
            source="Sofmap", id=sku.group(1), url=f"https://a.sofmap.com/product_detail.aspx?sku={sku.group(1)}",
            name=name, maker=clean(brand.get_text()) if brand else "", scale=scale.group(0) if scale else "",
            price=price_digits(price.get_text()) if price else "", currency="JPY",
            jan=jan.group(1) if jan else "",
            # the product page's first picture, 1000px, sits beside the listing's 250px one
            image_url=f"https://image.sofmap.com/images/product/pim/{jan.group(1)}_A01.jpg" if jan else "",
            preorder_start=f"{day.group(1)}-{int(day.group(2)):02d}-{int(day.group(3)):02d}" if day else "",
            status=" · ".join([SOFMAP_FULL if "soldout" in (li.get("class") or []) else SOFMAP_OPEN]
                              + (["再贩"] if "再販" in link.get_text() else [])),
            thumb_src=src,
        ))
    return items


def sofmap_detail(html: str, it: Item) -> None:
    """What the listing does not say: release month, series, product line, size."""
    soup = BeautifulSoup(html, "html.parser")
    prod = ld_product(soup)
    if prod:
        it.name = it.name or clean(prod.get("name"))
        image = first(prod.get("image"))
        it.image_url = image if isinstance(image, str) else it.image_url
        offer = first(prod.get("offers")) or {}
        it.price = price_digits(offer.get("price")) or it.price
    specs = label_pairs(soup)
    it.maker = specs.get("メーカー") or it.maker
    if re.fullmatch(r"\d{8,14}", specs.get("JANコード", "")):
        it.jan = specs["JANコード"]
    it.series = specs.get("作品タイトル/愛称", "")
    it.character = specs.get("キャラクター", "")
    it.release = month_of(specs.get("メーカー発売日") or specs.get("発売日") or "")
    # 在庫 starts with the state, "限定予約中 ..." or "限定数終了 ...", and goes on with boilerplate
    # that mentions 終了 either way, so only the first word counts.
    stock = specs.get("在庫", "").split(" ")[0]
    state = SOFMAP_FULL if "終了" in stock else SOFMAP_OPEN if "予約" in stock else ""
    if state:
        it.status = " · ".join([state] + [t for t in it.status.split(" · ") if t and t not in (SOFMAP_OPEN, SOFMAP_FULL)])
    # トップ > ホビー > フィギュア・ドール > フィギュア・ドール > フィギュアーツ: the last step is the product line
    crumbs = [clean(a.get_text()) for a in soup.select("ol.breadcrumb a")]
    it.category = crumbs[-1] if crumbs and crumbs[-1] not in ("トップ", "ホビー", "フィギュア・ドール") else ""
    text = clean(soup.get_text(" "))
    size = re.search(r"サイズ[：:]\s*([^・【※]{2,40})", text)
    it.size = clean(size.group(1)) if size else ""
    material = re.search(r"(?:材質|素材)[：:]\s*([^・【※]{2,40})", text)
    it.material = clean(material.group(1)) if material else ""


# --------------------------------------------------------------------------
# Solaris Japan (solarisjapan.com): an export shop on Shopify. Its figure collection
# lists the newest products first; prices are in US dollars.

SOLARIS_LIST = "https://solarisjapan.com/collections/figures/products.json?limit=50&page={page}"


def solaris_list(text: str) -> list[Item]:
    try:
        products = json.loads(text).get("products", [])
    except (ValueError, AttributeError):
        return []
    items = []
    for p in products:
        if not isinstance(p, dict) or not p.get("id") or not p.get("handle"):
            continue
        tags = [t for t in p.get("tags") or [] if isinstance(t, str)]

        def meta(key: str) -> str:  # tags look like "meta-franchise-Blue Archive"
            return next((clean(t.split("-", 2)[2]) for t in tags if t.startswith(f"meta-{key}-")), "")

        variants = {v.get("title"): v for v in p.get("variants") or [] if isinstance(v, dict)}
        early = variants.get("Early Bird")  # the pre-order price, offered until release
        offer = early if early and early.get("available") else variants.get("Brand New") or early or {}
        price = re.fullmatch(r"\d+(?:\.\d+)?", str(offer.get("price") or ""))
        adult = "nsfw" in tags
        image = first(p.get("images")) or {}
        kind = meta("figure")
        title = clean(p.get("title"))
        # Titles read "Franchise - Character - Line - 1/7 (Maker)"; half the products have no franchise tag.
        parts = title.split(" - ")
        # Vendors read "A, B" or "A as ManufacturerB"; the first name is the maker.
        maker = re.split(r",| as Manufacturer", clean(p.get("vendor")))[0].strip()
        # The tag is the franchise in English ("Jojo's Bizarre Adventure") but sometimes only its family
        # ("Honkai" for "Honkai: Star Rail - ..."); then the title's own first part says more.
        franchise, head = meta("franchise"), parts[0] if len(parts) > 1 else ""
        series = head if head and (not franchise or franchise.lower() in head.lower()) else franchise
        items.append(Item(
            source="Solaris", id=str(p["id"]), url=f"https://solarisjapan.com/products/{p['handle']}",
            name=title, maker=maker, series=series,
            character=meta("character"), category="" if kind == "General" else kind,
            price=str(round(float(price.group(0)))) if price else "", currency="USD" if price else "",
            status="预订中" if early and early.get("available") else "现货" if offer.get("available") else "",
            adult="1" if adult else "",
            thumb_src=norm_img_url(image.get("src") if isinstance(image, dict) else ""),
        ))
    return items


def solaris_cdn_url(url: str) -> str:
    """Convert solarisjapan.com/cdn/shop/files/<id>_750x.jpg to direct cdn.shopify.com full-res URL."""
    m = re.match(r"https?://solarisjapan\.com/cdn/shop/files/([^?#]+)(.*)", url or "")
    if not m:
        return ""
    fname = re.sub(r"_\d+x(?=\.[A-Za-z0-9]+$)", "", m.group(1))
    return f"https://cdn.shopify.com/s/files/1/0318/2649/files/{fname}{m.group(2)}"


def solaris_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    prod = ld_product(soup)
    if prod:
        code = clean(str(prod.get("gtin14") or prod.get("gtin13") or prod.get("gtin") or prod.get("sku") or ""))
        code = code[1:] if re.fullmatch(r"0\d{13}", code) else code
        it.jan = code if re.fullmatch(r"\d{8,14}", code) else ""
        image = first(prod.get("image"))
        if isinstance(image, str):
            normed = norm_img_url(image)
            it.image_url = solaris_cdn_url(normed) or normed
        it.maker = it.maker or name_of(prod.get("brand"))
    if not it.image_url:
        og = og_image(soup, it.url)
        it.image_url = norm_img_url(it.thumb_src) or solaris_cdn_url(og) or og
    text = clean(soup.get_text(" "))
    release = re.search(r"Release Date\s+((?:\d{1,2}\.?\s+)?[A-Za-z]{3,9}\.?\s+20\d{2}|20\d{2}[-/.]\d{1,2})", text)
    it.release = month_of(release.group(1)) if release else ""
    size = re.search(r"Dimensions\s+((?:[A-Z]=\d+(?:\.\d+)?\s*mm\s*(?:\([^)]*\))?[\s,x×]*)+)", text)
    it.size = clean(re.sub(r"\([^)]*\)", "", size.group(1)).replace("=", " ")).rstrip(" ,x×") if size else ""
    scale = re.search(r"(?<![\d/])1/\d+(?!\d)", it.name)
    it.scale = scale.group(0) if scale else ""


# --------------------------------------------------------------------------
# Native official site (www.native-web.jp): Native, BINDing, FROG, Rocket Boy,
# Second Axe, Pink・Cat, CAMELOT, Magic Bullets, etc.

NATIVE_BASE = "https://www.native-web.jp/"
NATIVE_FEEDS = [
    ["https://www.native-web.jp/creators/"],
    ["https://www.native-web.jp/characters/"],
]


def native_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.select("a[href]"):
        href = urljoin(NATIVE_BASE, a["href"])
        m = re.search(r"native-web\.jp/(creators|characters)/(\d+)/?$", href)
        if not m or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        img = a.find("img")
        thumb = norm_img_url(img.get("src", "") if img else "", NATIVE_BASE)
        heading = a.find(["h2", "h3", "h4", "p"])
        name = clean(heading.get_text(" ")) if heading else clean(a.get_text(" "))
        name = re.sub(r"\s*発売時期[：:].*$", "", name).strip()
        items.append(Item(
            source="Native", id=m.group(2), url=href,
            name=name, thumb_src=thumb,
        ))
        if len(items) >= 12:
            break
    return items


def native_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    specs = label_pairs(soup)
    it.name = specs.get("商品名") or it.name
    it.series = specs.get("作品名") or ""
    it.maker = specs.get("発売元") or specs.get("販売元") or "ネイティブ"
    price_text = specs.get("価格", "")
    it.price = price_digits(price_text)
    if it.price:
        it.currency = "JPY"
    it.release = month_of(specs.get("発売", "")) or it.release
    it.material = specs.get("仕様", "")
    size_text = specs.get("サイズ", "")
    scale = re.search(r"1/\d+", size_text)
    it.scale = scale.group(0) if scale else ("ノンスケール" if "ノンスケール" in size_text else "")
    size = re.search(r"(?:全高|全長|高さ|サイズ)[：:]?\s*約?\s*[\d.]+\s*(?:mm|cm)(?:（[^）]*）)?", size_text)
    it.size = size.group(0) if size else size_text
    it.sculptor = specs.get("原型制作", "") or specs.get("原型", "")
    it.painter = specs.get("彩色制作", "") or specs.get("彩色", "")
    it.bonus = specs.get("特典", "")
    if has_adult_hint(specs.get("対象年齢", "") + " " + it.name):
        it.adult = "1"
    text = clean(soup.get_text(" "))
    window = re.search(r"予約受付期間\s*(20\d{2}年\d{1,2}月\d{1,2}日[^～〜\-]*)[～〜\-]\s*(20\d{2}年\d{1,2}月\d{1,2}日)", text)
    if window:
        it.preorder_start = parse_ymd(window.group(1))
        it.preorder_end = parse_ymd(window.group(2))
    tags = []
    if "予約受付中" in text or "Preorder Now" in text:
        tags.append("预订中")
    elif "予約受付終了" in text or "受付終了" in text:
        tags.append("预订已满")
    if "再販" in text[:1500]:
        tags.append("再贩")
    if "オンライン" in text[:2500] or "限定" in it.name:
        tags.append("官网限定")
    if it.bonus and it.bonus not in ("なし", "無し", "—", "-"):
        tags.append("附特典")
    it.status = " · ".join(dict.fromkeys(tags))
    it.image_url = og_image(soup, NATIVE_BASE) or norm_img_url(it.thumb_src, NATIVE_BASE)
    if specs.get("付属品") and specs["付属品"] not in ("なし", "無し"):
        it.extra = f"仕様：{specs['仕様']}；付属品：{specs['付属品']}" if specs.get("仕様") else f"付属品：{specs['付属品']}"


# --------------------------------------------------------------------------
# Tokyo Figure (tokyofigure.jp): Japanese distributor for ALTER, Stronger,
# Mimeyoi, Knead, APEX, Myethos, AniGame, Prime 1 Studio, Reverse Studio, etc.

TF_BASE = "https://tokyofigure.jp/"
TF_LIST = "https://tokyofigure.jp/products/list.php"


def tokyofigure_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, by_id = [], {}
    for a in soup.select("a[href*='/products/detail/']"):
        href = urljoin(TF_BASE, a["href"])
        m = re.search(r"/products/detail/(\d+)", href)
        if not m:
            continue
        pid = m.group(1)
        img = a.find("img")
        thumb = norm_img_url(img.get("src", "") if img else "", TF_BASE)
        name = clean(a.get_text(" "))
        if pid not in by_id:
            it = Item(source="TokyoFigure", id=pid, url=f"https://tokyofigure.jp/products/detail/{pid}",
                      name=name, thumb_src=thumb)
            by_id[pid] = it
            items.append(it)
        else:
            if name and not by_id[pid].name:
                by_id[pid].name = name
            if thumb and not by_id[pid].thumb_src:
                by_id[pid].thumb_src = thumb
        if len(items) >= 20:
            break
    return items


def tokyofigure_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if " / " in title:
        it.name = clean(title.split(" / ", 1)[1]) or it.name
    raw_pairs = label_pairs(soup)
    specs = {k.rstrip("：:"): v for k, v in raw_pairs.items()}
    it.maker = specs.get("メーカー") or specs.get("発売元") or it.maker
    it.series = specs.get("原作") or specs.get("作品名") or ""
    it.character = specs.get("キャラクター") or ""
    it.category = specs.get("商品カテゴリ") or ""
    it.preorder_start = parse_ymd(specs.get("予約開始", ""))
    it.preorder_end = parse_ymd(specs.get("予約終了", ""))
    jan = clean(specs.get("JANコード", ""))
    it.jan = jan if re.fullmatch(r"\d{8,14}", jan) else ""
    it.release = month_of(specs.get("発売時期") or specs.get("発売日") or "") or it.release
    it.sculptor = specs.get("原型") or specs.get("原型制作") or ""
    it.painter = specs.get("彩色") or specs.get("彩色制作") or ""
    it.material = specs.get("素材") or specs.get("材質") or ""
    it.size = specs.get("サイズ") or ""
    scale_src = specs.get("スケール") or it.name
    scale = re.search(r"1/\d+", scale_src)
    it.scale = scale.group(0) if scale else ("ノンスケール" if "ノンスケール" in scale_src else "")
    for sel in (".price", ".ec-price__price", "[class*='price']"):
        for el in soup.select(sel):
            txt = clean(el.get_text(" "))
            if "円" in txt:
                p = price_digits(txt)
                if p:
                    it.price, it.currency = p, "JPY"
                    break
        if it.price:
            break
    text = clean(soup.get_text(" "))
    if not it.price:
        m = re.search(r"税込価格[^\d]{0,20}([\d,]+)\s*円", text)
        if m:
            it.price, it.currency = price_digits(m.group(1)), "JPY"
    tags = []
    if any(w in text for w in ("予約注文する", "予約受付中", "予約する")):
        tags.append("预订中")
    elif any(w in text for w in ("予約終了", "販売終了", "SOLD OUT", "在庫切れ")):
        tags.append("预订已满")
    if "再販" in it.name:
        tags.append("再贩")
    if "限定" in it.name or "限定版" in text[:1500]:
        tags.append("官网限定")
    if "特典" in it.name or specs.get("特典"):
        tags.append("附特典")
        it.bonus = specs.get("特典", "")
    it.status = " · ".join(dict.fromkeys(tags))
    if has_adult_hint(text[:2500]):
        it.adult = "1"
    it.image_url = og_image(soup, TF_BASE)
    if not it.image_url:
        img = soup.select_one("img[src*='save_image']")
        it.image_url = norm_img_url(img.get("src", "") if img else "", TF_BASE) or norm_img_url(it.thumb_src, TF_BASE)


# --------------------------------------------------------------------------
# MegaHobby (www.megahobby.jp): MegaHouse official figure catalogue.

MH_BASE = "https://www.megahobby.jp/"
MH_LIST = "https://www.megahobby.jp/products/"


def megahobby_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.select("a[href*='/products/item/']"):
        m = re.fullmatch(r"/products/item/(\d+)/?", a.get("href", ""))
        if not m or m.group(1) in seen:
            continue
        pid = m.group(1)
        seen.add(pid)
        txt = clean(a.get_text(" "))
        name = re.sub(r"\s*(?:発売日|希望小売価格|価格)[：:].*$", "", txt).strip()
        rel = re.search(r"発売日[：:]\s*([^\s希望]+)", txt)
        pr = re.search(r"価格[：:]\s*([\d,]+)\s*円", txt)
        img = a.find("img")
        thumb = norm_img_url(img.get("src", "") if img else "", MH_BASE)
        items.append(Item(
            source="MegaHobby", id=pid, url=f"https://www.megahobby.jp/products/item/{pid}/",
            name=name, maker="MegaHouse",
            price=price_digits(pr.group(1)) if pr else "", currency="JPY" if pr else "",
            release=month_of(rel.group(1)) if rel else "",
            thumb_src=thumb,
        ))
        if len(items) >= 15:
            break
    return items


def megahobby_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    crumbs = [clean(a.get_text()) for a in soup.select(".breadcrumb a, #breadcrumb a, .pankuzu a") if clean(a.get_text())]
    if len(crumbs) >= 2:
        it.category = " · ".join(c for c in crumbs[1:] if c not in ("トップページ", "商品情報"))
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if "｜" in title and not it.name:
        it.name = clean(title.split("｜", 1)[0])
    for dl in soup.select("dl")[:6]:
        t = clean(dl.get_text(" "))
        if not it.price and "円" in t:
            m = re.search(r"([\d,]+)\s*円", t)
            if m:
                it.price, it.currency = price_digits(m.group(1)), "JPY"
        if not it.release and re.search(r"20\d{2}年\d{1,2}月", t):
            it.release = month_of(t)
    text = clean(soup.get_text(" "))
    size = re.search(r"(?:全高|全長|サイズ)[：:]?\s*約?\s*[\d.]+\s*(?:mm|cm)", text)
    if size:
        it.size = size.group(0)
    scale = re.search(r"(?<![\d/])1/\d+(?!\d)", it.name + " " + text[:1500])
    if scale:
        it.scale = scale.group(0)
    series = re.search(r"『([^』]{2,40})』", text[:2000])
    if series:
        it.series = clean(series.group(1))
    tags = []
    if re.search(r"予約", text[:3000]):
        tags.append("预订中")
    if "再販" in it.name:
        tags.append("再贩")
    if "限定" in it.name:
        tags.append("官网限定")
    if "特典" in it.name or "付き】" in it.name:
        tags.append("附特典")
    it.status = " · ".join(dict.fromkeys(tags))
    it.image_url = og_image(soup, MH_BASE) or norm_img_url(it.thumb_src, MH_BASE)


# --------------------------------------------------------------------------
# KDcolle (kdcolle.kadokawa.co.jp): KADOKAWA Collection official figures.

KDCOLLE_BASE = "https://kdcolle.kadokawa.co.jp/"
KDCOLLE_LIST = "https://kdcolle.kadokawa.co.jp/product/"


def kdcolle_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.select("a[href]"):
        href = urljoin(KDCOLLE_BASE, a["href"])
        m = re.search(r"/product/([^/]+)/([^/]+)\.html$", href)
        if not m or "/tag/" in href or "/page/" in href:
            continue
        pid = re.sub(r"^entry-", "", m.group(2))
        if pid in seen:
            continue
        seen.add(pid)
        box = a.find_parent(["li", "article", "div"]) or a
        img = a.find("img") or box.find("img")
        thumb = norm_img_url(img.get("src", "") if img else "", KDCOLLE_BASE)
        txt = clean(a.get_text(" "))
        name = re.sub(r"\s*20\d{2}年\d{1,2}月発売.*$", "", txt).strip()
        items.append(Item(
            source="KDcolle", id=pid, url=href,
            name=name, maker="KADOKAWA", thumb_src=thumb,
        ))
        if len(items) >= 20:
            break
    return items


def kdcolle_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    specs = label_pairs(soup)
    it.name = specs.get("商品名") or it.name
    it.series = specs.get("作品名") or ""
    it.category = specs.get("カテゴリー") or ""
    it.maker = specs.get("発売元") or specs.get("企画・制作") or "KADOKAWA"
    price_text = specs.get("価格（税込）") or specs.get("価格") or specs.get("価格（税別）") or ""
    it.price = price_digits(price_text)
    if it.price:
        it.currency = "JPY"
    it.release = month_of(specs.get("発売時期") or specs.get("発売日") or "") or it.release
    spec = specs.get("仕様") or ""
    size = re.search(r"(?:全高|全長|高さ|サイズ)[：:]?\s*約?\s*[\d.]+\s*(?:mm|cm)", spec)
    it.size = size.group(0) if size else ""
    scale = re.search(r"1/\d+", spec + " " + it.name)
    it.scale = scale.group(0) if scale else ("ノンスケール" if "ノンスケール" in spec + it.category else "")
    it.sculptor = specs.get("原型制作") or specs.get("原型") or ""
    it.painter = specs.get("彩色") or specs.get("彩色制作") or ""
    jan = clean(specs.get("JANコード", ""))
    if re.fullmatch(r"\d{8,14}", jan):
        it.jan = jan
    text = clean(soup.get_text(" "))
    window = re.search(r"予約期間\s*(20\d{2}年\d{1,2}月\d{1,2}日)[^～〜\-]{0,20}[～〜\-]\s*(20\d{2}年\d{1,2}月\d{1,2}日)", text)
    if window:
        it.preorder_start = parse_ymd(window.group(1))
        it.preorder_end = parse_ymd(window.group(2))
    bonus = re.search(r"予約特典[：:]\s*([^・※\n]{2,60})", text)
    if bonus:
        it.bonus = clean(bonus.group(1))
    tags = ["预订中"]
    if "再販" in it.name:
        tags.append("再贩")
    if "限定" in it.name or "スペシャルセット" in it.name or "限定販売" in text[:3000]:
        tags.append("官网限定")
    if it.bonus or "特典" in it.name:
        tags.append("附特典")
    it.status = " · ".join(dict.fromkeys(tags))
    if spec:
        it.extra = f"仕様：{spec}"
    it.image_url = og_image(soup, KDCOLLE_BASE) or norm_img_url(it.thumb_src, KDCOLLE_BASE)


# --------------------------------------------------------------------------
# Hobby-Genki (hobby-genki.com): Japanese export shop on PrestaShop.

GENKI_BASE = "https://hobby-genki.com/"
GENKI_LIST = "https://hobby-genki.com/en/12-figures"


def hobbygenki_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for card in soup.select("article.product-miniature"):
        pid = clean(card.get("data-id-product", ""))
        link = card.select_one("h2 a[href], h3 a[href], .product-title a[href], a.product-thumbnail[href]")
        if not link:
            continue
        href = urljoin(GENKI_BASE, link["href"])
        if not pid:
            m = re.search(r"/(\d+)-[^/]+\.html", href)
            pid = m.group(1) if m else ""
        if not pid or pid in seen:
            continue
        seen.add(pid)
        title_el = card.select_one("h2, h3, .product-title")
        name = clean(title_el.get_text(" ")) if title_el else clean(link.get_text(" "))
        img = card.find("img")
        thumb = norm_img_url((img.get("data-full-size-image-url") or img.get("src") or "") if img else "", GENKI_BASE)
        price_el = card.select_one(".price")
        price = price_digits(price_el.get_text()) if price_el else ""
        items.append(Item(
            source="HobbyGenki", id=pid, url=href, name=name,
            price=price, currency="JPY" if price else "",
            thumb_src=thumb,
        ))
        if len(items) >= 18:
            break
    return items


def hobbygenki_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    pd = soup.select_one("#product-details")
    data = {}
    if pd and pd.get("data-product"):
        try:
            data = json.loads(pd["data-product"])
        except ValueError:
            data = {}
    if data:
        it.name = clean(data.get("name")) or it.name
        amt = data.get("price_amount")
        if isinstance(amt, (int, float)) and amt > 0:
            it.price = str(round(amt))
            cur = soup.select_one("meta[itemprop='priceCurrency']")
            it.currency = currency_of(cur["content"] if cur and cur.get("content") else "", str(data.get("price", ""))) or "JPY"
        for code_key in ("reference", "ean13"):
            code = clean(str(data.get(code_key) or ""))
            if re.fullmatch(r"\d{8,14}", code):
                it.jan = code
                break
        avail_date = clean(str(data.get("available_date") or ""))
        if re.match(r"20\d{2}-\d{2}", avail_date):
            it.release = avail_date[:7]
        cat_name = clean(str(data.get("category_name") or ""))
        maker_name = clean(str(data.get("manufacturer_name") or ""))
        if maker_name:
            it.maker = maker_name
        elif cat_name and cat_name.lower() not in ("hobby", "figures", "scale figures", "pre-orders"):
            it.maker = cat_name
        cover = data.get("cover") or {}
        if isinstance(cover, dict):
            large = (cover.get("large") or {}).get("url") or (cover.get("bySize", {}).get("large_default") or {}).get("url")
            if large:
                it.image_url = norm_img_url(large, GENKI_BASE)
    if not it.image_url:
        it.image_url = og_image(soup, GENKI_BASE) or norm_img_url(it.thumb_src, GENKI_BASE)
    desc = soup.select_one("#description, .product-description")
    dtxt = clean(desc.get_text(" ")) if desc else ""
    if not it.maker:
        m = re.search(r"\[([A-Za-z0-9 &.'+-]{2,40})\]", dtxt[:220])
        if m:
            it.maker = clean(m.group(1))
    scale = re.search(r"(?<![\d/])1/\d+(?!\d)", it.name)
    if scale:
        it.scale = scale.group(0)
    tags = []
    if it.release and it.release >= datetime.now(BEIJING).strftime("%Y-%m"):
        tags.append("预订中")
    elif "pre-order" in (dtxt[:400] + " " + clean(soup.get_text(" ")[:2000])).lower():
        tags.append("预订中")
    else:
        tags.append("现货")
    if re.search(r"re-?issue|re-?release|re-?run", it.name, re.I):
        tags.append("再贩")
    if re.search(r"limited|exclusive", it.name, re.I):
        tags.append("官网限定")
    if re.search(r"bonus", it.name, re.I):
        tags.append("附特典")
    it.status = " · ".join(dict.fromkeys(tags))
    if has_adult_hint(it.name + " " + dtxt[:400]):
        it.adult = "1"


# --------------------------------------------------------------------------
# Nin-Nin-Game (www.nin-nin-game.com): Japanese export shop on PrestaShop.
# Note: robots.txt disallows ?p= and ?order= query parameters, so only clean
# category URLs are fetched.

NIN_BASE = "https://www.nin-nin-game.com/"
NIN_LIST = "https://www.nin-nin-game.com/en/figures"


def ninningame_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for card in soup.select(".product-container, .ajax_block_product, li.ajax_block_product"):
        link = card.select_one("a.product-name[href], a[title][href$='.html']")
        if not link:
            continue
        href = urljoin(NIN_BASE, link["href"])
        m = re.search(r"/(\d+)-[^/]+\.html$", href)
        if not m or m.group(1) in seen:
            continue
        name = clean(link.get("title") or link.get_text(" "))
        if re.search(r"2nd\s*hand|used|pre-?owned", name, re.I):
            continue
        seen.add(m.group(1))
        img = card.find("img")
        thumb = norm_img_url((img.get("data-original") or img.get("data-src") or img.get("src") or "") if img else "", NIN_BASE)
        items.append(Item(
            source="NinNinGame", id=m.group(1), url=href, name=name, thumb_src=thumb,
        ))
        if len(items) >= 20:
            break
    return items


def ninningame_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    prod = ld_product(soup)
    if prod:
        it.name = clean(prod.get("name")) or it.name
        it.maker = name_of(prod.get("brand")) or it.maker
        code = clean(str(prod.get("gtin13") or prod.get("gtin") or prod.get("sku") or ""))
        if re.fullmatch(r"\d{8,14}", code):
            it.jan = code
        image = first(prod.get("image"))
        if isinstance(image, str):
            it.image_url = norm_img_url(image, NIN_BASE)
        offer = first(prod.get("offers")) or {}
        raw_price = re.search(r"\d+(?:\.\d+)?", str(offer.get("price") or "").replace(",", ""))
        if raw_price:
            it.price = str(round(float(raw_price.group(0))))
            it.currency = currency_of(offer.get("priceCurrency")) or "USD"
    if not it.image_url:
        it.image_url = og_image(soup, NIN_BASE) or norm_img_url(it.thumb_src, NIN_BASE)
    text = clean(soup.get_text(" "))
    rel = re.search(r"Release Date\s*[:：]\s*(20\d{2}[/-]\d{1,2}(?:[/-]\d{1,2})?|[A-Za-z]{3,9}\.?\s+20\d{2})", text, re.I)
    if rel:
        it.release = month_of(rel.group(1))
    series = re.search(
        r"Series\s*[:：]\s*([^|•\n]{2,60}?)(?:\s+(?:Character|Manufacturer|Japanese Original|Brand New|Release Date|Category|Details)\b|$)",
        text,
    )
    if series:
        it.series = clean(series.group(1))
    char = re.search(
        r"Character\s*[:：]\s*([^|•\n]{2,60}?)(?:\s+(?:Manufacturer|Series|Japanese Original|Brand New|Release Date|Category|Details)\b|$)",
        text,
    )
    if char:
        it.character = clean(char.group(1))
    if not it.maker:
        mk = re.search(r"\[([A-Za-z0-9 &.'+-]{2,40})\]\s*$", it.name)
        if mk:
            it.maker = clean(mk.group(1))
    scale = re.search(r"(?<![\d/])1/\d+(?!\d)", it.name)
    if scale:
        it.scale = scale.group(0)
    tags = []
    if re.search(r"PRE-?ORDER", text[:3000], re.I):
        tags.append("预订中")
    else:
        tags.append("现货")
    if re.search(r"re-?issue|re-?release|re-?run", it.name, re.I):
        tags.append("再贩")
    if re.search(r"limited|exclusive", it.name + " " + text[:2500], re.I):
        tags.append("官网限定")
    if re.search(r"bonus", it.name, re.I):
        tags.append("附特典")
    it.status = " · ".join(dict.fromkeys(tags))
    if has_adult_hint(it.name + " " + text[:2000]):
        it.adult = "1"


# --------------------------------------------------------------------------
# Figuya (figuya.com): European specialist anime figure shop.

FIGUYA_BASE = "https://figuya.com/"
FIGUYA_LIST = "https://figuya.com/en/figures"


def figuya_list(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    for a in soup.select("a[href*='/en/products/']"):
        img = a.find("img")
        if not img:
            continue
        href = urljoin(FIGUYA_BASE, a["href"].split("?", 1)[0])
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        thumb = norm_img_url(img.get("src") or img.get("data-src") or "", FIGUYA_BASE)
        items.append(Item(
            source="Figuya", id=slug, url=href,
            name=clean(img.get("alt") or a.get_text(" ")),
            thumb_src=thumb,
        ))
        if len(items) >= 16:
            break
    return items


def figuya_detail(html: str, it: Item) -> None:
    soup = BeautifulSoup(html, "html.parser")
    prod = ld_product(soup)
    specs = label_pairs(soup)
    if prod:
        it.name = clean(prod.get("name")) or it.name
        it.maker = name_of(prod.get("brand")) or it.maker
        code = clean(str(prod.get("gtin13") or prod.get("gtin") or ""))
        if re.fullmatch(r"\d{8,14}", code):
            it.jan = code
        image = first(prod.get("image"))
        if isinstance(image, str):
            it.image_url = norm_img_url(image, FIGUYA_BASE)
        offer = first(prod.get("offers")) or {}
        raw_price = re.search(r"\d+(?:\.\d+)?", str(offer.get("price") or "").replace(",", ""))
        if raw_price:
            it.price = str(round(float(raw_price.group(0))))
            it.currency = currency_of(offer.get("priceCurrency")) or "EUR"
        avail = str(offer.get("availability") or "")
        tags = ["预订中"] if "PreOrder" in avail else ["现货"] if "InStock" in avail else []
    else:
        tags = []
    if not it.image_url:
        it.image_url = og_image(soup, FIGUYA_BASE) or norm_img_url(it.thumb_src, FIGUYA_BASE)
    it.maker = specs.get("Manufacturer") or it.maker
    part_of = specs.get("Part of") or ""
    if part_of.lower() not in ("anime & manga", "gaming", "film & tv", "movies & tv", "comics", "other"):
        it.series = part_of
    raw_shows = re.sub(r"\s+\d+\s+to order.*$", "", specs.get("Shows") or "").strip()
    m_show = re.match(r"(.+?)\s*\(([^)]+)\)\s*$", raw_shows)
    if m_show:
        it.character = clean(m_show.group(1))
        if not it.series:
            it.series = clean(m_show.group(2))
    else:
        it.character = raw_shows
    if not it.jan and re.fullmatch(r"\d{8,14}", specs.get("EAN", "")):
        it.jan = specs["EAN"]
    rel_raw = specs.get("Release date") or ""
    it.release = "" if rel_raw.strip().lower() == "released" else (month_of(rel_raw) or it.release)
    it.size = specs.get("Size") or ""
    it.material = specs.get("Material") or ""
    it.sculptor = specs.get("Sculpted by") or ""
    scale_src = specs.get("Scale") or it.name
    scale = re.search(r"1/\d+", scale_src)
    it.scale = scale.group(0) if scale else ("无比例" if "non-scale" in scale_src.lower() else "")
    if re.search(r"re-?release|re-?issue|re-?run", it.name + " " + it.url, re.I):
        tags.append("再贩")
    if re.search(r"limited|exclusive", it.name, re.I):
        tags.append("官网限定")
    if re.search(r"bonus", it.name, re.I):
        tags.append("附特典")
    it.status = " · ".join(dict.fromkeys(tags))
    if has_adult_hint(it.name + " " + specs.get("Category", "") + " " + specs.get("Age rating", "")):
        it.adult = "1"


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
    details: bool = True  # read each new item's own page; off where the listing has to be enough


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
    Source("Native", NATIVE_FEEDS, native_list, native_detail, newest_first=True),
    Source("MegaHobby", [[MH_LIST]], megahobby_list, megahobby_detail, newest_first=True),
    Source("KDcolle", [[KDCOLLE_LIST]], kdcolle_list, kdcolle_detail, newest_first=True),
    Source("TokyoFigure", [[TF_LIST]], tokyofigure_list, tokyofigure_detail, newest_first=True),
    Source("HLJ", [[HLJ_SEARCH.format(page=p) for p in (1, 2, 3, 4)]], hlj_list, hlj_detail,
           newest_first=False, total_of=hlj_total),
    Source("Sofmap", [[SOFMAP_LIST.format(page=p) for p in (1, 2)]], sofmap_list, sofmap_detail, newest_first=True,
           details=False),
    Source("HobbyGenki", [[GENKI_LIST]], hobbygenki_list, hobbygenki_detail, newest_first=True),
    Source("NinNinGame", [[NIN_LIST]], ninningame_list, ninningame_detail, newest_first=True),
    Source("Solaris", [[SOLARIS_LIST.format(page=p) for p in (1, 2)]], solaris_list, solaris_detail, newest_first=True),
    Source("Figuya", [[FIGUYA_LIST]], figuya_list, figuya_detail, newest_first=True),
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


def thumb_file(source: str, item_id: str) -> Path:
    return THUMB_DIR / source / (re.sub(r"[^A-Za-z0-9_-]", "_", item_id) + ".webp")


def save_thumb(fetcher: Fetcher, it: Item) -> None:
    """Save the item's picture, scaled to fit THUMB_BOX, as thumbs/<source>/<id>.webp.

    The product page's own picture comes first: it is larger and usually shows
    the whole figure. The listing's small picture is the fallback.
    """
    dest = thumb_file(it.source, it.id)
    if not dest.exists():
        parts = urlparse(it.url)
        referer = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else None
        urls = []
        for raw in (it.image_url, it.thumb_src):
            u = norm_img_url(raw)
            if not u:
                continue
            cdn = solaris_cdn_url(u)
            if cdn:
                urls.append(cdn)
            urls.append(u)
        for url in dict.fromkeys(urls):
            try:
                img = Image.open(io.BytesIO(fetcher.image(url, referer=referer)))
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    rgba = img.convert("RGBA")
                    img = Image.new("RGB", rgba.size, "white")
                    img.paste(rgba, mask=rgba.getchannel("A"))
                img = img.convert("RGB")
                img.thumbnail(THUMB_BOX, Image.Resampling.LANCZOS)
                dest.parent.mkdir(parents=True, exist_ok=True)
                img.save(dest, "WEBP", quality=THUMB_QUALITY, method=6)
                if not it.image_url:
                    it.image_url = url
                break
            except Exception as exc:  # a missing thumbnail is not worth failing the item over
                log(f"   thumbnail {it.source} {it.id}: {type(exc).__name__}: {exc}")
        else:
            return
    it.thumb = dest.relative_to(ROOT).as_posix()


def upgrade_thumbs(fetcher: Fetcher, rows: list[dict[str, str]], today: date) -> set[str]:
    """Give recent rows the larger picture.

    Rows with no picture come first (earlier versions saved none for 观感成熟
    entries), then the small JPEGs earlier versions saved are replaced. Newest
    items first, THUMB_UPGRADES per run. Returns the days whose daily page now
    points at a different file.
    """
    recent = [r for r in sorted(rows, key=lambda r: r["first_seen"], reverse=True)
              if r["image_url"] and 0 <= age_days(r, today) <= THUMB_UPGRADE_DAYS]
    days: set[str] = set()
    left = THUMB_UPGRADES
    for group in ([r for r in recent if not r["thumb"]], [r for r in recent if r["thumb"].endswith(".jpg")]):
        misses = 0
        for row in group[:left]:
            left -= 1
            it = Item(**{k: row.get(k, "") for k in CSV_FIELDS})
            it.thumb = ""
            save_thumb(fetcher, it)
            if it.thumb:
                if row["thumb"]:
                    (ROOT / row["thumb"]).unlink(missing_ok=True)
                row["thumb"] = it.thumb
                days.add(row["first_seen"])
                misses = 0
            else:
                misses += 1
                if misses >= 5:  # the site is refusing or down; the next run tries again
                    log("   stopped after 5 pictures in a row could not be fetched")
                    break
    return days


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
            if src.details:
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
        if not it.image_url and it.thumb_src:
            it.image_url = norm_img_url(it.thumb_src)
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


def stats_due(row: dict[str, str], today: date) -> bool:
    """Whether an HPOI item's popularity should be read again today.

    The counts are read when the item is first seen and again 2 and 7 days later. If no run
    happened on one of those days, the next run takes the reading instead; a second run on the
    same day does not take it twice.
    """
    age = age_days(row, today)
    reached = [a for a in STATS_AGES if a <= age]
    if not reached or age > STATS_AGES[-1] + STATS_CATCH_UP:
        return False
    try:
        read_at = (date.fromisoformat(row["stats_date"]) - date.fromisoformat(row["first_seen"])).days
    except ValueError:
        read_at = 0
    return read_at < reached[-1]


def refresh_rows(fetcher: Fetcher, rows: list[dict[str, str]], pick: Callable[[dict[str, str]], bool],
                 today: date) -> int:
    """Read the detail page of each picked row again and keep what it now says."""
    done = 0
    refused: set[str] = set()  # a source that says no is left alone for the rest of the run
    for row in rows:
        if row["source"] in refused or not pick(row):
            continue
        it = Item(**{k: row.get(k, "") for k in CSV_FIELDS})
        try:
            SOURCE_BY_KEY[row["source"]].parse_detail(fetcher.text(it.url), it)
        except Blocked as exc:
            log(f"   refresh of {row['source']} stopped: {exc}")
            refused.add(row["source"])
            continue
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


CURRENCY_LABEL = {"JPY": "日元", "CNY": "元", "USD": "美元", "EUR": "欧元"}


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
        fresh = [r for r in items if r["status"] != BACKFILL]
        old = [r for r in items if r["status"] == BACKFILL]
        out += [f"## {SOURCES[key]}（{len(items)}）", ""]
        if fresh:
            out += table(fresh) + [""]
        if old:
            out += [f"<details><summary>{BACKFILL} {len(old)} 件：HPOI 新收录、但早已发售的老商品</summary>", "",
                    *table(old), "", "</details>", ""]
    return "\n".join(out)


def render_readme(rows: list[dict[str, str]], last_run: dict) -> str:
    counts = Counter(r["first_seen"] for r in rows if r["first_seen"])
    days = sorted(counts, reverse=True)
    source_names = "、".join(SOURCES.values())
    out = ["# figure-news 手办新品日报", "",
           f"每天清晨（北京时间）自动抓一次 {source_names} 共 {len(SOURCES)} 个网站的新品和商品图片，按天汇总成表。", ""]
    if days:
        out += [f"**最新一期：[{days[0]}](daily/{days[0]}.md)（{counts[days[0]]} 件）**", ""]
    out += [f"**公开网页：[{PAGES_URL}]({PAGES_URL})**（GitHub Pages，每天抓取后自动更新，最近 30 天）", "",
            "网页把同一件商品在几个来源的记录合成一张卡片，按类型分组，可以搜索、筛选、收藏，"
            "还有一个按截订时间排的「预订」视图。", ""]
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
        "- `thumbs/`：商品图，缩到 400×560 以内的 WebP",
        "- `scrape.py`：抓取脚本；`.github/workflows/daily.yml`：每天定时运行",
        "- `build_site.py` 和 `page/`：生成网页版（输出到 build/，不进仓库）",
        "- `page/aliases.json`：作品、厂商在各个来源里的不同写法对照表，网页靠它统一成中文叫法，可以直接往里加",
        "", "## 字段", "",
        "| 字段 | 含义 |", "|---|---|",
        "| first_seen | 第一次抓到的日期 |",
        "| source / id / url | 来源站 / 站内编号 / 原页面 |",
        "| name / name_alt | 名称 / 日文原名（HPOI 提供） |",
        "| maker / series / character | 厂商 / 作品 / 角色 |",
        "| category / scale | 类型、产品线 / 比例 |",
        "| price / currency / price_note | 价格 / 币种（日元、美元、欧元、人民币）/ 备注（如 HPOI 的人民币参考价） |",
        "| release | 发售（出货）月份。Good Smile 官网只给再贩商品写，新品的月份来自其他同款来源 |",
        "| preorder_start / preorder_end | 预订开始 / 截止 |",
        "| status | 预订中、预订已满、现货、再贩、官网限定、旧品补录等 |",
        "| rating / adult | HPOI 的观感等级 / 成人向标记（1 = 是） |",
        "| size / material / sculptor / painter | 尺寸 / 材质 / 原型 / 涂装 |",
        "| bonus / progress / extra | 特典 / HPOI 进程（开订、出荷等最新动态）/ 其他信息（如 HLJ 取消截止日、Good Smile 再贩商品的发售记录） |",
        "| follows / wants / preorders / owned | HPOI 的关注 / 想买 / 预订 / 已入人数 |",
        "| score / score_count / views | HPOI 评分 / 评分人数 / 访问量 |",
        "| stats_date | 人气数据的读取日期（收录当天、第 2 天、第 7 天各读一次） |",
        "| jan | JAN 条码 |",
        "| image_url / thumb | 原图链接 / 缩略图路径 |",
        "", "## 运行时间", "",
        "定时任务在北京时间 2:23、4:17、6:07 各触发一次：哪一次先跑起来就由它抓取，"
        "后面的看到当天已经抓过就直接结束，所以每天只抓一遍；只有某个来源当次出错（不是被拒绝）时，后一次才会重抓。",
        "", "排三次是因为 GitHub 的定时任务不准时，忙的时候会晚几个小时"
        "（原来只排 7:37 一次，实际拖到过 11 点和 12 点多）。",
        "", "想马上抓一次：GitHub 仓库页 → Actions → daily → Run workflow。",
        "", "## 抓取规矩", "",
        "如实标明身份（`figure-news/0.1`），遵守各站 robots.txt，"
        "同一网站两次请求至少隔 3 秒（HPOI 按它的要求隔 5 秒；Sofmap 隔 15 秒，而且每次最多读 3 个商品页，"
        "其余只用它的列表），遇到人机验证直接跳过、不绕过，被拒绝（403、429）当次就停。",
        "", "试过但没有收录的：AmiAmi、HobbySearch、Hobby Stock（列表页）会弹人机验证；"
        "Animate、Joshin、Tokyo Otaku Mode 直接拒绝程序访问；电击 Hobby 的 robots.txt 不允许抓取；"
        "CDJapan 不允许抓搜索页和图片。",
        "", "## 成人向", "",
        "所有来源（包括 HPOI 观感成熟、Solaris Japan 标为 NSFW、Native 18+ 等）均照常收录并抓取商品封面图，同时标注「成人向」标签，"
        "网页的「筛选」里可以一键开关隐藏。HLJ 的成人向商品需要登录并通过年龄验证才可见，匿名访问不展示。",
        "", "_本文件由 scrape.py 自动生成，手动修改会在下次运行时被覆盖。_", "",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect new figures and thumbnails from 12 figure websites.")
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
    # Sofmap's listing has no release month. Its product page does, but only a few may be read per
    # run, so they go to rows no other source has dated yet (same JAN), newest first.
    dated = {r["jan"] for r in rows if r["jan"] and r["release"] and r["source"] != "Sofmap"}
    undated = [r for r in sorted(rows, key=lambda r: r["first_seen"], reverse=True)
               if r["source"] == "Sofmap" and not r["release"] and r["jan"] not in dated
               and 0 <= age_days(r, today) <= 30]
    sofmap_now = {r["id"] for r in undated[:SOFMAP_DETAILS]}

    def pick(row: dict[str, str]) -> bool:
        if row["source"] == "Sofmap":
            return row["id"] in sofmap_now
        if (row["source"], row["id"]) in just_added:
            return False
        if args.refresh_days:
            return 0 <= age_days(row, today) < args.refresh_days
        if not row["thumb"] and not row["image_url"] and 0 <= age_days(row, today) <= 30:
            return True
        if row["source"] == "GoodSmile":
            # A rerun's page says when it ships. Rows without that month are read again during their
            # first week: it fills in rows saved before the month was parsed, and late additions.
            return "再贩" in row["status"] and not row["release"] and 0 <= age_days(row, today) <= 7
        # Popularity grows after an announcement, so HPOI items are read again a few days on.
        return row["source"] == "HPOI" and row["status"] != BACKFILL and stats_due(row, today)

    log("== refresh")
    log(f"   {refresh_rows(fetcher, rows, pick, today)} items re-read")
    log("== thumbnails")
    redrawn = upgrade_thumbs(fetcher, rows, today)
    log(f"   larger pictures saved for items first seen on {len(redrawn)} days")
    save_rows(rows)
    last_run = {"time": now.strftime("%Y-%m-%d %H:%M"), "sources": status}
    LAST_RUN.write_text(json.dumps(last_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for day in sorted({r["first_seen"] for r in new_rows} | redrawn):
        DAILY_DIR.mkdir(exist_ok=True)
        (DAILY_DIR / f"{day}.md").write_text(render_day(day, [r for r in rows if r["first_seen"] == day]),
                                             encoding="utf-8")
    README.write_text(render_readme(rows, last_run), encoding="utf-8")
    return 0 if any(s["status"] == "ok" for s in status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
