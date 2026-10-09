# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""图表 HTML 模板 — SVG 饼图 + 柱状图 + 完整 HTML 页面

被 summary.py 的 chart_menu 调用。不依赖任何第三方库，纯 SVG 手绘。
"""

import os
import math

# 分类配色（顺序与默认分类一致，未命中则用兜底灰）
CATEGORY_COLORS = {
    "办公": "#42A5F5",
    "学习": "#26C6DA",
    "工具": "#FFA726",
    "浏览器": "#78909C",
    "游戏": "#EF5350",
    "视频": "#EC407A",
    "通讯": "#AB47BC",
    "系统": "#8D6E63",
    "音乐": "#FF7043",
    "未分类": "#BDBDBD",
}
CATEGORY_ORDER = ("办公", "学习", "工具", "浏览器", "游戏", "视频", "通讯", "系统", "音乐", "未分类")

FOCUS_COLOR = "#66BB6A"
NONFOCUS_COLOR = "#546E7A"


def _fmt(seconds):
    """紧凑时长文本：<60s 用 s，<1h 用 min，否则 h+min"""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}min"
    h = int(seconds // 3600)
    m = int((seconds % 3600) / 60)
    return f"{h}h{m}min"


def _pct(value, total):
    """百分比文本，保留一位小数"""
    if total == 0:
        return "0%"
    return f"{value / total * 100:.1f}%"


def _svg_pie(slices, radius=110, cx=125, cy=125):
    """生成 SVG 饼图，slices = [(label, value, color), ...]，悬浮提示"""
    total = sum(v for _, v, _ in slices)
    if total == 0:
        return ""
    paths = []
    start = -math.pi / 2  # 从 12 点方向开始
    for label, value, color in slices:
        sweep = value / total * 2 * math.pi
        if sweep <= 0:
            continue
        end = start + sweep
        x1 = cx + radius * math.cos(start)
        y1 = cy + radius * math.sin(start)
        x2 = cx + radius * math.cos(end)
        y2 = cy + radius * math.sin(end)
        large = 1 if sweep > math.pi else 0
        paths.append(
            f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} '
            f'A{radius},{radius} 0 {large},1 {x2:.1f},{y2:.1f} Z" fill="{color}" '
            f'stroke="#1a1a2e" stroke-width="2"><title>{label}  {_fmt(value)}  ({_pct(value, total)})</title></path>'
        )
        # 百分比 >= 5% 才写扇区标签，避免挤成一团
        mid = start + sweep / 2
        tx = cx + radius * 0.62 * math.cos(mid)
        ty = cy + radius * 0.62 * math.sin(mid)
        p = value / total * 100
        if p >= 5:
            paths.append(
                f'<text x="{tx:.0f}" y="{ty:.0f}" text-anchor="middle" dominant-baseline="central" '
                f'font-size="11" fill="#fff" font-weight="bold">{p:.0f}%</text>'
            )
        start = end
    return "\n".join(paths)


def _svg_bar_chart(slices, max_width=400, label_x=75, rect_x=82, text_x=530):
    """生成 SVG 横向柱状图，时长文字统一外置，悬浮提示

    返回 (svg, 总高度)
    """
    total = max(v for _, v, _ in slices) if slices else 1
    if total == 0:
        total = 1
    bars = []
    for i, (label, value, color) in enumerate(slices):
        w = value / total * max_width
        y = i * 28
        d = _fmt(value)
        p = _pct(value, sum(v for _, v, _ in slices))
        bars.append(
            f'<text x="{label_x}" y="{y + 16}" text-anchor="end" dominant-baseline="central" '
            f'font-size="12" fill="#ccc">{label}</text>'
        )
        bars.append(
            f'<rect x="{rect_x}" y="{y + 4}" width="{w:.0f}" height="20" rx="4" fill="{color}">'
            f"<title>{label}  {d}  ({p})</title></rect>"
        )
        bars.append(
            f'<text x="{text_x}" y="{y + 16}" text-anchor="end" dominant-baseline="central" '
            f'font-size="12" fill="#aaa">{d}</text>'
        )
    return "\n".join(bars), len(slices) * 28 + 10


def make_chart_html(title, focus_sec, total_sec, cat_sec, chart_dir):
    """生成单时段图表 HTML，写入 chart_dir"""
    nonfocus = total_sec - focus_sec

    focus_slices = [
        ("专注", int(focus_sec), FOCUS_COLOR),
        ("非专注", int(nonfocus), NONFOCUS_COLOR),
    ]
    focus_pie = _svg_pie(focus_slices, 110, 125, 125)

    sorted_cats = sorted(cat_sec.items(), key=lambda x: x[1], reverse=True)
    cat_slices = [
        (c, int(s), CATEGORY_COLORS.get(c, "#9E9E9E"))
        for c, s in sorted_cats
        if s > 0
    ]
    cat_pie = _svg_pie(cat_slices, 110, 125, 125)
    bar_svg, bar_h = _svg_bar_chart(cat_slices)

    focus_legend = "".join(
        f'<div class="legend-item"><div class="legend-color" style="background:{c}"></div>'
        f"{l} {_pct(v, total_sec)}</div>"
        for l, v, c in focus_slices
    )
    cat_legend = "".join(
        f'<div class="legend-item"><div class="legend-color" style="background:{c}"></div>'
        f"{l} {_pct(v, total_sec)}</div>"
        for l, v, c in cat_slices[:8]
    )

    html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n<title>时间图表 - '
        + title
        + '</title>\n<style>\n'
        '  * { margin:0; padding:0; box-sizing:border-box; }\n'
        '  body { font-family: "Microsoft YaHei", sans-serif; background:#1a1a2e; color:#eee; padding:40px 20px; }\n'
        '  h1 { text-align:center; font-size:24px; margin-bottom:30px; }\n'
        '  h2 { font-size:16px; color:#999; margin-bottom:15px; }\n'
        '  .row { display:flex; justify-content:center; gap:60px; flex-wrap:wrap; margin-bottom:30px; }\n'
        '  .chart-box { text-align:center; background:#22223a; border-radius:16px; padding:25px; min-width:300px; }\n'
        '  .legend { display:flex; flex-wrap:wrap; justify-content:center; gap:14px; margin-top:14px; }\n'
        '  .legend-item { display:flex; align-items:center; gap:6px; font-size:13px; }\n'
        '  .legend-color { width:12px; height:12px; border-radius:3px; }\n'
        '  .bar-box { background:#22223a; border-radius:16px; padding:30px; max-width:600px; margin:0 auto; }\n'
        '</style>\n</head>\n<body>\n<h1>'
        + title
        + '</h1>\n<div class="row">\n'
        '  <div class="chart-box"><h2>专注 / 非专注</h2>\n'
        '    <svg width="250" height="250" viewBox="0 0 250 250">'
        + focus_pie
        + '</svg>\n    <div class="legend">'
        + focus_legend
        + '</div>\n  </div>\n'
        '  <div class="chart-box"><h2>分类分布</h2>\n'
        '    <svg width="250" height="250" viewBox="0 0 250 250">'
        + cat_pie
        + '</svg>\n    <div class="legend">'
        + cat_legend
        + '</div>\n  </div>\n</div>\n'
        '<div class="bar-box">\n'
        '  <h2 style="text-align:center;margin-bottom:15px;">分类时长排行</h2>\n'
        f'  <svg width="580" height="{bar_h + 20}" viewBox="0 0 580 {bar_h + 20}">'
        + bar_svg
        + '</svg>\n</div>\n</body>\n</html>'
    )

    os.makedirs(chart_dir, exist_ok=True)
    safe = title.replace(" ", "_").replace(":", "").replace("/", "-")[:60]
    fpath = os.path.join(chart_dir, f"{safe}.html")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return fpath


def make_compare_html(title, f1, t1, ca1, title1, f2, t2, ca2, title2, chart_dir):
    """生成月份对比 HTML，写入 chart_dir"""

    def _box(fs, ts, cs, lb):
        nf = ts - fs
        fp = _svg_pie(
            [("专注", int(fs), FOCUS_COLOR), ("非专注", int(nf), NONFOCUS_COLOR)],
            80, 100, 100,
        )
        sc = sorted(cs.items(), key=lambda x: x[1], reverse=True)
        cl = [(c, int(s), CATEGORY_COLORS.get(c, "#9E9E9E")) for c, s in sc if s > 0]
        cp = _svg_pie(cl, 80, 100, 100)
        bs, bh = _svg_bar_chart(cl, 300, 345, text_x=345)
        return (
            '<div class="cc">\n  <h2>'
            + lb
            + "  |  专注 "
            + _pct(fs, ts)
            + "  |  总时长 "
            + _fmt(int(ts))
            + '</h2>\n  <div class="row" style="margin-bottom:10px;">\n'
            '    <div class="chart-box" style="padding:15px;"><h2>专注/非专注</h2>\n'
            '      <svg width="200" height="200" viewBox="0 0 200 200">'
            + fp
            + '</svg></div>\n'
            '    <div class="chart-box" style="padding:15px;"><h2>分类分布</h2>\n'
            '      <svg width="200" height="200" viewBox="0 0 200 200">'
            + cp
            + '</svg></div>\n  </div>\n'
            '  <div class="bar-box" style="padding:15px;">\n'
            f'    <svg width="360" height="{bh + 20}" viewBox="0 0 360 {bh + 20}">'
            + bs
            + '</svg></div>\n</div>'
        )

    body = (
        '<div class="compare">'
        + _box(f1, t1, ca1, title1)
        + _box(f2, t2, ca2, title2)
        + "</div>"
    )

    html = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n<title>月度对比 - '
        + title
        + '</title>\n<style>\n'
        '  * { margin:0; padding:0; box-sizing:border-box; }\n'
        '  body { font-family:"Microsoft YaHei",sans-serif; background:#1a1a2e; color:#eee; padding:30px; }\n'
        '  h1 { text-align:center; font-size:22px; margin-bottom:25px; }\n'
        '  h2 { font-size:14px; color:#999; margin-bottom:10px; }\n'
        '  .row { display:flex; justify-content:center; gap:20px; flex-wrap:wrap; }\n'
        '  .chart-box { text-align:center; background:#22223a; border-radius:12px; padding:15px; min-width:220px; }\n'
        '  .compare { display:flex; justify-content:center; gap:30px; flex-wrap:wrap; }\n'
        '  .cc { background:#1e1e32; border-radius:16px; padding:20px; text-align:center; }\n'
        '  .cc h2 { font-size:14px; color:#ddd; margin-bottom:12px; }\n'
        '  .bar-box { background:#22223a; border-radius:12px; padding:15px; max-width:400px; margin:10px auto; }\n'
        '</style>\n</head>\n<body>\n<h1>'
        + title
        + "</h1>\n"
        + body
        + "\n</body>\n</html>"
    )

    os.makedirs(chart_dir, exist_ok=True)
    fpath = os.path.join(chart_dir, f"{title}.html")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html)
    return fpath
