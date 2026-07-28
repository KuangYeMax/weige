"""
WeChat sender tests — the win32 layer is mocked so all tests run on macOS.
Actual WeChat interaction can only be verified on Windows.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.wechat import sender
from app.services.wechat.sender import send
from app.services.wechat.uia import ChatVerificationResult


def test_send_raises_notimplemented_on_macos():
    with pytest.raises(NotImplementedError, match="仅支持 Windows"):
        send("test", "hello", [], Settings())


def test_settings_coordinates_are_sensible():
    settings = Settings()
    assert 0 <= settings.wechat_search_bar_x <= 500
    assert 0 <= settings.wechat_search_bar_y <= 200
    assert 500 <= settings.wechat_input_x_offset <= 1500
    assert 0 <= settings.wechat_send_interval_min <= settings.wechat_send_interval_max


def test_wechat_module_imports_on_macos():
    from app.services.wechat import sender as s
    assert callable(s.send)
    from app.services.wechat import win32 as w
    assert w._PY32_OK is False
    assert w.win32gui is None
    from app.services.wechat import uia
    assert callable(uia.get_uia_adapter)


def test_input_area_uses_bottom_edge_and_configured_offset():
    settings = Settings(wechat_input_y_offset=40)

    assert sender._resolve_input_area((100, 200, 1100, 1000), settings) == (850, 960)


class FakeUIAAdapter:
    def __init__(self, result: ChatVerificationResult):
        self.result = result
        self.calls: list[tuple[int, str]] = []

    def verify_remark(self, hwnd: int, remark: str) -> ChatVerificationResult:
        self.calls.append((hwnd, remark))
        return self.result


def _mock_windows(monkeypatch) -> None:
    monkeypatch.setattr(sender.sys, "platform", "win32")
    monkeypatch.setattr(sender, "require_win32", lambda: None)
    monkeypatch.setattr(sender, "_check_wechat_connected", lambda: None)
    monkeypatch.setattr(sender, "find_wechat_main", lambda: (1, (0, 0, 1000, 800)))
    monkeypatch.setattr(sender, "wait_idle", lambda: None)
    monkeypatch.setattr(sender, "force_foreground", lambda _hwnd: None)
    monkeypatch.setattr(sender, "hide_overlays", lambda _hwnd: None)
    monkeypatch.setattr(sender, "_release_stuck_modifiers", lambda: None)


def test_verify_remark_returns_exact_uia_result(monkeypatch):
    _mock_windows(monkeypatch)
    result = ChatVerificationResult("买家A", 1, "买家A", "买家A")
    adapter = FakeUIAAdapter(result)
    monkeypatch.setattr(sender, "get_uia_adapter", lambda: adapter)

    assert sender.verify_remark("  买家A  ", Settings()) == result
    assert adapter.calls == [(1, "买家A")]


@pytest.mark.parametrize(
    "result",
    [
        ChatVerificationResult("买家A", 1, "买家A", "其他人"),
        ChatVerificationResult("买家A", 1, "买家A", ""),
        ChatVerificationResult("买家A", 2, "买家A", "买家A"),
    ],
    ids=["header-mismatch", "header-unreadable", "multiple-search-results"],
)
def test_send_rejects_failed_uia_verification_before_clipboard_or_send(monkeypatch, result):
    _mock_windows(monkeypatch)
    monkeypatch.setattr(sender, "get_uia_adapter", lambda: FakeUIAAdapter(result))
    clipboard_calls: list[str] = []
    send_keys: list[str] = []
    monkeypatch.setattr(sender, "clip_set_text", lambda value: clipboard_calls.append(value))
    monkeypatch.setattr(sender, "send_key", lambda key: send_keys.append(key))

    with pytest.raises(RuntimeError, match="好友精确校验失败"):
        sender.send("买家A", "消息", [], Settings())

    assert clipboard_calls == []
    assert send_keys == []


def test_send_uses_clipboard_only_after_exact_uia_verification(monkeypatch):
    _mock_windows(monkeypatch)
    result = ChatVerificationResult("买家A", 1, "买家A", "买家A")
    monkeypatch.setattr(sender, "get_uia_adapter", lambda: FakeUIAAdapter(result))
    clipboard_calls: list[str] = []
    send_keys: list[str] = []
    monkeypatch.setattr(sender, "clip_set_text", lambda value: clipboard_calls.append(value))
    monkeypatch.setattr(sender, "send_key", lambda key: send_keys.append(key))
    monkeypatch.setattr(sender, "click", lambda *_args: None)
    monkeypatch.setattr(sender, "_random_delay", lambda _settings: None)

    sender.send("买家A", "消息", [], Settings())

    assert clipboard_calls == ["消息"]
    assert send_keys == ["ctrl,v", "alt,s"]


@pytest.mark.parametrize(
    ("remark", "text", "images"),
    [("", "消息", []), ("买家A", "   ", [])],
)
def test_send_rejects_blank_remark_or_empty_payload_before_windows_calls(
    monkeypatch, remark, text, images
):
    monkeypatch.setattr(sender.sys, "platform", "win32")
    monkeypatch.setattr(sender, "require_win32", pytest.fail)

    with pytest.raises(ValueError, match="发送载荷不能为空"):
        sender.send(remark, text, images, Settings())
