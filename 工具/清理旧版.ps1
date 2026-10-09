# FocusLog  |  Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
﻿# 清理旧版残留  v2.14.1
# 单一实现：根目录「开始记录.bat」和 面板里的「开始记录」都调用本脚本。
#
# 为什么需要：旧版（含"双文件夹版"）如果还在开机自启里，旧 exe 会和新版
# 各写一份日志；而单实例锁是"按目录"的，两边锁文件不同 ⇒ 谁也不会退出。
# 这里做三件事：
#   ① 结束不是当前版本的 focus_tracker 进程
#   ② 把 FocusTracker 启动项纠正到当前版本
#   ③ 删掉"其它名字但指向旧 focus_tracker.exe"的启动项
$ErrorActionPreference = 'SilentlyContinue'

$root    = Split-Path -Parent $PSScriptRoot
$exeFull = [IO.Path]::GetFullPath((Join-Path $root '焦点监控\focus_tracker.exe'))
$run     = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$changed = $false

# ① 旧进程
Get-Process focus_tracker -ErrorAction SilentlyContinue | ForEach-Object {
  $path = $_.Path
  if ($path) {
    $full = [IO.Path]::GetFullPath($path)
    if ($full -ne $exeFull) {
      Write-Output ("  [清理] 结束旧版进程: " + $full)
      Stop-Process -Id $_.Id -Force
      $changed = $true
    }
  }
}

# ② 启动项纠偏
$cur = (Get-ItemProperty -Path $run -Name FocusTracker -ErrorAction SilentlyContinue).FocusTracker
if ($cur -and ($cur.Trim('"') -ne $exeFull)) {
  Set-ItemProperty -Path $run -Name FocusTracker -Value $exeFull
  Write-Output "  [清理] 启动项已纠正指向当前版本"
  $changed = $true
}

# ③ 其它名字的旧启动项
$props = Get-ItemProperty -Path $run
foreach ($name in $props.PSObject.Properties.Name) {
  if ($name -like 'PS*' -or $name -eq 'FocusTracker') { continue }
  $val = [string]$props.$name
  if ($val -match 'focus_tracker\.exe' -and ($val.Trim('"') -ne $exeFull)) {
    Remove-ItemProperty -Path $run -Name $name
    Write-Output ("  [清理] 删除旧启动项 " + $name + " -> " + $val)
    $changed = $true
  }
}

if (-not $changed) { Write-Output '  [清理] 没有发现旧版残留' }

# ④ 常见位置扫描旧副本（只列出不删文件；若其进程在跑由 ① 结束）
$search = @()
foreach ($b in @("$env:USERPROFILE\Desktop", "$env:USERPROFILE\OneDrive\Desktop",
                 'C:\Users\Public\Desktop', "$env:USERPROFILE\Downloads")) {
  if (Test-Path $b) {
    $search += Get-ChildItem $b -Recurse -Depth 2 -Filter focus_tracker.exe -ErrorAction SilentlyContinue
    $search += Get-ChildItem $b -Recurse -Depth 2 -Filter focuspanel.exe -ErrorAction SilentlyContinue
  }
}
foreach ($d in @('C:\', 'D:\', 'E:\')) {
  if (Test-Path $d) {
    $search += Get-ChildItem $d -Depth 1 -Filter focus_tracker.exe -ErrorAction SilentlyContinue
    $search += Get-ChildItem $d -Depth 1 -Filter focuspanel.exe -ErrorAction SilentlyContinue
  }
}
foreach ($f in $search) {
  if ($f.FullName -like "$root*") { continue }
  Write-Output ("  [发现旧副本] " + $f.FullName + " （文件未删，确认不用可手动删除）")
}
