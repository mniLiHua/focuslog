# -*- coding: utf-8 -*-
r"""自动备份共享模块  v3.0.0 —— 作者 冰叁狼

    tracker 每天跨天时调 run_if_due()（到期自动备份到指定目录）；
    面板「立即备份」调 zip_now()（手动，备到默认 _备份 目录）。
    配置：专注记录\backup_config.json
      {"enabled": true, "dir": "E:\\备份\\focuslog", "interval_days": 7, "last_ts": 0}
"""
import io
import json
import os
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "专注记录"))
CFG = os.path.join(DATA, "backup_config.json")
EXCLUDE = ("__pycache__", "_备份", "_build", "_release", "导出")


def load_cfg():
    try:
        return json.load(io.open(CFG, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {"enabled": False, "dir": "", "interval_days": 7, "last_ts": 0}


def save_cfg(cfg):
    json.dump(cfg, io.open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def default_dir():
    return os.path.join(DATA, "_备份")


def _prune(dst_dir, keep=10):
    zips = sorted(f for f in os.listdir(dst_dir) if f.lower().endswith(".zip"))
    for old in zips[:-keep]:
        try:
            os.remove(os.path.join(dst_dir, old))
        except OSError:
            pass


def zip_now(dst_dir=None):
    """打包数据（日志/深耕/统计/分类），返回 (绝对路径, 文件数, 字节数)"""
    dst_dir = dst_dir or default_dir()
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, f"focuslog_数据备份_{time.strftime('%Y%m%d_%H%M%S')}.zip")
    n = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(DATA):
            dirs[:] = [d for d in dirs if d not in EXCLUDE]
            for fn in files:
                if fn.lower().endswith((".zip", ".exe", ".pyc", ".spec")):
                    continue
                fp = os.path.join(root, fn)
                z.write(fp, os.path.relpath(fp, DATA))
                n += 1
    _prune(dst_dir)
    return dst, n, os.path.getsize(dst)


def run_if_due():
    """tracker 跨天时调用：到期自动备份。返回日志文本（无需备份返回 ''）"""
    cfg = load_cfg()
    if not cfg.get("enabled") or not str(cfg.get("dir") or "").strip():
        return ""
    if time.time() - float(cfg.get("last_ts") or 0) < \
            float(cfg.get("interval_days") or 7) * 86400:
        return ""
    try:
        dst, n, size = zip_now(str(cfg["dir"]))
        cfg["last_ts"] = time.time()
        save_cfg(cfg)
        return f"[BACKUP]|每周自动备份 {n} 个文件（{size // 1024}KB）-> {dst}"
    except Exception as e:                                   # noqa: BLE001
        return f"[BACKUP]|自动备份失败: {type(e).__name__}: {e}"
