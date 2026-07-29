"""feat/uia-sender 重构后的新增测试。

覆盖：
- _select_impl 实现选择（real/dryrun/test_account/自检失败降级）
- DryRunSender 落盘不真发
- TestAccountSender 白名单拒绝
- _SEND_LOCK 互斥（verify_remark 拿不到锁返回忙）
- SendResult → 异常映射（ClipboardVerificationError 等）
- check_environment 单项失败 → healthy=False

现有契约由 tests/test_wechat_send.py + tests/test_dispatch_send.py 覆盖，本文件不重复。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.services.wechat import sender
from app.services.wechat.sender_base import (
    HealthReport,
    SendReason,
    SendResult,
)
from app.services.wechat.uia import ChatVerificationError


# ── 公共 fixture ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_health_cache():
    """每个测试前后清理自检缓存，避免跨测试污染。"""
    from app.services.wechat import wechat_sender as ws
    saved = ws.get_health_cache()
    ws.set_health_cache(None)
    yield
    ws.set_health_cache(saved)


@pytest.fixture
def win32_env(monkeypatch):
    """模拟 win32 环境（平台/依赖），不真实调用 win32。"""
    monkeypatch.setattr(sender.sys, "platform", "win32")
    monkeypatch.setattr(sender, "require_win32", lambda: None)


# ── _select_impl 实现选择 ────────────────────────────────────


def test_select_impl_real_returns_wechat_sender(win32_env):
    s = Settings()
    s.test_wechat_sender_override = "real"
    impl = sender._select_impl(s)
    from app.services.wechat.wechat_sender import WechatSender
    assert isinstance(impl, WechatSender)


def test_select_impl_dryrun_returns_dryrun(win32_env):
    s = Settings()
    s.test_wechat_sender_override = "dryrun"
    impl = sender._select_impl(s)
    from app.services.wechat.dryrun_sender import DryRunSender
    assert isinstance(impl, DryRunSender)


def test_select_impl_test_account_returns_test_account(win32_env):
    s = Settings()
    s.test_wechat_sender_override = "test_account"
    impl = sender._select_impl(s)
    from app.services.wechat.test_account_sender import TestAccountSender
    assert isinstance(impl, TestAccountSender)


def test_select_impl_health_fail_forces_dryrun(win32_env):
    """自检不通过 → 强制 DryRunSender（无论 override 是 real）。"""
    from app.services.wechat import wechat_sender as ws
    ws.set_health_cache(HealthReport(
        healthy=False,
        failed_checks=["03_version_whitelist"],
        environment={},
        details="微信版本 4.1.9.57 不在白名单",
        checked_at="2026-07-29T00:00:00+00:00",
    ))
    s = Settings()
    s.test_wechat_sender_override = "real"
    impl = sender._select_impl(s)
    from app.services.wechat.dryrun_sender import DryRunSender
    assert isinstance(impl, DryRunSender), "自检失败必须强制降级演习模式"


def test_select_impl_health_pass_uses_override(win32_env):
    """自检通过 → 按 override 选实现（real → WechatSender）。"""
    from app.services.wechat import wechat_sender as ws
    ws.set_health_cache(HealthReport(
        healthy=True,
        failed_checks=[],
        environment={"main_hwnd": 12345, "rect_baseline": [0, 0, 800, 600]},
        details="全部通过",
        checked_at="2026-07-29T00:00:00+00:00",
    ))
    s = Settings()
    s.test_wechat_sender_override = "real"
    impl = sender._select_impl(s)
    from app.services.wechat.wechat_sender import WechatSender
    assert isinstance(impl, WechatSender)
    # 实例应从缓存继承 hwnd / rect_baseline
    assert impl._hwnd == 12345
    assert impl._rect_baseline == (0, 0, 800, 600)
    assert impl._health is not None and impl._health.healthy


# ── DryRunSender ────────────────────────────────────────────


def test_dryrun_send_text_records_to_disk(tmp_path):
    s = Settings(storage_root=tmp_path)
    from app.services.wechat.dryrun_sender import DryRunSender
    dr = DryRunSender(s)
    r = dr.send_text("买家A", "好评文案")
    assert r.success and r.reason == SendReason.OK
    log = tmp_path / "send_dryrun" / "dryrun_log.jsonl"
    assert log.is_file()
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["remark"] == "买家A"
    assert entries[0]["text"] == "好评文案"
    assert entries[0]["image_path"] is None


def test_dryrun_send_image_records_to_disk(tmp_path):
    s = Settings(storage_root=tmp_path)
    img = tmp_path / "test.jpg"
    img.write_bytes(b"fake-image")
    from app.services.wechat.dryrun_sender import DryRunSender
    dr = DryRunSender(s)
    r = dr.send_image("买家A", str(img))
    assert r.success and r.reason == SendReason.OK
    entries = [json.loads(line) for line in
               (tmp_path / "send_dryrun" / "dryrun_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert entries[0]["image_path"] == str(img)
    assert entries[0]["text"] is None


def test_dryrun_check_environment_always_healthy():
    s = Settings()
    from app.services.wechat.dryrun_sender import DryRunSender
    dr = DryRunSender(s)
    report = dr.check_environment()
    assert report.healthy is True
    assert dr.is_ready() is True


# ── TestAccountSender ───────────────────────────────────────


def test_test_account_rejects_non_whitelist(win32_env):
    s = Settings()
    s.test_wechat_sender_override = "test_account"
    s.wechat_test_accounts = ["测试小号1", "测试小号2"]
    from app.services.wechat.test_account_sender import TestAccountSender
    ta = TestAccountSender(s)
    r = ta.send_text("真实客户张三", "你好")
    assert not r.success
    assert r.reason == SendReason.FRIEND_NOT_FOUND
    assert "非测试账号白名单" in r.message


def test_test_account_image_rejects_non_whitelist(win32_env):
    s = Settings()
    s.wechat_test_accounts = ["测试小号"]
    from app.services.wechat.test_account_sender import TestAccountSender
    ta = TestAccountSender(s)
    r = ta.send_image("真实客户李四", "/fake.jpg")
    assert not r.success
    assert r.reason == SendReason.FRIEND_NOT_FOUND


def test_test_account_allows_whitelist_and_delegates(win32_env, monkeypatch):
    """白名单账号 → 委托给内部 WechatSender.send_text。"""
    s = Settings()
    s.wechat_test_accounts = ["测试小号"]
    from app.services.wechat.test_account_sender import TestAccountSender
    ta = TestAccountSender(s)
    # mock 内部 WechatSender.send_text 避免真实调用 win32
    called = []
    monkeypatch.setattr(ta._inner, "send_text", lambda remark, text: called.append((remark, text)) or SendResult(True, SendReason.OK, "mocked"))
    r = ta.send_text("测试小号", "你好")
    assert r.success
    assert called == [("测试小号", "你好")]


# ── _SEND_LOCK 互斥（计划 §3.2 硬伤 4）────────────────────────


def test_verify_remark_busy_when_lock_held(win32_env, monkeypatch):
    """send 持锁期间，verify_remark 拿不到锁 → 抛 ChatVerificationError。"""
    from app.services.wechat.wechat_sender import _SEND_LOCK
    # 模拟发送持锁
    held = _SEND_LOCK.acquire(blocking=False)
    assert held
    try:
        with pytest.raises(ChatVerificationError, match="系统正忙"):
            sender.verify_remark("买家A", Settings())
    finally:
        _SEND_LOCK.release()


def test_verify_remark_succeeds_when_lock_free(win32_env, monkeypatch):
    """锁空闲时 verify_remark 正常走（mock 原语，不真实调 win32）。"""
    monkeypatch.setattr(sender, "_prepare_wechat_window", lambda: (1, (0, 0, 1000, 800)))
    monkeypatch.setattr(sender, "_search_and_open", lambda *a: None)
    r = sender.verify_remark("买家A", Settings())
    assert r.expected_remark == "买家A"
    assert r.header_name == "买家A"


def test_send_acquires_and_releases_lock(win32_env, monkeypatch):
    """send 完成后释放锁，后续 verify_remark 能拿到。"""
    # mock _select_impl 返回 DryRunSender（避免真实 win32 调用）
    from app.services.wechat.dryrun_sender import DryRunSender
    monkeypatch.setattr(sender, "_select_impl", lambda s: DryRunSender(s))
    sender.send("买家A", "消息", [], Settings())
    from app.services.wechat.wechat_sender import _SEND_LOCK
    assert _SEND_LOCK.acquire(blocking=False), "send 完成后应释放锁"
    _SEND_LOCK.release()


# ── SendResult → 异常映射 ────────────────────────────────────


def test_to_exception_clipboard_failed():
    from app.services.wechat.win32 import ClipboardVerificationError
    r = SendResult(False, SendReason.CLIPBOARD_FAILED, "剪贴板回读失败")
    e = sender._to_exception(r)
    assert isinstance(e, ClipboardVerificationError)
    assert "剪贴板回读失败" in str(e)


def test_to_exception_friend_not_found():
    r = SendResult(False, SendReason.FRIEND_NOT_FOUND, "会话身份校验失败")
    e = sender._to_exception(r)
    assert isinstance(e, ChatVerificationError)


def test_to_exception_image_invalid():
    r = SendResult(False, SendReason.IMAGE_INVALID, "图片不存在: /x.jpg")
    e = sender._to_exception(r)
    assert isinstance(e, FileNotFoundError)


def test_to_exception_window_abnormal_is_runtime_error():
    r = SendResult(False, SendReason.WINDOW_ABNORMAL, "窗口丢失")
    e = sender._to_exception(r)
    assert isinstance(e, RuntimeError)


def test_to_exception_send_not_confirmed_is_runtime_error():
    r = SendResult(False, SendReason.SEND_NOT_CONFIRMED, "输入框未清空")
    e = sender._to_exception(r)
    assert isinstance(e, RuntimeError)


def test_to_exception_with_progress_includes_succeeded_count():
    """多图中途失败，异常消息携带已成功条目数（计划 §6 失败进度可观测）。"""
    r = SendResult(False, SendReason.UNKNOWN, "发送失败")
    e = sender._to_exception_with_progress(r, succeeded=2)
    assert isinstance(e, RuntimeError)
    assert "已成功发送 2 条" in str(e)


# ── check_environment 单项失败 ───────────────────────────────


def test_check_environment_non_win32_unhealthy(monkeypatch):
    """非 win32 平台自检不通过（第 1 项 platform_deps 失败）。

    mock sys.platform=darwin 避免真实 win32 副作用（如 SetWindowPos 移动窗口）。
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    s = Settings()
    from app.services.wechat.wechat_sender import WechatSender
    ws = WechatSender(s)
    report = ws.check_environment()
    assert report.healthy is False
    assert "01_platform_deps" in report.failed_checks


def test_check_environment_caches_result(monkeypatch):
    """check_environment 只跑一次并缓存（计划 §4 末注）。"""
    monkeypatch.setattr(sys, "platform", "darwin")
    s = Settings()
    from app.services.wechat.wechat_sender import WechatSender
    ws = WechatSender(s)
    # 第一次跑（非 win32 → 失败）
    r1 = ws.check_environment()
    assert r1 is not None
    # 第二次调应返回缓存（同一对象）
    r2 = ws.check_environment()
    assert r2 is r1


def test_health_report_persists_to_disk(tmp_path, monkeypatch):
    """自检结果落盘 health_report.json（计划 §7.2）。"""
    monkeypatch.setattr(sys, "platform", "darwin")
    s = Settings(storage_root=tmp_path)
    from app.services.wechat.wechat_sender import WechatSender
    ws = WechatSender(s)
    ws.check_environment()
    report_file = tmp_path / "health_report.json"
    assert report_file.is_file()
    data = json.loads(report_file.read_text(encoding="utf-8"))
    assert "healthy" in data
    assert "failed_checks" in data
    assert "environment" in data
    assert "checked_at" in data


# ── send 门面异常映射端到端 ──────────────────────────────────


def test_send_maps_impl_failure_to_exception(win32_env, monkeypatch):
    """impl 返回失败 SendResult → send 抛对应异常（旧契约不变）。"""
    from app.services.wechat.sender_base import SendResult, SendReason
    from app.services.wechat.wechat_sender import WechatSender

    class FailingImpl:
        def __init__(self, *a, **kw): pass
        def send_text(self, remark, text):
            return SendResult(False, SendReason.CLIPBOARD_FAILED, "剪贴板失败")
        def send_image(self, remark, path):
            return SendResult(False, SendReason.UNKNOWN, "不该走到这")

    monkeypatch.setattr(sender, "_select_impl", lambda s: FailingImpl())
    from app.services.wechat.win32 import ClipboardVerificationError
    with pytest.raises(ClipboardVerificationError, match="剪贴板失败"):
        sender.send("买家A", "消息", [], Settings())


def test_send_strict_verify_off_by_default():
    """wechat_strict_verify 默认 False（灰度过渡，行为同旧）。"""
    s = Settings()
    assert s.wechat_strict_verify is False


def test_new_config_fields_have_defaults():
    """新增配置项都有合理默认值。"""
    s = Settings()
    assert s.wechat_version_whitelist == ["4.1.1.19"]
    assert s.wechat_screenshot_dir == "storage/send_proofs"
    assert s.wechat_test_accounts == []
    assert s.wechat_window_drift_tolerance == 20
    assert s.wechat_ocr_engine == "rapidocr"
    assert s.wechat_desktop_recheck_interval == 300
    assert s.wechat_smoke_test_account == ""
    assert s.wechat_pin_window is True
