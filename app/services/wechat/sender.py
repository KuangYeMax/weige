"""
WeChat sender — coordinate-based automation (no UIA dependency).
Windows-only; safe to import on macOS.

门面层（feat/uia-sender 重构）：
- ``send`` / ``verify_remark`` 签名不变，内部委托给 ``_select_impl`` 选的实现
- ``_select_impl`` 根据 ``settings.test_wechat_sender_override`` + 自检缓存选实现：
  real → WechatSender / dryrun → DryRunSender / test_account → TestAccountSender
  / failing:* → failing_sender（测试用，保留）
- 自检缓存 ``_HEALTH_CACHE`` 由 main.py 启动时设置；healthy=False 时强制 DryRunSender
- ``_SEND_LOCK`` 模块级互斥锁，verify_remark 与 send 共用（计划 §3.2 硬伤 4）
- 保留所有原语函数（_prepare_wechat_window / _search_and_open / _resolve_input_area /
  _random_delay 及 win32 re-export）供测试 monkeypatch

不破坏现有契约：
- ``send(remark, text, images, settings)`` 签名不变
- 失败抛旧异常类型（ClipboardVerificationError / FileNotFoundError / RuntimeError 等），
  保证 dispatch_scheduler 的异常分类与 fail_reason 不变
- WechatSender 通过 ``_sender_mod()`` 动态访问本模块原语，monkeypatch 本模块属性时
  WechatSender 内部调用同样生效
"""
from __future__ import annotations

import os
import random
import sys
import time
import logging

from app.config import Settings
from app.services.wechat.win32 import (
    require_win32,
    clip_set_text,
    clip_set_image,
    find_wechat_main,
    hide_overlays,
    force_foreground,
    wait_idle,
    click,
    send_key,
    ensure_wechat_clear,
)
from app.services.wechat.uia import ChatVerificationResult, ChatVerificationError
from app.services.wechat.sender_base import SendResult, SendReason, HealthReport

logger = logging.getLogger(__name__)

# 模块级互斥锁 + 自检缓存（从 wechat_sender 导入，数据归属统一，避免循环 import）。
# _SEND_LOCK：发送路径与 verify_remark 共用（计划 §3.2 硬伤 4）。
# _HEALTH_CACHE：由 main.py 启动时调 set_health_cache 设置。
from app.services.wechat.wechat_sender import (  # noqa: E402
    _SEND_LOCK,
    set_health_cache,
    get_health_cache,
)


# ── 原语函数（保留不变，供测试 monkeypatch）──────────────────────


def _prepare_wechat_window() -> tuple[int, tuple[int, int, int, int]]:
    find_wechat_main()
    hwnd, rect = find_wechat_main()
    wait_idle()
    force_foreground(hwnd)
    hide_overlays(hwnd)
    import pyautogui
    for key in ["alt", "ctrl", "shift", "win"]:
        pyautogui.keyUp(key)
    return hwnd, rect


def _search_and_open(hwnd, rect, remark, settings):
    """搜索好友并打开会话。

    优先用 OCR 精确选中搜索结果列表里标题==备注名的项（计划 §3.3），
    避免「两次 enter 赌第一个结果」导致打开错的会话（bug：消息发到群聊）。
    OCR 不可用或未找到精确匹配时，fallback 到旧的两次 enter 行为。

    搜索前强制清空搜索框（ESC + ctrl+a + delete），避免残留词导致搜错目标
    （真实 bug：搜索框残留"蝗虫"导致消息发到"蝗虫"会话）。
    """
    # ── 强制清空搜索框（修复残留词 bug）──
    # 先确保微信在前台（WorkBuddy 等 IDE 会抢回前台，导致 click/OCR 打到错误窗口）
    force_foreground(hwnd)
    time.sleep(0.3)
    click(rect, settings.wechat_search_bar_x, settings.wechat_search_bar_y)
    time.sleep(0.3)
    # ESC 清空微信搜索框（微信标准行为：ESC 清空搜索并关闭结果）
    send_key("escape")
    time.sleep(0.2)
    # 重新点击搜索栏（ESC 后焦点可能丢失）
    force_foreground(hwnd)
    time.sleep(0.1)
    click(rect, settings.wechat_search_bar_x, settings.wechat_search_bar_y)
    time.sleep(0.3)
    # ctrl+a + delete 双保险清空
    send_key("ctrl,a")
    time.sleep(0.1)
    send_key("delete")
    time.sleep(0.1)

    # ── 粘贴搜索词 ──
    clip_set_text(remark.strip())
    send_key("ctrl,v")
    time.sleep(1.8)  # 等搜索结果加载（从 1.5 提到 1.8 更稳）

    # ── OCR 精确选中（计划 §3.3）──
    if _try_ocr_select(rect, remark):
        time.sleep(0.8)  # 等会话打开
        return

    ocr = _get_ocr()
    if ocr is not None:
        # OCR 可用但没找到精确匹配 → 拒绝发送，避免发错会话（不再 fallback 到两次 enter）
        # 之前两次 fallback 导致消息发到群聊的真实事故
        raise RuntimeError(
            f"OCR 未在搜索结果中找到精确匹配：{remark}（拒绝发送，避免发错会话）"
        )

    # OCR 不可用（rapidocr 未装）：fallback 到两次 enter（旧行为，有发错会话风险）
    logger.warning("rapidocr 未安装，fallback 到两次 enter（remark=%s，有发错会话风险）", remark)
    send_key("enter")
    time.sleep(0.5)
    send_key("enter")
    time.sleep(1)


# OCR 实例缓存（避免每次调用都加载模型）
_OCR_INSTANCE = None


def _get_ocr():
    """获取 RapidOCR 实例（缓存）。不可用时返回 None。"""
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_INSTANCE = RapidOCR()
        except ImportError:
            _OCR_INSTANCE = False  # 标记不可用
            logger.info("rapidocr 未安装，搜索结果精确选中将 fallback 到两次 enter")
    return _OCR_INSTANCE if _OCR_INSTANCE is not False else None


def _try_ocr_select(rect: tuple[int, int, int, int], remark: str) -> bool:
    """OCR 识别搜索结果列表，精确匹配 remark 并点击该项。

    成功返回 True（已点击选中），失败返回 False（调用方 fallback）。
    截图区域：主窗口左侧搜索栏下方 420x600 的列表区。
    选第一个精确匹配项（通常在顶部=联系人分类）。
    """
    ocr = _get_ocr()
    if ocr is None:
        return False
    try:
        import pyautogui
        import numpy as np
        left, top = rect[0], rect[1] + 70
        region = (left, top, 420, 600)
        img = pyautogui.screenshot(region=region)
        arr = np.array(img)
        result, _ = ocr(arr)
        if not result:
            return False
        # 找精确匹配（text == remark），取第一个（顶部=联系人分类）
        for box in result:
            if box[1] == remark:
                pts = box[0]
                cx = sum(p[0] for p in pts) / 4
                cy = sum(p[1] for p in pts) / 4
                screen_x = left + cx
                screen_y = top + cy
                click(rect, int(screen_x - rect[0]), int(screen_y - rect[1]))
                logger.info("OCR 精确选中成功：remark=%s 坐标=(%.0f,%.0f)", remark, screen_x, screen_y)
                return True
        logger.warning("OCR 未在搜索结果中找到精确匹配：remark=%s", remark)
        return False
    except Exception:
        logger.debug("OCR 精确选中异常", exc_info=True)
        return False


def _resolve_input_area(rect: tuple[int, int, int, int], settings: Settings) -> tuple[int, int]:
    x = rect[0] + settings.wechat_input_x_offset
    y = rect[3] - settings.wechat_input_y_offset
    return x, y


def _random_delay(settings: Settings) -> None:
    delay = random.uniform(settings.wechat_send_interval_min, settings.wechat_send_interval_max)
    time.sleep(delay)


# ── 实现选择（计划 §1.3）──────────────────────────────────────


def _select_impl(settings: Settings):
    """根据 override + 自检缓存选定实现。

    返回满足 WechatSenderProtocol 的对象（real/dryrun/test_account）。
    failing:* 模式不由此处处理（dispatch_scheduler._resolve_sender 直接走 failing_sender）。
    """
    override = settings.test_wechat_sender_override

    # 自检不通过 → 强制 DryRunSender（无论 override 是什么）
    cached = get_health_cache()
    if cached is not None and not cached.healthy:
        logger.warning("自检未通过，强制降级演习模式：failed_checks=%s", cached.failed_checks)
        from app.services.wechat.dryrun_sender import DryRunSender
        return DryRunSender(settings)

    if override == "dryrun":
        from app.services.wechat.dryrun_sender import DryRunSender
        return DryRunSender(settings)
    if override == "test_account":
        from app.services.wechat.test_account_sender import TestAccountSender
        return TestAccountSender(settings)
    # real 或其他 → WechatSender
    from app.services.wechat.wechat_sender import WechatSender
    return WechatSender(settings)


def _to_exception(r: SendResult) -> Exception:
    """把 SendResult 映射回旧异常类型，保证 dispatch_scheduler 异常分类不变。"""
    from app.services.wechat.win32 import ClipboardVerificationError
    if r.reason == SendReason.CLIPBOARD_FAILED:
        return ClipboardVerificationError(r.message)
    if r.reason == SendReason.FRIEND_NOT_FOUND:
        return ChatVerificationError(r.message)
    if r.reason == SendReason.IMAGE_INVALID:
        return FileNotFoundError(r.message)
    if r.reason == SendReason.LOCK_BUSY:
        return RuntimeError(r.message)
    # WINDOW_ABNORMAL / SEND_NOT_CONFIRMED / UNKNOWN / NOT_LOGGED_IN 等
    return RuntimeError(r.message)


# ── 门面：send / verify_remark（签名不变）─────────────────────────


def send(remark: str, text: str, images: list[str], settings: Settings) -> None:
    """发送图片+文本到微信好友。失败抛异常（旧契约不变）。

    内部委托给 _select_impl 选的实现；real 模式走 WechatSender（坐标方案），
    dryrun/test_account 模式走对应实现。自检不通过时强制 DryRunSender。
    """
    if not remark.strip() or (not text.strip() and not images):
        raise ValueError("发送载荷不能为空")
    if sys.platform != "win32":
        raise NotImplementedError("微信发送仅支持 Windows，当前系统无法执行")
    require_win32()

    # 图片存在性预检（与旧 send 一致，test_send_raises_on_missing_image 期望 FileNotFoundError）
    for p in images:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"发送图片不存在: {p}")

    impl = _select_impl(settings)

    # 持锁发送：与 verify_remark 互斥，抢同一微信窗口
    acquired = _SEND_LOCK.acquire(blocking=True, timeout=120.0)
    if not acquired:
        raise RuntimeError("获取发送锁超时（120s），可能有其他发送或 verify_remark 占用")
    try:
        succeeded = 0
        for img in images:
            r = impl.send_image(remark, img)
            if not r.success:
                # 多图中途失败：异常携带已成功条目数（计划 §6 失败进度可观测）
                raise _to_exception_with_progress(r, succeeded)
            succeeded += 1
        if text:
            r = impl.send_text(remark, text)
            if not r.success:
                raise _to_exception_with_progress(r, succeeded)
    finally:
        _SEND_LOCK.release()


def _to_exception_with_progress(r: SendResult, succeeded: int) -> Exception:
    """把 SendResult 映射回异常，并在 message 中附带已成功条目数。"""
    if succeeded > 0:
        r = SendResult(
            success=r.success,
            reason=r.reason,
            message=f"{r.message}（已成功发送 {succeeded} 条）",
            raw_exception=r.raw_exception,
            screenshot_path=r.screenshot_path,
            elapsed_ms=r.elapsed_ms,
            verified=r.verified,
            succeeded_count=succeeded,
        )
    return _to_exception(r)


def verify_remark(remark: str, settings: Settings) -> ChatVerificationResult:
    """校验好友备注名能否精确打开会话。签名不变。

    保持旧逻辑（_prepare_wechat_window + _search_and_open + 返回 ChatVerificationResult），
    新增 _SEND_LOCK 互斥（与 send 共用，拿不到锁返回「系统正忙」）。

    不委托给 WechatSender.open_chat，因为：
    1. 测试 test_verify_remark_searches_and_opens_chat 期望 _search_and_open 被调
    2. 返回类型是 ChatVerificationResult 而非 SendResult
    3. 旧逻辑已验证可用，无需改动
    """
    if not remark.strip():
        raise ValueError("好友备注不能为空")
    if sys.platform != "win32":
        raise NotImplementedError("微信发送仅支持 Windows，当前系统无法执行")
    require_win32()

    # 拿不到锁 → 系统正忙（不阻塞等待，避免路由请求卡死）
    if not _SEND_LOCK.acquire(blocking=False):
        raise ChatVerificationError("系统正忙，请稍后重试（发送任务进行中）")
    try:
        hwnd, rect = _prepare_wechat_window()
        remark = remark.strip()
        _search_and_open(hwnd, rect, remark, settings)
        return ChatVerificationResult(
            expected_remark=remark.strip(),
            exact_search_result_count=1,
            selected_result_name=remark.strip(),
            header_name=remark.strip(),
        )
    finally:
        _SEND_LOCK.release()
