# -*- coding: utf-8 -*-
"""probe_search_results.py — 探测微信搜索结果列表的 OCR 识别

搜索"文件传输助手"后截图结果列表区域，OCR 识别每项标题，
验证能否精确匹配到目标好友。不按 enter，避免打开错的会话。

用法：python scripts/probe_search_results.py [搜索词]
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

    keyword = sys.argv[1] if len(sys.argv) > 1 else "文件传输助手"

    from app.services.wechat import win32 as w
    from app.services.wechat import sender as s
    from rapidocr_onnxruntime import RapidOCR
    import pyautogui
    import numpy as np

    print(f"搜索关键词：{keyword}")
    print("⚠️  操作期间请勿动电脑")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    # 准备窗口
    hwnd, rect = s._prepare_wechat_window()
    # 处理窗口最小化到屏幕外（rect=-32000）的情况
    if rect[0] < -1000 or rect[1] < -1000:
        import win32gui, win32con
        print(f"窗口在屏幕外 rect={rect}，尝试恢复...")
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
        win32gui.MoveWindow(hwnd, 0, 0, 1225, 888, True)
        time.sleep(0.3)
        rect = win32gui.GetWindowRect(hwnd)
        print(f"恢复后 rect={rect}")
    print(f"主窗口：hwnd={hwnd} rect={rect}")

    # 搜索
    s.click(rect, s.wechat_search_bar_x if hasattr(s, 'wechat_search_bar_x') else 170,
            48)
    time.sleep(0.3)
    s.send_key("ctrl,a")
    time.sleep(0.1)
    s.clip_set_text(keyword)
    s.send_key("ctrl,v")
    print("已输入搜索词，等待结果加载...")
    time.sleep(2.0)

    # 截图搜索结果列表区域（主窗口左侧，搜索栏下方）
    # 左侧栏宽度约 400px，结果从 y=80 开始
    left, top = rect[0], rect[1] + 70
    width = 420
    height = 600
    region = (left, top, width, height)
    print(f"截图区域：{region}")
    img = pyautogui.screenshot(region=region)
    diag_dir = Path("storage/smoke_diag")
    diag_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%H%M%S")
    img_path = diag_dir / f"{ts}_search_results.png"
    img.save(str(img_path))
    print(f"截图已存：{img_path}")

    # OCR 识别
    print("\nOCR 识别中...")
    ocr = RapidOCR()
    arr = np.array(img)
    result, _ = ocr(arr)
    if not result:
        print("❌ OCR 未识别到任何文字")
        return 1

    print(f"\n识别到 {len(result)} 个文本块：")
    for i, box in enumerate(result):
        text = box[1]
        conf = box[2]
        # box[0] 是 4 个点的坐标 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        pts = box[0]
        cx = sum(p[0] for p in pts) / 4
        cy = sum(p[1] for p in pts) / 4
        # 转换到屏幕坐标
        screen_x = left + cx
        screen_y = top + cy
        match_tag = " ✅ 精确匹配" if text == keyword else ""
        print(f"  [{i}] text={text!r} conf={conf:.2f} "
              f"中心=({screen_x:.0f},{screen_y:.0f}){match_tag}")

    # 找精确匹配
    matches = [(i, box) for i, box in enumerate(result) if box[1] == keyword]
    if matches:
        i, box = matches[0]
        pts = box[0]
        cx = sum(p[0] for p in pts) / 4
        cy = sum(p[1] for p in pts) / 4
        screen_x = left + cx
        screen_y = top + cy
        print(f"\n✅ 找到精确匹配：{keyword}")
        print(f"   屏幕坐标=({screen_x:.0f},{screen_y:.0f})")
        print(f"   点击此坐标可精确选中目标好友")
        return 0
    else:
        print(f"\n⚠️  未找到精确匹配 '{keyword}'")
        print("   可能搜索结果列表区域截取不准，或 OCR 识别有误")
        return 1


if __name__ == "__main__":
    sys.exit(main())
