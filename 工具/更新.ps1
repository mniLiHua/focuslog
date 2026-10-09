# FocusLog  |  Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# FocusLog updater - only program files are replaced, your data stays.
# Usage: double click 更新.bat  (or: powershell -ExecutionPolicy Bypass -File 工具\更新.ps1)

$ErrorActionPreference = 'Stop'
$RepoOwner = 'mniLiHua'
$RepoName  = 'focuslog'
$Asset     = 'FocusLog_latest.zip'
$Root      = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)

function Say($m) { Write-Host $m }

try {
Say '============================================================'
Say '  FocusLog 更新程序'
Say '  - 只替换程序文件（.py / .bat / .exe / docs）'
Say '  - 保留：专注记录\category.txt 与你的全部日志'
Say '============================================================'
Say ''

$tmp = Join-Path $env:TEMP ("fl_update_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$zip = Join-Path $tmp $Asset

# 1) download
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
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 120
        $ok = Test-Path $zip
    } catch {
        Say ''
        Say '  [!] 下载失败（网络原因，或仓库暂时无法访问）。'
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
        Get-ChildItem $from -File | Where-Object { $_.Extension -in '.py', '.bat', '.exe' } |
            ForEach-Object { Copy-Item $_.FullName $to -Force }
    }
}

# 4) overwrite program files only (a locked exe is skipped, not fatal)
Say '[4/4] 更新程序文件 ...'
$locked = @()
foreach ($d in @('专注记录', '焦点监控')) {
    $to = Join-Path $Root $d
    $from = Join-Path $src $d
    if (Test-Path $from) {
        New-Item -ItemType Directory -Path $to -Force | Out-Null
        Get-ChildItem $from -File | Where-Object { $_.Extension -in '.py', '.bat', '.exe' } |
            ForEach-Object {
                try { Copy-Item $_.FullName (Join-Path $to $_.Name) -Force -ErrorAction Stop }
                catch { $locked += $_.Name }
            }
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
Say '  完成。category.txt 与所有日志都没有被改动。'
Say ('  旧程序文件已备份到: ' + $bak)
if ($locked.Count -gt 0) {
    Say ('  [!] 以下文件正被占用，本次没有更新: ' + ($locked -join ', '))
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
