# -*- coding: utf-8 -*-
"""把系统代理还原：优先用抓取脚本留下的快照，没有快照就关掉代理。"""
import ctypes
import json
import os
import winreg

BASE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.path.join(BASE, "snapshot.json")
IE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def write(name, value):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IE_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if value is None:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
        elif isinstance(value, int):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
        else:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def notify():
    try:
        w = ctypes.windll.wininet
        w.InternetSetOptionW(None, 39, None, 0)
        w.InternetSetOptionW(None, 37, None, 0)
    except Exception:
        pass


def main():
    if os.path.exists(SNAPSHOT):
        with open(SNAPSHOT, encoding="utf-8") as f:
            old = json.load(f)
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
            write(name, old.get(name))
        os.remove(SNAPSHOT)
        print("已还原到抓取前的代理设置。")
    else:
        write("ProxyEnable", 0)
        write("ProxyServer", None)
        write("ProxyOverride", None)
        print("已关闭系统代理（ProxyEnable=0）。")
    notify()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("还原失败：%s" % e)
        print("可以手动操作：设置 -> 网络和 Internet -> 代理 -> 关掉“使用代理服务器”")
