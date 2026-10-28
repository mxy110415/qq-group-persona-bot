#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 群聊人格机器人 · 调试面板

只用标准库，Linux / Windows 都能跑：
    python panel.py            # 默认 http://127.0.0.1:6100
    python panel.py --port 6101

功能：
  · 登录状态：NapCat 端口、机器人进程、最近日志
  · 二维码：直接显示 NapCat 生成的登录二维码，自动刷新
  · 参数调试：插话概率、冷却秒数、每小时上限、静默时段、触发词
  · 白名单：群号 / 好友号增删
  · 人格：直接编辑 persona/persona.txt，保存并重载
  · 多开：多个实例（各自独立的配置/人格/数据目录），逐个启动停止

安全：默认只绑 127.0.0.1；要对外必须先在本机反代加鉴权。
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(ROOT, "bot.py")
INSTANCES_FILE = os.path.join(ROOT, "instances.json")
PY = sys.executable

_procs = {}          # name -> Popen
_lock = threading.Lock()


# ---------------------------------------------------------------- 实例
def default_instances():
    return {"instances": [
        {"name": "主实例", "home": ".", "ws": "ws://127.0.0.1:3000", "qr": "QQn/resources/app/napcat/cache/qrcode.png"}
    ]}


def load_instances():
    if not os.path.exists(INSTANCES_FILE):
        data = default_instances()
        save_instances(data)
        return data
    try:
        with open(INSTANCES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_instances()


def save_instances(data):
    tmp = INSTANCES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INSTANCES_FILE)


def instance_home(inst):
    home = inst.get("home") or "."
    return os.path.abspath(os.path.join(ROOT, home))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def port_open(host, port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def proc_alive(p):
    return p is not None and p.poll() is None


def detect_bot_pids(home):
    """检测由 systemd / 手动启动的外部 bot.py 进程（跨平台）"""
    pids = []
    home = os.path.abspath(home)
    if os.name == "nt":
        try:
            out = subprocess.run(["wmic", "process", "where", "name='python.exe'", "get",
                                  "ProcessId,CommandLine", "/format:csv"],
                                 capture_output=True, text=True, timeout=6).stdout
            for line in out.splitlines():
                if "bot.py" in line:
                    part = line.strip().rsplit(",", 1)
                    if part[-1].isdigit():
                        pids.append(int(part[-1]))
        except Exception:
            pass
        return pids
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
                if "bot.py" not in cmd:
                    continue
                cwd = os.readlink("/proc/%s/cwd" % pid)
                if os.path.abspath(cwd) == home:
                    pids.append(int(pid))
            except Exception:
                continue
    except Exception:
        pass
    return pids


def instance_state(inst):
    home = instance_home(inst)
    cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
    wl = read_json(os.path.join(home, "config", "whitelist.json"), {}) or {}
    qr_rel = inst.get("qr") or "QQn/resources/app/napcat/cache/qrcode.png"
    qr = os.path.abspath(os.path.join(ROOT, qr_rel))
    gm = cfg.get("group_mode", {}) or {}
    p = _procs.get(inst["name"])
    ext = detect_bot_pids(home)
    running = proc_alive(p) or bool(ext)
    return {
        "name": inst["name"],
        "home": home,
        "ws": inst.get("ws"),
        "ws_ok": bool(inst.get("ws")) and port_open("127.0.0.1", int(re.findall(r":(\d+)", inst["ws"])[0])) if inst.get("ws") else False,
        "bot_running": running,
        "bot_pid": p.pid if proc_alive(p) else (ext[0] if ext else None),
        "bot_external": bool(ext) and not proc_alive(p),
        "owner_id": cfg.get("owner_id", ""),
        "owner_name": cfg.get("owner_name", ""),
        "model": (cfg.get("api") or {}).get("model", ""),
        "group_mode": {
            "chance": gm.get("chance", 0),
            "cooldown": [gm.get("min_gap_s", 900), gm.get("max_gap_s", 2700)],
            "hourly_limit": gm.get("max_per_hour", 3),
            "quiet_hours": gm.get("quiet_hours", []),
        },
        "name_triggers": cfg.get("name_triggers", []),
        "owner_only_triggers": cfg.get("owner_only_triggers", []),
        "groups": wl.get("groups", []),
        "friends": wl.get("friends", []),
        "qr_path": qr,
        "qr_mtime": int(os.path.getmtime(qr)) if os.path.exists(qr) else 0,
    }


# ---------------------------------------------------------------- 进程
def start_instance(name):
    insts = {i["name"]: i for i in load_instances()["instances"]}
    inst = insts.get(name)
    if not inst:
        return False, "实例不存在"
    with _lock:
        if proc_alive(_procs.get(name)):
            return True, "已在运行"
        home = instance_home(inst)
        if not os.path.exists(os.path.join(home, "config", "config.json")):
            return False, "缺配置：%s/config/config.json" % home
        env = dict(os.environ)
        env["QQBOT_HOME"] = home
        env["PYTHONUNBUFFERED"] = "1"
        log = open(os.path.join(home, "panel-bot.log"), "ab")
        p = subprocess.Popen([PY, BOT], cwd=home, env=env, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        _procs[name] = p
        return True, "已启动 PID %d" % p.pid


def stop_instance(name):
    with _lock:
        p = _procs.get(name)
        if not proc_alive(p):
            _procs.pop(name, None)
            return True, "未在运行"
        try:
            p.terminate()
            p.wait(timeout=10)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        _procs.pop(name, None)
        return True, "已停止"


def tail(path, n=120):
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", "ignore").splitlines()
        return "\n".join(data[-n:])
    except Exception as e:
        return "读取日志失败: %s" % e


# ---------------------------------------------------------------- 设置
SETTINGS_FILE = os.path.join(ROOT, "panel-settings.json")
DEFAULT_SETTINGS = {
    "auto_update": "off",      # off | check | apply
    "interval_hours": 6,
    "instances_dir": "instances",
}
_last_check = {"time": 0, "result": ""}


def load_settings():
    d = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d.update(json.load(f))
    except Exception:
        pass
    return d


def save_settings(d):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def fetch_url(url, timeout=20, headers=None):
    """先直连；失败则试本机常见代理端口（7897/7890）。返回 bytes。"""
    import urllib.request
    hdrs = headers or {"User-Agent": "qqbot-panel"}
    last = None
    for proxy in (None, "http://127.0.0.1:7897", "http://127.0.0.1:7890"):
        if proxy and not port_open("127.0.0.1", int(proxy.rsplit(":", 1)[1])):
            continue
        try:
            handler = urllib.request.ProxyHandler({} if proxy is None else {"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(handler)
            req = urllib.request.Request(url, headers=hdrs)
            with opener.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            continue
    raise last if last else RuntimeError("unknown error")


def paths_info():
    st = load_settings()
    inst_dir = os.path.join(ROOT, st.get("instances_dir") or "instances")
    return {
        "root": ROOT,
        "config": os.path.join(ROOT, "config"),
        "persona": os.path.join(ROOT, "persona"),
        "data": os.path.join(ROOT, "data"),
        "instances": os.path.abspath(inst_dir),
        "settings": SETTINGS_FILE,
        "version": VERSION_FILE,
        "autostart_unit": (os.path.expanduser("~/.config/systemd/user/qqbot-panel.service")
                           if os.name != "nt" else "启动文件夹快捷方式"),
        "note": "全部路径固定，由脚本所在目录决定；实例目录可在下方修改",
    }


def auto_update_loop():
    while True:
        st = load_settings()
        mode = st.get("auto_update", "off")
        interval = max(1, int(st.get("interval_hours", 6) or 6)) * 3600
        time.sleep(60)
        if mode == "off":
            continue
        if time.time() - _last_check["time"] < interval:
            continue
        rv = remote_version()
        _last_check["time"] = time.time()
        if rv.get("error"):
            _last_check["result"] = "检查失败: " + rv["error"]
            continue
        lv = local_version()
        if rv["sha"] == lv:
            _last_check["result"] = "已是最新 (%s)" % lv
            continue
        if mode == "check":
            _last_check["result"] = "发现新版本 %s（仅提醒，未自动安装）" % rv["sha"]
            continue
        try:
            files = apply_update(rv["sha"])
            _last_check["result"] = "已自动更新到 %s（%d 个文件），重启后生效" % (rv["sha"], len(files))
        except Exception as e:
            _last_check["result"] = "自动更新失败: " + str(e)[:120]


# ---------------------------------------------------------------- 自启 / 更新
REPO = "mxy110415/qq-group-persona-bot"
BRANCH = "main"
VERSION_FILE = os.path.join(ROOT, "data", ".version")
UPDATE_FILES = ["bot.py", "panel.py", "README.md", "LICENSE", "deploy", "meme-pack/README.md"]


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def autostart_state():
    if os.name == "nt":
        startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        lnk = os.path.join(startup, "qqbot-panel.lnk")
        return {"supported": True, "enabled": os.path.exists(lnk), "detail": "启动文件夹快捷方式"}
    unit = os.path.expanduser("~/.config/systemd/user/qqbot-panel.service")
    enabled = False
    if os.path.exists(unit):
        code, out = _run(["systemctl", "--user", "is-enabled", "qqbot-panel.service"])
        enabled = code == 0 and "enabled" in out
    return {"supported": True, "enabled": enabled, "detail": "systemd 用户服务"}


def autostart_set(on):
    if os.name == "nt":
        startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        os.makedirs(startup, exist_ok=True)
        lnk = os.path.join(startup, "qqbot-panel.lnk")
        if on:
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
                  "$s.TargetPath='%s';$s.Arguments='%s --port 6100';$s.WorkingDirectory='%s';$s.Save()"
                  % (lnk.replace("'", "''"), PY, os.path.join(ROOT, "panel.py"), ROOT))
            _run(["powershell", "-NoProfile", "-Command", ps])
        elif os.path.exists(lnk):
            os.remove(lnk)
        return autostart_state()

    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    unit = os.path.join(unit_dir, "qqbot-panel.service")
    if on:
        with open(unit, "w", encoding="utf-8") as f:
            f.write("""[Unit]
Description=QQ Persona Bot Panel
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%s
ExecStart=%s %s --port 6100
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""" % (ROOT, PY, os.path.join(ROOT, "panel.py")))
        _run(["systemctl", "--user", "daemon-reload"])
        code, out = _run(["systemctl", "--user", "enable", "--now", "qqbot-panel.service"])
        if code != 0:
            return {"supported": True, "enabled": False, "detail": "启用失败：" + out.strip()[:120]}
    else:
        _run(["systemctl", "--user", "disable", "--now", "qqbot-panel.service"])
        if os.path.exists(unit):
            os.remove(unit)
        _run(["systemctl", "--user", "daemon-reload"])
    return autostart_state()


def local_version():
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def remote_version():
    try:
        raw = fetch_url("https://api.github.com/repos/%s/commits/%s" % (REPO, BRANCH),
                        timeout=15,
                        headers={"User-Agent": "qqbot-panel", "Accept": "application/vnd.github+json"})
        d = json.loads(raw.decode("utf-8", "ignore"))
        return {"sha": d.get("sha", "")[:12],
                "date": (d.get("commit", {}).get("committer", {}) or {}).get("date", ""),
                "message": (d.get("commit", {}).get("message", "") or "").splitlines()[0]}
    except Exception as e:
        return {"error": str(e)[:120]}


def apply_update(sha):
    import io
    import tarfile
    import urllib.request
    url = "https://codeload.github.com/%s/tar.gz/refs/heads/%s" % (REPO, BRANCH)
    raw = fetch_url(url, timeout=90)
    tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    members = tf.getmembers()
    root_dirs = {m.name.split("/")[0] for m in members if "/" in m.name}
    prefix = sorted(root_dirs)[0] + "/"
    written = []
    for m in members:
        if not m.isfile():
            continue
        rel = m.name[len(prefix):]
        if not rel:
            continue
        if not any(rel == f or rel.startswith(f + "/") for f in UPDATE_FILES):
            continue
        dst = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with tf.extractfile(m) as src, open(dst + ".new", "wb") as out:
            shutil.copyfileobj(src, out)
        os.replace(dst + ".new", dst)
        written.append(rel)
    os.makedirs(os.path.dirname(VERSION_FILE), exist_ok=True)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(sha)
    return written


# ---------------------------------------------------------------- HTTP
PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QQ 人格机器人 · 调试面板</title>
<style>
:root{--bg:#101012;--card:#1b1b1f;--line:#2b2b31;--txt:#e8e8ea;--dim:#9a9aa2;--ok:#3ddc84;--bad:#ff5d5d;--acc:#6aa8ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif}
.wrap{max-width:100%;margin:0 auto;padding:16px 22px}
h1{font-size:18px;margin:0 0 4px}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.card h2{font-size:13px;color:var(--dim);font-weight:600;margin:0 0 12px;letter-spacing:.04em}
.row{display:flex;gap:12px;flex-wrap:wrap}
.row>*{flex:1;min-width:180px}
label{display:block;font-size:12px;color:var(--dim);margin-bottom:4px}
input,textarea,select{width:100%;background:#121216;border:1px solid var(--line);border-radius:8px;color:var(--txt);padding:8px 10px;font:inherit}
textarea{min-height:46vh;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.65}
button{background:var(--acc);color:#06101f;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}
button.ghost{background:#26262c;color:var(--txt)}
button.danger{background:var(--bad);color:#1a0000}
button:disabled{opacity:.45;cursor:not-allowed}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#26262c;color:var(--dim)}
.pill.ok{background:rgba(61,220,132,.14);color:var(--ok)}
.pill.bad{background:rgba(255,93,93,.14);color:var(--bad)}
pre{background:#0c0c0f;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;max-height:280px;font-size:12px;margin:0}
img.qr{width:220px;height:220px;image-rendering:pixelated;border-radius:8px;background:#fff;padding:6px}
.muted{color:var(--dim);font-size:12px}
</style></head><body><div class="wrap">
<h1>QQ 群聊人格机器人 · 调试面板</h1>
<div class="sub" id="sub">加载中…</div>

<div class="card"><h2>实例</h2>
  <div class="row"><div><label>选择实例</label><select id="inst"></select></div>
  <div style="display:flex;align-items:flex-end;gap:8px">
    <button onclick="ctrl('start')">启动</button>
    <button class="ghost" onclick="ctrl('restart')">重启</button>
    <button class="danger" onclick="ctrl('stop')">停止</button>
  </div></div>
  <div style="margin-top:12px" id="state"></div>
  <div class="row" style="margin-top:12px">
    <div><label>新建实例（多开）</label><input id="newname" placeholder="例如：小号二"></div>
    <div style="display:flex;align-items:flex-end"><button class="ghost" onclick="newInst()">创建</button></div>
  </div>
</div>

<div class="card"><h2>QQ 登录</h2>
  <div class="row">
    <div style="flex:0 0 240px"><div id="qrbox" class="muted">没有二维码</div></div>
    <div><label>NapCat 地址</label><input id="ws" readonly>
      <div class="muted" style="margin-top:8px">扫码后这里会自动变成在线；二维码由 NapCat 生成，过期会自动刷新。</div>
    </div>
  </div>
</div>

<div class="card"><h2>说话节奏</h2>
  <div class="row">
    <div><label>插话概率（0~1）</label><input id="chance" type="number" step="0.01" min="0" max="1"></div>
    <div><label>冷却下限（秒）</label><input id="cd_min" type="number" min="0"></div>
    <div><label>冷却上限（秒）</label><input id="cd_max" type="number" min="0"></div>
    <div><label>每小时最多</label><input id="hourly" type="number" min="0"></div>
  </div>
  <div class="row" style="margin-top:12px">
    <div><label>静默开始（点）</label><input id="q_from" type="number" min="0" max="23"></div>
    <div><label>静默结束（点）</label><input id="q_to" type="number" min="0" max="23"></div>
    <div><label>触发词（逗号分隔）</label><input id="trig"></div>
    <div><label>仅主人触发（逗号分隔）</label><input id="otrig"></div>
  </div>
  <div style="margin-top:12px"><button onclick="saveConfig()">保存参数</button></div>
</div>

<div class="card"><h2>白名单</h2>
  <div class="row">
    <div><label>群号（逗号分隔）</label><input id="groups"></div>
    <div><label>好友/私聊号（逗号分隔）</label><input id="friends"></div>
  </div>
  <div style="margin-top:12px"><button onclick="saveWhitelist()">保存白名单</button></div>
</div>

<div class="card"><h2>人格</h2>
  <div class="row" style="align-items:flex-end">
    <div style="flex:2"><label>当前编辑的人格文件</label><select id="pfile" onchange="switchPersona()"></select></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="ghost" onclick="newPersona()">新建</button>
      <button onclick="savePersona(false)">保存</button>
      <button onclick="savePersona(true)">保存并启用</button>
      <button class="danger" onclick="delPersona()">删除</button>
    </div>
  </div>
  <div class="row" style="margin-top:12px;align-items:flex-end">
    <div style="flex:2"><label>从云端人格导入（https 地址）</label>
      <input id="purl" value="https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/persona/persona.txt"></div>
    <div><button class="ghost" onclick="pullPersona()">拉取并保存</button></div>
  </div>
  <textarea id="persona" spellcheck="false" style="margin-top:12px"></textarea>
  <div style="margin-top:10px;display:flex;gap:10px;align-items:center">
    <span class="muted" id="plen"></span>
    <span class="muted" id="pactive"></span>
  </div>
</div>

<div class="card"><h2>设置</h2>
  <div class="row">
    <div><label>自动更新</label>
      <select id="autoupd">
        <option value="off">关闭（只手动点）</option>
        <option value="check">只检查并提示</option>
        <option value="apply">自动下载安装</option>
      </select></div>
    <div><label>检查间隔（小时）</label><input id="interval" type="number" min="1" value="6"></div>
    <div><label>实例目录（多开）</label><input id="instdir" placeholder="instances"></div>
  </div>
  <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
    <button onclick="saveSettings()">保存设置</button>
    <span class="muted" id="lastcheck"></span>
  </div>
</div>

<div class="card"><h2>路径</h2><div id="paths" class="muted">读取中…</div></div>

<div class="card"><h2>自启与更新</h2>
  <div class="row">
    <div><label>开机自启（可选）</label><div id="autostart" class="muted">读取中…</div></div>
    <div style="display:flex;align-items:flex-end;gap:8px">
      <button class="ghost" onclick="toggleAutostart(true)">开启</button>
      <button class="ghost" onclick="toggleAutostart(false)">关闭</button>
    </div>
  </div>
  <div class="row" style="margin-top:12px">
    <div><label>版本</label><div id="ver" class="muted">—</div></div>
    <div style="display:flex;align-items:flex-end;gap:8px">
      <button class="ghost" onclick="refreshAuto()">检查更新</button>
      <button onclick="doUpdate()">立即更新</button>
    </div>
  </div>
  <div class="muted" style="margin-top:10px">更新源：github.com/mxy110415/qq-group-persona-bot · 只覆盖代码文件，不动 config / data / persona</div>
</div>

<div class="card"><h2>日志</h2>
  <pre id="logs">—</pre>
  <div style="margin-top:10px"><button class="ghost" onclick="loadLogs()">刷新日志</button></div>
</div>
</div>
<script>
let STATE=null, CUR=null;
const $=id=>document.getElementById(id);
const j=async(u,o)=>{const r=await fetch(u,o);const t=await r.json().catch(()=>({}));if(!r.ok)throw new Error(t.error||r.status);return t};
const fmt=ts=>new Date(ts*1000).toLocaleTimeString('zh-CN',{hour12:false});

async function boot(){
  const d=await j('/api/instances');
  $('inst').innerHTML=d.instances.map(i=>`<option>${i.name}</option>`).join('');
  $('inst').onchange=()=>{CUR=$('inst').value;loadState()};
  CUR=d.instances[0]?.name; await loadState();
}

async function loadState(){
  STATE=await j('/api/state?name='+encodeURIComponent(CUR));
  const s=STATE, gm=s.group_mode||{};
  $('sub').innerHTML=`实例 <b>${s.name}</b> · ${s.bot_running?'<span class="pill ok">进程在跑</span>':'<span class="pill bad">进程未运行</span>'} · ${s.ws_ok?'<span class="pill ok">NapCat 在线</span>':'<span class="pill bad">NapCat 未连</span>'}`;
  $('state').innerHTML=`<span class="pill">主人 ${s.owner_id||'未设'}</span> <span class="pill">称呼 ${s.owner_name||'-'}</span> <span class="pill">模型 ${s.model||'-'}</span> <span class="pill">群 ${s.groups.length} · 好友 ${s.friends.length}</span>`;
  $('ws').value=s.ws||'';
  $('chance').value=gm.chance??''; 
  const cd=gm.cooldown||[900,2700];
  $('cd_min').value=cd[0]??''; $('cd_max').value=cd[1]??'';
  $('hourly').value=gm.hourly_limit??'';
  const q=gm.quiet_hours||[2,7];
  $('q_from').value=q[0]??''; $('q_to').value=q[1]??'';
  $('trig').value=(s.name_triggers||[]).join(',');
  $('otrig').value=(s.owner_only_triggers||[]).join(',');
  $('groups').value=(s.groups||[]).join(',');
  $('friends').value=(s.friends||[]).join(',');
  renderQR(s);
  await loadPersonas();
  loadLogs();
}

function renderQR(s){
  if(!s.qr_mtime){$('qrbox').innerHTML='<span class="muted">还没有二维码文件</span>';return}
  $('qrbox').innerHTML=`<img class="qr" src="/api/qr?name=${encodeURIComponent(CUR)}&t=${s.qr_mtime}">
    <div class="muted" style="margin-top:6px">生成于 ${fmt(s.qr_mtime)}</div>`;
}

async function loadLogs(){
  const d=await j('/api/logs?name='+encodeURIComponent(CUR));
  const el=$('logs'); el.textContent=d.log||'（暂无日志）'; el.scrollTop=el.scrollHeight;
}

async function saveConfig(){
  const body={name:CUR,group_mode:{
    chance:parseFloat($('chance').value)||0,
    cooldown:[parseInt($('cd_min').value)||0,parseInt($('cd_max').value)||0],
    hourly_limit:parseInt($('hourly').value)||0,
    quiet_hours:[parseInt($('q_from').value)||0,parseInt($('q_to').value)||0]},
    name_triggers:$('trig').value.split(',').map(x=>x.trim()).filter(Boolean),
    owner_only_triggers:$('otrig').value.split(',').map(x=>x.trim()).filter(Boolean)};
  const r=await j('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  alert(r.message||'已保存'); loadState();
}

async function saveWhitelist(){
  const body={name:CUR,
    groups:$('groups').value.split(',').map(x=>x.trim()).filter(Boolean),
    friends:$('friends').value.split(',').map(x=>x.trim()).filter(Boolean)};
  const r=await j('/api/whitelist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  alert(r.message||'已保存'); loadState();
}

async function loadPersonas(select){
  const d=await j('/api/personas?name='+encodeURIComponent(CUR));
  $('pfile').innerHTML=d.files.map(f=>`<option value="${f}"${f===(select||d.active)?' selected':''}>${f}${f===d.active?'（启用中）':''}</option>`).join('');
  $('pactive').textContent='当前启用：'+d.active;
  await switchPersona(select||d.active);
}

async function switchPersona(f){
  const file=f||$('pfile').value;
  const d=await j('/api/persona?name='+encodeURIComponent(CUR)+'&file='+encodeURIComponent(file));
  $('persona').value=d.text||'';
  $('plen').textContent=(d.text||'').length+' 字';
}

async function savePersona(activate){
  const r=await j('/api/persona',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:CUR,file:$('pfile').value,text:$('persona').value,activate:!!activate})});
  alert(r.message||'已保存');
  await loadPersonas($('pfile').value);
}

async function newPersona(){
  const n=prompt('新人格文件名（例如 温柔版）');
  if(!n) return;
  const r=await j('/api/persona',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:CUR,file:n+'.txt',text:'',action:'create'})});
  alert(r.message||'已创建'); await loadPersonas(n+'.txt');
}

async function delPersona(){
  const f=$('pfile').value;
  if(!confirm('删除人格文件 '+f+' ？')) return;
  try{
    const r=await j('/api/persona',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR,file:f,action:'delete'})});
    alert(r.message); await loadPersonas();
  }catch(e){alert('失败：'+e.message)}
}

async function pullPersona(){
  const url=$('purl').value.trim();
  if(!url) return alert('填个地址');
  const name=(prompt('保存为文件名（不含 .txt）','云端人格')||'云端人格')+'.txt';
  try{
    const r=await j('/api/persona',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR,file:name,url,action:'pull'})});
    alert(r.message); await loadPersonas(name);
  }catch(e){alert('拉取失败：'+e.message)}
}

async function newInst(){
  const name=$('newname').value.trim();
  if(!name) return alert('填个实例名');
  try{
    const r=await j('/api/instances',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
    alert(r.message||'已创建'); $('newname').value=''; boot();
  }catch(e){alert('失败：'+e.message)}
}

async function loadSettings(){
  const st=await j('/api/settings');
  $('autoupd').value=st.auto_update||'off';
  $('interval').value=st.interval_hours||6;
  $('instdir').value=st.instances_dir||'instances';
  const lc=st._last_check||{};
  $('lastcheck').textContent = lc.time? ('上次检查 '+new Date(lc.time*1000).toLocaleString('zh-CN')+' · '+(lc.result||'')) : '还没自动检查过';
  const p=await j('/api/paths');
  $('paths').innerHTML = Object.entries(p).filter(([k])=>k!=='note')
    .map(([k,v])=>`<div style="margin:2px 0"><span class="pill">${k}</span> <code>${String(v).replace(/</g,'&lt;')}</code></div>`).join('')
    + `<div class="muted" style="margin-top:8px">${p.note||''}</div>`;
}

async function saveSettings(){
  const body={auto_update:$('autoupd').value, interval_hours:parseInt($('interval').value)||6, instances_dir:$('instdir').value.trim()};
  const r=await j('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  alert(r.message||'已保存'); loadSettings();
}

async function refreshAuto(){
  try{
    const a=await j('/api/autostart');
    $('autostart').innerHTML=a.enabled?'<span class="pill ok">已开启</span> '+a.detail:'<span class="pill">未开启</span> '+a.detail;
  }catch(e){$('autostart').textContent='读取失败'}
  try{
    const u=await j('/api/update/check');
    const r=u.remote||{};
    $('ver').innerHTML=`本地 <span class="pill">${u.local||'未记录'}</span> 远程 <span class="pill">${r.sha||'取不到'}</span>` +
      (r.sha&&u.local===r.sha?' <span class="pill ok">已是最新</span>':'') +
      (r.sha&&u.local!==r.sha?' <span class="pill bad">可更新</span>':'') +
      `<div class="muted" style="margin-top:6px">${(r.message||'').replace(/</g,'&lt;')} ${r.date?('· '+r.date.slice(0,16)):''}</div>`;
  }catch(e){$('ver').textContent='检查失败'}
}

async function toggleAutostart(on){
  const r=await j('/api/autostart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:on})});
  alert(r.enabled?'已开启开机自启':'已关闭开机自启'); refreshAuto();
}

async function doUpdate(){
  if(!confirm('从 GitHub 拉取最新代码？\n只覆盖 bot.py / panel.py / deploy 等代码文件，配置、人格、数据都不会动。')) return;
  try{
    const r=await j('/api/update/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    alert(r.message+'\n更新文件：'+(r.files||[]).join(', ')); refreshAuto();
  }catch(e){alert('更新失败：'+e.message)}
}

async function ctrl(action){
  const r=await j('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:CUR,action})});
  alert(r.message||action); setTimeout(loadState,800);
}
boot(); refreshAuto(); loadSettings(); setInterval(()=>{if(CUR)loadState()},8000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "QQBotPanel/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _inst(self, name=None):
        insts = load_instances()["instances"]
        if not insts:
            return None
        if not name:
            return insts[0]
        for i in insts:
            if i["name"] == name:
                return i
        return insts[0]

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        name = (q.get("name") or [None])[0]
        if u.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if u.path == "/api/instances":
            return self._json({"instances": [{"name": i["name"]} for i in load_instances()["instances"]]})
        if u.path == "/api/state":
            inst = self._inst(name)
            if not inst:
                return self._json({"error": "没有实例"}, 404)
            try:
                return self._json(instance_state(inst))
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if u.path == "/api/qr":
            inst = self._inst(name)
            if not inst:
                return self._json({"error": "没有实例"}, 404)
            home = instance_home(inst)
            qr = os.path.join(ROOT, inst.get("qr") or "QQn/resources/app/napcat/cache/qrcode.png")
            if not os.path.exists(qr):
                return self._json({"error": "二维码还没生成"}, 404)
            with open(qr, "rb") as f:
                data = f.read()
            return self._send(200, data, "image/png")
        if u.path == "/api/personas":
            inst = self._inst(name)
            home = instance_home(inst)
            pdir = os.path.join(home, "persona")
            os.makedirs(pdir, exist_ok=True)
            files = sorted(f for f in os.listdir(pdir) if f.endswith(".txt"))
            cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
            return self._json({"files": files,
                               "active": cfg.get("persona_file") or "persona.txt"})
        if u.path == "/api/persona":
            inst = self._inst(name)
            if not inst:
                return self._json({"error": "没有实例"}, 404)
            home = instance_home(inst)
            cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
            fname = (q.get("file") or [None])[0] or cfg.get("persona_file") or "persona.txt"
            fname = os.path.basename(fname)
            p = os.path.join(home, "persona", fname)
            text = ""
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    text = f.read()
            return self._json({"text": text, "path": p, "file": fname})
        if u.path == "/api/settings":
            st = load_settings()
            st["_last_check"] = dict(_last_check)
            return self._json(st)
        if u.path == "/api/paths":
            return self._json(paths_info())
        if u.path == "/api/autostart":
            return self._json(autostart_state())
        if u.path == "/api/update/check":
            return self._json({"local": local_version(), "remote": remote_version()})
        if u.path == "/api/logs":
            inst = self._inst(name)
            if not inst:
                return self._json({"error": "没有实例"}, 404)
            home = instance_home(inst)
            for cand in ["panel-bot.log", "bot.log", "bot-service.log"]:
                p = os.path.join(home, cand)
                if os.path.exists(p):
                    return self._json({"log": tail(p)})
            return self._json({"log": ""})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}

        if u.path == "/api/instances":
            name = (body.get("name") or "").strip()
            if not name:
                return self._json({"error": "实例名不能为空"}, 400)
            data = load_instances()
            if any(i["name"] == name for i in data["instances"]):
                return self._json({"error": "同名实例已存在"}, 400)
            safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", name)
            used = {i.get("ws") for i in data["instances"]}
            port = 3001
            while "ws://127.0.0.1:%d" % port in used:
                port += 1
            home_rel = os.path.join("instances", safe)
            home = os.path.join(ROOT, home_rel)
            if not os.path.isdir(home):
                for d in ("config", "data", "persona"):
                    os.makedirs(os.path.join(home, d), exist_ok=True)
                # 用示例配置初始化；没有示例就从当前配置复制一份并清空敏感字段
                base_cfg = os.path.join(ROOT, "config", "config.example.json")
                if not os.path.exists(base_cfg):
                    base_cfg = os.path.join(ROOT, "config", "config.json")
                if os.path.exists(base_cfg):
                    c = read_json(base_cfg, {}) or {}
                    c["owner_id"] = ""
                    c["admins"] = []
                    c["ws_token"] = ""
                    if isinstance(c.get("api"), dict):
                        c["api"]["key"] = ""
                    c["napcat_ws"] = "ws://127.0.0.1:%d" % port
                    write_json(os.path.join(home, "config", "config.json"), c)
                write_json(os.path.join(home, "config", "whitelist.json"), {"groups": [], "friends": []})
                gc_ex = os.path.join(ROOT, "config", "group_configs.example.json")
                if os.path.exists(gc_ex):
                    shutil.copy2(gc_ex, os.path.join(home, "config", "group_configs.json"))
                else:
                    write_json(os.path.join(home, "config", "group_configs.json"), {"_说明": "按群号配置"})
                pp = os.path.join(ROOT, "persona", "persona.txt")
                if os.path.exists(pp):
                    shutil.copy2(pp, os.path.join(home, "persona", "persona.txt"))
            data["instances"].append({"name": name, "home": home_rel,
                                      "ws": "ws://127.0.0.1:%d" % port,
                                      "qr": ".//QQn/resources/app/napcat/cache/qrcode.png"})
            save_instances(data)
            return self._json({"message": "实例已创建：%s（OneBot 端口 %d，配置未填）" % (name, port)})


        inst = self._inst(body.get("name"))
        if not inst:
            return self._json({"error": "没有实例"}, 404)
        home = instance_home(inst)

        if u.path == "/api/settings":
            st = load_settings()
            if "auto_update" in body:
                st["auto_update"] = body["auto_update"] if body["auto_update"] in ("off", "check", "apply") else "off"
            if "interval_hours" in body:
                try:
                    st["interval_hours"] = max(1, int(body["interval_hours"]))
                except Exception:
                    pass
            if "instances_dir" in body and body["instances_dir"].strip():
                st["instances_dir"] = body["instances_dir"].strip()
            save_settings(st)
            return self._json({"message": "设置已保存", "settings": st})

        if u.path == "/api/autostart":
            return self._json(autostart_set(bool(body.get("enabled"))))

        if u.path == "/api/update/apply":
            rv = remote_version()
            if rv.get("error"):
                return self._json({"error": "拉取远程版本失败：" + rv["error"]}, 502)
            try:
                files = apply_update(rv["sha"])
            except Exception as e:
                return self._json({"error": "更新失败：" + str(e)[:160]}, 500)
            return self._json({"message": "已更新到 %s，重启面板/机器人后生效" % rv["sha"],
                               "files": files, "sha": rv["sha"]})

        if u.path == "/api/config":
            path = os.path.join(home, "config", "config.json")
            cfg = read_json(path, {}) or {}
            gm = body.get("group_mode") or {}
            cfg.setdefault("group_mode", {})
            if "chance" in gm:
                cfg["group_mode"]["chance"] = gm["chance"]
            if "hourly_limit" in gm:
                cfg["group_mode"]["max_per_hour"] = gm["hourly_limit"]
            if "cooldown" in gm:
                cd = gm["cooldown"] or [900, 2700]
                cfg["group_mode"]["min_gap_s"] = cd[0]
                cfg["group_mode"]["max_gap_s"] = cd[1] if len(cd) > 1 else cd[0]
            if "quiet_hours" in gm:
                cfg["group_mode"]["quiet_hours"] = gm["quiet_hours"]
            if "name_triggers" in body:
                cfg["name_triggers"] = body["name_triggers"]
            if "owner_only_triggers" in body:
                cfg["owner_only_triggers"] = body["owner_only_triggers"]
            write_json(path, cfg)
            return self._json({"message": "参数已保存（重启后完全生效）"})

        if u.path == "/api/whitelist":
            path = os.path.join(home, "config", "whitelist.json")
            wl = {"groups": body.get("groups", []), "friends": body.get("friends", [])}
            write_json(path, wl)
            return self._json({"message": "白名单已保存（立即生效，无需重启）"})

        if u.path == "/api/persona":
            pdir = os.path.join(home, "persona")
            os.makedirs(pdir, exist_ok=True)
            fname = os.path.basename(body.get("file") or "persona.txt")
            if not fname.endswith(".txt"):
                fname += ".txt"
            action = body.get("action") or "save"
            path = os.path.join(pdir, fname)

            if action == "delete":
                if fname == "persona.txt":
                    return self._json({"error": "默认人格不能删除"}, 400)
                if os.path.exists(path):
                    os.remove(path)
                return self._json({"message": "已删除 %s" % fname})

            if action == "pull":
                import urllib.request
                url = (body.get("url") or "").strip()
                if not url.startswith("https://"):
                    return self._json({"error": "只接受 https 地址"}, 400)
                try:
                    text = fetch_url(url, timeout=25).decode("utf-8", "ignore")
                except Exception as e:
                    return self._json({"error": "下载失败：" + str(e)[:120]}, 502)
                if len(text.strip()) < 20:
                    return self._json({"error": "内容太短，可能不是人格文件"}, 400)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                return self._json({"message": "已从云端保存为 %s（%d 字）" % (fname, len(text)), "file": fname})

            # save / create
            text = body.get("text", "")
            if action == "create" and os.path.exists(path):
                return self._json({"error": "同名人格已存在"}, 400)
            # 防呆：已有文件被写成空/极短内容时拒绝（避免误清空人格）
            if action != "create" and len(text.strip()) < 20 and os.path.exists(path):
                return self._json({"error": "内容太短（%d 字），已拒绝保存，防止误清空人格" % len(text.strip())}, 400)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)

            if body.get("activate"):
                cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
                cfg["persona_file"] = fname
                write_json(os.path.join(home, "config", "config.json"), cfg)
                if proc_alive(_procs.get(inst["name"])):
                    stop_instance(inst["name"])
                    start_instance(inst["name"])
            return self._json({"message": "已保存 %s" % fname, "file": fname})

        if u.path == "/api/control":
            action = body.get("action")
            if action == "start":
                ok, msg = start_instance(inst["name"])
            elif action == "stop":
                ok, msg = stop_instance(inst["name"])
            elif action == "restart":
                stop_instance(inst["name"])
                time.sleep(0.5)
                ok, msg = start_instance(inst["name"])
            else:
                return self._json({"error": "未知操作"}, 400)
            return self._json({"ok": ok, "message": msg}, 200 if ok else 400)

        return self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6100)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    threading.Thread(target=auto_update_loop, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("面板已启动: http://%s:%d  (Ctrl+C 退出)" % (a.host, a.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
