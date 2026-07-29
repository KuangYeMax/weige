# -*- coding: utf-8 -*-
"""probe_clipboard_paste.py — 单独诊断 clip_set_text + ctrl+v 粘贴链路

文件传输助手会话已打开的情况下，单独测试：
1. clip_set_text 写入剪贴板 → 立即读回，确认内容正确
2. click 输入框 + ctrl+v → OCR 读输入框文字，看是否粘贴成功

⚠️ 操作期间请勿动电脑。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    if sys.platform != "win32":
        print("❌ 仅支持 Windows")
        return 1

    from app.services.wechat import win32 as w
    from app.services.wechat import sender as s
    import win32clipboard
    import win32gui
    import win32con
    import pyautogui
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    from PIL import Image

    print("⚠️  请确认微信主窗口在前台 + 文件传输助手会话已打开")
    print("    操作期间请勿动电脑")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    # 准备窗口（处理屏幕外情况）
    hwnd, rect = s._prepare_wechat_window()
    if rect[0] < -1000 or rect[1] < -1000:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
        win32gui.MoveWindow(hwnd, 0, 0, 1225, 888, True)
        time.sleep(0.3)
        rect = win32gui.GetWindowRect(hwnd)
    print(f"主窗口：hwnd={hwnd} rect={rect}")

    # 强制前台
    s.force_foreground(hwnd)
    time.sleep(0.3)
    print(f"前台窗口：{win32gui.GetWindowText(win32gui.GetForegroundWindow())!r}")

    # ── 第 1 步：测试 clip_set_text + 读回 ──
    print("\n" + "="*50)
    print("第 1 步：测试 clip_set_text")
    print("="*50)
    test_text = f"clip_test_{int(time.time())}"
    print(f"写入文本：{test_text!r}")
    try:
        s.clip_set_text(test_text)
        print("clip_set_text 调用完成（未抛异常）")
    except Exception as e:
        print(f"❌ clip_set_text 抛异常：{type(e).__name__}: {e}")
        return 1

    # 立即读回
    time.sleep(0.1)
    readback = None
    for _ in range(3):
        try:
            win32clipboard.OpenClipboard()
            readback = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            break
        except Exception as e:
            print(f"读剪贴板失败重试：{e}")
            time.sleep(0.2)
    print(f"剪贴板读回：{readback!r}")
    if readback == test_text:
        print("✅ 剪贴板读写一致")
    else:
        print(f"❌ 剪贴板内容不一致！期望={test_text!r} 实际={readback!r}")
        return 1

    # ── 第 2 步：测试 click 输入框 + ctrl+v ──
    print("\n" + "="*50)
    print("第 2 步：测试 click 输入框 + ctrl+v")
    print("="*50)
    input_x, input_y = s._resolve_input_area(rect, s.Settings())
    print(f"输入区坐标：({input_x}, {input_y})")

    # 点击输入框
    s.click(rect, input_x - rect[0], input_y - rect[1])
    time.sleep(0.5)

    # 截图输入框（点击后）
    diag_dir = Path("storage/smoke_diag")
    diag_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%H%M%S")
    after_click_path = diag_dir / f"{ts}_paste_1_after_click.png"
    region = (input_x - 100, input_y - 20, 200, 40)
    pyautogui.screenshot(region=region).save(str(after_click_path))
    print(f"点击后输入框截图：{after_click_path}")

    # ctrl+v
    print("发送 ctrl+v...")
    s.send_key("ctrl,v")
    time.sleep(0.5)

    # 截图输入框（粘贴后）
    after_paste_path = diag_dir / f"{ts}_paste_2_after_ctrlv.png"
    pyautogui.screenshot(region=region).save(str(after_paste_path))
    print(f"ctrl+v 后输入框截图：{after_paste_path}")

    # OCR 读输入框
    print("\nOCR 识别输入框内容...")
    ocr = RapidOCR()
    for label, path in [("点击后", after_click_path), ("ctrl+v 后", after_paste_path)]:
        img = Image.open(path)
        arr = np.array(img)
        result, _ = ocr(arr)
        text_in_box = ""
        if result:
            # 拼接所有识别文字
            text_in_box = " ".join(box[1] for box in result)
        print(f"  [{label}] 输入框 OCR 识别：{text_in_box!r}")

    if test_text in text_in_box:
        print(f"\n✅ 粘贴成功！输入框包含 {test_text!r}")
    else:
        print(f"\n❌ 粘贴失败！输入框不含 {test_text!r}")
        print(f"   可能原因：")
        print(f"   1. 输入框没获得焦点（click 没点到）")
        print(f"   2. 微信输入框对标准 ctrl+v 不响应")
        print(f"   3. 剪贴板内容被清空（其他程序占用）")
        print(f"\n   请看截图对比：{after_click_path} vs {after_paste_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())