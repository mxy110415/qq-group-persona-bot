#!/usr/bin/env bash
# QQ 群聊人格机器人 · 命令行引导（纯 CLI Linux / WSL，无图形界面也能装）
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/bootstrap.sh | bash
#   curl -fsSL .../bootstrap.sh | bash -s -- ~/bots/qqbot    # 指定目录
#
# 走代理（国内常见）：
#   export https_proxy=http://127.0.0.1:7897
#   curl -fsSL .../bootstrap.sh | bash

set -euo pipefail

DIR="${1:-$HOME/qq-group-persona-bot}"
RAW="https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/launcher.py"

say() { printf '%s\n' "$*"; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then PY="$c"; break; fi
  fi
done
[ -n "$PY" ] || { say "需要 Python 3.8+，请先安装（apt install python3 python3-venv）"; exit 1; }

say "== QQ 群聊人格机器人 · 命令行安装 =="
say "   安装目录: $DIR"
say "   Python:   $($PY -V 2>&1)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LP="$TMP/launcher.py"

say "== 下载启动器 =="
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$RAW" -o "$LP"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$LP" "$RAW"
else
  say "需要 curl 或 wget"; exit 1
fi

say "== 拉取程序并初始化 =="
exec "$PY" "$LP" --dir "$DIR" --yes
