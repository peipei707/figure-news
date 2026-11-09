# figure-news 手办新品日报

每天早上（北京时间 7:40 左右）自动抓一次 HPOI、Good Smile 官网、HLJ 的新品，按天汇总成表。

**最新一期：[2026-11-09](daily/2026-11-09.md)（107 件）**

## 上次运行

2026-11-09 14:04（北京时间）

| 来源 | 结果 | 新增 | 备注 |
|---|---|---|---|
| HPOI | ✅ 正常 | 30 |  |
| Good Smile 官网 | ✅ 正常 | 40 |  |
| HLJ | ✅ 正常 | 37 |  |

## 最近 30 天

- [2026-11-09](daily/2026-11-09.md) · 107 件

## 文件

- `daily/日期.md`：当天第一次出现的商品，带缩略图
- `data/items.csv`：抓到过的全部商品，一行一件，可以直接用 Excel 打开
- `thumbs/`：缩略图
- `scrape.py`：抓取脚本；`.github/workflows/daily.yml`：每天定时运行

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
| jan | JAN 条码 |
| image_url / thumb | 原图链接 / 缩略图路径 |

## 手动运行

GitHub 仓库页 → Actions → daily → Run workflow。

## 抓取规矩

如实标明身份（`figure-news/0.1`），遵守各站 robots.txt，同一网站两次请求至少隔 3 秒（HPOI 按它的要求隔 5 秒），遇到人机验证直接跳过、不绕过。AmiAmi、HobbySearch、电击 Hobby 因此没有收录。

_本文件由 scrape.py 自动生成，手动修改会在下次运行时被覆盖。_
