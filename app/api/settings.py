from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

import os

from app.config import Settings

_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".env",
)


def _persist_env_value(key: str, value: str) -> None:
    """把设置写回 .env 文件，进程重启后依然生效。

    与 update_api_keys 的持久化保持一致；对含换行/引号的值加双引号包裹并转义，
    避免破坏 .env 解析。
    """
    lines: list[str] = []
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    # 含换行、双引号或 # 时包裹双引号，内部转义
    if ("\n" in value) or ('"' in value) or ("#" in value):
        inner = value.replace("\\", "\\\\").replace('"', '\\"')
        entry = f'{key}="{inner}"\n'
    else:
        entry = f"{key}={value}\n"
    for i, line in enumerate(lines):
        st = line.strip()
        if st.startswith(f"{key}=") or st.startswith(f"# {key}="):
            lines[i] = entry
            break
    else:
        lines.append(entry)
    with open(_ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


# body 字段名 -> (Settings 字段名, 对应的 .env 键)。
# 注意 random_interval_min/max 在 Settings 中实际名为 wechat_send_interval_min/max。
# send_window_start/end 为纯内存态（仅本次会话生效），不在此列。
_PERSISTABLE_FIELDS: dict[str, tuple[str, str]] = {
    "random_interval_min": ("wechat_send_interval_min", "WECHAT_SEND_INTERVAL_MIN"),
    "random_interval_max": ("wechat_send_interval_max", "WECHAT_SEND_INTERVAL_MAX"),
    "dispatch_provider": ("dispatch_image_provider", "DISPATCH_IMAGE_PROVIDER"),
    "wechat_opening_text": ("wechat_opening_text", "WECHAT_OPENING_TEXT"),
    "wechat_search_bar_x": ("wechat_search_bar_x", "WECHAT_SEARCH_BAR_X"),
    "wechat_search_bar_y": ("wechat_search_bar_y", "WECHAT_SEARCH_BAR_Y"),
    "wechat_input_x_offset": ("wechat_input_x_offset", "WECHAT_INPUT_X_OFFSET"),
    "wechat_input_y_offset": ("wechat_input_y_offset", "WECHAT_INPUT_Y_OFFSET"),
    "consistency_check": ("consistency_check", "CONSISTENCY_CHECK"),
    "consistency_check_max_retries": ("consistency_check_max_retries", "CONSISTENCY_CHECK_MAX_RETRIES"),
    "bailian_thinking_mode": ("bailian_thinking_mode", "BAILIAN_THINKING_MODE"),
    "degraded_retry_enabled": ("degraded_retry_enabled", "DEGRADED_RETRY_ENABLED"),
    "degraded_retry_max_attempts": ("degraded_retry_max_attempts", "DEGRADED_RETRY_MAX_ATTEMPTS"),
    "degraded_retry_count_emphasis": ("degraded_retry_count_emphasis", "DEGRADED_RETRY_COUNT_EMPHASIS"),
    "count_hard_check_enabled": ("count_hard_check_enabled", "COUNT_HARD_CHECK_ENABLED"),
    "post_grade": ("post_grade", "POST_GRADE"),
    "realism_pool": ("realism_pool", "REALISM_POOL"),
}


class SettingsUpdate(BaseModel):
    send_window_start: str | None = None
    send_window_end: str | None = None
    random_interval_min: float | None = None
    random_interval_max: float | None = None
    dispatch_provider: str | None = None
    wechat_opening_text: str | None = None
    wechat_search_bar_x: int | None = None
    wechat_search_bar_y: int | None = None
    wechat_input_x_offset: int | None = None
    wechat_input_y_offset: int | None = None
    consistency_check: bool | None = None
    consistency_check_max_retries: int | None = None
    bailian_thinking_mode: bool | None = None
    degraded_retry_enabled: bool | None = None
    degraded_retry_max_attempts: int | None = None
    degraded_retry_count_emphasis: bool | None = None
    count_hard_check_enabled: bool | None = None
    post_grade: bool | None = None
    realism_pool: bool | None = None


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
        "wechat_opening_text": s.wechat_opening_text,
        "wechat_search_bar_x": s.wechat_search_bar_x,
        "wechat_search_bar_y": s.wechat_search_bar_y,
        "wechat_input_x_offset": s.wechat_input_x_offset,
        "wechat_input_y_offset": s.wechat_input_y_offset,
        "post_grade": s.post_grade,
        "realism_pool": s.realism_pool,
        "consistency_check": s.consistency_check,
        "consistency_check_max_retries": s.consistency_check_max_retries,
        "bailian_thinking_mode": s.bailian_thinking_mode,
        "degraded_retry_enabled": s.degraded_retry_enabled,
        "degraded_retry_max_attempts": s.degraded_retry_max_attempts,
        "degraded_retry_count_emphasis": s.degraded_retry_count_emphasis,
        "count_hard_check_enabled": s.count_hard_check_enabled,
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
        data = body.model_dump()
        for body_field, (settings_field, env_key) in _PERSISTABLE_FIELDS.items():
            val = data.get(body_field)
            if val is None:
                continue
            setattr(s, settings_field, val)
            text = "true" if val is True else "false" if val is False else str(val)
            _persist_env_value(env_key, text)
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
