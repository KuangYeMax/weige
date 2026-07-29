# -*- coding: utf-8 -*-
"""smoke_test_diag.py — 带诊断的冒烟测试

开 strict_verify=True，发送前后截主窗口全屏图，打印真验证详情。
用于诊断「success=True 但消息没收到」的问题。

用法：python scripts/smoke_test_diag.py
"""
from __future__ import annotations

import sys
import time
import os
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TARGET = sys.argv[1] if len(sys.argv) > 1 else "零哥"


def main() -> int:
    if sys.platform != "win32":
        print("❌ 仅支持 Windows")
        return 1

    from app.config import Settings
    from app.services.wechat.wechat_sender import WechatSender, _sender_mod

    settings = Settings()
    settings.wechat_strict_verify = True  # 开真验证
    # 2026-07-29 临时校准：实测输入框中心约 (796, 790)
    settings.wechat_input_x_offset = 796
    settings.wechat_input_y_offset = 98

    diag_dir = Path(settings.storage_root) / "smoke_diag"
    diag_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%H%M%S")

    print(f"准备向「{TARGET}」发送（开 strict_verify，带诊断截图）...")
    print("⚠️  发送期间请勿操作电脑")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    sender = WechatSender(settings)
    s = _sender_mod()

    # 发送前截主窗口全屏
    try:
        hwnd, rect = s.find_wechat_main()
        print(f"\n主窗口：hwnd={hwnd} rect={rect}")
        import pyautogui
        import win32gui
        # 验证 force_foreground 效果
        fg_before = win32gui.GetForegroundWindow()
        print(f"force_foreground 前台窗口：hwnd={fg_before} title={win32gui.GetWindowText(fg_before)!r}")
        s.force_foreground(hwnd)
        time.sleep(0.3)
        fg_after = win32gui.GetForegroundWindow()
        print(f"force_foreground 后台窗口：hwnd={fg_after} title={win32gui.GetWindowText(fg_after)!r}")
        if fg_after != hwnd:
            print(f"⚠️  微信仍不在前台！force_foreground 失败")
        before_path = str(diag_dir / f"{ts}_before_main.png")
        pyautogui.screenshot(before_path)
        print(f"发送前截图：{before_path}")
        # 列出所有微信窗口
        wins = []
        def enum(h, _):
            if win32gui.GetClassName(h) == "Qt51514QWindowIcon" and win32gui.IsWindowVisible(h):
                wins.append((h, win32gui.GetWindowText(h), win32gui.GetWindowRect(h)))
        win32gui.EnumWindows(enum, None)
        print(f"当前微信窗口列表：")
        for h, t, r in wins:
            print(f"  hwnd={h} title={t!r} rect={r}")
    except Exception as e:
        print(f"发送前截图异常：{e}")

    msg = f"[smoke-diag] 诊断冒烟 {ts}"
    print(f"\n发送内容：{msg}")
    t0 = time.time()

    try:
        r = sender.send_text(TARGET, msg)
    except Exception as e:
        print(f"❌ 发送抛异常：{type(e).__name__}: {e}")
        return 1
    elapsed = time.time() - t0

    # 发送后截主窗口全屏
    try:
        import pyautogui
        after_path = str(diag_dir / f"{ts}_after_main.png")
        pyautogui.screenshot(after_path)
        print(f"发送后截图：{after_path}")
    except Exception as e:
        print(f"发送后截图异常：{e}")

    print(f"\n{'='*50}")
    print(f"耗时：{elapsed:.1f}s")
    print(f"success：{r.success}")
    print(f"reason：{r.reason.value}")
    print(f"message：{r.message}")
    print(f"verified：{r.verified}")
    print(f"screenshot_path：{r.screenshot_path}")
    print(f"{'='*50}")

    if r.success:
        print("\n✅ 真验证通过（消息应已发出）")
    else:
        print(f"\n⚠️ 真验证未通过：{r.reason.value}")
        print(f"   这说明消息可能没发出——真验证生效了！")
    print(f"\n请查看诊断截图对比：{diag_dir}")
    return 0 if r.success else 2


if __name__ == "__main__":
    sys.exit(main())
