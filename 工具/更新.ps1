# FocusLog  |  Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# FocusLog updater - only program files are replaced, your data stays.
# Usage: double click 更新.bat  (or: powershell -ExecutionPolicy Bypass -File 工具\更新.ps1)

$ErrorActionPreference = 'Stop'
$RepoOwner = 'mniLiHua'
$RepoName  = 'focuslog'
$Asset     = 'FocusLog_latest.zip'
$Root      = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)

function Say($m) { Write-Host $m }

function CmpVer($a, $b) {
    $a = $a.TrimStart('v'); $b = $b.TrimStart('v')
    $pa = $a.Split('.'); $pb = $b.Split('.')
    for ($i = 0; $i -lt [Math]::Max($pa.Count, $pb.Count); $i++) {
        $x = 0; $y = 0
        if ($i -lt $pa.Count) { [void][int]::TryParse($pa[$i], [ref]$x) }
        if ($i -lt $pb.Count) { [void][int]::TryParse($pb[$i], [ref]$y) }
        if ($x -lt $y) { return -1 }
        if ($x -gt $y) { return 1 }
    }
    return 0
}

try {
Say '============================================================'
Say '  FocusLog 更新程序'
Say '  - 只替换程序文件（.py / .bat / .exe / docs）'
Say '  - 保留：专注记录\category.txt 与你的全部日志'
Say '============================================================'
Say ''

# 0) 版本比对（读公开的 Releases API，不需要 GitHub 账号）
$verFile = Join-Path $Root 'VERSION.txt'
$local = if (Test-Path $verFile) { (Get-Content $verFile -Raw).Trim() } else { '' }
$remote = ''
if (Get-Command gh -ErrorAction SilentlyContinue) {
    try {
        $remote = (& gh api "repos/$RepoOwner/$RepoName/releases/latest" --jq .tag_name 2>$null)
        if ($remote) { $remote = "$remote".Trim() }
    } catch { }
}
if (-not $remote) {
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$RepoOwner/$RepoName/releases/latest" `
            -Headers @{ 'User-Agent' = 'FocusLog-Updater' } -TimeoutSec 15
        $remote = $rel.tag_name
    } catch { }
}

if ($remote -and $local) {
    $c = CmpVer $local $remote
    if ($c -ge 0) {
        Say ('  你已经是最新版本 v' + $local + '，无需更新。')
        if ($c -gt 0) { Say '  （本机版本比发布页还新，可能是新版尚未发布）' }
        Say ''
        Read-Host '  按回车退出'
        exit 0
    }
    Say ('  发现新版本: v' + $local + '  ->  ' + $remote + ' ，开始更新 ...')
    Say ''
} elseif (-not $remote) {
    Say '  [提示] 暂时取不到远端版本号（网络原因），直接尝试下载最新包 ...'
    Say ''
}

$tmp = Join-Path $env:TEMP ("fl_update_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$zip = Join-Path $tmp $Asset

# 1) download（仓库公开，普通浏览器直链即可，无需登录）
$ok = $false
if (Get-Command gh -ErrorAction SilentlyContinue) {
    Say '[1/4] 正在用 gh 下载最新版 ...'
    try {
        & gh release download -R "$RepoOwner/$RepoName" -p $Asset -D $tmp 2>$null
        if (Test-Path $zip) { $ok = $true }
    } catch { }
}
if (-not $ok) {
    Say '[1/4] 正在从 GitHub 下载最新版 ...'
    $url = "https://github.com/$RepoOwner/$RepoName/releases/latest/download/$Asset"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 300
        $ok = Test-Path $zip
    } catch {
        Say ''
        Say '  [!] 下载失败（网络原因）。'
        Say '      手动更新：浏览器打开下面的发布页，下载 zip 后解压，'
        Say '      把里面的程序文件覆盖到本目录即可（数据不会丢）：'
        Say "      https://github.com/$RepoOwner/$RepoName/releases/latest"
        Say ''
        Read-Host '  按回车退出'
        exit 1
    }
}

# 2) extract
Say '[2/4] 解压 ...'
Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'src') -Force
$src = Get-ChildItem (Join-Path $tmp 'src') -Directory | Where-Object { Test-Path (Join-Path $_.FullName '专注记录') } | Select-Object -First 1
if (-not $src) { Say '  [X] 压缩包结构不符合预期'; Read-Host '  按回车退出'; exit 1 }
$src = $src.FullName

# 3) backup current program files
Say '[3/4] 备份当前程序文件 ...'
$bak = Join-Path $Root ("_backup_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $bak -Force | Out-Null
foreach ($d in @('专注记录', '焦点监控')) {
    $from = Join-Path $Root $d
    if (Test-Path $from) {
        $to = Join-Path $bak $d
        New-Item -ItemType Directory -Path $to -Force | Out-Null
        Get-ChildItem $from -File | Where-Object { $_.Extension -in '.py', '.bat', '.exe', '.ps1' } |
            ForEach-Object { Copy-Item $_.FullName $to -Force }
    }
}
# 分类方案只有 .txt（category.txt 绝不能被模板覆盖，所以 .txt 只对这个目录生效）
$pdir = Join-Path $Root '分类方案'
if (Test-Path $pdir) {
    $pto = Join-Path $bak '分类方案'
    New-Item -ItemType Directory -Path $pto -Force | Out-Null
    Get-ChildItem $pdir -File | Where-Object { $_.Extension -eq '.txt' } |
        ForEach-Object { Copy-Item $_.FullName $pto -Force }
}

# 4) overwrite program files only (a locked exe is skipped, not fatal)
Say '[4/4] 更新程序文件 ...'
$locked = @()
foreach ($d in @('专注记录', '焦点监控')) {
    $to = Join-Path $Root $d
    $from = Join-Path $src $d
    if (Test-Path $from) {
        New-Item -ItemType Directory -Path $to -Force | Out-Null
        Get-ChildItem $from -File | Where-Object { $_.Extension -in '.py', '.bat', '.exe', '.ps1' } |
            ForEach-Object {
                try { Copy-Item $_.FullName (Join-Path $to $_.Name) -Force -ErrorAction Stop }
                catch { $locked += $_.Name }
            }
    }
}
# 分类方案：只合入 .txt 方案文件（用户自建的方案文件保留，同名才覆盖）
$pt = Join-Path $Root '分类方案'
$pf = Join-Path $src '分类方案'
if (Test-Path $pf) {
    New-Item -ItemType Directory -Path $pt -Force | Out-Null
    Get-ChildItem $pf -File | Where-Object { $_.Extension -eq '.txt' } |
        ForEach-Object {
            try { Copy-Item $_.FullName (Join-Path $pt $_.Name) -Force -ErrorAction Stop }
            catch { $locked += $_.Name }
        }
}
$docsFrom = Join-Path $src 'docs'
if (Test-Path $docsFrom) { try { Copy-Item $docsFrom (Join-Path $Root 'docs') -Recurse -Force -ErrorAction Stop } catch { } }
foreach ($f in @('VERSION.txt')) {
    $p = Join-Path $src $f
    if (Test-Path $p) { try { Copy-Item $p (Join-Path $Root $f) -Force -ErrorAction Stop } catch { $locked += $f } }
}
# ensure empty data skeleton exists
foreach ($d in @('归档', '深耕记录', '时长统计')) {
    $p = Join-Path (Join-Path $Root '专注记录') $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Say ''
Say '============================================================'
Say ('  更新完成: v' + $local + '  ->  v' + $remote.TrimStart('v'))
Say '  category.txt 与所有日志都没有被改动。'
Say ('  旧程序文件已备份到: ' + $bak)
if ($locked.Count -gt 0) {
    $locked = @($locked | Where-Object { $_ })
    Say ('  [!] 以下文件正被占用，本次没有更新: ' + (($locked | Select-Object -Unique) -join ', '))
    Say '      请先双击「停止监控.bat」，再运行一次 更新.bat。'
}
Say '  提示: 重新双击「打开面板.bat」即可用上新版本。'
Say '============================================================'
Read-Host '  按回车退出'
} catch {
    Say ''
    Say ('  [X] 更新出错: ' + $_.Exception.Message)
    Say '      请把本窗口内容截图，到 https://github.com/mniLiHua/focuslog/issues 反馈'
    Read-Host '  按回车退出'
    exit 1
}
