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

    # 处理窗口在屏幕外的情况（最小化到托盘/被移到 -32000）
    # IsIconic 可能返回 False 但 rect 仍在屏幕外，需主动 MoveWindow 回可见区
    try:
        r = win32gui.GetWindowRect(hwnd)
        if r[0] < -1000 or r[1] < -1000:
            logger.info("窗口在屏幕外 rect=%s，MoveWindow 恢复到 (0,0)", r)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.3)
            win32gui.MoveWindow(hwnd, 0, 0, 1225, 888, True)
            time.sleep(0.3)
    except Exception:
        logger.debug("MoveWindow 恢复失败", exc_info=True)

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


# ── 弹窗检测与恢复 ────────────────────────────────────────────


def _has_wechat_popup(main_hwnd: int) -> bool:
    """Check if WeChat has its own modal dialog open over the main window.

    WeChat dialogs share the same Qt class name but are smaller and centered
    within the main window. These block coordinate clicks but don't visibly
    steal the foreground — making them easy to miss.
    """
    try:
        main_rect = win32gui.GetWindowRect(main_hwnd)
        main_w = main_rect[2] - main_rect[0]
        main_h = main_rect[3] - main_rect[1]
        main_cx = (main_rect[0] + main_rect[2]) / 2
        main_cy = (main_rect[1] + main_rect[3]) / 2

        popups: list[int] = []

        def enum(hwnd, _):
            if hwnd == main_hwnd:
                return
            cls = win32gui.GetClassName(hwnd)
            if cls != "Qt51514QWindowIcon":
                return
            if not win32gui.IsWindowVisible(hwnd):
                return
            r = win32gui.GetWindowRect(hwnd)
            w = r[2] - r[0]
            h = r[3] - r[1]
            # WeChat dialogs are smaller ( < 80% of main ), but large enough
            # to be meaningful (> 200×100).  Centered-ness confirms it is a
            # modal overlay rather than an unrelated floating panel.
            if not (200 < w < main_w * 0.8 and 100 < h < main_h * 0.8):
                return
            cx = (r[0] + r[2]) / 2
            cy = (r[1] + r[3]) / 2
            if abs(cx - main_cx) < main_w * 0.3 and abs(cy - main_cy) < main_h * 0.3:
                popups.append(hwnd)

        win32gui.EnumWindows(enum, None)
        return len(popups) > 0
    except Exception:
        return False


def _point_hits_window(hwnd: int, screen_x: int, screen_y: int) -> bool:
    """Check that a screen point physically hits *hwnd* (or a descendant).

    Even when *hwnd* has keyboard focus (GetForegroundWindow), another
    window can sit visually on top (Z-order), causing pyautogui clicks to
    land on the wrong surface.  WindowFromPoint reveals the truth.
    """
    try:
        hit = win32gui.WindowFromPoint((screen_x, screen_y))
        if hit == hwnd:
            return True
        # Walk up the parent chain — the click might hit a child control
        # inside the main window.
        while hit:
            hit = win32gui.GetParent(hit)
            if hit == hwnd:
                return True
        return False
    except Exception:
        return True  # can't verify → assume ok (don't block on tool failure)


def ensure_wechat_clear(
    main_hwnd: int,
    popup_timeout: float = 30.0,
    check_interval: float = 2.0,
    click_point: tuple[int, int] | None = None,
) -> None:
    """确保微信窗口可用：前台 + 无阻塞弹窗 + 点击位置���视觉遮挡。

    若被弹窗或其他窗口阻塞，先尝试 ESC 关闭弹窗，失败则等待弹窗自行消失。
    超过 *popup_timeout* 秒仍被阻塞则抛 RuntimeError，由上层调度器将其标记
    为 needs_review 而非 sent，避免「实际未发完但状态显示已发送」的 bug。

    *click_point* 为屏幕坐标 (x, y)；提供时额外用 WindowFromPoint 校验
    点击位置是否确实落在微信窗口上，防止其他窗口视觉遮挡但键盘焦点仍在微信
    的情况（Z-order ≠ foreground）。
    """
    deadline = time.time() + popup_timeout
    dismiss_attempted = False
    topmost_attempted = False

    while time.time() < deadline:
        # 1. 确保微信在前台
        fg = win32gui.GetForegroundWindow()
        if fg != main_hwnd:
            logger.info("微信不在前台 (fg=%s expected=%s)，尝试恢复...", fg, main_hwnd)
            try:
                force_foreground(main_hwnd, max_retry=3)
                time.sleep(0.3)
            except Exception:
                logger.warning("恢复前台失败，将在下次轮询重试")

        # 2. 检测微信自身的模态弹窗
        fg = win32gui.GetForegroundWindow()
        if fg == main_hwnd:
            if not _has_wechat_popup(main_hwnd):
                # 3. 检查点击位置是否被视觉遮挡（Z-order 检查）
                if click_point is not None:
                    if _point_hits_window(main_hwnd, click_point[0], click_point[1]):
                        return  # 一切正常！

                    # 点击位置被遮挡 — 尝试将微信强行置顶
                    if not topmost_attempted:
                        logger.info(
                            "点击位置 (%s,%s) 被其他窗口遮挡，尝试将微信置顶...",
                            click_point[0], click_point[1],
                        )
                        try:
                            win32gui.SetWindowPos(
                                main_hwnd, win32con.HWND_TOP,
                                0, 0, 0, 0,
                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                            )
                            time.sleep(0.3)
                            topmost_attempted = True
                            if _point_hits_window(main_hwnd, click_point[0], click_point[1]):
                                logger.info("微信置顶成功，点击位置已恢复")
                                return
                        except Exception:
                            logger.warning("SetWindowPos 失败")
                else:
                    return  # 无 click_point，跳过视觉检测

            elif not dismiss_attempted:
                # 有弹窗：尝试 ESC 关闭一次
                logger.info("检测到微信弹窗，尝试 ESC 关闭...")
                pyautogui.press("esc")
                time.sleep(0.5)
                dismiss_attempted = True
                if not _has_wechat_popup(main_hwnd):
                    logger.info("ESC 成功关闭弹窗")
                    continue

        # 仍被阻塞 — 等一等再试
        remaining = deadline - time.time()
        logger.info(
            "微信窗口被阻塞，%.0f 秒后重试 (剩余 %.0f 秒)...",
            check_interval,
            max(remaining, 0),
        )
        time.sleep(check_interval)

    raise RuntimeError(
        f"微信发送被弹窗阻塞超过 {popup_timeout:.0f} 秒，请手动关闭弹窗后重试。"
    )
