# -*- coding: utf-8 -*-
"""smoke_test_send.py — 真实发送冒烟测试

向「文件传输助手」发 1 条文本，端到端验证 feat/uia-sender 重构可用。
会真实操作微信，发送期间请勿操作电脑。

用法：python scripts/smoke_test_send.py
"""
from __future__ import annotations

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TARGET = "文件传输助手"


def main() -> int:
    if sys.platform != "win32":
        print("❌ 仅支持 Windows")
        return 1

    from app.config import Settings
    from app.services.wechat.wechat_sender import WechatSender

    settings = Settings()
    # 关闭真验证（冒烟先验证基础链路，不卡真验证阈值）
    settings.wechat_strict_verify = False

    print(f"准备向「{TARGET}」发送 1 条冒烟文本...")
    print("⚠️  发送期间请勿操作电脑（脚本会等输入空闲 0.5s）")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    sender = WechatSender(settings)
    msg = f"[smoke-test] 重构后冒烟 {time.strftime('%H%M%S')}"
    print(f"\n发送内容：{msg}")

    t0 = time.time()
    try:
        r = sender.send_text(TARGET, msg)
    except Exception as e:
        print(f"❌ 发送抛异常：{type(e).__name__}: {e}")
        return 1
    elapsed = time.time() - t0

    print(f"\n{'='*50}")
    print(f"耗时：{elapsed:.1f}s")
    print(f"success：{r.success}")
    print(f"reason：{r.reason.value}")
    print(f"message：{r.message}")
    print(f"elapsed_ms：{r.elapsed_ms}")
    print(f"verified：{r.verified}")
    print(f"screenshot_path：{r.screenshot_path}")
    print(f"{'='*50}")

    if r.success:
        print("\n✅ 冒烟发送成功！重构端到端可用。")
        if r.screenshot_path:
            import os
            print(f"   截图留证：{r.screenshot_path}（{os.path.getsize(r.screenshot_path)} bytes）")
        return 0
    else:
        print(f"\n❌ 冒烟发送失败：{r.reason.value} - {r.message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
