from __future__ import annotations

import sys

from fastapi import APIRouter
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

    return router
