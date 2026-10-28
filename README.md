# QQ 群聊人格机器人

> 一个有固定人设、长期记忆与好感度的 QQ 群聊 AI，基于 OneBot11 + NapCat + DeepSeek。

不是那种「@ 一下就回一句罐头话」的机器人。它会**潜水观察群聊、按冷热场决定插不插话**，记得每个人说过什么、跟你熟到什么程度，说话带情绪、会损人、会撒娇 —— 人设是写死在配置里的，不是每次临场编。

---

## 它和普通 QQ 机器人的区别

| 普通机器人 | 这个 |
|---|---|
| 每条消息都回，像客服 | 潜水为主，冷场时不说话，热场才插嘴 |
| 每次都重新认识你 | 记录用户画像、好感度、长期印象 |
| 回答千篇一律 | 反模板闸 + 去句末句号，尽量不像 AI |
| 什么都说 | 政治/违法/色情内容硬拦截，群聊直接静默 |
| 只认文字 | 能看图（多模态）、能解析卡片/合并转发、能抓 B 站视频信息 |

---

## 功能

### 群聊行为
- **冷热场动态权重**：群沉寂越久越不说话；10 分钟内有人聊过天，插话概率上调（最高 ×1.5）
- **群友疲劳度**：同一个人 120 秒内 @ 5 次自动冷却 60 秒（主人豁免），防止被刷屏
- **禁言实时检测**：被禁言后停止发言，用 `get_group_member_info` 每 5 秒核实
- **低信息量过滤**：`哈哈` `666` `好的` `收到` 这类不触发
- **反模板闸**：过滤「作为一个 AI」「希望能帮到你」等典型 AI 腔
- **去句末句号**：`行吧。` → `行吧`，更像真人打字
- **多段发送**：长回复按标点切成 1~3 条短消息
- **戳一戳回应**：被戳会回应，可在群配置里关掉

### 记忆
- **群级 / 私聊独立上下文**，超长自动压缩
- **用户画像**：`/memory` 可查，长期印象注入 system prompt
- **好感度**：随对话评估增减，影响语气；`/好感度` 可查
- **群级独立配置**：每个群单独设插话概率、静默时段、表情包开关

### 消息处理
- **图片识别**：走 DeepSeek 多模态，不需要额外视觉模型
- **链接解析**：B 站走官方 API 拿标题/UP 主/播放量/分P；其他站点抓网页标题
- **卡片消息**：QQ 小程序、分享卡片能读出文字
- **合并转发展开**：前 8 条
- **外链混淆**：防止被判定为引流

### 自动更新（三种模式，默认关闭）

```
off    完全手动，只有点「立即更新」才更新
check  到了间隔自动查一次 GitHub，发现新版本只在面板上提示
apply  到了间隔自动下载安装，重启后生效
```

检查间隔可设（默认 6 小时）。默认值 `off`，需要你在面板里主动开。

### 路径：固定，不是随机

所有文件都跟着**脚本所在目录**走，不会写到随机位置：

```
<安装目录>/
  bot.py  panel.py  README.md
  config/      配置与白名单
  persona/     人格
  data/        会话状态、版本记录
  instances/   多开实例（可在面板里改目录名/位置）
  panel-settings.json   面板自身设置
```

系统里唯一的外部路径是自启用的 unit（Linux: `~/.config/systemd/user/qqbot-panel.service`）。
面板「路径」卡片会把当前真实路径全部列出来，方便确认。

### 开机自启与自动更新

面板底部有「自启与更新」卡片：

```
开机自启   一键开关（Linux 写 systemd 用户服务，Windows 写启动文件夹快捷方式）
版本       显示本地版本与 GitHub 最新提交
检查更新   读 github.com/<仓库>/commits/main
立即更新   下载 tarball，只覆盖代码文件
```

**更新只动代码，不碰你的数据**：

```
覆盖：bot.py / panel.py / deploy/ / README.md / LICENSE
不动：config/（配置与白名单）、persona/（人格）、data/（会话与状态）
```

Linux 上要开机（未登录桌面）也能跑用户服务，需要一次：

```bash
sudo loginctl enable-linger $USER
```

### 多套人格

`persona/` 下可以放任意多个 `.txt`，面板里：

```
下拉选择  当前编辑哪一套
新建      起个名字就生成一个空文件
保存      写回当前文件
保存并启用 写回并把 config.persona_file 指向它（重启 bot 生效）
删除      删掉这一套（默认 persona.txt 不允许删）
云端导入  填 https 地址，直接下载别人/自己仓库里的人格
```

配置里对应一个字段：

```json
{ "persona_file": "persona.txt" }
```

改它等于切人格，`bot.py` 启动时按这个文件名读 `persona/` 下的文件。

### 桌面应用形态（推荐）

不想开浏览器？用 `launcher.py` 一键装好并打开，它内部就是 GTK + WebKit2 原生窗口。
已装好之后也可以直接跑 `panel_app.py`：

```bash
python3 panel_app.py
```

它会先确保后台服务在跑，再打开一个 1120×840 的窗口，标题「QQ 群聊人格机器人 · 调试面板」。
缺 WebKit 时会自动退回默认浏览器打开。

放桌面快捷方式：

```bash
cp deploy/qqbot-panel.desktop ~/桌面/QQ机器人面板.desktop
chmod +x ~/桌面/QQ机器人面板.desktop
```

> Linux 桌面首次双击若提示「不受信任」，右键 →「允许启动」；
> Windows 直接双击 `panel_app.py`（需要 Python 环境）或在启动文件夹放个快捷方式。

### 安全
- **敏感词硬过滤**（不是靠模型自觉）：政治人物、敏感事件、分裂主张、违法违禁、色情低俗等 8 类
- **输入 / 输出双向拦截**：群聊命中完全静默，私聊一句岔开；日志只记「命中已拦截」，不落原文
- **词库外置** `sensitive.json`，改完 5 秒生效，不用重启
- **指令注入检测**：识别「忽略之前的设定」「开发者模式」这类越狱话术
- **主人开关**：`/暂停` `/恢复` 随时叫停

---

## 架构

```
QQ 客户端（LinuxQQ）
   └─ NapCat（注入，提供 OneBot11）
        └─ WebSocket ws://127.0.0.1:3000
             └─ bot.py（本项目）
                  ├─ 消息解析 / 过滤 / 行为决策
                  ├─ 上下文与记忆（JSON + 摘要压缩）
                  ├─ 敏感词过滤（sensitive.json）
                  └─ DeepSeek API（文本 + 图片）
```

---

## 目录结构

```
bot.py                      主程序（唯一入口）
persona/
  persona.txt               人格设定（纯文本，想换成谁就改成谁）
config/
  config.example.json       主配置示例（API、模型、行为参数）
  whitelist.example.json    群/私聊白名单示例
  group_configs.example.json 群级配置示例
data/
  sensitive.json            敏感词库（8 类，可直接改）
deploy/
  qqbot-napcat.service      NapCat + Xvfb 单元
  qqbot-client.service      bot 单元
  README.md                 部署步骤（改路径 → 安装 → 扫码）
meme-pack/
  开心/ 无语/ 生气/ ...     按情绪分类放表情包图片
```

运行时会在同目录生成 `config/config.json`、`config/whitelist.json`、`data/affinity.json` 等真实文件（已在 `.gitignore` 里）。

---

## 平台支持

| 平台 | 状态 | 说明 |
|---|---|---|
| **Linux** | ✅ 主要支持 | 仓库自带 systemd 单元 + Xvfb 无头方案 |
| **Windows** | ✅ 可运行 | 用 NapCat 的 Windows 版（`NapCat.Framework.zip`），手动启动 QQ，无需 Xvfb；systemd 换成任务计划或直接双击 |
| **macOS** | ⚠️ 未验证 | NapCat 对 macOS 支持有限 |

机器人本体（`bot.py`）只依赖 **OneBot11 协议**，不依赖操作系统：

```
任何能提供 OneBot11 反向/正向 WS 的实现都能接
（NapCat / go-cqhttp / Lagrange / LLOneBot ...）
```

只要把 `napcat_ws` 指向对应地址即可，不限于 NapCat。

---

## 三个程序的关系（互相独立）

```
bot.py       机器人本体   独立进程 / 独立服务
panel.py     面板后端     独立进程 / 独立服务（HTTP 6100）
panel_app.py 面板窗口     关掉它只关窗口，面板后端还在
launcher.py  启动器       检测安装 / 下载 / 打开面板
```

**关面板不影响机器人**，实测：

```
systemctl --user stop qqbot-panel   → 面板 inactive
systemctl is-active qqbot-client    → active（机器人照跑）
```

反过来也一样：机器人在面板里被停掉，面板仍可正常打开、改配置、再启动它。
面板里的「启动 / 停止 / 重启」对 systemd 服务和裸进程都生效（用户级服务无需 sudo）。

### 让面板能直接启停机器人

面板的「启动 / 停止 / 重启」按钮会优先用 systemd：

```
用户级服务（~/.config/systemd/user/qqbot-bot.service） → 面板可直接控制，无需密码
系统级服务（/etc/systemd/system/qqbot-client.service） → 需要管理员权限
裸进程（直接 python bot.py）                          → 按 PID 发信号
```

如果提示「需要管理员权限」，把机器人装成**用户级服务**即可（推荐）：

```ini
# ~/.config/systemd/user/qqbot-bot.service
[Unit]
Description=QQ Persona Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/qq-group-persona-bot
ExecStart=/path/to/qq-group-persona-bot/.venv/bin/python bot.py
Restart=always
RestartSec=10
LimitCORE=0

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now qqbot-bot.service
```

> 面板**不会**为了停止机器人去硬杀进程——系统级服务的 `Restart=always` 会把它拉起来，
> 结果变成两个实例同时跑（会重复发消息）。所以拿不到权限时只提示，不乱动。

## 三种获取方式

| 方式 | 适合谁 | 怎么用 |
|---|---|---|
| **全量包** `qqbot-v0.1.0-full.zip`（85KB） | 想一次拿全，离线部署 | 解压 → `python3 launcher.py` |
| **面板安装包** `qqbot-panel-installer.zip`（35KB） | 先要面板，再让面板去下程序 | 解压 → `python3 launcher.py` → 面板里选目录下载 |
| **命令行** `bootstrap.sh` | 无图形界面的服务器 | 见下一节，一条命令 |

两个 zip 都在 [Releases](https://github.com/mxy110415/qq-group-persona-bot/releases) 页面。

## 命令行远程安装（无图形界面）

### Linux / WSL / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/bootstrap.sh | bash
```

指定目录：

```bash
curl -fsSL https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/bootstrap.sh | bash -s -- ~/bots/qqbot
```

国内网络先设代理再跑：

```bash
export https_proxy=http://127.0.0.1:7897
curl -fsSL https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/bootstrap.sh | bash
```

脚本会：下载 launcher.py → 拉源码包 → 建 venv → 装 websockets → 生成配置。
全程不弹窗，适合服务器。

装完常驻：

```bash
cd ~/qq-group-persona-bot
nohup ./.venv/bin/python panel.py --port 6100 > panel.log 2>&1 &
nohup ./.venv/bin/python bot.py > bot.log 2>&1 &
```

或者用 `deploy/*.service` 装 systemd。

### Windows（PowerShell，命令行）

```powershell
irm https://raw.githubusercontent.com/mxy110415/qq-group-persona-bot/main/launcher.py -OutFile "$env:TEMP\qqbot-launcher.py"
py "$env:TEMP\qqbot-launcher.py" --dir "$env:USERPROFILE\qqbot" --yes
```

装完启动：

```powershell
cd "$env:USERPROFILE\qqbot"
.\.venv\Scripts\python.exe panel.py --port 6100
```

---

## 下载与安装（启动器）

不用命令行、不用脚本。仓库根目录的 **`launcher.py`** 就是入口：

```
双击桌面「QQ 机器人面板」快捷方式
   ↓
启动器检测本地有没有面板程序
   ├ 有 → 直接打开面板
   └ 没有 → 弹窗：选安装位置 + 是否下载 → 下载 → 装依赖 → 打开
```

命令行同样可用：

```bash
python3 launcher.py                       # 图形界面
python3 launcher.py --dir ~/qqbot --yes   # 静默下载到指定目录
```

启动器做的事：

```
① 从 GitHub 拉源码包（直连失败会自动试本机 7897/7890 代理）
② 解压到选定的目录（默认 ~/qq-group-persona-bot）
③ 用示例配置生成 config.json / whitelist.json / group_configs.json
④ 可选：建 .venv 并安装 websockets
⑤ 启动 panel.py 并打开窗口
```

安装位置记在 `~/.config/qqbot-launcher.json`，下次启动直接用，不再问。

> 也可以用 GitHub Release 右侧的 Source code (zip/tar.gz) 手动下载解压。

---

## 快速开始

### 前置

1. **LinuxQQ**（NT 版，实测 3.2.34）
2. **NapCat.Shell**（实测 4.18.41，注意不是 Framework 版 —— 那个是 Windows 的）
3. **DeepSeek API Key**

> NapCat 的安装是把它放进 QQ 的资源目录并替换入口，详见 NapCat 官方文档。本文不重复。

### 配置

```bash
cp config/config.example.json config/config.json
cp config/whitelist.example.json config/whitelist.json
cp config/group_configs.example.json config/group_configs.json
```

人格直接改 `persona/persona.txt`（纯文本，无需转义，重启或重新运行生效）。

`config.json` 里必填：

| 字段 | 说明 |
|---|---|
| `api.key` | DeepSeek API Key |
| `api.base_url` | `https://api.deepseek.com` |
| `api.model` | `deepseek-flash` |
| `owner_id` | 主人 QQ 号 |
| `admins` | 管理员 QQ 号数组 |
| `napcat_ws` | NapCat 的 WebSocket 地址 |
| `ws_token` | NapCat 配置里的 token |
| `owner_name` | 机器人在人格里怎么称呼主人（默认 `主人`） |

> 人格不在这里：改 `persona/persona.txt` 即可，程序启动时优先读它（文件不存在才回落到 `config.json` 的 `persona` 字段）。
> 人格里写 `{{OWNER}}` 表示主人的名字，运行时用 `owner_name` 替换 —— **真人名字不要写进人格文件**，留给部署者自己填。

### 白名单

```json
{
  "groups": ["123456789"],
  "friends": ["10001"]
}
```

**群和私聊都只回白名单内的**，不在名单里完全静默。

### 运行

```bash
pip install websockets
python bot.py
```

启动正常会看到：

```
[启动] 模型=deepseek-flash 私聊=白名单 群聊=潜水插嘴 概率=8.0% ...
[连接] 已连上 NapCat: ws://127.0.0.1:3000
[登录] 机器人 UIN = 3xxxxxxx
```

---

## 调试面板

仓库自带一个只用标准库的本地面板，**Linux / Windows 都能跑**：

```bash
python panel.py            # http://127.0.0.1:6100
python panel.py --port 6101
```

打开后能做的事：

| 区块 | 能调什么 |
|---|---|
| 实例 | 启动 / 重启 / 停止；**新建实例实现多开**（自动分配 OneBot 端口） |
| QQ 登录 | 直接显示 NapCat 的登录二维码，登录状态实时刷新 |
| 说话节奏 | 插话概率、冷却下限/上限（多少秒才能再说）、每小时上限、静默时段、触发词 |
| 白名单 | 群号、私聊号增删，保存即生效 |
| 人格 | 直接编辑 `persona/persona.txt`，保存后可一键重启使其生效 |
| 日志 | 最近 200 行，方便看为什么没说话 |

### 多开怎么工作

每个实例是**独立目录**：

```
instances/<实例名>/
  config/    该实例自己的配置与白名单
  persona/   该实例自己的人格
  data/      该实例的会话与状态
```

启动时面板用环境变量 `QQBOT_HOME` 指向该目录，`bot.py` 据此读写配置 —— 所以多个实例互不干扰。
新实例会拿到一个空闲端口（3001、3002…），NapCat 那边相应地开多个 OneBot 服务端即可。

### 安全

```
· 默认只绑 127.0.0.1，外网访问不到
· 要挂公网必须前面套一层带鉴权的反代（如 Cloudflare Tunnel + 登录页）
· 面板能改人格、能启停进程，等于半个管理员权限，别裸奔到公网
```

---

## 面板能力清单

| 分类 | 能做什么 |
|---|---|
| 实例 | 启动 / 停止 / 重启（systemd 或裸进程）、新建多开 |
| QQ 登录 | 二维码自动刷新、账号/昵称/主人/在线状态、「打开 QQ」 |
| 配置 | 基础 / 群聊 / 触发 / 记忆与限制 / 主动发言 / 成本 / 表情包（按 API 自动拉模型列表） |
| 白名单 | 群号、私聊号 |
| 人格 | 多套新建 / 保存 / 启用 / 删除 / 云端 https 拉取 |
| 开关 | **紧急暂停 / 恢复**（写 `data/paused.flag`，机器人每条消息前检查） |
| 试聊 | 用当前人格直接调模型，不进 QQ、不写上下文 |
| 记忆与好感 | 查看好感排行、用户画像，可单独清空 |
| 表情包 | 列目录、看张数、上传图片（≤3MB） |
| 敏感词 | 在线编辑 JSON，5 秒生效 |
| 备份 | 一键下载 zip（config + persona + data），可再导入恢复 |
| 日志 | 最近 120 行 |
| 系统 | 路径、开机自启、自动更新（off/check/apply） |

### 还没做的

- 群列表一键导入白名单：NapCat 目前只开了 WebSocket，没开 HTTP，需要额外开一个 HTTP 服务端才能拉群列表
- 面板自身登录鉴权：现在只绑 127.0.0.1，要挂公网必须先套反代

## 指令

| 指令 | 别名 | 权限 | 作用 |
|---|---|---|---|
| `/好感度` | `/好感` | 所有人 | 查自己好感度；主人可 `@某人` 或 `+10` `-10` `80` |
| `/memory` | `/记忆` | 所有人 | 查自己画像；主人可查别人 |
| `/暂停` | `/停止` | 主人 | 停止回复 |
| `/恢复` | `/继续` | 主人 | 恢复回复 |
| `/new` | `/清空` | 主人 | 清当前会话；`/new all` 清全部 |
| `/compress` | `/压缩` | 主人 | 立即压缩上下文 |

群里需要 **@机器人** 才算指令；非主人发其它斜杠命令会被静默丢弃（不暴露指令系统）。

---

## 配置项

### 全局 `config.json`

```jsonc
{
  "group_mode": {
    "chance": 0.08,          // 群聊插话基础概率
    "cooldown": [900, 2700], // 两次插话间隔（秒）
    "hourly_limit": 3,       // 每小时最多插话次数
    "quiet_hours": [2, 7]    // 静默时段
  },
  "name_triggers": ["伊蕾娜", "魔女"],   // 谁叫都响应
  "owner_only_triggers": ["老婆"]        // 只有主人说才响应
}
```

### 群级 `group_configs.json`

```jsonc
{
  "123456789": {
    "interject_prob": 0.08,
    "enable_interject": true,
    "enable_sleep": true,
    "quiet_hours": [2, 7],
    "enable_poke": true,
    "enable_meme": true,
    "meme_prob": 0.10
  }
}
```

### 敏感词 `sensitive.json`

```jsonc
{
  "categories": {
    "政治人物": ["..."],
    "敏感事件": ["..."]
  },
  "私聊回避话术": ["这个不聊，换个话题吧"]
}
```

改完保存即生效（服务端 5 秒缓存）。

---

## 部署（完整教程）

仓库里 `deploy/` 目录带了两份可用的 systemd 单元，按下面五步走。

### 第 1 步：准备 NapCat

1. 装 **LinuxQQ NT 版**
2. 把 **NapCat.Shell**（不是 Framework 版）注入到 QQ 资源目录
3. 复制一份 QQ 目录到你能改的地方，例如 `/path/to/QQn`

### 第 2 步：拉代码、写配置

```bash
git clone https://github.com/mxy110415/qq-group-persona-bot.git
cd qq-group-persona-bot
cp config/config.example.json config/config.json
cp config/whitelist.example.json config/whitelist.json
cp config/group_configs.example.json config/group_configs.json
```

改 `config/config.json`：`api.key`、`owner_id`、`owner_name`、`napcat_ws`、`ws_token`；
改 `config/whitelist.json`：把你的群号和 QQ 号填进去（**不在白名单里完全不响应**）。

### 第 3 步：装服务

```bash
sudo cp deploy/qqbot-napcat.service deploy/qqbot-client.service /etc/systemd/system/
# 先把两份文件里的 /path/to/... 和 <你的用户> 改成实际值
sudo systemctl daemon-reload
sudo systemctl enable --now qqbot-napcat qqbot-client
```

### 第 4 步：扫码登录

NapCat 首次启动会把二维码写到：

```
<QQn>/resources/app/napcat/cache/qrcode.png
```

取出来用手机 QQ 扫。登录成功后 NapCat 会监听 OneBot11 端口（默认 3000）。

### 第 5 步：确认

```bash
systemctl status qqbot-napcat qqbot-client --no-pager
ss -tln | grep -E ':(3000|6099)'
journalctl -u qqbot-client -f
```

看到下面这三行就说明通了：

```
[启动] 模型=... 私聊=白名单 群聊=潜水插嘴 ...
[连接] 已连上 NapCat: ws://127.0.0.1:3000
[登录] 机器人 UIN = ...
```

> 详细说明（含无 sudo 环境的手动启动方式）见 [`deploy/README.md`](deploy/README.md)。

---

## 已知限制

- **图片识别**依赖模型的多模态能力，不是所有 DeepSeek 端点都支持；发图失败会静默降级成纯文本
- **视频**只能拿到标题/简介，看不到画面（本地没有视频理解能力）
- **敏感词**挡不住谐音、拆字、拼音首字母，需要自己补词
- **LinuxQQ 重启后可能要求重新扫码**，快速登录在无头 Xvfb 环境下不稳定
- **表情包**需要自己往 `meme-pack/<情绪>/` 放图，空目录自动跳过
- 群聊插话靠概率 + 判断，**可能长时间不吭声**，这是设计而不是故障

---

## 致谢

初版 `bot.py` 来源于 [lifefuck/qq-deepseek-realistic-bot](https://github.com/lifefuck/qq-deepseek-realistic-bot)，
本仓库在其基础上做了大量重构与功能扩展（人格系统、安全过滤、多模态、群聊行为、记忆体系等）。

## 许可证

[MIT](LICENSE)
