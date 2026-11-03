# -*- coding: utf-8 -*-
"""wxcapture - 抓取微信公众号会话凭据（本地 MITM，仅拦截 mp.weixin.qq.com）

用法：
    python capture_proxy.py            # 正常使用（会装证书、改系统代理）
    python capture_proxy.py --no-install --no-proxy --port=8899   # 本地测试
"""
import ctypes
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import winreg

BASE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(BASE, "certs")
CA_CRT = os.path.join(CERT_DIR, "ca.crt")
SERVER_CRT = os.path.join(CERT_DIR, "server.crt")
SERVER_KEY = os.path.join(CERT_DIR, "server.key")
OUT_FILE = os.path.join(os.path.dirname(BASE), "credential.txt")

TARGET = "mp.weixin.qq.com"
IE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"

PORT = 8898
NO_INSTALL = False
NO_PROXY = False
captured = threading.Event()
url_holder = {"url": None}
lock = threading.Lock()
tried = set()
probed = set()
session = {}
last_biz = {"v": None}
conn_count = {"n": 0}
LOG_PATH = os.path.join(BASE, "capture_run.log")
SNAPSHOT = os.path.join(BASE, "snapshot.json")


def log(msg):
    line = time.strftime("%H:%M:%S") + "  " + msg
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        try:
            chunk = sock.recv(1)
        except Exception:
            return None
        if not chunk:
            return data or None
        data += chunk
    return data


def gen_certs():
    exe = os.path.join(BASE, "gencert.exe")
    if not os.path.exists(exe):
        return False
    log("生成证书...")
    subprocess.run([exe, CERT_DIR], check=False)
    return os.path.exists(SERVER_CRT) and os.path.exists(SERVER_KEY)


def install_ca():
    if NO_INSTALL:
        log("--no-install：跳过证书安装")
        return True
    log("[2/5] 安装根证书：如果弹出「是否安装证书」，请点『是』（只需一次）")
    try:
        r = subprocess.run(["certutil", "-addstore", "-user", "Root", CA_CRT],
                           capture_output=True, text=True, timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 or "已存在" in out or "already" in out.lower():
            log("      根证书已就绪")
            return True
        log("      certutil 返回（%s）：%s" % (r.returncode, out.strip()[:300]))
        log("      证书没装上，微信会拒绝连接；多半是弹框被取消了")
        return False
    except subprocess.TimeoutExpired:
        log("      等待证书确认超时（弹框可能被挡住或没点）")
        return False
    except Exception as e:
        log("      安装证书失败：%s" % e)
        return False


# ---------------------------------------------------------------- 系统代理
def read_proxy():
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_READ) as k:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
            try:
                out[name], _ = winreg.QueryValueEx(k, name)
            except FileNotFoundError:
                out[name] = None
    return out


def write_proxy(vals):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for name, v in vals.items():
            if v is None:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
            elif isinstance(v, int):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, v)
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, v)


def notify():
    try:
        w = ctypes.windll.wininet
        w.InternetSetOptionW(None, 39, None, 0)
        w.InternetSetOptionW(None, 37, None, 0)
    except Exception:
        pass


def apply_proxy():
    try:
        write_proxy({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:%d" % PORT, "ProxyOverride": "<local>"})
        notify()
        log("      系统代理已指向 127.0.0.1:%d" % PORT)
        return True
    except Exception as e:
        log("      无法设置系统代理：%s" % e)
        return False


def save_snapshot(vals):
    try:
        with open(SNAPSHOT, "w", encoding="utf-8") as f:
            json.dump(vals, f, ensure_ascii=False)
    except Exception as e:
        log("写快照失败：%s" % e)


def restore_snapshot():
    if not os.path.exists(SNAPSHOT):
        return False
    ok = False
    try:
        with open(SNAPSHOT, encoding="utf-8") as f:
            old = json.load(f)
        write_proxy({k: old.get(k) for k in ("ProxyEnable", "ProxyServer", "ProxyOverride")})
        notify()
        log("已按上次快照还原系统代理")
        ok = True
    except Exception as e:
        log("还原快照失败：%s" % e)
    if ok:
        try:
            os.remove(SNAPSHOT)
        except Exception:
            pass
    return ok


# ---------------------------------------------------------------- 代理逻辑
def pipe(src, dst, scan=False):
    tail = ""
    seen = set()
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
            if scan and not captured.is_set():
                tail += data.decode("latin-1", "replace")
                if len(tail) > 131072:
                    tail = tail[-65536:]
                while "\r\n\r\n" in tail:
                    head, tail = tail.split("\r\n\r\n", 1)
                    process_head(head, seen)
    except Exception:
        pass
    finally:
        for s in (dst, src):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def process_head(head, seen):
    lines = head.split("\r\n")
    if not lines:
        return
    parts = lines[0].split(" ")
    if len(parts) < 2:
        return
    path = parts[1]
    if path in seen:
        return
    seen.add(path)
    if len(seen) <= 200:
        log("      REQ " + path[:110])

    q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query, keep_blank_values=True)

    def g(name):
        return (q.get(name) or [""])[0]

    if g("__biz"):
        last_biz["v"] = g("__biz")
    elif g("biz"):
        last_biz["v"] = g("biz")

    if g("key") and g("uin"):
        for k in ("uin", "key", "pass_ticket", "appmsg_token", "scene", "wxtoken"):
            if g(k):
                session[k] = g(k)
        for line in lines[1:]:
            if line.lower().startswith("cookie:"):
                session["cookie"] = line.split(":", 1)[1].strip()
        threading.Thread(target=try_probe, daemon=True).start()


def try_probe():
    key = session.get("key")
    biz = last_biz["v"]
    if not key or not biz:
        return
    if key in probed:
        return
    probed.add(key)
    url = ("https://" + TARGET + "/mp/profile_ext?action=getmsg&__biz=" + urllib.parse.quote(biz) +
           "&uin=" + urllib.parse.quote(session.get("uin", "")) +
           "&key=" + urllib.parse.quote(key) +
           "&pass_ticket=" + urllib.parse.quote(session.get("pass_ticket", "")) +
           "&appmsg_token=" + urllib.parse.quote(session.get("appmsg_token", "")) +
           "&scene=" + urllib.parse.quote(session.get("scene", "124")))
    if probe_key(url):
        capture(url)
    else:
        log("      拿到 key/uin，但验证未通过（可能过期），继续等下一个")


def probe_key(url):
    """用抓到的 key 真发一次 getmsg，ret==0 才算有效。"""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)

        def g(name, default=""):
            return (q.get(name) or [default])[0]

        if not g("__biz") or not g("key"):
            return False
        params = {
            "action": "getmsg", "__biz": g("__biz"), "f": "json", "offset": "0", "count": "1",
            "is_ok": "1", "scene": g("scene", "124"), "uin": g("uin"), "key": g("key"),
            "pass_ticket": g("pass_ticket"), "wxtoken": g("wxtoken"),
            "appmsg_token": g("appmsg_token"), "x5": "0",
        }
        check = "https://mp.weixin.qq.com/mp/profile_ext?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(check, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                          "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.47 NetType/WIFI Language/zh_CN",
            "Accept": "application/json, text/plain, */*",
        })
        if session.get("cookie"):
            req.add_header("Cookie", session["cookie"])
        # 必须直连，不能走我们自己刚设的系统代理（否则会绕回自身）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as r:
            body = r.read().decode("utf-8", "replace")
        d = json.loads(body)
        ok = d.get("ret") == 0 and bool(d.get("general_msg_list"))
        if not ok:
            log("  验证返回：ret=%s errmsg=%s" % (d.get("ret"), d.get("errmsg")))
        return ok
    except Exception as e:
        log("  验证失败：%s" % e)
        return False


def capture(url):
    with lock:
        if captured.is_set():
            return
        url_holder["url"] = url
        captured.set()
    payload = url
    if session.get("cookie"):
        payload = url + "\nCookie: " + session["cookie"]
    try:
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            f.write(payload)
    except Exception as e:
        log("写入凭据失败：%s" % e)
    print()
    print("=" * 70)
    print("已捕获凭据：")
    print("  " + url)
    print("已写入：" + OUT_FILE)
    print("=" * 70)
    print(flush=True)


def tls_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(SERVER_CRT, SERVER_KEY)
    try:
        ctx.set_alpn_protocols(["http/1.1"])
    except Exception:
        pass
    return ctx


def handle(conn, ctx):
    try:
        conn.settimeout(30)
        head = read_head(conn)
        if not head:
            return
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = first.split(" ")
        if len(parts) < 2:
            return
        method, target = parts[0].upper(), parts[1]
        conn_count["n"] += 1
        if conn_count["n"] <= 400:
            log("CONNECT %s" % target[:80])

        if method != "CONNECT":
            conn.sendall(b"HTTP/1.1 405 Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return

        host, _, port_s = target.partition(":")
        port = int(port_s or 443)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

        if host.lower() != TARGET:
            up = socket.create_connection((host, port), timeout=20)
            threading.Thread(target=pipe, args=(conn, up, False), daemon=True).start()
            pipe(up, conn, False)
            return

        log("    MITM " + host)
        try:
            ss = ctx.wrap_socket(conn, server_side=True)
        except Exception as e:
            log("    TLS 握手失败：%s" % e)
            return
        log("    TLS ok %s alpn=%s" % (ss.version(), ss.selected_alpn_protocol()))

        up_raw = socket.create_connection((host, port), timeout=20)
        up = ssl.create_default_context().wrap_socket(up_raw, server_hostname=host)
        threading.Thread(target=pipe, args=(ss, up, True), daemon=True).start()
        pipe(up, ss, False)
    except Exception as e:
        log("连接异常：%s" % e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def serve(srv, ctx):
    while True:
        try:
            c, _ = srv.accept()
        except Exception:
            break
        threading.Thread(target=handle, args=(c, ctx), daemon=True).start()


def make_listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", PORT))
        srv.listen(128)
    except OSError as e:
        log("[!] 端口 %d 无法监听：%s" % (PORT, e))
        log("    多半是上一个「抓取凭据」窗口还开着，先关掉它再运行")
        return None
    return srv


def main():
    global PORT, NO_INSTALL, NO_PROXY
    for a in sys.argv[1:]:
        if a == "--no-install":
            NO_INSTALL = True
        elif a == "--no-proxy":
            NO_PROXY = True
        elif a.startswith("--port="):
            PORT = int(a.split("=", 1)[1])

    try:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("wxcapture %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass

    log("[1/5] 检查证书")
    if os.path.exists(SNAPSHOT):
        log("[0/5] 发现上次残留的代理设置，先还原")
        restore_snapshot()
    if not (os.path.exists(SERVER_CRT) and os.path.exists(SERVER_KEY)):
        if not gen_certs():
            log("缺少证书且无法生成，退出。")
            return 1
    log("      证书 OK")

    ok_ca = install_ca()

    log("[3/5] 启动本地代理")
    srv = make_listener()
    if srv is None:
        return 3
    ctx = tls_context()
    threading.Thread(target=serve, args=(srv, ctx), daemon=True).start()

    old = None
    applied = False
    if not NO_PROXY:
        log("[4/5] 设置系统代理")
        try:
            old = read_proxy()
            cur = (old.get("ProxyServer") or "").strip().lower()
            if cur.startswith("127.0.0.1:88"):
                log("      当前代理指向本工具的旧实例，按“不使用代理”处理")
                old = {"ProxyEnable": 0, "ProxyServer": None, "ProxyOverride": None}
            save_snapshot(old)
            applied = apply_proxy()
        except Exception as e:
            log("      读取系统代理失败：%s" % e)
    else:
        log("[4/5] --no-proxy，跳过系统代理")

    log("[5/5] 等待抓取")
    log("      1. 先完全退出微信（托盘图标右键 -> 退出）")
    log("      2. 确认本窗口已经显示到这一行，再重新打开微信")
    log("      3. 微信里打开目标公众号的任意一篇文章（不一定要点历史消息）")
    if not ok_ca:
        log("      ⚠ 证书没装上：微信里打开文章会报错，请关掉本窗口重跑并点『是』")

    def heartbeat():
        last = 0
        warned = False
        while not captured.is_set():
            time.sleep(30)
            n = conn_count["n"]
            log("心跳：累计 %d 个连接" % n)
            if n == 0 and not warned:
                warned = True
                log("提示：30 秒内没有任何代理连接。微信必须在本窗口启动之后再打开，才会走新代理。")

    threading.Thread(target=heartbeat, daemon=True).start()

    try:
        ok = captured.wait(timeout=900)
    except KeyboardInterrupt:
        ok = False
    finally:
        if applied and old is not None:
            try:
                write_proxy(old)
                notify()
                try:
                    os.remove(SNAPSHOT)
                except Exception:
                    pass
                log("系统代理已还原")
            except Exception as e:
                log("还原代理失败：%s" % e)

    if not ok:
        log("超时/取消，没有抓到有效凭据。")
        return 2
    log("已捕获，3 秒后退出（可关闭本窗口）")
    time.sleep(3.0)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
