rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
cd /d "%~dp0"

echo   正在检查更新（只更新程序，不动你的数据与分类规则）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0工具\更新.ps1"
