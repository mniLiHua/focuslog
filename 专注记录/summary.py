# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
窗口时长分布统计脚本  v2.7.1
读取焦点监控的日志 + category.txt，输出各大类时长分布、窗口明细和未分类进程提示

日志文件与统计工具同目录（专注记录），category.txt 读取自上级 焦点监控 目录。
用法：python summary.py [日期]          (日期格式 YYYY-MM-DD，默认今天)
      python summary.py --export        (批量导出全部日期统计，支持已归档日志)
      python summary.py --archive       (把「本月之前」所有遗留记录全部归档，含补归档)
      python summary.py --classify      (交互式分类助手：扫描未分类进程，快速添加规则)
      python summary.py --renew         (用当前 #专注# 规则重新计算全部深耕记录)
      python summary.py --rank          (交互式应用排行：本周/本月/全部/指定月份)
      python summary.py --chart         (导出时间图表：饼图 + 柱状图 HTML)

v2.6 新增：
  · 兼容「点压缩」日志格式 —— 连续同窗口的短行 `HH:MM:SS|.` 会按采样间隔累加，
    旧格式（每行完整三段）依然能正常读取，两种格式混排也没问题

v2.7.1 新增：
  · 归档改为「补齐式」—— 扫描所有早于本月的记录，逐月归档，而不是只处理上个月。
    漏跑几个月后补跑一次即可全部补齐，无需按月手动执行。
"""

import os
import sys
import time
import re
import math
import shutil
import time as _time
from datetime import date, datetime, timedelta
from collections import defaultdict

# 终端输出编码兜底：遇到 GBK 控制台无法表示的字符不崩溃
# ⚠️ GUI exe（--noconsole）里 sys.stdout 可能是 None，必须先补一个再谈编码
import io as _io

if sys.stdout is None:
    sys.stdout = _io.StringIO()
elif hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
elif hasattr(sys.stdout, "buffer"):
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, sys.stdout.encoding or "utf-8",
                                   errors="replace")

if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

TRACKER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "焦点监控"))
LOG_DIR = SCRIPT_DIR

sys.path.insert(0, TRACKER_DIR)
from category_utils import (load_categories, parse_category_file, is_focus_category,
                            classify_window, is_focus_window)
import archive_utils

OUTPUT_DIR = os.path.join(SCRIPT_DIR, "时长统计")

# 采样间隔（秒）—— 点压缩日志里每个 "." 代表一段这么长的时间
SAMPLING_INTERVAL = 10


def get_category_file():
    """category.txt 路径（现在在 专注记录 目录下）"""
    return os.path.join(LOG_DIR, "category.txt")


def format_duration(seconds):
    """格式化时长显示"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m = int(seconds / 60)
        return f"{m}min"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) / 60)
        return f"{h}h{m}min"


_ARCHIVE_INDEX = {"t": 0.0, "map": {}}


def _archive_index():
    """归档目录的 {日期: 路径} 索引（30 秒 TTL）

    为什么：旧实现在根目录找不到时，**每个日期都 os.walk 整棵 归档/ 树**，
    55 天就是 55 次 walk（归档 6 个月×30 天时尤其明显）。这里一次 walk 建索引，
    之后 O(1)。面板是常驻进程，索引能反复复用。
    """
    now = time.time()
    if _ARCHIVE_INDEX["map"] and now - _ARCHIVE_INDEX["t"] < 30:
        return _ARCHIVE_INDEX["map"]
    m = {}
    for root, _dirs, files in os.walk(os.path.join(LOG_DIR, "归档")):
        for fn in files:
            if len(fn) == 14 and fn.endswith(".txt"):        # YYYY-MM-DD.txt
                m[fn[:-4]] = os.path.join(root, fn)
    _ARCHIVE_INDEX["t"], _ARCHIVE_INDEX["map"] = now, m
    return m


def _find_log_file(date_str):
    """查找日志文件，优先根目录，再查归档索引"""
    p = os.path.join(LOG_DIR, f"{date_str}.txt")
    if os.path.exists(p):
        return p
    return _archive_index().get(date_str)


_LOG_CACHE = {}          # 日志路径 -> (size, mtime, rows)
_LOG_CACHE_MAX = 90      # 90 天足够覆盖面板"全部"范围，再多也只是 LRU 淘汰


def parse_log(date_str):
    """解析日志（带文件级缓存）

    v2.14.1 性能：面板一次刷新会从 collect / windows / rank / categories
    四条链路读同一批日志，旧实现每条链路都重新 open+解析一遍
    （实测点开「工具」要 8.4 秒，因为全历史被 parse 了 3 遍）。
    这里以 (文件大小, mtime) 做 key 缓存解析结果：日志被追加或归档改名后
    key 自然变化，不会读到脏数据。
    """
    log_file = _find_log_file(date_str)
    if not log_file:
        return []
    try:
        st = os.stat(log_file)
    except OSError:
        return []
    key = (st.st_size, st.st_mtime)
    hit = _LOG_CACHE.get(log_file)
    if hit is not None and hit[0] == key:
        return hit[1]
    rows = _parse_log_raw(log_file)
    if len(_LOG_CACHE) >= _LOG_CACHE_MAX:
        _LOG_CACHE.pop(next(iter(_LOG_CACHE)))
    _LOG_CACHE[log_file] = (key, rows)
    return rows


def _parse_log_raw(log_file):
    """解析当天的日志文件，返回 [(time_str, proc, title, duration_seconds), ...]
    duration_seconds 为本条记录到下一条记录的时间差（秒），最后一条按标准采样间隔计

    v2.6：支持「点压缩」格式
      · 完整行   HH:MM:SS|proc.exe|标题
      · 省略行   HH:MM:SS|.        ← 表示「还是上一个窗口」，按采样间隔累加
      两种格式混排也能正确解析，旧日志无需改动。

    v2.7：识别 [LOCK] / [UNLOCK] 标记
      · [LOCK]|锁屏暂停    —— 断开上下文，锁屏期间不计时
      · [UNLOCK]|解锁恢复  —— 同样断开上下文，解锁后重新按完整行起算
      两者与 [RESUME] 等价处理，旧日志无需改动。
    """

    records = []
    last_proc = None
    last_title = None

    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "|" not in line:
                continue
            parts = line.split("|", 2)

            # ---- 点压缩省略行：HH:MM:SS|. ----
            if len(parts) == 2 and parts[1].strip() == ".":
                if last_proc is None:
                    continue  # 文件开头就是省略行（异常），跳过
                try:
                    t = datetime.strptime(parts[0], "%H:%M:%S")
                except ValueError:
                    continue
                records.append((parts[0], last_proc, last_title, t))
                continue

            # ---- 完整行：必须三段 ----
            if len(parts) < 3:
                continue
            if parts[1].startswith("[") and parts[1].endswith("]"):
                # [RESUME] / [LOCK] / [UNLOCK] 等标记行：断开上下文，不计入时长
                last_proc = None
                last_title = None
                continue
            try:
                t = datetime.strptime(parts[0], "%H:%M:%S")
            except ValueError:
                continue

            last_proc = parts[1]
            last_title = parts[2]
            records.append((parts[0], parts[1], parts[2], t))

    # 计算每条记录的持续时长（到下一条的时间差）
    result = []
    for i in range(len(records)):
        if i + 1 < len(records):
            diff = (records[i + 1][3] - records[i][3]).total_seconds()
            # v2.7 修正：间隔超过 60 秒 = 中间发生过暂停/锁屏/空闲，
            # 这一段不该按采样间隔补时，直接记 0（真实中断）。
            # ⚠️ 旧代码在此处写成 diff = 0 也会把「正常的长间隔」判为 0，
            #    但那本来就是中断，行为等价；这里只补注释说明。
            if diff < 0 or diff > 60:
                diff = 0
        else:
            # 只有最后一条才按标准采样间隔补时
            diff = SAMPLING_INTERVAL
        result.append((records[i][0], records[i][1], records[i][2], diff))

    return result


def _bar_chars():
    """根据终端编码选择柱状图字符，GBK 下用 ASCII 字符避免编码错误"""
    try:
        enc = (sys.stdout.encoding or "").lower()
        if enc in ("gbk", "gb2312", "gb18030", "cp936"):
            return "#", "."
    except AttributeError:
        pass
    return "█", "░"


_BAR_FULL, _BAR_EMPTY = _bar_chars()


def _visual_width(s):
    """计算字符串在终端中的实际显示宽度（中文2列，英文1列）"""
    w = 0
    for ch in s:
        if ord(ch) > 0x2E80:
            w += 2
        else:
            w += 1
    return w


def _pad_visual(s, target_width):
    """在字符串右侧补空格，使终端显示宽度达到 target_width"""
    pad = target_width - _visual_width(s)
    return s + " " * max(pad, 0)


def draw_bar(label, value, max_val, bar_width=30):
    """绘制文本柱状图（自动适配终端编码）"""
    if max_val == 0:
        filled = 0
    else:
        filled = int(value / max_val * bar_width)
    filled = min(filled, bar_width)
    bar = _BAR_FULL * filled + _BAR_EMPTY * (bar_width - filled)
    return f"  {_pad_visual(label, 8)} {bar}"


def run_summary(date_str, categories, to_file=False):
    """统计单日数据，返回 (文本内容, 前3大类标签) 或 None"""
    records = parse_log(date_str)
    if not records:
        return None

    # 专注判断必须只用带 #专注# 的分类，否则会把普通分类也算成专注
    focus_cats = load_categories(get_category_file(), only_focus=True)

    lines = []
    cat_seconds = defaultdict(float)
    unclass_seconds = defaultdict(float)
    window_seconds = defaultdict(float)
    total_seconds = 0
    focus_seconds = 0
    total_samples = len(records)

    window_detail = {}

    for time_str, proc, title, dur in records:
        cat = classify_window(proc, title, categories)
        if cat:
            cat_seconds[cat] += dur
        else:
            unclass_seconds[proc] += dur

        if is_focus_window(proc, title, focus_cats):
            focus_seconds += dur

        detail_key = f"{proc} | {title}"
        window_seconds[detail_key] += dur
        if detail_key not in window_detail:
            window_detail[detail_key] = cat or "未分类"
        total_seconds += dur

    lines.append("")
    lines.append("=" * 60)
    lines.append(f"  窗口时长分布统计  |  {date_str}")
    lines.append(f"  总采样: {total_samples}次  |  总时长: {format_duration(total_seconds)}"
                 f"  |  专注: {format_duration(focus_seconds)}")
    lines.append("=" * 60)
    lines.append("")

    sorted_cats = sorted(cat_seconds.items(), key=lambda x: x[1], reverse=True)
    max_sec = sorted_cats[0][1] if sorted_cats else 1

    lines.append("  [分类时长]")
    for cat_name, sec in sorted_cats:
        pct = sec / total_seconds * 100 if total_seconds > 0 else 0
        bar = draw_bar(cat_name, sec, max_sec)
        lines.append(f"{bar:<42s} {format_duration(sec):>8s} ({pct:>5.1f}%)")

    unclass_total = sum(unclass_seconds.values())
    if unclass_total > 0:
        unclass_pct = unclass_total / total_seconds * 100
        bar = draw_bar("未分类", unclass_total, max_sec)
        lines.append(f"{bar:<42s} {format_duration(unclass_total):>8s} ({unclass_pct:>5.1f}%)")

    lines.append("")
    lines.append(f"  合计: {format_duration(total_seconds)}")
    lines.append("")

    lines.append("=" * 60)
    lines.append("  [窗口使用时长 TOP10]")
    lines.append("=" * 60)
    sorted_windows = sorted(window_seconds.items(), key=lambda x: x[1], reverse=True)
    for i, (detail, sec) in enumerate(sorted_windows[:10], 1):
        pct = sec / total_seconds * 100 if total_seconds > 0 else 0
        cat_label = window_detail.get(detail, "")
        if cat_label:
            cat_label = f"[{cat_label}] "
        lines.append(f"  {i:>2}. {cat_label}{detail:<50s} {format_duration(sec):>8s} ({pct:>5.1f}%)")

    lines.append("")

    if unclass_seconds:
        lines.append("=" * 60)
        lines.append("  [未分类进程明细]  建议添加到 category.txt")
        lines.append("=" * 60)
        sorted_unc = sorted(unclass_seconds.items(), key=lambda x: x[1], reverse=True)
        for proc, sec in sorted_unc:
            pct = sec / total_seconds * 100
            lines.append(f"  {proc:<30s} {format_duration(sec):>8s} ({pct:.1f}%)")

        lines.append("")
        lines.append("  提示: 在 category.txt 中添加对应的分类规则即可")
        lines.append("  格式: 分类名|proc=进程名  或  分类名|title=标题关键词")
        lines.append("")

    text = "\n".join(lines)

    # 前 3 大类标签（用于统计文件名）
    top = [f"{c}_{format_duration(s)}" for c, s in sorted_cats[:3]]
    return text, top


_DATES_CACHE = {"t": 0.0, "dates": None}


def _find_all_log_dates():
    """扫描根目录 + 归档子目录，返回所有 YYYY-MM-DD 日期列表（去重排序）

    v2.14.1 性能：这个函数要 os.walk 整个 LOG_DIR（含整棵归档树），而
    _rank_stats 一天里会调它 4 次、面板一次刷新调它 3 遍以上 —— 全部是重复
    walk。加 30 秒 TTL：日志文件名只有"新建/归档"才会变，30 秒足够安全。
    """
    now = time.time()
    if _DATES_CACHE["dates"] is not None and now - _DATES_CACHE["t"] < 30:
        return _DATES_CACHE["dates"]
    pat = re.compile(r"^\d{4}-\d{2}-\d{2}\.txt$")
    dates = set()
    for root, _dirs, files in os.walk(LOG_DIR):
        for fn in files:
            if pat.match(fn):
                dates.add(fn[:-4])
    result = sorted(dates)
    _DATES_CACHE.update(t=now, dates=result)
    return result


def _get_output_path(date_str):
    """根据日志文件的位置决定统计输出路径：日志在归档夹，输出也进对应归档夹"""
    log_file = _find_log_file(date_str)
    if log_file and os.path.join(LOG_DIR, "归档") in log_file:
        # 定位到 归档\YYYY-MM 这一层
        rel = os.path.relpath(os.path.dirname(log_file), os.path.join(LOG_DIR, "归档"))
        out_dir = os.path.join(OUTPUT_DIR, "归档", rel)
    else:
        out_dir = OUTPUT_DIR
    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, f"{date_str}.txt")


def export_all():
    """导出所有日期的统计（日志在哪个目录，输出就写进哪个目录）"""
    categories = load_categories(get_category_file())
    if not categories:
        print("[警告] 未找到分类配置文件: category.txt (在焦点监控目录)")
        return

    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    log_files = _find_all_log_dates()
    if not log_files:
        print("[提示] 没有找到任何日志文件")
        return

    print(f"\n  找到 {len(log_files)} 个日志文件，开始导出...\n")

    success = 0
    for date_str in log_files:
        result = run_summary(date_str, categories, to_file=True)
        out_path = _get_output_path(date_str)
        if result:
            text, top = result
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"  [OK] {date_str}")
            success += 1
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"\n  {date_str} 无有效采样数据\n")
            print(f"  [--] {date_str} (无数据)")

    print(f"\n  导出完成: {success} 个文件已保存\n")


def _is_archived_date(date_str):
    """该日期的日志是否已经躺在 归档/ 目录下"""
    p = _find_log_file(date_str)
    return bool(p) and os.path.join(LOG_DIR, "归档") in p


def _week_dates():
    """「本周」= 本周一起至今天

    v2.7.2：排行与图表共用这一个定义 —— 旧版排行用「周一至今」、
    图表用「今天-7天」，同一套菜单里两个「本周」数字对不上。
    """
    today = date.today()
    start = today - timedelta(days=today.weekday())
    return start, today


def renew_focus_records(include_archived=False):
    """用当前 #专注# 规则重新计算深耕记录

    清空深耕记录根目录旧文件 → 扫描日志 → 按新规则计算专注/在线 → 写入

    v2.7.2：默认只处理「未归档」的日期。旧版会把已归档月份也重建到根目录，
    下次 --archive 再 move 回同名归档文件 ⇒ 撞车。需要全量重算用 --renew-all。
    """
    categories = load_categories(get_category_file(), only_focus=True)
    if not categories:
        print("[警告] 未找到任何 #专注# 分类，专注时长为0")

    print(f"\n  当前专注分类: {', '.join(categories.keys()) if categories else '(无)'}")

    sg_dir = os.path.join(LOG_DIR, "深耕记录")
    os.makedirs(sg_dir, exist_ok=True)
    # 清空根目录旧文件（保留 归档 子目录）
    for fn in os.listdir(sg_dir):
        p = os.path.join(sg_dir, fn)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass

    dates = [d for d in _find_all_log_dates()
             if include_archived or not _is_archived_date(d)]
    scope = "全部历史" if include_archived else "未归档月份"
    print(f"\n  [{scope}] 共 {len(dates)} 个日志文件，开始重新计算深耕记录...\n")

    ok = 0
    for date_str in dates:
        records = parse_log(date_str)
        if not records:
            print(f"  [--] {date_str}  专注 0  在线 0")
            continue

        focus_sec = 0
        total_sec = 0
        focus_windows = defaultdict(float)
        for _ts, proc, title, dur in records:
            total_sec += dur
            if is_focus_window(proc, title, categories):
                focus_sec += dur
                focus_windows[f"{proc} | {title}"] += dur

        fh = int(focus_sec // 3600)
        fm = int((focus_sec % 3600) // 60)
        focus_txt = (f"{fh}小时{fm}分钟" if fh else f"{fm}分钟") if focus_sec >= 60 else f"{int(focus_sec)}秒"
        th = int(total_sec // 3600)
        tm = int((total_sec % 3600) // 60)
        total_txt = (f"{th}小时{tm}分钟" if th else f"{tm}分钟") if total_sec >= 60 else f"{int(total_sec)}秒"

        lines = [f"日期: {date_str}", f"专注: {focus_txt}", f"在线: {total_txt}", ""]
        detail = sorted(
            [(k, v) for k, v in focus_windows.items() if v >= 300],
            key=lambda x: x[1], reverse=True
        )
        if detail:
            lines.append("=== 专注窗口明细 (>= 5min) ===")
            for k, v in detail:
                lines.append(f"  {_pad_visual(k[:40], 40)} {format_duration(v):>8s}")

        fname = f"{date_str}_专注{format_duration(focus_sec)}_在线{format_duration(total_sec)}.txt"
        with open(os.path.join(sg_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"  [OK] {date_str}  专注 {format_duration(focus_sec)}  在线 {format_duration(total_sec)}")
        ok += 1

    print(f"\n  深耕记录更新完成: {ok} 个文件已保存到 深耕记录\\\n")


def _rank_stats(period):
    """统计指定时间段的进程排行

    period: "week" / "month" / "all" / "YYYY-MM"
    返回 (title, [(proc, seconds, days), ...])
    """
    today = date.today()
    if period == "week":
        start, _end = _week_dates()
        dates = [d for d in _find_all_log_dates() if start.strftime("%Y-%m-%d") <= d <= _end.strftime("%Y-%m-%d")]
        title = f"本周 ({start.strftime('%Y-%m-%d')} ~ {_end.strftime('%Y-%m-%d')})"
    elif period == "month":
        ym = today.strftime("%Y-%m")
        dates = [d for d in _find_all_log_dates() if d.startswith(ym)]
        title = f"本月 ({ym})"
    elif period == "all":
        dates = _find_all_log_dates()
        title = "全部历史"
    elif re.match(r"^\d{4}-\d{2}$", period):
        dates = [d for d in _find_all_log_dates() if d.startswith(period)]
        title = f"{period} 月"
    else:
        return None, []

    proc_sec = defaultdict(float)
    proc_days = defaultdict(set)
    for date_str in dates:
        for _ts, proc, _title, dur in parse_log(date_str):
            proc_sec[proc] += dur
            if dur > 0:
                proc_days[proc].add(date_str)

    rows = [(p, s, len(proc_days[p])) for p, s in proc_sec.items()]
    rows.sort(key=lambda x: x[1], reverse=True)
    return title, rows


def _truncate_visual(s, max_vw):
    """按视觉宽度截断字符串，超出用...替换（保证总视觉宽度 <= max_vw）"""
    if _visual_width(s) <= max_vw:
        return s
    out = ""
    for ch in s:
        if _visual_width(out + ch) > max_vw - 3:
            break
        out += ch
    return out + "..."


def _display_rank(period):
    """计算并显示排行"""
    title, rows = _rank_stats(period)
    if not rows:
        print("\n  [提示] 该时段无数据")
        input("  [按 Enter 返回]")
        return

    total = sum(r[1] for r in rows)
    shown = [r for r in rows if r[1] >= 900]
    hidden = len(rows) - len(shown)

    print()
    print("=" * 26 + f" {title} " + "=" * 26)
    print(f"  {'No.':<4s}{'进程名':<28s}{'总时长':>10s}{'占比':>8s}{'天数':>6s}")
    print("  " + "─" * 4 + "─" * 28 + "─" * 10 + "─" * 8 + "─" * 6)
    for i, (proc, sec, days) in enumerate(shown[:30], 1):
        pct = sec / total * 100 if total else 0
        print(f"  {i:<4d}{_truncate_visual(proc, 26):<28s}{format_duration(sec):>10s}{pct:>7.1f}%{days:>5d}天")
    print("  " + "─" * 4 + "─" * 28 + "─" * 10 + "─" * 8 + "─" * 6)
    print(f"  {'':<4s}{'总计':<28s}{format_duration(total):>10s}{'100%':>8s}")
    if hidden > 0:
        print(f"\n  (其他 {hidden} 个进程不足 15min，已隐藏)")
    input("  [按 Enter 返回]")


def rank_menu():
    """交互式应用排行"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("=" * 60)
        print("         应用使用排行")
        print("=" * 60)
        print("  1  本周排行")
        print("  2  本月排行")
        print("  3  全部历史排行")
        print("  4  指定月份（输入 YYYY-MM）")
        print("  0  返回")
        print("=" * 60)
        c = input("  >> 请输入编号 (0-4): ").strip()
        if c == "0":
            return
        elif c == "1":
            _display_rank("week")
        elif c == "2":
            _display_rank("month")
        elif c == "3":
            _display_rank("all")
        elif c == "4":
            ym = input("  >> 请输入月份 (YYYY-MM) > ").strip()
            if re.match(r"^\d{4}-\d{2}$", ym):
                _display_rank(ym)
            else:
                print("  [X] 无效输入")
                input("  [按 Enter 继续]")


def _scan_unclassified_procs(min_seconds=900):
    """扫描全部日志，返回累计 >= min_seconds 的未分类进程列表

    跳过已存在 proc= 规则的全部进程（只显示完全无 proc 规则的进程）。

    返回 [{proc, total_seconds, day_count, example_title}, ...] 按时长降序
    """
    categories = load_categories(get_category_file())
    known_procs = set()
    for _cat, rules in categories.items():
        for rtype, kw in rules:
            if rtype == "proc":
                known_procs.add(kw.lower())

    stat = {}
    for date_str in _find_all_log_dates():
        for _ts, proc, title, dur in parse_log(date_str):
            if proc.lower() in known_procs:
                continue
            if classify_window(proc, title, categories) is not None:
                continue
            e = stat.setdefault(proc, {"proc": proc, "total_seconds": 0,
                                       "days": set(), "example_title": title})
            e["total_seconds"] += dur
            if dur > 0:
                e["days"].add(date_str)
            if not e["example_title"] and title:
                e["example_title"] = title

    rows = [
        {"proc": e["proc"], "total_seconds": e["total_seconds"],
         "day_count": len(e["days"]), "example_title": e["example_title"]}
        for e in stat.values() if e["total_seconds"] >= min_seconds
    ]
    rows.sort(key=lambda x: x["total_seconds"], reverse=True)
    return rows


def _pick_category_menu(categories):
    """显示分类选择子菜单，返回选中分类名，None=返回上级，__quit__=退出"""
    # v2.7.2：专注标记必须从 #专注# 前缀读，不能用「有没有 proc 规则」冒充
    _meta, _dup = parse_category_file(get_category_file())
    names = list(categories.keys())
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print("=" * 50)
        print(f"  为 [{names[0] if names else ''}] 指定分类")
        print("=" * 50)
        for i, n in enumerate(names, 1):
            focus = " (专注)" if is_focus_category(n, _meta) else ""
            print(f"  {i:<2d}   {_pad_visual(n, 10)}      {focus}")
        print(f"  {len(names)+1:<2d}   (新建分类)")
        print("  [b] 返回上级    [q] 退出")
        print("=" * 50)
        c = input("  >> 输入编号 / b / q > ").strip().lower()
        if c == "q":
            return "__quit__"
        if c == "b":
            return None
        if c.isdigit():
            i = int(c)
            if 1 <= i <= len(names):
                return names[i - 1]
            if i == len(names) + 1:
                new = input("  >> 输入新分类名称 > ").strip()
                if not new:
                    print("  分类名不能为空")
                    input("  [按 Enter 继续]")
                    continue
                if new in categories:
                    print(f"  分类 [{new}] 已存在，将使用已有分类")
                    input("  [按 Enter 继续]")
                return new
        print("  [X] 无效编号")
        input("  [按 Enter 继续]")


def _append_category_rule(cat_name, proc_name, is_new=False, is_focus=False):
    """将一条分类规则写入 category.txt

    如果该分类已有行，将 proc= 合并到行末（不覆盖原有规则）；
    如果该分类不存在且 is_new=True，按规范化格式插入完整块；
    否则仅追加裸行。
    """
    path = get_category_file()
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            content = f.read()
    except OSError:
        content = ""

    lines = content.replace("\r\n", "\n").split("\n")
    prefix = "#专注#" if is_focus else ""
    rule = f"proc={proc_name}"

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") and not s.startswith("#专注#"):
            continue
        body = s[len("#专注#"):] if s.startswith("#专注#") else s
        if "|" not in body:
            continue
        name = body.split("|", 1)[0].strip()
        if name == cat_name:
            if rule in line:
                return False          # 规则已存在，不重复添加
            lines[i] = line.rstrip() + "," + rule
            break
    else:
        if is_new:
            tag = " (专注)" if is_focus else " (非专注)"
            lines.append("")
            lines.append(f"# ----- {cat_name}{tag} -----")
        lines.append(f"{prefix}{cat_name}|{rule}")

    # v2.7.2：先备份 + 原子替换（写 tmp 再 os.replace），避免写一半把配置损坏
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))
    os.replace(tmp_path, path)
    return True


# ============================================================
#  图表导出（v2.6 补齐）
# ============================================================

from chart_templates import make_chart_html, make_compare_html

CHART_OUT_DIR = os.path.join(OUTPUT_DIR, "图表")


def _collect_chart_data(period):
    """收集时段数据：focus_sec, total_sec, cat_seconds dict

    period 取值：
      'today'         今天
      'week'          最近 7 天
      'month'         本月（当月 1 号至今）
      'all'           全部历史
      'YYYY-MM'       指定月份
      'YYYY-MM-DD-to-YYYY-MM-DD'  指定范围
    """
    cat = load_categories(get_category_file())
    focus_cats = load_categories(get_category_file(), only_focus=True)
    cat_sec = defaultdict(float)
    focus_sec = 0
    total_sec = 0

    if period == "today":
        d = date.today().strftime("%Y-%m-%d")
        records = parse_log(d)
        for _, proc, title, dur in records:
            total_sec += dur
            c = classify_window(proc, title, cat)
            cat_sec[c or "未分类"] += dur
            if is_focus_window(proc, title, focus_cats):
                focus_sec += dur
    elif period == "week":
        start, end = _week_dates()
        for d in _find_all_log_dates():
            if start <= datetime.strptime(d, "%Y-%m-%d").date() <= end:
                records = parse_log(d)
                for _, proc, title, dur in records:
                    _accum_chart(dur, proc, title, cat, focus_cats, cat_sec)
                    total_sec += dur
                    if is_focus_window(proc, title, focus_cats):
                        focus_sec += dur
    elif period == "month":
        start = date.today().replace(day=1)
        for d in _find_all_log_dates():
            if start <= datetime.strptime(d, "%Y-%m-%d").date() <= date.today():
                records = parse_log(d)
                for _, proc, title, dur in records:
                    _accum_chart(dur, proc, title, cat, focus_cats, cat_sec)
                    total_sec += dur
                    if is_focus_window(proc, title, focus_cats):
                        focus_sec += dur
    elif period == "all":
        for d in _find_all_log_dates():
            records = parse_log(d)
            for _, proc, title, dur in records:
                _accum_chart(dur, proc, title, cat, focus_cats, cat_sec)
                total_sec += dur
                if is_focus_window(proc, title, focus_cats):
                    focus_sec += dur
    elif re.match(r"^\d{4}-\d{2}$", period):
        for d in _find_all_log_dates():
            if not d.startswith(period):
                continue
            records = parse_log(d)
            for _, proc, title, dur in records:
                _accum_chart(dur, proc, title, cat, focus_cats, cat_sec)
                total_sec += dur
                if is_focus_window(proc, title, focus_cats):
                    focus_sec += dur
    elif "-to-" in period:
        parts = period.split("-to-")
        s_d = datetime.strptime(parts[0], "%Y-%m-%d").date()
        e_d = datetime.strptime(parts[1], "%Y-%m-%d").date()
        for d in _find_all_log_dates():
            dd = datetime.strptime(d, "%Y-%m-%d").date()
            if s_d <= dd <= e_d:
                records = parse_log(d)
                for _, proc, title, dur in records:
                    _accum_chart(dur, proc, title, cat, focus_cats, cat_sec)
                    total_sec += dur
                    if is_focus_window(proc, title, focus_cats):
                        focus_sec += dur

    return focus_sec, total_sec, dict(cat_sec)


def _accum_chart(dur, proc, title, cat, focus_cats, cat_sec):
    """把一条采样累加进分类桶（分类名兜底为「未分类」）"""
    c = classify_window(proc, title, cat)
    cat_sec[c or "未分类"] += dur


def _make_weekday_html(month=None):
    """按星期聚合 HTML"""
    cat = load_categories(get_category_file())
    focus_cats = load_categories(get_category_file(), only_focus=True)
    days = {"Mon": [], "Tue": [], "Wed": [], "Thu": [], "Fri": [], "Sat": [], "Sun": []}
    day_names = {
        "Mon": "周一", "Tue": "周二", "Wed": "周三", "Thu": "周四",
        "Fri": "周五", "Sat": "周六", "Sun": "周日",
    }

    for d in _find_all_log_dates():
        if month and not d.startswith(month):
            continue
        weekday = datetime.strptime(d, "%Y-%m-%d").strftime("%a")
        records = parse_log(d)
        if not records:
            continue
        fs, ts = 0, 0
        cs = defaultdict(float)
        for _, proc, title, dur in records:
            ts += dur
            c = classify_window(proc, title, cat)
            cs[c or "未分类"] += dur
            if is_focus_window(proc, title, focus_cats):
                fs += dur
        days[weekday].append((fs, ts, dict(cs)))

    cells = ""
    for wd in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        lst = days[wd]
        if not lst:
            cells += f'<div class="week-cell"><h3>{day_names[wd]}</h3><div class="small">无数据</div></div>'
            continue
        n = len(lst)
        avg_focus = sum(f for f, _, _ in lst) / n
        avg_total = sum(t for _, t, _ in lst) / n
        pct = avg_focus / avg_total * 100 if avg_total > 0 else 0
        avg_cats = defaultdict(float)
        for _, _, cs in lst:
            for c, s in cs.items():
                avg_cats[c] += s / n
        top3 = sorted(avg_cats.items(), key=lambda x: x[1], reverse=True)[:3]
        bars = "".join(
            f'<div style="display:flex;justify-content:space-between;font-size:11px;color:#aaa;margin:2px 0;">'
            f"<span>{c}</span><span>{format_duration(int(s))}</span></div>"
            for c, s in top3
        )
        cells += (
            f'<div class="week-cell">\n<h3>{day_names[wd]}</h3>\n'
            f'<div style="font-size:18px;font-weight:bold;color:#4CAF50;">{pct:.0f}%</div>\n'
            f'<div class="small">专注比例</div>\n'
            f'<div class="week-bar"><div class="week-fill" style="width:{pct:.0f}%;background:#66BB6A;"></div></div>\n'
            f'<div style="margin-top:8px;">{bars}</div>\n'
            f'<div class="small" style="margin-top:4px;">日均在线 {format_duration(int(avg_total))} | {n}天</div>\n</div>'
        )

    ttl = f"{month} 按星期聚合" if month else "全部历史按星期聚合"
    html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head><meta charset="utf-8"><title>'
        + ttl
        + '</title>\n<style>\n'
        '  * { margin:0; padding:0; box-sizing:border-box; }\n'
        '  body { font-family:"Microsoft YaHei",sans-serif; background:#1a1a2e; color:#eee; padding:40px 20px; }\n'
        '  h1 { text-align:center; font-size:22px; margin-bottom:25px; }\n'
        '  .week-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:12px; max-width:900px; margin:0 auto; }\n'
        '  .week-cell { background:#22223a; padding:16px; border-radius:12px; text-align:center; }\n'
        '  .week-cell h3 { font-size:15px; margin-bottom:8px; }\n'
        '  .week-bar { height:8px; background:#333; border-radius:4px; overflow:hidden; margin:8px 0; }\n'
        '  .week-fill { height:100%; border-radius:4px; }\n'
        '  .small { font-size:11px; color:#888; }\n'
        '</style>\n</head>\n<body>\n<h1>'
        + ttl
        + '</h1>\n<div class="week-grid">'
        + cells
        + '</div>\n</body>\n</html>'
    )

    out_dir = CHART_OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    fpath = os.path.join(out_dir, f"{ttl}.html")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return fpath


def _chart_pick_month():
    """选择月份（复用现有月份列表逻辑）"""
    date_set = set()
    for d in _find_all_log_dates():
        date_set.add(d[:7])
    months = sorted(date_set, reverse=True)

    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print()
        print("=" * 60)
        print("         选择月份                                              ")
        print("=" * 60)
        print()
        for i, ym in enumerate(months, 1):
            print(f"  {i:<4d} {ym}")
        print()
        print("  [0]  返回")
        print()
        choice = input("  >> 请输入编号: ").strip().strip("\ufeff\u200b\u200c\u200d\u2060\ufffe")
        if choice == "0":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(months):
                return months[idx]
        except ValueError:
            pass
        print("  [X] 无效编号")
        _time.sleep(1)


def _chart_range_input():
    """输入日期范围"""
    print()
    s = input("  >> 起始日期 (YYYY-MM-DD) > ").strip()
    e = input("  >> 结束日期 (YYYY-MM-DD) > ").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s) and re.match(r"^\d{4}-\d{2}-\d{2}$", e):
        return f"{s}-to-{e}"
    return None


def chart_menu():
    """交互式图表菜单"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print()
        print("=" * 60)
        print("         图表导出                                             ")
        print("=" * 60)
        print()
        print("  1  今天的图表")
        print("  2  本周图表")
        print("  3  本月图表")
        print("  4  本月 vs 上月对比")
        print("  5  按星期聚合（本月）")
        print("  6  指定月份")
        print("  7  指定日期范围")
        print("  8  全部历史图表")
        print()
        print("  0  返回")
        print()
        choice = input("  >> 请输入编号 (0-8): ").strip().strip("\ufeff\u200b\u200c\u200d\u2060\ufffe")

        if choice == "0":
            return
        fpath = None
        if choice == "1":
            fs, ts, cs = _collect_chart_data("today")
            fpath = make_chart_html("今日时间分布", fs, ts, cs, CHART_OUT_DIR)
        elif choice == "2":
            fs, ts, cs = _collect_chart_data("week")
            fpath = make_chart_html("本周时间分布", fs, ts, cs, CHART_OUT_DIR)
        elif choice == "3":
            fs, ts, cs = _collect_chart_data("month")
            ym = date.today().strftime("%Y-%m")
            fpath = make_chart_html(f"{ym} 本月时间分布", fs, ts, cs, CHART_OUT_DIR)
        elif choice == "4":
            ym = date.today().strftime("%Y-%m")
            f1, t1, ca1 = _collect_chart_data(ym)
            y, m = int(ym[:4]), int(ym[5:7])
            if m == 1:
                last_ym = f"{y - 1}-12"
            else:
                last_ym = f"{y}-{m - 1:02d}"
            f2, t2, ca2 = _collect_chart_data(last_ym)
            fpath = make_compare_html(
                f"{ym} vs {last_ym}",
                f1, t1, ca1, ym,
                f2, t2, ca2, last_ym,
                CHART_OUT_DIR,
            )
        elif choice == "5":
            ym = date.today().strftime("%Y-%m")
            fpath = _make_weekday_html(ym)
        elif choice == "6":
            ym = _chart_pick_month()
            if ym:
                fs, ts, cs = _collect_chart_data(ym)
                fpath = make_chart_html(f"{ym} 时间分布", fs, ts, cs, CHART_OUT_DIR)
        elif choice == "7":
            rng = _chart_range_input()
            if rng:
                fs, ts, cs = _collect_chart_data(rng)
                label = rng.replace("-to-", " ~ ")
                fpath = make_chart_html(f"{label} 时间分布", fs, ts, cs, CHART_OUT_DIR)
            else:
                print("  [X] 日期格式错误")
                _time.sleep(1)
        elif choice == "8":
            fs, ts, cs = _collect_chart_data("all")
            fpath = make_chart_html("全部历史时间分布", fs, ts, cs, CHART_OUT_DIR)
        else:
            print("  [X] 无效输入")
            _time.sleep(1)
            continue

        if fpath:
            print(f"\n  [OK] 图表已生成: {fpath}")
        input("  [按 Enter 返回]")


def classify_menu():
    """交互式分类助手：扫描全部日志 → 列出 ≥15min 未分类进程 → 交互菜单分类"""
    categories = load_categories(get_category_file())
    if not categories:
        print("[警告] 未找到分类配置文件: category.txt")
        input("[按 Enter 返回]")
        return

    print("\n  === 正在扫描所有日志，请稍候... ===")
    rows = _scan_unclassified_procs(900)
    if not rows:
        print("\n  没有找到累计 >= 15min 的未分类进程，分类助手退出。")
        input("  [按 Enter 返回]")
        return

    while rows:
        os.system("cls" if os.name == "nt" else "clear")
        print("=" * 60)
        print("        分类助手  |  未分类进程 (>= 15min)")
        print("=" * 60)
        print(f"  {'No.':<4s} {'Process':<28s} {'Duration':>8s} {'Days':>4s}  Sample Title")
        print("  " + "─" * 4 + " " + "─" * 28 + " " + "─" * 8 + " " + "─" * 4 + "  " + "─" * 22)
        for i, r in enumerate(rows, 1):
            t = _truncate_visual(r["example_title"] or "(无标题)", 20)
            print(f"  {i:<4d} {_truncate_visual(r['proc'], 26):<28s} "
                  f"{format_duration(r['total_seconds']):>8s} {r['day_count']:>4d}  {t}")
        print("=" * 60)
        print("  [a] 全部标记为 [系统]")
        print("  [q] 退出")
        print("=" * 60)
        c = input("  >> 请输入编号 / a / q > ").strip().lower()

        if c == "q":
            print("\n  已退出分类助手。")
            return
        if c == "a":
            print("\n  将添加以下规则（全部标记为 [系统] ）:")
            for r in rows:
                print(f"    系统|proc={r['proc']}")
            if input("  确认添加? [Y/n] > ").strip().lower() in ("", "y"):
                for r in rows:
                    _append_category_rule("系统", r["proc"])
                print(f"\n  [OK] 已添加 {len(rows)} 条规则，全部处理完毕。")
                input("  [按 Enter 返回]")
                return
            continue

        if not c.isdigit() or not (1 <= int(c) <= len(rows)):
            print("  [X] 无效编号，请重新输入")
            input("  [按 Enter 继续]")
            continue

        r = rows[int(c) - 1]
        categories = load_categories(get_category_file())
        ch = _pick_category_menu(categories)
        if ch == "__quit__":
            print("\n  已退出分类助手。")
            return
        if ch is None:
            continue

        is_new = ch not in categories
        is_focus = False
        if is_new:
            if input(f"  [{ch}] 是新分类，是否标记为专注? [y/N] > ").strip().lower() == "y":
                is_focus = True
        # 生成规则（保持行序：新的专注分类插到靠前位置由用户自行调整）
        _append_category_rule(ch, r["proc"], is_new=is_new, is_focus=is_focus)
        print(f"  [OK] 已添加: {('#专注#' if is_focus else '')}{ch}|proc={r['proc']}")
        rows = [x for x in rows if x["proc"] != r["proc"]]

    print("\n  所有未分类进程已处理完毕！")
    input("  [按 Enter 返回]")


# v2.7.2：归档逻辑收敛到共享模块 archive_utils（tracker 跨天自动归档复用同一份），
# 这里只做兼容别名，避免两侧各写一份实现而漂移。
_collect_pending_months = archive_utils.collect_pending_months
_archive_one_month = archive_utils.archive_one_month
archive_old_months = archive_utils.archive_old_months


def main():
    categories = load_categories(get_category_file())
    _meta, dup = parse_category_file(get_category_file())
    if dup:
        print(f"[警告] category.txt 里有重复分类名: {', '.join(dup)}")
        print("       已自动合并规则（专注标记取或），建议手工去重以免口径混乱")
    if not categories:
        print("[警告] 未找到分类配置文件: category.txt")
        print("       请检查 专注记录 目录下是否有 category.txt")
        return

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--export":
            export_all()
            return
        if arg == "--archive":
            archive_old_months()
            return
        if arg == "--classify":
            classify_menu()
            return
        if arg == "--renew":
            renew_focus_records()
            return
        if arg == "--renew-all":
            renew_focus_records(include_archived=True)
            return
        if arg == "--maintain":
            # 子进程维护入口：归档 → 重算深耕 → 导出统计（+周一自动周报）
            # 由记录进程跨天时派发，输出全部落 maintenance.log 供排查
            archive_old_months()
            renew_focus_records()
            export_all()
            import datetime as _dt
            if _dt.date.today().weekday() == 0:
                try:
                    import importlib.util as _ilu
                    wp = os.path.normpath(os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        "..", "工具", "weekly_report.py"))
                    spec = _ilu.spec_from_file_location("fl_weekly", wp)
                    m = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(m)
                except Exception as e:                       # noqa: BLE001
                    print(f"[周报失败] {e}")
            return
        if arg == "--rank":
            rank_menu()
            return
        if arg == "--chart":
            chart_menu()
            return
        date_str = arg
    else:
        date_str = date.today().strftime("%Y-%m-%d")

    result = run_summary(date_str, categories)
    if result is None:
        print(f"[提示] 未找到 {date_str} 的日志文件")
        return
    print(result[0])


if __name__ == "__main__":
    main()
