"""DryRunSender — 演习模式，绝不真发。

把要发的内容（好友/文本/图片路径）打印 + 落盘到 ``storage/send_dryrun/``，
供联调验证发送编排链路（开场语/图/文案/分隔符顺序）而不真发到客户。

``check_environment`` / ``is_ready`` 恒返回通过（演习模式不依赖真实微信环境）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.services.wechat.sender_base import (
    HealthReport,
    SendReason,
    SendResult,
)

logger = logging.getLogger(__name__)


class DryRunSender:
    """演习实现：打印 + 落盘，绝不真发。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._dryrun_dir = Path(settings.storage_root) / "send_dryrun"

    def check_environment(self) -> HealthReport:
        # 演习模式不依赖真实环境，恒通过
        return HealthReport(
            healthy=True,
            failed_checks=[],
            environment={"mode": "dryrun"},
            details="演习模式，环境检查跳过",
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

    def is_ready(self) -> bool:
        return True

    def send_text(self, friend_remark: str, text: str) -> SendResult:
        return self._record(friend_remark, text=text, image_path=None)

    def send_image(self, friend_remark: str, image_path: str) -> SendResult:
        return self._record(friend_remark, text=None, image_path=image_path)

    def _record(self, friend_remark: str, *, text: str | None, image_path: str | None) -> SendResult:
        try:
            self._dryrun_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "remark": friend_remark,
                "text": text,
                "image_path": image_path,
            }
            # 落盘到 jsonl
            log_path = self._dryrun_dir / "dryrun_log.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            kind = "text" if image_path is None else "image"
            logger.info("[DRYRUN] %s → %s: %s", kind, friend_remark, text or image_path)
            return SendResult(
                success=True,
                reason=SendReason.OK,
                message=f"dryrun recorded to {log_path}",
                succeeded_count=1,
            )
        except Exception as e:
            return SendResult(
                success=False,
                reason=SendReason.UNKNOWN,
                message=f"dryrun 落盘失败: {e}",
                raw_exception=type(e).__name__,
            )
