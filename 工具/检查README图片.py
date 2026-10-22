# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""README 图片引用闸门  v1.0.0
    python 工具\检查README图片.py     # 手工查（README 已 add 则查暂存区，否则查工作区）
    .git\hooks\pre-commit 会调它：非零退出 = 拒绝提交

为什么需要（G17，2026-10-22 踩过）：
  格式化工具 / 编辑器重写 README 时，把 `![夜晚](docs/screenshots/panel-dark.png)`
  换成了纯文本「夜晚」。两张 png 仍躺在磁盘上，`git status` 只显示 modified，
  **只有看 diff 才看得出图没了** —— 这种损坏能一路提交上去。

判据（只比「引用有没有变少」，不猜意图）：
  取 HEAD 版与待提交版的 `![alt](path)` 集合，凡 HEAD 里有、待提交版里没有的 = 失败。
  真有意删图时用 `git commit --no-verify` 跳过，并在提交信息里写明原因。
"""

import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)")


def git(*args):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout or ""


def staged(path):
    code, out = git("diff", "--cached", "--name-only", "--", path)
    return code == 0 and out.strip() != ""


def read_new():
    """README 已 add => 读暂存区（= pre-commit 时真正要提交的内容）；否则读工作区。"""
    if staged("README.md"):
        code, out = git("show", ":README.md")
        if code == 0:
            return out, "暂存区"
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8", errors="replace") as f:
        return f.read(), "工作区"


def read_head():
    """HEAD 版（无 HEAD 的处女仓返回空）。"""
    code, out = git("show", "HEAD:README.md")
    return out if code == 0 else ""


def main():
    new, source = read_new()
    old = read_head()
    if not old:
        print("[README图片] 无 HEAD 版可对比，跳过（首次提交）")
        return 0

    lost = [r for r in REF.findall(old) if r not in REF.findall(new)]
    print("[README图片] 对比源=%s  HEAD 引用=%d  待提交引用=%d"
          % (source, len(REF.findall(old)), len(REF.findall(new))))
    if not lost:
        print("[README图片] VERDICT=PASS 无引用丢失")
        return 0

    print("[README图片] VERDICT=FAIL 以下图片引用消失了：")
    for alt, path in lost:
        disk = "" if re.match(r"^[a-z]+://", path, re.I) else "（文件仍在磁盘上，说明是误伤）" \
            if os.path.exists(os.path.join(ROOT, path)) else ""
        print("  - ![%s](%s) %s" % (alt, path, disk))
    print("[README图片] 确为有意删除 => git commit --no-verify，并在提交信息里写明原因")
    return 1


if __name__ == "__main__":
    sys.exit(main())
