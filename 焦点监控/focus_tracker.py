# FocusLog —— 本地时间记录（https://github.com/mniLiHua/focuslog）
# Copyright (C) 2026 冰叁狼  |  SPDX-License-Identifier: GPL-3.0-only
# 本程序按 GPL-3.0 授权：可自由使用/修改/再分发，衍生作品须同协议开源。
# -*- coding: utf-8 -*-
"""
窗口焦点监控脚本  v2.7
每10秒采样一次当前活跃窗口（进程名 + 标题），写入当天日期txt文件
支持鼠标空闲自动暂停：鼠标5分钟不动且焦点不在视频类窗口时自动暂停记录
支持锁屏/休眠自动暂停：锁屏瞬间即停表，解锁后自动恢复并写日志标记
支持专注时长统计：根据 category.txt 中 #专注# 标记的分类判断是否专注
支持里程碑通知：专注时长达到阈值时弹出系统通知
实时更新 today_focus.txt：文件名包含专注时长，桌面一眼可见
用 pythonw.exe 运行无窗口后台，手动停止

v2.6 新增（在 v2.5 基础上）：
  · 日志「点压缩」——同一窗口连续采样只记一行 + 省略号，日志体积降约 85%
  · 隐私脱敏 —— 命中小抄表窗口（密码管理器/聊天软件等）的标题替换为 [已脱敏]

v2.7 新增（在 v2.6 基础上）：
  · 锁屏 / 休眠自动暂停 —— OpenInputDesktop 探测，锁屏即停表，解锁即复表
  · 打包为无控制台窗口（WINDOWS_GUI），后台运行不弹黑框
"""

import os
import sys
import io
import json
import ctypes
import ctypes.wintypes
import time
import traceback
import contextlib
import datetime
import atexit
import psutil
from category_utils import load_categories, is_focus_window
import backup_utils
import backup_utils

# ============ 配置 ============
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "专注记录"))
INTERVAL_SECONDS = 10       # 采样间隔（秒）
IDLE_TIMEOUT = 300          # 鼠标空闲超时（秒），超过此时间无鼠标移动则暂停
VIDEO_IDLE_TIMEOUT = 2700   # 视频/电影窗口的空闲超时（秒）= 45 分钟（v2.7.2：原 120 分钟会虚增在线时长）
# 视频类判断规则：进程名 或 标题关键词（匹配任一则不暂停）
VIDEO_PROCS = {"mplayerc.exe", "vlc.exe"}
VIDEO_TITLE_KEYWORDS = ("bilibili", "哔哩哔哩", "youtube", "优酷", "爱奇艺", "腾讯视频", "芒果tv", "netflix", "twitch")
# 专注里程碑（分钟, 提示语）
MILESTONES = [
    (60,  "专注1小时达成！"),
    (180, "专注3小时达成！大神！"),
    (300, "专注5小时达成！神仙！"),
]
# ============ 目标与提醒（v2.12 新增） ============
GOALS_FILE = os.path.join(LOG_DIR, "goals.json")
DEFAULT_GOALS = {"daily_focus_min": 360, "app_limits_min": {}, "notify": True}

# 锁文件（用于替代进程遍历检测重复运行）
LOCK_FILE = os.path.join(LOG_DIR, "focus_tracker.lock")
# 状态文件
STATE_FILE = os.path.join(LOG_DIR, "focus_state.json")
# 旧日期归档目录
ARCHIVE_DIR = os.path.join(LOG_DIR, "深耕记录")

# ============ 隐私脱敏配置（v2.6 新增） ============
# 命中以下任一条件的窗口，标题一律写成 [已脱敏]，避免聊天内容/账号泄露到日志
PRIVACY_PROCS = {
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
    "lastpass.exe", "dashlane.exe", "signal.exe", "telegram.exe",
    "whatsapp.exe", "putty.exe", "securecrt.exe", "xshell.exe",
}
PRIVACY_TITLE_KEYWORDS = (
    "密码", "password", "登录密码", "银行", "支付", "钱包", "验证码",
    "无痕", "隐私", "私密",
)
REDACTED = "[已脱敏]"

# ============ 点压缩配置（v2.6 新增） ============
# 同一窗口连续采样时，后续条目写为 "HH:MM:SS|." 而不是完整行
DOT = "."


def is_video_active(proc, title):
    """判断当前是否在观看视频（视频类不触发空闲暂停）"""
    if proc.lower() in VIDEO_PROCS:
        return True
    title_lower = title.lower()
    return any(kw in title_lower for kw in VIDEO_TITLE_KEYWORDS)


def should_redact(proc, title):
    """判断该窗口标题是否需要脱敏（v2.6 新增）"""
    if proc.lower() in PRIVACY_PROCS:
        return True
    title_lower = title.lower()
    return any(kw in title_lower for kw in PRIVACY_TITLE_KEYWORDS)


def get_cursor_pos():
    """获取当前鼠标坐标"""
    point = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


# ============ 锁屏 / 休眠检测（v2.7 新增） ============
# 原理：
#   1) OpenInputDesktop  —— 只有当前会话真的对着「输入桌面」时才成功。
#      一旦锁屏（Win+L）或切到 UAC 安全桌面，本进程的调用会失败 → 立刻判定锁屏。
#      这是事件级判据，不用等超时，锁屏那一秒就能捕获。
#   2) GetLastInputInfo   —— 返回系统全局最后一次输入事件的时间戳（含锁屏界面上的
#      键盘鼠标）。解锁瞬间会有输入，时间戳会前进 → 用它确认「已恢复」。
#   3) 休眠/睡眠回来 —— OpenInputDesktop 恢复成功即可，无需单独判断。
# 相比原方案（纯鼠标坐标 + 5 分钟空闲超时）的优势：
#   · 锁屏后立刻停止计时，不会把「锁屏挂机」算成在线时长
#   · 锁屏时鼠标本来就不动，原方案要白等 5 分钟才暂停 → 这 5 分钟是虚增的
#   · 解锁后立即恢复，无需等鼠标移动
DESKTOP_READOBJECTS = 0x0001
LOCK_PROBE_INTERVAL = 1        # 锁屏状态探测缓存有效期（秒），避免每轮都调 API


def is_locked():
    """检测当前是否处于锁屏 / 安全桌面状态（v2.7 新增）

    返回 True 表示锁屏中（应暂停记录）
    """
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not hdesk:
            # 打不开输入桌面 → 锁屏 / UAC 安全桌面 / 切换用户
            return True
        user32.CloseDesktop(hdesk)
        return False
    except Exception:
        # 任何异常都不应影响主循环 → 保守认为没锁屏
        return False


def get_last_input_ms():
    """获取距上次系统输入事件的毫秒数（v2.7 新增）

    锁屏界面上敲键盘、动鼠标也算输入，所以解锁瞬间该值会归零。
    """
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.wintypes.UINT),
                        ("dwTime", ctypes.wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return -1
        tick = ctypes.windll.kernel32.GetTickCount()
        return tick - info.dwTime
    except Exception:
        return -1



def get_active_window():
    """获取当前活跃窗口的进程名和标题"""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()

    # 获取窗口标题
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
    else:
        title = "(无标题)"

    # 获取进程名
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value)
        proc_name = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        proc_name = "(未知进程)"

    return proc_name, title


def get_today_file():
    """获取当天日志文件的路径"""
    today = datetime.date.today()
    filename = f"{today.strftime('%Y-%m-%d')}.txt"
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, filename)


def write_log(log_file, content):
    """写入一条日志"""
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(content)


def format_log_line(time_str, proc, title, last_key):
    """生成一条日志行（v2.6 点压缩）

    Args:
        last_key: 上一条已写入的 (proc, title)，None 表示文件刚开或刚恢复
    返回 (要写入的文本, 本条对应的 key)
    """
    key = (proc, title)
    if last_key == key:
        # 同一窗口连续采样 → 只写时间戳 + 点
        return f"{time_str}|{DOT}\n", key
    return f"{time_str}|{proc}|{title}\n", key


def format_duration_short(seconds):
    """格式化时长（简短版，用于文件名）"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        m = int(seconds / 60)
        return f"{m}min"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) / 60)
        if m == 0:
            return f"{h}h"
        return f"{h}h{m}min"


def format_duration_display(seconds):
    """格式化时长（展示版，用于文件内容）"""
    if seconds < 60:
        return f"{int(seconds)}秒"
    elif seconds < 3600:
        m = int(seconds / 60)
        return f"{m}分钟"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) / 60)
        if m == 0:
            return f"{h}小时"
        return f"{h}小时{m}分钟"


def load_state():
    """加载专注状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def save_state(state):
    """保存专注状态"""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_today_str():
    """获取今天的日期字符串"""
    return datetime.date.today().strftime("%Y-%m-%d")


def get_focus_filename(focus_seconds, total_seconds):
    """生成专注时长文件名"""
    return f"专注{format_duration_short(focus_seconds)}_在线{format_duration_short(total_seconds)}.txt"


def find_all_focus_files():
    """找到所有专注时长文件（匹配 专注*在线*.txt 模式）"""
    if not os.path.exists(LOG_DIR):
        return []
    return [
        os.path.join(LOG_DIR, f)
        for f in os.listdir(LOG_DIR)
        if f.startswith("专注") and "在线" in f and f.endswith(".txt")
    ]


def update_focus_file(focus_seconds, total_seconds, date_str):
    """更新专注时长文件（内容+文件名）"""
    # 确保目录存在
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    new_name = get_focus_filename(focus_seconds, total_seconds)
    new_path = os.path.join(LOG_DIR, new_name)

    # 写入内容
    now_str = datetime.datetime.now().strftime("%H:%M")
    content = f"专注 {format_duration_display(focus_seconds)} | 在线 {format_duration_display(total_seconds)} | 更新于 {now_str}\n"

    with open(new_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 删除所有旧文件（文件名不同的都删，确保只保留最新的一个）
    for old_path in find_all_focus_files():
        if old_path != new_path:
            try:
                os.remove(old_path)
            except OSError:
                pass


def archive_old_focus_file(date_str, focus_seconds, total_seconds):
    """将旧日期的专注文件归档到深耕记录目录"""
    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR, exist_ok=True)

    # 归档文件名带日期
    archive_name = f"{date_str}_专注{format_duration_short(focus_seconds)}_在线{format_duration_short(total_seconds)}.txt"
    archive_path = os.path.join(ARCHIVE_DIR, archive_name)

    # 写入归档内容
    content = f"日期: {date_str}\n专注: {format_duration_display(focus_seconds)}\n在线: {format_duration_display(total_seconds)}\n"
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 删除当前目录的所有专注文件
    for old_path in find_all_focus_files():
        try:
            os.remove(old_path)
        except OSError:
            pass


def check_milestones(focus_seconds, state):
    """检查专注里程碑，达标时弹通知并标记"""
    for minutes, message in MILESTONES:
        key = f"milestone_{minutes}"
        if key not in state:
            state[key] = False
        if focus_seconds >= minutes * 60 and not state[key]:
            state[key] = True
            # 弹出自动消失的通知
            try:
                ctypes.windll.user32.MessageBoxTimeoutW(
                    0, message, "深耕里程碑", 0x40, 0, 10000
                )
            except Exception as e:
                # 静默失败曾经让「自动化到底跑没跑」无从判断 —— 这里必须留痕
                import traceback as _tb
                write_log(log_file, f"{time_str}|[MAINTAIN-FAIL]|{type(e).__name__}: {e}\n")
                _last_maintain_err = _tb.format_exc().strip().splitlines()[-1]
                write_log(log_file, f"{time_str}|[MAINTAIN-FAIL]|{_last_maintain_err}\n")



def load_goals():
    r"""读 专注记录\goals.json（缺失就建默认；坏了就退回默认，绝不因此中断记录）"""
    try:
        if not os.path.exists(GOALS_FILE):
            with open(GOALS_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_GOALS, f, ensure_ascii=False, indent=2)
            return dict(DEFAULT_GOALS)
        with open(GOALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = dict(DEFAULT_GOALS)
        out.update(data or {})
        return out
    except Exception:                                        # noqa: BLE001
        return dict(DEFAULT_GOALS)


def notify_user(title, message):
    """系统通知：自动消失的消息框（与里程碑通知同一套）"""
    try:
        ctypes.windll.user32.MessageBoxTimeoutW(0, message, title, 0x40, 0, 12000)
    except Exception:                                        # noqa: BLE001
        pass


def parse_today_apps():
    """轻量解析今天的日志，返回 {进程: 秒}（只在配了应用上限时才算，省开销）"""
    path = get_today_file()
    if not os.path.exists(path):
        return {}
    out = {}
    last = None
    last_key = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "|" not in line:
                continue
            parts = line.split("|", 2)
            try:
                ts = datetime.datetime.strptime(parts[0], "%H:%M:%S")
            except ValueError:
                continue
            if len(parts) == 2 and parts[1].strip() == ".":
                if last_key is None:
                    continue
                proc = last_key
            elif len(parts) == 3 and not (parts[1].startswith("[") and parts[1].endswith("]")):
                proc = parts[1]
                last_key = proc
            else:
                last_key = None            # 标记行（[LOCK]/[PAUSE]…）断开上下文
                last = ts
                continue
            if last is not None:
                diff = (ts - last).total_seconds()
                if 0 <= diff <= 60:
                    out[proc] = out.get(proc, 0) + diff
            last = ts
    return out


def check_goals(focus_seconds, state, goals):
    """达标提醒 + 单应用超限提醒（每项每天只提醒一次）"""
    if not goals.get("notify", True):
        return
    goal_min = goals.get("daily_focus_min") or 0
    try:
        goal_min = int(goal_min)
    except (TypeError, ValueError):
        goal_min = 0
    if goal_min > 0 and focus_seconds >= goal_min * 60 and not state.get("goal_notified"):
        state["goal_notified"] = True
        notify_user("今日目标达成",
                    f"专注已到 {goal_min // 60} 小时{goal_min % 60} 分，漂亮！\n\n"
                    "（不想要这类提醒：面板 → ⚙ 工具 → 系统设置 → 关掉「弹窗提醒」）")

    limits = goals.get("app_limits_min") or {}
    if not isinstance(limits, dict) or not limits:
        return
    apps = parse_today_apps()
    done = state.setdefault("app_notified", {})
    for proc, mins in limits.items():
        try:
            mins = int(mins)
        except (TypeError, ValueError):
            continue
        if mins <= 0 or done.get(proc) or str(proc).startswith("_"):
            continue
        sec = 0
        low = str(proc).lower()
        for name, s in apps.items():
            if name.lower() == low:
                sec = s
                break
        if sec >= mins * 60:
            done[proc] = True
            notify_user("用时提醒",
                        f"{proc} 今天已用 {int(sec // 3600)}h{int(sec % 3600 // 60)}min"
                        f"（设定上限 {mins} 分钟）\n\n"
                        "（不想要这类提醒：面板 → ⚙ 工具 → 系统设置 → 关掉「弹窗提醒」）")

def run_daily_maintenance(log_file, time_str):
    """跨天自动维护：归档 → 重算深耕 → 导出统计（+周一自动周报）

    v3.1.0：改为派发独立 summary 进程（--maintain）跑完即退。
    为什么：此前在本进程内 import summary 全家（含图表/统计），只为一年
    几次的维护就常驻 ~25MB 内存；子进程化后常驻内存立减。
    输出落 专注记录\maintenance.log；失败不影响记录。
    """
    try:
        if getattr(sys, "frozen", False):
            m_exe = os.path.normpath(os.path.join(
                os.path.dirname(os.path.abspath(sys.executable)),
                "..", "专注记录", "summary.exe"))
            cmd = [m_exe, "--maintain"]
        else:
            cmd = [sys.executable,
                   os.path.normpath(os.path.join(LOG_DIR, "summary.py")),
                   "--maintain"]
        mlog = open(os.path.join(LOG_DIR, "maintenance.log"), "a", encoding="utf-8")
        mlog.write(f"\n===== {time_str} 跨天维护 =====\n")
        subprocess.Popen(cmd, cwd=LOG_DIR, stdout=mlog, stderr=subprocess.STDOUT)
        mlog.close()
        write_log(log_file, f"{time_str}|[MAINTAIN]|跨天维护已派发后台进程\n")
    except Exception as e:                                   # noqa: BLE001
        write_log(log_file, f"{time_str}|[MAINTAIN]|派发失败 {e}\n")
    # v3.0.0：每周自动备份（backup_config.json 里 enabled 时）
    try:
        msg = backup_utils.run_if_due()
        if msg:
            write_log(log_file, f"{time_str}|{msg}\n")
    except Exception:                                        # noqa: BLE001
        pass

def main():
    """主循环，含鼠标空闲自动暂停、专注统计、里程碑、实时文件更新"""
    # 加载专注分类规则（只用带 #专注# 的）
    category_file = os.path.join(LOG_DIR, "category.txt")
    focus_categories = load_categories(category_file, only_focus=True)

    # 目标与提醒（goals.json 改了会在一分钟内自动生效）
    goals = load_goals()
    try:
        goals_mtime = os.path.getmtime(GOALS_FILE)
    except OSError:
        goals_mtime = 0

    # 加载或初始化状态
    state = load_state()
    today_str = get_today_str()

    if state is None or state.get("date") != today_str:
        # 新的一天或首次运行：归档旧文件
        if state is not None and state.get("date") and state.get("date") != today_str:
            archive_old_focus_file(
                state["date"],
                state.get("focus_seconds", 0),
                state.get("total_seconds", 0)
            )
            # ⚠️ 开机自启走的就是这条路径（昨天关掉、今早启动）——
            #    维护必须在这里也跑一次，否则最常见的场景不触发自动化。
            run_daily_maintenance(get_today_file(),
                                  datetime.datetime.now().strftime("%H:%M:%S"))
        state = {
            "date": today_str,
            "focus_seconds": 0,
            "total_seconds": 0,
            "goal_notified": False,
            "app_notified": {},
        }
        for minutes, _ in MILESTONES:
            state[f"milestone_{minutes}"] = False

    focus_seconds = state["focus_seconds"]
    total_seconds = state["total_seconds"]

    # 鼠标空闲检测
    last_cursor = get_cursor_pos()
    idle_seconds = 0
    paused = False
    pause_cursor = None
    buffer = []
    buffer_sec = 0
    buffer_focus_sec = 0
    buffer_size_limit = IDLE_TIMEOUT // INTERVAL_SECONDS

    # v2.7 锁屏检测：锁定状态独立于鼠标空闲，锁屏时优先级更高
    locked = False
    lock_pause_started = None         # 锁屏暂停的起始时间（写日志用）

    # 上次保存状态的时间（每分钟保存一次，避免频繁IO）
    last_save_time = time.time()

    # v2.7.2：真实计时用的上一轮时间戳；暂停起点（写 [PAUSE]/[RESUME] 用）
    last_tick = time.time()
    pause_started = None

    # v2.15.1：双实例自检节流（启动时清理过一次，5 分钟后再兜底查）
    last_dup_check = time.time()
    _dup_notified_day = None

    # v2.6 点压缩：记录日志文件里最后写入的窗口 key
    last_log_key = None

    def flush_buffer(buf, log_file):
        """将缓冲区全部写入日志文件"""
        if buf:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("".join(buf))
            buf.clear()

    while True:
        now = datetime.datetime.now()
        cursor = get_cursor_pos()
        time_str = now.strftime("%H:%M:%S")
        log_file = get_today_file()

        # v2.7.2：按真实经过时间计时。旧版每轮固定 +10s，循环本身耗时与系统卡顿
        # 会让在线时长系统性偏慢；这里取真实 delta，并对异常跳变（休眠/长时间卡顿）
        # 做上限保护 —— 那种情况本该由 [LOCK]/[PAUSE] 隔断，不该补成大段时长。
        now_epoch = time.time()
        delta = now_epoch - last_tick
        if delta <= 0 or delta > INTERVAL_SECONDS * 6:
            delta = INTERVAL_SECONDS
        last_tick = now_epoch

        # v2.15.1：每 5 分钟自检一次旧版记录程序并行（详见 _check_duplicate_trackers）
        if now_epoch - last_dup_check > 300:
            last_dup_check = now_epoch
            try:
                _check_duplicate_trackers(log_file)
            except Exception:                                # 自检绝不影响计时
                pass

        # v2.7.2：活跃判据 = 鼠标位移 或 系统有键鼠输入。
        # 旧版只看鼠标坐标 ⇒ 纯键盘工作（打字、读文档）5 分钟就被判成离开并丢弃时长。
        idle_ms = get_last_input_ms()
        is_active = (cursor != last_cursor) or (0 <= idle_ms < IDLE_TIMEOUT * 1000)

        # ===== v2.7 锁屏 / 休眠检测（优先级高于鼠标空闲）=====
        # 锁屏时 GetForegroundWindow 拿不到有意义的结果，直接跳过采样。
        now_locked = is_locked()
        if now_locked:
            if not locked:
                # 刚进入锁屏：丢弃未结算的缓冲区，标记停表
                locked = True
                lock_pause_started = now
                paused = False
                pause_cursor = None
                idle_seconds = 0
                buffer.clear()
                buffer_sec = 0
                buffer_focus_sec = 0
                last_log_key = None
                write_log(log_file, f"{time_str}|[LOCK]|锁屏暂停\n")
            # 锁屏期间不采样、不计时、不写点
            time.sleep(INTERVAL_SECONDS)
            continue
        else:
            if locked:
                # 刚解锁：恢复计时并写标记
                locked = False
                lock_pause_started = None
                idle_seconds = 0
                last_cursor = cursor
                last_log_key = None
                write_log(log_file, f"{time_str}|[UNLOCK]|解锁恢复\n")
                # 本轮正常继续往下走（下面会写一条完整行）

        proc, title = get_active_window()

        # 检测日期变化
        current_date = get_today_str()
        if current_date != state["date"]:
            # 新的一天：归档旧文件，重置状态
            archive_old_focus_file(state["date"], focus_seconds, total_seconds)
            state["date"] = current_date
            focus_seconds = 0
            total_seconds = 0
            state["focus_seconds"] = 0
            state["total_seconds"] = 0
            state["goal_notified"] = False
            state["app_notified"] = {}
            for minutes, _ in MILESTONES:
                state[f"milestone_{minutes}"] = False
            save_state(state)
            update_focus_file(focus_seconds, total_seconds, current_date)
            last_log_key = None
            # v2.9.2：跨天自动维护（与「启动时发现跨天」共用同一份实现）
            run_daily_maintenance(log_file, time_str)

        # v2.6 隐私脱敏：标题命中敏感规则时替换
        safe_title = REDACTED if should_redact(proc, title) else title

        if paused:
            # 暂停中：有鼠标移动或键鼠输入 → 恢复
            if is_active:
                paused = False
                idle_seconds = 0
                last_cursor = cursor
                buffer.clear()
                buffer_sec = 0
                buffer_focus_sec = 0
                last_log_key = None
                gap = (now - pause_started).total_seconds() if pause_started else 0
                gap_txt = f"（暂停 {format_duration_short(gap)}）" if gap >= 60 else ""
                write_log(log_file, f"{time_str}|[RESUME]|自动恢复{gap_txt}\n")
                line, last_log_key = format_log_line(time_str, proc, safe_title, last_log_key)
                write_log(log_file, line)
        else:
            # 正常运行中：无鼠标移动且无键鼠输入才算空闲
            if not is_active:
                idle_seconds += delta
                # 鼠标没动：先判断是否触发暂停
                if is_video_active(proc, title):
                    # 视频类窗口：用更长的超时
                    if idle_seconds >= VIDEO_IDLE_TIMEOUT:
                        paused = True
                        pause_cursor = cursor
                        pause_started = now
                        buffer.clear()
                        buffer_sec = 0
                        buffer_focus_sec = 0
                        write_log(log_file, f"{time_str}|[PAUSE]|视频窗口内长时间无输入\n")
                    else:
                        line, last_log_key = format_log_line(time_str, proc, safe_title, last_log_key)
                        buffer.append(line)
                        buffer_size_limit_video = VIDEO_IDLE_TIMEOUT // INTERVAL_SECONDS
                        if len(buffer) > buffer_size_limit_video:
                            buffer = buffer[-buffer_size_limit_video:]
                        buffer_sec += delta
                        if is_focus_window(proc, title, focus_categories):
                            buffer_focus_sec += delta
                else:
                    if idle_seconds >= IDLE_TIMEOUT:
                        # 空闲超时且非视频类 → 进入暂停，丢弃缓冲区
                        paused = True
                        pause_cursor = cursor
                        pause_started = now
                        buffer.clear()
                        buffer_sec = 0
                        buffer_focus_sec = 0
                        write_log(log_file,
                                  f"{time_str}|[PAUSE]|无键鼠输入 {IDLE_TIMEOUT // 60} 分钟\n")
                    else:
                        # 还没超时：写入缓冲区暂存
                        line, last_log_key = format_log_line(time_str, proc, safe_title, last_log_key)
                        buffer.append(line)
                        if len(buffer) > buffer_size_limit:
                            buffer = buffer[-buffer_size_limit:]
                        buffer_sec += delta
                        if is_focus_window(proc, title, focus_categories):
                            buffer_focus_sec += delta
            else:
                # 鼠标动了：先刷缓冲区（之前的记录安全），再结算时长
                idle_seconds = 0
                last_cursor = cursor
                flush_buffer(buffer, log_file)

                total_seconds += buffer_sec
                focus_seconds += buffer_focus_sec
                buffer_sec = 0
                buffer_focus_sec = 0

                line, last_log_key = format_log_line(time_str, proc, safe_title, last_log_key)
                write_log(log_file, line)

                total_seconds += delta
                if is_focus_window(proc, title, focus_categories):
                    focus_seconds += delta

        # 更新状态（每分钟保存一次）
        now_time = time.time()
        if now_time - last_save_time >= 60:
            state["focus_seconds"] = focus_seconds
            state["total_seconds"] = total_seconds
            check_milestones(focus_seconds, state)
            try:                       # goals.json 被改动就热重载
                mt = os.path.getmtime(GOALS_FILE)
                if mt != goals_mtime:
                    goals = load_goals()
                    goals_mtime = mt
            except OSError:
                pass
            check_goals(focus_seconds, state, goals)
            save_state(state)
            update_focus_file(focus_seconds, total_seconds, state["date"])
            last_save_time = now_time

        time.sleep(INTERVAL_SECONDS)


def acquire_lock():
    """文件锁机制检测是否已运行。成功返回True（表示是唯一实例），失败返回False。

    使用 os.open + O_EXCL 实现原子性创建，比 psutil 进程遍历更可靠，
    不存在进程刚退出时的 race condition。
    """
    lock_file = LOCK_FILE
    try:
        # 尝试独占创建（O_EXCL + O_CREAT 原子操作）
        fd = os.open(lock_file, os.O_CREAT | os.O_WRONLY | os.O_EXCL)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        _write_startup_log(f"Lock acquired: PID={os.getpid()}")
        return True
    except FileExistsError:
        # 锁文件已存在：读取 PID 检查旧进程是否还活着
        _write_startup_log("Lock file exists, checking old PID...")
        old_pid = _read_lock_pid(lock_file)
        if old_pid is not None and _is_process_alive(old_pid):
            _write_startup_log(f"  Old PID={old_pid} is still alive, another instance running")
            return False
        # 旧进程已死：清理锁文件，重新尝试
        _write_startup_log(f"  Old PID={old_pid} is dead, reclaiming lock...")
        try:
            os.remove(lock_file)
        except OSError:
            pass
        # 重试一次
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_WRONLY | os.O_EXCL)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            _write_startup_log(f"Lock re-acquired: PID={os.getpid()}")
            return True
        except OSError:
            _write_startup_log("  Failed to re-acquire lock, falling back to process scan")
            return _fallback_check()
    except OSError:
        # 其他错误（权限等）：回退到进程遍历
        _write_startup_log("Lock file error, falling back to process scan")
        return _fallback_check()


def _read_lock_pid(lock_file):
    """安全读取锁文件中的 PID"""
    try:
        with open(lock_file, "r") as f:
            return int(f.read().strip())
    except (ValueError, OSError, IOError):
        return None


def _is_process_alive(pid):
    """检查指定 PID 的进程是否存活且是 focus_tracker"""
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        return proc.is_running() and ("focus_tracker" in name or "python" in name)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _fallback_check():
    """回退方案：通过 psutil 进程遍历检测其他 focus_tracker 实例"""
    current_pid = os.getpid()
    current_name = os.path.basename(sys.executable).lower()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == current_pid:
                continue
            name = proc.info.get("name") or ""
            name_lower = name.lower()
            if name_lower in ("python.exe", "pythonw.exe", current_name):
                cmdline = proc.info.get("cmdline") or []
                if any("focus_tracker" in arg.lower() for arg in cmdline):
                    _write_startup_log(f"  Fallback found: PID={proc.info['pid']}")
                    return False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return True


def cleanup_lock():
    """退出时删除锁文件"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except OSError:
        pass


def _write_startup_log(msg):
    """写入启动日志"""
    try:
        log_path = os.path.join(LOG_DIR, "startup.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def _check_duplicate_trackers(log_file):
    """运行中自检：是否有别的 focus_tracker.exe 在同时记录（v2.15.1）

    背景：单实例锁是【按目录】的 —— 旧版（含双文件夹版）和新版各有一把锁文件，
    互相拦不住，会同时写两份日志（在线时长翻倍、深耕记录互相踩）。
    「工具\清理旧版.ps1」在启动时拦一次；这里是运行中的兜底：
    每 5 分钟扫一次进程表，发现非当前路径的实例就结束它，并把它挂的自启项
    纠偏到当前版本（或删除）。与清理 ps1 逻辑一致，两处修改需同步。

    只处理"确认不是当前 exe"的实例，绝不误杀自己或正常副本。
    """
    try:
        import psutil
        import winreg
    except ImportError:                                      # 打包环境缺库时静默跳过
        return

    me = os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                         else __file__).lower()
    dupes = []
    for pr in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if (pr.info.get("name") or "").lower() != "focus_tracker.exe":
                continue
            exe = pr.info.get("exe") or ""
            if exe and os.path.abspath(exe).lower() != me:
                dupes.append((pr, exe))
        except Exception:                                    # noqa: BLE001
            continue
    if not dupes:
        return

    paths = []
    for pr, exe in dupes:
        try:
            pr.kill()
            paths.append(exe)
        except Exception:                                    # noqa: BLE001
            pass
    if paths:
        try:
            write_log(log_file, f"{time.strftime('%H:%M:%S')}|[DUP]|"
                                f"检测到旧版记录程序并行，已结束: {'; '.join(paths)}\n")
        except Exception:                                    # noqa: BLE001
            pass
        try:
            notify_user("检测到旧版记录程序",
                        "有另一个旧版打卡程序在和当前版本同时记录，已自动关闭它，"
                        "并纠正了开机自启。\n\n（本条提示每天只弹一次）")
        except Exception:                                    # noqa: BLE001
            pass

    # 自启纠偏/清理（与 工具\清理旧版.ps1 同逻辑）
    try:
        run_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_path, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            i, to_fix, to_del = 0, [], []
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                v = str(val)
                if "focus_tracker.exe" not in v.lower():
                    continue
                target = v.strip('"').split(" --")[0]
                if os.path.abspath(target).lower() == me:
                    continue                                  # 指向当前版本，合法
                (to_fix if name == "FocusTracker" else to_del).append((name, v))
            for name, val in to_del:
                try:
                    winreg.DeleteValue(key, name)
                    write_log(log_file, f"{time.strftime('%H:%M:%S')}|[DUP]|"
                                        f"已删除旧版自启项 {name} -> {val}\n")
                except OSError:
                    pass
            if to_fix:
                winreg.SetValueEx(key, "FocusTracker", 0, winreg.REG_SZ,
                                  sys.executable if getattr(sys, "frozen", False)
                                  else os.path.abspath(__file__))
                write_log(log_file, f"{time.strftime('%H:%M:%S')}|[DUP]|"
                                    f"已把 FocusTracker 自启项纠正到当前版本\n")
    except Exception:                                        # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        if not acquire_lock():
            sys.exit(0)
        atexit.register(cleanup_lock)
        _write_startup_log("Entering main loop")
        main()
    except Exception as e:
        try:
            with open(os.path.join(LOG_DIR, "crash.log"), "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        finally:
            cleanup_lock()
