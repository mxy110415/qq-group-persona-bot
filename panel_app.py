#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 群聊人格机器人 · 调试面板（桌面应用外壳）

用 GTK + WebKit2 把本地面板包成一个原生窗口，看起来就是个软件，
不是「打开浏览器跳网页」。

依赖：python3-gi / gir1.2-webkit2-4.1（Linux Mint 自带）
"""

import os
import socket
import subprocess
import sys
import time

PANEL_URL = os.environ.get("PANEL_URL", "http://127.0.0.1:6100")
SERVICE = "qqbot-panel.service"


def port_open(port, host="127.0.0.1", timeout=0.3):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def ensure_backend():
    """面板服务没起来就先拉起来（systemd 用户服务）"""
    if port_open(6100):
        return
    try:
        subprocess.run(["systemctl", "--user", "start", SERVICE],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    for _ in range(40):
        if port_open(6100):
            return
        time.sleep(0.25)


def main():
    ensure_backend()

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2, GLib

    win = Gtk.Window(title="QQ 群聊人格机器人 · 调试面板")
    win.set_default_size(1120, 840)
    win.set_position(Gtk.WindowPosition.CENTER)

    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_property("enable-javascript", True)
    settings.set_property("enable-developer-extras", True)
    view.load_uri(PANEL_URL)
    win.add(view)

    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("启动失败:", e, file=sys.stderr)
        # 退路：用默认浏览器打开
        try:
            subprocess.Popen(["xdg-open", PANEL_URL])
        except Exception:
            pass
        sys.exit(1)
