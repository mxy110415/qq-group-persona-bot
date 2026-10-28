#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 可执行文件入口（PyInstaller 打包用）

Windows 上没有 GTK/WebKit，面板就用系统浏览器当界面：
  · 启动本地服务 127.0.0.1:6100
  · 自动打开默认浏览器
  · 运行数据（config / data / persona）放在 exe 旁边
"""

import os
import shutil
import sys
import threading
import time
import webbrowser

FROZEN = getattr(sys, "frozen", False)
# exe 所在目录：配置与数据都落在这里，升级覆盖 exe 不影响数据
BASE = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", BASE)

os.environ["QQBOT_ROOT"] = BASE


def bootstrap_data():
    """首次运行：把自带的示例配置和人格复制到 exe 旁边"""
    for sub in ("config", "data", "persona", "meme-pack"):
        os.makedirs(os.path.join(BASE, sub), exist_ok=True)

    # persona：没有就放一份默认的
    dst_persona = os.path.join(BASE, "persona", "persona.txt")
    src_persona = os.path.join(BUNDLE, "persona", "persona.txt")
    if not os.path.exists(dst_persona) and os.path.exists(src_persona):
        shutil.copy2(src_persona, dst_persona)

    # 配置：example → 真实配置
    for name in ("config", "whitelist", "group_configs"):
        dst = os.path.join(BASE, "config", name + ".json")
        src = os.path.join(BUNDLE, "config", name + ".example.json")
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy2(src, dst)


def open_browser_later():
    time.sleep(1.8)
    try:
        webbrowser.open("http://127.0.0.1:6100/")
    except Exception:
        pass


def main():
    bootstrap_data()
    threading.Thread(target=open_browser_later, daemon=True).start()
    entry = os.path.join(BUNDLE, "panel.py")
    import runpy
    sys.argv = [entry] + sys.argv[1:]
    runpy.run_path(entry, run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
