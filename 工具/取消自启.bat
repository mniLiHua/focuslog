rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "FocusTracker" /f >nul 2>nul
echo   已取消开机自启。
timeout /t 2 >nul
