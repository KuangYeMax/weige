"""TestAccountSender — 白名单测试账号发送。

包一层 ``WechatSender``，``send_text`` / ``send_image`` 前校验
``friend_remark in settings.wechat_test_accounts``，不在白名单则返回
``FRIEND_NOT_FOUND``（拒绝发送），防止演习时误发真实客户。

适用场景：联调时用受控真实小号验证发送链路，但不允许发给真实客户备注名。
``check_environment`` / ``is_ready`` 委托给内部 ``WechatSender``。
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.services.wechat.sender_base import (
    HealthReport,
    SendReason,
    SendResult,
)
from app.services.wechat.wechat_sender import WechatSender

logger = logging.getLogger(__name__)


class TestAccountSender:
    """白名单测试账号发送实现。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._inner = WechatSender(settings)

    def check_environment(self) -> HealthReport:
        return self._inner.check_environment()

    def is_ready(self) -> bool:
        return self._inner.is_ready()

    def _check_whitelist(self, friend_remark: str) -> SendResult | None:
        """非白名单返回失败 SendResult，白名单返回 None（继续发送）。"""
        whitelist = self.settings.wechat_test_accounts
        if friend_remark not in whitelist:
            logger.warning(
                "[TEST_ACCOUNT] 拒绝发送给非白名单账号: %s（白名单=%s）",
                friend_remark, whitelist,
            )
            return SendResult(
                success=False,
                reason=SendReason.FRIEND_NOT_FOUND,
                message=f"非测试账号白名单: {friend_remark}",
            )
        return None

    def send_text(self, friend_remark: str, text: str) -> SendResult:
        blocked = self._check_whitelist(friend_remark)
        if blocked is not None:
            return blocked
        return self._inner.send_text(friend_remark, text)

    def send_image(self, friend_remark: str, image_path: str) -> SendResult:
        blocked = self._check_whitelist(friend_remark)
        if blocked is not None:
            return blocked
        return self._inner.send_image(friend_remark, image_path)
