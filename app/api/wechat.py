from __future__ import annotations

import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def create_wechat_router() -> APIRouter:
    router = APIRouter(prefix="/api/wechat", tags=["wechat"])

    @router.get("/status")
    async def wechat_status():
        if sys.platform != "win32":
            return {
                "platform_supported": False,
                "connected": None,
                "reason": "当前系统为 macOS/Linux，微信自动化仅支持 Windows",
            }

        try:
            from app.services.wechat.win32 import find_wechat_main
            find_wechat_main()
        except Exception as exc:
            return {
                "platform_supported": True,
                "connected": False,
                "reason": f"微信窗口未找到: {exc}",
            }

        return {
            "platform_supported": True,
            "connected": True,
            "reason": None,
        }

    @router.get("/health")
    async def wechat_health(request: Request):
        """返回微信发送层启动自检报告（feat/uia-sender，计划 §7.3）。

        前端用此结果在最显眼处显示自检告警条：
        - healthy=False → 红色告警「自检未通过：<具体项>，已切换演习模式」
        - healthy=True  → 绿色「自检通过」
        - 无报告（None）→ 灰色「未跑自检（非 Windows 或启动异常）」
        """
        health = getattr(request.app.state, "wechat_health", None)
        if health is None:
            return {
                "healthy": None,
                "failed_checks": [],
                "environment": {},
                "details": "未跑自检（非 Windows 或启动异常）",
                "checked_at": None,
            }
        return {
            "healthy": health.healthy,
            "failed_checks": health.failed_checks,
            "environment": health.environment,
            "details": health.details,
            "checked_at": health.checked_at,
        }

    return router
