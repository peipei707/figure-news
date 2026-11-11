# figure-news 手办新品日报

每天早上（北京时间 7:40 左右）自动抓一次 HPOI、Good Smile 官网、HLJ 的新品，按天汇总成表。

**最新一期：[2026-11-11](daily/2026-11-11.md)（80 件）**

**公开网页：[https://peipei707.github.io/figure-news/](https://peipei707.github.io/figure-news/)**（GitHub Pages，每天抓取后自动更新，最近 30 天）

网页把同一件商品在几个来源的记录合成一张卡片，按类型分组，可以搜索、筛选、收藏，还有一个按截订时间排的「预订」视图。

## 上次运行

2026-11-11 20:34（北京时间）

| 来源 | 结果 | 新增 | 备注 |
|---|---|---|---|
| HPOI | ✅ 正常 | 17 |  |
| Good Smile 官网 | ✅ 正常 | 0 |  |
| HLJ | ✅ 正常 | 14 |  |

## 最近 30 天

- [2026-11-11](daily/2026-11-11.md) · 80 件
- [2026-11-10](daily/2026-11-10.md) · 39 件
- [2026-11-09](daily/2026-11-09.md) · 152 件

## 文件

- `daily/日期.md`：当天第一次出现的商品，带缩略图
- `data/items.csv`：抓到过的全部商品，一行一件，可以直接用 Excel 打开
- `thumbs/`：商品图，缩到 400×560 以内的 WebP
- `scrape.py`：抓取脚本；`.github/workflows/daily.yml`：每天定时运行
- `build_site.py` 和 `page/`：生成网页版（输出到 build/，不进仓库）
- `page/aliases.json`：作品、厂商在三个来源里的不同写法对照表，网页靠它统一成中文叫法，可以直接往里加

## 字段

| 字段 | 含义 |
|---|---|
| first_seen | 第一次抓到的日期 |
| source / id / url | 来源站 / 站内编号 / 原页面 |
| name / name_alt | 名称 / 日文原名（HPOI 提供） |
| maker / series / character | 厂商 / 作品 / 角色 |
| category / scale | 类型、产品线 / 比例 |
| price / currency / price_note | 价格 / 币种 / 备注（如 HPOI 的人民币参考价） |
| release | 发售（出货）月份。Good Smile 官网只给再贩商品写，新品的月份来自 HLJ 或 HPOI 的同款 |
| preorder_start / preorder_end | 预订开始 / 截止 |
| status | 预订中、再贩、官网限定、旧品补录等 |
| rating / adult | HPOI 的观感等级 / 成人向标记（1 = 是） |
| size / material / sculptor / painter | 尺寸 / 材质 / 原型 / 涂装 |
| bonus / progress / extra | 特典 / HPOI 进程（开订、出荷等最新动态）/ 其他信息（如 HLJ 取消截止日、Good Smile 再贩商品的发售记录） |
| follows / wants / preorders / owned | HPOI 的关注 / 想买 / 预订 / 已入人数 |
| score / score_count / views | HPOI 评分 / 评分人数 / 访问量 |
| stats_date | 人气数据的读取日期（收录当天、第 2 天、第 7 天各读一次） |
| jan | JAN 条码 |
| image_url / thumb | 原图链接 / 缩略图路径 |

## 手动运行

GitHub 仓库页 → Actions → daily → Run workflow。

## 抓取规矩

如实标明身份（`figure-news/0.1`），遵守各站 robots.txt，同一网站两次请求至少隔 3 秒（HPOI 按它的要求隔 5 秒），遇到人机验证直接跳过、不绕过。AmiAmi、HobbySearch、电击 Hobby 因此没有收录。

## 成人向

HPOI 里观感成熟的条目和原站一样照常收录、照常显示封面，只多一个「成人向」标记，网页的「筛选」里可以把它们关掉。HLJ 的成人向商品要登录并通过年龄验证才会显示，匿名访问看不到，所以没有收录。

_本文件由 scrape.py 自动生成，手动修改会在下次运行时被覆盖。_
