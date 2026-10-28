# QQ 群聊人格机器人 · Windows 一键安装
#
# 用法（PowerShell，建议以普通用户身份）：
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   或右键「使用 PowerShell 运行」
#
# 做的事：下载代码 → 建 venv → 装依赖 → 生成配置 → 桌面快捷方式

param(
    [string]$Dir = "$env:USERPROFILE\qq-group-persona-bot",
    [string]$Branch = "main",
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$Repo = "mxy110415/qq-group-persona-bot"

function Say($m) { Write-Host $m }
function Ok($m)  { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die($m) { Write-Host "  [X]  $m" -ForegroundColor Red; exit 1 }

Say ""
Say "=============================================="
Say " QQ 群聊人格机器人 · Windows 一键安装"
Say "=============================================="

# ---------- 1. 检查 Python ----------
Say ""
Say "[1/5] 检查 Python"
$py = $null
foreach ($c in @("py", "python")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        try {
            $v = & $c -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]"3.8") { $py = $c; break }
        } catch {}
    }
}
if (-not $py) { Die "需要 Python 3.8+，请先安装 https://www.python.org/downloads/（勾选 Add to PATH）" }
Ok "Python $(& $py -V 2>&1)"

# ---------- 2. 下载代码 ----------
Say ""
Say "[2/5] 下载代码 -> $Dir"
if (Test-Path (Join-Path $Dir ".git")) {
    Push-Location $Dir; git pull --ff-only; Pop-Location
    Ok "已更新现有仓库"
} elseif ((Test-Path $Dir) -and (Get-ChildItem $Dir -Force | Select-Object -First 1)) {
    Die "目录已存在且非空：$Dir（换个 -Dir）"
} else {
    $url = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
    $zip = Join-Path $env:TEMP "qqbot.zip"
    $tmp = Join-Path $env:TEMP "qqbot-unzip"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (Test-Path $Dir) { Remove-Item $Dir -Recurse -Force }
    Move-Item $inner.FullName $Dir
    Ok "下载并解压完成"
}

Set-Location $Dir

# ---------- 3. 虚拟环境 ----------
Say ""
Say "[3/5] 创建虚拟环境并安装依赖"
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -q websockets
if ($LASTEXITCODE -ne 0) { Die "安装 websockets 失败" }
Ok "依赖就绪"

# ---------- 4. 配置 ----------
Say ""
Say "[4/5] 生成配置"
$pairs = @(
    @("config\config.example.json", "config\config.json"),
    @("config\whitelist.example.json", "config\whitelist.json"),
    @("config\group_configs.example.json", "config\group_configs.json")
)
foreach ($p in $pairs) {
    if (Test-Path $p[1]) { Warn "$($p[1]) 已存在，跳过" }
    else { Copy-Item $p[0] $p[1]; Ok "生成 $($p[1])" }
}

if (-not $Yes) {
    Say ""
    Say "  填写配置（直接回车用默认值，之后也能在面板里改）"
    $key   = Read-Host "  DeepSeek API Key"
    $owner = Read-Host "  主人 QQ 号"
    $name  = Read-Host "  机器人怎么称呼主人 [主人]"
    if (-not $name) { $name = "主人" }
    $ws    = Read-Host "  NapCat 地址 [ws://127.0.0.1:3000]"
    if (-not $ws) { $ws = "ws://127.0.0.1:3000" }
    $token = Read-Host "  NapCat Token（可留空）"
    $groups= Read-Host "  要聊的群号（逗号分隔，可留空）"

    $env:QB_KEY=$key; $env:QB_OWNER=$owner; $env:QB_NAME=$name
    $env:QB_WS=$ws; $env:QB_TOKEN=$token; $env:QB_GROUPS=$groups
    & ".\.venv\Scripts\python.exe" -c @"
import json, os
c = json.load(open('config/config.json', encoding='utf-8'))
k, o, n = os.environ.get('QB_KEY'), os.environ.get('QB_OWNER'), os.environ.get('QB_NAME')
ws, tk, gs = os.environ.get('QB_WS'), os.environ.get('QB_TOKEN'), os.environ.get('QB_GROUPS')
if k: c['api']['key'] = k
if o: c['owner_id'] = o; c['admins'] = [o]
if n: c['owner_name'] = n
if ws: c['napcat_ws'] = ws
if tk: c['ws_token'] = tk
json.dump(c, open('config/config.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)
w = json.load(open('config/whitelist.json', encoding='utf-8'))
w['groups'] = [x.strip() for x in (gs or '').split(',') if x.strip()]
if o: w['friends'] = [o]
json.dump(w, open('config/whitelist.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)
print('  配置已写入')
"@
}

# ---------- 5. 快捷方式 ----------
Say ""
Say "[5/5] 创建桌面快捷方式"
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "QQ机器人面板.lnk"
$wshell = New-Object -ComObject WScript.Shell
$sc = $wshell.CreateShortcut($lnk)
$sc.TargetPath = (Join-Path $Dir ".venv\Scripts\pythonw.exe")
$sc.Arguments = "`"$(Join-Path $Dir 'panel.py')`" --port 6100"
$sc.WorkingDirectory = $Dir
$sc.Description = "QQ 群聊人格机器人调试面板"
$sc.Save()
Ok "桌面快捷方式: $lnk"

Say ""
Say "=============================================="
Say " 完成"
Say "=============================================="
Say "  目录: $Dir"
Say "  面板: http://127.0.0.1:6100"
Say ""
Say "  还要做的："
Say "    1) 装好 QQ NT + NapCat（Windows 版 NapCat.Framework）"
Say "    2) 启动 NapCat，在面板里扫码登录"
Say "    3) 把机器人拉进群，面板「白名单」填群号"
Say ""
Say "  手动启动："
Say "    .\.venv\Scripts\python.exe bot.py"
Say "    .\.venv\Scripts\python.exe panel.py"
Say ""
