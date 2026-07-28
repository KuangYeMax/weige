"""
Windows-only WeChat automation primitives.
Conditionally imported — safe to import on macOS (raises at call time if unavailable).
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import logging

logger = logging.getLogger(__name__)


class ClipboardVerificationError(RuntimeError):
    """Raised when clipboard read-back confirms the image was NOT written."""
    pass

_PY32_OK = True
try:
    import win32gui
    import win32con
    import win32clipboard
    import win32process
    import pyautogui
except ImportError:
    _PY32_OK = False
    win32gui = win32con = win32clipboard = win32process = pyautogui = None

if pyautogui:
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.1

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None


def require_win32():
    if not _PY32_OK or sys.platform != "win32":
        raise RuntimeError(
            "微信发送仅支持 Windows。当前系统无法加载 win32 依赖。"
        )


def clip_set_text(text: str) -> None:
    for _ in range(3):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("剪贴板写入失败（文本）")


def _verify_clipboard_has_image() -> bool:
    ret = os.system(
        'powershell -Command "Add-Type -AssemblyName System.Windows.Forms; '
        'if (-not [System.Windows.Forms.Clipboard]::ContainsImage()) { exit 1 }"'
    )
    return ret == 0


def clip_set_image(path: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"图片文件不存在: {path}")
    e = path.replace("'", "''")
    for _ in range(3):
        ret = os.system(
            f'powershell -Command "Add-Type -AssemblyName System.Windows.Forms; '
            f'$img = [System.Drawing.Image]::FromFile(\'{e}\'); '
            f'[System.Windows.Forms.Clipboard]::SetImage($img); $img.Dispose()"'
        )
        if ret == 0:
            if not _verify_clipboard_has_image():
                raise ClipboardVerificationError(f"剪贴板回读校验失败，图片未写入: {path}")
            return
        time.sleep(0.3)
    raise RuntimeError(f"剪贴板写入失败（图片）: {path}")


def find_wechat_main() -> tuple[int, tuple[int, int, int, int]]:
    best = [0, 0]

    def enum(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls == "Qt51514QWindowIcon" and ("微信" in title or "Weixin" in title):
            if win32gui.IsWindowVisible(hwnd):
                r = win32gui.GetWindowRect(hwnd)
                area = (r[2] - r[0]) * (r[3] - r[1])
                if area > best[0]:
                    best[0] = area
                    best[1] = hwnd

    win32gui.EnumWindows(enum, None)
    if not best[1]:
        raise RuntimeError("未找到微信主窗口")
    return best[1], win32gui.GetWindowRect(best[1])


def hide_overlays(main_hwnd: int) -> None:
    def enum(hwnd, _):
        if hwnd == main_hwnd:
            return
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls == "Qt51514QWindowIcon" and ("微信" in title or "Weixin" in title):
            if win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

    win32gui.EnumWindows(enum, None)


def force_foreground(hwnd: int, max_retry: int = 5) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)

    for attempt in range(max_retry):
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return
        try:
            fg_tid = win32process.GetWindowThreadProcessId(fg)[0]
            my_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            win32process.AttachThreadInput(my_tid, fg_tid, True)
            win32gui.SetForegroundWindow(hwnd)
            win32process.AttachThreadInput(my_tid, fg_tid, False)
            time.sleep(0.2)
            if win32gui.GetForegroundWindow() == hwnd:
                return
        except Exception:
            pass

        user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() == hwnd:
            return

        if attempt > 2:
            pyautogui.keyDown("alt")
            pyautogui.press("tab")
            time.sleep(0.3)
            pyautogui.keyUp("alt")
            time.sleep(0.3)

    raise RuntimeError(f"无法将微信窗口置于前台 (hwnd={hwnd})")


def wait_idle(min_idle: float = 0.5, timeout: float = 10) -> None:
    from ctypes import wintypes

    LAST_INPUT_INFO = ctypes.c_ulong * 2
    lii = LAST_INPUT_INFO(ctypes.sizeof(LAST_INPUT_INFO), 0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        user32.GetLastInputInfo(lii)
        ticks_since = kernel32.GetTickCount() - lii[1]
        if ticks_since >= min_idle * 1000:
            return
        time.sleep(0.1)
    logger.warning("用户持续按键超时，仍将尝试发送")


def click(window_rect: tuple[int, int, int, int], rel_x: int, rel_y: int) -> None:
    pyautogui.click(window_rect[0] + rel_x, window_rect[1] + rel_y)


def send_key(hotkey: str) -> None:
    if "," in hotkey:
        parts = hotkey.split(",")
        pyautogui.hotkey(*[p.strip() for p in parts])
    else:
        pyautogui.press(hotkey)
