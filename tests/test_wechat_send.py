"""
WeChat sender tests — the win32 layer is mocked so all tests run on macOS.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.wechat import sender
from app.services.wechat.sender import send, verify_remark
from app.services.wechat.uia import ChatVerificationResult


def test_send_raises_notimplemented_on_macos(monkeypatch):
    monkeypatch.setattr(sender.sys, "platform", "darwin")
    with pytest.raises(NotImplementedError, match="仅支持 Windows"):
        send("test", "hello", [], Settings())


def test_settings_coordinates_are_sensible():
    settings = Settings()
    assert 0 <= settings.wechat_search_bar_x <= 500
    assert 0 <= settings.wechat_search_bar_y <= 200
    assert 500 <= settings.wechat_input_x_offset <= 1500
    assert 0 <= settings.wechat_send_interval_min <= settings.wechat_send_interval_max


def test_wechat_module_imports():
    from app.services.wechat import sender as s
    assert callable(s.send)
    from app.services.wechat import win32 as w
    import sys
    if sys.platform == "win32":
        assert w._PY32_OK is True
        assert w.win32gui is not None
    else:
        assert w._PY32_OK is False
        assert w.win32gui is None


def test_input_area_uses_bottom_edge_and_configured_offset():
    settings = Settings(wechat_input_y_offset=40)
    assert sender._resolve_input_area((100, 200, 1100, 1000), settings) == (850, 960)


def _mock_windows(monkeypatch):
    monkeypatch.setattr(sender.sys, "platform", "win32")
    monkeypatch.setattr(sender, "require_win32", lambda: None)
    monkeypatch.setattr(sender, "find_wechat_main", lambda: (1, (0, 0, 1000, 800)))
    monkeypatch.setattr(sender, "wait_idle", lambda: None)
    monkeypatch.setattr(sender, "force_foreground", lambda _hwnd: None)
    monkeypatch.setattr(sender, "hide_overlays", lambda _hwnd: None)


def test_verify_remark_searches_and_opens_chat(monkeypatch):
    _mock_windows(monkeypatch)
    search_calls = []

    def fake_search(hwnd, rect, remark, settings):
        search_calls.append((hwnd, rect, remark, settings))

    monkeypatch.setattr(sender, "_search_and_open", fake_search)

    result = verify_remark("  买家A  ", Settings())

    assert len(search_calls) == 1
    assert search_calls[0][2] == "买家A"
    assert result.expected_remark == "买家A"
    assert result.header_name == "买家A"


def test_send_uses_search_and_clipboard(monkeypatch):
    _mock_windows(monkeypatch)
    search_calls = []
    clip_calls = []
    key_calls = []
    click_calls = []

    monkeypatch.setattr(sender, "_search_and_open", lambda *a: search_calls.append(a))
    monkeypatch.setattr(sender, "clip_set_text", lambda v: clip_calls.append(v))
    monkeypatch.setattr(sender, "clip_set_image", lambda p: None)
    monkeypatch.setattr(sender, "send_key", lambda k: key_calls.append(k))
    monkeypatch.setattr(sender, "click", lambda *a: click_calls.append(a))
    monkeypatch.setattr(sender, "_random_delay", lambda s: None)

    send("买家A", "消息", [], Settings())

    assert len(search_calls) == 1
    assert clip_calls == ["消息"]
    assert key_calls == ["ctrl,v", "alt,s"]


def test_send_rejects_blank_remark_or_empty_payload(monkeypatch):
    monkeypatch.setattr(sender.sys, "platform", "win32")
    monkeypatch.setattr(sender, "require_win32", pytest.fail)

    with pytest.raises(ValueError, match="发送载荷不能为空"):
        sender.send("", "消息", [], Settings())

    with pytest.raises(ValueError, match="发送载荷不能为空"):
        sender.send("买家A", "   ", [], Settings())


def test_send_raises_on_missing_image(monkeypatch):
    _mock_windows(monkeypatch)
    monkeypatch.setattr(sender, "_search_and_open", lambda *a: None)
    monkeypatch.setattr(sender, "clip_set_text", lambda v: None)
    monkeypatch.setattr(sender, "send_key", lambda k: None)
    monkeypatch.setattr(sender, "click", lambda *a: None)
    monkeypatch.setattr(sender, "_random_delay", lambda s: None)

    with pytest.raises(FileNotFoundError, match="发送图片不存在"):
        sender.send("买家A", "消息", ["/nonexistent.jpg"], Settings())
