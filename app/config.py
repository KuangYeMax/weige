from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    vision_provider: str = "mock"
    image_provider: str = "mock"
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_vision_base_url: str = ""
    ark_image_base_url: str = ""
    ark_vision_model: str = ""
    ark_image_model: str = ""
    volc_image_model: str = ""
    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
    dashscope_base_url: str = ""
    dashscope_compat_base_url: str = ""
    dashscope_region: str = "cn-beijing"
    bailian_image_model: str = "wan2.7-image"
    storage_root: Path = Field(default=PROJECT_ROOT / "storage")
    max_upload_bytes: int = 10 * 1024 * 1024
    min_image_dimension: int = 512
    max_image_pixels: int = 40_000_000
    external_timeout_seconds: float = 120.0
    max_download_bytes: int = 30 * 1024 * 1024
    shrink_reference: bool = True
    detail_crop_ratio: float = 0.4
    detail_crop_center_y_bias: float = 0.35
    post_grade: bool = True
    grade_saturation: float = 0.75
    grade_highlight: float = 0.88
    grade_contrast: float = 0.93
    grade_grain: float = 0.015
    grade_vignette: float = 0.25
    grade_micro_rotation: float = 1.5
    realism_pool: bool = True
    realism_seed: int | None = None

    # 2026-07-28 消融实验证实：将事实卡外观描述（颜色材质/形态/位置关系）注入生图 prompt
    # 会导致图生图模型重绘主体而非保持参考图保真。禁止开启。
    # 事实卡字段解析保留完好，供好评文案等非生图场景使用。
    inject_appearance_into_image_prompt: bool = False

    # 2026-07-28 消融证实：thinking_mode 在无注入时不影响保真度，
    # 但在有注入时会放大失真。保持默认 True（无注入时安全）。
    bailian_thinking_mode: bool = True
    dispatch_poll_seconds: float = 30.0
    dispatch_image_provider: str = "bailian"
    dispatch_image_model: str | None = None

    # ── 生图策略 ──
    image_prompt_strategy: str = "legacy"
    image_realism_boost: str = "off"

    # ── 一致性校验 ──
    consistency_check: bool = True
    consistency_check_model: str = ""
    consistency_check_max_retries: int = 1

    # ── 阶梯式降级重试 ──
    # 总开关关闭时退回原行为（max_attempts = 1 + consistency_check_max_retries，
    # 重试时重抽 scene/shot_type），便于新旧策略 A/B 对比。
    degraded_retry_enabled: bool = True
    # 降级重试最大尝试档数（1=仅正常随机，2=加换种子，3=加数量强调，4=加极简保底）。
    degraded_retry_max_attempts: int = 4
    # 数量强调句独立开关：第三、四档是否向 prompt 注入「主体数量必须为 N」。
    degraded_retry_count_emphasis: bool = True
    # 数量硬校验独立开关：校验通过后额外问视觉模型「图里有几个主体」，与事实卡数量做相等判定。
    # 默认关闭，需先积累结构化数量字段后再开。
    count_hard_check_enabled: bool = False

    # ── 好评文案 ──
    review_provider: str = "ark"
    ark_review_base_url: str = ""
    ark_review_model: str = "doubao-seed-1-6-flash-250828"
    review_temperature: float = 1.0
    review_max_retries_on_forbidden: int = 2
    review_fallback_text: str = "收到商品了，质量不错，和描述一致，满意。"

    review_forbidden_words_hard: list[str] = Field(
        default_factory=lambda: [
            # 风控词
            "好评返现", "返现", "返图", "红包", "返款", "补偿", "五星", "全五分",
            "加微信", "联系客服返", "刷单", "好评有礼", "晒单奖励",
            # 极限词组（整词匹配）
            "最好的", "最棒的", "最便宜", "全网最低", "第一名", "行业第一",
            "唯一选择", "独一无二", "国家级", "世界级", "顶级", "绝对",
            "百分百", "完美无缺",
        ],
        validation_alias=AliasChoices(
            "REVIEW_FORBIDDEN_WORDS_HARD",
            "REVIEW_FORBIDDEN_WORDS",
        ),
    )
    review_forbidden_words_soft: list[str] = Field(default_factory=lambda: [
        "作为一名", "总的来说", "综上", "性价比之王",
    ])
    review_colloquial_pool: list[str] = Field(default_factory=lambda: [
        "好看", "漂亮", "精致", "有质感", "上档次", "颜值高",
        "质量好", "做工好", "手感好", "扎实",
        "喜欢", "满意", "推荐", "回购", "没失望",
        "实用", "方便", "真心不错", "对得起价格",
        "划算", "值",
    ])
    review_minor_flaw_defaults: list[str] = Field(default_factory=lambda: [
        "包装可以再好一点",
        "物流速度一般",
        "客服回复可以再及时一点",
    ])
    review_similarity_threshold: float = 0.6
    review_cheap_model: str = ""

    # ── 微信发送（仅 Windows） ──
    wechat_search_bar_x: int = 170
    wechat_search_bar_y: int = 48
    wechat_input_x_offset: int = 750
    wechat_input_y_offset: int = 40
    wechat_send_interval_min: float = 1.0
    wechat_send_interval_max: float = 3.0

    # ── 弹窗阻塞处理：发送过程中若微信被弹窗遮挡，等待其消失；超时则中止 ──
    wechat_popup_wait_timeout: float = 30.0
    wechat_popup_interval: float = 2.0

    # ── 开场语：待发任务触发发送时，最先发给客户的一段话；留空则不发 ──
    wechat_opening_text: str = ""

    # ── 微信发送层重构（feat/uia-sender）新增配置 ──

    # 实测通过的微信版本白名单。当前锁定 4.1.1.19：
    # 4.1.9.57 屏蔽 UIAutomation 且坐标方案未在该版本验证，回退并锁定 4.1.1.19。
    wechat_version_whitelist: list[str] = Field(
        default_factory=lambda: ["4.1.1.19"],
        validation_alias=AliasChoices("WECHAT_VERSION_WHITELIST"),
    )
    # 发送后截图留证目录（相对 storage_root）
    wechat_screenshot_dir: str = "storage/send_proofs"
    # TestAccountSender 白名单（备注名列表）
    wechat_test_accounts: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("WECHAT_TEST_ACCOUNTS"),
    )
    # 窗口矩形漂移容忍像素（被动兜底，主约束靠启动 SetWindowPos 固定）
    wechat_window_drift_tolerance: int = 20
    # 会话标题 OCR 引擎（rapidocr/paddleocr）；依赖未装时退化为窗口标题精确匹配
    wechat_ocr_engine: str = "rapidocr"
    # 运行期复检桌面可交互/微信版本/窗口类的间隔秒（热降级用）
    wechat_desktop_recheck_interval: int = 300
    # 受控真实小号，端到端冒烟第二条目标（空则跳过真实账号冒烟，仅冒烟文件传输助手）
    wechat_smoke_test_account: str = ""
    # 启动是否 SetWindowPos 固定微信窗口尺寸位置（幂等）
    wechat_pin_window: bool = True

    # 发送后真验证开关（灰度过渡）：默认 False，行为同旧（按键执行即视为已发）；
    # 生产环境手动开 True 后，send_text/send_image 会做输入框清空/新增气泡/红色叹号
    # 三条真验证，未通过则返回 SEND_NOT_CONFIRMED（计划 §3.1 步骤 6 / §6）。
    wechat_strict_verify: bool = False

    # ── 测试专用：sender 覆盖，PrivateAttr 不参与 env 解析 ──
    _test_wechat_sender_override: str = PrivateAttr(default="real")

    @property
    def test_wechat_sender_override(self) -> str:
        return self._test_wechat_sender_override

    @test_wechat_sender_override.setter
    def test_wechat_sender_override(self, value: str) -> None:
        self._test_wechat_sender_override = value

    @property
    def db_path(self) -> Path:
        return self.storage_root / "app.db"

    @property
    def vision_base_url(self) -> str:
        return (self.ark_vision_base_url or self.ark_base_url).rstrip("/")

    @property
    def image_base_url(self) -> str:
        return (self.ark_image_base_url or self.ark_base_url).rstrip("/")

    @property
    def volcengine_configured(self) -> bool:
        if not self.ark_api_key:
            return False
        checks = []
        if self.vision_provider == "volcengine":
            checks.append(bool(self.ark_vision_model))
        if self.image_provider == "volcengine":
            checks.append(bool(self.ark_image_model or self.volc_image_model))
        return bool(checks and all(checks))

    @property
    def bailian_configured(self) -> bool:
        return bool(self.dashscope_api_key and self.dashscope_base_url)
