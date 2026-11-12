(() => {
  "use strict";

  const DATA = JSON.parse(document.getElementById("figure-data").textContent);
  const TYPES = DATA.types || {};
  const TYPE_ORDER = Object.keys(TYPES);
  const SOURCES = {
    HPOI: { name: "HPOI", short: "HPOI" },
    GoodSmile: { name: "Good Smile 官网", short: "GSC" },
    HLJ: { name: "HLJ", short: "HLJ" },
    Sofmap: { name: "Sofmap", short: "Sofmap" },
    Solaris: { name: "Solaris Japan", short: "Solaris" },
  };
  const FLAGS = { pre: "可预订", re: "再贩", ltd: "官网限定", bonus: "附特典", mto: "受注生产", fav: "已收藏" };
  const QUICK_FLAGS = ["pre", "re", "ltd", "bonus"];
  const TAGS = { adult: "成人向", re: "再贩", ltd: "限定", bonus: "特典", mto: "受注", old: "旧品" };
  const UNIT = { JPY: "日元", USD: "美元", CNY: "元" };
  // Bands are in renminbi, the one figure every priced item has.
  const BANDS = [
    ["a", "¥200 以下", 0, 200], ["b", "¥200–500", 200, 500], ["c", "¥500–1000", 500, 1000],
    ["d", "¥1000–2000", 1000, 2000], ["e", "¥2000 以上", 2000, Infinity], ["none", "价格未定"],
  ];
  const SORTS = [
    ["auto", "默认排序"], ["hot", "人气最高"], ["price-desc", "价格从高到低"], ["price-asc", "价格从低到高"],
    ["release", "发售最早"], ["due", "截订最近"],
  ];
  const STEP = 60;
  const RATE = Number(DATA.rate) || 0.047;

  // ---- small helpers
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  // Japanese names get lang="ja" so kanji use Japanese letterforms rather than Chinese ones.
  const say = (tag, cls, text) => {
    const n = el(tag, cls, text);
    if (/[぀-ヿ]/.test(text || "")) n.lang = "ja";
    return n;
  };
  const fmt = (n) => Number(n).toLocaleString("en-US");
  const safeUrl = (u) => (/^https:\/\//i.test(u || "") ? u : null);
  const plainClick = (e) => e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(key);
        return raw == null ? fallback : JSON.parse(raw);
      } catch (e) {
        return fallback;
      }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private window: keep going without it */ }
    },
  };
  const ICONS = {
    heart: '<path d="M12 20.3s-7.4-4.3-7.4-10A4.2 4.2 0 0 1 12 7.7a4.2 4.2 0 0 1 7.4 2.6c0 5.7-7.4 10-7.4 10z"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    prev: '<path d="m14.5 6-6 6 6 6"/>',
    next: '<path d="m9.5 6 6 6-6 6"/>',
    out: '<path d="M8 16l8.5-8.5M9.5 7.5h7v7"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
    auto: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"/>',
  };
  const icon = (name) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name];
    return svg;
  };

  // ---- dates: everything is a Beijing calendar day
  const WEEK = "日一二三四五六";
  const bj = new Date(Date.now() + 8 * 3600e3);
  const TODAY = Date.UTC(bj.getUTCFullYear(), bj.getUTCMonth(), bj.getUTCDate());
  const THIS_MONTH = `${bj.getUTCFullYear()}-${String(bj.getUTCMonth() + 1).padStart(2, "0")}`;
  const LATE_HOUR = 9;
  const parseDay = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || "");
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) : NaN;
  };
  const daysTo = (s) => {
    const t = parseDay(s);
    return Number.isNaN(t) ? null : Math.round((t - TODAY) / 864e5);
  };
  const md = (s) => {
    const m = /^\d{4}-(\d{2})-(\d{2})/.exec(s || "");
    return m ? `${+m[1]}/${+m[2]}` : s || "";
  };
  const dayInfo = (s) => {
    const d = new Date(parseDay(s));
    const gap = daysTo(s);
    const wk = "周" + WEEK[d.getUTCDay()];
    return {
      md: `${d.getUTCMonth() + 1}/${d.getUTCDate()}`,
      near: gap === 0 ? "今天" : gap === -1 ? "昨天" : wk,
      long: `${d.getUTCMonth() + 1}月${d.getUTCDate()}日 ${wk}`,
    };
  };
  const leftText = (left) => (left < 0 ? "已截订" : left === 0 ? "今天截订" : left === 1 ? "明天截订" : `剩 ${left} 天`);

  // ---- items
  const heightText = (mm) => (mm ? `${Math.round(mm) / 10}cm` : "");
  const releaseLabel = (it) => {
    if (it.relt) return it.relt;
    const m = /^(\d{4})-(\d{2})$/.exec(it.rel || "");
    return m ? `${m[1]}.${m[2]}` : "";
  };
  const releaseText = (it) => {
    const label = releaseLabel(it);
    if (!label) return "发售未定";
    return it.rel && it.rel < THIS_MONTH ? `${label} 已发售` : `${label} 发售`;
  };
  const prep = (raw, idx) => {
    const it = { ...raw, idx, offers: raw.offers || [], keys: raw.keys || [raw.k] };
    const flags = new Set(raw.flags || []);
    const left = raw.pe ? daysTo(raw.pe) : null;
    const opens = raw.open ? daysTo(raw.open) : null;
    if (left != null && left < 0) flags.delete("pre");
    else if (opens != null && opens <= 0) flags.add("pre");
    if (raw.adult) flags.add("adult");
    it.flagSet = flags;
    it.left = left != null && left >= 0 ? left : null;
    it.opens = opens != null && opens >= 0 && !raw.pe ? opens : null;
    const hot = raw.hot || {};
    it.heat = (hot.w || 0) * 2 + (hot.f || 0) + (hot.p || 0) * 3 + (hot.o || 0) * 2;
    it.yuan = raw.cur === "CNY" ? raw.price : raw.cny == null ? null : raw.cny;
    it.srcs = it.offers.map((o) => o.s);
    it.hay = [raw.name, raw.alts, raw.ip, raw.series, raw.chr, raw.maker, raw.makers, raw.line, TYPES[raw.type],
      raw.scale, raw.jan, it.keys, raw.sculptor, raw.painter, raw.artist]
      .flat().filter(Boolean).join(" ").toLowerCase();
    return it;
  };
  const items = (DATA.items || []).map(prep);
  const days = DATA.days || [];
  const DAYSET = new Set(days.map((d) => d.day));
  const byKey = new Map();
  for (const it of items) for (const key of it.keys) byKey.set(key, it);

  // ---- favourites: kept in this browser, with enough of the item to list it after it leaves the page
  const favs = store.get("fn-favs", {}) || {};
  const isFav = (it) => it.keys.some((key) => Object.prototype.hasOwnProperty.call(favs, key));
  const snapshot = (it) => ({
    name: it.name, ip: it.ip, maker: it.maker, type: it.type, scale: it.scale, price: it.price, cur: it.cur,
    cny: it.cny, rel: it.rel, relt: it.relt, pe: it.pe, img: it.img, fit: it.fit, bg: it.bg, day: it.day,
    offers: it.offers.map((o) => ({ s: o.s, id: o.id, url: o.url })), at: Date.now(),
  });
  function favItems() {
    const seen = new Set();
    const out = [];
    for (const key of Object.keys(favs)) {
      const live = byKey.get(key);
      if (live) {
        if (!seen.has(live)) { seen.add(live); out.push(live); }
      } else {
        out.push({ ...prep({ ...favs[key], k: key, keys: [key] }, 1e6 + out.length), gone: true });
      }
    }
    return out;
  }
  function toggleFav(it) {
    const on = !isFav(it);
    for (const key of it.keys) delete favs[key];
    if (on) favs[it.k] = snapshot(it);
    store.set("fn-favs", favs);
    for (const b of document.querySelectorAll(".fav")) if (b.dataset.key === it.k) paintFav(b, on);
    toast(on ? "已收藏，可在「收藏」里查看" : "已取消收藏");
    renderTabs();
    if (state.tab === "fav" || state.flags.has("fav")) render();
  }
  const paintFav = (b, on) => {
    b.setAttribute("aria-pressed", String(on));
    b.setAttribute("aria-label", on ? "取消收藏" : "收藏");
  };
  function favButton(it) {
    const b = el("button", "fav");
    b.type = "button";
    b.dataset.key = it.k;
    b.append(icon("heart"));
    paintFav(b, isFav(it));
    b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); toggleFav(it); });
    return b;
  }

  // ---- state: every visit opens on the newest day with nothing filtered
  const state = {
    tab: "new",
    day: days.length ? days[0].day : "all",
    type: "all",
    flags: new Set(), src: new Set(), scale: new Set(), price: new Set(), maker: new Set(), ip: new Set(),
    q: "", words: [],
    sort: "auto",
    view: store.get("fn-view", "grid") === "list" ? "list" : "grid",
    adult: store.get("fn-adult", true) !== false,
    old: store.get("fn-old", false) === true,
  };
  const SETS = ["src", "scale", "price", "maker", "ip"];
  // Days already opened in this browser; the others get a dot in the day strip.
  const seenDays = new Set(store.get("fn-seen", []) || []);
  const firstVisit = !seenDays.size;
  const bandOf = (it) => {
    if (it.yuan == null) return "none";
    return (BANDS.find((b) => b.length > 2 && it.yuan >= b[2] && it.yuan < b[3]) || BANDS[4])[0];
  };
  const valueOf = { src: (it) => it.srcs, scale: (it) => [it.scale || "none"], price: (it) => [bandOf(it)],
    maker: (it) => [it.maker || ""], ip: (it) => [it.ip || ""] };
  const hasFlag = (it, f) => (f === "fav" ? isFav(it) : it.flagSet.has(f));

  function scope() {
    if (state.tab === "fav") return favItems();
    if (state.tab === "due") return items.filter((it) => it.left != null || it.opens != null);
    return items.filter((it) => (state.day === "all" ? DAYSET.has(it.day) : it.day === state.day));
  }
  // Adult and old-catalogue entries can be switched off; favourites always show what was saved.
  const shown = (it) => state.tab === "fav"
    || ((state.adult || !it.adult) && (state.old || state.tab !== "new" || !it.flagSet.has("old")));
  // `skip` leaves one group out, to count what choosing another value in that group would give.
  function match(it, skip) {
    if (skip !== "type" && state.type !== "all" && it.type !== state.type) return false;
    if (skip !== "flags") for (const f of state.flags) if (!hasFlag(it, f)) return false;
    for (const group of SETS) {
      if (skip === group || !state[group].size) continue;
      if (!valueOf[group](it).some((v) => state[group].has(v))) return false;
    }
    for (const word of state.words) if (!it.hay.includes(word)) return false;
    return true;
  }

  // ---- ordering and sections
  const typeRank = (it) => {
    const i = TYPE_ORDER.indexOf(it.type);
    return i < 0 ? 99 : i;
  };
  // Clearly popular entries first, then those with a picture, then what can be ordered now,
  // then the bigger releases.
  const byScore = (a, b) => (b.heat >= 10 ? b.heat : 0) - (a.heat >= 10 ? a.heat : 0)
    || Number(!!b.img) - Number(!!a.img)
    || Number(b.flagSet.has("pre")) - Number(a.flagSet.has("pre"))
    || (b.yuan == null ? -1 : b.yuan) - (a.yuan == null ? -1 : a.yuan)
    || a.idx - b.idx;
  const far = (v) => (v == null ? Infinity : v);
  const CMP = {
    hot: (a, b) => b.heat - a.heat || byScore(a, b),
    "price-desc": (a, b) => (b.yuan == null ? -1 : b.yuan) - (a.yuan == null ? -1 : a.yuan) || byScore(a, b),
    "price-asc": (a, b) => far(a.yuan) - far(b.yuan) || byScore(a, b),
    due: (a, b) => far(a.left) - far(b.left) || far(a.opens) - far(b.opens) || byScore(a, b),
  };

  function typeSections(list) {
    const plain = list.filter((it) => !it.flagSet.has("old"));
    const out = TYPE_ORDER.map((t) => ({ title: TYPES[t], items: plain.filter((it) => it.type === t).sort(byScore) }));
    out.push({ title: "其它", items: plain.filter((it) => !TYPES[it.type]).sort(byScore) });
    out.push({ title: "旧品补录", note: "HPOI 新建的条目，发售已超过三个月",
      items: list.filter((it) => it.flagSet.has("old")).sort(byScore) });
    return out.filter((sec) => sec.items.length);
  }
  function daySections(list) {
    const order = (a, b) => typeRank(a) - typeRank(b) || byScore(a, b);
    return days.map(({ day }) => ({ title: dayInfo(day).long, items: list.filter((it) => it.day === day).sort(order) }))
      .filter((sec) => sec.items.length);
  }
  function releaseSections(list) {
    const groups = new Map();
    for (const it of list) {
      const key = !it.rel ? "~" : it.rel < THIS_MONTH ? "past" : it.rel;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(it);
    }
    const title = (key) => (key === "~" ? "发售未定" : key === "past" ? "已发售" : `${+key.slice(0, 4)}年${+key.slice(5)}月`);
    // Months in order, then the undated, then what is already out. Plain string order: "~" sorts after digits.
    const rank = (key) => (key === "past" ? "~~" : key);
    const plain = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
    return [...groups.keys()].sort((a, b) => plain(rank(a), rank(b)))
      .map((key) => ({ title: title(key), items: groups.get(key).sort(key === "past" ? (a, b) => plain(b.rel, a.rel) || byScore(a, b) : byScore) }));
  }
  const DUE_BUCKETS = [
    ["今天截订", (n) => n === 0, "due"], ["明天截订", (n) => n === 1, "due"], ["本周内截订", (n) => n <= 7, "soon"],
    ["两周内截订", (n) => n <= 14], ["一个月内截订", (n) => n <= 31], ["一个月以后截订", () => true],
  ];
  function dueSections(list, withRest) {
    const out = DUE_BUCKETS.map(([title, , cls]) => ({ title, cls, items: [] }));
    const opening = { title: "即将开订", note: "来自 HPOI 的进程，开订后以各店铺为准", items: [] };
    const rest = { title: "没有截订日期", items: [] };
    for (const it of list) {
      if (it.left != null) out[DUE_BUCKETS.findIndex(([, test]) => test(it.left))].items.push(it);
      else if (it.opens != null) opening.items.push(it);
      else rest.items.push(it);
    }
    for (const sec of out) sec.items.sort(CMP.due);
    opening.items.sort(CMP.due);
    rest.items.sort(byScore);
    return [...out, opening, ...(withRest ? [rest] : [])].filter((sec) => sec.items.length);
  }
  function sections(list) {
    const sort = state.sort;
    if (sort === "release") return releaseSections(list);
    if (sort === "due" || (sort === "auto" && state.tab === "due")) return dueSections(list, state.tab !== "due");
    if (sort !== "auto") return [{ items: [...list].sort(CMP[sort]) }];
    if (state.tab === "fav") return [{ items: [...list].sort((a, b) => Number(!!a.gone) - Number(!!b.gone) || CMP.due(a, b)) }];
    return state.day === "all" ? daySections(list) : typeSections(list);
  }

  // ---- pieces of a card
  function shot(it, eager, small) {
    const box = el("div", "shot");
    if (it.img && /^thumbs\//.test(it.img)) {
      if (it.fit) box.classList.add("whole");
      // Behind a picture shown whole: its own background colour, or its two edge colours as a gradient.
      const tone = (v) => typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v);
      if (tone(it.bg)) box.style.setProperty("--frame", it.bg);
      else if (Array.isArray(it.bg) && tone(it.bg[0]) && tone(it.bg[1])) {
        box.style.setProperty("--frame", `linear-gradient(${it.bg[2] === "h" ? "90deg" : "180deg"}, ${it.bg[0]}, ${it.bg[1]})`);
      }
      const img = el("img");
      img.alt = "";
      img.decoding = "async";
      if (!eager) img.loading = "lazy";
      img.addEventListener("error", () => {
        box.className = "shot none";
        box.style.removeProperty("--frame");
        box.replaceChildren(el("span", null, "暂无图片"));
      }, { once: true });
      img.src = it.img;
      box.append(img);
    } else {
      box.classList.add("none");
      // Solaris Japan keeps its NSFW pictures behind a click, so none is saved for those.
      box.append(el("span", null, small ? "无图" : it.adult ? "图片见原站" : "暂无图片"));
    }
    return box;
  }
  // The small line above a name says what it is from: the series, else the product line, else the maker.
  const context = (it) => it.ip || (it.line && it.line[0]) || it.maker || TYPES[it.type] || "";
  const specText = (it) => [context(it) === it.maker ? "" : it.maker, it.scale, heightText(it.h)]
    .filter(Boolean).join(" · ") || TYPES[it.type] || "";
  const popText = (it) => {
    const hot = it.hot || {};
    if (!hot.f && !hot.w) return "";
    return (hot.w || 0) > (hot.f || 0) ? `想买 ${fmt(hot.w)}` : `关注 ${fmt(hot.f)}`;
  };
  function priceLine(o, via) {
    const p = el("p", "price");
    if (o.price == null) p.append(el("span", "na", "价格未定"));
    else if (o.cur === "CNY") p.append(el("b", null, `¥${fmt(o.price)}`));
    else {
      p.append(el("b", null, fmt(o.price)), el("span", "unit", UNIT[o.cur] || o.cur || ""));
      if (o.cny != null) p.append(el("span", "cny", `≈¥${fmt(o.cny)}`));
    }
    if (via) p.append(el("span", "via", via));
    return p;
  }
  const priceText = (it) => (it.price == null ? "价格未定" : it.cur === "CNY" ? `¥${fmt(it.price)}`
    : `${fmt(it.price)} ${UNIT[it.cur] || it.cur || ""}`);
  // The one line about ordering: a deadline if there is one, else when orders open, else the stage.
  function status(it) {
    if (it.pe && daysTo(it.pe) != null) {
      const left = daysTo(it.pe);
      if (left < 0) return { text: "已截订", cls: "mute" };
      if (left <= 1) return { text: leftText(left), cls: "due" };
      if (left <= 7) return { text: `剩 ${left} 天截订`, cls: left <= 3 ? "due" : "soon" };
      return { text: `${md(it.pe)} 截订`, cls: "" };
    }
    if (it.open && daysTo(it.open) != null) {
      const gap = daysTo(it.open);
      return { text: gap > 1 ? `${md(it.open)} 开订` : gap === 1 ? "明天开订" : gap === 0 ? "今天开订" : "已开订", cls: "ok" };
    }
    if (it.flagSet.has("pre")) return { text: "预订中", cls: "ok" };
    if (it.stage) return { text: it.stage, cls: "mute" };
    return null;
  }
  function tagRow(it) {
    const row = el("div", "tags");
    for (const key of Object.keys(TAGS)) if (it.flagSet.has(key)) row.append(el("span", "tag", TAGS[key]));
    return row.childElementCount ? row : null;
  }
  const itemLink = (it, cls) => {
    const a = el("a", cls);
    a.href = `#item=${encodeURIComponent(it.k)}`;
    a.addEventListener("click", (e) => {
      if (!plainClick(e)) return;
      e.preventDefault();
      openSheet(it, { push: true, from: a });
    });
    return a;
  };

  function tile(it) {
    const li = el("li", "card");
    const a = itemLink(it, "tile");
    const pic = shot(it);
    const tags = tagRow(it);
    if (tags) pic.append(tags);
    const info = el("div", "info");
    const spec = el("p", "spec");
    spec.append(say("span", null, specText(it)));
    if (popText(it)) spec.append(el("span", null, popText(it)));
    const when = el("p", "when");
    when.append(el("span", null, releaseText(it)));
    const st = status(it);
    if (st) when.append(el("span", `st ${st.cls}`, st.text));
    info.append(say("p", "ctx", context(it)), say("h3", "name", it.name), spec,
      priceLine(it, it.srcs.map((s) => (SOURCES[s] || {}).short || s).join(" · ")), when);
    a.append(pic, info);
    li.append(a, favButton(it));
    return li;
  }

  function row(it) {
    const li = el("li", "row");
    const a = itemLink(it, "row-hit");
    const st = status(it);
    const main = el("div", "row-main");
    const facts = el("p", "facts");
    facts.append(el(it.price == null ? "span" : "b", null, priceText(it)));
    if (it.cur !== "CNY" && it.cny != null) facts.append(el("span", null, `≈¥${fmt(it.cny)}`));
    facts.append(el("span", null, releaseText(it)));
    if (st) facts.append(el("span", `st ${st.cls}`, st.text));
    const tags = Object.keys(TAGS).filter((key) => it.flagSet.has(key)).map((key) => TAGS[key]);
    const ctx = say("p", "ctx", [it.ip || (it.line && it.line[0]) || TYPES[it.type], ...tags].filter(Boolean).join(" · "));
    const extra = [it.maker, it.scale, heightText(it.h)].filter(Boolean).join(" · ");
    if (extra) ctx.append(say("span", "narrow", ` · ${extra}`));
    main.append(ctx, say("h3", "name", it.name), facts);

    const cell = (cls, top, bottom) => {
      const c = el("div", `cell ${cls || ""}`);
      c.append(top instanceof Node ? top : say("span", null, top || "—"));
      if (bottom) c.append(bottom instanceof Node ? bottom : say("small", null, bottom));
      return c;
    };
    const stNode = st ? el("small", `st ${st.cls}`, st.text) : null;
    const hot = it.hot || {};
    a.append(shot(it, false, true), main,
      cell("", it.maker || "—", [it.scale, heightText(it.h)].filter(Boolean).join(" · ")),
      cell("num", el(it.price == null ? "small" : "b", null, priceText(it)), it.cur !== "CNY" && it.cny != null ? `≈¥${fmt(it.cny)}` : ""),
      cell("", releaseText(it), stNode),
      cell("num", hot.f ? `关注 ${fmt(hot.f)}` : "—", hot.w ? `想买 ${fmt(hot.w)}` : ""));
    li.append(a, favButton(it));
    return li;
  }

  // ---- the list, rendered a screenful at a time
  let queue = [];
  let cursor = 0;
  let host = null;
  function openSection(sec, asRows) {
    const box = el("section", "sec");
    if (sec.title) {
      const h = el("h2", `sec-h ${sec.cls || ""}`);
      h.append(el("span", null, sec.title), el("i", null, `${sec.items.length} 件`));
      if (sec.note) h.append(el("small", null, sec.note));
      box.append(h);
    }
    const ul = el("ul", asRows ? "rows" : "grid");
    if (asRows) {
      const head = el("li", "rows-head");
      head.setAttribute("aria-hidden", "true");
      for (const [label, cls] of [["", ""], ["商品", ""], ["厂商 · 规格", ""], ["价格", "num"], ["发售 · 预订", ""], ["HPOI 人气", "num"]]) {
        head.append(el("span", cls, label));
      }
      ul.append(head);
    }
    box.append(ul);
    $("list").append(box);
    return ul;
  }
  function more() {
    const end = Math.min(queue.length, cursor + STEP);
    for (; cursor < end; cursor++) {
      const { sec, it, first } = queue[cursor];
      const asRows = sec.rows || state.view === "list";
      if (first) host = openSection(sec, asRows);
      host.append(asRows ? row(it) : tile(it));
    }
  }
  const sentinel = $("sentinel");
  const pager = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    if (!entries.some((e) => e.isIntersecting) || cursor >= queue.length) return;
    more();
    pager.unobserve(sentinel);  // look again: the new cards may not have filled the screen
    pager.observe(sentinel);
  }, { rootMargin: "900px 0px" }) : null;
  function renderList(secs) {
    $("list").replaceChildren();
    queue = [];
    for (const sec of secs) sec.items.forEach((it, i) => queue.push({ sec, it, first: i === 0 }));
    cursor = 0;
    host = null;
    more();
    if (!pager) while (cursor < queue.length) more();
    else { pager.unobserve(sentinel); pager.observe(sentinel); }
  }

  // ---- header: tabs, days, headline, picks
  const TABS = [["new", "新品"], ["due", "预订"], ["fav", "收藏"]];
  function renderTabs() {
    const closing = items.filter((it) => it.left != null && it.left <= 7 && (state.adult || !it.adult));
    const counts = { due: closing.length, fav: favItems().length };
    const box = $("tabs");
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.tab : null;
    box.replaceChildren();
    for (const [key, label] of TABS) {
      const b = el("button", "tab", label);
      b.type = "button";
      b.dataset.tab = key;
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(state.tab === key));
      if (counts[key]) {
        const n = el("span", "n", String(counts[key]));
        if (key === "due") {
          n.title = "7 天内截订的件数";
          if (closing.some((it) => it.left <= 3)) n.classList.add("urgent");
        }
        b.append(n);
      }
      b.addEventListener("click", () => {
        if (state.tab === key) return;
        state.tab = key;
        state.sort = "auto";
        render();
      });
      box.append(b);
      if (focused === key) b.focus();
    }
  }
  function renderDays() {
    const box = $("days");
    box.hidden = state.tab !== "new" || !days.length;
    if (box.hidden) return;
    if (!box.childElementCount) {
      const top = Math.max(1, ...days.map((d) => d.count));
      const make = (value, small, big, count) => {
        const b = el("button", "day");
        b.type = "button";
        b.dataset.day = value;
        const bar = el("i");
        bar.style.setProperty("--w", `${Math.round((count / top) * 100)}%`);
        b.append(el("b", null, big), el("small", null, `${small} · ${fmt(count)} 件`));
        if (value !== "all") b.append(bar);
        b.addEventListener("click", () => { state.day = value; render(); });
        box.append(b);
      };
      if (days.length > 1) make("all", `近 ${days.length} 天`, "全部", days.reduce((n, d) => n + d.count, 0));
      for (const { day, count } of days) {
        const d = dayInfo(day);
        make(day, d.near, d.md, count);
      }
    }
    if (state.day !== "all" && !seenDays.has(state.day)) {
      seenDays.add(state.day);
      store.set("fn-seen", [...seenDays].sort().slice(-90));
    }
    for (const b of box.children) {
      const day = b.dataset.day;
      b.setAttribute("aria-pressed", String(day === state.day));
      const fresh = day !== "all" && !firstVisit && !seenDays.has(day);
      b.classList.toggle("unseen", fresh);
      b.title = fresh ? "这一天还没看过" : "";
    }
    fadeDays();
  }
  // The strip fades out at the right only while there is more of it to scroll to.
  function fadeDays() {
    const box = $("days");
    box.classList.toggle("more", box.scrollLeft + box.clientWidth < box.scrollWidth - 4);
  }
  // The line under the headline: a few counted facts, each kept on one line.
  function facts(parts) {
    const p = $("subline");
    p.replaceChildren();
    for (const part of parts.filter(Boolean)) {
      const span = el("span");
      if (typeof part === "string") span.textContent = part;
      else {
        const [label, value, tail] = part;
        if (label) span.append(`${label} `);
        span.append(el("b", null, value));
        if (tail) span.append(` ${tail}`);
      }
      p.append(span);
    }
  }
  function renderLede(pool) {
    const picks = $("picks");
    picks.replaceChildren();
    if (state.tab === "due") {
      $("headline").textContent = "预订日程";
      const week = pool.filter((it) => it.left != null && it.left <= 7).length;
      facts([["", fmt(week), "件 7 天内截订"], ["", fmt(pool.filter((it) => it.left != null).length), "件在预订期内"],
        pool.some((it) => it.opens != null) && ["", fmt(pool.filter((it) => it.opens != null).length), "件即将开订"]]);
    } else if (state.tab === "fav") {
      $("headline").textContent = "我的收藏";
      facts([["", fmt(pool.length), "件"], "只保存在这台设备的浏览器里"]);
    } else {
      const fresh = pool.filter((it) => !it.flagSet.has("old"));
      const priced = fresh.map((it) => it.yuan).filter((v) => v != null).sort((a, b) => a - b);
      $("headline").textContent = state.day === "all" ? `最近 ${days.length} 天` : dayInfo(state.day).long;
      facts([["收录", fmt(fresh.length), "件"],
        ["", fmt(fresh.filter((it) => it.flagSet.has("pre")).length), "件可预订"],
        fresh.some((it) => it.flagSet.has("re")) && ["", fmt(fresh.filter((it) => it.flagSet.has("re")).length), "件再贩"],
        priced.length >= 3 && ["价格中位约", `¥${fmt(priced[priced.length >> 1])}`]]);
      for (const pick of choosePicks(fresh)) picks.append(pick);
    }
    picks.hidden = !picks.childElementCount;
  }
  // Up to five things worth a look: the most followed, the priciest, the first to close,
  // and the series and maker with the most new items.
  function choosePicks(pool) {
    const out = [];
    const used = new Set();
    const addItem = (label, it, value) => {
      if (!it || used.has(it)) return;
      used.add(it);
      const a = itemLink(it, "pick");
      const body = el("span", "pick-text");
      body.append(el("span", "k", label), say("span", "t", it.name), el("span", "v", value));
      a.append(shot(it, false, true), body);
      out.push(a);
    };
    const addGroup = (label, group, key) => {
      const tally = new Map();
      for (const it of pool) if (it[key]) tally.set(it[key], (tally.get(it[key]) || 0) + 1);
      const best = [...tally.entries()].sort((a, b) => b[1] - a[1])[0];
      if (!best || best[1] < 3) return;
      const b = el("button", "pick plain");
      b.type = "button";
      const body = el("span", "pick-text");
      body.append(el("span", "k", label), say("span", "t", best[0]), el("span", "v", `${best[1]} 件，点一下只看这些`));
      b.append(body);
      b.addEventListener("click", () => { state[group].clear(); state[group].add(best[0]); render(); scrollToList(); });
      out.push(b);
    };
    const hottest = [...pool].sort(CMP.hot)[0];
    if (hottest && hottest.heat >= 5) {
      const hot = hottest.hot;
      addItem("HPOI 人气最高", hottest, [hot.f && `关注 ${fmt(hot.f)}`, hot.w && `想买 ${fmt(hot.w)}`].filter(Boolean).join(" · "));
    }
    const dearest = [...pool].filter((it) => it.yuan != null && !used.has(it)).sort(CMP["price-desc"])[0];
    if (dearest) addItem("价格最高", dearest, priceText(dearest) + (dearest.cur !== "CNY" && dearest.cny != null ? ` ≈¥${fmt(dearest.cny)}` : ""));
    const first = [...pool].filter((it) => it.left != null && !used.has(it)).sort(CMP.due)[0];
    if (first) addItem("最先截订", first, `${md(first.pe)} · ${leftText(first.left)}`);
    addGroup("新品最多的作品", "ip", "ip");
    addGroup("新品最多的厂商", "maker", "maker");
    return out;
  }

  // ---- filter chips, the filter panel, and what is switched on
  const count = (pool, group, test) => pool.reduce((n, it) => n + (match(it, group) && test(it) ? 1 : 0), 0);
  function chip(label, n, on, act) {
    const b = el("button", "chip", label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(on));
    if (n != null) b.append(el("span", "n", fmt(n)));
    if (!n && !on && n != null) b.disabled = true;
    b.addEventListener("click", act);
    return b;
  }
  function renderChips(pool) {
    const box = $("chips");
    const scrolled = box.scrollLeft;
    box.replaceChildren();
    box.append(chip("全部", count(pool, "type", () => true), state.type === "all", () => { state.type = "all"; render(); }));
    for (const t of TYPE_ORDER) {
      const n = count(pool, "type", (it) => it.type === t);
      if (!n && state.type !== t) continue;
      box.append(chip(TYPES[t], n, state.type === t, () => { state.type = state.type === t ? "all" : t; render(); }));
    }
    box.append(el("span", "rule"));
    for (const f of QUICK_FLAGS) {
      const n = count(pool, null, (it) => hasFlag(it, f));
      if (!n && !state.flags.has(f)) continue;
      box.append(chip(FLAGS[f], n, state.flags.has(f), () => toggle("flags", f)));
    }
    box.scrollLeft = scrolled;
  }
  function toggle(group, value) {
    if (state[group].has(value)) state[group].delete(value);
    else state[group].add(value);
    render();
  }
  const scaleRank = (s) => (s === "none" ? 1e6 : s === "无比例" ? 1e5 : Number((/1\/(\d+)/.exec(s) || [0, 9e4])[1]));
  const LABEL = {
    src: (v) => (SOURCES[v] || {}).name || v,
    scale: (v) => (v === "none" ? "未标比例" : v),
    price: (v) => (BANDS.find((b) => b[0] === v) || [0, v])[1],
    maker: (v) => v || "厂商未标",
    ip: (v) => v || "作品未标",
  };
  function options(pool, group, limit) {
    const tally = new Map();
    for (const it of pool) if (match(it, group)) for (const v of valueOf[group](it)) tally.set(v, (tally.get(v) || 0) + 1);
    for (const v of state[group]) if (!tally.has(v)) tally.set(v, 0);
    let list = [...tally.entries()];
    if (group === "src") list.sort((a, b) => Object.keys(SOURCES).indexOf(a[0]) - Object.keys(SOURCES).indexOf(b[0]));
    else if (group === "scale") list.sort((a, b) => scaleRank(a[0]) - scaleRank(b[0]));
    else if (group === "price") list.sort((a, b) => BANDS.findIndex((x) => x[0] === a[0]) - BANDS.findIndex((x) => x[0] === b[0]));
    else {
      list = list.filter(([v]) => v).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh"));
      if (limit) list = list.filter(([v], i) => i < limit || state[group].has(v));
    }
    return list;
  }
  function renderPanel() {
    const dlg = $("filters");
    if (!dlg.open) return;
    const pool = scope().filter(shown);
    const keep = dlg.querySelector(".panel-body");
    const scrollTop = keep ? keep.scrollTop : 0;
    const focusKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.f : null;

    const bar = el("div", "panel-bar");
    const title = el("h2", null, "筛选");
    title.id = "filters-title";
    const close = el("button", "icon-btn");
    close.type = "button";
    close.setAttribute("aria-label", "关闭");
    close.append(icon("close"));
    close.addEventListener("click", closeOverlay);
    bar.append(title, close);

    const body = el("div", "panel-body");
    const group = (heading, hint, nodes) => {
      if (!nodes.length) return;
      const box = el("div", "group");
      const h = el("h3", null, heading);
      if (hint) h.append(el("small", null, hint));
      const opts = el("div", "opts");
      opts.append(...nodes);
      box.append(h, opts);
      body.append(box);
    };
    const mark = (node, key) => { node.dataset.f = key; return node; };
    group("状态", "", ["pre", "re", "ltd", "bonus", "mto", "fav"].map((f) => {
      const n = count(pool, null, (it) => hasFlag(it, f));
      return n || state.flags.has(f) ? mark(chip(FLAGS[f], n, state.flags.has(f), () => toggle("flags", f)), `flags:${f}`) : null;
    }).filter(Boolean));
    const facet = (key, heading, hint, limit) => group(heading, hint, options(pool, key, limit).map(([v, n]) =>
      mark(chip(LABEL[key](v), n, state[key].has(v), () => toggle(key, v)), `${key}:${v}`)));
    facet("src", "来源", "");
    facet("scale", "比例", "");
    facet("price", "价格", "约合人民币");
    facet("maker", "厂商", "按件数取前 16 家", 16);
    facet("ip", "作品", "按件数取前 16 部", 16);

    const show = el("div", "group");
    show.append(el("h3", null, "显示"));
    const sw = (key, label, hint) => {
      const row = el("label", "switch");
      const text = el("span", null, label);
      text.append(el("small", null, hint));
      const input = el("input");
      input.type = "checkbox";
      input.id = `sw-${key}`;
      input.dataset.f = `sw:${key}`;
      input.checked = state[key];
      input.addEventListener("change", () => { state[key] = input.checked; store.set(`fn-${key}`, state[key]); render(); });
      row.append(text, input);
      show.append(row);
    };
    sw("adult", "成人向条目", "HPOI 标为「观感成熟」、Solaris Japan 标为 NSFW 的条目");
    sw("old", "旧品补录", "HPOI 新建、但早已发售的老商品");
    body.append(show);

    const foot = el("div", "panel-foot");
    const clear = el("button", "btn", "清除");
    clear.type = "button";
    clear.addEventListener("click", () => { resetFilters(); render(); });
    const done = el("button", "btn solid", `查看 ${fmt(pool.filter((it) => match(it)).length)} 件`);
    done.type = "button";
    done.addEventListener("click", closeOverlay);
    foot.append(clear, done);

    dlg.replaceChildren(bar, body, foot);
    body.scrollTop = scrollTop;
    if (focusKey) for (const n of dlg.querySelectorAll("[data-f]")) if (n.dataset.f === focusKey) n.focus();
  }
  function renderActive() {
    const box = $("active");
    box.replaceChildren();
    const add = (label, act) => {
      const b = el("button", "token", label);
      b.type = "button";
      b.setAttribute("aria-label", `去掉筛选：${label}`);
      b.append(icon("close"));
      b.addEventListener("click", act);
      box.append(b);
    };
    for (const f of state.flags) if (!QUICK_FLAGS.includes(f)) add(FLAGS[f], () => toggle("flags", f));
    for (const group of SETS) for (const v of state[group]) add(LABEL[group](v), () => toggle(group, v));
    const n = box.childElementCount;
    $("filter-count").hidden = !n;
    $("filter-count").textContent = String(n);
    if (n || state.type !== "all" || state.flags.size || state.words.length) {
      const clear = el("button", "link", "清除全部筛选");
      clear.type = "button";
      clear.addEventListener("click", () => { resetFilters(); render(); });
      box.append(clear);
    }
    box.hidden = !n;
  }
  function resetFilters() {
    state.type = "all";
    state.flags.clear();
    for (const group of SETS) state[group].clear();
    state.q = "";
    state.words = [];
    $("q").value = "";
  }

  // ---- the whole page
  function render() {
    const all = scope();
    const pool = all.filter(shown);
    const list = pool.filter((it) => match(it));
    renderTabs();
    renderDays();
    renderLede(pool);
    renderChips(pool);
    renderActive();
    renderPanel();

    const sort = $("sort");
    for (const o of sort.options) {
      if (o.value === "auto") o.textContent = state.tab === "new" ? (state.day === "all" ? "按日期" : "按类型") : "按截订时间";
      o.hidden = o.value === "due" && state.tab !== "new";
    }
    sort.value = state.sort;
    $("sorter").classList.toggle("on", state.sort !== "auto");
    $("view-grid").setAttribute("aria-pressed", String(state.view === "grid"));
    $("view-list").setAttribute("aria-pressed", String(state.view === "list"));

    renderList(sections(list));

    const empty = $("empty");
    empty.hidden = list.length > 0;
    if (!list.length) {
      const filtered = pool.length > 0;
      const [title, text] = filtered ? ["没有符合条件的商品", "换个关键词，或者清除筛选。"]
        : !items.length ? ["还没有数据", "第一次抓取完成后会出现在这里。"]
        : state.tab === "fav" ? ["还没有收藏", "点商品右上角的心形收藏，之后在这里看它们的价格和截订时间。收藏只保存在这台设备的浏览器里。"]
        : state.tab === "due" ? ["近期没有截订或开订的商品", "预订截止日来自 Good Smile 官网，开订日来自 HPOI 的进程。"]
        : ["这一天没有可显示的商品", "成人向和旧品补录可以在「筛选」里打开。"];
      $("empty-title").textContent = title;
      $("empty-text").textContent = text;
      $("reset").hidden = !filtered;
    }

    const note = $("hidden-note");
    const gone = all.filter((it) => !shown(it) && match(it));
    const adult = gone.filter((it) => it.adult).length;
    note.hidden = !gone.length;
    if (gone.length) {
      note.replaceChildren(`还有 ${[adult && `${adult} 件成人向`, gone.length - adult && `${gone.length - adult} 件旧品补录`]
        .filter(Boolean).join("、")}没有显示。`);
      const b = el("button", "link", "打开筛选");
      b.type = "button";
      b.addEventListener("click", openPanel);
      note.append(b);
    }
  }

  // ---- detail sheet
  let returnTo = null;
  const lock = (on) => document.documentElement.classList.toggle("locked", on);
  function kv(rows) {
    const dl = el("dl", "kv");
    for (const [label, value] of rows) {
      if (!value || (Array.isArray(value) && !value.length)) continue;
      const dd = el("dd");
      if (Array.isArray(value)) {
        const ul = el("ul");
        for (const v of value) ul.append(say("li", null, v));
        dd.append(ul);
      } else if (value instanceof Node) dd.append(value);
      else {
        dd.textContent = value;
        if (/[぀-ヿ]/.test(value)) dd.lang = "ja";
      }
      dl.append(el("dt", null, label), dd);
    }
    return dl.childElementCount ? dl : null;
  }
  function block(title, ...nodes) {
    const kept = nodes.filter(Boolean);
    if (!kept.length) return null;
    const box = el("section", "block");
    box.append(el("h3", null, title), ...kept);
    return box;
  }
  function sheetBody(it) {
    const grid = el("div", "sheet-grid");
    const pic = el("div", "sheet-pic");
    pic.append(shot(it, true));

    const main = el("div", "sheet-main");
    const head = el("div", "sheet-head");
    const ctx = [it.ip, it.chr].filter(Boolean).join(" · ");
    if (ctx) head.append(say("p", "ctx", ctx));
    const h = say("h2", null, it.name);
    h.id = "sheet-title";
    head.append(h);
    for (const alt of it.alts || []) head.append(say("p", "alt", alt));
    const pills = el("div", "pills");
    const st = status(it);
    if (st && st.cls !== "mute") pills.append(el("span", `pill ${st.cls}`, st.text));
    for (const text of [TYPES[it.type], it.scale !== "无比例" && it.scale, heightText(it.h) && `高 ${heightText(it.h)}`]) {
      if (text) pills.append(el("span", "pill", text));
    }
    for (const key of ["re", "ltd", "bonus", "mto"]) if (it.flagSet.has(key)) pills.append(el("span", "pill", FLAGS[key]));
    if (it.flagSet.has("old")) pills.append(el("span", "pill", "旧品补录"));
    if (it.adult) pills.append(el("span", "pill", it.rating ? `成人向（${it.rating}）` : "成人向"));
    head.append(pills);
    main.append(head);

    const offers = el("div", "offers");
    for (const o of it.offers) {
      const box = el("div", "offer");
      const src = SOURCES[o.s] || { name: o.s };
      box.append(el("p", "who", [src.name, ...(o.st || [])].join(" · ")), priceLine(o));
      const window_ = o.ps && o.pe ? `预订 ${md(o.ps)} – ${md(o.pe)}` : o.pe ? `截订 ${md(o.pe)}` : "";
      const notes = [o.note, window_ && `${window_}（${leftText(daysTo(o.pe))}）`, o.cancel && `可取消到 ${md(o.cancel)}`].filter(Boolean);
      if (notes.length) box.append(el("p", "note", notes.join(" · ")));
      const url = safeUrl(o.url);
      if (url) {
        const a = el("a", "btn", o.s === "HPOI" ? "资料页" : "去看看");
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.append(icon("out"));
        box.append(a);
      }
      offers.append(box);
    }
    main.append(block(it.offers.length > 1 ? `价格与来源（${it.offers.length} 处）` : "价格与来源", offers));
    if (it.gone) main.append(el("p", "fine", "这件商品已经不在最近的数据里了，这里是收藏时记下的信息。"));

    const opens = it.open ? `${md(it.open)}${daysTo(it.open) > 0 ? `（${daysTo(it.open)} 天后）` : ""}` : "";
    main.append(block("发售与预订", kv([
      ["发售", releaseText(it)],
      ["预订期", it.pe ? `${it.ps ? `${it.ps} 至 ` : ""}${it.pe}（${leftText(daysTo(it.pe))}）` : ""],
      ["开订", opens],
      ["进展", it.stage && it.stage !== "预订中" ? it.stage : ""],
      ["发售记录", it.history],
      ["首次收录", it.day],
    ])));

    const series = [it.ip, ...(it.series || [])].filter(Boolean);
    const makers = [it.maker, ...(it.makers || [])].filter(Boolean);
    main.append(block("规格", kv([
      ["作品", series.length > 1 ? series : series[0]],
      ["角色", it.chr],
      ["厂商", makers.length > 1 ? makers : makers[0]],
      ["发行", it.publisher],
      ["类型", [...new Set([TYPES[it.type], ...(it.line || [])].filter(Boolean))].join(" · ")],
      ["比例", it.scale],
      ["尺寸", it.size],
      ["材质", it.material],
      ["原型", it.sculptor],
      ["涂装", it.painter],
      ["原画", it.artist],
      ["特典", it.bonus],
      ["官方规格", it.spec],
      ["观感", it.rating],
      ["JAN", it.jan],
    ])));

    if (it.hot) {
      const hot = it.hot;
      const stats = el("div", "stats");
      for (const [label, value] of [["关注", hot.f], ["想买", hot.w], ["预订", hot.p], ["已入", hot.o],
        ["评分", hot.s ? hot.s.toFixed(2) : "—"], ["访问", it.views || "—"]]) {
        const cell = el("div", "stat");
        cell.append(el("b", null, typeof value === "number" ? fmt(value) : value || "0"), el("span", null, label));
        stats.append(cell);
      }
      const read = `${md(hot.at)} 读取${hot.n ? `，${hot.n} 人评分` : ""}`;
      main.append(block("HPOI 人气", stats, el("p", "fine", it.heat || hot.n ? read : `${read}。刚收录的商品人气还是 0，第 2 天和第 7 天会再读一次。`)));
    }
    if (it.prog && it.prog.length) main.append(block("HPOI 进程", kv(it.prog.map((e) => {
      const i = e.indexOf("：");
      return i > 0 ? [e.slice(0, i), e.slice(i + 1)] : ["动态", e];
    }))));

    grid.append(pic, main);
    return grid;
  }
  function openSheet(it, opts) {
    const o = opts || {};
    const dlg = $("sheet");
    const at = queue.findIndex((entry) => entry.it.k === it.k);
    const bar = el("div", "sheet-bar");
    const where = el("span", "where", [it.srcs.map((s) => (SOURCES[s] || {}).name || s).join(" + "),
      it.day && `${md(it.day)} 收录`].filter(Boolean).join(" · "));
    const nav = (name, label, target) => {
      const b = el("button", "icon-btn");
      b.type = "button";
      b.setAttribute("aria-label", label);
      b.append(icon(name));
      b.disabled = !target;
      if (target) b.addEventListener("click", () => openSheet(target.it, { swap: true }));
      return b;
    };
    const close = el("button", "icon-btn");
    close.type = "button";
    close.setAttribute("aria-label", "关闭");
    close.append(icon("close"));
    close.addEventListener("click", closeOverlay);
    bar.append(where, nav("prev", "上一件", at > 0 ? queue[at - 1] : null),
      nav("next", "下一件", at >= 0 ? queue[at + 1] : null), favButton(it), close);
    const body = el("div", "sheet-body");
    body.append(sheetBody(it));
    dlg.replaceChildren(bar, body);
    dlg.dataset.key = it.k;

    const hash = `#item=${encodeURIComponent(it.k)}`;
    if (o.push) history.pushState({ ov: "item" }, "", hash);
    else if (o.swap) history.replaceState(history.state, "", hash);
    if (o.from) returnTo = o.from;
    if (!dlg.open) {
      if ($("filters").open) $("filters").close();
      dlg.showModal();
      lock(true);
    }
    body.scrollTop = 0;
    close.focus({ preventScroll: true });
  }
  function openPanel() {
    const dlg = $("filters");
    if (dlg.open) return;
    returnTo = $("open-filters");
    history.pushState({ ov: "filters" }, "");
    dlg.showModal();
    lock(true);
    renderPanel();
  }
  function hideOverlays() {
    for (const id of ["sheet", "filters"]) if ($(id).open) $(id).close();
    lock(false);
    if (returnTo && returnTo.isConnected) returnTo.focus({ preventScroll: true });
    returnTo = null;
  }
  // Closing goes back one step in history when opening added one, so the phone's back button closes too.
  function closeOverlay() {
    if (history.state && history.state.ov) history.back();
    else {
      hideOverlays();
      if (location.hash) history.replaceState(null, "", location.pathname + location.search);
    }
  }
  const hashItem = () => {
    const m = /^#item=(.+)$/.exec(location.hash);
    if (!m) return null;
    try { return byKey.get(decodeURIComponent(m[1])) || null; } catch (e) { return null; }
  };
  window.addEventListener("popstate", () => {
    const it = hashItem();
    if (it) openSheet(it);
    else hideOverlays();
  });
  for (const id of ["sheet", "filters"]) {
    $(id).addEventListener("cancel", (e) => { e.preventDefault(); closeOverlay(); });
    $(id).addEventListener("click", (e) => { if (e.target === $(id)) closeOverlay(); });
  }
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((e.target && e.target.tagName) || "");
    if ($("sheet").open && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      const at = queue.findIndex((entry) => entry.it.k === $("sheet").dataset.key);
      const target = at < 0 ? null : queue[at + (e.key === "ArrowLeft" ? -1 : 1)];
      if (target) openSheet(target.it, { swap: true });
    } else if (e.key === "/" && !typing && !$("sheet").open && !$("filters").open) {
      e.preventDefault();
      $("q").focus();
    }
  });

  // ---- footer, theme, toast
  function renderMeta() {
    const names = { ok: ["正常", "var(--ok)"], blocked: ["被网站拦截", "var(--due)"], error: ["出错", "var(--soon)"] };
    const tbody = $("runs").querySelector("tbody");
    let bad = 0;
    for (const key of Object.keys(SOURCES)) {
      const run = (DATA.runs || {})[key] || {};
      const [text, color] = names[run.status] || ["—", "var(--ink-3)"];
      if (run.status && run.status !== "ok") bad++;
      const state_ = el("span", "run", text);
      state_.style.setProperty("--c", color);
      const cell = el("td");
      cell.append(state_);
      const tr = el("tr");
      tr.append(el("td", null, SOURCES[key].name), cell, el("td", "n", String(run.new == null ? 0 : run.new)), el("td", "note", run.note || ""));
      tbody.append(tr);
    }
    const m = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/.exec(DATA.updated || "");
    if (m) {
      // The scraper runs in the small hours, Beijing time, so by LATE_HOUR today's data is overdue.
      const gap = daysTo(m[1]);
      const late = gap < -1 || (gap === -1 && bj.getUTCHours() >= LATE_HOUR);
      const when = gap === 0 ? "今天" : gap === -1 ? "昨天" : md(m[1]);
      $("fresh").append(el("span", bad ? "dot bad" : late ? "dot late" : "dot"), `${when} ${m[2]} 更新`);
      if (late) $("fresh").append(el("small", null, gap < -1 ? `已经 ${-gap} 天没更新` : "今天的还没到"));
      $("fresh").title = bad ? `有 ${bad} 个来源这次没抓到，详见页面底部`
        : late ? "每天清晨抓取一次（北京时间）；GitHub 的定时任务有时会晚几个小时" : "各个来源都抓取正常（北京时间）";
    }
    $("rate-note").textContent = `页面上的 ¥ 一律指人民币，日元、美元价格写作「日元」「美元」。约合价按 1 日元 ≈ ${RATE} 元估算`
      + `（取自 HPOI 近期标注的中位数），HPOI 自己标了人民币参考价的直接用它的；美元按 1 美元 ≈ ${Number(DATA.usd) || 7.1} 元粗算。`;
    $("brand-sub").textContent = `每天汇总 ${Object.keys(SOURCES).length} 个来源的新品`;
  }
  const THEMES = { auto: ["auto", "外观：跟随系统"], light: ["sun", "外观：浅色"], dark: ["moon", "外观：深色"] };
  function paintTheme() {
    const mode = document.documentElement.dataset.theme || "auto";
    const b = $("theme");
    b.replaceChildren(icon(THEMES[mode][0]));
    b.setAttribute("aria-label", `${THEMES[mode][1]}，点一下切换`);
    b.title = THEMES[mode][1];
  }
  $("theme").addEventListener("click", () => {
    const order = ["auto", "light", "dark"];
    const next = order[(order.indexOf(document.documentElement.dataset.theme || "auto") + 1) % 3];
    if (next === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
    try {
      if (next === "auto") localStorage.removeItem("fn-theme");
      else localStorage.setItem("fn-theme", next);
    } catch (e) { /* not saved, still applied */ }
    paintTheme();
  });
  let toastTimer = 0;
  function toast(text) {
    const box = $("toast");
    box.textContent = text;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 1800);
  }

  // ---- controls
  const scrollToList = () => {
    const y = $("bar-top").getBoundingClientRect().top + window.scrollY;
    if (window.scrollY > y) window.scrollTo(0, y);
  };
  for (const [value, label] of SORTS) {
    const o = el("option", null, label);
    o.value = value;
    $("sort").append(o);
  }
  let typing = 0;
  $("q").addEventListener("input", (e) => {
    clearTimeout(typing);
    typing = setTimeout(() => {
      state.q = e.target.value.trim().toLowerCase();
      state.words = state.q.split(/\s+/).filter(Boolean);
      // A search looks through every day, not only the one that happens to be open.
      if (state.words.length && state.tab === "new" && days.length > 1) state.day = "all";
      render();
      scrollToList();
    }, 160);
  });
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); scrollToList(); });
  $("open-filters").addEventListener("click", openPanel);
  $("reset").addEventListener("click", () => { resetFilters(); render(); });
  for (const view of ["grid", "list"]) {
    $(`view-${view}`).addEventListener("click", () => { state.view = view; store.set("fn-view", view); render(); });
  }
  // A mouse wheel moves the day strip sideways, since it has no scrollbar to grab.
  $("days").addEventListener("wheel", (e) => {
    const box = e.currentTarget;
    if (box.scrollWidth <= box.clientWidth || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
    e.preventDefault();
    box.scrollLeft += e.deltaY;
  }, { passive: false });
  $("days").addEventListener("scroll", fadeDays, { passive: true });
  window.addEventListener("resize", fadeDays);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(([entry]) => {
      $("bar").classList.toggle("stuck", !entry.isIntersecting && entry.boundingClientRect.top < 60);
    }, { rootMargin: "-1px 0px 0px 0px" }).observe($("bar-top"));
  }

  // ---- a page left open: pick up the next build when it is out
  // Coming back to the page reloads it if there is a newer build and nothing is in progress.
  // Otherwise, and for a page that stayed in view all along, a button offers the reload.
  let checkedAt = Date.now();
  async function checkNewer(cameBack) {
    if (document.visibilityState !== "visible" || Date.now() - checkedAt < 5 * 60e3) return;
    checkedAt = Date.now();
    let latest = "";
    try {
      const res = await fetch("version.json", { cache: "no-store" });
      if (res.ok) latest = String((await res.json()).updated || "");
    } catch (e) { return; } // offline, or opened as a local file
    if (!latest || latest <= (DATA.updated || "")) return;
    let reload = cameBack && !$("sheet").open && !$("filters").open && !$("q").value;
    try {
      // Once per build, in case the newer page has not reached this visitor yet.
      if (sessionStorage.getItem("fn-reloaded") === latest) reload = false;
      else if (reload) sessionStorage.setItem("fn-reloaded", latest);
    } catch (e) { reload = false; }
    if (reload) location.reload();
    else $("newer").hidden = false;
  }
  document.addEventListener("visibilitychange", () => checkNewer(true));
  window.addEventListener("pageshow", (e) => { if (e.persisted) checkNewer(true); });
  setInterval(() => checkNewer(false), 30 * 60e3);
  $("newer").addEventListener("click", () => location.reload());

  if (window.matchMedia("(max-width: 599px)").matches) $("q").placeholder = "搜名称、作品";
  renderMeta();
  paintTheme();
  render();
  const linked = hashItem();
  if (linked) openSheet(linked);
})();
