# 项目长期记忆 — 同物景（weige）

## 项目概述
微信好评自动发送系统。FastAPI 后端 + Alpine.js 前端。核心流程：产品入库 → 生成好评图+文案 → 待发列表调度 → 微信自动化发送（仅 Windows，坐标+剪贴板）。

## 关键架构
- **调度状态机**（`app/services/db.py`）：pending→generating→ready→sending→sent；生成失败→failed；发送不确定→needs_review。
- **发送容错**（`app/services/dispatch_scheduler.py` `_send_ready_task`）：逐组发送，每组 verify→标记 submission_uncertain→send→标记 local_submitted，manifest 持久化。submission_uncertain 的任务禁止自动重试（避免重复发送），需人工 retry-after-review。
- **发送器**（`app/services/wechat/sender.py`）：`send(remark, text, images, settings)`，每次调用都 `_search_and_open` 重新打开好友会话；发送顺序为「先图片后文字」。`test_wechat_sender_override` 可切换真实/failing sender 用于测试。
- **测试约束**：测试 monkeypatch `app.services.wechat.sender.send`，签名不可变；默认 `wechat_opening_text=""` 时单组任务行为须不变。
- **事实卡按需生成**（2026-07-29 改造，`app/services/fact_card.py` `ensure_fact_card`）：上传产品时**不再**调用视觉模型，只存原图+轻量 metadata（`fact_card=null`，产品名用文件名 stem）；事实卡推迟到「待发记录 generating 阶段」首次用到该产品时才生成（vision analyze → 写回 metadata → `upsert_product` 回填 name/dims/room），后续任务复用、不重复花 token。`upload_product` 返回 `fact_card=null`；workbench（index.html）无事实卡时禁用生图/对比/编辑；`_load_fact_card_for_regen` 改为调用 `ensure_fact_card` 兜底。

## 发送内容编排（2026-07-29 新增）
- 待发任务触发发送时：先发开场语（设置项 `wechat_opening_text`，留空不发，每任务仅一次）→ 每组「图片→文字」→ 组间用 `--------------` 分隔。
- 开场语/分隔符作为独立 `send` 调用（纯文字 images=[]），与该组的 submission_uncertain→local_submitted 窗口绑定，前缀失败不影响上一组。

## 业务约束与生成策略
- **种子策略（2026-07-29 用户最终拍板：「锁定最后的种子」）**：系统含多个独立随机源——模型 seed（bailian 内部 `bailian.py:219`，主导**主体**保真）、camera_seed（`dispatch_generation.py:283`，控制角度/构图）、scene/其他物品（i2i 由提示词+模型随机生成，不锁定即每次不同）。**方案**：每个产品 metadata 存 `current_seed`（当前种子）；正常生成用 `current_seed`（首次则为随机生成并写入）；**首次生成成功即锁定该 seed**；用户不满意则手动重生成 → 抽新 seed，满意后把新 seed 存为 `current_seed`（覆盖旧值，即“锁定最后的种子”，无需单独锁定按钮）；**只锁模型 seed（主体），scene/camera_seed/其他物品一律不锁** → 同一产品主体永远正确、可复现，而场景/角度/陪衬物每次各异、群发不穿帮。自动调度重试阶段应**复用** `current_seed`（只变 scene/camera，不换种子）；只有用户主动重生成才换种子；两次都没过（needs_review）不动 `current_seed`。落地需：metadata 加 `current_seed` 字段、bailian.generate 支持外部传入 seed、生成后把用过的 seed 写回 metadata。**待评估风险**：①wan2.7 用同 seed+同参考图+同核心 prompt 是否真能复现主体（可行性前提）；②锁 seed 后牛在不同背景的融合度可能略僵（像P图）。**→ 2026-07-29 经外部 AI 评估+代码核验：该「锁种子保主体」方案不可行，放弃之**。理由：①万相2.7 官方明确 seed 仅“相对稳定”、云端不承诺复现（多机/模型热更新致浮点非确定）；②每次换 scene/shot_type=改 prompt，同 seed 下主体仍被重新演绎，“seed锁主体+prompt变背景”在扩散去噪里无分离通道、架构不成立；③无法锁定步数/采样器/参考强度/模型版本；④锁 seed 反使构图/明暗趋同、抵消 scene/camera 随机、反噬群发不穿帮目标。**治“忽好忽坏”改走**：调高 `consistency_check_max_retries`（1→4 多候选挑优）、重试不换场景（同场景纯重摇，修 `dispatch_scheduler.py:170-172` 重抽 scene 的坑）、生图前恢复人工确认事实卡质量门、prompt 措辞强化保真锁。**附带核验**：`bailian_thinking_mode` 走 i2i（wan2.7-image `supports_reference=True` 恒传参考图）→ 官方称 thinking_mode 仅“无图片输入时生效”，故为 **no-op 死参数**，“关 thinking_mode 降方差”无效；万相2.7 parameters 无 strength/denoise/guidance 类“参考强度”参数，不可加（与早前建议矛盾，已证伪）。`prompt_extend` 代码未设置，报告称其默认改写 prompt 为“第四随机源”但报告自身参数表未列之，存疑待官方文档核实。
- **重生成画幅 bug**：调度自动生成用 `aspect_ratio="3:4"`（竖图），但手动重生成 `dispatch_regen_functions.py` 用 `"1:1"`（方图），导致详情页重出的图和批次里其它 3:4 图幅对不上，待统一为 3:4。
- **排查结论（2026-07-29 任务 7d415fa6，444/555/666/777）**：「上传即视觉分析→待发才分析」的改动**不直接导致**不合格率高。证据：事实卡 per-product 幂等复用且能正确解析（中文 alias + `populate_by_name`，`ensure_fact_card` 复用正常，非 bug）；4 产品 fact_card 质量均正常（666 保真锁明确「马数量=5」，仍被 wan2.7 脑补成 6 匹）。不合格根因 = wan2.7 i2i 对复杂/多主体商品的保真波动 + 一致性校验严格（原图 vs 生成图）+ `consistency_check_max_retries=1` 重试预算小。真实副作用：把关点从「上传时人工核对事实卡」后移到「批量生成时自动用」，失去隐性质量门；视觉模型对多主体/置信度中商品本就不稳（666 置信度=中、含不确定项）。**重试换场景是坑**：`dispatch_scheduler.py:170-172` 重试时重抽 `scene/shot_type`，等于连场景都变、越换越偏，应改为同场景纯重摇 seed。建议：调高 max_retries（如 4）、重试不换场景、生图前恢复事实卡确认环节、试关 `bailian_thinking_mode` 降方差。

## 环境注意
- `.env` 中 `REVIEW_FORBIDDEN_WORDS_HARD` 覆盖了 config.py 默认列表（当前不含「五星」「全五分」等），导致 `test_review*.py` 三个测试失败（`test_hard_filter_blocks_forbidden_phrase`、`test_hard_filter_blocks_new_risk_words`、`test_safe_fallback_randomness_at_least_8_unique_in_20`）——属环境问题，非代码缺陷。
- 运行测试用系统 Python 3.12（`C:\Users\松颠\AppData\Local\Programs\Python\Python312\python.exe`），已装好 fastapi/pydantic_settings/pytest 等依赖。
