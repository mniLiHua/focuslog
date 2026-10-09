# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
"""
窗口分类规则解析共享模块
被 focus_tracker.py（专注判断）和 summary.py（全部分类统计）共用

v2.7.2 修正（两处静默失效）：
  1. 旧 load_categories() 会把行首的 #专注# 前缀吃掉，调用方拿不到「哪几类是专注」，
     只能拿「有没有 proc 规则」冒充判断 ⇒ 分类菜单把「系统」「游戏」也标成专注。
     现在由 parse_category_file() 返回 focus 标记，load_categories() 保持旧签名兼容。
  2. 同名分类行旧行为是「后者静默覆盖前者」——一行写坏就整类规则 + 专注标记全丢，
     而且没有任何提示。现在改为合并（规则取并集、专注取或）并回报 duplicates 列表，
     由调用方打印告警。
"""

import fnmatch
import os
import shutil

FOCUS_PREFIX = "#专注#"



# 规则类型的别名 —— 让配置对普通用户可读：
#   proc / process / exe / 进程 / 进程名  → proc
#   title / 关键词 / 关键字 / 标题 / 窗口标题 → title
# 写回时统一规范成 proc= / title=（保证老版本程序仍然读得懂）
TYPE_ALIASES = {
    "proc": "proc", "process": "proc", "exe": "proc", "进程": "proc", "进程名": "proc",
    "title": "title", "关键词": "title", "关键字": "title", "标题": "title",
    "窗口标题": "title", "标题关键词": "title",
}


def normalize_type(raw):
    """把用户写的类型名规范成 proc / title（不认识的保持原样，交给调用方忽略）"""
    key = (raw or "").strip().lower()
    return TYPE_ALIASES.get(key, key)

def parse_category_file(category_file, only_focus=False):
    """解析分类配置

    Args:
        category_file: category.txt 的完整路径
        only_focus: True 时只返回带 #专注# 标记的分类

    Returns:
        (meta, duplicates)
        meta       = {分类名: {"rules": [(type, keyword), ...], "focus": bool}}
        duplicates = 出现两次及以上的分类名（旧行为会静默覆盖）

    分类规则格式：
        分类名|proc=进程名,title=标题关键词
        #专注#分类名|proc=进程名,title=标题关键词
    """
    meta = {}
    duplicates = []
    dup_seen = set()
    pending = []          # 紧跟分类行上方的注释行（写回时原样保留，别丢说明文字）

    if not os.path.exists(category_file):
        return meta, duplicates

    with open(category_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                pending = []          # 空行断开：上面的注释不属于下面的分类
                continue
            # 跳过纯注释行（#开头但没有 #专注# 标记）
            if line.startswith("#") and not line.startswith(FOCUS_PREFIX):
                pending.append(line)
                continue

            is_focus = line.startswith(FOCUS_PREFIX)
            if only_focus and not is_focus:
                pending = []
                continue

            body = line[len(FOCUS_PREFIX):] if is_focus else line
            if "|" not in body:
                continue

            name, rules_str = body.split("|", 1)
            name = name.strip()
            rules = []
            for rule in rules_str.split(","):
                rule = rule.strip()
                if "=" not in rule:
                    continue
                rtype, keyword = rule.split("=", 1)
                rules.append((normalize_type(rtype), keyword.strip()))

            if not rules:
                continue

            if name in meta:
                if name not in dup_seen:
                    duplicates.append(name)
                    dup_seen.add(name)
                # 合并而不是覆盖：规则取并集，专注标记取「或」
                old = meta[name]
                for r in rules:
                    if r not in old["rules"]:
                        old["rules"].append(r)
                old["focus"] = old["focus"] or is_focus
            else:
                meta[name] = {"rules": rules, "focus": is_focus, "comment": pending}
            pending = []

    return meta, duplicates


def load_categories(category_file, only_focus=False):
    """兼容旧接口：返回 {分类名: [(type, keyword), ...]}（已按文件行序排好）"""
    meta, _dup = parse_category_file(category_file, only_focus=only_focus)
    return {name: m["rules"] for name, m in meta.items()}


def is_focus_category(name, meta):
    """判断某个分类是否带 #专注# 标记（meta 来自 parse_category_file）"""
    return bool(meta.get(name, {}).get("focus"))


def dump_categories(category_file, meta):
    """把 meta 写回 category.txt（**字典顺序即优先级顺序**，调用方负责保持）

    meta: {分类名: {"rules": [(type, keyword), ...], "focus": bool}}

    规则：
      · 文件开头的说明注释原样保留（遇到第一条规则行就停）
      · 每个分类前写一行 `# ----- 名字 (专注) -----` 分隔注释
      · 先备份 .bak，再原子替换（写 tmp → os.replace），避免写一半把配置写坏
    """
    header = []
    if os.path.exists(category_file):
        with open(category_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.rstrip("\n")
                stripped = s.lstrip()
                # 头部 = 文件最开头的连续注释块；一到空行就结束
                # （否则会把各分类的分隔注释也当成头部，写回时越叠越多 —— 曾踩）
                if stripped.startswith("#") and not stripped.startswith(FOCUS_PREFIX):
                    header.append(s)
                else:
                    break

    lines = list(header)
    while lines and lines[-1].strip() == "":
        lines.pop()
    lines.append("")
    for name, m in meta.items():
        rules = m.get("rules") or []
        if not rules:
            continue
        # 分类行上方的说明注释原样写回（少了它，调优理由就没了）；
        # 只把括号里的 专注/非专注 标记同步成当前值。
        tag_txt = " (专注)" if m.get("focus") else " (非专注)"
        comment = list(m.get("comment") or [])
        if comment:
            for idx, cline in enumerate(comment):
                if "(专注)" in cline or "(非专注)" in cline:
                    for old_tag in (" (专注)", " (非专注)"):
                        if old_tag in cline:
                            comment[idx] = cline.replace(old_tag, tag_txt, 1)
                            break
                    break
            lines.extend(comment)
        else:
            lines.append(f"# ----- {name}{tag_txt} -----")
        lines.append(f"{FOCUS_PREFIX if m.get('focus') else ''}{name}|"
                     + ",".join(f"{t}={k}" for t, k in rules))
        lines.append("")

    content = "\n".join(lines).rstrip("\n") + "\n"
    tmp = category_file + ".tmp"
    if os.path.exists(category_file):
        try:
            shutil.copy2(category_file, category_file + ".bak")
        except OSError:
            pass
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    os.replace(tmp, category_file)
    return True


def _match(keyword, value, exact=False):
    """关键字匹配：无 * 时与原行为完全一致（向后兼容）；带 * 时按通配符

    · proc：默认精确；写了 * 就按通配（如 chrome*=chrome.exe、*code*）
    · title：默认子串；写了 * 就按通配（如 *bilibili*、*物理*）
    """
    kw = keyword.lower()
    if "*" in kw or "?" in kw:
        return fnmatch.fnmatch(value, kw if exact else f"*{kw}*")
    return value == kw if exact else kw in value


def classify_window(proc, title, categories):
    """判断窗口属于哪个分类

    返回分类名，若未匹配则返回 None
    """
    proc_lower = proc.lower()
    title_lower = title.lower()

    for cat_name, rules in categories.items():
        for rtype, keyword in rules:
            if rtype == "proc" and _match(keyword, proc_lower, exact=True):
                return cat_name
            if rtype == "title" and _match(keyword, title_lower):
                return cat_name

    return None


def is_focus_window(proc, title, focus_categories):
    """判断当前窗口是否属于专注分类"""
    return classify_window(proc, title, focus_categories) is not None
