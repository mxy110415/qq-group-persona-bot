#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 群聊人格机器人 · 启动器

双击桌面图标时先跑这个：
  1. 检测本地是否已经下载了面板程序
  2. 没下载 → 弹窗让你选安装位置、确认是否下载，然后下
  3. 下载完成 → 装依赖 → 打开调试面板

命令行：
  python3 launcher.py                      # 图形界面
  python3 launcher.py --dir ~/qqbot --yes  # 无需交互，直接下到指定目录
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request

REPO = "mxy110415/qq-group-persona-bot"
BRANCH = "main"
TARBALL = "https://codeload.github.com/%s/tar.gz/refs/heads/%s" % (REPO, BRANCH)
CONF = os.path.expanduser("~/.config/qqbot-launcher.json")
DEFAULT_DIR = os.path.expanduser("~/qq-group-persona-bot")
NEED_FILES = ("panel.py", "bot.py", "persona/persona.txt")


# ---------------------------------------------------------------- 工具
def load_conf():
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_conf(d):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_installed(path):
    return all(os.path.exists(os.path.join(path, f)) for f in NEED_FILES)


def proxy_candidates():
    """依次尝试：直连 → 本机常见代理"""
    return [None, "http://127.0.0.1:7897", "http://127.0.0.1:7890"]


def download_to(path, progress=None):
    """下载仓库 tarball 并解压到 path（覆盖空目录）"""
    os.makedirs(path, exist_ok=True)
    last = None
    for proxy in proxy_candidates():
        try:
            handler = urllib.request.ProxyHandler({} if proxy is None else {"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(TARBALL, headers={"User-Agent": "qqbot-launcher"})
            with opener.open(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0)
                buf = bytearray()
                while True:
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if progress and total:
                        progress(len(buf) / total * 0.75)
            if progress:
                progress(0.78)
            with tempfile.TemporaryDirectory() as tmp:
                fp = os.path.join(tmp, "src.tar.gz")
                with open(fp, "wb") as f:
                    f.write(buf)
                with tarfile.open(fp, "r:gz") as tf:
                    tf.extractall(tmp)
                inner = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
                if not inner:
                    raise RuntimeError("压缩包结构异常")
                src = os.path.join(tmp, inner[0])
                for name in os.listdir(src):
                    s = os.path.join(src, name)
                    d = os.path.join(path, name)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            return True, "下载完成"
        except Exception as e:
            last = e
            continue
    return False, "下载失败：%s" % last


def ensure_deps(path, log=None):
    """建 venv 并装 websockets（面板本身只用标准库，bot 需要 websockets）"""
    venv = os.path.join(path, ".venv")
    py = os.path.join(venv, "bin", "python")
    if os.name == "nt":
        py = os.path.join(venv, "Scripts", "python.exe")
    if not os.path.exists(py):
        if log:
            log("创建虚拟环境…")
        subprocess.run([sys.executable, "-m", "venv", venv], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(py):
        if log:
            log("安装依赖 websockets…")
        for proxy in proxy_candidates():
            cmd = [py, "-m", "pip", "install", "-q", "--upgrade", "pip"]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cmd = [py, "-m", "pip", "install", "-q", "websockets"]
            if proxy:
                cmd += ["--proxy", proxy]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode == 0:
                return True, "依赖就绪"
        return False, "依赖安装失败（可稍后手动 pip install websockets）"
    return False, "虚拟环境创建失败（Debian/Ubuntu 需要 python3-venv）"


def init_configs(path):
    """从示例生成真实配置（已存在则跳过）"""
    pairs = [
        ("config/config.example.json", "config/config.json"),
        ("config/whitelist.example.json", "config/whitelist.json"),
        ("config/group_configs.example.json", "config/group_configs.json"),
    ]
    made = []
    for src, dst in pairs:
        sp, dp = os.path.join(path, src), os.path.join(path, dst)
        if not os.path.exists(dp) and os.path.exists(sp):
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
            made.append(dst)
    for dst in made:
        try:
            os.chmod(os.path.join(path, dst), 0o600)
        except Exception:
            pass
    return made


def python_for(path):
    venv = os.path.join(path, ".venv", "bin", "python")
    if os.name == "nt":
        venv = os.path.join(path, ".venv", "Scripts", "python.exe")
    return venv if os.path.exists(venv) else sys.executable


def launch_panel(path):
    env = dict(os.environ, QQBOT_HOME=path)
    subprocess.Popen([python_for(path), os.path.join(path, "panel.py"), "--port", "6100"],
                     cwd=path, env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # 有图形环境才开窗口（纯 CLI 服务器只跑后台面板）
    if os.name == "nt" or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        subprocess.Popen([sys.executable, os.path.join(path, "panel_app.py")],
                         cwd=path, env=env)


# ---------------------------------------------------------------- 图形界面
def gui():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib

    conf = load_conf()
    start_dir = conf.get("install_dir") or DEFAULT_DIR

    win = Gtk.Window(title="QQ 群聊人格机器人 · 启动器")
    win.set_default_size(560, 320)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_border_width(20)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    win.add(box)

    title = Gtk.Label()
    title.set_markup("<b>面板程序尚未下载</b>")
    title.set_xalign(0)
    box.pack_start(title, False, False, 0)

    hint = Gtk.Label(label="选择安装位置，点「下载并启动」会从 GitHub 拉取面板程序并安装依赖。")
    hint.set_line_wrap(True)
    hint.set_xalign(0)
    box.pack_start(hint, False, False, 0)

    row = Gtk.Box(spacing=8)
    entry = Gtk.Entry()
    entry.set_text(start_dir)
    btn_browse = Gtk.Button(label="浏览…")
    row.pack_start(entry, True, True, 0)
    row.pack_start(btn_browse, False, False, 0)
    box.pack_start(row, False, False, 0)

    chk = Gtk.CheckButton(label="下载完成后安装依赖（推荐，bot 需要 websockets）")
    chk.set_active(True)
    box.pack_start(chk, False, False, 0)

    status = Gtk.Label(label="")
    status.set_xalign(0)
    status.set_line_wrap(True)
    box.pack_start(status, False, False, 0)

    bar = Gtk.ProgressBar()
    bar.set_show_text(True)
    box.pack_start(bar, False, False, 0)

    btns = Gtk.Box(spacing=8)
    btn_go = Gtk.Button(label="下载并启动")
    btn_quit = Gtk.Button(label="退出")
    btns.pack_end(btn_go, False, False, 0)
    btns.pack_end(btn_quit, False, False, 0)
    box.pack_end(btns, False, False, 0)

    def on_browse(_):
        d = Gtk.FileChooserDialog(title="选择安装目录", parent=win,
                                  action=Gtk.FileChooserAction.SELECT_FOLDER)
        d.add_buttons("取消", Gtk.ResponseType.CANCEL, "选择", Gtk.ResponseType.OK)
        d.set_current_folder(os.path.expanduser("~"))
        if d.run() == Gtk.ResponseType.OK:
            entry.set_text(d.get_filename())
        d.destroy()

    def set_status(msg):
        def _():
            status.set_text(msg)
            return False
        GLib.idle_add(_)

    def set_progress(v):
        def _():
            bar.set_fraction(v)
            bar.set_text("%d%%" % int(v * 100))
            return False
        GLib.idle_add(_)

    def work(path):
        ok, msg = download_to(path, progress=set_progress)
        if not ok:
            set_status(msg); return
        set_progress(0.8)
        init_configs(path)
        if chk.get_active():
            ensure_deps(path, log=set_status)
        set_progress(0.95)
        save_conf({"install_dir": path})
        set_status("安装完成，正在启动面板…")
        set_progress(1.0)
        launch_panel(path)
        GLib.idle_add(lambda: (win.close(), False)[1])

    def on_go(_):
        path = os.path.expanduser(entry.get_text().strip() or DEFAULT_DIR)
        btn_go.set_sensitive(False)
        set_status("开始下载…")
        threading.Thread(target=work, args=(path,), daemon=True).start()

    btn_browse.connect("clicked", on_browse)
    btn_go.connect("clicked", on_go)
    btn_quit.connect("clicked", lambda *_: win.close())
    win.connect("destroy", Gtk.main_quit)

    # 已经装过就直接开
    if is_installed(start_dir):
        launch_panel(start_dir)
        win.close()
        Gtk.main()
        return

    win.show_all()
    Gtk.main()


# ---------------------------------------------------------------- CLI
def cli(path, yes):
    path = os.path.expanduser(path)
    if is_installed(path):
        print("已安装：", path)
    else:
        print("下载到", path)
        ok, msg = download_to(path, progress=lambda p: print("\r  %d%%" % int(p * 100), end="", flush=True))
        print()
        if not ok:
            print(msg); return 1
    made = init_configs(path)
    if made:
        print("生成配置：", ", ".join(made))
    ok, msg = ensure_deps(path, log=lambda m: print(" ", m))
    print(" ", msg)
    save_conf({"install_dir": path})
    launch_panel(path)
    print("面板已启动： http://127.0.0.1:6100")
    print()
    print("纯命令行服务器（没有桌面）可以这样常驻：")
    print("  cd %s" % path)
    print("  nohup ./.venv/bin/python panel.py --port 6100 > panel.log 2>&1 &")
    print("  nohup ./.venv/bin/python bot.py > bot.log 2>&1 &")
    print("或者用仓库里的 deploy/*.service 装成 systemd 服务。")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.yes:
        sys.exit(cli(a.dir or DEFAULT_DIR, True))
    gui()
