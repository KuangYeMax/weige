"""WeChat sender 抽象层 — 接口、数据结构、Protocol。

定义所有 sender 实现共用的契约：
- ``SendReason``：失败原因分类枚举（与状态机衔接，供可观测性分类统计）
- ``SendResult``：单次 send_text/send_image 的结构化结果
- ``HealthReport``：启动自检报告（环境快照 + 各检查项通过/失败）
- ``WechatSenderProtocol``：实现方需满足的 Protocol

设计要点：
- 数据结构用 dataclass，轻量、可序列化（落盘 health_report.json / send_log.jsonl）
- SendReason 是 str Enum，便于日志/前端直接展示
- Protocol 仅约束接口形状，不做运行时检查（duck typing）
- 不依赖 win32 / PIL / numpy，本模块在任意平台均可 import
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class SendReason(str, Enum):
    """发送结果原因分类。

    取值与计划 §2.1 对齐。状态机衔接：
    - OK → manifest 该项 local_submitted
    - 任何非 OK → manifest 该项 submission_uncertain（禁止自动重试，转人工）
    """

    OK = "ok"
    # 搜索后校验会话名不符（OCR/窗口标题读到的当前会话名 != 预期备注名）
    FRIEND_NOT_FOUND = "friend_not_found"
    # 备注名重名（产品层保证唯一，此为兜底；搜索结果中出现多条匹配）
    FRIEND_DUPLICATE = "friend_duplicate"
    # 窗口丢失/最小化/被遮挡/矩形漂移超阈值
    WINDOW_ABNORMAL = "window_abnormal"
    NOT_LOGGED_IN = "not_logged_in"
    # 图片不存在/超像素尺寸上限/格式不可读
    IMAGE_INVALID = "image_invalid"
    # 剪贴板写入或回读校验失败（含重试退避后仍失败）
    CLIPBOARD_FAILED = "clipboard_failed"
    # 发送后真验证未通过（输入框未清空 / 无新增气泡 / 出现红色叹号）
    SEND_NOT_CONFIRMED = "send_not_confirmed"
    # 锁竞争：verify_remark 与后台发送抢同一窗口，拿不到锁
    LOCK_BUSY = "lock_busy"
    UNKNOWN = "unknown"


@dataclass
class SendResult:
    """单次 send_text / send_image 的结果。

    ``success=True`` 仅当 §3.1 步骤 6 的三条真验证全部通过。
    ``screenshot_path`` 为发送后截图留证路径（供事后核查，非判据）。
    ``verified`` 标记真验证是否通过（True=输入框清空+新增气泡+无红色叹号）。
    """

    success: bool
    reason: SendReason
    message: str = ""
    raw_exception: str = ""
    screenshot_path: str | None = None
    elapsed_ms: int = 0
    verified: bool = False
    # 多图发送中途失败时，记录已成功条目数（计划 §6 失败进度可观测）
    # 单次 send_image 调用时为 0/1；send() 门面聚合多图时累加
    succeeded_count: int = 0


@dataclass
class HealthReport:
    """启动自检报告。

    ``check_environment()`` 仅在进程启动时执行一次，结果缓存进此结构。
    ``_select_impl`` 只读缓存，绝不每次 send 重跑自检（计划 §4 末注）。
    ``environment`` 含微信版本/DPI/分辨率/显示器数/hwnd/窗口基准矩形快照。
    ``failed_checks`` 为失败项名列表（按检查顺序）。
    """

    healthy: bool
    failed_checks: list[str] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    details: str = ""
    # 自检完成时间戳（ISO 8601），便于前端「上次自检时间」展示
    checked_at: str = ""


class WechatSenderProtocol(Protocol):
    """所有 sender 实现需满足的接口。

    实现方：
    - ``WechatSender``：真实坐标发送（Windows 专用）
    - ``DryRunSender``：演习模式，绝不真发
    - ``TestAccountSender``：白名单测试账号发送
    """

    def check_environment(self) -> HealthReport: ...
    def is_ready(self) -> bool: ...
    def send_text(self, friend_remark: str, text: str) -> SendResult: ...
    def send_image(self, friend_remark: str, image_path: str) -> SendResult: ...
