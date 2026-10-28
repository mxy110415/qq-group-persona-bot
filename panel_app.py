#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 群聊人格机器人 · 调试面板（桌面应用外壳）

用 GTK + WebKit2 把本地面板包成一个原生窗口：
  · 自绘标题栏（HeaderBar），跟随面板主题深/浅
  · 面板里切换主题会写 data/.theme，这里每 1.5 秒读一次并同步标题栏

依赖：python3-gi / gir1.2-webkit2-4.1（Linux Mint 自带）
"""

import os
import socket
import subprocess
import sys
import time

# 老显卡/核显上 WebKitGTK 的合成器会明显掉帧，关掉后滚动和重绘更顺
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("WEBKIT_FORCE_SANDBOX", "0")

PANEL_URL = os.environ.get("PANEL_URL", "http://127.0.0.1:6100")
SERVICE = "qqbot-panel.service"
BASE = os.path.dirname(os.path.abspath(__file__))
THEME_FILE = os.path.join(BASE, "data", ".theme")


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


def read_theme():
    try:
        with open(THEME_FILE, encoding="utf-8") as f:
            return f.read().strip() or "system"
    except Exception:
        return "system"


def system_prefers_dark():
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=4).stdout
        if "dark" in out.lower():
            return True
        if "light" in out.lower():
            return False
    except Exception:
        pass
    try:
        out = subprocess.run(["gsettings", "get", "org.cinnamon.desktop.interface", "gtk-theme"],
                             capture_output=True, text=True, timeout=4).stdout.lower()
        if "dark" in out:
            return True
        if "light" in out:
            return False
    except Exception:
        pass
    return True


def main():
    ensure_backend()

    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2, GLib

    gtk_settings = Gtk.Settings.get_default()

    def apply_theme():
        t = read_theme()
        dark = (t == "dark") or (t == "system" and system_prefers_dark())
        try:
            gtk_settings.set_property("gtk-application-prefer-dark-theme", bool(dark))
        except Exception:
            pass
        return True

    apply_theme()

    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    header.set_title("QQ 群聊人格机器人 · 调试面板")
    header.set_subtitle("独立面板进程")

    win = Gtk.Window()
    win.set_titlebar(header)
    win.set_default_size(1080, 800)
    win.set_position(Gtk.WindowPosition.CENTER)

    view = WebKit2.WebView()
    st = view.get_settings()
    st.set_property("enable-javascript", True)
    st.set_property("enable-developer-extras", True)
    view.load_uri(PANEL_URL)
    win.add(view)

    win.connect("destroy", Gtk.main_quit)
    win.show_all()

    # 面板里切了主题 → 标题栏跟着变
    GLib.timeout_add(1500, apply_theme)

    Gtk.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("启动失败:", e, file=sys.stderr)
        try:
            subprocess.Popen(["xdg-open", PANEL_URL])
        except Exception:
            pass
        sys.exit(1)
