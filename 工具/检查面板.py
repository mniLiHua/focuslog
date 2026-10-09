# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""发版前必跑：面板前端自检  v1.0

为什么有这个东西
    2026-10-09 连着踩了三类"接口 200、页面却空的"坑，全都是接口测试抓不到的：
      1. 函数改名漏改调用点（drawTopWin → refreshTop）⇒ 整条 draw 链抛异常，后面全空
      2. await 写在非 async 函数里 ⇒ 语法错误，整页 JS 一行都不执行
      3. 函数写了但没挂进 draw() 链（drawHourly）⇒ 安静地什么都不画

用法
    python 工具\检查面板.py            # 自检 + 生成一次面板
    python 工具\检查面板.py --no-build  # 只自检

退出码 0 = 全过；非 0 = 有 ❌，别发版。
"""
import io
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "专注记录", "dashboard.py")
SERVER = os.path.join(ROOT, "专注记录", "panel_server.py")

fails = []
warns = []


def ok(msg):
    print("  [OK] " + msg)


def bad(msg):
    print("  [X]  " + msg)
    fails.append(msg)


def warn(msg):
    print("  [!]  " + msg)
    warns.append(msg)


src = io.open(DASH, encoding="utf-8").read()

# ---------- 1) JS 语法（node --check 是权威；没有 node 就退化为括号平衡） ----------
print("1) JS 语法")
m = re.search(r"<script[^>]*>(.*?)</script>", src, re.S)
js = m.group(1) if m else ""
tmp = os.path.join(ROOT, "_js_check_tmp.js")
io.open(tmp, "w", encoding="utf-8").write(js)
has_node = True
try:
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        ok("node --check 通过（await 位置写错、括号不配对这类都是它抓的）")
    else:
        bad("node --check 失败：\n" + (r.stderr or r.stdout)[:600])
except FileNotFoundError:
    has_node = False
    warn("本机没有 node，跳过权威语法检查 —— 建议装 node 后重跑")
finally:
    try:
        os.remove(tmp)
    except OSError:
        pass

# 只在没有 node 时才用启发式兜底（正则判"非 async 函数里有没有 await"会把
# 内层 `onclick = async () => { await ... }` 误判，所以它只能当警告，不能当闸门）
if not has_node:
    cur, depth = None, 0
    for i, ln in enumerate(js.split("\n"), 1):
        mm = re.match(r"\s*(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", ln)
        if mm and cur is None:
            cur = (mm.group(2), bool(mm.group(1)), i)
            depth = ln.count("{") - ln.count("}")
            continue
        if cur:
            depth += ln.count("{") - ln.count("}")
            if re.search(r"\bawait\b", ln) and not cur[1] and "async" not in ln:
                warn("疑似：非 async 函数 %s()（第 %d 行）里有 await（第 %d 行）"
                     % (cur[0], cur[2], i))
            if depth <= 0:
                cur = None

# ---------- 2) 定义了但从未调用的绘制函数 ----------
print("2) 绘图函数有没有挂进 draw() 链")
defs = set(re.findall(r"function\s+((?:draw|render|paint)[A-Za-z_$][\w$]*)\s*\(", src))
for fn in sorted(defs):
    calls = len(re.findall(r"(?<!function\s)\b" + re.escape(fn) + r"\s*\(", src))
    if calls == 0:
        bad("写了但从没被调用：%s()  ← 这种会安静地不画，接口还是 200" % fn)
if not fails:
    ok("所有 draw/render/paint 函数都有调用点")

# ---------- 3) 调用了但没定义 ----------
print("3) 是否有调用未定义的函数")
defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", src))
defined |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^)=\n]*\)?\s*=>", src))
defined |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*function", src))
BUILTIN = {
    "getElementById", "querySelector", "querySelectorAll", "addEventListener",
    "removeEventListener", "createElement", "appendChild", "removeChild", "setAttribute",
    "getAttribute", "removeAttribute", "getComputedStyle", "requestAnimationFrame",
    "setInterval", "clearInterval", "setTimeout", "clearTimeout", "encodeURIComponent",
    "decodeURIComponent", "getBoundingClientRect", "scrollIntoView", "insertBefore",
    "createObjectURL", "revokeObjectURL", "matchMedia", "toLocaleString",
    # 字符串 / 数字 / 数组 / 日期 的内置方法（这些出现在项目代码里很正常）
    "toFixed", "toString", "padStart", "padEnd", "toUpperCase", "toLowerCase",
    "startsWith", "endsWith", "includes", "indexOf", "lastIndexOf", "localeCompare",
    "parseInt", "parseFloat", "isNaN", "toISOString", "toTimeString", "getTime",
    "getDate", "getDay", "getMonth", "getFullYear", "getHours", "getMinutes",
    "setDate", "setHours", "setMinutes", "flatMap", "forEach", "getItem", "setItem",
    "removeItem", "JSON.stringify", "keys", "values", "entries",
}
calls = set(re.findall(r"\b([a-z][a-zA-Z]*[A-Z][\w$]*)\s*\(", src))
missing = sorted(c for c in calls if c not in defined and c not in BUILTIN)
if missing:
    warn("调用了但没定义（可能是 DOM/JS 内置，请人眼扫一眼）：%s" % missing)
else:
    ok("没有未定义调用")

# ---------- 4) getElementById 的 id 是否都存在于 HTML ----------
print("4) getElementById 引用的 id 是否都在 HTML 里")
ids_used = set(re.findall(r"getElementById\(['\"]([\w\-]+)['\"]\)", src))
ids_html = set(re.findall(r'id="([\w\-]+)"', src))
gone = sorted(i for i in ids_used if i not in ids_html)
if gone:
    bad("JS 找了 HTML 里不存在的 id（会 TypeError，后面的代码全不执行）：%s" % gone)
else:
    ok("全部 id 都能找到")

# ---------- 5) 双主题 CSS 变量成对 ----------
print("5) 双主题变量是否成对")
dark = re.findall(r"--([\w\-]+)\s*:", src)
allv = {}
for name in set(dark):
    allv[name] = len(re.findall(r"--" + re.escape(name) + r"\s*:", src))
odd = sorted(n for n, c in allv.items() if c == 1 and n.startswith(("bg", "fg", "accent", "line", "ok", "warn")))
if odd:
    warn("只定义了一次的主题变量（可能是单向缺失）：%s" % odd[:12])
else:
    ok("关键主题变量都是成对的")

# ---------- 6) 生成面板（顺带验证不抛异常） ----------
if "--no-build" not in sys.argv:
    print("6) 生成面板")
    r = subprocess.run([sys.executable, DASH], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode == 0:
        ok((r.stdout or "").strip().splitlines()[0][:80])
    else:
        bad("生成失败：" + (r.stderr or "")[-400:])

print("\n===== 结论 =====")
print("  ❌ %d 项，⚠ %d 项" % (len(fails), len(warns)))
if fails:
    print("  别发版，先把 ❌ 修掉。")
sys.exit(1 if fails else 0)
