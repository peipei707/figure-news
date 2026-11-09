# figure-news 手办新品日报

每天早上（北京时间 7:40 左右）自动抓一次 HPOI、Good Smile 官网、HLJ 的新品，按天汇总成表。

**最新一期：[2026-11-09](daily/2026-11-09.md)（151 件）**

**公开网页：[https://peipei707.github.io/figure-news/](https://peipei707.github.io/figure-news/)**（GitHub Pages，每天抓取后自动更新，最近 30 天）

claude.ai 版本：[手办新品日报](https://claude.ai/artifact/MpGg62ocB5aBF7wCvRubAf)（每天 9:15 左右刷新）

## 上次运行

2026-11-09 17:28（北京时间）

| 来源 | 结果 | 新增 | 备注 |
|---|---|---|---|
| HPOI | ✅ 正常 | 0 |  |
| Good Smile 官网 | ✅ 正常 | 0 |  |
| HLJ | ✅ 正常 | 0 |  |

## 最近 30 天

- [2026-11-09](daily/2026-11-09.md) · 151 件

## 文件

- `daily/日期.md`：当天第一次出现的商品，带缩略图
- `data/items.csv`：抓到过的全部商品，一行一件，可以直接用 Excel 打开
- `thumbs/`：缩略图
- `scrape.py`：抓取脚本；`.github/workflows/daily.yml`：每天定时运行
- `build_site.py` 和 `page/template.html`：生成网页版（输出到 build/，不进仓库）

## 字段

| 字段 | 含义 |
|---|---|
| first_seen | 第一次抓到的日期 |
| source / id / url | 来源站 / 站内编号 / 原页面 |
| name / name_alt | 名称 / 日文原名（HPOI 提供） |
| maker / series / character | 厂商 / 作品 / 角色 |
| category / scale | 类型、产品线 / 比例 |
| price / currency / price_note | 价格 / 币种 / 备注（如 HPOI 的人民币参考价） |
| release | 发售（出货）月份 |
| preorder_start / preorder_end | 预订开始 / 截止 |
| status | 预订中、再贩、官网限定、旧品补录等 |
| rating / adult | HPOI 的观感等级 / 成人向标记（1 = 是） |
| size / material / sculptor / painter | 尺寸 / 材质 / 原型 / 涂装 |
| bonus / progress / extra | 特典 / HPOI 进程（开订、出荷等最新动态）/ 其他信息（如 HLJ 取消截止日） |
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

HPOI 里观感成熟的条目照常录入并标为成人向，只记录文字和链接，不保存图片。HLJ 的成人向商品要登录并通过年龄验证才会显示，匿名访问看不到，所以没有收录。

_本文件由 scrape.py 自动生成，手动修改会在下次运行时被覆盖。_
