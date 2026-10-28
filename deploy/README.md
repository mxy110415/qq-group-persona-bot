# 部署说明

两份 systemd 单元：先 NapCat（含 Xvfb），再 bot。

## 1. 改路径

把两份 `.service` 里的 `/path/to/...` 和 `<你的用户>` 换成实际值：

```
/path/to/QQn                 QQ 客户端副本目录（NapCat 已注入）
/path/to/qqdata              QQ 用户数据目录
/path/to/qq-group-persona-bot 本仓库目录
<你的用户>                   运行用户
```

## 2. 安装

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qqbot-napcat.service qqbot-client.service
```

## 3. 看状态

```bash
systemctl status qqbot-napcat qqbot-client --no-pager
journalctl -u qqbot-client -f
tail -f /var/log/qqbot.log
```

## 4. 首次登录

NapCat 首次启动会把二维码存到：

```
<QQn>/resources/app/napcat/cache/qrcode.png
```

把它取出来用手机 QQ 扫。登录态存在 QQD 用户数据目录，之后正常重启一般能快速登录；
**进程被强杀（SIGKILL）或崩溃时，下次多半要重新扫码**，所以单元里设了 `TimeoutStopSec=90`。

## 5. 无 sudo / 无 systemd 的环境

直接跑：

```bash
Xvfb :99 -screen 0 1280x900x24 &
DISPLAY=:99 /path/to/QQn/qq --no-sandbox &
python3 bot.py
```
