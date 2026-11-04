# 公众号文章导出器 · WeChat Article Exporter

![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

**[English summary](#english-summary)** · **[中文说明](#中文说明)**

---

## English summary

A small, **fully local** tool that exports the article history of any WeChat Official Account (公众号) to an Excel spreadsheet within a date range — title, link, publish time, likes, favorites, shares and comments in one sheet.

No dependencies: standard-library Python only. Nothing is uploaded anywhere; your session credential lives in memory and in a local `credential.txt` that is git-ignored.

### Features

- **Date-range export** — pick a start and end date; pagination stops as soon as it walks past the start date, so you don't have to crawl to the bottom of a large account.
- **Seven columns, one sheet** — title, link, publish time, likes, favorites, shares, comments.
- **Excel or CSV** — xlsx is written by a tiny built-in writer (zip + XML), so `openpyxl`/`pandas` are not needed.
- **Browser UI on localhost** — the panel binds to `127.0.0.1:8765` only; it is never reachable from the network.
- **Credential helper included** — a bundled local proxy intercepts `mp.weixin.qq.com` only, extracts the `action=getmsg` URL, and restores your system proxy afterwards.
- **Polite by default** — 1.2 s between list pages, 3 workers with a 0.5 s pause per article.

### Quick start

Requires [Python 3.8+](https://www.python.org/downloads/) (tick *Add python.exe to PATH* on Windows).

```bash
git clone https://github.com/sincewhen110/wechat-article-exporter.git
cd wechat-article-exporter

python app.py          # Windows: you can also just double-click 启动.bat
```

Your browser opens <http://127.0.0.1:8765> automatically. If that port is taken it walks forward; you can also pass one explicitly: `python app.py 9000`.

### Usage

1. **Get a session credential** (valid for a few hours). Double-click `抓取凭据.bat` on Windows: it installs a local root CA (one-time prompt), points the system proxy at `127.0.0.1:8898` while decrypting only `mp.weixin.qq.com`, and waits for you to fully restart WeChat and open any article of the target account. The captured URL is written to `credential.txt` and the proxy is restored. Then click *读取本机抓取结果* in the panel.
2. **Export.** Paste an `action=getmsg` URL (or a whole cookie string), paste any article link of the target account (the `__biz` is extracted automatically), choose the date range, hit start, then download the xlsx or CSV.

> No Windows? Capture the `action=getmsg` request yourself with mitmproxy / Charles / Proxyman and paste the full URL — the exporter part is cross-platform (`python3 app.py`).

### Output columns

| Column | Source |
| --- | --- |
| 公众号标题 / title | `title` from the article list |
| 发布链接 / link | `link`, normalised to `https://` and unescaped |
| 发布时间 / published at | article page `ct` (Unix seconds) → local `YYYY-MM-DD HH:MM:SS` |
| 点赞 / likes | `like_count` |
| 收藏 / favorites | `collect_count` |
| 转发 / shares | `share_count` |
| 评论 / comments | `comment_count` |

The four metric columns are only returned when the article request carries a valid WeChat session (`key` + cookie). With an expired credential they will be empty.

### How it works

```
WeChat client ──system proxy──> 127.0.0.1:8898 (capture_proxy.py)
                                    │  MITM mp.weixin.qq.com only
                                    └─> action=getmsg URL ──> credential.txt
                                                                     │
browser panel (127.0.0.1:8765) <── app.py <──────────────────────────┘
        │                          │ paginate the list (1.2 s / page)
        └── xlsx / csv <───────────┴ fetch metrics concurrently (3 workers, 0.5 s / article)
```

- `app.py` — `ThreadingHTTPServer` from the standard library, plus a hand-rolled xlsx writer.
- `capture/capture_proxy.py` — SNI-based routing with `ssl`; anything that is not `mp.weixin.qq.com` is tunnelled through untouched.
- `capture/gencert.cs` — generates the local CA and the `mp.weixin.qq.com` leaf certificate with .NET. A prebuilt `gencert.exe` is included; build it yourself with `csc /target:exe /out:gencert.exe gencert.cs` if you prefer.

### Notes

- Binds to loopback only; nothing listens on your LAN.
- Best-effort scraping of a private API: WeChat can change parameters or rate-limit you at any time. There is a hard cap of 300 pages (≈ 3000 articles) per run.
- Use it only for content you are allowed to access, and respect the WeChat Terms of Service and copyright law. The authors take no responsibility for how it is used.
- Windows is the only platform with the credential-capture helper; the exporter itself is cross-platform.

### License

[MIT](LICENSE) © 2026 wechat-article-exporter contributors

---

## 中文说明

一个**完全在本机运行**的小工具：把任意微信公众号的历史文章，按时间段导出成 Excel（xlsx / csv），一次拿到标题、链接、发布时间、点赞、收藏、转发、评论。

只用 Python 标准库，不联网上传任何数据，凭据只存在内存里。

## 功能

- **按时间段导出**：选起止日期，翻页到早于起始日会自动停止，不用把整个号翻到底
- **六项数据一表**：标题 / 链接 / 发布时间 / 点赞 / 收藏 / 转发 / 评论
- **两种导出格式**：Excel（内置最小 xlsx 写入器，无需 openpyxl）和 CSV
- **网页操作**：本地面板跑在 `127.0.0.1`，不对外暴露；粘贴一次凭据即可
- **自动抓凭据**：内置本地代理，只拦截 `mp.weixin.qq.com`，抓完自动还原系统代理
- **零依赖**：`pip install` 都不用，装了 Python 就能跑

## 快速开始

### 方式一：下载发行版（推荐给不想装 Python 的人）

1. 打开右侧 [Releases](../../releases) 页面，下载最新的 `wechat-article-exporter-windows.zip`
2. 解压到任意目录（路径别带空格更稳）
3. 双击 `启动.bat`，浏览器会自动打开操作面板

### 方式二：从源码运行

需要 [Python 3.8+](https://www.python.org/downloads/)（安装时勾选 *Add python.exe to PATH*）。

```bash
git clone https://github.com/sincewhen110/wechat-article-exporter.git
cd wechat-article-exporter

python app.py            # Windows 上也可以直接双击 启动.bat
```

浏览器会自动打开 <http://127.0.0.1:8765>（端口被占用时会自动往后找；也可以 `python app.py 9000` 指定）。

## 使用

### 第一步：拿到一次会话凭据（几小时内有效）

微信的列表接口需要一条带 `key` 的 URL。手动抓比较麻烦，所以仓库里带了一个小工具：

1. 双击 `抓取凭据.bat`（首次运行会提示安装一个本地根证书，点「是」，只需一次）
2. 它会把系统代理临时指向 `127.0.0.1:8898`，只解密 `mp.weixin.qq.com`，其它流量原样直通
3. **完全退出微信再重开**（托盘图标右键 → 退出），在微信里打开目标公众号的任意一篇文章
4. 抓到 `key` 后写成 `credential.txt`，并自动把系统代理还原

然后回到操作面板，点「读取本机抓取结果」。

> 也可以跳过这一步：用 mitmproxy / Charles 自己抓一条 `action=getmsg` 的请求，把完整 URL 粘进面板即可。

### 第二步：导出

1. **凭据**：粘贴 `action=getmsg` 请求的完整 URL（或整段 Cookie）
2. **公众号**：粘贴目标号任意一篇文章的链接，程序自动取出 `__biz`；也可以直接填 `__biz`
3. **时间段**：选起止日期（留空 = 不限）
4. 点「开始导出」，跑完后下载 Excel 或 CSV

## 输出字段

| 列 | 来源 |
| --- | --- |
| 公众号标题 | 文章列表 `title` |
| 发布链接 | `link`，统一成 `https://` 并还原 `&amp;` |
| 发布时间 | 文章页 `ct`（Unix 秒）→ 本地时间 `YYYY-MM-DD HH:MM:SS` |
| 点赞 / 收藏 / 转发 / 评论 | `like_count` / `collect_count` / `share_count` / `comment_count` |

后四个数只有带着微信会话（`key` + cookie）访问文章页时才会返回；凭据失效时会是空的。

## 工作原理

```
微信客户端 ──HKLM 系统代理──> 127.0.0.1:8898 (capture_proxy.py)
                                   │  只 MITM mp.weixin.qq.com
                                   └─> 抓到 action=getmsg 的 URL ──> credential.txt
                                                                            │
浏览器面板 (127.0.0.1:8765) <── app.py <────────────────────────────────────┘
        │                         │ 翻页取列表（每页间隔 1.2s）
        └── xlsx / csv <──────────┴ 并发取指标（3 线程，每篇间隔 0.5s）
```

- `app.py`：标准库 `ThreadingHTTPServer` + 手写 xlsx（zip + xml）
- `capture/capture_proxy.py`：`ssl` 做 SNI 分流，非目标域名走了 CONNECT 隧道直通
- `capture/gencert.cs`：用 .NET 生成 CA + `mp.weixin.qq.com` 叶子证书（自带 `gencert.exe`，不想用可自行 `csc` 编译）

## 目录结构

```
wechat-article-exporter/
├─ app.py                  # 本地网页服务 + 抓取 + 导出
├─ static/index.html       # 操作面板
├─ capture/
│  ├─ capture_proxy.py     # 本地 MITM 代理，抓 getmsg URL
│  ├─ reset_proxy.py       # 还原系统代理
│  ├─ gencert.cs           # 证书生成源码
│  └─ gencert.exe          # 证书生成器（可选，缺证书时才用）
├─ 启动.bat / 抓取凭据.bat / 修复网络.bat
├─ README.md
└─ LICENSE
```

## 常见问题

**点启动没反应 / 提示找不到 Python**
装 Python 3.8+ 并勾选 *Add to PATH*；或直接用 Releases 里的打包版。

**「列表接口返回 ret=…」**
凭据失效了，或粘的不是 `action=getmsg` 那条 URL。重抓一次。

**导出的点赞、评论全是空**
说明请求没带上微信会话，多半是 `key` 过期；也可能是该号已注销、文章被删。

**个别文章显示「页面无正文」**
文章被删除 / 违规 / 需要验证，属正常情况，该行仍会保留。

**抓完凭据后上不了网**
双击 `修复网络.bat` 还原系统代理（抓取正常结束时也会自动还原；程序被强杀时可能残留）。

**Mac / Linux 能用吗**
`app.py` 本身跨平台：`python3 app.py` 即可。抓凭据部分依赖 `winreg`，目前只支持 Windows，macOS 可自行用 mitmproxy。

## 免责声明

本工具仅用于导出**你有权访问的公开内容**，供个人备份、研究或数据分析使用。请自行遵守《微信公众平台服务协议》、相关法律法规与著作权规定；作者不对使用者的行为及其后果负责。

## 许可

[MIT](LICENSE) © 2026 wechat-article-exporter contributors
