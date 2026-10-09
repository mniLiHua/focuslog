# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
归档共享模块  v2.7.2 新增
被 summary.py（--archive）和 focus_tracker.py（跨天自动归档）共用

把「本月之前」的三类数据按月归档：
  1. 根目录日志  YYYY-MM-DD.txt    → 归档\YYYY-MM\
  2. 深耕记录    YYYY-MM-DD_专注…  → 深耕记录\归档\YYYY-MM\
  3. 时长统计    YYYY-MM-DD*.txt   → 时长统计\归档\YYYY-MM\

补齐式：扫描所有早于本月的月份，漏跑几个月补跑一次即可全部补齐；
重复运行幂等，本月数据不动。
"""

import os
import re
import shutil
import sys
from datetime import date

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "专注记录"))
OUTPUT_DIR = os.path.join(LOG_DIR, "时长统计")


def _move_into(src, dst_dir):
    """把文件移进归档目录；目标已存在时先删除

    Windows 下 shutil.move 到已存在的文件会抛 OSError ——
    旧版会让整个归档流程崩在某个月的中途，留下半归档状态。
    """
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst):
        os.remove(dst)
    shutil.move(src, dst)


def collect_pending_months(log_dir=None, output_dir=None):
    """扫描三类数据源，返回所有「早于本月」且仍有文件留在根目录的月份"""
    log_dir = log_dir or LOG_DIR
    output_dir = output_dir or OUTPUT_DIR
    this_month = date.today().strftime("%Y-%m")
    months = set()

    def _scan(dir_path, pattern):
        if not os.path.isdir(dir_path):
            return
        for fn in os.listdir(dir_path):
            if not os.path.isfile(os.path.join(dir_path, fn)):
                continue
            m = pattern.match(fn)
            if not m:
                continue
            if m.group(1) < this_month:          # 本月数据还在用，不归档
                months.add(m.group(1))

    _scan(log_dir, re.compile(r"^(\d{4}-\d{2})-\d{2}\.txt$"))
    _scan(os.path.join(log_dir, "深耕记录"), re.compile(r"^(\d{4}-\d{2})-\d{2}_.+\.txt$"))
    _scan(output_dir, re.compile(r"^(\d{4}-\d{2})-\d{2}.*\.txt$"))
    return sorted(months)


def archive_one_month(ym, log_dir=None, output_dir=None, quiet=False):
    """归档某一个 'YYYY-MM'，返回 (日志数, 深耕数, 统计数)"""
    log_dir = log_dir or LOG_DIR
    output_dir = output_dir or OUTPUT_DIR
    arch_log = os.path.join(log_dir, "归档", ym)
    arch_sg = os.path.join(log_dir, "深耕记录", "归档", ym)
    arch_sc = os.path.join(output_dir, "归档", ym)

    n_log = n_sg = n_sc = 0

    for fn in os.listdir(log_dir):
        if re.match(rf"^{ym}-\d{{2}}\.txt$", fn):
            _move_into(os.path.join(log_dir, fn), arch_log)
            n_log += 1

    sg_dir = os.path.join(log_dir, "深耕记录")
    if os.path.isdir(sg_dir):
        for fn in os.listdir(sg_dir):
            if re.match(rf"^{ym}-\d{{2}}_.+\.txt$", fn):
                _move_into(os.path.join(sg_dir, fn), arch_sg)
                n_sg += 1

    if os.path.isdir(output_dir):
        for fn in os.listdir(output_dir):
            if re.match(rf"^{ym}-\d{{2}}.*\.txt$", fn):
                src = os.path.join(output_dir, fn)
                if os.path.isfile(src):
                    _move_into(src, arch_sc)
                    n_sc += 1

    if not quiet:
        print(f"  [{ym}]  日志 {n_log} 个  |  深耕 {n_sg} 个  |  统计 {n_sc} 个")
    return n_log, n_sg, n_sc


def archive_old_months(log_dir=None, output_dir=None, quiet=False):
    """补齐式归档所有早于本月的月份，返回成功归档的月份数

    quiet=True 时不打印（供 tracker 后台调用，只写它自己的日志）。
    """
    log_dir = log_dir or LOG_DIR
    output_dir = output_dir or OUTPUT_DIR
    months = collect_pending_months(log_dir, output_dir)

    if not quiet:
        print()
        print("=" * 60)
        print("  归档扫描")
        print("=" * 60)

    if not months:
        if not quiet:
            print(f"  没有需要归档的月份（{date.today().strftime('%Y-%m')} 及之前的记录都已归档）")
            print("=" * 60)
            print()
        return 0

    if not quiet:
        print(f"  发现 {len(months)} 个待归档月份: {', '.join(months)}")
        print("-" * 60)

    tot = [0, 0, 0]
    done = 0
    for ym in months:
        try:
            a, b, c = archive_one_month(ym, log_dir, output_dir, quiet)
        except OSError as e:
            if not quiet:
                print(f"  [X] {ym} 归档失败: {e}（已跳过，可重跑补齐）")
            continue
        tot[0] += a
        tot[1] += b
        tot[2] += c
        done += 1

    if not quiet:
        print("-" * 60)
        print(f"  归档完成: 共 {done} 个月份")
        print(f"  日志文件:  {tot[0]} 个 → 归档\\YYYY-MM\\")
        print(f"  深耕记录:  {tot[1]} 个 → 深耕记录\\归档\\YYYY-MM\\")
        print(f"  时长统计:  {tot[2]} 个 → 时长统计\\归档\\YYYY-MM\\")
        print("=" * 60)
        print()
    return done
