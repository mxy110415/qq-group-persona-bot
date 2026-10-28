#!/usr/bin/env bash
# 构建 .deb：在仓库根目录执行
#   VERSION=0.2.0 bash packaging/linux/build-deb.sh
# 产物：dist/qqbot-agent_<版本>_all.deb
set -euo pipefail

VERSION="${VERSION:-0.1.0}"
NAME="qqbot-agent"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="$ROOT/build/$NAME-$VERSION"
DIST="$ROOT/dist"
STAGE="$BUILD/opt/qqbot"

rm -rf "$BUILD"
mkdir -p "$STAGE" "$DIST"

# ---------- 代码 ----------
for f in bot.py panel.py panel_app.py launcher.py bootstrap.sh README.md LICENSE panel-icon.svg; do
  [ -f "$ROOT/$f" ] && cp "$ROOT/$f" "$STAGE/"
done
[ -f "$ROOT/bootstrap.sh" ] && chmod +x "$STAGE/bootstrap.sh"

mkdir -p "$STAGE/persona" "$STAGE/config" "$STAGE/data" "$STAGE/deploy" "$STAGE/meme-pack"
cp -r "$ROOT/persona/." "$STAGE/persona/" 2>/dev/null || true
cp -r "$ROOT/config/." "$STAGE/config/" 2>/dev/null || true
cp -r "$ROOT/deploy/." "$STAGE/deploy/" 2>/dev/null || true
cp -r "$ROOT/meme-pack/." "$STAGE/meme-pack/" 2>/dev/null || true

# 只装 example，真实配置首次启动时生成
rm -f "$STAGE/config/config.json" "$STAGE/config/whitelist.json" "$STAGE/config/group_configs.json"
rm -f "$STAGE/data/"*.json "$STAGE/data/.version" 2>/dev/null || true

# ---------- 启动脚本 ----------
mkdir -p "$BUILD/usr/bin"
cat > "$BUILD/usr/bin/qqbot-panel" <<'EOB'
#!/bin/sh
# 面板：优先用原生窗口，没有 GTK/WebKit 就退回浏览器
exec /usr/bin/python3 /opt/qqbot/panel_app.py
EOB
cat > "$BUILD/usr/bin/qqbot-server" <<'EOB'
#!/bin/sh
exec /usr/bin/python3 /opt/qqbot/panel.py "$@"
EOB
chmod 755 "$BUILD/usr/bin/qqbot-panel" "$BUILD/usr/bin/qqbot-server"

# ---------- 桌面图标与菜单 ----------
mkdir -p "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/scalable/apps"
cp "$ROOT/panel-icon.svg" "$BUILD/usr/share/icons/hicolor/scalable/apps/qqbot-panel.svg"
cat > "$BUILD/usr/share/applications/qqbot-panel.desktop" <<'EOD'
[Desktop Entry]
Type=Application
Name=QQ 群聊人格机器人
Name[zh_CN]=QQ 群聊人格机器人
Comment=调试面板：实例、登录、人格、白名单、日志
Exec=qqbot-panel
Icon=qqbot-panel
Terminal=false
Categories=Network;Chat;
Keywords=QQ;Bot;NapCat;
EOD

# ---------- systemd 用户单元（系统级安装，任何用户都能 --user 启动）----------
mkdir -p "$BUILD/usr/lib/systemd/user"
cat > "$BUILD/usr/lib/systemd/user/qqbot-panel.service" <<'EOU'
[Unit]
Description=QQ 群聊人格机器人 调试面板
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/qqbot/panel.py --host 127.0.0.1 --port 6100
Restart=on-failure
RestartSec=3
WorkingDirectory=/opt/qqbot

[Install]
WantedBy=default.target
EOU

# ---------- control ----------
mkdir -p "$BUILD/DEBIAN"
INSTALLED_SIZE=$(du -sk "$BUILD" | cut -f1)
cat > "$BUILD/DEBIAN/control" <<EOC
Package: $NAME
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Maintainer: 星染 (mxy110415) <mxy110415@users.noreply.github.com>
Homepage: https://github.com/mxy110415/qq-group-persona-bot
Description: QQ 群聊人格机器人（含调试面板）
 有固定人设、长期记忆与好感度的群聊 AI，基于 OneBot11 + NapCat + DeepSeek。
 自带桌面调试面板：实例启停、多开、扫码登录、人格编辑、白名单、敏感词、日志。
Depends: python3 (>= 3.8), python3-websockets
Recommends: python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, curl
EOC

cat > "$BUILD/DEBIAN/postinst" <<'EOP'
#!/bin/sh
set -e
# 让安装者本人能直接读写 /opt/qqbot（单用户桌面场景）
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  chown -R "$SUDO_USER":"$SUDO_USER" /opt/qqbot || true
fi
chmod 755 /opt/qqbot
# 首次安装生成配置骨架
if [ ! -f /opt/qqbot/config/config.json ] && [ -f /opt/qqbot/config/config.example.json ]; then
  cp /opt/qqbot/config/config.example.json /opt/qqbot/config/config.json
fi
if [ ! -f /opt/qqbot/config/whitelist.json ] && [ -f /opt/qqbot/config/whitelist.example.json ]; then
  cp /opt/qqbot/config/whitelist.example.json /opt/qqbot/config/whitelist.json
fi
systemctl daemon-reload || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q || true
echo "安装完成：命令 qqbot-panel（面板） / qqbot-server（仅后端）。"
echo "开机自启：systemctl --user enable --now qqbot-panel"
exit 0
EOP

cat > "$BUILD/DEBIAN/prerm" <<'EOP'
#!/bin/sh
set -e
systemctl --user stop qqbot-panel 2>/dev/null || true
exit 0
EOP

chmod 755 "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/prerm"

chmod -R go-w "$BUILD/opt" || true
dpkg-deb --build --root-owner-group "$BUILD" "$DIST/${NAME}_${VERSION}_all.deb" >/dev/null
echo "已生成：$DIST/${NAME}_${VERSION}_all.deb"
