# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
周报生成器  v2.12
    python 工具\weekly_report.py            生成"上周"报告
    python 工具\weekly_report.py --this     生成"本周至今"报告

产出 `docs\周报\YYYY-Www.md`（Markdown），内容：
  · 七天各自的专注 / 在线
  · 合计与「与上一周对比」
  · 分类构成 TOP、应用 TOP、每天最专注的一天
周一跨天时守护程序会自动跑一次。

口径与面板完全一致：复用 summary.parse_log 与 category_utils。
"""

import io
import os
import sys
from datetime import date, timedelta

sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FL = os.path.join(ROOT, "专注记录")
TR = os.path.join(ROOT, "焦点监控")
OUT_DIR = os.path.join(ROOT, "docs", "周报")

sys.path.insert(0, FL)
sys.path.insert(0, TR)

import summary as S                                  # noqa: E402
from category_utils import load_categories, classify_window, is_focus_window  # noqa: E402

CAT_FILE = os.path.join(FL, "category.txt")


def hm(sec):
    sec = int(sec)
    h, m = sec // 3600, sec % 3600 // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def week_range(this_week=False):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    if this_week:
        return monday, today
    return monday - timedelta(days=7), monday - timedelta(days=1)


def collect_range(d0, d1):
    cats = load_categories(CAT_FILE)
    focus_cats = load_categories(CAT_FILE, only_focus=True)
    per_day, cat_sec, app_sec = [], {}, {}
    for i in range((d1 - d0).days + 1):
        dd = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        records = S.parse_log(dd)
        f = t = 0.0
        for _ts, proc, title, dur in records:
            if dur <= 0:
                continue
            t += dur
            c = classify_window(proc, title, cats) or "未分类"
            cat_sec[c] = cat_sec.get(c, 0) + dur
            app_sec[proc] = app_sec.get(proc, 0) + dur
            if is_focus_window(proc, title, focus_cats):
                f += dur
        per_day.append((dd, int(f), int(t)))
    return per_day, cat_sec, app_sec


def build_report(this_week=False, quiet=False):
    d0, d1 = week_range(this_week)
    per_day, cat_sec, app_sec = collect_range(d0, d1)

    p0, p1 = d0 - timedelta(days=7), d1 - timedelta(days=7)
    prev_days, _pc, _pa = collect_range(p0, p1)
    cur_f = sum(x[1] for x in per_day)
    cur_t = sum(x[2] for x in per_day)
    prev_f = sum(x[1] for x in prev_days)
    prev_t = sum(x[2] for x in prev_days)

    def delta(cur, prev):
        if not prev:
            return "—"
        p = round((cur - prev) / prev * 100)
        return f"{'+' if p >= 0 else ''}{p}%"

    best = max(per_day, key=lambda x: x[1]) if per_day else ("", 0, 0)
    title = ("本周至今" if this_week else "上周") + f"（{d0} ~ {d1}）"
    iso = d1.isocalendar()
    name = f"{iso[0]}-W{iso[1]:02d}" + ("_本周至今" if this_week else "")

    lines = [f"# 时间周报 · {title}", "",
             f"- 专注合计：**{hm(cur_f)}**（较上一周 {delta(cur_f, prev_f)}）",
             f"- 在线合计：**{hm(cur_t)}**（较上一周 {delta(cur_t, prev_t)}）",
             f"- 平均专注占比：**{round(cur_f / cur_t * 100) if cur_t else 0}%**",
             f"- 最专注的一天：**{best[0]}（{hm(best[1])}）**" if best[0] else "",
             "", "## 每天", "", "| 日期 | 专注 | 在线 | 占比 |", "|---|---|---|---|"]
    for dd, f, t in per_day:
        wd = "周" + "一二三四五六日"[(date.fromisoformat(dd)).weekday()]
        lines.append(f"| {dd} {wd} | {hm(f)} | {hm(t)} | {round(f / t * 100) if t else 0}% |")

    lines += ["", "## 分类构成", ""]
    for k, v in sorted(cat_sec.items(), key=lambda x: -x[1])[:8]:
        lines.append(f"- {k}：{hm(v)}")
    lines += ["", "## 应用 TOP10", ""]
    for k, v in sorted(app_sec.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"- {k}：{hm(v)}")
    lines.append("")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.md")
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    if not quiet:
        print(f"[OK] 周报已生成: {path}")
        print(f"     {title}，专注 {hm(cur_f)} / 在线 {hm(cur_t)}")
    return path


if __name__ == "__main__":
    build_report(this_week=("--this" in sys.argv))
