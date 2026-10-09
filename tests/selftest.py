# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
兼容性自检  v2.9.1
回答一个问题：**新版本还能不能吃老记录？**

    python tests\selftest.py

覆盖的老格式：
  A. v2.5 老日志：每行完整三段、没有点压缩（归档里 2026-05 就是这种）
  B. v2.6 点压缩：HH:MM:SS|.
  C. v2.7 锁屏标记：[LOCK] / [UNLOCK]
  D. v2.7.2 暂停标记：[PAUSE] / [RESUME]（带暂停时长文案）
  E. 混排 + 空文件 + 跨天缺失
  F. 旧版 category.txt（无「游戏/音乐」分类、规则写在旧顺序）

跑法不碰真实数据：全部在临时目录里构造样本。
"""

import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FL = os.path.join(ROOT, "专注记录")
TR = os.path.join(ROOT, "焦点监控")
sys.path.insert(0, FL)
sys.path.insert(0, TR)

import summary as S                                   # noqa: E402
from category_utils import load_categories, parse_category_file, is_focus_window  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(f"  [OK] {name}  = {got}")
    else:
        FAIL.append(f"  [X]  {name}  得到 {got}，应为 {want}")


def write(day, text):
    p = os.path.join(S.LOG_DIR, day + ".txt")
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def total_of(day):
    return int(sum(r[3] for r in S.parse_log(day)))


def main():
    tmp = tempfile.mkdtemp(prefix="fl_selftest_")
    S.LOG_DIR = tmp          # parse_log 只认模块级 LOG_DIR，直接指过去
    try:
        # ---- A. v2.5 老日志：完整行、无点压缩 ----
        write("2020-01-01", "\n".join([
            "00:00:00|Code.exe|a.md - Code",
            "00:00:10|Code.exe|a.md - Code",
            "00:00:20|Code.exe|b.md - Code",
            "00:00:30|chrome.exe|GitHub",
        ]) + "\n")
        # 4 条：前 3 条各 10s 间隔，最后一条补一个采样间隔
        check("A 老格式总时长", total_of("2020-01-01"), 40)

        # ---- B. v2.6 点压缩 ----
        write("2020-01-02", "\n".join([
            "00:00:00|Code.exe|a.md - Code",
            "00:00:10|.",
            "00:00:20|.",
            "00:00:30|chrome.exe|GitHub",
        ]) + "\n")
        check("B 点压缩总时长", total_of("2020-01-02"), 40)
        procs = [r[1] for r in S.parse_log("2020-01-02")]
        check("B 点压缩还原进程", procs,
              ["Code.exe", "Code.exe", "Code.exe", "chrome.exe"])

        # ---- C. v2.7 锁屏：中断段必须不计 ----
        write("2020-01-03", "\n".join([
            "00:00:00|Code.exe|a.md - Code",
            "00:00:10|Code.exe|a.md - Code",
            "00:00:20|[LOCK]|锁屏暂停",
            "00:03:30|Code.exe|a.md - Code",
            "00:03:40|Code.exe|a.md - Code",
        ]) + "\n")
        # 10（行1）+ 0（跨锁屏 210s 判中断）+ 10（行3）+ 10（末条补时） = 30
        check("C 锁屏段不计入", total_of("2020-01-03"), 30)

        # ---- D. v2.7.2 暂停标记（带文案）----
        write("2020-01-04", "\n".join([
            "00:00:00|Code.exe|a.md - Code",
            "00:00:10|Code.exe|a.md - Code",
            "00:00:20|[PAUSE]|无键鼠输入 5 分钟",
            "00:20:00|[RESUME]|自动恢复（暂停 19分钟）",
            "00:20:10|Code.exe|a.md - Code",
        ]) + "\n")
        # 行1(10) + 行2 跨暂停判中断(0) + 末条补时(10) = 20
        # 关键：中间那 19 分钟不能被算进在线（错误实现会得到 1200+）
        check("D 暂停段不计入", total_of("2020-01-04"), 20)
        check("D 暂停时长没被算进在线", total_of("2020-01-04") < 60, True)

        # ---- E. 边界：空文件 / 只有标记 ----
        write("2020-01-05", "")
        check("E 空文件", S.parse_log("2020-01-05"), [])
        write("2020-01-06", "00:00:00|[LOCK]|锁屏暂停\n00:00:10|[UNLOCK]|解锁恢复\n")
        check("E 只有标记", S.parse_log("2020-01-06"), [])

        # ---- F. 旧版 category.txt（无游戏/音乐、专注写在旧分类上）----
        cat_old = os.path.join(tmp, "category_old.txt")
        with io.open(cat_old, "w", encoding="utf-8") as f:
            f.write("\n".join([
                "# 旧版配置（v2.6 之前）：没有游戏/音乐分类，工具也不是专注",
                "#专注#办公|proc=Code.exe,proc=WINWORD.EXE",
                "视频|proc=mplayerc.exe,title=bilibili",
                "浏览器|proc=chrome.exe",
                "系统|proc=explorer.exe",
                "工具|proc=Snipaste.exe",
            ]) + "\n")
        cats = load_categories(cat_old)
        check("F 旧配置分类数", len(cats), 5)
        meta, dup = parse_category_file(cat_old)
        check("F 旧配置专注项", sorted(k for k, v in meta.items() if v["focus"]), ["办公"])
        focus_cats = load_categories(cat_old, only_focus=True)
        check("F 旧配置下 Code.exe 算专注", is_focus_window("Code.exe", "x", focus_cats), True)
        check("F 旧配置下 chrome 不算专注", is_focus_window("chrome.exe", "x", focus_cats), False)
        check("F 无重复分类告警", dup, [])

        # ---- G. 真实归档日志抽样（2026-05，最老的一批）----
        real = os.path.join(FL, "归档")
        if os.path.isdir(real):
            olds = sorted(f for _, _, fs in os.walk(real) for f in fs if f.endswith(".txt"))
            if olds:
                S.LOG_DIR = FL          # 临时指回真实目录读一天
                d = olds[0][:-4]
                recs = S.parse_log(d)
                check("G 真实老日志可解析", len(recs) > 0, True)
                S.LOG_DIR = tmp

        print("=" * 62)
        print("  兼容性自检")
        print("=" * 62)
        for line in PASS:
            print(line)
        for line in FAIL:
            print(line)
        print("-" * 62)
        print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
        print("=" * 62)
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
