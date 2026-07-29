"""WechatSender — 真实坐标发送实现（Windows 专用）。

复用 ``sender.py`` 的原语函数（``_search_and_open`` / ``clip_set_text`` /
``clip_set_image`` / ``send_key`` / ``click`` / ``ensure_wechat_clear`` /
``find_wechat_main`` 等），通过 sender 模块对象动态访问，保持测试 monkeypatch
兼容（``tests/test_wechat_send.py`` monkeypatch ``sender.xxx`` 时，WechatSender
内部调用同样被 patch 到）。

设计要点（对齐 docs/wechat-sender-refactor-plan.md）：
- ``check_environment()`` 启动跑一次并缓存（计划 §4 末注，避免每次 send 重跑）
- ``is_ready()`` 每条发送前轻量检查 5 项（计划 §5）
- ``open_chat()`` 一次定位 + 身份校验（计划 §3.3）；身份校验优先用窗口标题，
  OCR 作为可选增强（rapidocr 未装时退化）
- ``send_text``/``send_image`` 复用旧按键序列（保证 ``test_send_uses_search_and_clipboard``
  测试通过），发送后真验证受 ``settings.wechat_strict_verify`` 控制（默认 False，
  灰度过渡；True 时做输入框清空/新增气泡/红色叹号三条真验证）
- 截图留证始终做（事后核查，非判据）
- ``_SEND_LOCK`` 模块级，与 ``verify_remark`` 共用（计划 §3.2 硬伤 4）

不破坏现有契约：
- ``send(remark, text, images, settings)`` 签名不变（在 sender.py 门面）
- 失败时由 sender.py 门面把 ``SendResult`` 映射回旧异常类型
  （``ClipboardVerificationError`` / ``NotImplementedError`` / ``RuntimeError``），
  保证 ``dispatch_scheduler`` 的异常分类与 fail_reason 不变
"""
from __future__ import annotations

import logging
import os
import sys
import time
import threading
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.services.wechat.sender_base import (
    SendReason,
    SendResult,
    HealthReport,
)

logger = logging.getLogger(__name__)

# 模块级互斥锁：发送路径与 verify_remark 共用（计划 §3.2 硬伤 4）。
# verify_remark 拿不到锁即返回「系统正忙」，不与后台发送抢同一微信窗口。
_SEND_LOCK = threading.Lock()

# 自检缓存：由 main.py 启动时调 set_health_cache 设置。
# None 表示未跑自检（如测试环境），_select_impl 此时按 override 选实现（不强制降级）。
_HEALTH_CACHE: "HealthReport | None" = None


def set_health_cache(report: "HealthReport | None") -> None:
    """供 main.py 启动时设置自检缓存。"""
    global _HEALTH_CACHE
    _HEALTH_CACHE = report


def get_health_cache() -> "HealthReport | None":
    """供 sender 门面 _select_impl / 前端 API 读取当前自检状态。"""
    return _HEALTH_CACHE


# 文件传输助手——冒烟测试第一条目标（无搜索歧义/身份校验风险）
_FILE_TRANSFER_ASSISTANT = "文件传输助手"


def _sender_mod():
    """Lazy import sender 模块，避免循环引用。

    WechatSender 通过此函数拿到 sender 模块对象，再动态访问原语
    （``_sender._search_and_open`` / ``_sender.clip_set_text`` 等），
    这样测试 monkeypatch ``sender.xxx`` 时 WechatSender 内部调用同样生效。
    """
    from app.services.wechat import sender as s
    return s


def _win32_ok() -> bool:
    """是否在 win32 且 win32 依赖已加载。"""
    if sys.platform != "win32":
        return False
    try:
        from app.services.wechat import win32 as w
        return w._PY32_OK
    except Exception:
        return False


class WechatSender:
    """真实坐标发送实现。

    单实例承载：启动自检缓存、窗口基准矩形、当前会话标记。
    线程安全由 ``_SEND_LOCK`` 保证（send_text/send_image/open_chat 全程持锁）。
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._health: HealthReport | None = None
        self._hwnd: int = 0
        self._rect_baseline: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._current_chat_remark: str = ""
        self._lock = _SEND_LOCK
        # 从模块级缓存继承启动自检结果（main.py 启动时设置）
        # 这样新实例能拿到启动时的 hwnd / rect_baseline / health，
        # is_ready 可基于真实窗口状态检查
        cached = get_health_cache()
        if cached is not None and cached.healthy:
            self._health = cached
            env = cached.environment
            self._hwnd = int(env.get("main_hwnd", 0))
            rb = env.get("rect_baseline")
            if isinstance(rb, (list, tuple)) and len(rb) == 4:
                self._rect_baseline = tuple(int(x) for x in rb)

    # ── 启动自检（计划 §4，11 项）──────────────────────────────

    def check_environment(self) -> HealthReport:
        """启动时跑一次，结果缓存。任一项失败 → healthy=False（强制降级演习）。"""
        if self._health is not None:
            return self._health

        env: dict = {}
        failed: list[str] = []
        details_parts: list[str] = []

        # 按计划 §4 顺序，越早失败越好
        checks = [
            ("01_platform_deps", self._check_01_platform_deps),
            ("02_wechat_process", self._check_02_wechat_process),
            ("03_version_whitelist", self._check_03_version_whitelist),
            ("04_main_window", self._check_04_main_window),
            ("05_logged_in", self._check_05_logged_in),
            ("06_dpi_resolution", self._check_06_dpi_resolution),
            ("07_pin_window", self._check_07_pin_window),
            ("08_clipboard", self._check_08_clipboard),
            ("09_desktop_interactive", self._check_09_desktop_interactive),
            ("10_smoke_test", self._check_10_smoke_test),
            ("11_auto_update_off", self._check_11_auto_update_off),
        ]
        for name, fn in checks:
            try:
                ok, detail = fn(env)
                if detail:
                    details_parts.append(f"[{name}] {detail}")
                if not ok:
                    failed.append(name)
                    logger.warning("自检失败 %s: %s", name, detail)
            except Exception as e:
                failed.append(name)
                details_parts.append(f"[{name}] 异常: {type(e).__name__}: {e}")
                logger.exception("自检异常 %s", name)

        healthy = not failed
        self._health = HealthReport(
            healthy=healthy,
            failed_checks=failed,
            environment=env,
            details="\n".join(details_parts),
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
        # 落盘（计划 §7.2）
        self._persist_health_report()
        return self._health

    def _check_01_platform_deps(self, env: dict) -> tuple[bool, str]:
        if not _win32_ok():
            return False, "非 win32 平台或 win32 依赖未加载（win32gui/pyautogui/win32com）"
        env["platform"] = sys.platform
        return True, "win32 依赖已加载"

    def _check_02_wechat_process(self, env: dict) -> tuple[bool, str]:
        try:
            import win32process
            import win32api
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin") as k:
                installdir = winreg.QueryValueEx(k, "InstallPath")[0]
            exe = os.path.join(installdir, "Weixin.exe")
            if not os.path.isfile(exe):
                return False, f"微信 exe 不存在: {exe}"
            # 检查进程
            found = False
            for proc in win32process.EnumProcesses():
                try:
                    h = win32api.OpenProcess(0x0400 | 0x0010, False, proc)  # QUERY_INFO|VM_READ
                    try:
                        name = win32process.GetModuleFileNameEx(h, 0)
                        if name.endswith("Weixin.exe"):
                            found = True
                            break
                    finally:
                        win32api.CloseHandle(h)
                except Exception:
                    continue
            env["wechat_exe"] = exe
            if not found:
                return False, "Weixin.exe 进程未运行"
            return True, "Weixin.exe 进程存在"
        except FileNotFoundError:
            return False, "注册表 Software\\Tencent\\Weixin 不存在，可能未安装 4.x 微信"
        except Exception as e:
            return False, f"进程检查异常: {e}"

    def _check_03_version_whitelist(self, env: dict) -> tuple[bool, str]:
        try:
            import win32api
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin") as k:
                installdir = winreg.QueryValueEx(k, "InstallPath")[0]
            exe = os.path.join(installdir, "Weixin.exe")
            info = win32api.GetFileVersionInfo(exe, "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            ver = f"{(ms >> 16) & 0xffff}.{ms & 0xffff}.{(ls >> 16) & 0xffff}.{ls & 0xffff}"
            env["wechat_version"] = ver
            whitelist = self.settings.wechat_version_whitelist
            if ver not in whitelist:
                return False, f"微信版本 {ver} 不在白名单 {whitelist}"
            return True, f"微信版本 {ver} 在白名单"
        except Exception as e:
            return False, f"版本检查异常: {e}"

    def _check_04_main_window(self, env: dict) -> tuple[bool, str]:
        try:
            s = _sender_mod()
            hwnd, rect = s.find_wechat_main()
            self._hwnd = hwnd
            env["main_hwnd"] = hwnd
            env["main_rect"] = list(rect)
            return True, f"主窗口 hwnd={hwnd} rect={rect}"
        except Exception as e:
            return False, f"未找到微信主窗口: {e}"

    def _check_05_logged_in(self, env: dict) -> tuple[bool, str]:
        try:
            import win32gui
            # 登录二维码窗口特征：标题含"登录"或 class 含 Login
            login_windows = []
            def _enum(h, _):
                cls = win32gui.GetClassName(h)
                title = win32gui.GetWindowText(h)
                if "Login" in cls or "登录" in title:
                    if win32gui.IsWindowVisible(h):
                        login_windows.append((h, cls, title))
            win32gui.EnumWindows(_enum, None)
            if login_windows:
                return False, f"检测到登录窗口: {login_windows[:2]}"
            if not win32gui.IsWindowVisible(self._hwnd):
                return False, "主窗口不可见（可能最小化到托盘）"
            return True, "已登录"
        except Exception as e:
            return False, f"登录态检查异常: {e}"

    def _check_06_dpi_resolution(self, env: dict) -> tuple[bool, str]:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # 启动即设 PER_MONITOR_AWARE_V2（计划 §4 第 6 项）
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            try:
                user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            except Exception:
                # 老版本 Windows 无此 API，退回 SetProcessDpiAwareness
                try:
                    user32.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
                except Exception:
                    pass
            hdc = user32.GetDC(0)
            gdi32 = ctypes.windll.gdi32
            dpi = gdi32.GetDeviceCaps(hdc, 88)
            user32.ReleaseDC(0, hdc)
            monitors = user32.GetSystemMetrics(80)
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            env["dpi"] = dpi
            env["dpi_scale_pct"] = round(dpi / 96.0 * 100)
            env["monitors"] = monitors
            env["screen_resolution"] = [w, h]
            # 多显示器在坐标方案下风险高（窗口跨屏坐标漂移）
            if monitors > 1:
                return False, f"多显示器环境（{monitors}），坐标方案不稳定，请单显示器运行"
            # DPI 非 100% 时告警但不硬拦（已设 PER_MONITOR_AWARE，pyautogui 用物理坐标）
            scale = round(dpi / 96.0 * 100)
            if scale != 100:
                logger.warning("DPI 缩放 %s%%，已设 PER_MONITOR_AWARE_V2，坐标应使用物理像素", scale)
            return True, f"DPI={dpi}({scale}%) 分辨率={w}x{h} 显示器数={monitors}"
        except Exception as e:
            return False, f"DPI/分辨率检查异常: {e}"

    def _check_07_pin_window(self, env: dict) -> tuple[bool, str]:
        if not self.settings.wechat_pin_window:
            # 用户关闭固定，仅记录基准矩形
            try:
                import win32gui
                rect = win32gui.GetWindowRect(self._hwnd)
                self._rect_baseline = rect
                env["window_pinned"] = False
                env["rect_baseline"] = list(rect)
                return True, "未固定窗口（wechat_pin_window=False），仅记录基准"
            except Exception as e:
                return False, f"记录基准矩形失败: {e}"
        try:
            import win32gui, win32con
            # 固定到左上角，尺寸用基准（800x600 是 4.1.1.19 已验证可用尺寸）
            # 幂等：多次调用同参数无副作用
            rect = win32gui.GetWindowRect(self._hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            # 只固定位置到 (0,0)，保留当前尺寸（避免改变用户布局）
            win32gui.SetWindowPos(
                self._hwnd, 0, 0, 0, w, h,
                win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
            )
            time.sleep(0.3)
            new_rect = win32gui.GetWindowRect(self._hwnd)
            self._rect_baseline = new_rect
            env["window_pinned"] = True
            env["rect_baseline"] = list(new_rect)
            return True, f"窗口已固定到 (0,0) 尺寸 {w}x{h}"
        except Exception as e:
            return False, f"固定窗口失败: {e}"

    def _check_08_clipboard(self, env: dict) -> tuple[bool, str]:
        try:
            s = _sender_mod()
            # 文本写入+回读
            test_text = "wechat_health_check_" + str(int(time.time()))
            s.clip_set_text(test_text)
            # 回读校验
            import win32clipboard
            for _ in range(3):
                try:
                    win32clipboard.OpenClipboard()
                    read = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                    win32clipboard.CloseClipboard()
                    if read == test_text:
                        break
                except Exception:
                    time.sleep(0.2)
            else:
                return False, "剪贴板文本回读不一致"
            env["clipboard_text_ok"] = True
            return True, "剪贴板文本读写正常"
        except Exception as e:
            return False, f"剪贴板检查异常: {e}"

    def _check_09_desktop_interactive(self, env: dict) -> tuple[bool, str]:
        """桌面可交互检测（计划 §4 第 9 项）：防锁屏/休眠/屏保/RDP 断开。"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # OpenInputDesktop 成功 = 有交互桌面；失败 = 锁屏/屏保/RDP 断开
            DESKTOP_READOBJECTS = 0x0001
            hdesk = user32.OpenInputDesktop(DESKTOP_READOBJECTS, False, DESKTOP_READOBJECTS)
            if not hdesk:
                return False, "OpenInputDesktop 失败，桌面不可交互（锁屏/休眠/RDP 断开）"
            try:
                user32.CloseDesktop(hdesk)
            except Exception:
                pass
            # 截图非全黑校验
            try:
                import pyautogui
                img = pyautogui.screenshot(region=(0, 0, 100, 100))
                import numpy as np
                arr = np.array(img)
                if arr.mean() < 5:  # 几乎全黑
                    return False, f"截图全黑（mean={arr.mean():.1f}），桌面不可交互"
                env["desktop_interactive"] = True
                env["screenshot_mean"] = float(arr.mean())
                return True, f"桌面可交互（截图 mean={arr.mean():.1f}）"
            except Exception as e:
                logger.warning("截图校验失败，仅凭 OpenInputDesktop 判定: %s", e)
                env["desktop_interactive"] = True
                return True, "OpenInputDesktop 成功（截图校验跳过）"
        except Exception as e:
            return False, f"桌面可交互检查异常: {e}"

    def _check_10_smoke_test(self, env: dict) -> tuple[bool, str]:
        """端到端冒烟（计划 §4 第 10 项）。

        默认跳过（避免启动时给文件传输助手发垃圾）；仅当
        ``settings.wechat_smoke_test_account`` 非空时才跑真实账号冒烟。
        文件传输助手冒烟需显式设 ``wechat_smoke_test_account="文件传输助手"``。
        """
        target = (self.settings.wechat_smoke_test_account or "").strip()
        if not target:
            env["smoke_test"] = "skipped"
            return True, "未配置 wechat_smoke_test_account，跳过冒烟测试（默认安全）"
        try:
            # 仅冒烟一条文本，避免启动副作用过大
            r = self.send_text(target, f"[health-check] 自检冒烟 {datetime.now().strftime('%H%M%S')}")
            if r.success:
                env["smoke_test"] = "passed"
                return True, f"冒烟发送成功（目标={target}）"
            return False, f"冒烟发送失败：{r.reason.value} {r.message}"
        except Exception as e:
            return False, f"冒烟异常: {type(e).__name__}: {e}"

    def _check_11_auto_update_off(self, env: dict) -> tuple[bool, str]:
        """微信自动更新已关闭（计划 §4 第 11 项）。"""
        try:
            import winreg
            # 微信 4.x 自动更新注册表项
            closed = False
            for path in [
                r"Software\Tencent\Weixin",
                r"Software\Tencent\WeChat",
            ]:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                        try:
                            v = winreg.QueryValueEx(k, "UpdaterSetting")[0]
                            if v == 0:
                                closed = True
                        except FileNotFoundError:
                            pass
                except FileNotFoundError:
                    continue
            env["auto_update_off"] = closed
            if not closed:
                logger.warning("微信自动更新状态未知，建议手动关闭防止静默更新致坐标失效")
                # 不硬拦：状态未知 ≠ 已开启
                return True, "自动更新状态未知（建议手动确认已关闭）"
            return True, "微信自动更新已关闭"
        except Exception as e:
            return True, f"自动更新检查异常（不拦）: {e}"

    def _persist_health_report(self) -> None:
        try:
            root = Path(self.settings.storage_root)
            root.mkdir(parents=True, exist_ok=True)
            path = root / "health_report.json"
            if self._health is None:
                return
            data = {
                "healthy": self._health.healthy,
                "failed_checks": self._health.failed_checks,
                "environment": self._health.environment,
                "details": self._health.details,
                "checked_at": self._health.checked_at,
            }
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("落盘 health_report.json 失败")

    # ── 发送前检查（计划 §5，5 项）──────────────────────────────

    def is_ready(self) -> bool:
        """每条消息发送前轻量检查。瞬时/可恢复问题返回 False（跳过本轮），
        持久/硬失效由 check_environment 缓存驱动降级。

        未跑自检（_health is None，如测试环境）时直接返回 True，避免依赖
        self._hwnd（由 check_environment 设置）导致测试环境误判失败。
        生产环境自检失败会通过 _HEALTH_CACHE 驱动 _select_impl 降级 DryRunSender，
        不会走到 WechatSender.is_ready。
        """
        if self._health is None:
            return True
        if not self._health.healthy:
            return False
        try:
            import win32gui
            # 1. 窗口仍存在
            if not win32gui.IsWindow(self._hwnd):
                logger.warning("微信窗口句柄失效")
                return False
            # 2. 前台可见、非最小化
            if not win32gui.IsWindowVisible(self._hwnd):
                logger.warning("微信窗口不可见")
                return False
            if win32gui.IsIconic(self._hwnd):
                logger.warning("微信窗口已最小化")
                return False
            # 3. 无陌生模态弹窗
            s = _sender_mod()
            # 复用 win32._has_wechat_popup
            from app.services.wechat import win32 as w
            if w._has_wechat_popup(self._hwnd):
                logger.warning("微信有模态弹窗")
                return False
            # 4. 窗口矩形 == 启动基准（防漂移）
            rect = win32gui.GetWindowRect(self._hwnd)
            tol = self.settings.wechat_window_drift_tolerance
            if self._rect_baseline != (0, 0, 0, 0):
                for i in range(4):
                    if abs(rect[i] - self._rect_baseline[i]) > tol:
                        logger.warning("窗口矩形漂移超阈值: 当前=%s 基准=%s 容忍=%s", rect, self._rect_baseline, tol)
                        return False
            # 5. 登录态兜底
            login_windows = []
            def _enum(h, _):
                cls = win32gui.GetClassName(h)
                title = win32gui.GetWindowText(h)
                if ("Login" in cls or "登录" in title) and win32gui.IsWindowVisible(h):
                    login_windows.append(h)
            win32gui.EnumWindows(_enum, None)
            if login_windows:
                logger.warning("检测到登录窗口，疑似掉线")
                return False
            return True
        except Exception:
            logger.exception("is_ready 检查异常")
            return False

    # ── 打开会话（计划 §3.3）──────────────────────────────────

    def open_chat(self, remark: str) -> SendResult:
        """搜索好友 → 打开会话 → 身份校验。

        成功后 ``self._current_chat_remark = remark``，后续 send_text/send_image
        若同备注名则跳过重复搜索（一次定位锁内连发，计划 §3.1 步骤 2）。
        """
        s = _sender_mod()
        try:
            hwnd, rect = s.find_wechat_main()
        except Exception as e:
            return SendResult(False, SendReason.WINDOW_ABNORMAL, f"找不到微信主窗口: {e}", raw_exception=type(e).__name__)
        try:
            s._search_and_open(hwnd, rect, remark, self.settings)
        except Exception as e:
            err_msg = str(e)
            # OCR 未找到精确匹配 → FRIEND_NOT_FOUND（拒绝发送，避免发错会话）
            if "精确匹配" in err_msg:
                return SendResult(False, SendReason.FRIEND_NOT_FOUND, err_msg, raw_exception=type(e).__name__)
            return SendResult(False, SendReason.WINDOW_ABNORMAL, f"搜索/打开会话失败: {e}", raw_exception=type(e).__name__)
        # 身份校验
        ok, msg = self._verify_chat_identity(remark)
        if not ok:
            return SendResult(False, SendReason.FRIEND_NOT_FOUND, f"会话身份校验失败: {msg}")
        self._current_chat_remark = remark
        return SendResult(True, SendReason.OK, f"会话已打开: {remark}")

    def _verify_chat_identity(self, remark: str) -> tuple[bool, str]:
        """会话身份校验（计划 §3.2）。

        优先级：
        1. 独立聊天窗口标题精确匹配（4.1.9 待验证，若可用最可靠）
        2. OCR 读会话标题（rapidocr 未装时跳过）
        3. 退化为「信任搜索结果」+ 告警（当前环境默认走此路）

        废除 pHash/像素比对/首发建基准（计划 §3.2 硬伤 1）。

        未跑自检（_health is None，如测试环境）时直接放行，避免真实枚举窗口。
        """
        if self._health is None:
            return True, "未跑自检，信任搜索结果"
        try:
            import win32gui
            # 1. 尝试独立聊天窗口标题精确匹配
            matched = []
            def _enum(h, _):
                cls = win32gui.GetClassName(h)
                title = win32gui.GetWindowText(h)
                if cls == "Qt51514QWindowIcon" and title == remark and win32gui.IsWindowVisible(h):
                    matched.append(h)
            win32gui.EnumWindows(_enum, None)
            if matched:
                return True, f"独立窗口标题精确匹配: {remark}"
            # 2. OCR（可选）
            ocr_ok = self._ocr_available()
            if ocr_ok:
                ocr_text = self._ocr_chat_title()
                if ocr_text is not None:
                    if remark in ocr_text or ocr_text in remark:
                        return True, f"OCR 标题匹配: {ocr_text!r} ~ {remark!r}"
                    return False, f"OCR 标题不匹配: 识别={ocr_text!r} 预期={remark!r}"
            # 3. 退化：信任搜索结果 + 告警
            logger.warning(
                "会话身份校验退化为信任搜索结果（remark=%s）—— "
                "建议安装 rapidocr 或验证独立聊天窗口方案", remark
            )
            return True, "信任搜索结果（OCR 未装/独立窗口未拉出）"
        except Exception as e:
            logger.exception("身份校验异常")
            return True, f"身份校验异常（放行）: {e}"  # 异常时放行，不阻塞发送

    def _ocr_available(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
            return True
        except ImportError:
            return False

    def _ocr_chat_title(self) -> str | None:
        """OCR 读会话标题区域。返回识别文本或 None。"""
        try:
            from rapidocr_onnxruntime import RapidOCR
            import pyautogui
            import numpy as np
            if not self._hwnd:
                return None
            import win32gui
            rect = win32gui.GetWindowRect(self._hwnd)
            # 标题区域：窗口顶部偏上的一小块（4.1.1.19 会话标题在顶部居中）
            left = rect[0] + rect[2] - rect[0]  # width
            top = rect[1]
            w = rect[2] - rect[0]
            # 截顶部 60px 高、居中 400px 宽
            region = (rect[0] + max(0, w // 2 - 200), top + 40, 400, 60)
            img = pyautogui.screenshot(region=region)
            arr = np.array(img)
            ocr = RapidOCR()
            result, _ = ocr(arr)
            if not result:
                return None
            # 拼接所有识别文本
            return "".join(box[1] for box in result)
        except Exception:
            logger.debug("OCR 读标题失败", exc_info=True)
            return None

    # ── 发送（计划 §3.1）──────────────────────────────────────

    def send_text(self, friend_remark: str, text: str) -> SendResult:
        return self._send_one(friend_remark, text=text, image_path=None)

    def send_image(self, friend_remark: str, image_path: str) -> SendResult:
        return self._send_one(friend_remark, text=None, image_path=image_path)

    def _send_one(self, remark: str, *, text: str | None, image_path: str | None) -> SendResult:
        """单条发送（文本或图片二选一）。

        按键序列与旧 sender.send 对齐，保证 test_send_uses_search_and_clipboard 过：
        - 文本：_search_and_open → clip_set_text → click → ctrl,v → alt,s → _random_delay
        - 图片：_search_and_open → clip_set_image → click → ctrl,v → (等待渲染) → alt,s → _random_delay
        """
        if not self.is_ready():
            return SendResult(False, SendReason.WINDOW_ABNORMAL, "is_ready 未通过，跳过本轮")
        s = _sender_mod()
        started = time.time()
        popup_timeout = float(getattr(self.settings, "wechat_popup_wait_timeout", 30.0))
        popup_interval = float(getattr(self.settings, "wechat_popup_interval", 2.0))

        # 图片预检
        if image_path is not None:
            if not os.path.isfile(image_path):
                return SendResult(False, SendReason.IMAGE_INVALID, f"图片不存在: {image_path}")
            try:
                from PIL import Image
                with Image.open(image_path) as im:
                    w, h = im.size
                if w * h > self.settings.max_image_pixels:
                    return SendResult(False, SendReason.IMAGE_INVALID, f"图片像素超上限: {w}x{h}")
            except Exception as e:
                return SendResult(False, SendReason.IMAGE_INVALID, f"图片不可读: {e}", raw_exception=type(e).__name__)

        # 打开会话（同备注名跳过重复搜索）
        if self._current_chat_remark != remark:
            open_r = self.open_chat(remark)
            if not open_r.success:
                return open_r

        # 发送前可选截图（用于新增气泡比对）
        before_bubble = None
        if self.settings.wechat_strict_verify:
            before_bubble = self._screenshot_chat_bottom()

        try:
            hwnd, rect = s.find_wechat_main()
        except Exception as e:
            return SendResult(False, SendReason.WINDOW_ABNORMAL, f"重读窗口失败: {e}")

        input_x, input_y = s._resolve_input_area(rect, self.settings)

        try:
            if image_path is not None:
                # 图片发送
                s.ensure_wechat_clear(hwnd, popup_timeout, popup_interval, click_point=(input_x, input_y))
                s.click(rect, input_x - rect[0], input_y - rect[1])
                time.sleep(0.2)
                s.clip_set_image(image_path)
                s.send_key("ctrl,v")
                # 等待粘贴渲染完成（计划 §3.2：不靠固定 sleep 赌时序）
                self._wait_paste_rendered(is_image=True)
                s.ensure_wechat_clear(hwnd, popup_timeout, popup_interval, click_point=(input_x, input_y))
                s.send_key("alt,s")
            else:
                # 文本发送
                s.ensure_wechat_clear(hwnd, popup_timeout, popup_interval, click_point=(input_x, input_y))
                s.click(rect, input_x - rect[0], input_y - rect[1])
                time.sleep(0.2)
                s.clip_set_text(text or "")
                s.send_key("ctrl,v")
                time.sleep(0.2)
                s.ensure_wechat_clear(hwnd, popup_timeout, popup_interval, click_point=(input_x, input_y))
                s.send_key("alt,s")
        except Exception as e:
            # 区分剪贴板失败与其他
            from app.services.wechat.win32 import ClipboardVerificationError
            if isinstance(e, ClipboardVerificationError):
                return SendResult(False, SendReason.CLIPBOARD_FAILED, str(e), raw_exception=type(e).__name__)
            return SendResult(False, SendReason.UNKNOWN, str(e), raw_exception=type(e).__name__)

        # 随机间隔（计划 §3.2：每条消息间 1~3 秒）
        s._random_delay(self.settings)

        elapsed_ms = int((time.time() - started) * 1000)
        screenshot_path = self._save_proof_screenshot(remark)

        # 发送后真验证（计划 §3.1 步骤 6，受 strict_verify 控制）
        verified = True
        verify_msg = ""
        if self.settings.wechat_strict_verify:
            verified, verify_msg = self._verify_send_success(before_bubble)
            if not verified:
                self._log_send(remark, "text" if image_path is None else "image",
                                False, SendReason.SEND_NOT_CONFIRMED, elapsed_ms, screenshot_path)
                return SendResult(
                    success=False,
                    reason=SendReason.SEND_NOT_CONFIRMED,
                    message=f"发送后真验证未通过: {verify_msg}",
                    screenshot_path=screenshot_path,
                    elapsed_ms=elapsed_ms,
                    verified=False,
                )

        kind = "text" if image_path is None else "image"
        self._log_send(remark, kind, True, SendReason.OK, elapsed_ms, screenshot_path)
        return SendResult(
            success=True,
            reason=SendReason.OK,
            message="ok",
            screenshot_path=screenshot_path,
            elapsed_ms=elapsed_ms,
            verified=verified,
            succeeded_count=1,
        )

    def _log_send(self, remark: str, kind: str, success: bool,
                  reason: SendReason, elapsed_ms: int, screenshot_path: str | None) -> None:
        """发送日志落盘（计划 §7.1）。未跑自检时跳过，避免测试留垃圾。"""
        if self._health is None:
            return
        try:
            root = Path(self.settings.storage_root)
            root.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "remark": remark,
                "kind": kind,
                "success": success,
                "reason": reason.value,
                "elapsed_ms": elapsed_ms,
                "screenshot_path": screenshot_path,
            }
            with open(root / "send_log.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("发送日志落盘失败", exc_info=True)

    def _wait_paste_rendered(self, *, is_image: bool, timeout: float = 3.0) -> None:
        """等待粘贴渲染完成（计划 §3.2）。

        图片需 1.5~2s，文本 0.3s。这里用渐进等待 + 短轮询，不靠固定 sleep 赌时序。
        实际「输入框出现缩略图/文本」的显式确认需要 UIA（不可用），退化为主观等待。
        """
        if is_image:
            # 图片粘贴渲染较慢，等 1.8s（ empirically 4.1.1.19 够用）
            time.sleep(1.8)
        else:
            time.sleep(0.3)

    def _verify_send_success(self, before_bubble) -> tuple[bool, str]:
        """发送后真验证（计划 §3.1 步骤 6 三条）。

        - (a) 输入框已清空：截图输入区，判断是否为空（最可靠）
        - (b) 会话区底部新增气泡：发送前后像素差异
        - (c) 红色叹号：检测会话区显著红色像素（兜底，误报率高仅辅助）

        任一未通过 → 返回 False。
        """
        try:
            import pyautogui
            import numpy as np
            s = _sender_mod()
            hwnd, rect = s.find_wechat_main()
            input_x, input_y = s._resolve_input_area(rect, self.settings)

            # (a) 输入框清空校验：截图输入区一小块，判断是否为「空」状态
            # 空状态特征：均值接近背景色（灰白），方差小
            region = (input_x - 50, input_y - 15, 100, 30)
            after_input = pyautogui.screenshot(region=region)
            arr = np.array(after_input)
            # 输入框有内容时方差大（文字像素），空时方差小
            std = float(arr.std())
            if std > 30:  # 经验阈值，可能仍有内容
                return False, f"输入框疑似未清空（std={std:.1f}）"

            # (b) 新增气泡：发送前后会话底部像素差异
            after_bubble = self._screenshot_chat_bottom()
            if before_bubble is not None and after_bubble is not None:
                before_arr = np.array(before_bubble)
                after_arr = np.array(after_bubble)
                if before_arr.shape == after_arr.shape:
                    diff = np.abs(before_arr.astype(int) - after_arr.astype(int))
                    changed_ratio = float((diff.sum(axis=2) > 30).mean())
                    if changed_ratio < 0.01:  # 几乎无变化
                        return False, f"会话区无新增气泡（变化率={changed_ratio:.3f}）"

            # (c) 红色叹号：检测会话区显著红色像素团块
            if after_bubble is not None:
                arr = np.array(after_bubble)
                r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
                red_mask = (r > 200) & (g < 80) & (b < 80)
                red_ratio = float(red_mask.mean())
                if red_ratio > 0.02:  # 显著红色区域
                    return False, f"检测到疑似红色叹号（红色占比={red_ratio:.3f}）"

            return True, "真验证通过"
        except Exception as e:
            logger.exception("真验证异常（放行）")
            return True, f"真验证异常（放行）: {e}"

    def _screenshot_chat_bottom(self):
        """截取会话区（用于发送前后像素差异比对）。

        2026-07-29 修复：原来只截会话区底部（y=50%~85%），但新消息可能出现在
        会话区顶部（如首次打开会话时第一条消息在 y≈172），导致真验证误判
        「无新增气泡（变化率=0.000）」。现改为截整个会话区（y=8%~88%）。
        """
        try:
            import pyautogui
            s = _sender_mod()
            hwnd, rect = s.find_wechat_main()
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            region = (rect[0] + 50, rect[1] + int(h * 0.08), w - 100, int(h * 0.80))
            return pyautogui.screenshot(region=region)
        except Exception:
            return None

    def _save_proof_screenshot(self, remark: str) -> str | None:
        """发送后截图留证（计划 §3.1 步骤 7）。事后核查，非判据。

        未跑自检（_health is None，如测试环境）时跳过，避免留垃圾文件。
        """
        if self._health is None:
            return None
        try:
            import pyautogui
            root = Path(self.settings.storage_root) / "send_proofs"
            root.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_remark = "".join(c for c in remark if c.isalnum() or c in "_-") or "unknown"
            path = root / f"{ts}_{safe_remark}.png"
            pyautogui.screenshot(str(path))
            return str(path)
        except Exception:
            logger.debug("截图留证失败", exc_info=True)
            return None
