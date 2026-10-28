#!/usr/bin/env bash
# QQ 群聊人格机器人 · 一键安装（Linux / macOS）
#
# 用法：
#   bash install.sh                 # 装到当前目录下 qq-group-persona-bot
#   bash install.sh --dir ~/bots/qq # 指定目录
#   bash install.sh --yes           # 全默认，不交互（CI/测试用）
#
# 做的事：拉代码 → 建 venv → 装依赖 → 生成配置 → （可选）装 systemd 自启

set -euo pipefail

REPO="mxy110415/qq-group-persona-bot"
BRANCH="main"
TARGET="qq-group-persona-bot"
ASSUME_YES=0
PROXY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) TARGET="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --proxy) PROXY="$2"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

say() { printf '%s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }

say ""
say "=============================================="
say " QQ 群聊人格机器人 · 一键安装"
say "=============================================="

# ---------- 1. 环境检查 ----------
say ""
say "[1/6] 检查环境"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then PY="$c"; break; fi
  fi
done
[ -n "$PY" ] || die "需要 Python 3.8+，没找到"
ok "Python: $($PY -V 2>&1)"

command -v git >/dev/null 2>&1 && ok "git: $(git --version | awk '{print $3}')" || warn "没装 git，将用 tarball 下载"

# ---------- 2. 取代码 ----------
say ""
say "[2/6] 获取代码 → $TARGET"
if [ -d "$TARGET/.git" ]; then
  ( cd "$TARGET" && git pull --ff-only ) && ok "已更新现有仓库"
elif [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
  die "目录 $TARGET 已存在且非空，换个 --dir"
else
  if command -v git >/dev/null 2>&1; then
    [ -n "$PROXY" ] && git -c "http.proxy=$PROXY" clone -q -b "$BRANCH" "https://github.com/$REPO.git" "$TARGET" \
                    || git clone -q -b "$BRANCH" "https://github.com/$REPO.git" "$TARGET"
    ok "git clone 完成"
  else
    URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
    mkdir -p "$TARGET"
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL ${PROXY:+-x "$PROXY"} "$URL" | tar xz -C "$TARGET" --strip-components=1
    else
      wget -qO- "$URL" | tar xz -C "$TARGET" --strip-components=1
    fi
    ok "tarball 解压完成"
  fi
fi

cd "$TARGET"

# ---------- 3. 虚拟环境 ----------
say ""
say "[3/6] 创建虚拟环境并安装依赖"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv || die "创建 venv 失败（Debian/Ubuntu 需要 python3-venv）"
fi
./.venv/bin/pip install -q --upgrade pip 2>/dev/null || true
./.venv/bin/pip install -q websockets ${PROXY:+--proxy "$PROXY"} || die "安装 websockets 失败"
ok "依赖就绪: $(./.venv/bin/python -c 'import websockets;print("websockets", websockets.__version__)')"

# ---------- 4. 生成配置 ----------
say ""
say "[4/6] 生成配置"
for pair in "config/config.example.json:config/config.json" \
            "config/whitelist.example.json:config/whitelist.json" \
            "config/group_configs.example.json:config/group_configs.json"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  if [ -f "$dst" ]; then
    warn "$dst 已存在，跳过"
  else
    cp "$src" "$dst"; ok "生成 $dst"
  fi
done
chmod 600 config/*.json 2>/dev/null || true

ask() {  # ask <提示> <默认值>
  local prompt="$1" def="$2" val=""
  if [ "$ASSUME_YES" = "1" ]; then echo "$def"; return; fi
  read -r -p "  $prompt [$def]: " val </dev/tty || val=""
  echo "${val:-$def}"
}

if [ "$ASSUME_YES" != "1" ]; then
  say ""
  say "  填写配置（回车用默认值，之后也能在面板里改）"
  KEY=$(ask "DeepSeek API Key" "")
  OWNER=$(ask "主人 QQ 号" "")
  NAME=$(ask "机器人怎么称呼主人" "主人")
  WS=$(ask "NapCat WebSocket 地址" "ws://127.0.0.1:3000")
  TOKEN=$(ask "NapCat Token（没有就留空）" "")
  GROUPS=$(ask "要聊的群号（逗号分隔，可留空）" "")

  "$PY" - "$KEY" "$OWNER" "$NAME" "$WS" "$TOKEN" "$GROUPS" <<'PY'
import json, sys
key, owner, name, ws, token, groups = sys.argv[1:7]
c = json.load(open("config/config.json"))
if key:   c["api"]["key"] = key
if owner: c["owner_id"] = owner; c["admins"] = [owner] if owner else []
if name:  c["owner_name"] = name
if ws:    c["napcat_ws"] = ws
if token: c["ws_token"] = token
json.dump(c, open("config/config.json","w"), ensure_ascii=False, indent=2)
w = json.load(open("config/whitelist.json"))
w["groups"] = [x.strip() for x in groups.split(",") if x.strip()]
if owner: w["friends"] = [owner]
json.dump(w, open("config/whitelist.json","w"), ensure_ascii=False, indent=2)
print("  配置已写入")
PY
fi

# ---------- 5. 自启（可选） ----------
say ""
say "[5/6] 开机自启（可选）"
if [ "$ASSUME_YES" = "1" ]; then
  warn "非交互模式，跳过自启"
elif command -v systemctl >/dev/null 2>&1; then
  read -r -p "  装成 systemd 服务（开机自启）？[y/N]: " ans </dev/tty || ans="n"
  if [ "${ans:-n}" = "y" ]; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/qqbot-panel.service" <<EOF
[Unit]
Description=QQ Persona Bot Panel
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/python $(pwd)/panel.py --port 6100
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    cat > "$UNIT_DIR/qqbot-client.service" <<EOF
[Unit]
Description=QQ Persona Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/python $(pwd)/bot.py
Restart=always
RestartSec=10
LimitCORE=0

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now qqbot-panel.service qqbot-client.service
    ok "已装并启动（面板 + 机器人）"
  else
    warn "跳过"
  fi
else
  warn "这台机器没有 systemd，跳过"
fi

# ---------- 6. 完成 ----------
say ""
say "[6/6] 完成"
say ""
say "  目录: $(pwd)"
say "  面板: http://127.0.0.1:6100 （或双击 panel_app.py / 桌面快捷方式）"
say ""
say "  还要做的："
say "    1) 装好 LinuxQQ + NapCat（见 README 部署章节）"
say "    2) 启动 NapCat，面板里扫码登录"
say "    3) 把机器人拉进群，在面板「白名单」里填群号"
say ""
say "  手动启动："
say "    ./.venv/bin/python bot.py        # 机器人"
say "    ./.venv/bin/python panel.py      # 面板"
say ""
