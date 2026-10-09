# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
面板本地服务  v2.9 新增
把「看板 + 工具」合成一个界面，不再需要一个个 bat 点：

    python panel_server.py          启动服务（默认 127.0.0.1:8765）并打开浏览器
    python panel_server.py --port 9000

设计：
  · 只用标准库 http.server（零第三方依赖、离线可用）
  · 只绑定 127.0.0.1（不对局域网开放）
  · 工具动作 = 白名单函数调用（归档 / 重算 / 导出 / 扫未分类 / 写分类规则），
    不执行任何用户传入的命令
  · 业务逻辑全部复用 summary.py 的既有实现，服务层只做"调度 + 收集输出"
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from urllib.parse import urlparse, parse_qs

# exe 下 __file__ 是临时解包目录，必须用 exe 自身位置
if getattr(sys, "frozen", False):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.normpath(os.path.join(SCRIPT_DIR, "..", "焦点监控")))

import dashboard as DASH  # noqa: E402
import summary as S       # noqa: E402
import category_utils as CU
import backup_utils as BU  # noqa: E402

# 固定冷门端口：8765 太容易撞（很多本地工具用它），改到高位不常见端口；
# 仍会在 47823~47832 内顺延找空位（见 main）。
DEFAULT_PORT = 47823
AUTHOR = "冰叁狼"          # 作者署名：启动横幅 / README / CHANGELOG / 面板页脚
try:
    LOCAL_VERSION = io.open(os.path.join(os.path.dirname(SCRIPT_DIR),
                                         "VERSION.txt"), encoding="utf-8").read().strip()
except OSError:
    LOCAL_VERSION = "dev"

# 监控进程与备份目录（供网页里的"开始/停止记录""备份数据"用）
TRACKER_EXE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "焦点监控", "focus_tracker.exe"))
BACKUP_DIR = "_备份"
_STATE_CACHE = {"t": 0.0, "data": None}   # /api/state 的短缓存（见 _state）
_COLLECT_CACHE = {"key": None, "data": None}   # collect() 结果，按"日志文件集合版本"缓存
_WIN_CACHE = {"ver": None, "data": {}}         # 窗口排行结果：ver=日志版本，data[key|agg|min]=rows


_WIN_REBUILDING = {"keys": set(), "t": 0.0}


def _windows_rebuild(ver, cks):
    """后台重算（stale-while-revalidate 的 revalidate 半边，带节流）"""
    for ck in cks:
        key, agg_s, min_s = ck.split("|")
        try:
            r = windows_payload(key, agg=(agg_s != "0"), min_sec=int(min_s))
            if _WIN_CACHE["ver"] != ver:                     # 期间数据又变了就放弃
                return
            _WIN_CACHE["data"][ck] = r
        except Exception:                                    # noqa: BLE001
            continue


def _windows_cached(key, agg, min_sec):
    """排行结果缓存：数据版本没变 → 瞬时返回；
    版本变了 → 先给上一版结果（标记 stale，前端 1.5s 后静默刷新），
    后台重算。用户从此感知不到重算等待。"""
    ver = _collect_key()
    if ver != _WIN_CACHE["ver"]:
        if _WIN_CACHE["data"]:
            # 有旧版数据：把全部旧 key 排进后台重算队列（带节流，30s 一轮）
            now = time.time()
            olds = list(_WIN_CACHE["data"].keys())
            if now - _WIN_REBUILDING["t"] > 30:
                _WIN_REBUILDING["t"] = now
                threading.Thread(target=_windows_rebuild,
                                 args=(ver, olds), daemon=True).start()
            ck = f"{key}|{int(agg)}|{min_sec}"
            if ck in _WIN_CACHE["data"]:
                r = dict(_WIN_CACHE["data"][ck])
                r["stale"] = True
                return r
        _WIN_CACHE["ver"], _WIN_CACHE["data"] = ver, {}
    ck = f"{key}|{agg}|{min_sec}"
    if ck in _WIN_CACHE["data"]:
        return _WIN_CACHE["data"][ck]
    r = windows_payload(key, agg=agg, min_sec=min_sec)
    _WIN_CACHE["data"][ck] = r
    return r


def _windows_warmup():
    """启动预热：把用户最常用的排行组合算好（打开网页必命中）"""
    try:
        for key in ("d7", "d30", "all", "today"):
            for agg, mn in ((True, 900), (False, 900)):
                _windows_cached(key, agg, mn)
    except Exception:                                        # noqa: BLE001
        pass


def _collect_key():
    """所有日志文件的 (路径, 大小, mtime) 摘要 —— 任何一个文件变了 key 就变

    这才是真正该做的缓存：collect() 全量计算约 2.4 秒（55 天 × 几千行），
    而日志只有"当天那份"每分钟在追加。key 不变 ⇒ 直接给上一次结果。
    """
    items = []
    try:
        for d in S._find_all_log_dates():
            fp = S._find_log_file(d)
            if fp:
                try:
                    st = os.stat(fp)
                    items.append((fp, st.st_size, int(st.st_mtime)))
                except OSError:
                    pass
    except Exception:                                        # noqa: BLE001
        return None
    return hash(tuple(items))


_COLLECT_LOCK = __import__("threading").Lock()
_HTML_CACHE = {"key": None, "html": None}     # 渲染好的整页 HTML，随数据版本失效


def _collect_cached():
    """预热线程与首个请求可能同时到达 ⇒ 无锁会各算一遍（2 倍 CPU，用户看到"没秒开"）。

    锁内二次检查：谁先拿到锁谁算，另一个直接命中。"""
    k = _collect_key()
    with _COLLECT_LOCK:
        if k is not None and _COLLECT_CACHE["data"] is not None and _COLLECT_CACHE["key"] == k:
            return _COLLECT_CACHE["data"]
        data = DASH.collect()
        _COLLECT_CACHE["key"], _COLLECT_CACHE["data"] = k, data
        return data

# 控制台可能是 GBK：打不出的字符直接替换，别让启动横幅把服务带崩
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")


def _capture(fn, *a, **kw):
    """执行函数并把它打印的内容一起收回来（给面板显示）"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            result = fn(*a, **kw)
        return {"ok": True, "message": buf.getvalue().strip() or "完成", "result": result}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "message": f"失败：{type(e).__name__}: {e}\n{buf.getvalue()}"}


# ---------- 分类数据 / 编辑 ----------
def categories_payload():
    """分类维度 + 进程维度的现状，供面板的「分类助手 / 分类管理」用"""
    meta, dup = CU.parse_category_file(DASH.CAT_FILE)
    # 一次 collect 同时取 cats 与 procCat，别调两遍（每遍都是全历史扫描）
    snap = _collect_cached()
    sec_of = dict(snap.get("ranges", {}).get("all", {}).get("cats", []))

    cats = [{"name": n, "focus": bool(m.get("focus")),
             "rules": [[tp, kw] for tp, kw in m["rules"]],
             "sec": int(sec_of.get(n, 0))} for n, m in meta.items()]

    # 进程维度：用时 / 天数 / 当前归属
    # ⚠️ 归属必须用「实际分类」= classify_window(proc, 典型标题)：
    #    只反查 proc 规则会把靠 title 命中的进程（如 CodeBuddy CN.exe → title=CodeBuddy）
    #    误报成未分类。口径直接复用 dashboard.collect() 的 procCat，全站一致。
    owner = snap.get("procCat", {})
    procs = []
    got = _capture(S._rank_stats, "all")
    if got.get("ok"):
        _title, rows = got["result"]            # (标题, [(进程, 秒, 天数)])
        for proc, sec, days in rows:
            procs.append({"proc": proc, "sec": int(sec), "days": days,
                          "cat": owner.get(proc, "")})
    return {"ok": True, "cats": cats, "procs": procs, "dup": dup}

def cat_edit_payload(payload):
    """分类调整：移动进程 / 切专注 / 重命名 / 增删规则 / 删分类

    全部走 parse→改→dump 一条路，dump 内部会先备份 .bak 再原子替换。
    """
    action = (payload.get("action") or "").strip()
    meta, _dup = CU.parse_category_file(DASH.CAT_FILE)
    if not meta:
        return {"ok": False, "message": "category.txt 读不到（或没有规则）"}

    if action == "move_rule":
        proc = (payload.get("proc") or "").strip()
        to = (payload.get("to") or "").strip()
        if not proc or not to:
            return {"ok": False, "message": "缺少 proc / to"}
        for name in list(meta.keys()):
            meta[name]["rules"] = [(tp, kw) for tp, kw in meta[name]["rules"]
                                   if not (tp == "proc" and kw.lower() == proc.lower())]
        if to not in meta:
            meta[to] = {"rules": [], "focus": bool(payload.get("focus"))}
        if ("proc", proc) not in meta[to]["rules"]:
            meta[to]["rules"].append(("proc", proc))
        msg = f"{proc} → [{to}]"

    elif action == "set_focus":
        cat = payload.get("cat")
        if cat not in meta:
            return {"ok": False, "message": "分类不存在"}
        meta[cat]["focus"] = bool(payload.get("focus"))
        msg = f"[{cat}] 专注 = {'是' if meta[cat]['focus'] else '否'}"

    elif action == "rename_cat":
        old = payload.get("cat")
        new = (payload.get("new") or "").strip()
        if old not in meta or not new:
            return {"ok": False, "message": "参数不完整"}
        if new in meta and new != old:
            return {"ok": False, "message": f"[{new}] 已存在"}
        meta = {(new if k == old else k): v for k, v in meta.items()}
        msg = f"[{old}] → [{new}]"

    elif action in ("del_rule", "add_rule"):
        cat = payload.get("cat")
        rtype = (payload.get("type") or "proc").strip().lower()
        kw = (payload.get("keyword") or "").strip()
        if cat not in meta or not kw:
            return {"ok": False, "message": "参数不完整"}
        if rtype not in ("proc", "title"):
            return {"ok": False, "message": "type 只能是 proc 或 title"}
        if action == "del_rule":
            meta[cat]["rules"] = [(tp, k) for tp, k in meta[cat]["rules"]
                                  if not (tp == rtype and k.lower() == kw.lower())]
            msg = f"[{cat}] 删除 {rtype}={kw}"
        else:
            if (rtype, kw) not in meta[cat]["rules"]:
                meta[cat]["rules"].append((rtype, kw))
            msg = f"[{cat}] 添加 {rtype}={kw}"

    elif action == "del_cat":
        cat = payload.get("cat")
        if cat not in meta:
            return {"ok": False, "message": "分类不存在"}
        meta.pop(cat)
        msg = f"已删除分类 [{cat}]"

    else:
        return {"ok": False, "message": f"未知动作 {action}"}

    CU.dump_categories(DASH.CAT_FILE, meta)
    return {"ok": True, "message": msg + "（已写回 category.txt，旧文件备份为 .bak）"}


def windows_payload(key="today", limit=200, agg=1, min_sec=900):
    """某个范围的排行，供面板 TOP 列表分页用

    key: today / d7 / d30 / all / day:YYYY-MM-DD / month:YYYY-MM / week:YYYY-Www
    agg: 1=按进程聚合（默认，一行一个程序，附"用得最多的窗口标题"）
         0=按 proc|标题 明细（同一浏览器换标签就会分行，容易被切碎）
    """
    today = date.today()
    if key == "today":
        dates = [today.strftime("%Y-%m-%d")]
    elif key == "d7":
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    elif key == "d30":
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    elif key == "all":
        dates = S._find_all_log_dates()
    elif key.startswith("day:"):
        dates = [key[4:]]
    elif key.startswith("month:"):
        ym = key[6:]
        dates = [d for d in S._find_all_log_dates() if d.startswith(ym)]
    elif key.startswith("week:"):
        # YYYY-Www → 该 ISO 周的一整周
        try:
            y, w = key[5:].split("-W")
            monday = date.fromisocalendar(int(y), int(w), 1)
            dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        except Exception:                                    # noqa: BLE001
            dates = []
    else:
        dates = [key]

    wins = {}
    procs = {}          # proc -> [总秒, {标题: 秒}]
    for d in dates:
        for _ts, proc, title, dur in S.parse_log(d):
            if dur > 0:
                k = f"{proc} | {title}"
                wins[k] = wins.get(k, 0) + dur
                p = procs.setdefault(proc, [0, {}])
                p[0] += dur
                p[1][title] = p[1].get(title, 0) + dur

    # 上榜门槛：低于 min_sec 的不显示（默认 900 秒 = 15 分钟，避免刷屏的"1 分钟"条目）
    procs = {k: v for k, v in procs.items() if v[0] >= min_sec} if min_sec else procs
    wins = {k: v for k, v in wins.items() if v >= min_sec} if min_sec else wins

    if agg:
        items = []
        for proc, (sec, titles) in sorted(procs.items(), key=lambda x: -x[1][0])[:limit]:
            t0 = max(titles.items(), key=lambda x: x[1]) if titles else ("", 0)
            items.append([proc, int(sec), t0[0], int(t0[1])])
        return {"ok": True, "items": items, "total": len(procs), "agg": 1,
                "min_sec": min_sec}

    rows = [[k, int(v)] for k, v in sorted(wins.items(), key=lambda x: -x[1])[:limit]]
    return {"ok": True, "items": rows, "total": len(wins), "agg": 0, "min_sec": min_sec}


def _migrate_mod():
    """动态加载 工具/migrate.py（不复用 sys.path，避免污染）"""
    import importlib.util
    p = os.path.join(os.path.dirname(SCRIPT_DIR), "工具", "migrate.py")
    spec = importlib.util.spec_from_file_location("fl_migrate", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def migrate_scan(payload):
    """扫描旧项目：给了 path 就看那一个，否则自动搜索常见位置"""
    MG = _migrate_mod()
    p = (payload.get("path") or "").strip().strip('"')
    if p:
        prog, data = MG.find_old(p)
        if not data:
            return {"ok": False, "message": f"这个位置没找到旧数据：{p}"}
        return {"ok": True, "items": [MG.scan_candidate(prog, data)]}
    return {"ok": True, "items": MG.find_candidates()}


def migrate_run(payload):
    """执行导入（只读旧目录、同名不覆盖、先整包备份）"""
    MG = _migrate_mod()
    p = (payload.get("path") or "").strip().strip('"')
    prog, data = MG.find_old(p) if p else (None, None)
    if not data:
        return {"ok": False, "message": "没定位到旧数据目录，请手动填路径后重试"}
    rep = MG.migrate_api(prog, data)
    h, m = divmod(rep["total_sec"] // 60, 60)
    return {"ok": True, "report": rep,
            "message": (f"已导入 {rep['copied']} 个文件（已存在跳过 {rep['skipped']}）\n"
                        f"现在共 {rep['days']} 天日志，累计在线 {h} 小时 {m} 分钟\n"
                        + (rep["cat_msg"] + "\n" if rep["cat_msg"] else "")
                        + f"备份：{rep['backup']}\n" + "；".join(rep["messages"]))}


def goals_payload():
    """读 goals.json（面板设置用）"""
    p = os.path.join(SCRIPT_DIR, "goals.json")
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return {"ok": True, "goals": json.load(f)}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}
    return {"ok": True, "goals": {"daily_focus_min": 360, "app_limits_min": {}, "notify": True}}


def set_goals(payload):
    """写 goals.json（只接受白名单字段）"""
    p = os.path.join(SCRIPT_DIR, "goals.json")
    cur = goals_payload().get("goals") or {}
    for k in ("daily_focus_min", "app_limits_min", "notify"):
        if k in payload:
            cur[k] = payload[k]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    return {"ok": True, "goals": cur,
            "message": f"目标已保存：每日专注 {cur.get('daily_focus_min')} 分钟"
                       f"（守护程序一分钟内生效）"}


def export_csv(payload):
    """导出某个范围的分日 / 分类 / 应用 CSV，返回下载链接"""
    key = (payload.get("range") or "all").strip()
    data = DASH.collect()
    out_dir = os.path.join(SCRIPT_DIR, "导出")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    name = f"focuslog_{key.replace(':', '-').replace('/', '-')}_{stamp}.csv"
    path = os.path.join(out_dir, name)

    rows = [["日期", "专注秒", "在线秒"]]
    if key == "all" or key == "d30" or key == "d7":
        n = {"d7": 7, "d30": 30}.get(key, len(data["days"]))
        for d in data["days"][-n:]:
            rows.append([d["d"], d["f"], d["t"]])
    else:
        for d in data["days"]:
            rows.append([d["d"], d["f"], d["t"]])
    rows.append([])
    rows.append(["分类", "秒"])
    for c in (data["ranges"].get("all", {}).get("cats") or []):
        rows.append([c[0], c[1]])
    rows.append([])
    rows.append(["应用", "秒"])
    for a in (data["ranges"].get("all", {}).get("apps") or []):
        rows.append([a[0], a[1]])

    import csv as _csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        _csv.writer(f).writerows(rows)
    rel = os.path.relpath(path, SCRIPT_DIR).replace("\\", "/")
    _reveal(os.path.join(SCRIPT_DIR, rel.replace("/", os.sep)))
    return {"ok": True, "file": rel,
            "message": f"已导出：导出/{name}",
            "url": "/download?f=" + urllib.parse.quote(rel)}


def weekly_now(payload):
    """立刻生成一份周报"""
    import importlib.util as _ilu
    wp = os.path.join(os.path.dirname(SCRIPT_DIR), "工具", "weekly_report.py")
    spec = _ilu.spec_from_file_location("fl_weekly", wp)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = mod.build_report(this_week=bool(payload.get("this_week")))
    body = ""
    try:
        body = open(path, "r", encoding="utf-8").read()
    except OSError:
        pass
    # 周报自动下载：复制一份到 导出/（download 路由只允许 SCRIPT_DIR 内）
    url = ""
    try:
        exp = os.path.join(SCRIPT_DIR, "导出")
        os.makedirs(exp, exist_ok=True)
        shutil.copy2(path, os.path.join(exp, os.path.basename(path)))
        rel = "导出/" + os.path.basename(path)
        url = "/download?f=" + urllib.parse.quote(rel)
    except OSError:
        pass
    _reveal(path)
    return {"ok": True, "file": os.path.relpath(path, os.path.dirname(SCRIPT_DIR)), "url": url,
            "message": "周报已生成：" + os.path.basename(path), "body": body}


AUTOSTART_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "FocusTracker"


def autostart_payload():
    """读开机自启状态（注册表 HKCU\\...\\Run\\FocusTracker）"""
    exe = os.path.join(os.path.dirname(SCRIPT_DIR), "焦点监控", "focus_tracker.exe")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            try:
                val, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
            except FileNotFoundError:
                val = ""
        return {"ok": True, "enabled": bool(val), "value": val, "target": exe}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "enabled": False, "message": f"{type(e).__name__}: {e}"}


def set_autostart(payload):
    """开/关开机自启"""
    enable = bool(payload.get("enable"))
    import winreg
    exe = os.path.join(os.path.dirname(SCRIPT_DIR), "焦点监控", "focus_tracker.exe")
    if not os.path.exists(exe):
        return {"ok": False, "message": f"找不到 {exe}，请先用 build.bat 打包"}
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, exe)
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return {"ok": True, "enabled": enable,
                "message": ("已开启开机自启" if enable else "已关闭开机自启")
                           + f"（{os.path.basename(exe)}）"}
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


SESSIONS_FILE = "sessions.json"


def sessions_payload():
    """番茄钟记录：GET 返回今日统计，POST 追加一段"""
    p = os.path.join(SCRIPT_DIR, SESSIONS_FILE)
    data = []
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f) or []
    except Exception:                                        # noqa: BLE001
        data = []
    today = date.today().strftime("%Y-%m-%d")
    mine = [x for x in data if x.get("date") == today]
    return {"ok": True, "items": mine,
            "today_count": len(mine),
            "today_minutes": sum(int(x.get("minutes") or 0) for x in mine)}


def add_session(payload):
    minutes = int(payload.get("minutes") or 0)
    if minutes <= 0:
        return {"ok": False, "message": "minutes 必须是正整数"}
    p = os.path.join(SCRIPT_DIR, SESSIONS_FILE)
    data = []
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f) or []
    except Exception:                                        # noqa: BLE001
        data = []
    data.append({"date": date.today().strftime("%Y-%m-%d"),
                 "end": str(payload.get("end") or datetime.now().strftime("%H:%M")),
                 "minutes": minutes})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    mine = [x for x in data if x["date"] == date.today().strftime("%Y-%m-%d")]
    return {"ok": True, "today_count": len(mine),
            "today_minutes": sum(int(x["minutes"]) for x in mine),
            "message": f"已记录一段 {minutes} 分钟专注"}


def _tracker_running():
    """focus_tracker.exe 在不在跑（用 tasklist，不引第三方库）"""
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq focus_tracker.exe", "/NH"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        return "focus_tracker.exe" in (r.stdout or "").lower()
    except Exception:                                        # noqa: BLE001
        return False


def _clean_old_versions():
    """调用 工具\\清理旧版.ps1（与「开始记录.bat」同一份实现）

    旧版（含双文件夹版）可能还挂在开机自启里，旧 exe 会和新版同时记录 ——
    单实例锁是按目录的，两边锁文件不同，谁也拦不住谁。
    """
    p = os.path.join(os.path.dirname(SCRIPT_DIR), "工具", "清理旧版.ps1")
    if not os.path.exists(p):
        return ""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", p], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out
    except Exception as e:                                   # noqa: BLE001
        return f"（旧版清理跳过：{type(e).__name__}）"


def _tracker_dupes():
    """非当前路径、仍在跑的 focus_tracker 进程列表（面板警示条用）"""
    try:
        import psutil
    except ImportError:
        return []
    me = os.path.abspath(TRACKER_EXE).lower()
    out = []
    for pr in psutil.process_iter(["name", "exe"]):
        try:
            if (pr.info.get("name") or "").lower() != "focus_tracker.exe":
                continue
            exe = pr.info.get("exe") or ""
            if exe and os.path.abspath(exe).lower() != me:
                out.append(exe)
        except Exception:                                    # noqa: BLE001
            continue
    return out


def tracker_payload(payload=None):
    """网页里的记录开关：GET 查状态，POST {action: start|stop} 控制"""
    action = (payload or {}).get("action") or "status"
    if action == "start":
        _clean_old_versions()
        if not os.path.exists(TRACKER_EXE):
            return {"ok": False, "running": False,
                    "message": f"找不到 {TRACKER_EXE}\n请先从发布包获取，或跑 工具\\build.bat"}
        if not _tracker_running():
            subprocess.Popen([TRACKER_EXE], cwd=os.path.dirname(TRACKER_EXE))
            time.sleep(3)
        ok = _tracker_running()
        clean = _clean_old_versions()
        msg = "已开始记录" if ok else "启动失败，请看 专注记录\\startup.log"
        if clean and "没有发现旧版残留" not in clean:
            msg += "\n" + clean
        return {"ok": ok, "running": ok, "message": msg}
    if action == "stop":
        subprocess.run(["taskkill", "/IM", "focus_tracker.exe", "/F"],
                       capture_output=True, text=True, errors="replace")
        time.sleep(1)
        return {"ok": True, "running": _tracker_running(),
                "message": "已停止记录（数据都在，点一下就开始）"}
    running = _tracker_running()
    return {"ok": True, "running": running, "exe": TRACKER_EXE,
            "dupes": _tracker_dupes() if running else [],
            "message": "记录中" if running else "未在记录"}


def open_folder(payload=None):
    """在资源管理器里打开文件夹（服务跑在本机，所以能做这件事）"""
    which = (payload or {}).get("which") or "logs"
    root = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
    target = root if which == "root" else SCRIPT_DIR
    try:
        os.startfile(target)                                 # noqa: S606 (Windows 专用)
        return {"ok": True, "message": f"已打开：{target}"}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def _backup_list():
    d = os.path.join(SCRIPT_DIR, BACKUP_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d), reverse=True):
        if fn.lower().endswith(".zip"):
            p = os.path.join(d, fn)
            out.append({"name": fn, "size": os.path.getsize(p)})
    return out[:6]


def backup_now(payload=None):
    """手动备份：默认到 专注记录\_备份（每周自动备份见 backup_utils.run_if_due）"""
    dst, n, size = BU.zip_now()
    rel = os.path.relpath(dst, SCRIPT_DIR).replace("\\", "/")
    _reveal(dst)
    return {"ok": True, "file": rel, "count": n, "size": size,
            "url": "/download?f=" + urllib.parse.quote(rel),
            "items": _backup_list(),
            "message": f"已备份 {n} 个文件（{size / 1048576:.1f} MB）：{rel}"}


def backup_payload(payload=None):
    if payload:
        return backup_now(payload)
    return {"ok": True, "items": _backup_list()}


def checkup_payload(payload=None):
    """一键体检（reportEmpty 思想）：小白不用懂原理，看 ✅/❌ 就知道要干什么"""
    items = []
    running = _tracker_running()
    items.append({"name": "记录进程", "ok": running,
                  "note": "" if running else "点面板顶部「开始记录」"})
    today = date.today().strftime("%Y-%m-%d")
    lp = S._find_log_file(today)
    if lp and os.path.exists(lp):
        age = time.time() - os.stat(lp).st_mtime
        items.append({"name": "今日日志在写", "ok": age < 600,
                      "note": "" if age < 600 else f"最后写入是 {int(age/60)} 分钟前（记录可能停了）"})
    else:
        items.append({"name": "今日日志", "ok": False, "note": "今天还没有日志文件"})
    au = autostart_payload()
    if au.get("enabled"):
        val = (au.get("value") or "").strip('"').lower()
        ok = "focus_tracker.exe" in val and val.startswith(
            os.path.abspath(TRACKER_EXE).rsplit("\\", 1)[0].lower())
        items.append({"name": "开机自启指向当前版本", "ok": ok,
                      "note": "" if ok else "面板「开始记录」会自动纠正"})
    else:
        items.append({"name": "开机自启", "ok": True, "note": "未开启（可选：系统设置里打开）"})
    stray = [d for d in S._find_all_log_dates() if d < today[:8] + "01"
             and not S._find_log_file(d).replace(os.path.join(SCRIPT_DIR, "归档"), "")
             and os.path.join(SCRIPT_DIR, "归档") not in S._find_log_file(d)]
    try:
        r2 = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                             os.path.join(os.path.dirname(SCRIPT_DIR), "工具", "清理旧版.ps1")],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180)
        copies = [l.strip() for l in (r2.stdout or "").splitlines() if "[发现旧副本]" in l]
        items.append({"name": "旧版副本扫描", "ok": not copies,
                      "note": ("发现 " + str(len(copies)) + " 个旧副本（已列于体检输出）："
                               + "；".join(x.split("] ", 1)[-1] for x in copies))[:200] if copies else ""})
    except Exception:                                        # noqa: BLE001
        pass
    items.append({"name": "归档无欠账", "ok": not stray,
                  "note": f"{len(stray)} 天旧日志还在根目录，跑一次「立即维护」即可" if stray else ""})
    return {"ok": all(x["ok"] for x in items), "items": items}


def about_payload(payload=None):
    try:
        ver = io.open(os.path.join(os.path.dirname(SCRIPT_DIR), "VERSION.txt"),
                      encoding="utf-8").read().strip()
    except OSError:
        ver = "?"
    cfg = BU.load_cfg()
    return {"ok": True, "version": ver, "author": AUTHOR,
            "data_dir": SCRIPT_DIR, "port": DEFAULT_PORT,
            "days": len(S._find_all_log_dates()),
            "backup": {"enabled": bool(cfg.get("enabled")),
                       "dir": cfg.get("dir") or BU.default_dir(),
                       "interval_days": cfg.get("interval_days") or 7,
                       "last_ts": cfg.get("last_ts") or 0}}


def backup_config_payload(payload=None):
    if payload:
        cfg = BU.load_cfg()
        cfg["enabled"] = bool(payload.get("enabled"))
        cfg["dir"] = str(payload.get("dir") or "").strip()
        cfg["interval_days"] = max(1, int(payload.get("interval_days") or 7))
        BU.save_cfg(cfg)
        return {"ok": True, "backup": cfg,
                "message": ("已开启：记录进程会在跨天时检查，到期自动备份到指定目录"
                            "（保留最近 10 份）") if cfg["enabled"] else "已关闭自动备份"}
    return {"ok": True, "backup": BU.load_cfg(), "default_dir": BU.default_dir()}


PRESET_DIR = os.path.normpath(os.path.join(os.path.dirname(SCRIPT_DIR), "分类方案"))
PII_RE = re.compile(r"1[3-9]\d{9}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def presets_list():
    """分类方案\ 目录下的方案文件清单"""
    try:
        files = sorted(f for f in os.listdir(PRESET_DIR)
                       if f.endswith(".txt") and f != "README.md")
    except OSError:
        files = []
    return {"ok": True, "items": files,
            "dir": os.path.basename(os.path.dirname(PRESET_DIR))}


def _preset_text(payload):
    name = str(payload.get("name") or "").strip()
    text = str(payload.get("text") or "")
    if name:
        if "/" in name or ".." in name:
            return ""
        fp = os.path.join(PRESET_DIR, name)
        if not os.path.isfile(fp):
            return ""
        return io.open(fp, encoding="utf-8", errors="replace").read()
    return text


def preset_scan(payload):
    """解析社区方案：只产出"预览"，绝不写文件。

    增量即默认：与用户现状一致的规则忽略；用户已有归属的规则标为冲突并跳过；
    方案内部互斥（同一规则指向两个分类）标为互斥并跳过。
    """
    text = _preset_text(payload)
    if not text.strip():
        return {"ok": False, "message": "方案内容为空（选择内置方案或粘贴文本）"}
    pii = bool(PII_RE.search(text))
    cats = S.load_categories(DASH.CAT_FILE)
    owner = {}
    for name, rules in cats.items():
        for r in rules:
            owner[f"{r[0]}={r[1]}"] = name
    add, conflict, bad = [], [], []
    seen = {}
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if "|" not in s:
            bad.append(s[:40])
            continue
        name, rules_s = s.split("|", 1)
        focus = name.startswith("#专注#")
        name = name.replace("#专注#", "").strip()
        for r in [x.strip() for x in rules_s.split(",") if x.strip()]:
            m = re.match(r"^(proc|title)=(.+)$", r)
            if not m:
                bad.append(r[:40])
                continue
            key = f"{m.group(1)}={m.group(2)}"
            prev = seen.get(key)
            if prev and prev[0] != name:
                conflict.append({"rule": key,
                                 "cats": f"{prev[0]} / {name}",
                                 "note": "方案内部互斥：同一规则指向两个分类"})
                continue
            seen[key] = (name, focus)
            mine = owner.get(key)
            if mine == name:
                continue                                  # 与用户现状一致，无需动作
            if mine:
                conflict.append({"rule": key, "cats": f"{mine} / {name}",
                                 "note": "你已有该规则的归类，增量模式将跳过"})
            else:
                add.append({"cat": name, "rule": key, "focus": focus})
    return {"ok": True, "pii": pii,
            "add": add, "conflict": conflict, "bad": bad,
            "message": f"新增 {len(add)} · 冲突跳过 {len(conflict)} · 无法解析 {len(bad)}"}


def preset_apply(payload):
    """只导入预览中的新增项（前端只回传 add 列表，冲突项根本到不了这里）"""
    adds = payload.get("adds") or []
    if not adds:
        return {"ok": True, "message": "没有可导入的新增项"}
    cats = S.load_categories(DASH.CAT_FILE)
    ok_n, cat_new = 0, False
    for a in adds:
        cat, rule = str(a.get("cat") or ""), str(a.get("rule") or "")
        if not cat or "=" not in rule:
            continue
        k, v = rule.split("=", 1)
        try:
            is_new = cat not in cats
            cat_new = cat_new or is_new
            if S._append_category_rule(cat, v, is_new=is_new,
                                       is_focus=bool(a.get("focus"))):
                ok_n += 1
            cats.setdefault(cat, [])
        except Exception:                                    # noqa: BLE001
            continue
    return {"ok": True,
            "message": f"已增量导入 {ok_n} 条规则（你的现有归类未动）"
                       + ("；新分类已创建" if cat_new else "")}


PRESET_REG = os.path.join(SCRIPT_DIR, "preset_registry.json")


def _reg_load():
    try:
        return json.load(io.open(PRESET_REG, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}


def _reg_save(reg):
    json.dump(reg, io.open(PRESET_REG, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def preset_enable(payload):
    """启用方案 = 增量导入其未拥有规则，并记录"本包贡献了哪些规则"（供停用精准撤回）"""
    name = str(payload.get("name") or "").strip()
    if not name or "/" in name or ".." in name:
        return {"ok": False, "message": "无效方案名"}
    fp = os.path.join(PRESET_DIR, name)
    if not os.path.isfile(fp):
        return {"ok": False, "message": f"分类方案目录里没有 {name}"}
    scan = preset_scan({"name": name})
    if not scan.get("ok"):
        return {"ok": False, "message": scan.get("message", "解析失败")}
    reg = _reg_load()
    cats = S.load_categories(DASH.CAT_FILE)
    applied = []
    for a in scan["add"]:
        cat, rule = a["cat"], a["rule"]
        k, v = rule.split("=", 1)
        try:
            if S._append_category_rule(cat, v, is_new=cat not in cats,
                                       is_focus=bool(a.get("focus"))):
                applied.append(rule)
                cats.setdefault(cat, [])
        except Exception:                                    # noqa: BLE001
            continue
    reg[name] = sorted(set(reg.get(name, []) + applied))
    _reg_save(reg)
    return {"ok": True, "applied": applied,
            "message": f"已启用 {name}：新增 {len(applied)} 条规则"
                       + ("（本方案与你的现状完全一致，无需新增）" if not applied else "")}


def preset_disable(payload):
    """停用方案 = 只移除该包此前贡献的规则（你后来手工加的、别的包贡献的一律不动）"""
    name = str(payload.get("name") or "").strip()
    reg = _reg_load()
    rules = reg.get(name) or []
    if not rules:
        return {"ok": True, "message": f"{name} 没有已启用的规则记录，无需停用"}
    catp = DASH.CAT_FILE
    lines = io.open(catp, encoding="utf-8").read().split("\n")
    n = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        for r in rules:
            token = "," + r
            if token in ln:
                ln = ln.replace(token, "")
                n += 1
        lines[i] = ln.rstrip(",")
    lines = [l for l in lines
             if not re.match(r"^(#专注#)?[^||#]+\|\s*$", l.strip())]
    io.open(catp, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
    reg.pop(name, None)
    _reg_save(reg)
    return {"ok": True, "message": f"已停用 {name}：移除其贡献的 {n} 条规则"
                                   "（你自己的规则全部保留）"}


def preset_status(payload=None):
    return {"ok": True, "registry": _reg_load()}


class Handler(BaseHTTPRequestHandler):
    server_version = "FocusLogPanel/2.9"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):        # 静音：不往控制台刷访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # 浏览器/脚本发完请求就断连（很常见）：不是错误，别把 Traceback 刷到窗口里
            pass

    def _state(self):
        """页面 60 秒自动刷新用：2 秒短缓存 + 出错回退上一份好数据

        为什么：维护/归档正在移动日志文件时，恰好页面在刷新，collect() 可能扑空
        抛异常 —— 旧实现直接 500，浏览器控制台就冒红字。现在返回上一份结果，
        页面完全无感（实在没有缓存才报错）。
        """
        now = time.time()
        if _STATE_CACHE["data"] is not None and now - _STATE_CACHE["t"] < 2:
            return {"ok": True, "data": _STATE_CACHE["data"], "cached": True}
        try:
            data = _collect_cached()
        except Exception as e:                               # noqa: BLE001
            if _STATE_CACHE["data"] is not None:
                return {"ok": True, "data": _STATE_CACHE["data"], "cached": True,
                        "note": f"读取时出错，先给上一次结果（{type(e).__name__}）"}
            return {"ok": False, "message": f"{type(e).__name__}: {e}"}
        _STATE_CACHE["t"], _STATE_CACHE["data"] = now, data
        return {"ok": True, "data": data}

    # ---------- 路由 ----------
    def _api(self, path, payload=None):
        """GET / POST 共用的路由：命中返回响应，未命中返回 None

        坑：这些路由原本只挂在 do_GET 上（插入锚点选错），POST 调用直接 404。
        现在两个入口都先走这里。"""
        payload = payload or {}
        if path == "/api/state":
            return self._send(200, self._state())
        if path == "/api/goals":
            try:
                return self._send(200, set_goals(payload) if payload else goals_payload())
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/export_csv":
            try:
                return self._send(200, export_csv(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/weekly":
            try:
                return self._send(200, weekly_now(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/migrate/scan":
            try:
                return self._send(200, migrate_scan(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/migrate/run":
            try:
                return self._send(200, migrate_run(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/windows":
            key = "today"
            if "?" in self.path:
                key = (parse_qs(urlparse(self.path).query).get("range") or ["today"])[0]
            agg, mins = "1", "15"
            if "?" in self.path:
                q = parse_qs(urlparse(self.path).query)
                agg = (q.get("agg") or ["1"])[0]
                mins = (q.get("min") or ["15"])[0]
            try:
                return self._send(200, _windows_cached(
                    key, agg=(agg != "0"), min_sec=max(0, int(float(mins))) * 60))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/session":
            try:
                return self._send(200, add_session(payload) if payload else sessions_payload())
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/tracker":
            try:
                return self._send(200, tracker_payload(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/open_folder":
            if not payload:
                return self._send(400, {"ok": False, "message": "POST only"})
            return self._send(200, open_folder(payload))

        if path == "/api/backup":
            try:
                return self._send(200, backup_payload(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/about":
            try:
                return self._send(200, about_payload(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/backup_config":
            try:
                return self._send(200, backup_config_payload(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/preset_enable":
            try:
                return self._send(200, preset_enable(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/preset_disable":
            try:
                return self._send(200, preset_disable(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/preset_status":
            return self._send(200, preset_status(payload))

        if path == "/api/presets_list":
            return self._send(200, presets_list())

        if path == "/api/preset_scan":
            try:
                return self._send(200, preset_scan(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/preset_apply":
            try:
                return self._send(200, preset_apply(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/checkup":
            try:
                return self._send(200, checkup_payload(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/panel_autostart":
            try:
                return self._send(200, set_panel_autostart(payload) if payload
                                  else panel_autostart_payload())
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/autostart":
            try:
                if payload:
                    return self._send(200, set_autostart(payload))
                return self._send(200, autostart_payload())
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/categories":
            try:
                return self._send(200, categories_payload())
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})
        return None          # ⚠️ 这里绝不能 _send(404)：_api 的约定是"未命中返回 None"，
        # 一旦它自己把 404 发出去，do_GET/do_POST 后面那些专属路由（首页 / 、/download）
        # 就永远走不到 —— 曾经因此让「打开面板」直接显示 {"ok": false, "message": "not found"}

    def do_GET(self):
        path = self.path.split("?")[0]
        resp = self._api(path, None)
        if resp is not None:
            return resp

        if path in ("/", "/index.html"):
            # HTML 也按"数据版本"缓存：collect 有缓存时，打开面板是纯内存回包
            # （90KB 拼接虽然只有几十毫秒，但叠加起来就是"转的慢"的感知来源）
            key = _COLLECT_CACHE.get("key")
            if key is not None and _HTML_CACHE.get("html"):
                return self._send(200, _HTML_CACHE["html"], "text/html; charset=utf-8")
            html = DASH.build_html(_collect_cached(), server_mode=True)
            _HTML_CACHE["key"], _HTML_CACHE["html"] = key, html
            return self._send(200, html, "text/html; charset=utf-8")
        if path == "/download":
            rel = (parse_qs(urlparse(self.path).query).get("f") or [""])[0]
            full = os.path.normpath(os.path.join(SCRIPT_DIR, rel))
            if not full.startswith(SCRIPT_DIR) or not os.path.isfile(full):
                return self._send(404, {"ok": False, "message": "not found"})
            with open(full, "rb") as fh:
                return self._send(200, fh.read(), "application/octet-stream")
        return self._send(404, {"ok": False, "message": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}

        resp = self._api(path, payload)
        if resp is not None:
            return resp

        if path == "/api/refresh":
            return self._send(200, self._state())

        if path == "/api/maintain":
            # 归档 → 重算深耕 → 导出统计（同一套动作，跨天时守护程序也会自动跑）
            msgs, ok = [], True
            for name, fn in (("归档", S.archive_old_months),
                             ("重算深耕", S.renew_focus_records),
                             ("导出统计", S.export_all)):
                r = _capture(fn)
                ok = ok and r.get("ok", False)
                msgs.append(f"[{name}] {r.get('message','').strip() or '完成'}")
            return self._send(200, {"ok": ok, "message": "\n".join(msgs)})

        if path == "/api/archive":
            return self._send(200, _capture(S.archive_old_months))

        if path == "/api/renew":
            return self._send(200, _capture(S.renew_focus_records))

        if path == "/api/export":
            return self._send(200, _capture(S.export_all))

        if path == "/api/categories":
            try:
                return self._send(200, categories_payload())
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/cat_edit":
            try:
                return self._send(200, cat_edit_payload(payload))
            except Exception as e:                       # noqa: BLE001
                return self._send(500, {"ok": False,
                                        "message": f"{type(e).__name__}: {e}"})

        if path == "/api/scan":
            rows = _capture(S._scan_unclassified_procs, 900)
            if not rows.get("ok"):
                return self._send(200, rows)
            items = [{"proc": r["proc"], "sec": int(r["total_seconds"]),
                      "days": r["day_count"], "title": r.get("example_title", "")}
                     for r in rows["result"]]
            return self._send(200, {"ok": True, "items": items,
                                    "message": f"扫到 {len(items)} 个未分类进程"})

        if path == "/api/rule":
            rules = payload.get("rules") or []
            cats = S.load_categories(DASH.CAT_FILE)
            added, skipped = [], []
            for r in rules:
                proc, cat = (r.get("proc") or "").strip(), (r.get("cat") or "").strip()
                if not proc or not cat:
                    continue
                is_new = cat not in cats
                changed = S._append_category_rule(cat, proc, is_new=is_new,
                                                  is_focus=bool(r.get("focus")))
                (added if changed else skipped).append(f"{cat} ← {proc}")
            msg = []
            if added:
                msg.append("已写入 %d 条：\n  %s" % (len(added), "\n  ".join(added)))
            if skipped:
                msg.append("已存在跳过 %d 条：\n  %s" % (len(skipped), "\n  ".join(skipped)))
            if not msg:
                msg.append("没有可写入的规则")
            return self._send(200, {"ok": True, "message": "\n".join(msg)})

        return self._send(404, {"ok": False, "message": "not found"})


def main():
    if "--static" in sys.argv:          # 只生成静态面板（给打包后的 exe 用）
        DASH.build(do_open=True)
        return 0

    no_open = "--no-open" in sys.argv   # 常驻模式：开机自启的那份不弹浏览器

    # 端口上已经有面板在跑（比如开机常驻的那份）⇒ 直接帮用户打开浏览器就退出，
    # 双击「打开面板.bat」永远能看，不会报"端口被占用"。
    try:
        import urllib.request as _ur
        _op = _ur.build_opener(_ur.ProxyHandler({}))         # 直连：绝不让探测走系统代理
        with _op.open(f"http://127.0.0.1:{DEFAULT_PORT}/",
                      timeout=5) as _r:
            body = _r.read().decode("utf-8", "replace")
        if _r.status == 200:
            # 版本校验：占位实例的版本必须与本 exe 一致，否则"更新了没生效"
            stale = "btnCheckup" not in body or f"v{LOCAL_VERSION}" not in body
            if not stale:
                print(f"  面板已经在运行（端口 {DEFAULT_PORT}，v{LOCAL_VERSION}），直接帮你打开。")
                print("  [本次窗口结束] —— 面板服务仍在后台运行，放心关闭本窗口。")
                if not no_open:
                    webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/")
                return 0
            # 占着端口的是【旧版本面板】⇒ 页面可能不完整/功能缺失。
            # 自愈：结束旧进程，落到下面的正常启动（新版）。
            print("  检测到旧版本面板占着端口，自动重启为新版…")
            subprocess.run(["taskkill", "/IM", "focuspanel.exe", "/F"],
                           capture_output=True)
            time.sleep(1.5)
    except Exception:                                        # noqa: BLE001
        pass

    port = DEFAULT_PORT
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass

    httpd = None
    for p in range(port, port + 10):          # 端口被占就顺延
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print("[X] 端口 8765~8774 都被占用，无法启动")
        return 1

    url = f"http://127.0.0.1:{port}/"
    print("  作者 " + AUTHOR)
    print("=" * 60)
    print("  时间面板已启动（这个黑窗口就是服务本体，关掉它就停止）")
    print(f"  浏览器地址：{url}")
    print()
    print("  进去以后点右上角「工具」，里面有：")
    print("    · 分类助手 / 分类管理 —— 把没归类的程序归到分类里")
    print("    · 导入旧数据 —— 换电脑或从旧版本搬记录")
    print("    · 系统设置 —— 开机自启、提醒开关、每日目标")
    print("    · 生成周报 / 导出 CSV / 立即维护")
    print()
    print("  归档、重算深耕、导出统计 每天跨天会自动跑，不用手动点。")
    print("  记录没在跑的话，面板顶上会提示，点一下就开。")
    print("  作者 " + AUTHOR)
    print("=" * 60)
    def _open_when_ready():
        # 轮询就绪后再开浏览器（盲等 0.8s 在慢机器上会打开还没起来的页面）
        for _ in range(30):
            try:
                import urllib.request as _ur2
                _op2 = _ur2.build_opener(_ur2.ProxyHandler({}))
                _op2.open(f"http://127.0.0.1:{port}/", timeout=2)
                break
            except Exception:                                # noqa: BLE001
                time.sleep(0.5)
        if no_open:
            return
        # TUN / 系统代理环境抗干扰：优先 Chrome --app 独立窗口直连
        # （绕开系统代理 + 时间戳防缓存 + 不污染日常标签页），找不到再退默认浏览器
        u = url + "?t=" + str(int(time.time()))
        for c in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  os.path.join(os.environ.get("LOCALAPPDATA") or "",
                               "Google", "Chrome", "Application", "chrome.exe")):
            try:
                if c and os.path.exists(c):
                    subprocess.Popen([c, "--app=" + u, "--proxy-server=direct://"])
                    return
            except Exception:                                # noqa: BLE001
                continue
        webbrowser.open(u)
    threading.Thread(target=_open_when_ready, daemon=True).start()

    # UX：服务一起来就在后台把全量数据算好，浏览器打开时永远是热数据。
    # 没有这一步，开机常驻后第一次打开面板要等一次冷计算（实测 2.4s）。
    threading.Thread(
        target=lambda: (
            _collect_cached(),
            _windows_warmup(),
            print("  数据预热完成（含窗口排行），面板秒开。"),
        ), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
