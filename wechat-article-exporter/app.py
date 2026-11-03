#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号文章按时间段导出（本地网页工具）

只使用 Python 标准库，不需要 pip install 任何东西。
运行后在浏览器打开 http://127.0.0.1:8765 ，粘贴一次微信会话凭据即可导出 xlsx。
"""

from __future__ import annotations

import csv
import datetime
import gzip
import html as htmlmod
import io
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE, "static")
CAPTURE_FILE = os.path.join(BASE, "credential.txt")

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47(0x18002f2c) NetType/WIFI Language/zh_CN"
)

COLUMNS = ["公众号标题", "发布链接", "发布时间", "点赞", "收藏", "转发", "评论"]
MAX_PAGES = 300          # 安全上限：3000 篇
PAGE_SLEEP = 1.2         # 翻页间隔（秒）
METRIC_WORKERS = 3       # 抓指标的并发
METRIC_SLEEP = 0.5       # 单篇抓取前的等待

JOBS: dict[str, "Job"] = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_get(url: str, cookie: str | None = None, timeout: int = 40, retries: int = 2) -> tuple[int, str]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            headers = {
                "User-Agent": UA,
                "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip",
                "Referer": "https://mp.weixin.qq.com/",
            }
            if cookie:
                headers["Cookie"] = cookie
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in enc:
                    raw = gzip.decompress(raw)
                elif "deflate" in enc:
                    raw = zlib.decompress(raw)
                return resp.status, raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            last = RuntimeError("HTTP %s: %s" % (e.code, body[:200]))
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < retries:
            time.sleep(1.0 + attempt)
    raise last if last else RuntimeError("请求失败")


# --------------------------------------------------------------------------- #
# 凭据 / 账号
# --------------------------------------------------------------------------- #
def parse_credential(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("凭据为空")
    raw = htmlmod.unescape(raw)
    info = {"params": {}, "cookie": None, "url": None}

    m = re.search(r"https?://mp\.weixin\.qq\.com/[^\s\"'<>]+", raw)
    if m:
        url = m.group(0).split("#")[0]
        info["url"] = url
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)
        info["params"] = {k: v[0] for k, v in q.items()}

    if not info["url"]:
        if ";" in raw and "=" in raw:
            info["cookie"] = raw
        elif "=" in raw:
            q = urllib.parse.parse_qs(raw.lstrip("?"), keep_blank_values=True)
            info["params"] = {k: v[0] for k, v in q.items()}

    mc = re.search(r"^\s*Cookie:\s*(.+)$", raw, re.M | re.I)
    if mc:
        info["cookie"] = mc.group(1).strip()

    if not info["params"] and not info["cookie"]:
        raise ValueError("无法识别凭据：请粘贴完整的 getmsg 请求 URL，或 Cookie 字符串")
    return info


def extract_biz(text: str) -> str | None:
    text = htmlmod.unescape(text or "")
    m = re.search(r"__biz=([A-Za-z0-9%+/=]+)", text)
    if m:
        return urllib.parse.unquote(m.group(1))
    m = re.search(r"\b(Mz[A-Za-z0-9+/=]{10,})\b", text)
    if m:
        return m.group(1)
    return None


def normalize_url(url: str) -> str:
    url = htmlmod.unescape((url or "").strip())
    url = url.replace("http://", "https://", 1)
    return url.split("#")[0]


def build_list_url(params: dict, biz: str, offset: int, count: int = 10) -> str:
    q = {
        "action": "getmsg",
        "__biz": biz,
        "f": "json",
        "offset": str(offset),
        "count": str(count),
        "is_ok": "1",
        "scene": params.get("scene") or "124",
        "uin": params.get("uin", ""),
        "key": params.get("key", ""),
        "pass_ticket": params.get("pass_ticket", ""),
        "wxtoken": params.get("wxtoken", ""),
        "appmsg_token": params.get("appmsg_token", ""),
        "x5": "0",
    }
    return "https://mp.weixin.qq.com/mp/profile_ext?" + urllib.parse.urlencode(q)


def parse_list_payload(body: str) -> dict:
    d = json.loads(body)
    ret = d.get("ret")
    if ret not in (0, None):
        msg = d.get("errmsg") or d.get("err_msg") or ""
        raise RuntimeError("列表接口返回 ret=%s %s（凭据多半已失效）" % (ret, msg))
    gl = d.get("general_msg_list")
    if isinstance(gl, str):
        gl = json.loads(gl) if gl.strip() else {"list": []}
    items = (gl or {}).get("list") or []
    return {
        "items": items,
        "can_continue": bool(d.get("can_msg_continue")),
        "count": int(d.get("msg_count") or 0) or 10,
    }


def expand_items(items: list):
    for it in items:
        cm = it.get("comm_msg_info") or {}
        ts = int(cm.get("datetime") or 0)
        ext = it.get("app_msg_ext_info")
        if not ext:
            continue
        yield ts, ext
        for sub in ext.get("multi_app_msg_item_list") or []:
            yield ts, sub


# --------------------------------------------------------------------------- #
# 文章解析
# --------------------------------------------------------------------------- #
BAR_KEYS = ["read_num", "like_count", "share_count", "old_like_count", "collect_count", "comment_count"]


def _first(pat: str, text: str, flags: int = re.S) -> str | None:
    m = re.search(pat, text, flags)
    return m.group(1) if m else None


def parse_bar_data(text: str) -> dict:
    """从文章页的 tmpAppmsgBarData 里取指标（带会话时才有值）。"""
    out: dict[str, int | None] = {k: None for k in BAR_KEYS}
    for m in re.finditer(r"var\s+tmpAppmsgBarData\s*=\s*\{(.*?)\}\s*;", text, re.S):
        body = m.group(1)
        for key in BAR_KEYS:
            if out.get(key) is not None:
                continue
            mm = re.search(
                r"\b" + key + r"\s*:\s*(?:'([^']*)'|\"([^\"]*)\"|(\d+))",
                body,
            )
            if not mm:
                continue
            val = next((g for g in mm.groups() if g not in (None, "")), None)
            if val is not None and str(val).isdigit():
                out[key] = int(val)
    return out


def parse_article(text: str) -> dict:
    title = _first(r'<meta\s+property="og:title"\s+content="([^"]*)"', text)
    if not title:
        title = _first(r"var\s+msg_title\s*=\s*'([^']*)'", text)
    if not title:
        title = _first(r'id="activity-name"[^>]*>(.*?)</h1>', text)

    ct = _first(r'var\s+ct\s*=\s*"(\d+)"', text) or _first(r'var\s+create_time\s*=\s*"(\d+)"', text)
    return {
        "title": (title or "").strip(),
        "ct": int(ct) if ct and ct.isdigit() else 0,
        "appmsg_token": _first(r'window\.appmsg_token\s*=\s*"([^"]*)"', text) or "",
        "has_content": 'id="js_content"' in text,
        "bar": parse_bar_data(text),
    }


def fetch_metrics_api(params: dict, cookie: str | None, url: str, appmsg_token: str = "") -> dict:
    """兜底：直接调 getappmsgext（返回 read_num / like_num / old_like_num）。"""
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    mid = (qs.get("mid") or [""])[0]
    sn = (qs.get("sn") or [""])[0]
    idx = (qs.get("idx") or ["1"])[0]
    if not (mid and sn):
        return {}
    q = {
        "f": "json", "mock": "", "appmsg_type": "9", "mid": mid, "sn": sn, "idx": idx,
        "scene": "27", "title": "", "ct": "", "abtest_cookie": "", "devicetype": "",
        "version": "", "lang": "zh_CN", "nettype": "WIFI", "ascene": "0", "fontScale": "100",
        "uin": params.get("uin", ""), "key": params.get("key", ""),
        "pass_ticket": params.get("pass_ticket", ""), "wx_header": "0",
        "appmsg_token": appmsg_token or params.get("appmsg_token", ""), "x5": "0",
    }
    u = "https://mp.weixin.qq.com/mp/getappmsgext?" + urllib.parse.urlencode(q)
    try:
        _, body = http_get(u, cookie, retries=0)
        d = json.loads(body)
    except Exception:
        return {}
    if (d.get("base_resp") or {}).get("ret") not in (0, None):
        return {}
    stat = d.get("appmsgstat") or {}
    return {
        "read_num": stat.get("read_num"),
        "like_num": stat.get("like_num"),
        "old_like_num": stat.get("old_like_num"),
    }


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, cfg: dict):
        self.id = uuid.uuid4().hex[:12]
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = "running"        # running / done / error
        self.phase = "准备"
        self.pages = 0
        self.found = 0
        self.metrics_done = 0
        self.metrics_total = 0
        self.rows: list[dict] = []
        self.errors: list[str] = []
        self.message = ""

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "state": self.state,
                "phase": self.phase,
                "pages": self.pages,
                "found": self.found,
                "metrics_done": self.metrics_done,
                "metrics_total": self.metrics_total,
                "rows": [dict(r) for r in self.rows],
                "errors": list(self.errors[-30:]),
                "error_count": len(self.errors),
                "message": self.message,
            }


def parse_range(cfg: dict) -> tuple[int, int]:
    def ts(v: str, end: bool) -> int:
        v = (v or "").strip()
        if not v:
            return 0
        d = datetime.datetime.strptime(v, "%Y-%m-%d")
        if end:
            d = d + datetime.timedelta(days=1)
        return int(d.timestamp())

    return ts(cfg.get("start", ""), False), ts(cfg.get("end", ""), True)


def run_job(job: Job) -> None:
    try:
        cred = parse_credential(job.cfg.get("credential", ""))
        params = cred["params"]
        cookie = cred["cookie"]
        biz = extract_biz(job.cfg.get("account", "")) or params.get("__biz")
        if not biz:
            raise RuntimeError("没解析出 __biz：请在「公众号」里粘贴该号任意一篇文章的链接")

        start_ts, end_ts = parse_range(job.cfg)
        job.phase = "拉取列表"

        articles: list[dict] = []
        seen: set[str] = set()
        offset = 0
        for page in range(1, MAX_PAGES + 1):
            url = build_list_url(params, biz, offset)
            _, body = http_get(url, cookie)
            data = parse_list_payload(body)
            items = data["items"]
            if not items:
                break

            stop = False
            for ts, ext in expand_items(items):
                if ts and start_ts and ts < start_ts:
                    stop = True
                    continue
                if ts and end_ts and ts >= end_ts:
                    continue
                link = normalize_url(ext.get("content_url") or "")
                if not link or link in seen:
                    continue
                seen.add(link)
                articles.append(
                    {
                        "ts": ts,
                        "title": (ext.get("title") or "").strip(),
                        "url": link,
                    }
                )

            job.pages = page
            job.found = len(articles)
            if stop or not data["can_continue"]:
                break
            offset += data["count"]
            time.sleep(PAGE_SLEEP)

        job.metrics_total = len(articles)
        job.phase = "抓取指标"

        sess_qs = urllib.parse.urlencode({
            "key": params.get("key", ""),
            "uin": params.get("uin", ""),
            "pass_ticket": params.get("pass_ticket", ""),
            "scene": "27",
        })

        def work(a: dict) -> dict:
            time.sleep(METRIC_SLEEP)
            like = collect = share = comment = None
            err = ""
            try:
                fetch_url = a["url"] + ("&" if "?" in a["url"] else "?") + sess_qs
                _, page_html = http_get(fetch_url, cookie, retries=1)
                info = parse_article(page_html)
                bar = info["bar"]
                # 页面底部栏：'赞' 按钮取 old_like_count，'在看/推荐' 按钮取 like_count
                like = bar.get("old_like_count")
                collect = bar.get("like_count")
                share = bar.get("share_count")
                comment = bar.get("comment_count")
                if like is None and collect is None:
                    alt = fetch_metrics_api(params, cookie, a["url"], info.get("appmsg_token", ""))
                    if alt:
                        like = alt.get("old_like_num") or alt.get("like_num")
                if not info["has_content"]:
                    err = "页面无正文（可能已删除/被拦截）"
            except Exception as e:  # noqa: BLE001
                err = str(e)[:120]
            return {
                "title": a["title"],
                "url": a["url"],
                "time": datetime.datetime.fromtimestamp(a["ts"]).strftime("%Y-%m-%d %H:%M:%S") if a["ts"] else "",
                "like": like,
                "collect": collect,
                "share": share,
                "comment": comment,
                "_ts": a["ts"],
                "_err": err,
            }

        with ThreadPoolExecutor(max_workers=METRIC_WORKERS) as pool:
            futures = [pool.submit(work, a) for a in articles]
            for fut in as_completed(futures):
                row = fut.result()
                with job.lock:
                    job.rows.append(row)
                    job.metrics_done += 1
                    if row["_err"]:
                        job.errors.append("%s：%s" % (row["title"][:24], row["_err"]))

        job.rows.sort(key=lambda r: r["_ts"], reverse=True)
        job.phase = "完成"
        job.state = "done"
        if job.metrics_total and all(
            r["like"] is None and r["collect"] is None and r["share"] is None and r["comment"] is None
            for r in job.rows
        ):
            job.message = "指标全为空：文章页没带上会话（cookie/key 过期），或该号已注销。"
    except Exception as e:  # noqa: BLE001
        with job.lock:
            job.state = "error"
            job.message = str(e)
            job.errors.append(str(e))


# --------------------------------------------------------------------------- #
# xlsx（零依赖的最小实现）
# --------------------------------------------------------------------------- #
def _xesc(s) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx_bytes(rows: list[list], sheet_name: str = "公众号数据") -> bytes:
    sheet_rows = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, val in enumerate(row):
            ref = "%s%d" % (_col_letter(c_idx), r_idx)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, val))
            else:
                txt = "" if val is None else str(val)
                if txt == "":
                    continue
                cells.append(
                    '<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>'
                    % (ref, ' s="1"' if r_idx == 1 else "", _xesc(txt))
                )
        sheet_rows.append('<row r="%d">%s</row>' % (r_idx, "".join(cells)))

    cols = "".join(
        '<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w)
        for i, w in enumerate([52, 72, 21, 8, 8, 8, 8][: max(1, len(rows[0]) if rows else len(COLUMNS))])
    )

    last_col = _col_letter((len(rows[0]) if rows else len(COLUMNS)) - 1)
    autofilter = '<autoFilter ref="A1:%s%d"/>' % (last_col, max(1, len(rows)))
    sheet = "".join(
        [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            "<cols>" + cols + "</cols>",
            "<sheetData>" + "".join(sheet_rows) + "</sheetData>",
            autofilter,
            "</worksheet>",
        ]
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % _xesc(sheet_name)
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="等线"/></font>'
        '<font><b/><sz val="11"/><name val="等线"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )

    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def csv_bytes(rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# --------------------------------------------------------------------------- #
# HTTP 服务
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "WXExport/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默
        pass

    # ---------- helpers ----------
    def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8", headers: dict | None = None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---------- routes ----------
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._serve_file("index.html")

        if path == "/api/status":
            job = JOBS.get((qs.get("job") or [""])[0])
            if not job:
                return self._json({"error": "任务不存在"}, 404)
            return self._json(job.snapshot())

        if path == "/api/capture":
            if not os.path.isfile(CAPTURE_FILE):
                return self._json({"error": "还没有抓取结果：先运行「抓取凭据.bat」"}, 404)
            try:
                with open(CAPTURE_FILE, "r", encoding="utf-8") as f:
                    cred = f.read().strip()
            except Exception as e:  # noqa: BLE001
                return self._json({"error": "读取失败：%s" % e}, 500)
            if not cred:
                return self._json({"error": "抓取结果为空，重新抓一次"}, 404)
            return self._json({
                "credential": cred,
                "mtime": datetime.datetime.fromtimestamp(os.path.getmtime(CAPTURE_FILE)).strftime("%Y-%m-%d %H:%M:%S"),
            })

        if path in ("/api/export.xlsx", "/api/export.csv"):
            job = JOBS.get((qs.get("job") or [""])[0])
            if not job:
                return self._json({"error": "任务不存在"}, 404)
            snap = job.snapshot()
            rows = [[r["title"], r["url"], r["time"], r["like"], r["collect"], r["share"], r["comment"]]
                    for r in snap["rows"]]
            data = [COLUMNS] + rows
            if path.endswith(".xlsx"):
                blob = xlsx_bytes(data)
                name = "公众号数据_%s.xlsx" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                blob = csv_bytes(data)
                name = "公众号数据_%s.csv" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                ctype = "text/csv; charset=utf-8"
            disp = "attachment; filename=\"export%s\"; filename*=UTF-8''%s" % (
                os.path.splitext(name)[1],
                urllib.parse.quote(name),
            )
            return self._send(200, blob, ctype, {"Content-Disposition": disp})

        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")

        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path != "/api/start":
            return self._json({"error": "not found"}, 404)

        cfg = self._body_json()
        if not (cfg.get("credential") or "").strip():
            return self._json({"error": "请先填写凭据"}, 400)
        if not (cfg.get("account") or "").strip() and not extract_biz(cfg.get("credential", "")):
            return self._json({"error": "请填写公众号（任意一篇文章链接）"}, 400)

        job = Job(cfg)
        with JOBS_LOCK:
            JOBS[job.id] = job
        threading.Thread(target=run_job, args=(job,), daemon=True).start()
        return self._json({"job": job.id})

    def _serve_file(self, name: str):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            return self._json({"error": "缺少 %s" % name}, 500)
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, "text/html; charset=utf-8")


def pick_port(start: int = 8765) -> int:
    for p in range(start, start + 30):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return 0


def main():
    port = pick_port(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765)
    if not port:
        print("没有可用端口")
        return
    url = "http://127.0.0.1:%d" % port
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print("=" * 56)
    print("  微信公众号文章导出  ->  %s" % url)
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
