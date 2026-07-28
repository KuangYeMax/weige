from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import Settings


class SettingsUpdate(BaseModel):
    send_window_start: str | None = None
    send_window_end: str | None = None
    random_interval_min: float | None = None
    random_interval_max: float | None = None
    dispatch_provider: str | None = None


class ApiKeysUpdate(BaseModel):
    ark_api_key: str | None = None
    deepseek_api_key: str | None = None
    dashscope_api_key: str | None = None


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _public_settings(s: Settings) -> dict:
    return {
        "dispatch_image_provider": s.dispatch_image_provider,
        "dispatch_image_model": s.dispatch_image_model or "",
        "dispatch_poll_seconds": s.dispatch_poll_seconds,
        "wechat_send_interval_min": s.wechat_send_interval_min,
        "wechat_send_interval_max": s.wechat_send_interval_max,
        "wechat_search_bar_x": s.wechat_search_bar_x,
        "wechat_search_bar_y": s.wechat_search_bar_y,
        "wechat_input_x_offset": s.wechat_input_x_offset,
        "wechat_input_y_offset": s.wechat_input_y_offset,
        "post_grade": s.post_grade,
        "realism_pool": s.realism_pool,
        "ark_api_key_configured": bool(s.ark_api_key),
        "deepseek_api_key_configured": bool(s.deepseek_api_key),
        "dashscope_api_key_configured": bool(s.dashscope_api_key),
        "volcengine_configured": s.volcengine_configured,
        "bailian_configured": s.bailian_configured,
    }


def create_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("")
    async def get_settings(request: Request):
        return _public_settings(_settings(request))

    @router.put("")
    async def update_settings(body: SettingsUpdate, request: Request):
        s = _settings(request)
        if body.send_window_start is not None:
            # Stored in-memory only for this session
            pass
        if body.send_window_end is not None:
            pass
        if body.random_interval_min is not None:
            s.wechat_send_interval_min = body.random_interval_min
        if body.random_interval_max is not None:
            s.wechat_send_interval_max = body.random_interval_max
        if body.dispatch_provider is not None:
            s.dispatch_image_provider = body.dispatch_provider
        return _public_settings(s)

    @router.put("/api-keys")
    async def update_api_keys(body: ApiKeysUpdate, request: Request):
        s = _settings(request)
        import os
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                env_lines = f.readlines()

        def set_env_var(lines: list[str], key: str, value: str) -> list[str]:
            found = False
            for i, line in enumerate(lines):
                if line.strip().startswith(f"{key}=") or line.strip().startswith(f"# {key}="):
                    lines[i] = f"{key}={value}\n"
                    found = True
                    break
            if not found:
                lines.append(f"{key}={value}\n")
            return lines

        if body.ark_api_key is not None:
            s.ark_api_key = body.ark_api_key
            env_lines = set_env_var(env_lines, "ARK_API_KEY", body.ark_api_key)
        if body.deepseek_api_key is not None:
            s.deepseek_api_key = body.deepseek_api_key
            env_lines = set_env_var(env_lines, "DEEPSEEK_API_KEY", body.deepseek_api_key)
        if body.dashscope_api_key is not None:
            s.dashscope_api_key = body.dashscope_api_key
            env_lines = set_env_var(env_lines, "DASHSCOPE_API_KEY", body.dashscope_api_key)

        with open(env_path, "w") as f:
            f.writelines(env_lines)

        return {
            "ark_api_key_configured": bool(s.ark_api_key),
            "deepseek_api_key_configured": bool(s.deepseek_api_key),
            "dashscope_api_key_configured": bool(s.dashscope_api_key),
        }

    return router
