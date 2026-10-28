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

# 打包成单文件 exe 时，代码解压在临时目录，运行数据要落在 exe 旁边
ROOT = os.environ.get("QQBOT_ROOT") or os.path.dirname(os.path.abspath(__file__))
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





# ---------------------------------------------------------------- 暂停 / 试聊 / 数据 / 备份
def pause_state(home):
    return os.path.exists(os.path.join(home, "data", "paused.flag"))


def set_pause(home, paused):
    fp = os.path.join(home, "data", "paused.flag")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    if paused:
        with open(fp, "w", encoding="utf-8") as f:
            f.write("paused\n")
    elif os.path.exists(fp):
        os.remove(fp)
    return pause_state(home)


def load_persona_text(home):
    cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
    fname = os.path.basename(cfg.get("persona_file") or "persona.txt")
    pth = os.path.join(home, "persona", fname)
    txt = ""
    if os.path.exists(pth):
        with open(pth, encoding="utf-8") as f:
            txt = f.read()
    if not txt:
        txt = cfg.get("persona") or ""
    return txt.replace("{{OWNER}}", cfg.get("owner_name") or "主人")


def test_chat(home, text):
    cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
    api = cfg.get("api") or {}
    base = (api.get("base_url") or "").rstrip("/")
    key = api.get("key") or ""
    model = api.get("model") or ""
    if not (base and key and model):
        return None, "配置里缺 API 地址 / Key / 模型"
    persona = load_persona_text(home)
    limits = cfg.get("limits") or {}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": text},
        ],
        "max_tokens": int(limits.get("reply_max_chars") or 300),
        "temperature": float(limits.get("temperature") or 0.9),
        "thinking": {"type": "disabled"},
    }
    try:
        raw = fetch_url(base + "/chat/completions", timeout=60,
                        headers={"Authorization": "Bearer " + key,
                                 "Content-Type": "application/json",
                                 "User-Agent": "qqbot-panel"},
                        data=json.dumps(payload).encode())
        d = json.loads(raw.decode("utf-8", "ignore"))
        msg = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return msg, None
    except Exception as e:
        return None, "调用失败：%s" % str(e)[:160]


def data_summary(home):
    aff = read_json(os.path.join(home, "data", "affinity.json"), {}) or {}
    mem = read_json(os.path.join(home, "data", "user_memory.json"), {}) or {}
    a = [{"uid": k, "name": (v or {}).get("name") or "", "score": (v or {}).get("score", 0)}
         for k, v in aff.items() if isinstance(v, dict)]
    a.sort(key=lambda x: -float(x.get("score") or 0))
    m = [{"uid": k, "text": (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))[:400]}
         for k, v in mem.items()]
    return {"affinity": a, "memory": m}


def memes_info(home):
    cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
    d = cfg.get("meme_dir") or os.path.join(home, "meme-pack")
    if not os.path.isabs(d):
        d = os.path.join(home, d)
    out = []
    if os.path.isdir(d):
        for sub in sorted(os.listdir(d)):
            sp = os.path.join(d, sub)
            if not os.path.isdir(sp):
                continue
            files = [f for f in os.listdir(sp) if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))]
            out.append({"dir": sub, "count": len(files), "files": sorted(files)[:50]})
    return {"dir": d, "groups": out}


def backup_zip(home):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(home):
            rel = os.path.relpath(root, home)
            if rel.startswith(".venv") or rel.startswith("QQn") or rel.startswith("instances"):
                continue
            for f in files:
                fp = os.path.join(root, f)
                rp = os.path.relpath(fp, home)
                if rp.startswith(("config/", "persona/", "data/")):
                    z.write(fp, rp)
    return buf.getvalue()


def restore_zip(home, data):
    import io
    import zipfile
    n = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if not (name.startswith(("config/", "persona/", "data/"))):
                continue
            if ".." in name or name.endswith("/"):
                continue
            dst = os.path.join(home, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(name) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


# ---------------------------------------------------------------- 机器人进程控制
BOT_UNITS_USER = "qqbot-bot.service"
BOT_UNITS_SYSTEM = "qqbot-client.service"


def systemd_do(scope, action, unit):
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [action, unit]
    code, out = _run(cmd, timeout=30)
    return code == 0, (out or "").strip()


def start_instance_by_dir(home):
    if not os.path.exists(os.path.join(home, "config", "config.json")):
        return False, "缺配置：%s/config/config.json" % home
    env = dict(os.environ, QQBOT_HOME=home, PYTHONUNBUFFERED="1")
    log = open(os.path.join(home, "panel-bot.log"), "ab")
    pr = subprocess.Popen([PY, os.path.join(ROOT, "bot.py")], cwd=home, env=env,
                          stdout=log, stderr=subprocess.STDOUT)
    return True, "已启动（PID %d）" % pr.pid


def bot_process_control(home, action):
    """优先 systemd；系统级服务缺权限时只报错，不杀进程（避免被 Restart 拉起成双实例）"""
    # 1) 用户级服务
    if os.path.exists(os.path.expanduser("~/.config/systemd/user/" + BOT_UNITS_USER)):
        ok, out = systemd_do("user", action, BOT_UNITS_USER)
        return ok, "%s %s：%s" % (action, BOT_UNITS_USER, "成功" if ok else "失败 " + out[:80])

    # 2) 系统级服务：先看它是否存在（show 不需要 root）
    code, frag = _run(["systemctl", "show", "-p", "FragmentPath", "--value", BOT_UNITS_SYSTEM])
    exists = code == 0 and BOT_UNITS_SYSTEM in (frag or "")
    if exists:
        ok, out = systemd_do("system", action, BOT_UNITS_SYSTEM)
        if ok:
            return True, "%s %s：成功" % (action, BOT_UNITS_SYSTEM)
        return False, ("%s %s 需要管理员权限（面板没有 sudo）。\n"
                       "要么用 sudo systemctl %s %s，要么按 README 把机器人迁移成用户级服务。"
                       % (action, BOT_UNITS_SYSTEM, action, BOT_UNITS_SYSTEM))

    # 3) 裸进程
    pids = detect_bot_pids(home)
    if action in ("stop", "restart") and pids:
        for pid in pids:
            try:
                os.kill(pid, 15)
            except Exception:
                pass
        time.sleep(1.5)
        if action == "stop":
            return True, "已停止"
    if action in ("start", "restart"):
        # 有 systemd 单元（用户级或系统级）优先交给 systemd，保证机器人不跟面板同生共死
        for scope, unit in (("user", BOT_UNITS_USER), ("system", BOT_UNITS_SYSTEM)):
            c, f = _run(["systemctl"] + (["--user"] if scope == "user" else []) +
                        ["show", "-p", "FragmentPath", "--value", unit])
            if c == 0 and unit in (f or ""):
                ok, out = systemd_do(scope, "start", unit)
                if ok:
                    return True, "已通过 %s 启动 %s" % ("user systemd" if scope == "user" else "systemd", unit)
        return start_instance_by_dir(home)
    return (True, "没有在运行") if action == "stop" else (False, "未知操作")


# ---------------------------------------------------------------- QQ 账号 / 窗口
def ffmpeg_path():
    import shutil as _sh
    for cand in ("ffmpeg", "/usr/bin/ffmpeg"):
        if _sh.which(cand):
            return _sh.which(cand)
    import glob as _g
    hits = sorted(_g.glob(os.path.expanduser("~/.hermes/tools/ffmpeg-*/bin/ffmpeg")))
    return hits[-1] if hits else None


def qq_account(home):
    """从 bot_state.json 取账号信息，失败则退回解析日志"""
    st = read_json(os.path.join(home, "data", "bot_state.json"), {}) or {}
    if not st.get("uin"):
        for name in ("bot-service.log", "panel-bot.log", "bot.log"):
            p = os.path.join(home, name)
            if not os.path.exists(p):
                continue
            try:
                with open(p, "rb") as f:
                    f.seek(max(0, os.path.getsize(p) - 20000))
                    txt = f.read().decode("utf-8", "ignore")
                import re as _re
                ms = _re.findall(r"机器人 UIN = (\d+)", txt)
                if ms:
                    st = {"uin": ms[-1]}
                    nc = _re.findall(r"昵称:\s*([^|\n]+)", txt)
                    if nc:
                        st["nickname"] = nc[-1].strip()
                    break
            except Exception:
                continue
    cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
    st.setdefault("owner_id", cfg.get("owner_id", ""))
    st.setdefault("owner_name", cfg.get("owner_name", ""))
    return st


def qq_window_png(size="1280x900"):
    ff = ffmpeg_path()
    if not ff:
        return None
    out = "/tmp/qqwin_%d.png" % int(time.time())
    code, _ = _run([ff, "-y", "-loglevel", "error", "-f", "x11grab",
                    "-video_size", size, "-i", ":99.0+0,0", "-frames:v", "1", out], timeout=20)
    if code != 0 or not os.path.exists(out):
        return None
    with open(out, "rb") as f:
        data = f.read()
    if len(data) < 12000:
        try:
            os.remove(out)
        except Exception:
            pass
        return None
    try:
        os.remove(out)
    except Exception:
        pass
    return data


def qq_window_visible():
    """:99 上有没有可见的 QQ 窗口（大窗口，不是 10x10 的托盘壳）"""
    code, out = _run(["xwininfo", "-display", ":99", "-root", "-children"], timeout=6)
    if code != 0:
        return False
    for line in out.splitlines():
        if '"qq"' in line or '"QQ"' in line:
            import re as _re
            m = _re.search(r"(\d+)x(\d+)\+", line)
            if m and int(m.group(1)) > 200 and int(m.group(2)) > 200:
                return True
    return False


def qq_pids(home):
    pids = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            if "QQn/qq" in cmd and "--no-sandbox" in cmd and "--type=" not in cmd:
                pids.append(int(pid))
        except Exception:
            continue
    return pids


def open_qq(home):
    """打开 QQ：已在运行但窗口隐藏 → 触发重启让它重新弹窗；没运行 → 拉起"""
    if qq_window_visible():
        return True, "QQ 窗口已显示"
    pids = qq_pids(home)
    if pids:
        for pid in pids:
            try:
                os.kill(pid, 15)
            except Exception:
                pass
        for _ in range(40):
            time.sleep(0.5)
            if qq_window_visible():
                return True, "已让 QQ 重新弹出登录窗口"
        return False, "已请求 QQ 重启，但窗口还没出现，稍后点「刷新窗口截图」看看"

    code, out = _run(["systemctl", "start", "qqbot-napcat.service"], timeout=25)
    for _ in range(30):
        time.sleep(0.5)
        if qq_window_visible():
            return True, "已通过 systemd 启动 QQ"
    if not port_open("127.0.0.1", 6099):
        return False, "启动失败：" + out.strip()[:120]
    return True, "NapCat 已起来，窗口稍后出现"


# ---------------------------------------------------------------- NapCat 连接
def napcat_cfg_file(home):
    """找到 NapCat 的 onebot11_<uin>.json"""
    import glob
    pats = sorted(glob.glob(os.path.join(home, "QQn", "resources", "app", "napcat",
                                         "config", "onebot11_*.json")))
    return pats[0] if pats else None


def napcat_net(home):
    """读 NapCat 的 websocket 服务器设置"""
    f = napcat_cfg_file(home)
    out = {"file": f, "exists": bool(f), "host": "127.0.0.1", "port": 3000,
           "enable": True, "token_set": False}
    if not f:
        return out
    try:
        with open(f, encoding="utf-8") as fp:
            d = json.load(fp)
        srv = ((d.get("network") or {}).get("websocketServers") or [{}])[0]
        out["host"] = srv.get("host") or "127.0.0.1"
        out["port"] = int(srv.get("port") or 3000)
        out["enable"] = bool(srv.get("enable", True))
        out["token_set"] = bool(srv.get("token"))
    except Exception as e:
        out["error"] = str(e)[:100]
    return out


def napcat_set_net(home, host, port, token=None, enable=None):
    """写回 NapCat 配置，并同步面板的 napcat_ws / ws_token"""
    f = napcat_cfg_file(home)
    if not f:
        return False, "没找到 NapCat 配置文件（QQn/resources/app/napcat/config/onebot11_*.json）"
    try:
        with open(f, encoding="utf-8") as fp:
            d = json.load(fp)
        net = d.setdefault("network", {})
        servers = net.setdefault("websocketServers", [])
        if not servers:
            servers.append({"name": "hermes-ws", "enable": True, "messagePostFormat": "array"})
        srv = servers[0]
        srv["host"] = host
        srv["port"] = int(port)
        if enable is not None:
            srv["enable"] = bool(enable)
        if token:
            srv["token"] = token
        tmp = f + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, f)
    except Exception as e:
        return False, "写 NapCat 配置失败：%s" % str(e)[:100]

    # 同步面板配置（bot.py 连的地址）
    try:
        cfg_path = os.path.join(home, "config", "config.json")
        cfg = read_json(cfg_path, {}) or {}
        conn_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
        cfg["napcat_ws"] = "ws://%s:%d" % (conn_host, int(port))
        if token:
            cfg["ws_token"] = token
        with open(cfg_path, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
    except Exception as e:
        return True, "NapCat 已更新，但同步面板配置失败：%s" % str(e)[:80]
    return True, "已保存"


# ---------------------------------------------------------------- 配置架构
SCHEMA = {
    "基础": [
        {"key": "api.base_url", "label": "API 地址", "type": "str"},
        {"key": "api.model", "label": "模型", "type": "str"},
        {"key": "api.key", "label": "API Key", "type": "secret"},
        {"key": "napcat_ws", "label": "NapCat 地址", "type": "str"},
        {"key": "ws_token", "label": "NapCat Token", "type": "secret"},
        {"key": "owner_id", "label": "主人 QQ", "type": "str"},
        {"key": "owner_name", "label": "称呼主人为", "type": "str"},
        {"key": "admins", "label": "管理员 QQ", "type": "list"},
    ],
    "群聊": [
        {"key": "group_mode.enabled", "label": "启用群聊", "type": "bool"},
        {"key": "group_mode.chance", "label": "插话概率", "type": "num", "step": 0.01, "hint": "0~1"},
        {"key": "group_mode.min_gap_s", "label": "冷却下限（秒）", "type": "num"},
        {"key": "group_mode.max_gap_s", "label": "冷却上限（秒）", "type": "num"},
        {"key": "group_mode.max_per_hour", "label": "每小时最多", "type": "num"},
        {"key": "group_mode.max_per_minute", "label": "每分钟最多", "type": "num"},
        {"key": "group_mode.reply_window_s", "label": "回应窗口（秒）", "type": "num"},
        {"key": "group_mode.max_consecutive", "label": "最多连发", "type": "num"},
        {"key": "group_mode.quiet_hours", "label": "静默时段（起,止）", "type": "pair"},
        {"key": "quiet_groups", "label": "只读群（不插话）", "type": "list"},
    ],
    "触发": [
        {"key": "name_triggers", "label": "叫名字就响应", "type": "list"},
        {"key": "topic_triggers", "label": "话题触发词", "type": "list"},
        {"key": "owner_only_triggers", "label": "仅主人触发", "type": "list"},
        {"key": "affinity_cmds", "label": "好感度指令别名", "type": "list"},
    ],
    "记忆与限制": [
        {"key": "limits.max_history_turns", "label": "保留对话轮数", "type": "num"},
        {"key": "limits.per_user_per_minute", "label": "每人每分钟上限", "type": "num"},
        {"key": "limits.reply_max_chars", "label": "回复最大字数", "type": "num"},
        {"key": "limits.request_timeout_s", "label": "请求超时（秒）", "type": "num"},
        {"key": "limits.temperature", "label": "温度", "type": "num", "step": 0.05},
        {"key": "limits.compress_chars", "label": "压缩阈值（字）", "type": "num"},
        {"key": "limits.keep_recent", "label": "压缩后保留条数", "type": "num"},
    ],
    "主动发言": [
        {"key": "proactive.enabled", "label": "启用主动", "type": "bool"},
        {"key": "proactive.continue_enabled", "label": "允许接着聊", "type": "bool"},
        {"key": "proactive.timer_enabled", "label": "定时冒泡", "type": "bool"},
        {"key": "proactive.min_gap_s", "label": "最小间隔（秒）", "type": "num"},
        {"key": "proactive.max_gap_s", "label": "最大间隔（秒）", "type": "num"},
        {"key": "proactive.continue_idle_s", "label": "多久没聊可续（秒）", "type": "num"},
        {"key": "proactive.continue_window_s", "label": "续聊窗口（秒）", "type": "num"},
    ],
    "成本": [
        {"key": "cost.usd_to_cny", "label": "汇率", "type": "num", "step": 0.1},
        {"key": "cost.price_hit", "label": "缓存命中价", "type": "pairf"},
        {"key": "cost.price_miss", "label": "缓存未命中价", "type": "pairf"},
        {"key": "cost.price_out", "label": "输出价", "type": "pairf"},
        {"key": "cost.report_hour", "label": "日报（时）", "type": "num"},
        {"key": "cost.report_minute", "label": "日报（分）", "type": "num"},
    ],
    "表情包": [
        {"key": "meme_dir", "label": "表情包目录", "type": "str"},
    ],
}


def get_dotted(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def set_dotted(d, path, value):
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


MASK = "••••••保留原值••••••"


def config_view(home):
    cfg = read_json(os.path.join(home, "config", "config.json"), {}) or {}
    out = {}
    for fields in SCHEMA.values():
        for f in fields:
            v = get_dotted(cfg, f["key"])
            if f["type"] == "secret":
                v = MASK if v else ""
            out[f["key"]] = v
    return out


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


def fetch_url(url, timeout=20, headers=None, data=None):
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
            req = urllib.request.Request(url, headers=hdrs, data=data)
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
APP_NAME = "QQ 群聊人格机器人"
APP_AUTHOR = "星染 (mxy110415)"
APP_VERSION = "v0.1.0"
REPO_URL = "https://github.com/" + REPO
RELEASES_URL = REPO_URL + "/releases"
ISSUES_URL = REPO_URL + "/issues"
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
:root{
  --bg:#101012;--card:#1b1b1f;--line:#2b2b31;--txt:#e8e8ea;--dim:#9a9aa2;
  --ok:#3ddc84;--bad:#ff5d5d;--acc:#6aa8ff;--acc-txt:#06101f;--field:#121216;--sub:#26262c;
}
/* 浅色 */
html[data-theme="light"]{
  --bg:#f4f5f7;--card:#ffffff;--line:#e3e5ea;--txt:#1b1b21;--dim:#6a6d76;
  --ok:#1f9d55;--bad:#d9363e;--acc:#3b7ddd;--acc-txt:#ffffff;--field:#ffffff;--sub:#eceef2;
}
/* 跟随系统 */
@media (prefers-color-scheme: light){
  html[data-theme="system"]{
    --bg:#f4f5f7;--card:#ffffff;--line:#e3e5ea;--txt:#1b1b21;--dim:#6a6d76;
    --ok:#1f9d55;--bad:#d9363e;--acc:#3b7ddd;--acc-txt:#ffffff;--field:#ffffff;--sub:#eceef2;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 -apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif}
.wrap{max-width:100%;margin:0 auto;padding:16px 22px}
h1{font-size:18px;margin:0 0 4px}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.card h2{font-size:13px;color:var(--dim);font-weight:600;margin:0 0 12px;letter-spacing:.04em}
.row{display:flex;gap:12px;flex-wrap:wrap}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.row>*{flex:1 1 220px;min-width:0}
.row>button{flex:0 0 auto}
.row>div[style*="flex-end"]{flex:0 0 auto}
label{display:block;font-size:12px;color:var(--dim);margin-bottom:4px}
input,textarea,select{width:100%;background:var(--field);border:1px solid var(--line);border-radius:8px;color:var(--txt);padding:8px 10px;font:inherit}
select option{background:var(--card);color:var(--txt)}
select{-webkit-appearance:none;appearance:none;background:var(--field);color:var(--txt);
  background-image:linear-gradient(45deg,transparent 50%,var(--dim) 50%),linear-gradient(135deg,var(--dim) 50%,transparent 50%);
  background-position:calc(100% - 18px) 50%,calc(100% - 13px) 50%;background-size:5px 5px,5px 5px;background-repeat:no-repeat;
  padding-right:32px}
select{color-scheme:dark}
input::placeholder{color:var(--dim)}
textarea{min-height:46vh;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.65}
button{background:var(--acc);color:#06101f;border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}
button.ghost{background:var(--sub);color:var(--txt)}
button.danger{background:var(--bad);color:#1a0000}
button:disabled{opacity:.45;cursor:not-allowed}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:var(--sub);color:var(--dim);border:1px solid var(--line)}
.pill.ok{background:rgba(61,220,132,.14);color:var(--ok)}
.pill.bad{background:rgba(255,93,93,.14);color:var(--bad)}
pre{background:var(--field);border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;max-height:280px;font-size:12px;margin:0}
img.qr{width:190px;height:190px;image-rendering:pixelated;border-radius:8px;background:#fff;padding:6px}
.muted{color:var(--dim);font-size:12px}
#mask{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;z-index:50}
#mask.on{display:flex}
#dlg{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;width:min(420px,88vw);box-shadow:0 18px 50px rgba(0,0,0,.45)}
#dlg h3{margin:0 0 10px;font-size:15px;color:var(--txt)}
#dlgmsg{color:var(--dim);font-size:13.5px;line-height:1.65;white-space:pre-wrap}
#dlgbtns{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
#toast{position:fixed;right:20px;bottom:20px;z-index:60;display:flex;flex-direction:column;gap:8px}
.tst{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);color:var(--txt);
  padding:10px 14px;border-radius:10px;font-size:13px;box-shadow:0 10px 30px rgba(0,0,0,.35);animation:in .18s ease}
@keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.foot{margin:26px 0 10px;padding-top:16px;border-top:1px solid var(--line)}
.num{display:inline-block;min-width:22px;color:var(--acc);opacity:.75;font-variant-numeric:tabular-nums}
.footrow{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
.tag{font-size:12px;color:var(--dim);background:var(--sub);border:1px solid var(--line);padding:4px 10px;border-radius:999px}
.fbtn{font-size:12.5px;color:var(--acc);text-decoration:none;border:1px solid var(--line);background:var(--card);
  padding:6px 12px;border-radius:8px}
.fbtn:hover{border-color:var(--acc)}
.btnlink{display:inline-block;padding:8px 14px;border-radius:8px;background:var(--acc);color:var(--acc-txt);
  text-decoration:none;font-weight:600;font-size:13px}
.themebtn{padding:6px 10px;border-radius:8px;background:var(--sub);border:1px solid var(--line);color:var(--dim);cursor:pointer;font-size:12.5px}
.themebtn.on{color:var(--acc);border-color:var(--acc)}
.topnav{display:flex;gap:8px;margin:0 0 16px}
.tnav{padding:9px 16px;border-radius:10px;background:var(--card);border:1px solid var(--line);color:var(--dim);cursor:pointer;font-size:13.5px}
.tnav.on{background:rgba(106,168,255,.16);color:var(--acc);border-color:var(--acc)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:10px}
.tab{padding:6px 12px;border-radius:8px;background:var(--sub);color:var(--dim);cursor:pointer;font-size:13px;border:1px solid transparent}
.tab.on{background:rgba(106,168,255,.16);color:var(--acc);border-color:var(--acc)}
.field{margin-bottom:12px}
.field .hint{font-size:11px;color:var(--dim);margin-top:3px}
</style></head><body><div class="wrap">
<h1>QQ 群聊人格机器人 · 调试面板</h1>
<div class="sub" id="sub">加载中…</div>
<div class="topnav" id="topnav">
  <div class="tnav on" data-sec="bot" onclick="topTab('bot')">🤖 QQ 机器人</div>
  <div class="tnav" data-sec="persona" onclick="topTab('persona')">🎭 人格</div>
  <div class="tnav" data-sec="napcat" onclick="topTab('napcat')">🔌 NapCat</div>
  <div class="tnav" data-sec="system" onclick="topTab('system')">⚙️ 系统</div>
</div>
<div id="mask"><div id="dlg"><h3 id="dlgtitle">提示</h3><div id="dlgmsg"></div>
  <div id="dlgbtns"><button class="ghost" id="dlgno">取消</button><button id="dlgyes">确定</button></div></div></div>
<div id="toast"></div>


<div class="card" data-sec="bot"><h2>实例</h2>
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

<div class="card" data-sec="bot"><h2>QQ 登录</h2>
  <div class="row">
    <div style="flex:0 0 210px"><div id="qrbox" class="muted">没有二维码</div>
      <div style="text-align:center;margin-top:8px">
        <button class="ghost" onclick="forceQR()" style="flex:0 0 auto">刷新二维码</button>
      </div></div>
    <div>
      <div id="accinfo" style="margin-bottom:12px">读取账号…</div>
      <label>NapCat 地址</label><input id="ws" readonly>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        <button onclick="openQQ()">打开 QQ（重启到登录界面）</button>
      </div>
      <div class="muted" style="margin-top:8px">扫码后自动变在线。「打开 QQ」会让客户端回到登录界面并刷新二维码。</div>
    </div>
  </div>
</div>

<div class="card" data-sec="napcat"><h2>NapCat 面板</h2>
  <div class="muted" id="ncw_hint" style="margin-bottom:12px">读取中…</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button onclick="openNapcatWeb()">在系统浏览器打开 NapCat 面板</button>
    <button class="ghost" onclick="loadNapcatWeb()">重新检测</button>
  </div>
  <div class="hint">NapCat 自带的管理界面是独立的 React 网页，嵌在窗口里会明显拖慢面板，所以改成用系统浏览器打开；改完配置回来点「保存并重启 NapCat」即可。</div>
</div>

<div class="card" data-sec="napcat"><h2>NapCat 连接</h2>
  <div class="row">
    <div><label>主机</label><input id="nc_host" placeholder="127.0.0.1"></div>
    <div><label>OneBot 端口（机器人连这个）</label><input id="nc_port" type="number" placeholder="3000"></div>
    <div><label>令牌（留空表示不修改）</label><input id="nc_token" type="password" autocomplete="off" placeholder="未设置"></div>
  </div>
  <div class="muted" id="nc_hint" style="margin-top:10px">读取中…</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
    <button onclick="saveNapcat(false)">保存</button>
    <button class="ghost" onclick="saveNapcat(true)">保存并重启 NapCat</button>
    <button class="ghost" onclick="testNapcat()">测试连接</button>
  </div>
  <div class="hint">主机填 0.0.0.0 表示允许其它机器连入，这时一定要设令牌。保存会同时更新机器人连接的地址。</div>
</div>

<div class="card" data-sec="bot"><h2>开关与试聊</h2>
  <div class="row" style="align-items:flex-end">
    <div><label>紧急暂停</label><div id="pausestate" class="muted">读取中…</div></div>
    <div style="display:flex;gap:8px;align-items:flex-end">
      <button class="danger" onclick="setPause(true)">暂停回复</button>
      <button class="ghost" onclick="setPause(false)">恢复</button>
    </div>
  </div>
  <div class="row" style="margin-top:14px;align-items:flex-end">
    <div style="flex:3"><label>试聊（只调模型，不发给 QQ）</label><input id="testin" placeholder="输入一句话，回车试"></div>
    <div><button onclick="doTest()">试一下</button></div>
  </div>
  <div id="testout" class="muted" style="margin-top:10px;white-space:pre-wrap"></div>
</div>

<div class="card" data-sec="bot"><h2>配置</h2>
  <div class="tabs" id="tabs"></div>
  <div id="form" style="margin-top:14px"></div>
  <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
    <button onclick="saveTab()">保存本页</button>
    <span class="muted" id="savehint"></span>
  </div>
</div>

<div class="card" data-sec="bot"><h2>白名单</h2>
  <div class="row">
    <div><label>群号（逗号分隔）</label><input id="groups"></div>
    <div><label>好友/私聊号（逗号分隔）</label><input id="friends"></div>
  </div>
  <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
    <button onclick="saveWhitelist()">保存白名单</button>
    <span class="muted">不在这里的群/人，机器人完全不响应</span>
  </div>
</div>
<div class="card" data-sec="bot"><h2>敏感词库</h2>
  <textarea id="sens" style="min-height:220px" spellcheck="false"></textarea>
  <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
    <button onclick="saveSens()">保存词库</button>
    <span class="muted" id="sensinfo"></span>
  </div>
</div>

<div class="card" data-sec="bot"><h2>记忆与好感</h2>
  <div class="row">
    <div><label>好感度（按分数排序）</label><div id="afflist" class="muted" style="max-height:200px;overflow:auto">读取中…</div></div>
    <div><label>用户画像</label><div id="memlist" class="muted" style="max-height:200px;overflow:auto">读取中…</div></div>
  </div>
  <div style="margin-top:12px;display:flex;gap:8px">
    <button class="ghost" onclick="resetData('affinity')">清空好感度</button>
    <button class="ghost" onclick="resetData('memory')">清空画像</button>
    <button class="ghost" onclick="loadData()">刷新</button>
  </div>
</div>
<div class="card" data-sec="bot"><h2>表情包</h2>
  <div id="memes" class="muted">读取中…</div>
  <div class="row" style="margin-top:12px;align-items:flex-end">
    <div><label>分类目录</label><input id="memedir" placeholder="开心"></div>
    <div><label>选择图片</label><input id="memefile" type="file" accept="image/*"></div>
    <div><button onclick="uploadMeme()">上传</button></div>
  </div>
</div>

<div class="card" data-sec="bot"><h2>备份与恢复</h2>
  <div class="muted">备份内容：config/ + persona/ + data/（含敏感词、好感度），不含 .venv 和 QQ 客户端</div>
  <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
    <a class="btnlink" href="/api/backup">下载备份 zip</a>
    <input id="restorefile" type="file" accept=".zip" style="max-width:260px">
    <button class="ghost" onclick="doRestore()">恢复备份</button>
  </div>
</div>

<div class="card" data-sec="bot"><h2>日志</h2>
  <pre id="logs">—</pre>
  <div style="margin-top:10px"><button class="ghost" onclick="loadLogs()">刷新日志</button></div>
</div>

<div class="card" data-sec="persona"><h2>人格</h2>
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

<div class="card" data-sec="system"><h2>设置</h2>

  <div class="row" style="align-items:flex-end">
    <div><label>主题</label>
      <div style="display:flex;gap:6px">
        <div class="themebtn" data-t="system" onclick="setTheme('system')">跟随系统</div>
        <div class="themebtn" data-t="dark" onclick="setTheme('dark')">深色</div>
        <div class="themebtn" data-t="light" onclick="setTheme('light')">浅色</div>
      </div>
    </div>
    <div></div>
  </div>

  <div class="row" style="margin-top:16px">
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

  <div class="row" style="margin-top:18px;align-items:flex-end">
    <div><label>开机自启</label><div id="autostart" class="muted">读取中…</div></div>
    <div style="display:flex;align-items:flex-end">
      <button id="autobtn" class="ghost" onclick="toggleAutostart()">开机自启</button>
    </div>
  </div>

  <div class="row" style="margin-top:18px;align-items:flex-end">
    <div><label>版本与更新</label><div id="ver" class="muted">—</div></div>
    <div style="display:flex;align-items:flex-end;gap:8px">
      <button class="ghost" onclick="refreshAuto()">检查更新</button>
      <button onclick="doUpdate()">立即更新</button>
    </div>
  </div>

  <div style="margin-top:18px;padding-top:14px;border-top:1px solid var(--line)">
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <span class="tag" id="fapp">—</span>
      <span class="tag" id="fver">—</span>
      <span class="tag" id="fauthor">—</span>
    </div>
    <div class="muted" style="margin-top:8px" id="fbuild">—</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
      <a class="fbtn" id="flink-repo" href="#" onclick="openUrl('repo');return false">GitHub 仓库</a>
      <a class="fbtn" id="flink-rel" href="#" onclick="openUrl('rel');return false">版本发布</a>
      <a class="fbtn" id="flink-iss" href="#" onclick="openUrl('iss');return false">问题反馈</a>
    </div>
  </div>
</div>
<div class="card" data-sec="system"><h2>路径</h2><div id="paths" class="muted">读取中…</div></div>
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

let NCW_URL='', NCW_PORT=0;
async function loadNapcatWeb(){
  try{
    const d=await j('/api/napcat/webui?name='+encodeURIComponent(CUR));
    NCW_URL=d.url; NCW_PORT=d.port;
    $('ncw_hint').innerHTML = d.running
      ? `NapCat WebUi 正在运行 · WebUi 端口 <b>${d.port}</b>（机器人连的是 OneBot 端口 3000，两者不是一回事）`
      : `NapCat WebUi 没在监听（端口 ${d.port}），先把 NapCat 启起来`;
  }catch(e){ $('ncw_hint').textContent='读取失败：'+e.message; }
}

async function openNapcatWeb(){
  try{
    if(!NCW_URL){ await loadNapcatWeb(); }
    await j('/api/open-url',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:NCW_URL})});
    toast('已在系统浏览器打开 NapCat 面板');
  }catch(e){ toast('打不开：'+e.message); }
}

async function loadNapcat(){
  try{
    const d=await j('/api/napcat/net?name='+encodeURIComponent(CUR));
    if(document.activeElement!==$('nc_host')) $('nc_host').value=d.host||'127.0.0.1';
    if(document.activeElement!==$('nc_port')) $('nc_port').value=d.port||3000;
    $('nc_hint').innerHTML=`OneBot 端口 <b>${d.port}</b>（机器人连接用） · `
      + `WebUi 端口 <b>${NCW_PORT||'—'}</b>（浏览器管理界面） · `
      + `${d.enable?'已启用':'已停用'} · 令牌${d.token_set?'已设置':'未设置'}
      <br><span style="opacity:.7">${d.file||'未找到配置文件'}</span>`;
  }catch(e){ $('nc_hint').textContent='读取失败：'+e.message; }
}

async function saveNapcat(restart){
  try{
    const body={name:CUR, host:$('nc_host').value.trim()||'127.0.0.1',
                port:parseInt($('nc_port').value||'3000',10),
                token:$('nc_token').value, restart:!!restart, reconnect_bot:!!restart};
    await j('/api/napcat/net',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    $('nc_token').value='';
    toast(restart?'已保存，正在重启 NapCat…':'已保存');
    setTimeout(()=>{loadNapcat(); if(restart) setTimeout(loadState, 12000); else loadState();}, restart?1500:300);
  }catch(e){ toast('保存失败：'+e.message); }
}

async function testNapcat(){
  try{
    const s=await j('/api/state?name='+encodeURIComponent(CUR));
    toast(s.ws_ok? '连接正常：NapCat 在线' : '连不上 NapCat（端口没监听或令牌不对）');
  }catch(e){ toast('测试失败：'+e.message); }
}

async function forceQR(){
  try{
    toast('刷新中…如果二维码没变，会让 QQ 重新生成（约 20 秒）');
    const st=await j('/api/state?name='+encodeURIComponent(CUR));
    const r=await j('/api/qr/refresh',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR, since:st.qr_mtime||0})});
    if(r.changed){
      QRM=0;
      const s2=await j('/api/state?name='+encodeURIComponent(CUR));
      STATE=s2; renderQR(s2); toast('二维码已更新');
    }else{
      toast(r.message||'正在重新生成二维码…');
      for(let i=0;i<6;i++){
        await new Promise(x=>setTimeout(x,3000));
        const s2=await j('/api/state?name='+encodeURIComponent(CUR));
        if(s2.qr_mtime && s2.qr_mtime!==(st.qr_mtime||0)){ STATE=s2; QRM=0; renderQR(s2); break; }
      }
    }
  }catch(e){ toast('刷新失败：'+e.message); }
}

async function refreshLight(){
  try{
    const s=await j('/api/state?name='+encodeURIComponent(CUR));
    STATE=s;
    $('sub').innerHTML=`实例 <b>${s.name}</b> · ${s.bot_running?'<span class="pill ok">进程在跑</span>':'<span class="pill bad">进程未运行</span>'} · ${s.ws_ok?'<span class="pill ok">NapCat 在线</span>':'<span class="pill bad">NapCat 未连</span>'}`;
    $('state').innerHTML=`<span class="pill">主人 ${s.owner_id||'未设'}</span> <span class="pill">称呼 ${s.owner_name||'-'}</span> <span class="pill">模型 ${s.model||'-'}</span> <span class="pill">群 ${s.groups.length} · 好友 ${s.friends.length}</span>`;
    renderQR(s);
  }catch(e){}
  loadQQ();
  loadPause();
  if(TOPSEC==='napcat'){ loadNapcat(); }
  if(TOPSEC==='napcat'){ loadNapcatWeb(); }
}

async function loadState(){
  STATE=await j('/api/state?name='+encodeURIComponent(CUR));
  const s=STATE, gm=s.group_mode||{};
  $('sub').innerHTML=`实例 <b>${s.name}</b> · ${s.bot_running?'<span class="pill ok">进程在跑</span>':'<span class="pill bad">进程未运行</span>'} · ${s.ws_ok?'<span class="pill ok">NapCat 在线</span>':'<span class="pill bad">NapCat 未连</span>'}`;
  $('state').innerHTML=`<span class="pill">主人 ${s.owner_id||'未设'}</span> <span class="pill">称呼 ${s.owner_name||'-'}</span> <span class="pill">模型 ${s.model||'-'}</span> <span class="pill">群 ${s.groups.length} · 好友 ${s.friends.length}</span>`;
  $('ws').value=s.ws||'';
  $('groups').value=(s.groups||[]).join(',');
  $('friends').value=(s.friends||[]).join(',');
  renderQR(s);
  await loadPersonas(); loadQQ(); loadPause(); loadData(); loadMemes();
  await loadConfig();
  try{ const sn=await j('/api/sensitive?name='+encodeURIComponent(CUR)); $('sens').value=sn.text||''; }catch(e){}
  loadLogs();
}

let QRM=0;
function renderQR(s){
  if(!s.qr_mtime){$('qrbox').innerHTML='<span class="muted">还没有二维码文件</span>';return}
  if(s.qr_mtime===QRM) return;          // 没变就不重绘，避免闪烁/卡顿
  QRM=s.qr_mtime;
  $('qrbox').innerHTML=`<img class="qr" src="/api/qr?name=${encodeURIComponent(CUR)}&t=${s.qr_mtime}">
    <div class="muted" style="margin-top:6px">生成于 ${fmt(s.qr_mtime)}</div>`;
}

async function loadLogs(){
  const d=await j('/api/logs?name='+encodeURIComponent(CUR));
  const el=$('logs'); el.textContent=d.log||'（暂无日志）'; el.scrollTop=el.scrollHeight;
}

let MODEL_LIST=[];

async function loadModels(){
  const sel=document.getElementById('f_api_model');
  const cur=sel?sel.value:'';
  try{
    const d=await j('/api/models?name='+encodeURIComponent(CUR));
    if(!d.models||!d.models.length) return;
    MODEL_LIST=d.models;
    if(sel){
      sel.innerHTML=d.models.map(m=>`<option value="${m}">${m}</option>`).join('');
      if(d.models.includes(cur)) sel.value=cur;
    }
  }catch(e){ /* 静默：允许手填 */ }
}

let SCHEMA=null, VALUES=null, CURTAB=null;

/* ---------- 主题 ---------- */
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('qqbot-theme', t);
  document.querySelectorAll('.themebtn').forEach(b=>b.classList.toggle('on', b.dataset.t===t));
}
function setTheme(t){ applyTheme(t); toast('主题已切换：'+({system:'跟随系统',dark:'深色',light:'浅色'})[t]); }
(function(){ const t=localStorage.getItem('qqbot-theme')||'system'; applyTheme(t); })();
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', ()=>{
  if((localStorage.getItem('qqbot-theme')||'system')==='system') applyTheme('system');
});

/* ---------- 吐司 / 模态框（替代原生 alert/confirm） ---------- */
function toast(msg){
  const el=document.createElement('div'); el.className='tst'; el.textContent=msg;
  document.getElementById('toast').appendChild(el);
  setTimeout(()=>{ el.style.opacity='0'; el.style.transition='opacity .25s'; setTimeout(()=>el.remove(),260); }, 2600);
}
window.alert = toast;

function dialog(msg, {title='提示', ok='确定', cancel=null}={}){
  return new Promise(res=>{
    const mask=document.getElementById('mask');
    document.getElementById('dlgtitle').textContent=title;
    document.getElementById('dlgmsg').textContent=msg;
    const yes=document.getElementById('dlgyes'), no=document.getElementById('dlgno');
    yes.textContent=ok; no.textContent=cancel||'取消';
    no.style.display = cancel? '' : 'none';
    const close=v=>{ mask.classList.remove('on'); yes.onclick=null; no.onclick=null; res(v); };
    yes.onclick=()=>close(true);
    no.onclick=()=>close(false);
    mask.onclick=e=>{ if(e.target===mask) close(false); };
    mask.classList.add('on');
    yes.focus();
  });
}
const confirmBox = (msg, title='请确认') => dialog(msg, {title, ok:'确定', cancel:'取消'});

let TOPSEC='bot';
function topTab(sec){
  TOPSEC=sec;
  document.querySelectorAll('.tnav').forEach(e=>e.classList.toggle('on', e.dataset.sec===sec));
  document.querySelectorAll('.card[data-sec]').forEach(e=>{
    e.style.display = (e.dataset.sec===sec)?'':'none';
  });
  if(sec==='napcat'){ loadNapcat(); loadNapcatWeb(); }
}

async function loadQQ(){
  try{
    const q=await j('/api/qq?name='+encodeURIComponent(CUR));
    const on = q.online ? '<span class="pill ok">在线</span>' : (q.logging_in?'<span class="pill bad">等待扫码</span>':'<span class="pill bad">离线</span>');
    $('accinfo').innerHTML = on +
      ` <span class="pill">QQ ${q.uin||'未知'}</span>` +
      ` <span class="pill">昵称 ${(q.nickname||'未取到')}</span>` +
      ` <span class="pill">主人 ${q.owner_name||''}(${q.owner_id||''})</span>` +
      (q.login_time?`<div class="muted" style="margin-top:6px">登录于 ${new Date(q.login_time*1000).toLocaleString('zh-CN')}</div>`:'');
  }catch(e){ $('accinfo').textContent='账号信息读取失败'; }
}


async function openQQ(){
  const r=await j('/api/qq/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:CUR})});
  alert(r.message); setTimeout(async()=>{await loadQQ(); await loadState();},1500);
}

/* ---------- 暂停 / 试聊 / 数据 / 表情包 / 备份 ---------- */
async function openUrl(kind){
  let url = '';
  try{
    const d = await j('/api/about');
    url = kind==='repo' ? d.repo : (kind==='rel' ? d.releases : d.issues);
    await j('/api/open-url',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url})});
    toast('已在浏览器打开');
  }catch(e){ toast('打开失败：'+e.message+'\n地址：'+url); }
  return false;
}

async function loadAbout(){
  try{
    const d=await j('/api/about');
    $('fapp').textContent=d.name;
    $('fver').textContent='版本 '+d.version;
    $('fauthor').textContent='作者 '+d.author;
    $('fbuild').textContent='构建 '+d.build+' · 面板仅监听 127.0.0.1';
    $('flink-repo').href=d.repo;
    $('flink-rel').href=d.releases;
    $('flink-iss').href=d.issues;
  }catch(e){}
}

async function loadPause(){
  try{
    const d=await j('/api/pause?name='+encodeURIComponent(CUR));
    $('pausestate').innerHTML = d.paused
      ? '<span class="pill bad">已暂停（不回复任何消息）</span>'
      : '<span class="pill ok">运行中</span>';
  }catch(e){ $('pausestate').textContent='读取失败'; }
}
async function setPause(v){
  await j('/api/pause',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:CUR,paused:v})});
  toast(v?'已暂停':'已恢复'); loadPause();
}
async function doTest(){
  const t=$('testin').value.trim(); if(!t) return;
  $('testout').textContent='思考中…';
  try{
    const r=await j('/api/test-chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR,text:t})});
    $('testout').textContent='🤖 '+r.reply;
  }catch(e){ $('testout').textContent='失败：'+e.message; }
}
async function loadData(){
  try{
    const d=await j('/api/data?name='+encodeURIComponent(CUR));
    $('afflist').innerHTML = d.affinity.length
      ? d.affinity.map(a=>`<div>${a.name||a.uid} <span class="pill">${a.score}</span></div>`).join('')
      : '<span class="muted">暂无记录</span>';
    $('memlist').innerHTML = d.memory.length
      ? d.memory.map(m=>`<div style="margin-bottom:6px">${m.uid}：${m.text}</div>`).join('')
      : '<span class="muted">暂无画像</span>';
  }catch(e){}
}
async function resetData(kind){
  if(!(await confirmBox('确定清空 '+kind+' ？不可恢复（建议先备份）','清空数据'))) return;
  const r=await j('/api/data/reset',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:CUR,kind})});
  toast(r.message); loadData();
}
async function loadMemes(){
  try{
    const d=await j('/api/memes?name='+encodeURIComponent(CUR));
    $('memes').innerHTML = d.groups.length
      ? d.groups.map(g=>`<div style="margin:4px 0"><span class="pill">${g.dir}</span> ${g.count} 张
          ${g.files.slice(0,6).map(f=>`<span class="muted"> ${f}</span>`).join('')}</div>`).join('')
      : '<span class="muted">还没有表情包目录</span>';
  }catch(e){}
}
async function uploadMeme(){
  const dir=$('memedir').value.trim() || '日常';
  const f=$('memefile').files[0];
  if(!f) return toast('先选一张图片');
  const b64=await new Promise(res=>{const r=new FileReader();r.onload=()=>res(r.result);r.readAsDataURL(f)});
  try{
    const r=await j('/api/memes/upload',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR,dir,filename:f.name,data:b64})});
    toast(r.message); loadMemes();
  }catch(e){ toast('失败：'+e.message); }
}
async function doRestore(){
  const f=$('restorefile').files[0];
  if(!f) return toast('先选备份 zip');
  if(!(await confirmBox('恢复会覆盖当前 config / persona / data，确定？','恢复备份'))) return;
  const b64=await new Promise(res=>{const r=new FileReader();r.onload=()=>res(r.result);r.readAsDataURL(f)});
  try{
    const r=await j('/api/restore',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR,zip_base64:b64})});
    toast(r.message);
  }catch(e){ toast('失败：'+e.message); }
}

async function loadConfig(){
  const d=await j('/api/config?name='+encodeURIComponent(CUR));
  SCHEMA=d.schema; VALUES=d.values;
  const tabs=Object.keys(SCHEMA);
  if(!CURTAB||!tabs.includes(CURTAB)) CURTAB=tabs[0];
  $('tabs').innerHTML=tabs.map(t=>`<div class="tab${t===CURTAB?' on':''}" onclick="pickTab('${t}')">${t}</div>`).join('');
  renderForm();
  try{
    const st=await j('/api/stats?name='+encodeURIComponent(CUR));
    $('sensinfo').textContent=`当前词条 ${st.sensitive_words} 条 · 好感记录 ${st.affinity_users} 人 · 画像 ${st.memory_users} 人`;
  }catch(e){}
}

function pickTab(t){ CURTAB=t; loadConfig(); }
function esc(v){ return String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;'); }

function renderForm(){
  const fields=SCHEMA[CURTAB]||[];
  $('form').innerHTML=fields.map(f=>{
    const v=VALUES[f.key];
    const id='f_'+f.key.replace(/\./g,'_');
    if(f.type==='bool')
      return `<div class="field"><label><input type="checkbox" id="${id}" ${v?'checked':''} style="width:auto;margin-right:8px">${f.label}</label></div>`;
    if(f.type==='secret'){
      const has = v && v!=='' ;
      return `<div class="field"><label>${f.label}${has?' <span class="pill ok">已设置</span>':''}</label>
        <input id="${id}" type="password" value="" autocomplete="off"
               placeholder="${has?'留空表示不修改':'还没有填'}">
        <div class="hint">${has?'要换就直接填新值':'这里必须填，否则连不上'}</div></div>`;
    }
    if(f.type==='list')
      return `<div class="field"><label>${f.label}</label><input id="${id}" value="${esc((v||[]).join(','))}"><div class="hint">逗号分隔</div></div>`;
    if(f.type==='pair'||f.type==='pairf')
      return `<div class="field"><label>${f.label}</label><input id="${id}" value="${esc((v||[]).join(','))}"><div class="hint">两个值，逗号分隔</div></div>`;
    if(f.type==='str')
      return `<div class="field"><label>${f.label}</label><input id="${id}" type="text" value="${esc(v)}" autocomplete="off">${f.hint?`<div class="hint">${f.hint}</div>`:''}</div>`;
    if(f.key==='api.model')
      return `<div class="field"><label>${f.label}</label>
        <div style="display:flex;gap:8px">
          <select id="${id}"><option value="${esc(v)}">${esc(v||'（先填地址和 Key）')}</option></select>
          <button class="ghost" style="flex:0 0 auto" onclick="loadModels()">拉取模型</button>
        </div>
        <div class="hint">按 API 地址自动拉 /models 列表；拉不到就直接手填</div></div>`;
    const t = (f.type==='num') ? 'number' : 'text';
    return `<div class="field"><label>${f.label}</label><input id="${id}" type="${t}" step="${f.step||'any'}" value="${esc(v)}">${f.hint?`<div class="hint">${f.hint}</div>`:''}</div>`;
  }).join('');
  // 基础页自动拉一次模型列表（静默，失败就算了）
  if(CURTAB==='基础'){
    setTimeout(()=>{ const sel=document.getElementById('f_api_model');
      if(sel && sel.options.length<=1) loadModels(); }, 300);
  }
}

async function saveTab(){
  const fields=SCHEMA[CURTAB]||[], values={};
  for(const f of fields){
    const el=document.getElementById('f_'+f.key.replace(/\./g,'_'));
    if(!el) continue;
    values[f.key]= f.type==='bool' ? el.checked : el.value;
  }
  const r=await j('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:CUR,values})});
  $('savehint').textContent=r.message;
  await loadConfig();
}

async function saveSens(){
  try{
    const r=await j('/api/sensitive',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:CUR,text:$('sens').value})});
    alert(r.message);
  }catch(e){alert('保存失败：'+e.message)}
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
  if(!(await confirmBox('删除人格文件 '+f+' ？','删除人格'))) return;
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

let AUTO_ON=false;
async function refreshAuto(){
  try{
    const a=await j('/api/autostart');
    AUTO_ON=!!a.enabled;
    $('autostart').innerHTML=a.enabled?'<span class="pill ok">已开启</span> '+a.detail:'<span class="pill">未开启</span> '+a.detail;
    if($('autobtn')){
      $('autobtn').textContent = AUTO_ON ? '关闭开机自启' : '开启开机自启';
      $('autobtn').className = AUTO_ON ? 'danger' : '';
    }
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
  const target = (typeof on === 'boolean') ? on : !AUTO_ON;
  const r=await j('/api/autostart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:target})});
  toast(r.enabled?'已开启开机自启':'已关闭开机自启'); refreshAuto();
}

async function doUpdate(){
  if(!(await confirmBox('从 GitHub 拉取最新代码？\n只覆盖 bot.py / panel.py / deploy 等代码文件，配置、人格、数据都不会动。','立即更新'))) return;
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
const QS=new URLSearchParams(location.search);
if('scrollRestoration' in history) history.scrollRestoration='manual';
if(!QS.get('scroll')){
  window.scrollTo(0,0);
  setTimeout(()=>window.scrollTo(0,0), 200);
  window.addEventListener('load', ()=>setTimeout(()=>window.scrollTo(0,0), 50));
}
boot(); loadAbout();
(()=>{const o=QS.get('open');
 if(o){setTimeout(()=>openUrl(o),900);} })();
(()=>{const n=parseInt(QS.get('scroll')||'0',10);
 if(n>0){setTimeout(()=>{const el=document.querySelectorAll('.card')[n-1];
   if(el) el.scrollIntoView({block:'start'});},800);}})();
topTab(new URLSearchParams(location.search).get('tab') || 'bot'); refreshAuto(); loadSettings(); setInterval(()=>{if(CUR)refreshLight()},5000); 
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
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
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
        if u.path == "/api/about":
            return self._json({"name": APP_NAME, "author": APP_AUTHOR, "version": APP_VERSION,
                               "build": local_version() or "-", "repo": REPO_URL,
                               "releases": RELEASES_URL, "issues": ISSUES_URL})
        if u.path == "/api/napcat/webui":
            inst = self._inst(name)
            home2 = instance_home(inst)
            candidates = [
                os.path.join(home2, "QQn/resources/app/napcat/config/webui.json"),
                os.path.join(home2, "qqhome/.config/QQ/NapCat/config/webui.json"),
                os.path.expanduser("~/.config/QQ/NapCat/config/webui.json"),
            ]
            token, port, host = "", 6099, "127.0.0.1"
            for f in candidates:
                try:
                    with open(f, encoding="utf-8") as fp:
                        d = json.load(fp)
                    token = d.get("token") or ""
                    port = int(d.get("port") or 6099)
                    host = d.get("host") or "127.0.0.1"
                    break
                except Exception:
                    continue
            conn_host = "127.0.0.1" if host in ("::", "0.0.0.0", "") else host
            url = "http://%s:%d/webui/%s" % (conn_host, port,
                                             ("?token=" + token) if token else "")
            return self._json({"url": url, "port": port, "running": port_open(conn_host, port),
                               "token_set": bool(token)})

        if u.path == "/api/napcat/net":
            inst = self._inst(name)
            return self._json(napcat_net(instance_home(inst)))

        if u.path == "/api/theme":
            inst = self._inst(name)
            tf = os.path.join(instance_home(inst), "data", ".theme")
            try:
                with open(tf, encoding="utf-8") as f:
                    return self._json({"theme": f.read().strip() or "system"})
            except Exception:
                return self._json({"theme": "system"})
        if u.path == "/api/pause":
            inst = self._inst(name)
            return self._json({"paused": pause_state(instance_home(inst))})
        if u.path == "/api/data":
            inst = self._inst(name)
            return self._json(data_summary(instance_home(inst)))
        if u.path == "/api/memes":
            inst = self._inst(name)
            return self._json(memes_info(instance_home(inst)))
        if u.path == "/api/backup":
            inst = self._inst(name)
            data = backup_zip(instance_home(inst))
            return self._send(200, data, "application/zip",
                              {"Content-Disposition": 'attachment; filename="qqbot-backup.zip"'})
        if u.path == "/api/models":
            inst = self._inst(name)
            home2 = instance_home(inst)
            cfg2 = read_json(os.path.join(home2, "config", "config.json"), {}) or {}
            api2 = cfg2.get("api") or {}
            base2 = (api2.get("base_url") or "").rstrip("/")
            key2 = api2.get("key") or ""
            if not base2 or not key2:
                return self._json({"error": "先填 API 地址和 Key"}, 400)
            try:
                raw2 = fetch_url(base2 + "/models", timeout=15,
                                 headers={"Authorization": "Bearer " + key2,
                                          "User-Agent": "qqbot-panel"})
                d2 = json.loads(raw2.decode("utf-8", "ignore"))
                ids2 = [m.get("id") for m in (d2.get("data") or []) if m.get("id")]
                return self._json({"models": ids2, "count": len(ids2)})
            except Exception as e2:
                return self._json({"error": "拉取失败：" + str(e2)[:140]}, 502)

        if u.path == "/api/qq":
            inst = self._inst(name)
            home2 = instance_home(inst)
            acc = qq_account(home2)
            cfg = read_json(os.path.join(home2, "config", "config.json"), {}) or {}
            ws = cfg.get("napcat_ws") or ""
            import re as _re
            m = _re.search(r":(\d+)", ws)
            port = int(m.group(1)) if m else 3000
            acc["online"] = port_open("127.0.0.1", port)
            acc["logging_in"] = port_open("127.0.0.1", 6099) and not acc["online"]
            return self._json(acc)
        if u.path == "/api/qq/window":
            data = qq_window_png()
            if not data:
                return self._json({"error": "取不到窗口截图（Xvfb :99 或 ffmpeg 不可用）"}, 404)
            return self._send(200, data, "image/png")
        if u.path == "/api/config":
            inst = self._inst(name)
            return self._json({"schema": SCHEMA, "values": config_view(instance_home(inst))})
        if u.path == "/api/sensitive":
            inst = self._inst(name)
            pth = os.path.join(instance_home(inst), "data", "sensitive.json")
            try:
                with open(pth, encoding="utf-8") as f:
                    return self._json({"path": pth, "text": f.read()})
            except Exception as e:
                return self._json({"path": pth, "text": "", "error": str(e)[:100]})
        if u.path == "/api/stats":
            inst = self._inst(name)
            home2 = instance_home(inst)
            aff = read_json(os.path.join(home2, "data", "affinity.json"), {}) or {}
            mem = read_json(os.path.join(home2, "data", "user_memory.json"), {}) or {}
            sen = read_json(os.path.join(home2, "data", "sensitive.json"), {}) or {}
            words = sum(len(v) for v in (sen.get("categories") or {}).values() if isinstance(v, list))
            return self._json({"affinity_users": len(aff), "memory_users": len(mem),
                               "sensitive_words": words})
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

        if u.path == "/api/open-url":
            url = (body.get("url") or "").strip()
            allowed = (REPO_URL, RELEASES_URL, ISSUES_URL)
            # NapCat WebUi（本机 127.0.0.1 的 /webui/ 路径）也允许交给浏览器
            local_webui = bool(re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/webui/", url))
            if url not in allowed and not local_webui:
                return self._json({"error": "只允许打开本项目的 GitHub 页面"}, 400)
            try:
                if os.name == "nt":
                    os.startfile(url)  # noqa
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", url])
                else:
                    subprocess.Popen(["xdg-open", url],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._json({"message": "已在浏览器打开：" + url})
            except Exception as e:
                return self._json({"error": "打不开：%s" % str(e)[:100]}, 500)

        if u.path == "/api/theme":
            t = body.get("theme") or "system"
            if t not in ("system", "dark", "light"):
                t = "system"
            os.makedirs(os.path.join(home, "data"), exist_ok=True)
            with open(os.path.join(home, "data", ".theme"), "w", encoding="utf-8") as f:
                f.write(t)
            return self._json({"message": "主题已保存", "theme": t})

        if u.path == "/api/pause":
            st = set_pause(home, bool(body.get("paused")))
            return self._json({"paused": st, "message": "已暂停" if st else "已恢复"})

        if u.path == "/api/test-chat":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json({"error": "内容为空"}, 400)
            reply, err = test_chat(home, text)
            if err:
                return self._json({"error": err}, 502)
            return self._json({"reply": reply})

        if u.path == "/api/data/reset":
            kind = body.get("kind") or "affinity"
            n = 0
            if kind in ("affinity", "both"):
                fp = os.path.join(home, "data", "affinity.json")
                if os.path.exists(fp):
                    os.remove(fp); n += 1
            if kind in ("memory", "both"):
                fp = os.path.join(home, "data", "user_memory.json")
                if os.path.exists(fp):
                    os.remove(fp); n += 1
            return self._json({"message": "已清空 %d 个数据文件" % n})

        if u.path == "/api/memes/upload":
            import base64
            d = os.path.basename(body.get("dir") or "")
            nm = os.path.basename(body.get("filename") or "")
            raw = body.get("data") or ""
            if not d or not nm or not raw:
                return self._json({"error": "参数不全"}, 400)
            if not nm.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                return self._json({"error": "只支持图片扩展名"}, 400)
            info = memes_info(home)
            base = os.path.join(info["dir"], d)
            os.makedirs(base, exist_ok=True)
            try:
                blob = base64.b64decode(raw.split(",")[-1])
            except Exception:
                return self._json({"error": "图片数据无效"}, 400)
            if len(blob) > 3 * 1024 * 1024:
                return self._json({"error": "超过 3MB"}, 400)
            with open(os.path.join(base, nm), "wb") as f:
                f.write(blob)
            return self._json({"message": "已上传 %s/%s" % (d, nm)})

        if u.path == "/api/memes/delete":
            info = memes_info(home)
            d = os.path.basename(body.get("dir") or "")
            nm = os.path.basename(body.get("filename") or "")
            fp = os.path.join(info["dir"], d, nm)
            if os.path.exists(fp):
                os.remove(fp)
                return self._json({"message": "已删除"})
            return self._json({"error": "文件不存在"}, 404)

        if u.path == "/api/restore":
            raw = body.get("zip_base64") or ""
            if not raw:
                return self._json({"error": "没有备份数据"}, 400)
            import base64
            try:
                data = base64.b64decode(raw.split(",")[-1])
                n = restore_zip(home, data)
            except Exception as e:
                return self._json({"error": "恢复失败：%s" % str(e)[:120]}, 400)
            return self._json({"message": "已恢复 %d 个文件（重启后生效）" % n})

        if u.path == "/api/napcat/net":
            inst = self._inst(body.get("name"))
            home2 = instance_home(inst)
            host = (body.get("host") or "127.0.0.1").strip()
            try:
                port = int(body.get("port") or 3000)
            except Exception:
                return self._json({"error": "端口必须是数字"}, 400)
            if not (1 <= port <= 65535):
                return self._json({"error": "端口范围 1~65535"}, 400)
            token = body.get("token") or ""
            ok, msg = napcat_set_net(home2, host, port, token or None)
            if not ok:
                return self._json({"error": msg}, 400)
            restarted = False
            if body.get("restart"):
                _run(["systemctl", "restart", "qqbot-napcat.service"], timeout=30)
                time.sleep(2)
                restarted = True
            if body.get("reconnect_bot"):
                bot_process_control(home2, "restart")
            return self._json({"ok": True, "message": msg, "restarted": restarted,
                               "net": napcat_net(home2)})

        if u.path == "/api/qr/refresh":
            inst = self._inst(body.get("name"))
            h2 = instance_home(inst)
            qr = os.path.join(h2, inst.get("qr") or "QQn/resources/app/napcat/cache/qrcode.png")
            try:
                since = float(body.get("since") or 0)
            except Exception:
                since = 0
            mt = int(os.path.getmtime(qr)) if os.path.exists(qr) else 0
            if mt and abs(mt - since) >= 1:
                return self._json({"ok": True, "changed": True, "mtime": mt,
                                   "message": "二维码已更新"})
            # 文件没变 → 说明 QQ 没有重新生成，重启到登录界面逼它出新码
            stamp = os.path.join(h2, "data", ".qrrefresh")
            now = time.time()
            try:
                last = float(open(stamp, encoding="utf-8").read().strip() or 0)
            except Exception:
                last = 0
            if now - last < 25:
                return self._json({"ok": False, "changed": False,
                                   "message": "刚重启过，等 %d 秒再试" % int(25 - (now - last))}, 429)
            try:
                os.makedirs(os.path.dirname(stamp), exist_ok=True)
                with open(stamp, "w", encoding="utf-8") as f:
                    f.write(str(now))
            except Exception:
                pass
            open_qq(h2)          # 窗口检测不一定成功，真正标准是二维码文件有没有更新
            for _ in range(32):  # 最多等 48 秒
                time.sleep(1.5)
                mt2 = int(os.path.getmtime(qr)) if os.path.exists(qr) else 0
                if mt2 and mt2 != mt:
                    return self._json({"ok": True, "changed": True, "mtime": mt2,
                                       "message": "二维码已重新生成"})
            return self._json({"ok": False, "changed": False,
                               "message": "等了一分钟还没生成新码，检查 QQ 是否正常启动"})

        if u.path == "/api/qq/open":
            ok, msg = open_qq(home)
            return self._json({"ok": ok, "message": msg})

        if u.path == "/api/config":
            path = os.path.join(home, "config", "config.json")
            cfg = read_json(path, {}) or {}
            values = body.get("values") or {}
            changed = []
            for key, val in values.items():
                field = None
                for fields in SCHEMA.values():
                    for f in fields:
                        if f["key"] == key:
                            field = f
                            break
                    if field:
                        break
                if not field:
                    continue
                if field["type"] == "secret" and (val == MASK or val == ""):
                    continue
                if field["type"] == "list":
                    val = [x.strip() for x in str(val).split(",") if x.strip()]
                elif field["type"] == "num":
                    try:
                        fv = float(val)
                        val = int(fv) if fv == int(fv) else fv
                    except Exception:
                        continue
                elif field["type"] == "bool":
                    val = bool(val)
                elif field["type"] in ("pair", "pairf"):
                    try:
                        parts = [x.strip() for x in str(val).replace("，", ",").split(",")]
                        val = [float(parts[0]), float(parts[1])] if field["type"] == "pairf" else [int(parts[0]), int(parts[1])]
                    except Exception:
                        continue
                set_dotted(cfg, key, val)
                changed.append(key)
            write_json(path, cfg)
            return self._json({"message": "已保存 %d 项（重启后生效）" % len(changed), "changed": changed})

        if u.path == "/api/sensitive":
            pth = os.path.join(home, "data", "sensitive.json")
            text = body.get("text", "")
            try:
                json.loads(text)
            except Exception as e:
                return self._json({"error": "不是合法 JSON：" + str(e)[:80]}, 400)
            with open(pth, "w", encoding="utf-8") as f:
                f.write(text)
            return self._json({"message": "敏感词库已保存（5 秒内生效）"})

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
                if detect_bot_pids(home) or proc_alive(_procs.get(inst["name"])):
                    ok, msg = True, "已在运行"
                else:
                    ok, msg = bot_process_control(home, "start")
            elif action == "stop":
                if proc_alive(_procs.get(inst["name"])):
                    ok, msg = stop_instance(inst["name"])
                else:
                    ok, msg = bot_process_control(home, "stop")
            elif action == "restart":
                if proc_alive(_procs.get(inst["name"])):
                    stop_instance(inst["name"]); time.sleep(0.5)
                    ok, msg = start_instance(inst["name"])
                else:
                    ok, msg = bot_process_control(home, "restart")
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
