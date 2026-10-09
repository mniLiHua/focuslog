# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
旧版本一键迁移  v2.11
把老布局（桌面上的「焦点监控\」+「focuslogs\」两个文件夹）里的**日志与配置**
安全搬到新布局（单目录 focuslog\），并重设开机自启。

    python tools\migrate.py               自动探测旧目录并迁移
    python tools\migrate.py --from D:\old 指定旧目录（里面有 focus_tracker.py）
    python tools\migrate.py --dry-run     只检查、不写任何文件

安全原则：
  1. **只读旧目录**（用复制，不移动），旧数据原地保留，随时可回退
  2. **绝不覆盖新目录已有内容**：同名冲突一律跳过并报告
  3. 迁移前把旧数据整包备份到 `_migrate_backup_时间戳\`
  4. 迁移后做一次校验：日志文件数 + 累计在线时长（用同一套解析器算）
"""

import io
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_TR = os.path.join(ROOT, "焦点监控")
NEW_FL = os.path.join(ROOT, "专注记录")

LOG_PAT = re.compile(r"^\d{4}-\d{2}-\d{2}\.txt$")
DATA_DIRS = ("归档", "深耕记录", "时长统计")
SKIP_NAMES = ("category_旧版备份", "category_旧版原始")


def say(msg=""):
    print(msg)


def find_old(from_arg=None):
    """返回 (程序目录, 数据目录)"""
    cands = []
    if from_arg:
        cands.append(from_arg)
    home = os.path.expanduser("~")
    for base in (os.path.dirname(ROOT), os.path.join(home, "Desktop"), os.path.join(home, "桌面"),
                 "C:\\Users\\Public\\Desktop"):
        if not os.path.isdir(base):
            continue
        for name in ("焦点监控", "focus_tracker", "focuslog_old"):
            cands.append(os.path.join(base, name))

    # 小白增强（v2.15.0）：扫桌面（含 OneDrive 桌面）一层子目录，像旧版的都当候选
    home_dsk = [os.path.join(home, "Desktop"), os.path.join(home, "桌面"),
                os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "OneDrive", "桌面")]
    for dsk in home_dsk:
        if os.path.isdir(dsk):
            try:
                cands += [os.path.join(dsk, n) for n in os.listdir(dsk)]
            except OSError:
                pass

    for prog in cands:
        if not prog or not os.path.isdir(prog):
            continue
        if not os.path.exists(os.path.join(prog, "focus_tracker.py")) and \
           not os.path.exists(os.path.join(prog, "focus_tracker.exe")):
            continue
        for data in (os.path.join(os.path.dirname(prog), "focuslogs"), prog):
            if os.path.isdir(data) and (
                    os.path.exists(os.path.join(data, "category.txt")) or
                    any(LOG_PAT.match(f) for f in os.listdir(data))):
                return prog, data
    return None, None


def copy_tree_merge(src, dst, stats):
    """递归合并复制；同名已存在的文件跳过（绝不覆盖）"""
    os.makedirs(dst, exist_ok=True)
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            copy_tree_merge(s, d, stats)
        else:
            if os.path.exists(d):
                stats["skipped"] += 1
                stats.setdefault("skip_list", []).append(os.path.relpath(d, ROOT))
            else:
                shutil.copy2(s, d)
                stats["copied"] += 1


def count_logs(d):
    n = 0
    for dirpath, _dirs, files in os.walk(d):
        n += sum(1 for f in files if LOG_PAT.match(f))
    return n


def scan_candidate(prog, data):
    """给一个候选旧目录出个体检报告（给面板列表用）"""
    dates = []
    for dirpath, _dirs, files in os.walk(data):
        for f in files:
            if LOG_PAT.match(f):
                dates.append(f[:-4])
    dates.sort()
    cat = os.path.join(data, "category.txt")
    cats = 0
    if os.path.exists(cat):
        try:
            sys.path.insert(0, NEW_TR)
            import category_utils as CU
            meta, _dup = CU.parse_category_file(cat)
            cats = len(meta)
        except Exception:                                     # noqa: BLE001
            cats = 0
    return {"prog": prog, "data": data, "logs": len(dates),
            "first": dates[0] if dates else "", "last": dates[-1] if dates else "",
            "cats": cats}


def find_candidates(from_arg=None):
    """自动搜索常见位置里的旧项目（面板的「自动搜索」用）"""
    cands, seen = [], set()
    if from_arg:
        cands.append(from_arg)
    home = os.path.expanduser("~")
    bases = [os.path.dirname(ROOT), os.path.join(home, "Desktop"), os.path.join(home, "桌面"),
             os.path.join(home, "Downloads"), os.path.join(home, "下载"),
             "C:\\Users\\Public\\Desktop"]
    for drive in ("C:\\", "D:\\", "E:\\", "F:\\"):
        for name in ("焦点监控", "focuslog_old", "focus_tracker"):
            bases.append(os.path.join(drive, name))
    for base in bases:
        if not base or not os.path.isdir(base):
            continue
        for name in ("焦点监控", "focuslog_old", "focus_tracker", ""):
            prog = os.path.join(base, name) if name else base
            if not prog or prog in seen or not os.path.isdir(prog):
                continue
            seen.add(prog)
            if not (os.path.exists(os.path.join(prog, "focus_tracker.py")) or
                    os.path.exists(os.path.join(prog, "focus_tracker.exe"))):
                continue
            data = None
            for cand in (os.path.join(os.path.dirname(prog), "focuslogs"), prog):
                if os.path.isdir(cand) and (
                        os.path.exists(os.path.join(cand, "category.txt")) or
                        any(LOG_PAT.match(f) for f in os.listdir(cand))):
                    data = cand
                    break
            if data:
                cands.append((prog, data))
    out, seen2 = [], set()
    for item in cands:
        prog, data = item if isinstance(item, tuple) else (item, None)
        if data is None:
            found = find_old(prog)
            prog, data = found
            if not data:
                continue
        if data in seen2:
            continue
        seen2.add(data)
        out.append(scan_candidate(prog, data))
    return out


def migrate_api(old_prog, old_data, dry=False):
    """执行迁移，返回结构化报告（CLI 与面板共用）"""
    rep = {"ok": True, "copied": 0, "skipped": 0, "backup": "", "days": 0,
           "total_sec": 0, "cat_msg": "", "messages": []}
    if dry:
        rep["messages"].append("dry-run：只扫描，不写文件")
        return rep
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(ROOT, f"_migrate_backup_{stamp}")
    shutil.copytree(old_data, os.path.join(backup, "focuslogs"), dirs_exist_ok=True)
    rep["backup"] = backup

    stats = {"copied": 0, "skipped": 0}
    os.makedirs(NEW_FL, exist_ok=True)
    for name in sorted(os.listdir(old_data)):
        if not LOG_PAT.match(name):
            continue
        dst = os.path.join(NEW_FL, name)
        if os.path.exists(dst):
            stats["skipped"] += 1
        else:
            shutil.copy2(os.path.join(old_data, name), dst)
            stats["copied"] += 1

    cat_old = os.path.join(old_data, "category.txt")
    cat_new = os.path.join(NEW_FL, "category.txt")
    if os.path.exists(cat_old):
        if os.path.exists(cat_new):
            shutil.copy2(cat_old, os.path.join(NEW_FL, "category_来自旧版本.txt"))
            rep["cat_msg"] = "保留现用配置，旧配置另存为 category_来自旧版本.txt"
        else:
            shutil.copy2(cat_old, cat_new)
            rep["cat_msg"] = "已导入旧分类配置"

    for d in DATA_DIRS:
        s = os.path.join(old_data, d)
        if os.path.isdir(s):
            copy_tree_merge(s, os.path.join(NEW_FL, d), stats)

    st_old = os.path.join(old_data, "focus_state.json")
    if os.path.exists(st_old) and not os.path.exists(os.path.join(NEW_FL, "focus_state.json")):
        shutil.copy2(st_old, os.path.join(NEW_FL, "focus_state.json"))

    rep["copied"], rep["skipped"] = stats["copied"], stats["skipped"]

    # 校验
    try:
        sys.path.insert(0, NEW_FL)
        sys.path.insert(0, NEW_TR)
        import summary as S
        dates = S._find_all_log_dates()
        rep["days"] = len(dates)
        rep["total_sec"] = int(sum(sum(r[3] for r in S.parse_log(d)) for d in dates))
    except Exception as e:                                    # noqa: BLE001
        rep["messages"].append(f"校验跳过：{type(e).__name__}: {e}")

    # 重设自启
    exe = os.path.join(NEW_TR, "focus_tracker.exe")
    if os.path.exists(exe):
        r = subprocess.run(["reg", "add",
                            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                            "/v", "FocusTracker", "/t", "REG_SZ", "/d", exe, "/f"],
                           capture_output=True, text=True)
        rep["messages"].append("开机自启已指向新路径" if r.returncode == 0 else "自启写入失败")
    return rep


def main():
    dry = "--dry-run" in sys.argv
    from_arg = None
    if "--from" in sys.argv:
        try:
            from_arg = sys.argv[sys.argv.index("--from") + 1]
        except IndexError:
            pass

    say("=" * 62)
    say("  旧版本数据迁移（只读旧目录；冲突不覆盖）")
    say("=" * 62)

    old_prog, old_data = find_old(from_arg)
    if not old_data:
        say("  没找到旧目录。")
        say("  如果你的旧版本在别处，请这样跑：")
        say("      python tools\\migrate.py --from \"D:\\旧路径\\焦点监控\"")
        return 1

    say(f"  旧程序目录: {old_prog}")
    say(f"  旧数据目录: {old_data}")
    say(f"  新数据目录: {NEW_FL}")
    say("")

    old_logs = count_logs(old_data)
    new_logs = count_logs(NEW_FL)
    say(f"  旧目录日志 {old_logs} 个；新目录现有 {new_logs} 个")

    if dry:
        say("\n  [dry-run] 只检查，不做任何写入。")
        return 0

    # 1) 整包备份旧数据
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(ROOT, f"_migrate_backup_{stamp}")
    say(f"\n  [1/4] 备份旧数据 → {os.path.basename(backup)} ...")
    shutil.copytree(old_data, os.path.join(backup, "focuslogs"), dirs_exist_ok=True)

    # 2) 复制日志与配置
    say("  [2/4] 复制日志与配置 ...")
    stats = {"copied": 0, "skipped": 0}
    os.makedirs(NEW_FL, exist_ok=True)

    for name in sorted(os.listdir(old_data)):
        if not LOG_PAT.match(name):
            continue
        src = os.path.join(old_data, name)
        dst = os.path.join(NEW_FL, name)
        if os.path.exists(dst):
            stats["skipped"] += 1
            stats.setdefault("skip_list", []).append(name)
        else:
            shutil.copy2(src, dst)
            stats["copied"] += 1

    cat_old = os.path.join(old_data, "category.txt")
    cat_new = os.path.join(NEW_FL, "category.txt")
    cat_msg = ""
    if os.path.exists(cat_old):
        if os.path.exists(cat_new):
            cat_backup = os.path.join(NEW_FL, "category_来自旧版本.txt")
            shutil.copy2(cat_old, cat_backup)
            cat_msg = "新目录已有 category.txt（保留现用的），旧配置另存为 category_来自旧版本.txt"
        else:
            shutil.copy2(cat_old, cat_new)
            cat_msg = "已导入旧分类配置"

    for d in DATA_DIRS:
        s = os.path.join(old_data, d)
        if os.path.isdir(s):
            copy_tree_merge(s, os.path.join(NEW_FL, d), stats)

    state_old = os.path.join(old_data, "focus_state.json")
    if os.path.exists(state_old) and not os.path.exists(os.path.join(NEW_FL, "focus_state.json")):
        shutil.copy2(state_old, os.path.join(NEW_FL, "focus_state.json"))

    for name in ("category.txt.bak",):
        p = os.path.join(old_data, name)
        if os.path.exists(p) and not os.path.exists(os.path.join(NEW_FL, name)):
            shutil.copy2(p, os.path.join(NEW_FL, name))

    say(f"       复制 {stats['copied']} 个文件，跳过（已存在）{stats['skipped']} 个")
    if cat_msg:
        say("       分类配置：" + cat_msg)

    # 3) 校验：日志数 + 累计在线时长
    say("  [3/4] 校验 ...")
    sys.path.insert(0, NEW_FL)
    sys.path.insert(0, NEW_TR)
    try:
        import summary as S
        dates = S._find_all_log_dates()
        total = 0.0
        for d in dates:
            total += sum(r[3] for r in S.parse_log(d))
        h, m = int(total // 3600), int((total % 3600) // 60)
        say(f"       新目录现在有 {len(dates)} 天日志，累计在线 {h} 小时 {m} 分钟")
    except Exception as e:                                # noqa: BLE001
        say(f"       校验跳过（{type(e).__name__}: {e}）")

    # 4) 重设开机自启
    say("  [4/4] 重设开机自启 ...")
    exe = os.path.join(NEW_TR, "focus_tracker.exe")
    if os.path.exists(exe):
        r = subprocess.run(["reg", "add",
                            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                            "/v", "FocusTracker", "/t", "REG_SZ", "/d", exe, "/f"],
                           capture_output=True, text=True)
        say("       已把自启指向新路径" if r.returncode == 0 else "       自启写入失败（可手动双击「开机自启.bat」）")
    else:
        say("       新目录里没有 focus_tracker.exe，跳过（双击「开机自启.bat」即可）")

    say("\n" + "=" * 62)
    say("  迁移完成。旧目录**没有被动过**，确认没问题后可以自行删除。")
    say(f"  备份位置：{backup}")
    say("  下一步：双击「启动监控.bat」，再双击「打开面板.bat」看数据是否齐全。")
    say("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
