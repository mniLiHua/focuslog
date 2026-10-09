# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""
打发布包  v3.2.0
    python 工具\make_release.py

产出 `_release\FocusLog_vX.Y.Z.zip`：程序 + 文档 + exe + 空数据骨架，
**不含任何真实日志**。带两道安全断言：
  1. 包里不允许出现 YYYY-MM-DD*.txt 这类真实日志
  2. category.txt 会先清掉私人条目（PRIVATE_RULES）再打包
"""

import io
import os
import re
import shutil
import sys
import zipfile

# 控制台可能是 GBK（cmd 默认 936），打不出的字符直接替换，别让脚本崩在最后一行
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = os.path.join(ROOT, "焦点监控")
FL = os.path.join(ROOT, "专注记录")
OUT_DIR = os.path.join(ROOT, "_release")

# 只在本机有意义的规则，不进发布包（每次发版前扫一眼这个列表）
PRIVATE_RULES = ["王婕"]

PROG_EXT = (".py", ".exe")
BAT_EXT = (".bat",)
DOC_FILES = ("VERSION.txt",)  # 微信分发：md 是 GitHub 视角，不发   # CHANGELOG 在 docs\ 里，随 docs 整目录拷贝
EMPTY_DIRS = ("归档", "深耕记录", "时长统计")

# 普通用户最常用的入口直接放包根目录（子目录只留程序与数据）
ROOT_BATS = ("打开面板.bat", "开始记录.bat", "更新.bat", "使用说明.txt")
# 进阶入口随包一起发（朋友也可能想"只看快照"/"手动设自启"），开发用的 build/打包 不发
TOOLS = ("更新.ps1", "清理旧版.ps1", "migrate.py", "weekly_report.py",
         "面板（快照）.bat", "取消自启.bat", "停止监控.bat", "迁移旧数据.bat")


def read_version():
    p = os.path.join(ROOT, "VERSION.txt")
    # \ufeff 兜底：文件若被 PowerShell Set-Content -Encoding UTF8 重写会带 BOM
    return io.open(p, encoding="utf-8-sig").read().replace("\ufeff", "").strip() or "0.0.0"


def clean_category(src, dst):
    """去掉私人条目后的分类模板"""
    out = []
    for line in io.open(src, encoding="utf-8"):
        keep = line.rstrip("\n")
        for bad in PRIVATE_RULES:
            keep = re.sub(r",?title=" + re.escape(bad), "", keep)
        keep = keep.replace(",,", ",").rstrip(",")
        out.append(keep)
    with io.open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def build():
    ver = read_version()
    name = f"FocusLog_v{ver}"
    stage = os.path.join(OUT_DIR, name)
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    # 根目录：普通用户看得到的入口 bat
    for fn in ROOT_BATS:
        p = os.path.join(ROOT, fn)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(stage, fn))
        else:
            print("   [warn] 缺少入口:", fn)

    # 程序目录（只留 py/exe，bat 已提到根目录）
    dst_tr = os.path.join(stage, "焦点监控")
    os.makedirs(dst_tr)
    for fn in sorted(os.listdir(TR)):
        if fn.endswith(PROG_EXT):
            shutil.copy2(os.path.join(TR, fn), os.path.join(dst_tr, fn))

    dst_fl = os.path.join(stage, "专注记录")
    os.makedirs(dst_fl)
    for fn in sorted(os.listdir(FL)):
        if fn.endswith(PROG_EXT):
            shutil.copy2(os.path.join(FL, fn), os.path.join(dst_fl, fn))

    # 工具（更新 / 迁移要用）
    dst_tools = os.path.join(stage, "工具")
    os.makedirs(dst_tools)
    for fn in TOOLS:
        p = os.path.join(ROOT, "工具", fn)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dst_tools, fn))
    # 分类方案（面板"方案专区"的数据源；README.md 属 GitHub 视角，不进包）
    dst_pre = os.path.join(stage, "分类方案")
    os.makedirs(dst_pre)
    pre_dir = os.path.join(ROOT, "分类方案")
    for fn in sorted(os.listdir(pre_dir)):
        if fn.endswith(".txt"):
            shutil.copy2(os.path.join(pre_dir, fn), os.path.join(dst_pre, fn))

    clean_category(os.path.join(FL, "category.txt"), os.path.join(dst_fl, "category.txt"))
    for d in EMPTY_DIRS:
        os.makedirs(os.path.join(dst_fl, d), exist_ok=True)
        keep = io.open(os.path.join(dst_fl, d, ".gitkeep"), "w")
        keep.close()

    # 文档
    # 微信分发场景不带 docs/（md 小白打不开）；使用说明.txt 已在根目录
    for fn in DOC_FILES:
        shutil.copy2(os.path.join(ROOT, fn), os.path.join(stage, fn))

    # ---- 安全断言：包里不能有真实日志 ----
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}.*\.txt$")
    leaked = []
    for dirpath, _dirs, files in os.walk(stage):
        for fn in files:
            if pattern.match(fn):
                leaked.append(os.path.relpath(os.path.join(dirpath, fn), stage))
    if leaked:
        shutil.rmtree(stage)
        raise SystemExit("中止！包里出现了真实日志：\n  " + "\n  ".join(leaked))

    zip_path = os.path.join(OUT_DIR, name + ".zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirs, files in os.walk(stage):
            for fn in files:
                full = os.path.join(dirpath, fn)
                z.write(full, os.path.relpath(full, OUT_DIR))
    shutil.rmtree(stage)

    size = os.path.getsize(zip_path)
    print(f"[OK] 发布包: {zip_path}")
    print(f"     体积 {size/1024/1024:.1f} MB")
    print(f"     已剔除私人规则: {PRIVATE_RULES}")
    print("     安全断言: 包内无真实日志 [OK]")
    print("\n  上传方式（二选一）：")
    print("   a) gh release create v%s %s --title \"FocusLog v%s\" --notes-file CHANGELOG.md"
          % (ver, os.path.basename(zip_path), ver))
    print("      再补一个固定名副本供 更新.bat 使用: FocusLog_latest.zip")
    print("   b) 手动上传到 GitHub Release 页面")
    return 0


if __name__ == "__main__":
    sys.exit(build())
