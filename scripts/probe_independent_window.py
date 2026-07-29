# -*- coding: utf-8 -*-
"""probe_independent_window.py — 实测微信独立聊天窗口方案可行性

验证计划 §3.2「优先实测」项：4.1.x 是否支持把单聊拉成独立窗口，
若可用则 GetWindowText 直接读到好友名，身份校验最可靠。

用法：
  1. 手动在微信里把一个单聊（如「文件传输助手」或某好友）拉成独立窗口
     （右键会话 →「独立窗口打开」/「在新窗口打开」，或拖拽会话标签出来）
  2. 运行本脚本：python scripts/probe_independent_window.py
  3. 观察输出：是否有 title == 好友名的独立窗口

退出码：0=发现独立聊天窗口（方案可行），1=未发现（方案不可用或未拉出）
"""
from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MAIN_WINDOW_TITLES = {"微信", "Weixin"}


def main() -> int:
    if sys.platform != "win32":
        print("❌ 仅支持 Windows")
        return 1

    try:
        import win32gui
    except ImportError:
        print("❌ 未安装 pywin32")
        return 1

    windows: list[dict] = []

    def enum(hwnd, _):
        cls = win32gui.GetClassName(hwnd)
        if cls != "Qt51514QWindowIcon":
            return
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        r = win32gui.GetWindowRect(hwnd)
        windows.append({
            "hwnd": hwnd,
            "class": cls,
            "title": title,
            "rect": r,
            "w": r[2] - r[0],
            "h": r[3] - r[1],
        })

    win32gui.EnumWindows(enum, None)

    if not windows:
        print("❌ 未找到任何 Qt51514QWindowIcon 可见窗口，微信可能未运行或未登录")
        return 1

    print(f"找到 {len(windows)} 个微信相关窗口：\n")
    main_windows = []
    chat_windows = []
    for w in windows:
        is_main = w["title"] in MAIN_WINDOW_TITLES
        tag = "【主窗口】" if is_main else "【独立聊天?】"
        print(f"  {tag} hwnd={w['hwnd']} title={w['title']!r} size={w['w']}x{w['h']} rect={w['rect']}")
        if is_main:
            main_windows.append(w)
        else:
            chat_windows.append(w)

    print()
    if chat_windows:
        print(f"✅ 发现 {len(chat_windows)} 个非主窗口微信窗口（疑似独立聊天窗口）：")
        for w in chat_windows:
            print(f"   - title={w['title']!r}（{len(w['title'])} 字符）")
            print(f"     hwnd={w['hwnd']} size={w['w']}x{w['h']}")
        print()
        print("【结论】独立聊天窗口方案可行！")
        print("   GetWindowText 可直接读到好友名，身份校验可用窗口标题精确匹配。")
        print("   WechatSender._verify_chat_identity 应优先走此路径。")
        return 0
    else:
        print("⚠️  未发现独立聊天窗口。")
        print("   请先在微信里手动把一个单聊拉成独立窗口：")
        print("     方法1：右键会话 →「独立窗口打开」/「在新窗口打开」")
        print("     方法2：拖拽会话标签到桌面空白处")
        print("   拉出后重新运行本脚本。")
        print()
        print("【当前结论】4.1.1.19 主窗口标题读不到好友名，")
        print("   若不支持独立窗口则身份校验退化为 OCR / 信任搜索结果。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
