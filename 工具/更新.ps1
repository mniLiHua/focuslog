# FocusLog  |  Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
﻿# FocusLog updater - only program files are replaced, your data stays.
# Usage: double click 更新.bat  (or: powershell -ExecutionPolicy Bypass -File 工具\更新.ps1)

$ErrorActionPreference = 'Stop'
$RepoOwner = 'mniLiHua'
$RepoName  = 'focuslog'
$Asset     = 'FocusLog_latest.zip'
$Root      = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)

function Say($m) { Write-Host $m }

Say '============================================================'
Say '  FocusLog updater'
Say '  - replaces program files only (.py / .bat / .exe / docs)'
Say '  - keeps: 专注记录\category.txt and ALL your logs'
Say '============================================================'
Say ''

$tmp = Join-Path $env:TEMP ("fl_update_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$zip = Join-Path $tmp $Asset

# 1) download
$ok = $false
if (Get-Command gh -ErrorAction SilentlyContinue) {
    Say '[1/4] Trying gh release download ...'
    try {
        & gh release download -R "$RepoOwner/$RepoName" -p $Asset -O $zip 2>$null
        if (Test-Path $zip) { $ok = $true }
    } catch { }
}
if (-not $ok) {
    Say '[1/4] Downloading from GitHub releases (latest) ...'
    $url = "https://github.com/$RepoOwner/$RepoName/releases/latest/download/$Asset"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing -TimeoutSec 60
        $ok = Test-Path $zip
    } catch {
        Say ''
        Say '  [!] Download failed.'
        Say '      This repo is PRIVATE - a plain download needs auth.'
        Say '      Options:'
        Say '        a) install gh and run:  gh auth login'
        Say '        b) open the release page and save the zip manually:'
        Say "           https://github.com/$RepoOwner/$RepoName/releases/latest"
        Say '        c) ask the author for the zip and drop it next to this file'
        Say ''
        Read-Host '  press Enter to exit'
        exit 1
    }
}

# 2) extract
Say '[2/4] Extracting ...'
Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'src') -Force
$src = Get-ChildItem (Join-Path $tmp 'src') -Directory | Where-Object { Test-Path (Join-Path $_.FullName '专注记录') } | Select-Object -First 1
if (-not $src) { Say '  [X] package layout unexpected'; exit 1 }
$src = $src.FullName

# 3) backup current program files
Say '[3/4] Backing up current program files ...'
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

# 4) overwrite program files only
Say '[4/4] Updating ...'
foreach ($d in @('专注记录', '焦点监控')) {
    $to = Join-Path $Root $d
    $from = Join-Path $src $d
    if (Test-Path $from) {
        New-Item -ItemType Directory -Path $to -Force | Out-Null
        Get-ChildItem $from -File | Where-Object { $_.Extension -in '.py', '.bat', '.exe' } |
            ForEach-Object { Copy-Item $_.FullName (Join-Path $to $_.Name) -Force }
    }
}
$docsFrom = Join-Path $src 'docs'
if (Test-Path $docsFrom) { Copy-Item $docsFrom (Join-Path $Root 'docs') -Recurse -Force }
foreach ($f in @('VERSION.txt')) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p (Join-Path $Root $f) -Force }
}
# ensure empty data skeleton exists
foreach ($d in @('归档', '深耕记录', '时长统计')) {
    $p = Join-Path (Join-Path $Root '专注记录') $d
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
}

Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Say ''
Say '============================================================'
Say '  Done. Your category.txt and logs were NOT touched.'
Say '  Old program files backed up to: ' + $bak
Say '  Tip: if 监控 is running, stop it (停止监控.bat) before updating.'
Say '============================================================'
Read-Host '  press Enter to exit'
