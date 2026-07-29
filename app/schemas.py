from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FactBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class KeyStructure(FactBaseModel):
    name: str = Field(default="", alias="名称")
    count: str = Field(default="", alias="数量")
    position_relation: str = Field(default="", alias="位置与关系")
    appearance: str = Field(default="", alias="外观特征")
    importance: str = Field(default="中", alias="重要性")


class TextSpec(FactBaseModel):
    content: str = Field(default="", alias="内容")
    position: str = Field(default="", alias="位置")
    confidence: str = Field(default="低", alias="置信度")


class SubjectBbox(FactBaseModel):
    x: float = Field(default=0.0, ge=0, le=1)
    y: float = Field(default=0.0, ge=0, le=1)
    w: float = Field(default=1.0, ge=0, le=1)
    h: float = Field(default=1.0, ge=0, le=1)


class Dimensions(FactBaseModel):
    length_cm: float | None = Field(default=None, alias="长_cm")
    width_cm: float | None = Field(default=None, alias="宽_cm")
    height_cm: float | None = Field(default=None, alias="高_cm")
    weight_kg: float | None = Field(default=None, alias="重量_kg")
    other_specs: list[str] = Field(default_factory=list, alias="其他规格")
    evidence: str = Field(default="未提供", alias="证据")
    volume_tier: str = Field(default="", alias="体量等级")
    composition_strategy: str = Field(default="", alias="构图策略")
    size_source: str | None = Field(default=None, alias="尺寸来源")


class Scene(FactBaseModel):
    scene: str = Field(default="", alias="场景")
    placement: str = Field(default="", alias="具体位置")


class ShootingAdvice(FactBaseModel):
    full_shot: str = Field(default="", alias="完整照")
    medium_shot: str = Field(default="", alias="中近景")
    detail_shots: list[str] = Field(default_factory=list, alias="细节照")


class ViewAngleTolerance(FactBaseModel):
    level: str = Field(default="低", alias="等级")
    reasoning: str = Field(default="", alias="判断依据")
    allowed_offset: str = Field(default="", alias="允许角度偏移")


class Accessory(FactBaseModel):
    name: str = Field(default="", alias="名称")
    category: str = Field(default="", alias="类别")
    description: str = Field(default="", alias="外观描述")


class FactCard(FactBaseModel):
    product_name: str = Field(default="", alias="商品名称")
    confidence: str = Field(default="低", alias="识别置信度")
    category: str = Field(default="", alias="商品品类")
    product_form: str = Field(default="", alias="商品形态")
    subject_definition: str = Field(default="", alias="主体定义")
    # ── 结构化主体信息（供阶梯降级重试的数量强调与数量硬校验使用） ──
    # 主体名称：商品主体的统称，如「马」「牛」「花瓶」。
    # 主体数量：主体个体的整数数量；数不清或不适用（成套餐具、一串散珠、液体粉末等）必须为 None。
    # 主体数量待确认：仅由存量回填脚本置 True，表示该数量系从旧文本解析、尚未人工核对，
    #   降级逻辑与硬校验一律视为「无数量」跳过，直到用户在详情页确认后清除该标志。
    subject_name: str | None = Field(default=None, alias="主体名称")
    subject_count: int | None = Field(default=None, alias="主体数量")
    subject_count_unconfirmed: bool | None = Field(default=None, alias="主体数量待确认")
    subject_bbox: SubjectBbox = Field(default_factory=SubjectBbox, alias="主体包围框")
    ignored_elements: list[str] = Field(default_factory=list, alias="画面中需忽略")
    overall_features: str = Field(default="", alias="整体特征")
    key_structures: list[KeyStructure] = Field(default_factory=list, alias="关键结构")
    colors_materials: list[str] = Field(default_factory=list, alias="颜色与材质观感")
    text_specs: list[TextSpec] = Field(default_factory=list, alias="文字与规格")
    dimensions: Dimensions = Field(default_factory=Dimensions, alias="尺寸")
    scenes: list[Scene] = Field(default_factory=list, alias="自然场景")
    shooting_advice: ShootingAdvice = Field(default_factory=ShootingAdvice, alias="建议拍法")
    fidelity_locks: list[str] = Field(default_factory=list, alias="保真锁")
    view_angle_tolerance: ViewAngleTolerance | None = Field(
        default=None, alias="视角容差"
    )
    accessories: list[Accessory] = Field(default_factory=list, alias="配件与包装")
    uncertainties: list[str] = Field(default_factory=list, alias="不确定项")
    room: str | None = Field(default=None, alias="建议房间")


class GenerateRequest(FactBaseModel):
    fact_card: FactCard
    shot_type: Literal["中近景", "细节照"]
    scene_index: int = Field(ge=0)
    aspect_ratio: Literal["3:4", "1:1"]
    image_provider: str | None = None
    image_model: str | None = None


class CompareRequest(FactBaseModel):
    shot_type: Literal["中近景", "细节照"]
    aspect_ratio: Literal["3:4", "1:1"]
    models: list[str] | None = None


# --------------- Dispatch Task Schemas ---------------

class DispatchTaskCreate(BaseModel):
    wx_remark: str = Field(..., min_length=1)
    send_codes: list[str] = Field(..., min_length=1, max_length=4)
    countdown_days: int = Field(default=3, ge=1)
    trigger_at: str | None = None


class DispatchRemarkVerificationRequest(BaseModel):
    wx_remark: str


class DispatchTaskReschedule(BaseModel):
    trigger_at: str


class DispatchTaskOut(BaseModel):
    task_id: str
    wx_remark: str
    send_codes: list[str]
    countdown_days: int
    created_at: str
    trigger_at: str
    status: str
    fail_reason: str | None = None
