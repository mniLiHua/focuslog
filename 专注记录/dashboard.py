# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
时间面板生成器  v2.9
把日志算成一份「单文件 HTML 看板」：零依赖、离线可用、双击即看。

用法：python dashboard.py              生成 面板.html（静态快照）
      python dashboard.py --open       生成后用默认浏览器打开
      python panel_server.py           启动本地服务（面板里可直接用工具，见 panel_server.py）

设计取舍（为什么不用 Streamlit / NiceGUI）：
  · 数据在本地、要"双击就看"，引入框架就要装依赖 + 常驻服务
  · 所以沿用 chart_templates.py 的路线：手写 SVG + 原生 JS，单文件
  · ⚠️ file:// 下 fetch('data.json') 会被 CORS 拦 ⇒ 数据必须内联进 HTML

口径单一来源：
  · 日志解析复用 summary.parse_log（点压缩 / [LOCK] / [UNLOCK] 全兼容）
  · 分类与专注标记复用 category_utils
  · 面板里的工具按钮复用 summary 的既有实现（归档 / 重算 / 导出 / 分类助手）
"""

import io
import json
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

# ⚠️ exe（PyInstaller onefile）下 __file__ 指向 %TEMP%\_MEIxxx 解包目录，
#    直接用它会把面板和数据目录都算错 —— 必须用 exe 自身所在目录。
if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "焦点监控"))
sys.path.insert(0, TRACKER_DIR)
sys.path.insert(0, SCRIPT_DIR)

from category_utils import load_categories, parse_category_file, classify_window, is_focus_window  # noqa: E402
import summary as S  # noqa: E402  复用 parse_log / _find_all_log_dates

LOG_DIR = SCRIPT_DIR
OUT_FILE = os.path.join(SCRIPT_DIR, "面板.html")
CAT_FILE = os.path.join(LOG_DIR, "category.txt")
RECENT_DAYS = 60          # 趋势图回看天数（再久也看不清，体积也涨）
CAT_COLORS = 10           # 前端调色板长度（与 CSS --c0..--c9 对应）


def short_key(s, n=48):
    """窗口 key 形如 `proc | 标题`；标题可能极长（几百字），内联进 HTML 前先截断"""
    return s if len(s) <= n else s[:n - 1] + "…"


def fmt_hm(sec):
    """小时+分钟（面板显示用）"""
    sec = int(sec)
    if sec < 60:
        return f"{sec}秒"
    if sec < 3600:
        return f"{int(sec / 60)}分钟"
    h, m = int(sec // 3600), int((sec % 3600) / 60)
    return f"{h}小时{m}分钟" if m else f"{h}小时"


def collect(days_limit=RECENT_DAYS):
    """收集面板数据

    · days   —— 逐日明细（趋势「按天」用 + 选中日的窗口 TOP）
    · weeks / months —— 按自然周 / 月聚合（趋势「按周 / 按月」用）
    · ranges —— 今日 / 近 7 天 / 近 30 天 / 全部，四档汇总（含窗口 TOP）
    · hourly —— 每小时在线/专注（热力图用）

    每个单元都带同一套字段：f 专注 / t 在线 / c 分类 / a 应用 / win 窗口，
    前端只切换显示，不再二次计算 —— 口径不会分叉。
    """
    categories = load_categories(CAT_FILE)
    _meta, dup = parse_category_file(CAT_FILE)
    focus_cats = load_categories(CAT_FILE, only_focus=True)

    all_dates = S._find_all_log_dates()
    recent_set = set(all_dates[-days_limit:]) if days_limit else set(all_dates)

    today_s = date.today().strftime("%Y-%m-%d")
    since7 = (date.today() - timedelta(days=6)).strftime("%Y-%m-%d")
    since30 = (date.today() - timedelta(days=29)).strftime("%Y-%m-%d")

    def rows(dct, limit=None, shrink=False):
        out = [[(short_key(k) if shrink else k), int(v)]
               for k, v in sorted(dct.items(), key=lambda x: -x[1])]
        return out[:limit] if limit else out

    def bucket(store, key, day):
        if key not in store:
            store[key] = {"k": key, "d0": day, "d1": day, "f": 0.0, "t": 0.0,
                          "c": {}, "a": {}, "w": {}}
        return store[key]

    def add(b, focus, total, cats, apps, wins):
        b["f"] += focus
        b["t"] += total
        for k, v in cats.items():
            b["c"][k] = b["c"].get(k, 0) + v
        for k, v in apps.items():
            b["a"][k] = b["a"].get(k, 0) + v
        for k, v in wins.items():
            b["w"][k] = b["w"].get(k, 0) + v

    def pack(b):
        return {"f": int(b["f"]), "t": int(b["t"]),
                "cats": rows(b["c"]), "apps": rows(b["a"], 12),
                "win": rows(b["w"], 15, shrink=True)}

    def pack_bucket(b):
        """周/月桶的输出：**只带派生字段**
        ⚠️ 直接用 dict(b, win=...) 会把桶内未截断、未限量的原始窗口字典一起序列化，
        单一文件会从 130 KB 涨到 520 KB（踩过）。"""
        return {"k": b["k"], "d0": b["d0"], "d1": b["d1"],
                "f": int(b["f"]), "t": int(b["t"]),
                "cats": rows(b["c"]), "apps": rows(b["a"], 12),
                "win": rows(b["w"], 15, shrink=True)}

    days_out = []
    weeks, months = {}, {}
    total_b = {"f": 0.0, "t": 0.0, "c": {}, "a": {}, "w": {}}
    range_wins = {"today": {}, "d7": {}, "d30": {}}
    hourly = [[0.0, 0.0] for _ in range(24)]      # [在线, 专注]
    proc_cat_sec = {}

    for d in all_dates:
        records = S.parse_log(d)
        if not records:
            continue
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        iso = dt.isocalendar()
        wk, mo = f"{iso[0]}-W{iso[1]:02d}", d[:7]
        in_today, in7, in30 = d == today_s, d >= since7, d >= since30

        total = focus = 0.0
        cats, apps, wins = {}, {}, {}
        for ts, proc, title, dur in records:
            if dur <= 0:
                continue
            total += dur
            c = classify_window(proc, title, categories) or "未分类"
            cats[c] = cats.get(c, 0) + dur
            apps[proc] = apps.get(proc, 0) + dur
            is_focus = is_focus_window(proc, title, focus_cats)
            if is_focus:
                focus += dur
            wins[f"{proc} | {title}"] = wins.get(f"{proc} | {title}", 0) + dur
            proc_cat_sec.setdefault(proc, {})
            proc_cat_sec[proc][c] = proc_cat_sec[proc].get(c, 0) + dur
            try:
                hh = int(ts[:2])
                hourly[hh][0] += dur
                if is_focus:
                    hourly[hh][1] += dur
            except (ValueError, IndexError):
                pass

        add(total_b, focus, total, cats, apps, wins)
        for store, key in ((weeks, wk), (months, mo)):
            b = bucket(store, key, d)
            b["d1"] = d
            add(b, focus, total, cats, apps, wins)
        for name, ok in (("today", in_today), ("d7", in7), ("d30", in30)):
            if ok:
                for k, v in wins.items():
                    range_wins[name][k] = range_wins[name].get(k, 0) + v

        if d in recent_set:
            top = sorted(wins.items(), key=lambda x: x[1], reverse=True)[:6]
            days_out.append({
                "d": d, "f": int(focus), "t": int(total),
                "c": rows(cats), "a": rows(apps, 14),
                "w": [[short_key(k), int(v)] for k, v in top],
            })

    def agg(rs):
        f = t_ = 0
        cats, apps = {}, {}
        for r in rs:
            f += r["f"]
            t_ += r["t"]
            for k, v in r["c"]:
                cats[k] = cats.get(k, 0) + v
            for k, v in r["a"]:
                apps[k] = apps.get(k, 0) + v
        return {"f": f, "t": t_, "cats": rows(cats), "apps": rows(apps, 12)}

    ranges = {
        "today": dict(agg([r for r in days_out if r["d"] == today_s]),
                      win=rows(range_wins["today"], 20, shrink=True)),
        "d7": dict(agg([r for r in days_out if r["d"] >= since7]),
                   win=rows(range_wins["d7"], 20, shrink=True)),
        "d30": dict(agg([r for r in days_out if r["d"] >= since30]),
                    win=rows(range_wins["d30"], 20, shrink=True)),
        "all": pack(total_b),
    }

    cat_order = list(categories.keys())
    if "未分类" not in cat_order and total_b["c"].get("未分类"):
        cat_order.append("未分类")

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": today_s,
        "days": days_out,
        "weeks": [pack_bucket(weeks[k]) for k in sorted(weeks)],
        "months": [pack_bucket(months[k]) for k in sorted(months)],
        "ranges": ranges,
        "hourly": [[int(a), int(b)] for a, b in hourly],
        "catOrder": cat_order,
        "focusCats": sorted(set(focus_cats.keys()) & set(cat_order)),
        "procCat": {p: max(v.items(), key=lambda x: x[1])[0] for p, v in proc_cat_sec.items()},
        "dupWarn": dup,
        "todayRow": next((r for r in days_out if r["d"] == today_s), None),
        "range": [days_out[0]["d"], days_out[-1]["d"]] if days_out else ["", ""],
    }


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>时间面板 · FocusLog</title>
<style>
  :root[data-theme="dark"]{
    --bg:#1a1a2e; --card:#22223a; --card2:#2b2b45; --text:#eceef5; --muted:#9aa0b5;
    --accent:#66BB6A; --accent2:#4CAF50; --grid:#3a3a58; --line:#3a3a58;
    --shadow:0 6px 20px rgba(0,0,0,.35); --btn:#2f2f4d; --ok:#66BB6A; --bad:#EF5350;
    --c0:#66BB6A;--c1:#42A5F5;--c2:#FFA726;--c3:#AB47BC;--c4:#EF5350;
    --c5:#26C6DA;--c6:#FFCA28;--c7:#EC407A;--c8:#8D6E63;--c9:#90A4AE;
  }
  :root[data-theme="light"]{
    --bg:#f4f6fb; --card:#ffffff; --card2:#eef1f8; --text:#23263a; --muted:#6b7280;
    --accent:#2e9e52; --accent2:#43a047; --grid:#e8ecf5; --line:#dfe3ee;
    --shadow:0 6px 18px rgba(31,38,74,.08); --btn:#ffffff; --ok:#2e7d32; --bad:#c62828;
    --c0:#2E7D32;--c1:#1565C0;--c2:#E65100;--c3:#6A1B9A;--c4:#C62828;
    --c5:#00838F;--c6:#F9A825;--c7:#AD1457;--c8:#5D4037;--c9:#546E7A;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--text);
       font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;
       padding:26px 22px 56px;transition:background .25s,color .25s}
  .wrap{max-width:1140px;margin:0 auto}
  header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;
         margin-bottom:18px;flex-wrap:wrap}
  h1{font-size:20px;font-weight:600;letter-spacing:.4px}
  .sub{font-size:12px;color:var(--muted);margin-top:5px}
  .hbtns{display:flex;gap:8px;flex-wrap:wrap}
  .btn{background:var(--card);color:var(--text);border:1px solid var(--line);
       border-radius:10px;padding:8px 14px;font-size:13px;cursor:pointer;transition:.18s}
  .btn:hover{border-color:var(--accent)}
  .btn:disabled{opacity:.45;cursor:not-allowed}
  .btn.primary{background:var(--accent2);border-color:var(--accent2);color:#fff}
  .seg{display:inline-flex;background:var(--card);border-radius:12px;padding:3px;gap:2px;
       box-shadow:var(--shadow);margin-bottom:16px;flex-wrap:wrap}
  .seg button{background:transparent;border:0;color:var(--muted);padding:7px 14px;
              border-radius:9px;font-size:12.5px;cursor:pointer;transition:.18s}
  .seg button:hover{color:var(--text)}
  .seg button.on{background:var(--accent2);color:#fff}
  .seg .sep{width:14px;display:inline-block}
  .seg.sm{box-shadow:none;background:var(--card2);margin:0;padding:2px}
  .seg.sm button{padding:4px 10px;font-size:11.5px;border-radius:7px}
  .seg.sm button.on{background:var(--accent2);color:#fff}
  .hflex{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .hsub{font-size:11.5px;font-weight:400;color:var(--muted)}
  .hflex .seg{margin-left:auto}
  .delta{font-size:11.5px;color:var(--muted);margin-top:6px;min-height:16px}
  .delta b{font-weight:600}
  .delta .up{color:var(--ok)}
  .delta .down{color:var(--bad)}
  .legend{max-height:230px;overflow-y:auto}
  .pager{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;color:var(--muted)}
  .pager button{background:var(--card2);border:0;color:var(--text);border-radius:6px;
                width:22px;height:22px;cursor:pointer;font-size:12px;line-height:1}
  .pager button:disabled{opacity:.35;cursor:not-allowed}
  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}
  .card{background:var(--card);border-radius:16px;padding:16px 20px;box-shadow:var(--shadow)}
  .card .k{font-size:12px;color:var(--muted);letter-spacing:.4px}
  .card .v{font-size:25px;font-weight:600;margin-top:7px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:16px}
  .panel{background:var(--card);border-radius:16px;padding:18px 20px;box-shadow:var(--shadow)}
  .panel h2{font-size:14px;font-weight:600;margin-bottom:14px}
  .legend{display:flex;flex-direction:column;gap:8px;margin-top:10px}
  .lg{display:flex;align-items:center;gap:9px;font-size:12.5px}
  .dot{display:inline-block;width:10px;height:10px;border-radius:3px;flex:0 0 10px}
  .lg .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .lg .vl{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
  .bar-row{display:flex;align-items:center;gap:10px;font-size:12.5px;margin-bottom:9px;
           min-height:34px}          /* 固定行高：翻到最后页不足 10 条时卡片不再变矮 */
  .bar-row.tall{min-height:48px}    /* 带"主要窗口"副标题的行 */
  .bar-pad{visibility:hidden}       /* 末页补位，只为撑住高度 */
  #topWin{min-height:340px;width:100%}

  /* ---- 锁死宽度：翻页/切范围时布局不许抖 ---- */
  /* 坑：grid 的 1fr 实际是 minmax(auto,1fr)，内容一长就把列顶宽 → 卡片宽度跳。 */
  .grid2{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .cards{grid-template-columns:repeat(3,minmax(0,1fr))}
  .panel,.card{min-width:0}
  .bar-row .nm{min-width:0}
  /* 时长列必须定宽：否则"1小时"和"35小时1分钟"宽度不同，会把进度条挤得一跳一跳 */
  .bar-row .vl{width:96px;min-width:96px}
  .bar-track{flex:1 1 0;min-width:0}
  #topMin{width:118px}              /* 选项文字长短不一，定宽免得切换时抖 */
  .bar-row .nm{width:38%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .bar-track{flex:1;display:block;height:9px;background:var(--grid);border-radius:5px;
             overflow:hidden;min-width:40px}
  .bar-fill{display:block;height:100%;border-radius:5px;background:var(--accent);
            transition:width .35s ease}
  .bar-row .vl{min-width:92px;text-align:right;color:var(--muted);
               font-variant-numeric:tabular-nums;white-space:nowrap}
  svg{display:block}
  .bars{cursor:pointer}
  .tip{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.75}
  .warn{background:var(--card2);border-left:3px solid var(--c2);padding:10px 14px;
        border-radius:8px;font-size:12.5px;margin-bottom:14px}
  .工具{background:var(--card);border-radius:16px;padding:16px 20px;box-shadow:var(--shadow);
         margin-bottom:16px}
  .工具 .row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .out{font-size:12px;color:var(--muted);white-space:pre-wrap;line-height:1.8;
       font-family:Consolas,Menlo,monospace}
  table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:10px}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:500}
  select,input[type=text]{background:var(--card2);color:var(--text);border:1px solid var(--line);
    border-radius:8px;padding:5px 8px;font-size:12px;font-family:inherit}
  input[type=checkbox]{accent-color:var(--accent2);vertical-align:middle}
  .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--card2);
        font-size:11.5px;color:var(--muted)}
  .tabs{display:flex;gap:6px;margin-bottom:12px}
  .tab{background:var(--card2);color:var(--muted);border:1px solid transparent;border-radius:10px;
       padding:7px 14px;font-size:12.5px;cursor:pointer}
  .tab.on{background:var(--accent2);color:#fff}
  .chip{display:inline-flex;align-items:center;gap:4px;background:var(--card2);border-radius:999px;
        padding:2px 8px;margin:2px 4px 2px 0;font-size:11.5px}
  .chip b{font-weight:500;color:var(--muted)}
  .chip span{cursor:pointer;opacity:.55}
  .chip span:hover{opacity:1;color:var(--bad)}
  .catrow{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .catrow .nm{width:150px}
  .catrow .nm input{width:100%}
  .catrow .sec{color:var(--muted);font-variant-numeric:tabular-nums;min-width:86px}
  .catrow .rules{flex:1;min-width:240px}
  footer{text-align:center;font-size:11.5px;color:var(--muted);margin-top:26px}
  @media(max-width:860px){.cards,.grid2{grid-template-columns:1fr}}
.bar{border-radius:10px;padding:10px 14px;margin:0 0 14px;font-size:13px;
     display:flex;align-items:center;flex-wrap:wrap;gap:8px;line-height:1.5}
.bar.ok{background:rgba(102,187,106,.15);border:1px solid rgba(102,187,106,.55)}
.bar.bad{background:rgba(229,115,115,.15);border:1px solid rgba(229,115,115,.6)}
.bar b{font-weight:600}
</style>
<link rel="icon" href="data:,">
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>时间面板</h1>
      <div class="sub" id="sub"></div>
    </div>
    <div class="hbtns">
      <button class="btn" id="toolBtn">⚙ 工具</button>
      <button class="btn" id="theme">☀ 白天模式</button>
    </div>
  </header>

  <div id="trackBar"></div>
  <div id="onboard"></div>
  <div id="warn"></div>
  <div class="工具" id="工具" style="display:none">
    <div class="tabs">
      <button class="tab on" data-tab="assist">分类助手</button>
      <button class="tab" data-tab="manage">分类管理</button>
      <button class="tab" data-tab="import">导入旧数据</button>
      <button class="tab" data-tab="sys">系统设置</button>
      <button class="tab" data-tab="about">关于</button>
      <button class="tab" data-tab="presets">方案专区</button>
    </div>

    <div id="tab-assist">
      <div class="row">
        <input type="text" id="q" placeholder="搜索进程 / 标题…" style="min-width:190px">
        <select id="sortBy">
          <option value="sec">用时最多</option>
          <option value="days">使用天数</option>
          <option value="name">进程名</option>
          <option value="cat">所属分类</option>
        </select>
        <label class="tip" style="margin:0"><input type="checkbox" id="showAll"> 含已分类进程</label>
        <label class="tip" style="margin:0"><input type="checkbox" id="newFocus"> 新建分类标为专注</label>
        <button class="btn" id="btnReload">刷新列表</button>
        <button class="btn primary" id="btnSave">保存勾选的调整</button>
        <button class="btn" id="btnMaintain">立即维护</button>
      <button class="btn" id="btnCheckup">一键体检</button>
      <button class="btn" id="btnWeekly">生成周报</button>
      <button class="btn" id="btnCsv">导出 CSV</button>
      <button class="btn" id="btnGoal">设目标</button>
      </div>
      <div class="tip" id="assistHint" style="margin:0 0 6px">正在加载…</div>
      <div class="tip" style="margin:0 0 6px">
        用法：勾上要处理的进程 → 在「调整到」里选一个分类（或新建）→ 点「保存勾选的调整」。
        想看已经归好类的进程，勾上「含已分类进程」。
      </div>
      <div id="assistTable"></div>
    </div>

    <div id="tab-manage" style="display:none">
      <div class="tip" style="margin:0 0 8px">
        改名称 / 切专注 / 删规则都会立刻写回 <b>category.txt</b>（自动留 .bak 备份）。<br>
        <b>规则只有两种：</b>「按进程名」最准，例如 <i>Code.exe</i>、<i>chrome.exe</i>；
        「按窗口关键词」看标题，例如 <i>bilibili</i>、<i>八年级</i>——
        同一个程序干不同的事（浏览器里既看视频又查资料）时就得靠它。<br>
        <b>分类的先后顺序 = 匹配优先级</b>（从上往下先命中先生效），要调顺序请直接编辑 category.txt。
      </div>
      <div id="manageBox"></div>
    </div>

    <div id="tab-import" style="display:none">
      <div class="tip" style="margin:0 0 8px">
        换电脑 / 从旧版本搬过来时用：自动搜索旧项目 → 一键导入日志与分类配置。<br>
        <b>只读旧目录</b>（只复制）、<b>同名不覆盖</b>、导入前先整包备份。导入完面板会自动刷新。
      </div>
      <div class="row">
        <button class="btn primary" id="btnAutoScan">自动搜索旧项目</button>
        <input type="text" id="migPath" placeholder="或手动填旧目录路径，如 E:\桌面\焦点监控" style="min-width:280px">
        <button class="btn" id="btnScanPath">扫描这个路径</button>
      </div>
      <div id="migList"></div>
    </div>

    <div id="tab-sys" style="display:none">
      <div class="tip" style="margin:0 0 10px">
        这几项都是"写进系统/写进配置"的开关，改完立刻生效（不用手动跑 bat）。
      </div>
      <div id="sysBox"></div>
      <div class="tip" style="margin:12px 0 0" id="sysPost"></div>
    </div>

    <div id="tab-about" style="display:none"><div id="aboutBox"></div></div>
    <div id="tab-presets" style="display:none">
      <div class="tip" style="margin:0 0 8px">
        导入<b>社区分享的分类方案</b>。<b>增量模式</b>：你已归类的规则一律保持不变，
        只添加你没有的规则；方案内部互斥的规则会跳过并列出来。</div>
      <div class="row">
        <select id="presetSel" style="min-width:200px"><option value="">—— 选择内置方案 ——</option></select>
        <button class="btn" id="btnPresetLoad">载入选中方案</button>
        <button class="btn primary" id="btnPresetScan">解析预览</button>
        <button class="btn" id="btnPresetEnable">启用（增量补规则）</button>
        <button class="btn" id="btnPresetDisable">停用（移除该包规则）</button>
        <button class="btn primary" id="btnPresetApply" disabled>只导入新增项</button>
      </div>
      <textarea id="presetText" style="width:100%;min-height:150px;margin-top:8px;
        font-family:monospace;font-size:12px" placeholder="或直接粘贴方案内容（category.txt 格式）"></textarea>
      <div id="presetResult" style="margin-top:10px"></div>
    </div>

    <div class="out" id="toolOut"></div>
  </div>

  <div class="seg" id="seg"></div>
  <div class="tip" id="segTip" style="margin:-6px 0 14px"></div>

  <div class="cards">
    <div class="card"><div class="k" id="kFocusK">专注</div><div class="v" id="kFocus">—</div>
      <div class="delta" id="dFocus"></div></div>
    <div class="card"><div class="k" id="kTotalK">在线</div><div class="v" id="kTotal">—</div>
      <div class="delta" id="dTotal"></div></div>
    <div class="card"><div class="k">专注占比</div><div class="v" id="kRate">—</div>
      <div class="delta" id="dRate"></div>
      <div id="goalBar" style="margin-top:10px"></div></div>
  </div>

  <div class="grid2">
    <div class="panel">
      <h2 id="pieTitle">时间构成</h2>
      <div id="pie"></div>
      <div class="legend" id="pieLegend"></div>
    </div>
    <div class="panel">
      <h2 class="hflex"><span id="topTitle">窗口排行</span>
        <span class="hsub" id="topPager"></span>
        <span class="hsub" style="margin-left:auto">
          <select id="topMin" title="上榜门槛：低于这个时长的不显示">
            <option value="15" selected>≥15 分钟</option>
            <option value="5">≥5 分钟</option>
            <option value="30">≥30 分钟</option>
            <option value="60">≥1 小时</option>
            <option value="0">不过滤</option>
          </select>
        </span>
        <span class="seg sm" id="topAgg">
          <button data-v="1" class="on">按进程</button>
          <button data-v="0">按窗口</button>
        </span>
      </h2>
      <div id="topWin"></div>
    </div>
  </div>

  <div class="panel" style="margin-bottom:16px">
    <h2 class="hflex">专注 / 在线趋势
      <span class="hsub"><span id="rangeTip"></span></span>
      <span class="seg sm" id="grainSeg"></span>
    </h2>
    <div id="trend"></div>
    <div class="tip">灰柱 = 在线，彩色叠在上面 = 专注。点任意一天看该日明细。</div>
  </div>

  <div class="panel" style="margin-bottom:16px">
    <h2 class="hflex"><span>活跃时段（每小时）</span>
      <span class="hsub" id="hourTip">灰 = 在线，绿 = 专注</span></h2>
    <div id="hourly"></div>
  </div>

  <div class="grid2">
    <div class="panel">
      <h2>分类明细</h2>
      <div class="tip" id="selDay" style="margin:0 0 10px"></div>
      <div id="selCats"></div>
      <div id="catTrend" style="margin-top:14px"></div>
    </div>
    <div class="panel">
      <h2>应用排行</h2>
      <div id="apps"></div>
    </div>
  </div>

  <!-- 番茄钟：单独成行，默认隐藏；开关在 工具 → 系统设置 -->
  <div class="panel" id="pomoCard" style="display:none;margin-bottom:16px">
    <h2 class="hflex"><span>专注计时（番茄钟）</span>
      <span class="hsub" id="pomoTip">25 分钟一段，完成会计入 sessions.json</span></h2>
    <div class="row">
      <button class="btn primary" id="pomo25">开始 25 分钟</button>
      <button class="btn" id="pomo50">开始 50 分钟</button>
      <button class="btn" id="pomoStop">停止</button>
      <span class="hsub" id="pomoClock">—</span>
    </div>
    <div id="pomoToday" class="tip" style="margin-top:8px"></div>
  </div>

<footer>作者 冰叁狼 · FocusLog · 数据全部在本机 · 生成于 <span id="gen"></span> · <span id="modeTip"></span></footer>
</div>

<script>
let D = __DATA__;
const SERVER = __SERVER__;

const CV = i => `var(--c${i % 10})`;
let CAT_COLOR = {};
function buildColors(){ CAT_COLOR = {}; D.catOrder.forEach((c,i) => CAT_COLOR[c] = i); }
buildColors();

function hm(s){
  s = Math.round(s);
  if (s < 60) return s + '秒';
  if (s < 3600) return Math.round(s/60) + '分钟';
  const h = Math.floor(s/3600), m = Math.round((s%3600)/60);
  return m ? `${h}小时${m}分钟` : `${h}小时`;
}
const esc = t => String(t).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const colorOf = name => CV(CAT_COLOR[name] ?? 9);

/* ---------- 视图状态 ---------- */
const RANGES = [['today','今日'], ['d7','近 7 天'], ['d30','近 30 天'], ['all','全部']];
const GRAINS = [['day','按天'], ['week','按周'], ['month','按月']];
const CN_WD = ['周一','周二','周三','周四','周五','周六','周日'];

let view = 'd7';            // today | d7 | d30 | all | day:… | week:… | month:…
let grain = 'day';          // 趋势图分组：day | week | month
let selected = D.today;     // 趋势图当前高亮的那个单元

const empty = () => ({f:0, t:0, cats:[], apps:[]});
function byKey(list, keyName, key){
  const r = list.find(x => x[keyName] === key);
  return r ? {f:r.f, t:r.t, cats:r.c, apps:r.a} : null;
}
function viewData(){
  if (view.startsWith('day:'))   return byKey(D.days,   'd', view.slice(4))  || empty();
  if (view.startsWith('week:'))  return byKey(D.weeks,  'k', view.slice(5))  || empty();
  if (view.startsWith('month:')) return byKey(D.months, 'k', view.slice(6))  || empty();
  const r = D.ranges[view] || empty();
  return {f:r.f, t:r.t, cats:r.cats, apps:r.apps};
}
function viewLabel(){
  if (view.startsWith('day:')){
    const d = view.slice(4);
    return d + ' ' + CN_WD[(new Date(d + 'T00:00:00').getDay() + 6) % 7];
  }
  if (view.startsWith('week:')){
    const w = D.weeks.find(x => x.k === view.slice(5));
    return w ? `${w.k} 周（${w.d0.slice(5)} ~ ${w.d1.slice(5)}）` : view.slice(5);
  }
  if (view.startsWith('month:')) return view.slice(6) + ' 月';
  return {today:'今日', d7:'近 7 天', d30:'近 30 天', all:'全部历史'}[view] || view;
}

/* ---------- 主题 ---------- */
const tbtn = document.getElementById('theme');
function setTheme(t){
  document.documentElement.dataset.theme = t;
  tbtn.textContent = t === 'dark' ? '☀ 白天模式' : '🌙 夜晚模式';
  try { localStorage.setItem('fl_theme', t); } catch(e){}
  draw();
}
tbtn.onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
try {
  const saved = localStorage.getItem('fl_theme');
  if (saved){ document.documentElement.dataset.theme = saved;
              tbtn.textContent = saved === 'dark' ? '☀ 白天模式' : '🌙 夜晚模式'; }
} catch(e){}

/* ---------- 渲染 ---------- */
function drawSeg(){
  let h = RANGES.map(([k,l]) =>
    `<button data-r="${k}" class="${view===k?'on':''}">${l}</button>`).join('');
  if (/^(day|week|month):/.test(view))
    h += `<button data-r="${view}" class="on">${viewLabel()}</button>`;
  const seg = document.getElementById('seg');
  seg.innerHTML = h;
  seg.querySelectorAll('button[data-r]').forEach(b => b.onclick = () => { view = b.dataset.r; draw(); });

  const g = document.getElementById('grainSeg');
  g.innerHTML = GRAINS.map(([k,l]) =>
    `<button data-g="${k}" class="${grain===k?'on':''}">${l}</button>`).join('');
  g.querySelectorAll('button[data-g]').forEach(b => b.onclick = () => { grain = b.dataset.g; draw(); });

  document.getElementById('segTip').textContent =
    '范围决定上面卡片 / 环形图 / 排行；粒度在趋势卡片右上角。点趋势条任意一格，整页切到那一格。';
}

function shiftDate(dstr, n){
  const d = new Date(dstr + 'T00:00:00'); d.setDate(d.getDate() + n);
  return d.toISOString().slice(0,10);
}
function aggDays(from, to){
  let f = 0, t = 0;
  D.days.forEach(r => { if (r.d >= from && r.d <= to){ f += r.f; t += r.t; } });
  return {f:f, t:t};
}
function deltaTxt(cur, prev){
  if (!prev) return '';
  const p = Math.round((cur - prev) / prev * 100);
  const cls = p >= 0 ? 'up' : 'down';
  return `较上一周期 <b class="${cls}">${p>=0?'+':''}${p}%</b>`;
}
function drawCards(){
  const v = viewData(), lb = viewLabel();
  document.getElementById('kFocusK').textContent = lb + '专注';
  document.getElementById('kTotalK').textContent = lb + '在线';
  document.getElementById('kFocus').textContent = hm(v.f);
  document.getElementById('kTotal').textContent = hm(v.t);
  document.getElementById('kRate').textContent = v.t ? Math.round(v.f/v.t*100) + '%' : '—';

  const span = {today:1, d7:7, d30:30}[view];
  const ids = ['dFocus','dTotal','dRate'];
  if (!span || !D.days.length){ ids.forEach(i => document.getElementById(i).textContent = ''); return; }
  const cur = aggDays(shiftDate(D.today, -(span-1)), D.today);
  const prev = aggDays(shiftDate(D.today, -(2*span-1)), shiftDate(D.today, -span));
  document.getElementById('dFocus').innerHTML = deltaTxt(cur.f, prev.f);
  document.getElementById('dTotal').innerHTML = deltaTxt(cur.t, prev.t);
  const cr = cur.t ? cur.f/cur.t*100 : 0, pr = prev.t ? prev.f/prev.t*100 : 0;
  document.getElementById('dRate').innerHTML = deltaTxt(cr, pr);
}

function drawPie(){
  const v = viewData(), box = document.getElementById('pie');
  document.getElementById('pieTitle').textContent = viewLabel() + '时间构成';
  const sum = v.cats.reduce((a,c) => a + c[1], 0);
  if (!sum){ box.innerHTML = '<div class="tip">该范围没有数据</div>';
             document.getElementById('pieLegend').innerHTML = ''; return; }
  const R = 78, SW = 22, C = 2*Math.PI*R;
  let off = 0, seg = '';
  v.cats.forEach(([name, sec]) => {
    const frac = sec/sum;
    seg += `<circle cx="100" cy="100" r="${R}" fill="none" stroke="${colorOf(name)}"
      stroke-width="${SW}" stroke-dasharray="${(C*frac).toFixed(2)} ${C.toFixed(2)}"
      stroke-dashoffset="${(-off).toFixed(2)}" transform="rotate(-90 100 100)"></circle>`;
    off += C*frac;
  });
  const pct = Math.round(v.f/v.t*100);
  box.innerHTML = `<svg width="200" height="200" viewBox="0 0 200 200">
    <circle cx="100" cy="100" r="${R}" fill="none" stroke="var(--grid)" stroke-width="${SW}"></circle>
    ${seg}
    <text x="100" y="94" text-anchor="middle" fill="var(--text)" font-size="26" font-weight="600">${pct}%</text>
    <text x="100" y="116" text-anchor="middle" fill="var(--muted)" font-size="12">专注占比</text></svg>`;
  document.getElementById('pieLegend').innerHTML = v.cats.map(([n,s]) =>
    `<div class="lg"><span class="dot" style="background:${colorOf(n)}"></span>
      <span class="nm">${esc(n)}</span><span class="vl">${hm(s)} · ${Math.round(s/sum*100)}%</span></div>`).join('');
}

const PAGE = 10;
let topRows = null, topPage = 0, topKey = null, topAgg = 1, topMin = 15;
let topCache = {};      // 已取过的排行结果：切回来秒开（数据刷新时整包清空）

/* 上榜门槛：低于 N 分钟的直接不显示（默认 15 分钟，和数据手册的口径一致） */
function applyMin(rows){
  if (!topMin) return rows || [];
  const floor = topMin * 60;
  return (rows || []).filter(r => (r[1] || 0) >= floor);
}

/* 静态快照的数据是 [proc|标题, 秒] 明细，这里就地按进程汇总 */
function aggregate(rows){
  if (!topAgg || !rows || !rows.length) return rows || [];
  const m = new Map();
  rows.forEach(([k, sec]) => {
    const proc = String(k).split(' | ')[0];
    const title = String(k).split(' | ').slice(1).join(' | ');
    const t = m.get(proc) || {sec: 0, sub: '', subSec: 0};
    t.sec += sec;
    if (sec > t.subSec){ t.subSec = sec; t.sub = title; }
    m.set(proc, t);
  });
  return [...m.entries()].sort((a, b) => b[1].sec - a[1].sec)
    .map(([proc, t]) => [proc, Math.round(t.sec), t.sub, Math.round(t.subSec)]);
}

function setTopAgg(v){
  topAgg = (String(v) === '1') ? 1 : 0;     // dataset 出来是字符串，"0" 也是真值，必须转
  topKey = null;
  topPage = 0;
  [...document.querySelectorAll('#topAgg button')].forEach(b => {
    b.className = (String(b.dataset.v) === String(v)) ? 'on' : '';
  });
  refreshTop();
}

function refreshTop(){
  if (!SERVER){
    document.getElementById('topTitle').textContent = '窗口排行 · ' + viewLabel();
    let rows = [];
    if (view.startsWith('day:')){
      const r = D.days.find(x => x.d === view.slice(4));
      rows = (r && r.w) || [];
    } else if (view.startsWith('week:')){
      const w = D.weeks.find(x => x.k === view.slice(5));
      rows = (w && w.win) || [];
    } else if (view.startsWith('month:')){
      const m = D.months.find(x => x.k === view.slice(6));
      rows = (m && m.win) || [];
    } else {
      const r = D.ranges[view];
      rows = (r && r.win) || [];
    }
    topRows = applyMin(aggregate(rows)); topPage = 0; renderTop(); return;
  }
  const ck = view + '|' + topAgg + '|' + topMin;
  if (topCache[ck]){ topRows = topCache[ck]; topPage = 0; renderTop(); return; }
  if (topKey === ck){ renderTop(); return; }
  topKey = ck;
  document.getElementById('topTitle').textContent = '窗口排行 · ' + viewLabel();
  fetch('/api/windows?range=' + encodeURIComponent(view) + '&agg=' + topAgg
        + '&min=' + topMin)
    .then(x => x.json())
    .then(r => {
      topCache[ck] = r.items || [];
      topRows = topCache[ck]; topPage = 0; renderTop();
      if (r.stale)                                           // 服务端给的是旧数据：稍后静默换新
        setTimeout(() => {
          if (topKey === ck) fetch('/api/windows?range=' + encodeURIComponent(view)
            + '&agg=' + topAgg + '&min=' + topMin)
            .then(x => x.json())
            .then(r2 => { if (!r2.stale){ topCache[ck] = r2.items || [];
              if (topKey === ck){ topRows = topCache[ck]; renderTop(); } } })
            .catch(() => {});
        }, 1500);
    })
    .catch(() => { topRows = []; renderTop(); });
}

function renderTop(){
  const box = document.getElementById('topWin'), pager = document.getElementById('topPager');
  if (!topRows || !topRows.length){
    box.innerHTML = '<div class="tip">没有数据' + (topMin ? `（门槛 ≥${topMin} 分钟过滤后为空，右上角可放宽）` : '') + '</div>';
    pager.innerHTML = '';
    return;
  }
  const pages = Math.max(1, Math.ceil(topRows.length / PAGE));
  topPage = Math.min(Math.max(0, topPage), pages - 1);
  const max = topRows[0][1] || 1;
  const pageRows = topRows.slice(topPage * PAGE, topPage * PAGE + PAGE);
  const padRows = Math.max(0, PAGE - pageRows.length);     // 末页补位，高度不跳
  box.innerHTML = pageRows.map(r => {
    const name = r[0], sec = r[1], sub = r[2], subSec = r[3];
    const proc = topAgg ? name : String(name).split(' | ')[0];
    const tip = (topAgg && sub) ? `<span class="hsub" style="display:block;margin-top:2px">
        主要窗口：${esc(sub)} · ${hm(subSec || 0)}</span>` : '';
    return `<div class="bar-row${tip ? ' tall' : ''}" title="${esc(name)}"><span class="nm">${esc(name)}${tip}</span>
      <span class="bar-track"><span class="bar-fill"
        style="width:${Math.max(2, Math.round(sec / max * 100))}%;background:${colorOf(D.procCat[proc] || '未分类')}"></span></span>
      <span class="vl">${hm(sec)}</span></div>`;
  }).join('') + Array(padRows).fill('<div class="bar-row bar-pad">&nbsp;</div>').join('');
  pager.innerHTML = `<span class="pager">
      <button id="pvTop" ${topPage === 0 ? 'disabled' : ''}>◀</button>
      ${topPage + 1}/${pages}
      <button id="nxTop" ${topPage === pages - 1 ? 'disabled' : ''}>▶</button></span>
    <span style="margin-left:6px">共 ${topRows.length} 个${topAgg ? '程序' : '窗口'}`
      + (topMin ? `（已隐藏 < ${topMin} 分钟的）` : '') + `</span>`;
  const pv = document.getElementById('pvTop'), nx = document.getElementById('nxTop');
  if (pv) pv.onclick = () => { topPage--; renderTop(); };
  if (nx) nx.onclick = () => { topPage++; renderTop(); };
}

document.getElementById('topMin').onchange = (e) => {
  topMin = parseInt(e.target.value, 10) || 0;
  topKey = null;
  topPage = 0;
  refreshTop();
};

document.getElementById('topAgg').onclick = (e) => {
  const b = e.target.closest('button');
  if (b) setTopAgg(b.dataset.v);
};

function trendWindow(){
  // 趋势条的"看多长"跟着上面的范围走：
  // 今日/近 7 天 → 最近 7 天；近 30 天 → 最近 30 天；全部 → 全历史
  if (view === 'all') return D.days;
  if (view === 'd30') return D.days.slice(-30);
  return D.days.slice(-7);
}
function trendItems(){
  const days = trendWindow();
  const from = days.length ? days[0].d : '';
  if (grain === 'week')
    return D.weeks.filter(w => w.d1 >= from).map(w =>
      ({key:'week:'+w.k, f:w.f, t:w.t, big:w.k.slice(5), small:w.d0.slice(5)+'~'+w.d1.slice(5),
        title:`${w.k} 周（${w.d0} ~ ${w.d1}）`}));
  if (grain === 'month')
    return D.months.filter(m => m.k >= from.slice(0,7)).map(m =>
      ({key:'month:'+m.k, f:m.f, t:m.t, big:parseInt(m.k.slice(5),10)+'月', small:m.k, title:`${m.k}`}));
  return days.map(x => ({key:'day:'+x.d, f:x.f, t:x.t, big:x.d.slice(5),
                         small:CN_WD[(new Date(x.d+'T00:00:00').getDay()+6)%7],
                         title:`${x.d} ${CN_WD[(new Date(x.d+'T00:00:00').getDay()+6)%7]}`}));
}

function tickIdx(items){
  const n = items.length;
  if (grain !== 'day' || n <= 16) return items.map((d,i) => i);
  const idx = [];
  items.forEach((d,i) => {
    const dt = new Date(d.key.slice(4) + 'T00:00:00');
    const wd = (dt.getDay() + 6) % 7;
    if (wd === 0 || dt.getDate() === 1) idx.push(i);      // 只标「周一」和「每月 1 号」
  });
  if (idx.length < 3){                                     // 边界太稀就补均匀刻度（含首末）
    const every = Math.ceil(n / 6);
    const set = new Set(idx);
    for (let i = 0; i < n; i += every) set.add(i);
    set.add(0); set.add(n - 1);
    return Array.from(set).sort((a,b) => a - b);
  }
  return idx;
}

function drawTrend(){
  const box = document.getElementById('trend');
  const items = trendItems();
  document.getElementById('rangeTip').textContent =
    {day:'按天', week:'按周', month:'按月'}[grain] + ' · ' + items.length + ' 格';
  if (!items.length){ box.innerHTML = '<div class="tip">暂无数据</div>'; return; }

  const W = 1060, H = 226, pad = 36, baseY = H - 46;
  const max = Math.max(...items.map(d => d.t), 3600);
  const step = (W - pad*2) / items.length;
  const bw = Math.max(3, Math.min(20, step - 2.5));

  let bars = '';
  items.forEach((d,i) => {
    const x = pad + i*step + (step - bw)/2;
    const th = Math.max(1, d.t/max*(baseY - 14));
    const fh = Math.max(0, d.f/max*(baseY - 14));
    const hot = d.key === view || d.key === 'day:' + selected;
    bars += `<g class="bars" data-k="${d.key}">
      <rect x="${x.toFixed(1)}" y="${(baseY-th).toFixed(1)}" width="${bw.toFixed(1)}" height="${th.toFixed(1)}"
            rx="2" fill="var(--grid)"></rect>
      <rect x="${x.toFixed(1)}" y="${(baseY-fh).toFixed(1)}" width="${bw.toFixed(1)}" height="${fh.toFixed(1)}"
            rx="2" fill="${hot ? 'var(--accent)' : 'var(--accent2)'}" opacity="${hot?1:.85}"></rect>
      <rect x="${x.toFixed(1)}" y="6" width="${Math.max(step, bw).toFixed(1)}" height="${H-6}" fill="transparent"></rect>
      <title>${d.title}　在线 ${hm(d.t)}　专注 ${hm(d.f)}</title></g>`;
  });

  // 刻度：只画规律位置的文字，并在该柱下画一根淡竖线（让规律看得见）
  const idx = tickIdx(items);
  const twoLine = step * (items.length / Math.max(idx.length,1)) >= 30;
  let labels = '';
  idx.forEach(i => {
    const d = items[i];
    const cx = pad + i*step + step/2;
    labels += `<line x1="${cx.toFixed(1)}" y1="${(baseY+1).toFixed(1)}" x2="${cx.toFixed(1)}" y2="${(baseY+5).toFixed(1)}"
      stroke="var(--muted)" opacity=".5"></line>
      <text x="${cx.toFixed(1)}" y="${baseY+17}" text-anchor="middle"
        fill="var(--muted)" font-size="10">${d.big}</text>`;
    if (twoLine)
      labels += `<text x="${cx.toFixed(1)}" y="${baseY+30}" text-anchor="middle"
        fill="var(--muted)" font-size="9" opacity=".75">${d.small}</text>`;
  });

  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <line x1="${pad}" y1="${(baseY+0.5).toFixed(1)}" x2="${W-pad}" y2="${(baseY+0.5).toFixed(1)}" stroke="var(--line)"></line>
    <line x1="${pad}" y1="12" x2="${pad}" y2="${baseY}" stroke="var(--line)" opacity=".5"></line>
    <text x="2" y="16" fill="var(--muted)" font-size="10">${hm(max)}</text>
    <text x="2" y="${(baseY - (baseY-14)/2).toFixed(0)}" fill="var(--muted)" font-size="10">${hm(max/2)}</text>
    ${bars}${labels}</svg>`;

  box.querySelectorAll('g.bars').forEach(g => g.onclick = () => {
    const k = g.dataset.k;
    view = k;
    if (k.startsWith('week:')) grain = 'week';
    else if (k.startsWith('month:')) grain = 'month';
    else { grain = 'day'; selected = k.slice(4); }
    draw();
  });
}

function drawSel(){
  const v = viewData();
  document.getElementById('selDay').textContent =
    `${viewLabel()}　专注 ${hm(v.f)} / 在线 ${hm(v.t)}`;
  const box = document.getElementById('selCats');
  const sum = v.cats.reduce((a,c) => a + c[1], 0);
  if (!sum){ box.innerHTML = '<div class="tip">没有数据</div>'; return; }
  const max = v.cats[0][1];
  box.innerHTML = v.cats.map(([n,s]) =>
    `<div class="bar-row" data-cat="${esc(n)}" style="cursor:pointer"><span class="nm">${esc(n)}</span>
      <span class="bar-track"><span class="bar-fill"
        style="width:${Math.max(2, Math.round(s/max*100))}%;background:${colorOf(n)}"></span></span>
      <span class="vl">${hm(s)} · ${Math.round(s/sum*100)}%</span></div>`).join('');
  box.querySelectorAll('[data-cat]').forEach(el => el.onclick = () => {
    trendCat = el.dataset.cat;
    drawCatTrend();
  });
  drawCatTrend();
}

function drawApps(){
  const v = viewData(), box = document.getElementById('apps');
  if (!v.apps.length){ box.innerHTML = '<div class="tip">没有数据</div>'; return; }
  const max = v.apps[0][1];
  box.innerHTML = v.apps.map(([n,s]) =>
    `<div class="bar-row"><span class="nm" title="${esc(n)}">${esc(n)}</span>
      <span class="bar-track"><span class="bar-fill"
        style="width:${Math.max(2, Math.round(s/max*100))}%;background:${colorOf(D.procCat[n] || '未分类')}"></span></span>
      <span class="vl">${hm(s)}</span></div>`).join('');
}

function draw(){
  drawSeg(); drawCards(); drawPie(); refreshTop(); drawTrend(); drawHourly(); drawSel(); drawApps();
  updateTitle();
  // 注意：新画的卡片一定要挂进这条链，否则函数写了也不会画（历史踩过两次：
  //       drawTopWin 漏改名字、drawHourly 从未挂上）
}

/* ---------- 头部 / 底部 ---------- */
function updateTitle(){
  const f = (D.todayRow && D.todayRow.f) || 0;
  document.title = '今日专注 ' + hm(f) + ' · FocusLog';
}
function drawHead(){
  document.getElementById('sub').textContent =
    `今天是 ${D.today} · 逐日明细回看 ${D.range[0]} ~ ${D.range[1]} 共 ${D.days.length} 天`
      + ` · 归档 ${D.months.length} 个月 / ${D.weeks.length} 周`;
  document.getElementById('gen').textContent = D.generated;
  document.getElementById('modeTip').textContent = SERVER
    ? '服务模式：数据每 60 秒自动刷新；归档 / 重算 / 导出每天跨天自动跑，「⚙ 工具」里只剩分类助手'
    : '静态快照：重新运行 dashboard.py 或双击「面板服务.bat」获得实时工具';
  const w = document.getElementById('warn');
  w.innerHTML = (D.dupWarn && D.dupWarn.length)
    ? `<div class="warn">category.txt 里有重复分类名：${D.dupWarn.map(esc).join('、')}（已合并规则，建议手工去重）</div>` : '';
}

/* ---------- 工具（需服务模式） ---------- */
const toolBtn = document.getElementById('toolBtn');
const 工具 = document.getElementById('工具');
const out = document.getElementById('toolOut');
if (!SERVER){ toolBtn.disabled = true; toolBtn.title = '请双击 专注记录\\面板服务.bat 后在服务模式使用'; }
toolBtn.onclick = () => { 工具.style.display = 工具.style.display === 'none' ? 'block' : 'none'; };

async function api(path, body){
  const r = await fetch(path, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body || {})});
  return await r.json();
}

/* ---------- 工具：分类助手 / 分类管理 ---------- */
let ALLCAT = null;          // /api/categories 的结果

async function api(path, body){
  const r = await fetch(path, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body || {})});
  return await r.json();
}

document.querySelectorAll('.tab').forEach(tb => tb.onclick = async () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('on', x === tb));
  const which = tb.dataset.tab;
  ['assist', 'manage', 'import', 'sys', 'about', 'presets'].forEach(k => {
    document.getElementById('tab-' + k).style.display = (k === which) ? 'block' : 'none';
  });
  if (which === 'manage') await loadCats();
  if (which === 'sys') await loadSys();
  if (which === 'about') await loadAbout();
  if (which === 'presets') await loadPresetList();
});

async function loadCats(){
  const hint = document.getElementById('assistHint');
  hint.textContent = '正在统计全部历史（首次几秒）…';
  try {
    const r = await api('/api/categories');
    ALLCAT = r;
    const un = (r.procs || []).filter(p => !p.cat).length;
    hint.textContent = `共 ${r.cats.length} 个分类 / ${r.procs.length} 个进程`
      + `（其中还没归类的 ${un} 个）`
      + (r.dup && r.dup.length ? `　⚠ 重复分类名：${r.dup.join('、')}` : '');
    renderAssist();
    renderManage();
  } catch(e){ hint.textContent = '加载失败：' + e; }
}

/* --- 分类助手：搜索 / 排序 / 批量改分类 --- */
function assistRows(){
  if (!ALLCAT) return [];
  const onlyUn = !document.getElementById('showAll').checked;
  const q = document.getElementById('q').value.trim().toLowerCase();
  let rows = ALLCAT.procs.filter(r => !onlyUn || !r.cat);
  if (q) rows = rows.filter(r => r.proc.toLowerCase().includes(q));
  const by = document.getElementById('sortBy').value;
  rows.sort((a,b) => {
    if (by === 'sec')  return b.sec - a.sec;
    if (by === 'days') return b.days - a.days;
    if (by === 'cat')  return (a.cat || 'zz').localeCompare(b.cat || 'zz') || b.sec - a.sec;
    return a.proc.toLowerCase().localeCompare(b.proc.toLowerCase());
  });
  return rows;
}

function renderAssist(){
  const box = document.getElementById('assistTable');
  if (!ALLCAT){ box.innerHTML = ''; return; }
  const rows = assistRows().slice(0, 200);
  if (!rows.length){
    const all = (ALLCAT.procs || []).length;
    box.innerHTML = `<div class="tip">没有符合条件的进程${all && !document.getElementById('showAll').checked
      ? '　（只显示"还没归类"的进程；想看你已经归好类的，勾上「含已分类进程」）' : ''}</div>`;
    return;
  }
  const opts = D.catOrder.filter(c => c !== '未分类')
    .map(c => `<option>${esc(c)}</option>`).join('');
  box.innerHTML = `<table><thead><tr>
      <th style="width:30%">进程</th><th>累计</th><th>天数</th>
      <th style="width:18%">当前分类</th><th style="width:22%">调整到</th><th style="width:8%">应用</th>
    </tr></thead><tbody>` + rows.map((r,i) => `<tr>
      <td title="${esc(r.proc)}">${esc(r.proc)}</td>
      <td>${hm(r.sec)}</td><td>${r.days}</td>
      <td>${r.cat ? `<span class="pill">${esc(r.cat)}</span>` : '<span class="pill">未分类</span>'}</td>
      <td><select data-i="${i}"><option value="">（不调整）</option>
            <option value="__new__">（新建分类…）</option>${opts}</select>
          <input type="text" data-new="${i}" placeholder="新分类名"
                 style="width:96px;margin-left:4px;display:none"></td>
      <td><input type="checkbox" data-f="${i}"></td></tr>`).join('') + '</tbody></table>';
  box.querySelectorAll('select[data-i]').forEach(s => {
    s.onchange = () => {
      const inp = box.querySelector(`input[data-new="${s.dataset.i}"]`);
      inp.style.display = s.value === '__new__' ? 'inline-block' : 'none';
      if (s.value) box.querySelector(`input[data-f="${s.dataset.i}"]`).checked = true;
    };
  });
  box._rows = rows;
}

document.getElementById('showAll').onchange = renderAssist;
document.getElementById('q').oninput = renderAssist;
document.getElementById('sortBy').onchange = renderAssist;
document.getElementById('btnReload').onclick = loadCats;

document.getElementById('btnSave').onclick = async () => {
  const box = document.getElementById('assistTable');
  const rows = box._rows || [];
  const focusNew = document.getElementById('newFocus').checked;
  const jobs = [];
  box.querySelectorAll('select[data-i]').forEach(s => {
    const i = +s.dataset.i;
    if (!box.querySelector(`input[data-f="${i}"]`).checked) return;
    let to = s.value;
    if (to === '__new__') to = (box.querySelector(`input[data-new="${i}"]`).value || '').trim();
    if (!to) return;
    if (to === rows[i].cat) return;
    jobs.push({action:'move_rule', proc: rows[i].proc, to: to, focus: focusNew});
  });
  if (!jobs.length){ out.textContent = '没有需要应用的行（先选「调整到」并勾选）'; return; }
  out.textContent = '正在写入…';
  const done = [];
  for (const j of jobs){
    const r = await api('/api/cat_edit', j);
    done.push((r.ok ? '✓ ' : '✗ ') + (r.message || ''));
  }
  out.textContent = done.join('\n') + '\n（共 ' + jobs.length + ' 条）';
  await loadCats();
};

/* --- 分类管理：改名 / 切专注 / 规则增删 --- */
function renderManage(){
  const box = document.getElementById('manageBox');
  if (!ALLCAT){ box.innerHTML = ''; return; }
  box.innerHTML = ALLCAT.cats.map((c,i) => `
    <div class="catrow" data-cat="${esc(c.name)}">
      <span class="nm"><input type="text" data-name="${i}" value="${esc(c.name)}"></span>
      <label class="tip" style="margin:0">
        <input type="checkbox" data-focus="${i}" ${c.focus ? 'checked' : ''}> 专注</label>
      <span class="sec">${hm(c.sec)}</span>
      <span class="rules">${c.rules.map(([tp,kw]) =>
        `<span class="chip"><b>${tp === 'proc' ? '进程' : '关键词'}</b>${esc(kw)}<span data-del="${i}|${tp}|${esc(kw)}">✕</span></span>`).join('')}
        <span class="chip" style="background:transparent;border:1px dashed var(--line)">
          <select data-addtype="${i}" style="padding:1px 4px">
            <option value="proc">按进程名</option><option value="title">按窗口关键词</option></select>
          <input type="text" data-addkw="${i}" placeholder="加关键字" style="width:96px">
          <span data-add="${i}">＋</span></span>
      </span>
      <button class="btn" data-save="${i}">保存</button>
      <button class="btn" data-delcat="${i}">删除分类</button>
    </div>`).join('');

  const act = async (body) => {
    const r = await api('/api/cat_edit', body);
    out.textContent = (r.ok ? '✓ ' : '✗ ') + (r.message || '');
    await loadCats();
  };
  box.querySelectorAll('[data-del]').forEach(s => s.onclick = () => {
    const [i, tp, kw] = s.dataset.del.split('|');
    act({action:'del_rule', cat: ALLCAT.cats[+i].name, type: tp, keyword: kw});
  });
  box.querySelectorAll('[data-add]').forEach(s => s.onclick = () => {
    const i = +s.dataset.add;
    const tp = box.querySelector(`select[data-addtype="${i}"]`).value;
    const kw = box.querySelector(`input[data-addkw="${i}"]`).value.trim();
    if (!kw){ out.textContent = '先填关键字'; return; }
    act({action:'add_rule', cat: ALLCAT.cats[i].name, type: tp, keyword: kw});
  });
  box.querySelectorAll('[data-save]').forEach(b => b.onclick = async () => {
    const i = +b.dataset.save;
    const c = ALLCAT.cats[i];
    const newName = box.querySelector(`input[data-name="${i}"]`).value.trim();
    const focus = box.querySelector(`input[data-focus="${i}"]`).checked;
    if (newName && newName !== c.name)
      await act({action:'rename_cat', cat: c.name, new: newName});
    if (focus !== c.focus)
      await act({action:'set_focus', cat: (newName || c.name), focus: focus});
  });
  box.querySelectorAll('[data-delcat]').forEach(b => b.onclick = () => {
    const i = +b.dataset.delcat;
    if (confirm(`删除分类 [${ALLCAT.cats[i].name}]？规则会一起删掉`))
      act({action:'del_cat', cat: ALLCAT.cats[i].name});
  });
}

/* ---------- 导入旧数据 ---------- */
function renderMig(items){
  const box = document.getElementById('migList');
  if (!items || !items.length){
    box.innerHTML = '<div class="tip">没搜到旧项目。可以手动填路径（旧版那个含 focus_tracker.exe 的文件夹）。</div>';
    return;
  }
  box.innerHTML = `<table><thead><tr>
      <th style="width:38%">旧项目位置</th><th>日志</th><th>日期范围</th><th>分类</th><th style="width:14%"></th>
    </tr></thead><tbody>` + items.map((it,i) => `<tr>
      <td title="${esc(it.prog)}">${esc(it.prog)}</td>
      <td>${it.logs} 天</td>
      <td>${it.first || '-'} ~ ${it.last || '-'}</td>
      <td>${it.cats || 0} 类</td>
      <td><button class="btn" data-imp="${i}">导入</button></td>
    </tr>`).join('') + '</tbody></table>';
  box.querySelectorAll('button[data-imp]').forEach(b => b.onclick = async () => {
    const it = items[+b.dataset.imp];
    if (!confirm(`把旧数据导入到当前目录？\n\n${it.prog}\n（只复制、不覆盖，先自动备份）`)) return;
    b.disabled = true; out.textContent = '导入中…';
    try {
      const r = await api('/api/migrate/run', {path: it.prog});
      out.textContent = r.message || (r.ok ? '完成' : '失败');
      await loadCats();            // 分类可能变了
      await silentRefresh();        // 面板数据自动刷新
    } catch(e){ out.textContent = '出错：' + e; }
    finally { b.disabled = false; }
  });
}

document.getElementById('btnAutoScan').onclick = async (e) => {
  e.target.disabled = true; out.textContent = '正在搜索常见位置…';
  try {
    const r = await api('/api/migrate/scan', {});
    renderMig(r.items || []);
    out.textContent = (r.items || []).length ? `搜到 ${r.items.length} 处旧项目` : '没搜到，试试手动填路径';
  } catch(err){ out.textContent = '出错：' + err; }
  finally { e.target.disabled = false; }
};

document.getElementById('btnScanPath').onclick = async (e) => {
  const p = document.getElementById('migPath').value.trim();
  if (!p){ out.textContent = '先填一个路径'; return; }
  e.target.disabled = true; out.textContent = '扫描中…';
  try {
    const r = await api('/api/migrate/scan', {path: p});
    renderMig(r.items || []);
    out.textContent = r.ok ? ((r.items || []).length ? '找到了，可以点导入' : '没找到') : (r.message || '失败');
  } catch(err){ out.textContent = '出错：' + err; }
  finally { e.target.disabled = false; }
};

/* ---------- 目标进度 ---------- */
let GOAL = null;
async function loadGoal(){
  if (!SERVER) return;
  try { GOAL = (await fetch('/api/goals').then(x => x.json())).goals; } catch(e){ GOAL = null; }
  drawGoal();
}
function drawGoal(){
  const box = document.getElementById('goalBar');
  if (!box) return;
  const v = viewData();
  if (!GOAL || !GOAL.daily_focus_min){
    box.innerHTML = SERVER ? '<span class="tip">未设每日目标（点「设目标」）</span>' : '';
    return;
  }
  const target = GOAL.daily_focus_min * 60;
  const pct = Math.min(100, Math.round(v.f / target * 100));
  const done = v.f >= target;
  box.innerHTML = `<div class="tip" style="margin:0 0 4px">今日目标 ${hm(target)}　已完成 ${pct}%</div>
    <span class="bar-track" style="display:block;height:8px"><span class="bar-fill"
      style="display:block;width:${Math.max(2, pct)}%;background:${done ? 'var(--accent)' : 'var(--accent2)'}"></span></span>`;
}

/* ---------- 活跃时段热力 ---------- */
function drawHourly(){
  const box = document.getElementById('hourly');
  if (!box || !D.hourly) return;
  const max = Math.max.apply(null, D.hourly.map(h => h[0]).concat([1]));
  const W = 1060, H = 118, pad = 16, step = (W - pad*2) / 24, bw = step - 6;
  let g = '';
  D.hourly.forEach((h, i) => {
    const x = pad + i * step;
    const th = Math.max(1, h[0] / max * 72);
    const fh = Math.max(0, h[1] / max * 72);
    g += `<g><rect x="${x.toFixed(1)}" y="${(96-th).toFixed(1)}" width="${bw.toFixed(1)}" height="${th.toFixed(1)}"
            rx="2" fill="var(--grid)"></rect>
      <rect x="${x.toFixed(1)}" y="${(96-fh).toFixed(1)}" width="${bw.toFixed(1)}" height="${fh.toFixed(1)}"
            rx="2" fill="var(--accent2)"></rect>
      <text x="${(x+bw/2).toFixed(1)}" y="110" text-anchor="middle" fill="var(--muted)" font-size="9">${i}</text>
      <title>${i} 点　在线 ${hm(h[0])}　专注 ${hm(h[1])}</title></g>`;
  });
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
    <line x1="${pad}" y1="96.5" x2="${W-pad}" y2="96.5" stroke="var(--line)"></line>${g}</svg>`;
}

/* ---------- 分类趋势（点分类看近 30 天） ---------- */
let trendCat = null;
function drawCatTrend(){
  const box = document.getElementById('catTrend');
  if (!box) return;
  if (!trendCat){ box.innerHTML = '<div class="tip">点上面任意一行分类，看它近 30 天的走势。</div>'; return; }
  const days = D.days.slice(-30);
  const vals = days.map(x => {
    const hit = (x.c || []).find(y => y[0] === trendCat);
    return hit ? hit[1] : 0;
  });
  const max = Math.max.apply(null, vals.concat([1]));
  const W = 480, H = 90, step = W / days.length, bw = step - 2;
  let g = '';
  days.forEach((x, i) => {
    const hgt = Math.max(1, vals[i] / max * 60);
    g += `<rect x="${(i*step).toFixed(1)}" y="${(72-hgt).toFixed(1)}" width="${bw.toFixed(1)}"
      height="${hgt.toFixed(1)}" rx="1.5" fill="${colorOf(trendCat)}" opacity=".85">
      <title>${x.d}　${trendCat} ${hm(vals[i])}</title></rect>`;
  });
  box.innerHTML = `<div class="tip" style="margin:0 0 6px">${esc(trendCat)} · 近 30 天（峰值 ${hm(max)}）</div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}">
      <line x1="0" y1="72.5" x2="${W}" y2="72.5" stroke="var(--line)"></line>${g}</svg>`;
}

/* ---------- 方案专区：社区方案增量导入（两段式，绝不覆盖用户现状） ---------- */
let PRESET_LAST = null;
async function loadPresetList(){
  if (!SERVER) return;
  const sel = document.getElementById('presetSel');
  sel.innerHTML = '<option value="">—— 选择内置方案 ——</option>';
  let reg = {};
  try {
    const st = await fetch('/api/preset_status').then(x => x.json());
    reg = st.registry || {};
    const r = await fetch('/api/presets_list').then(x => x.json());
    (r.items || []).forEach(f => {
      const o = document.createElement('option');
      o.value = f;
      o.textContent = f + (reg[f] ? `（已启用 · ${reg[f].length} 条）` : '');
      sel.appendChild(o);
    });
  } catch(e){}
}
document.getElementById('btnPresetLoad').onclick = async () => {
  const v = document.getElementById('presetSel').value;
  if (!v){ out.textContent = '先选择一个内置方案'; return; }
  await presetPreview({name: v});
};
document.getElementById('btnPresetScan').onclick = () => presetPreview(
  {text: document.getElementById('presetText').value});
document.getElementById('btnPresetEnable').onclick = async (e) => {
  const v = document.getElementById('presetSel').value;
  if (!v){ out.textContent = '先选择一个方案'; return; }
  e.target.disabled = true;
  try {
    const r = await api('/api/preset_enable', {name: v});
    out.textContent = r.message || '';
    await loadPresetList();
  } finally { e.target.disabled = false; }
};
document.getElementById('btnPresetDisable').onclick = async (e) => {
  const v = document.getElementById('presetSel').value;
  if (!v){ out.textContent = '先选择一个方案'; return; }
  e.target.disabled = true;
  try {
    const r = await api('/api/preset_disable', {name: v});
    out.textContent = r.message || '';
    await loadPresetList();
  } finally { e.target.disabled = false; }
};
document.getElementById('btnPresetApply').onclick = async (e) => {
  if (!PRESET_LAST || !PRESET_LAST.add || !PRESET_LAST.add.length) return;
  e.target.disabled = true;
  const r = await api('/api/preset_apply', {adds: PRESET_LAST.add});
  out.textContent = r.message || '';
  await presetPreview({text: document.getElementById('presetText').value});
  e.target.disabled = false;
};
async function presetPreview(payload){
  const box = document.getElementById('presetResult');
  box.innerHTML = '<div class="tip">解析中…</div>';
  const r = await api('/api/preset_scan', payload);
  PRESET_LAST = r.ok ? r : null;
  document.getElementById('btnPresetApply').disabled = !(r.ok && r.add && r.add.length);
  if (!r.ok){ box.innerHTML = `<div class="tip">${esc(r.message || '')}</div>`; return; }
  const rows = a => a.map(x => `<tr><td style="width:42%">${esc(x.rule || x)}</td>
      <td>${esc(x.cats || x.note || '')}</td></tr>`).join('');
  box.innerHTML = `${r.pii ? '<div class="bar bad"><b>⚠ 该方案疑似包含手机号/邮箱</b> —— 请提醒贡献者先脱敏</div>' : ''}
    <div class="tip">${esc(r.message)}</div>
    ${r.add.length ? `<div class="tip"><b>将新增（${r.add.length}）：</b></div>
      <table><tbody>${r.add.map(x => `<tr><td style="width:30%"><b>${esc(x.cat)}</b>${x.focus ? ' (专注)' : ''}</td>
        <td>${esc(x.rule)}</td></tr>`).join('')}</tbody></table>` : ''}
    ${r.conflict.length ? `<div class="tip" style="margin-top:8px"><b>冲突跳过（${r.conflict.length}）：</b></div>
      <table><tbody>${rows(r.conflict)}</tbody></table>` : ''}
    ${r.bad.length ? `<div class="tip" style="margin-top:8px"><b>无法解析（${r.bad.length}）：</b>${esc(r.bad.join('；'))}</div>` : ''}`;
}

/* ---------- 关于 ---------- */
async function loadAbout(){
  const box = document.getElementById('aboutBox');
  if (!SERVER){ box.innerHTML = '<div class="tip">关于页需要服务模式。</div>'; return; }
  box.innerHTML = '<div class="tip">读取中…</div>';
  try {
    const r = await fetch('/api/about').then(x => x.json());
    box.innerHTML = `<table><tbody>
      <tr><td style="width:34%"><b>版本</b></td><td>v${esc(r.version)} · 作者 ${esc(r.author)}</td></tr>
      <tr><td><b>数据目录</b></td><td style="word-break:break-all">${esc(r.data_dir)}</td></tr>
      <tr><td><b>面板地址</b></td><td>http://127.0.0.1:${r.port}/（本机）</td></tr>
      <tr><td><b>已记录</b></td><td>${r.days} 天</td></tr>
      <tr><td><b>项目地址</b></td>
        <td><a href="https://github.com/mniLiHua/focuslog" target="_blank" style="color:var(--accent)">github.com/mniLiHua/focuslog</a>
          <div class="tip">遇到问题：先点「一键体检」，把 ❌ 项截图，到仓库 Issues 里发帖，附上截图与系统版本即可</div></td></tr>
      <tr><td><b>常见问题</b></td><td class="tip" style="line-height:1.9">
        · 数据不联网，全部在数据目录的纯文本里<br>
        · 记录停了？顶部状态条或体检里一键开始<br>
        · 想换电脑？工具 → 导入旧数据<br>
        · 想改分类？工具 → 分类助手 / 分类管理<br>
        · 出问题先点「一键体检」，按 ❌ 提示处理</td></tr>
    </tbody></table>`;
  } catch(e){ box.innerHTML = '<div class="tip">读取失败：' + e + '</div>'; }
}

/* ---------- 番茄钟（简版：页面内计时，完成落盘） ---------- */
let POMO = {end: 0, timer: null, minutes: 0};
function pomoTick(){
  const left = Math.round((POMO.end - Date.now()) / 1000);
  const el = document.getElementById('pomoClock');
  if (left <= 0){
    clearInterval(POMO.timer); POMO.timer = null;
    el.textContent = '完成 ✓';
    finishPomo(POMO.minutes);
    return;
  }
  el.textContent = `剩余 ${String(Math.floor(left/60)).padStart(2,'0')}:${String(left%60).padStart(2,'0')}`;
}
async function finishPomo(minutes){
  if (!minutes) return;
  const now = new Date().toTimeString().slice(0,5);
  try {
    const r = await api('/api/session', {minutes: minutes, end: now});
    out.textContent = (r.message || '一段专注已记录') +
      '　（sessions.json，仅作番茄统计，不影响自动记录）';
  } catch(e){ out.textContent = '记录失败：' + e; }
  loadPomoToday();
}
function startPomo(min){
  POMO.minutes = min;
  POMO.end = Date.now() + min*60000;
  if (POMO.timer) clearInterval(POMO.timer);
  POMO.timer = setInterval(pomoTick, 500);
  pomoTick();
  out.textContent = `开始一段 ${min} 分钟专注，页面别关就行（关了计时会丢，但自动记录不受影响）`;
}
document.getElementById('pomo25').onclick = () => startPomo(25);
document.getElementById('pomo50').onclick = () => startPomo(50);
document.getElementById('pomoStop').onclick = () => {
  if (POMO.timer){ clearInterval(POMO.timer); POMO.timer = null; }
  POMO.minutes = 0;
  document.getElementById('pomoClock').textContent = '已停止';
};
function applyPomo(){
  const show = localStorage.getItem('showPomo') === '1';
  const card = document.getElementById('pomoCard');
  if (card) card.style.display = show ? '' : 'none';
  if (show) loadPomoToday();
}

async function loadPomoToday(){
  const box = document.getElementById('pomoToday');
  if (!SERVER){ box.textContent = '番茄钟需要服务模式（用「打开面板.bat」）。'; return; }
  try {
    const r = await fetch('/api/session').then(x => x.json());
    box.textContent = `今天已完成 ${r.today_count || 0} 段 · 合计 ${r.today_minutes || 0} 分钟`;
  } catch(e){ box.textContent = ''; }
}

/* ---------- 周报 / CSV / 目标按钮 ---------- */
document.getElementById('btnWeekly').onclick = async (e) => {
  e.target.disabled = true; out.textContent = '生成中…';
  try {
    const r = await api('/api/weekly', {this_week: false});
    out.textContent = (r.message || '') + (r.body ? '\n\n' + r.body.slice(0, 1400) : '');
  } catch(err){ out.textContent = '出错：' + err; }
  finally { e.target.disabled = false; }
};
document.getElementById('btnCsv').onclick = async (e) => {
  e.target.disabled = true; out.textContent = '导出中…';
  try {
    const r = await api('/api/export_csv', {range: 'all'});
    out.textContent = r.message || '';
    /* 产物已由服务端生成并弹出资源管理器，不再走浏览器下载栏 */
  } catch(err){ out.textContent = '出错：' + err; }
  finally { e.target.disabled = false; }
};
// 不用原生 prompt（体验差、也没法自动测）：直接跳到「系统设置」里的目标输入框
document.getElementById('btnGoal').onclick = async () => {
  document.querySelector('.tab[data-tab=sys]').click();
  await loadSys();
  const el = document.getElementById('sysGoal');
  if (el){ el.focus(); el.select(); }
  out.textContent = '在下面「系统设置 → 每日专注目标」里改，回车或点保存即可。';
};

async function _legacyGoalPrompt(){          // 保留旧分支不删，避免其它地方还引用
  const cur = (GOAL && GOAL.daily_focus_min) || 360;
  const v = String(cur);
  if (v === null) return;
  const n = parseInt(v, 10);
  if (!n || n <= 0){ out.textContent = '目标要填正整数分钟'; return; }
  try {
    const r = await api('/api/goals', {daily_focus_min: n, notify: true});
    out.textContent = r.message || '';
    await loadGoal();
  } catch(err){ out.textContent = '出错：' + err; }
};

/* ---------- 首次运行向导：没有任何日志时给三步引导（老用户无感） ---------- */
function drawOnboard(){
  const el = document.getElementById('onboard');
  const empty = SERVER && D && D.days && D.days.length === 0;
  el.style.display = empty ? '' : 'none';
  if (!empty) return;
  el.innerHTML = `<div class="bar bad" style="flex-direction:column;align-items:stretch;gap:10px;padding:16px">
      <b style="font-size:15px">看起来你还没开始记录 —— 三步搞定：</b>
      <span>① <b>开始记录</b>（点一下就行，以后可开机自动开始）
           <button class="btn primary" id="obStart" style="margin-left:8px">开始记录</button></span>
      <span>② 有旧电脑/旧版本？<b>把历史记录搬过来</b>（自动搜索，只复制不覆盖）
           <button class="btn" id="obImport" style="margin-left:8px">导入旧数据</button></span>
      <span>③ 定一个<b>每日专注目标</b>，面板会显示进度条
           <button class="btn" id="obGoal" style="margin-left:8px">设目标</button></span>
      <span class="tip" style="margin:0">做完这几步这条提示会自己消失。就随便看看也行，不影响使用。</span>
    </div>`;
  document.getElementById('obStart').onclick = async () => {
    out.textContent = '正在启动…';
    const r = await api('/api/tracker', {action: 'start'});
    out.textContent = r.message || '';
    await loadTracker();
  };
  document.getElementById('obImport').onclick = () => {
    document.getElementById('toolBtn').click();
    document.querySelector('.tab[data-tab=import]').click();
  };
  document.getElementById('obGoal').onclick = () => {
    document.querySelector('.tab[data-tab=sys]').click();
    loadSys().then(() => {
      const g = document.getElementById('sysGoal');
      if (g){ g.focus(); g.select(); }
    });
  };
}

/* ---------- 顶部状态条：记录开没开（不用找 bat，点一下就切） ---------- */
let TRACK = true;
async function loadTracker(){
  const el = document.getElementById('trackBar');
  if (!SERVER){ el.innerHTML = ''; return; }
  try {
    const r = await fetch('/api/tracker').then(x => x.json());
    TRACK = !!r.running;
    const dupBar = (r.dupes && r.dupes.length)
      ? `<div class="bar bad" style="margin:-6px 0 14px"><b>检测到 ${r.dupes.length} 个旧版记录程序并行</b>
           —— 已通知记录进程自动关闭并禁用其自启（详见 focuslogs 日志的 [DUP] 行）</div>`
      : '';
    el.innerHTML = (TRACK
      ? `<div class="bar ok"><b>记录中</b> —— 后台正在记录你的窗口切换
           <button class="btn" id="tbStop" style="margin-left:auto">停止记录</button></div>`
      : `<div class="bar bad"><b>记录已停止</b> —— 现在不会记录任何数据
           <button class="btn primary" id="tbStart" style="margin-left:auto">开始记录</button></div>`)
      + dupBar;
    const s = document.getElementById('tbStart'), t2 = document.getElementById('tbStop');
    if (s) s.onclick = async () => {
      out.textContent = '正在启动…';
      const r2 = await api('/api/tracker', {action: 'start'});
      out.textContent = r2.message || '';
      await loadTracker();
    };
    if (t2) t2.onclick = async () => {
      if (!confirm('确定停止记录吗？停止期间不会产生任何数据（已记录的都在）。')) return;
      const r2 = await api('/api/tracker', {action: 'stop'});
      out.textContent = r2.message || '';
      await loadTracker();
    };
  } catch(e){ el.innerHTML = ''; }
}

/* ---------- 系统设置：开机自启 / 提醒开关 / 目标 ---------- */
let SYS = {autostart: false, notify: true, goal: 360};

async function loadSys(){
  const box = document.getElementById('sysBox');
  if (!SERVER){ box.innerHTML = '<div class="tip">系统设置需要服务模式（用「打开面板.bat」）。</div>'; return; }
  box.innerHTML = '<div class="tip">读取中…</div>';
  try {
    const a = await fetch('/api/autostart').then(x => x.json());
    const g = await fetch('/api/goals').then(x => x.json());
    const bk = await fetch('/api/backup').then(x => x.json()).catch(() => ({items: []}));
    const tk = await fetch('/api/tracker').then(x => x.json()).catch(() => ({}));
    const pa = await fetch('/api/panel_autostart').then(x => x.json()).catch(() => ({}));
    SYS.panelAuto = !!pa.enabled;
    SYS.panelMsg = pa.message || '';
    SYS.autostart = !!a.enabled;
    SYS.notify = !(g.goals && g.goals.notify === false);
    SYS.goal = (g.goals && g.goals.daily_focus_min) || 360;
    renderSys(a, bk, tk, pa);
    const bc = await fetch('/api/backup_config').then(x => x.json()).catch(() => ({}));
    SYS.bak = bc.backup || {};
    SYS.bakDir = SYS.bak.dir || bc.default_dir || '';
    document.getElementById('sysPost').insertAdjacentHTML('beforebegin', `
      <tr><td><b>每周自动备份</b><div class="tip" style="margin:2px 0 0">
          记录进程跨天时检查，到期自动打包到指定目录（保留最近 10 份）</div></td>
        <td><input type="text" id="bakDir" value="${esc(SYS.bakDir)}" style="width:280px">
          <input type="text" id="bakDays" value="${SYS.bak.interval_days || 7}" style="width:44px"> 天
          <button class="btn ${SYS.bak.enabled ? 'primary' : ''}" id="swBakSave">
            ${SYS.bak.enabled ? '已开启 · 保存修改' : '已关闭 · 保存并开启'}</button>
          <button class="btn" id="swBakOff">关闭</button></td></tr>`);
    document.getElementById('swBakSave').onclick = async () => {
      const r = await api('/api/backup_config', {enabled: true,
        dir: document.getElementById('bakDir').value,
        interval_days: parseInt(document.getElementById('bakDays').value, 10) || 7});
      out.textContent = r.message || '';
      await loadSys();
    };
    document.getElementById('swBakOff').onclick = async () => {
      const r = await api('/api/backup_config', {enabled: false,
        dir: document.getElementById('bakDir').value});
      out.textContent = r.message || '';
      await loadSys();
    };
  } catch(e){ box.innerHTML = '<div class="tip">读取失败：' + e + '</div>'; }
}

function renderSys(a, bk, tk, pa){
  const box = document.getElementById('sysBox');
  bk = bk || {items: []};
  tk = tk || {};
  const bkItems = (bk.items || []).map(x =>
      `<div class="tip" style="margin:2px 0 0">${esc(x.name)} · ${(x.size/1048576).toFixed(1)} MB</div>`).join('');
  SYS.tracking = !!tk.running;
  box.innerHTML = `<table><tbody>
    <tr><td style="width:34%"><b>记录开关</b><div class="tip" style="margin:2px 0 0">
        关掉就不再记录（数据不会删，随时再开）</div></td>
      <td><button class="btn ${SYS.tracking ? 'primary' : ''}" id="swTrack">
        ${SYS.tracking ? '记录中 · 点此停止' : '已停止 · 点此开始'}</button></td></tr>
    <tr><td><b>打开文件夹</b><div class="tip" style="margin:2px 0 0">
        想看原始日志、顺手复制一份给别人</div></td>
      <td><button class="btn" id="swFolder">打卡片数据目录</button>
        <button class="btn" id="swRoot">打开项目根目录</button></td></tr>
    <tr><td><b>备份数据</b><div class="tip" style="margin:2px 0 0">
        把日志/深耕/统计/分类打成一个 zip（不含 exe）</div></td>
      <td><button class="btn primary" id="swBackup">立即备份</button>
        <span class="tip" id="swBackupTip" style="margin-left:6px"></span>
        ${bkItems || '<div class="tip" style="margin:6px 0 0">还没有备份</div>'}</td></tr>
    <tr><td><b>面板服务常驻</b><div class="tip" style="margin:2px 0 0">
        开机后后台挂一个不弹窗的看板服务，浏览器随时打开
        127.0.0.1:47823 就能看（内存约 40-80MB，CPU 空闲时不占）</div></td>
      <td>${SYS.panelMsg && !pa.enabled
            ? `<div class="tip">${esc(SYS.panelMsg)}</div>`
            : `<button class="btn ${SYS.panelAuto ? 'primary' : ''}" id="swPanel">
                 ${SYS.panelAuto ? '已常驻 · 点此关闭' : '已关闭 · 点此开启'}</button>`}</td></tr>
    <tr><td><b>开机自动记录</b><div class="tip" style="margin:2px 0 0">
        开机后自动开始记（不需要手动点启动）</div></td>
      <td><button class="btn ${SYS.autostart ? 'primary' : ''}" id="swAuto">
        ${SYS.autostart ? '已开启 · 点此关闭' : '已关闭 · 点此开启'}</button>
        <div class="tip" style="margin:6px 0 0">当前指向：${esc((a && a.value) || (a && a.target) || '')}</div></td></tr>
    <tr><td><b>弹窗提醒</b><div class="tip" style="margin:2px 0 0">
        达标 / 某程序用时超上限时弹窗（每项每天一次）</div></td>
      <td><button class="btn ${SYS.notify ? 'primary' : ''}" id="swNotify">
        ${SYS.notify ? '已开启 · 点此关闭' : '已关闭 · 点此开启'}</button></td></tr>
    <tr><td><b>番茄钟卡片</b><div class="tip" style="margin:2px 0 0">
        默认隐藏；打开后面板底部会出现一张独立的番茄钟卡片</div></td>
      <td><button class="btn ${localStorage.getItem('showPomo') === '1' ? 'primary' : ''}" id="swPomo">
        ${localStorage.getItem('showPomo') === '1' ? '已显示 · 点此隐藏' : '已隐藏 · 点此显示'}</button></td></tr>
    <tr><td><b>每日专注目标</b><div class="tip" style="margin:2px 0 0">面板上那个进度条的口径</div></td>
      <td><input type="text" id="sysGoal" value="${SYS.goal}" style="width:80px"> 分钟
        <button class="btn" id="swGoalSave">保存</button></td></tr>
  </tbody></table>`;
  document.getElementById('swTrack').onclick = async () => {
    const r = await api('/api/tracker', {action: SYS.tracking ? 'stop' : 'start'});
    out.textContent = r.message || '';
    await loadSys(); await loadTracker();
  };
  document.getElementById('swFolder').onclick = async () => {
    const r = await api('/api/open_folder', {which: 'logs'});
    out.textContent = r.message || '';
  };
  document.getElementById('swRoot').onclick = async () => {
    const r = await api('/api/open_folder', {which: 'root'});
    out.textContent = r.message || '';
  };
  document.getElementById('swBackup').onclick = async (ev) => {
    ev.target.disabled = true;
    document.getElementById('swBackupTip').textContent = '打包中…（数据多的时候要几秒）';
    try {
      const r = await api('/api/backup', {go: 1});
      document.getElementById('swBackupTip').innerHTML = r.ok
        ? `${esc(r.message)}　<a href="${r.url}" style="color:var(--accent)">下载</a>`
        : esc(r.message || '备份失败');
    } finally { ev.target.disabled = false; }
  };
  document.getElementById('swPomo').onclick = () => {
    localStorage.setItem('showPomo',
      localStorage.getItem('showPomo') === '1' ? '0' : '1');
    applyPomo();
    loadSys();
  };
  const swPanel = document.getElementById('swPanel');
  if (swPanel) swPanel.onclick = async () => {
    const r = await api('/api/panel_autostart', {enable: !SYS.panelAuto});
    out.textContent = r.message || '';
    await loadSys();
  };
  document.getElementById('swAuto').onclick = async () => {
    const r = await api('/api/autostart', {enable: !SYS.autostart});
    out.textContent = r.message || '';
    await loadSys();
  };
  document.getElementById('swNotify').onclick = async () => {
    const r = await api('/api/goals', {notify: !SYS.notify});
    out.textContent = (r.message || '') + '（提醒开关已更新）';
    await loadSys(); await loadGoal();
  };
  const goalInput = document.getElementById('sysGoal');
  if (goalInput){
    goalInput.onkeydown = (ev) => {
      if (ev.key === 'Enter') document.getElementById('swGoalSave').click();
    };
  }
  document.getElementById('swGoalSave').onclick = async () => {
    const n = parseInt(document.getElementById('sysGoal').value, 10);
    if (!n || n <= 0){ out.textContent = '目标要填正整数分钟'; return; }
    const r = await api('/api/goals', {daily_focus_min: n});
    out.textContent = r.message || '';
    await loadSys(); await loadGoal();
  };
}

document.getElementById('btnMaintain').onclick = async (e) => {
  e.target.disabled = true;
  out.textContent = '执行中：① 归档 → ② 重算深耕 → ③ 导出统计（数据多时十几秒，别关页面）…';
  try {
    const r = await api('/api/maintain');
    out.textContent = r.message || '';
    await silentRefresh();
  } finally { e.target.disabled = false; }
};

/* 自动刷新：数据每 60 秒拉一次，页面不用手动点「刷新」 */
async function silentRefresh(){
  if (!SERVER) return;
  try {
    const r = await fetch('/api/state').then(x => x.json());
    if (r && r.ok){ D = r.data; topCache = {}; buildColors(); drawHead(); draw(); }
  } catch(e){ /* 服务没起来就静静跳过 */ }
}
if (SERVER) setInterval(silentRefresh, 60000);

/* 快捷键：T 主题；/ 搜索 */
document.addEventListener('keydown', (e) => {
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
  if (e.key === 't' || e.key === 'T') document.getElementById('theme').click();
  if (e.key === '/'){
    e.preventDefault();
    if (document.getElementById('tools').style.display === 'none') document.getElementById('toolBtn').click();
    document.getElementById('q').focus();
  }
});

/* ⑥ 数据体检 */
document.getElementById('btnCheckup').onclick = async (e) => {
  e.target.disabled = true; out.textContent = '体检中…';
  try {
    const r = await api('/api/checkup');
    out.textContent = (r.items || []).map(x => (x.ok ? '\u2705 ' : '\u274c ') + x.name + (x.note ? ' —— ' + x.note : '')).join('\n')
      + '\n' + (r.ok ? '全部正常' : '有异常项，按提示处理');
  } finally { e.target.disabled = false; }
};

// 打开工具面板时自动把分类助手列表拉出来（原来一直停在"正在加载…"，要手点刷新）
document.getElementById('toolBtn').addEventListener('click', () => {
  if (!ALLCAT) loadCats();
});


drawHead();
draw();
drawOnboard();
loadGoal();
applyPomo();
loadTracker();
</script>
</body>
</html>
"""


def build_html(data, server_mode=False):
    """生成完整 HTML（数据内联；服务模式由 panel_server 动态调用）

    服务模式下窗口排行走 /api/windows（TOP 200 按需取），所以把内联的 win 字段
    去掉 —— 不然单文件会白白胖几百 KB（服务模式并不用它）。
    """
    if server_mode:
        # 深拷贝后再删：浅拷贝会连带改掉调用方（collect() 结果）里的嵌套字典
        import copy as _copy
        data = _copy.deepcopy(data)
        data["days"] = [{k: v for k, v in d.items() if k != "w"} for d in data.get("days", [])]
        for key in ("weeks", "months"):
            data[key] = [{k: v for k, v in x.items() if k != "win"} for x in data.get(key, [])]
        for _k, rr in (data.get("ranges") or {}).items():
            rr.pop("win", None)
    return (HTML
            .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
            .replace("__SERVER__", "true" if server_mode else "false"))


def build(out_path=OUT_FILE, do_open=False):
    data = collect()
    html = build_html(data, server_mode=False)
    with io.open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    size = os.path.getsize(out_path)
    print(f"[OK] 面板已生成: {out_path}")
    print(f"     体积 {size/1024:.0f} KB | 覆盖 {data['range'][0]} ~ {data['range'][1]} 共 {len(data['days'])} 天")
    if do_open:
        webbrowser.open("file:///" + out_path.replace("\\", "/"))
    return out_path


if __name__ == "__main__":
    build(do_open=("--open" in sys.argv))
