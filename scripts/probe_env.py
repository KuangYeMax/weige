# -*- coding: utf-8 -*-
"""
probe_env.py — 微信 UIAutomation 发送层重构 · Step 0 环境探针
====================================================================

一次性、独立脚本，不 import 项目任何代码（app.* 一律不碰）。
目的：在动手重构前，先用真实环境验证「基于 UIAutomation 的控件定位」
在你的微信版本 / DPI / 分辨率 上是否可行。

依次执行 9 步，每步独立 try/except：
  1. 读取微信客户端真实版本号（exe 文件版本信息）
  2. 打印 DPI 缩放、主屏分辨率、显示器数量
  3. 能否拿到微信主窗口句柄（hwnd != 0）
  4. 微信当前是否处于已登录状态
  5. 能否枚举会话列表，打印前 5 个会话名称
  6. 能否按精确名称定位「文件传输助手」并打开该会话
  7. 向「文件传输助手」发送一条文本消息
  8. 向「文件传输助手」发送一张本地测试图片，并确认是图片消息而非文件附件
  9. 回读该会话最后一条消息，验证 7、8 是否真的发出去了

每步打印「通过 / 失败 + 具体异常」，不中途抛异常终止，跑完全部 9 步输出汇总。

⚠️ 本脚本会真实操作你的微信：向「文件传输助手」发送 1 条文本 + 1 张测试图。
   运行前请确保：微信已登录、主窗口可正常显示（未最小化到托盘）。
"""

from __future__ import annotations

import os
import sys
import time
import ctypes
import traceback
import tempfile
from dataclasses import dataclass, field

# Windows 控制台中文输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 探针目标 ──────────────────────────────────────────────
TARGET_FRIEND = "文件传输助手"   # 用自己的文件传输助手做发收测试，最安全
LIB_VERSION_FLOOR = "4.1.6"     # pyweixin 官方声明支持的下限；4.1.1.19 低于此

# 测试图临时路径
TEST_IMG = os.path.join(tempfile.gettempdir(), "probe_test_img.png")


# ── pyweixin 一次性 import（很多步依赖它）─────────────────
PYWEIXIN_OK = False
PYWEIXIN_ERR = ""
_LIB = {}
if sys.platform == "win32":
    try:
        import pyweixin  # noqa: F401  触发 Uielements 实例化（含 language_detector）
        from pyweixin.WeChatTools import Tools, Navigator
        from pyweixin.WeChatAuto import Messages, Files, Monitor
        from pyweixin.Uielements import Main_window, SideBar, Texts
        from pyweixin import GlobalConfig
        _LIB.update(Tools=Tools, Navigator=Navigator, Messages=Messages,
                    Files=Files, Monitor=Monitor,
                    Main_window=Main_window, SideBar=SideBar, Texts=Texts,
                    GlobalConfig=GlobalConfig)
        PYWEIXIN_OK = True
    except Exception as e:
        PYWEIXIN_ERR = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"


# ── 结果记录 ──────────────────────────────────────────────
@dataclass
class StepResult:
    idx: int
    name: str
    passed: bool
    detail: str = ""
    error: str = ""


_results: list[StepResult] = []


def run_step(idx: int, name: str, fn):
    print(f"\n{'='*64}\n步骤 {idx}: {name}\n{'='*64}")
    try:
        detail = fn() or "通过"
        print(f"✅ [通过] {detail}")
        _results.append(StepResult(idx, name, True, detail, ""))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"❌ [失败] {err}")
        print(traceback.format_exc())
        _results.append(StepResult(idx, name, False, "", err))
    return _results[-1]


def need_lib():
    if not PYWEIXIN_OK:
        raise RuntimeError(f"pyweixin 导入失败，无法执行本步：\n{PYWEIXIN_ERR}")


# ── 生成测试图片（红底白字，便于人眼与回读辨识）─────────────
def make_test_image():
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (260, 260), (210, 40, 40))
        d = ImageDraw.Draw(img)
        d.text((28, 120), "PROBE-TEST", fill=(255, 255, 255))
        img.save(TEST_IMG)
        return os.path.isfile(TEST_IMG) and f"{os.path.getsize(TEST_IMG)} bytes"
    except Exception as e:
        raise RuntimeError(f"生成测试图失败: {e}")


# ── 各步实现 ──────────────────────────────────────────────
def step1_version():
    """从 exe 文件版本信息读取微信真实版本号，并对比注册表/库的取值"""
    import win32api, winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin") as k:
            installdir = winreg.QueryValueEx(k, "InstallPath")[0]
    except FileNotFoundError:
        raise RuntimeError("注册表 Software\\Tencent\\Weixin 不存在，可能未安装 4.x 微信")
    exe = os.path.join(installdir, "Weixin.exe")
    if not os.path.isfile(exe):
        raise RuntimeError(f"微信 exe 不存在: {exe}")
    info = win32api.GetFileVersionInfo(exe, "\\")
    ms, ls = info["FileVersionMS"], info["FileVersionLS"]
    ver = f"{(ms >> 16) & 0xffff}.{ms & 0xffff}.{(ls >> 16) & 0xffff}.{ls & 0xffff}"
    # 对比库的注册表解码版本
    lib_ver = "N/A"
    try:
        need_lib()
        lib_ver = _LIB["Tools"].get_weixin_version()
    except Exception as e:
        lib_ver = f"(库取版本失败: {e})"
    flag = ""
    try:
        from packaging import version as pv
        if pv.parse(ver) < pv.parse(LIB_VERSION_FLOOR):
            flag = f" ⚠️ 低于 pyweixin 声明支持下限 {LIB_VERSION_FLOOR}"
    except Exception:
        pass
    return f"exe文件版本={ver} | 注册表(库)版本={lib_ver}{flag} | exe={exe}"


def step2_env():
    """DPI 缩放、主屏分辨率、显示器数量"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hdc = user32.GetDC(0)
    dpi = gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
    user32.ReleaseDC(0, hdc)
    scale = round(dpi / 96.0 * 100)
    monitors = user32.GetSystemMetrics(80)  # SM_CMONITORS
    w = user32.GetSystemMetrics(0)          # SM_CXSCREEN
    h = user32.GetSystemMetrics(1)          # SM_CYSCREEN
    # 进程感知的 DPI（Win10 1607+）
    try:
        process_dpi = user32.GetDpiForSystem()
    except Exception:
        process_dpi = dpi
    return (f"DPI缩放={scale}% (物理DPI={dpi}, 系统DPI={process_dpi}) | "
            f"主屏分辨率={w}x{h} | 显示器数={monitors}")


def step3_hwnd():
    """能否拿到微信主窗口句柄"""
    import win32gui
    hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "微信")
    if not hwnd:
        hwnd = win32gui.FindWindow("Qt51514QWindowIcon", "Weixin")
    if not hwnd:
        # 兜底：枚举所有 Qt51514QWindowIcon 窗口
        found = []
        def _enum(h, _):
            if win32gui.GetClassName(h) == "Qt51514QWindowIcon" and win32gui.IsWindowVisible(h):
                found.append((h, win32gui.GetWindowText(h)))
        win32gui.EnumWindows(_enum, None)
        raise RuntimeError(
            f"FindWindow 返回 0，未找到微信主窗口（疑似 issue#147：DPI 缩放下 find_wx_window 返回 0）。"
            f"枚举到的 Qt51514QWindowIcon 窗口={found}"
        )
    cls = win32gui.GetClassName(hwnd)
    title = win32gui.GetWindowText(hwnd)
    is_vis = win32gui.IsWindowVisible(hwnd)
    return f"hwnd={hwnd}, class={cls}, title={title}, visible={bool(is_vis)}"


def step4_login():
    """微信是否已登录"""
    need_lib()
    Tools = _LIB["Tools"]
    Navigator = _LIB["Navigator"]
    if not Tools.is_weixin_running():
        raise RuntimeError("Weixin.exe 进程不存在，微信未运行")
    main_window = Navigator.open_weixin()
    cn = main_window.class_name()
    if cn == "mmui::LoginWindow":
        raise RuntimeError("微信处于登录窗口，未登录")
    if cn != "mmui::MainWindow":
        raise RuntimeError(f"主窗口 class_name 非预期: {cn}（既不是 LoginWindow 也不是 MainWindow）")
    return f"已登录, 主窗口 class_name={cn}"


def step5_enum_sessions():
    """枚举会话列表，打印前 5 个"""
    need_lib()
    Navigator = _LIB["Navigator"]
    Main_window = _LIB["Main_window"]
    SideBar = _LIB["SideBar"]
    main_window = Navigator.open_weixin()
    # 确保在「聊天」侧边栏
    session_list = main_window.child_window(**Main_window.SessionList)
    if not session_list.exists(timeout=1):
        main_window.child_window(**SideBar.Weixin).click_input()
        time.sleep(0.5)
        session_list = main_window.child_window(**Main_window.SessionList)
    items = session_list.children(control_type="ListItem")
    names = []
    for it in items[:5]:
        names.append(f"{it.window_text()} (auto_id={it.automation_id()})")
    if not items:
        raise RuntimeError("会话列表为空（可能微信刚启动未加载，或会话列表定位失败）")
    return f"会话总数={len(items)} | 前5个: {names}"


def step6_locate_target():
    """按精确名称定位「文件传输助手」并打开会话"""
    need_lib()
    Navigator = _LIB["Navigator"]
    Texts = _LIB["Texts"]
    # search_friend 内部用 window_text()==friend 精确匹配搜索结果
    main_window = Navigator.search_friend(friend=TARGET_FRIEND)
    # 验证顶部当前会话名 == 目标
    label = dict(Texts.CurrentChatNameText)
    label["title"] = TARGET_FRIEND
    cur = main_window.child_window(**label)
    if not cur.exists(timeout=0.8):
        raise RuntimeError(
            f"搜索后主界面顶部未显示「{TARGET_FRIEND}」，可能未精确命中"
        )
    return f"已精确定位并打开「{TARGET_FRIEND}」会话"


def step7_send_text():
    """向文件传输助手发送一条文本"""
    need_lib()
    Messages = _LIB["Messages"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    msg = f"[probe-text] 探针文本测试 {stamp}"
    Messages.send_messages_to_friend(
        friend=TARGET_FRIEND, messages=[msg], close_weixin=False)
    # 暂存供 step9 比对
    step7_send_text.last_msg = msg
    return f"已发送文本: {msg}"


def step8_send_image():
    """向文件传输助手发送一张本地测试图片，并确认是图片消息而非文件附件"""
    need_lib()
    Files = _LIB["Files"]
    if not os.path.isfile(TEST_IMG):
        raise RuntimeError(f"测试图不存在: {TEST_IMG}")
    Files.send_files_to_friend(
        friend=TARGET_FRIEND, files=[TEST_IMG], close_weixin=False)
    # 是否为图片消息的最终判定放到 step9 回读时分析（依赖消息类型字段）
    return f"已通过 pyweixin.Files 发送测试图: {TEST_IMG}（图片/附件类型见 step9 回读）"


def step9_readback():
    """回读最后几条消息，验证 step7/step8 是否真的发出，并判断图片是否为图片消息"""
    need_lib()
    Messages = _LIB["Messages"]
    hist = Messages.pull_messages(
        friend=TARGET_FRIEND, number=3, close_weixin=False)
    if not hist:
        raise RuntimeError("回读到 0 条消息，发送可能未成功")
    last = hist[-1]
    # hist 元素: {'消息发送人':, '消息内容':, '消息类型':}
    mtype = last.get("消息类型", "?")
    mcontent = last.get("消息内容", "?")
    # 找出最近一条图片/文件类消息
    img_like = [h for h in hist if h.get("消息类型") in ("图片", "文件", "[图片]", "[文件]")]
    verdict = ""
    if mtype in ("文件", "[文件]"):
        verdict = " ⚠️ 回读最后一条为【文件附件】，非图片消息！不符合任务第8步要求。"
    elif mtype in ("图片", "[图片]"):
        verdict = " ✅ 回读最后一条为【图片消息】，符合要求。"
    else:
        verdict = f" ❓ 回读最后一条消息类型={mtype}，需人工确认是否图片消息。"
    sent_text = getattr(step7_send_text, "last_msg", "")
    text_found = any(sent_text in (h.get("消息内容", "") or "") for h in hist)
    text_verdict = "✅文本已发出" if text_found else "⚠️未在回读中找到所发文本"
    return (f"回读{len(hist)}条, 最后一条={{类型:{mtype}, 内容:{mcontent!r}}} {verdict} "
            f"| {text_verdict} | 全部回读={hist}")


# ── 主流程 ────────────────────────────────────────────────
def main():
    print("╔" + "═"*62 + "╗")
    print("║  微信 UIAutomation 发送层重构 · Step 0 环境探针           ║")
    print("╚" + "═"*62 + "╝")
    print(f"目标好友: {TARGET_FRIEND}")
    print(f"pyweixin 导入: {'✅ 成功' if PYWEIXIN_OK else '❌ 失败'}")
    if not PYWEIXIN_OK:
        print(PYWEIXIN_ERR)

    # 先生成测试图（不占步骤编号，但失败会记入 step8）
    try:
        img_info = make_test_image()
        print(f"测试图已生成: {TEST_IMG} ({img_info})")
    except Exception as e:
        print(f"⚠️ 测试图生成失败: {e}")

    steps = [
        (1, "读取微信真实版本号", step1_version),
        (2, "DPI/分辨率/显示器数", step2_env),
        (3, "拿到微信主窗口句柄", step3_hwnd),
        (4, "微信已登录", step4_login),
        (5, "枚举会话列表前5个", step5_enum_sessions),
        (6, f"精确定位「{TARGET_FRIEND}」", step6_locate_target),
        (7, f"发送一条文本给「{TARGET_FRIEND}」", step7_send_text),
        (8, f"发送一张测试图片给「{TARGET_FRIEND}」", step8_send_image),
        (9, "回读最后一条消息验证", step9_readback),
    ]
    for idx, name, fn in steps:
        run_step(idx, name, fn)
        time.sleep(0.5)

    # ── 汇总 ──────────────────────────────────────────────
    print("\n\n" + "╔" + "═"*62 + "╗")
    print("║                       9 步汇总                            ║")
    print("╠" + "═"*62 + "╣")
    passed = sum(1 for r in _results if r.passed)
    for r in _results:
        mark = "✅" if r.passed else "❌"
        line = f"{mark} 步骤{r.idx} {r.name}"
        if r.passed:
            line += f"  →  {r.detail[:80]}"
        else:
            line += f"  →  {r.error[:80]}"
        print("║ " + line.ljust(61) + "║")
    print("╠" + "═"*62 + "╣")
    print(f"║  通过 {passed}/9   失败 {9-passed}/9".ljust(63) + "║")
    print("╚" + "═"*62 + "╝")

    # ── 自动结论 ───────────────────────────────────────────
    print("\n【自动结论】")
    s1 = next((r for r in _results if r.idx == 1), None)
    if s1 and s1.passed:
        print(f"  · 微信版本: {s1.detail}")
    if s1 and "低于" in s1.detail:
        print(f"  ⚠️ 你的微信版本低于 pyweixin 声明支持下限 {LIB_VERSION_FLOOR}，"
              f"库的 UI 元素定位可能失效。")
    s3 = next((r for r in _results if r.idx == 3), None)
    s2 = next((r for r in _results if r.idx == 2), None)
    if s3 and not s3.passed:
        print(f"  ⚠️ hwnd 获取失败（疑似 issue#147）。当前 {s2.detail if s2 else ''}")
        print(f"     排查方向：①DPI 缩放是否非100%；②是否需开启系统无障碍/讲述人服务。")
    s9 = next((r for r in _results if r.idx == 9), None)
    if s9 and s9.passed and "文件附件" in s9.detail:
        print("  ⚠️ pyweixin.Files 发图片走文件剪贴板 → 发成文件附件，"
              "不满足「图片消息」要求；Step 2 需改用 Clipboard.SetImage 方案。")
    if passed == 9:
        print("  ✅ 全部通过，可进入 Step 1。")
    else:
        print(f"  ⚠️ {9-passed} 项未通过，需处理后再进入 Step 1。")


if __name__ == "__main__":
    main()
