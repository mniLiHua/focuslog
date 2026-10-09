# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
r"""发版流水线  v3.2.0
    python 工具\发版.py            # 本地：检查 -> 打包 -> 复验 -> 提交+打tag（不推送）
    python 工具\发版.py --push    # 上面全部 + 推送 + 创建 GitHub Release（需用户点头）

替代"每次在临时目录拼发版脚本"。步骤与防呆：
  1. VERSION.txt 与 CHANGELOG.md 必须有本版本条目（没有 = 拒绝发版）
  2. README 徽章必须是动态 shields（出现 version-X.Y.Z 硬编码 = 拒绝）
  3. 跑 工具\检查面板.py（JS 语法/挂载/主题）
  4. 工具\make_release.py 打包
  5. zip 结构复验：顶层清单、关键文件（分类方案/清理旧版.ps1/更新.ps1/category.txt）、
     无 md、无真实日志
  6. latest 副本 + git 提交 + tag vX.Y.Z（幂等：已存在则跳过）
  7. --push 时：git push + gh release create（notes 自动取 CHANGELOG 本版本段）
"""

import io
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUSH = "--push" in sys.argv


def sh(cmd, cwd=ROOT, timeout=900):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=True, timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def read_version():
    return io.open(os.path.join(ROOT, "VERSION.txt"), encoding="utf-8-sig").read() \
        .replace("\ufeff", "").strip() or "0.0.0"


def changelog_section(ver):
    """取 CHANGELOG 里 ## vX.Y.Z 段（到下一个 ## 或文件尾）"""
    txt = io.open(os.path.join(ROOT, "docs", "CHANGELOG.md"), encoding="utf-8").read()
    m = re.search(r"(^## v%s\b.*?)(?=^## |\Z)" % re.escape(ver), txt,
                  re.M | re.S)
    return m.group(1).strip() if m else ""


def main():
    ver = read_version()
    tag = "v" + ver
    print(f"== FocusLog 发版 {tag}  {'（含推送）' if PUSH else '（仅本地）'} ==")

    # 1) CHANGELOG 必须有本版本条目
    sec = changelog_section(ver)
    if not sec:
        raise SystemExit(f"[X] docs/CHANGELOG.md 里没有「## v{ver}」条目 —— 先写再发")
    print("[1] CHANGELOG 有本版本条目 ✓")

    # 2) README 徽章必须动态（跟 Release 走），不许手写版本号
    rd = io.open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    if re.search(r"badge/version-\d+\.\d+\.\d+", rd):
        raise SystemExit("[X] README 徽章还是手写版本号 —— 改成 "
                         "img.shields.io/github/v/release/mniLiHua/focuslog")
    print("[2] README 徽章动态 ✓")

    # 3) 发版前自检（检查面板.py 用 [X] 标失败，别只看退出码）
    code, out = sh('python "工具/检查面板.py"')
    if code != 0 or "❌" in out or "\n  [X]" in out or out.strip().endswith("[X]") or " [X] " in out:
        print(out[-600:])
        raise SystemExit("[X] 检查面板.py 未通过")
    print("[3] 检查面板.py 全过 ✓")

    # 4) 打包
    code, out = sh('python "工具/make_release.py"')
    z = os.path.join(ROOT, "_release", f"FocusLog_v{ver}.zip")
    if code != 0 or not os.path.exists(z):
        print(f"[debug] code={code}  z={z}  exists={os.path.exists(z)}  ver={ver!r}")
        print(out[-500:])
        raise SystemExit("[X] make_release 失败")
    print(f"[4] 打包 ✓ {os.path.getsize(z) / 1048576:.1f} MB")

    # 5) zip 结构复验
    shutil.copy2(z, os.path.join(ROOT, "_release", "FocusLog_latest.zip"))
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        pre = f"FocusLog_v{ver}/"
        tops = sorted({n[len(pre):].split("/")[0] for n in names if n.startswith(pre)})
        expect_top = {"VERSION.txt", "使用说明.txt", "打开面板.bat", "开始记录.bat",
                      "更新.bat", "专注记录", "焦点监控", "工具", "分类方案"}
        missing = expect_top - set(tops)
        if missing:
            raise SystemExit(f"[X] 包顶层缺: {missing}")
        for key in ("工具/清理旧版.ps1", "工具/更新.ps1", "工具/migrate.py",
                    "分类方案/通用版.txt", "专注记录/category.txt"):
            if pre + key not in names:
                raise SystemExit(f"[X] 包里缺 {key}")
        bad = [n for n in names if n.endswith(".md") or re.search(r"/\d{4}-\d{2}-\d{2}", n)]
        if bad:
            raise SystemExit("[X] 包里混进 md/真实日志: " + ", ".join(bad[:5]))
    print("[5] zip 结构复验 ✓（顶层齐/关键文件在/无 md 无日志）")

    # 6) git 提交 + tag（幂等）
    subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
                        text=True, encoding="utf-8", errors="replace").stdout
    if st.strip():
        first = sec.splitlines()[0].lstrip("# ").strip()
        io.open(os.path.join(ROOT, "_relmsg.txt"), "w", encoding="utf-8",
                newline="\n").write(first + "\n")
        subprocess.run(["git", "commit", "-F", "_relmsg.txt"], cwd=ROOT, capture_output=True)
        os.remove(os.path.join(ROOT, "_relmsg.txt"))
        subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: 清理临时文件"], cwd=ROOT, capture_output=True)
        print("[6] git 提交 ✓")
    else:
        print("[6] 工作区干净，无需提交")
    t = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
                       cwd=ROOT, capture_output=True)
    if t.returncode == 0:
        print(f"[6] tag {tag} 已存在，跳过")
    else:
        io.open(os.path.join(ROOT, "_t.txt"), "w", encoding="utf-8", newline="\n").write(tag + "\n")
        sh(f'git tag -a {tag} -F _t.txt')
        os.remove(os.path.join(ROOT, "_t.txt"))
        print(f"[6] tag {tag} ✓")

    # 7) 推送 + Release（必须 --push）
    if not PUSH:
        print("\n[完成] 本地已就绪。确认无误后执行：  python 工具\\发版.py --push")
        return 0

    code, out = sh("git push -f origin main --follow-tags")
    if "->" not in out:
        print(out[-300:])
        raise SystemExit("[X] git push 失败")
    print("[7] push ✓")

    if subprocess.run(["gh", "release", "view", tag], cwd=ROOT, capture_output=True).returncode == 0:
        print(f"[7] Release {tag} 已存在，跳过创建")
    else:
        notes = os.path.join(ROOT, "_relnotes.md")
        io.open(notes, "w", encoding="utf-8", newline="\n").write(sec + "\n")
        for i in range(3):
            code, out = sh(f'gh release create {tag} "{z}" '
                           f'"{os.path.join(ROOT, "_release", "FocusLog_latest.zip")}" '
                           f'--title "FocusLog {tag}" --notes-file "_relnotes.md"')
            if code == 0:
                print("[7] Release ✓", out.splitlines()[0])
                break
            print("   retry:", out[:150])
            time.sleep(8 * (i + 1))
        else:
            raise SystemExit("[X] Release 创建失败（资产可手动 gh release upload --clobber）")
        if os.path.exists(notes):
            os.remove(notes)
        subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: 清理临时文件"], cwd=ROOT, capture_output=True)
        sh("git push origin main")
    print("\n== 发版完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
