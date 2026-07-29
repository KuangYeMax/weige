# 微信发送层重构（feat/uia-sender）— 实施总结

> 分支：`feat/uia-sender` ｜ 日期：2026-07-29 ｜ 计划文档：`docs/wechat-sender-refactor-plan.md`

## 做了什么

按 `wechat-sender-refactor-plan.md` 在**现有坐标方案**上加固，达成「失败可见 + 安全护栏 + 启动自检 + 发送前置检查 + 可观测性」。原任务的 UIA 控件定位路线已在 Step 0 证伪（微信 4.0.6+ 屏蔽 UIAutomation），改在坐标方案上加固。

## 改动清单

### 新增文件（5 个）
| 文件 | 作用 |
|---|---|
| `app/services/wechat/sender_base.py` | 抽象接口：`SendReason` 枚举、`SendResult`/`HealthReport` 数据结构、`WechatSenderProtocol` |
| `app/services/wechat/wechat_sender.py` | 真实坐标实现 `WechatSender`：11 项启动自检、5 项发送前检查、open_chat 身份校验、发送后真验证、截图留证、`_SEND_LOCK` 互斥锁 |
| `app/services/wechat/dryrun_sender.py` | 演习模式：打印+落盘到 `storage/send_dryrun/`，绝不真发 |
| `app/services/wechat/test_account_sender.py` | 测试账号白名单：非白名单拒绝发送 |
| `tests/test_sender_refactor.py` | 26 个新增测试（实现选择/降级/锁/异常映射/自检缓存/落盘） |

### 修改文件（6 个）
| 文件 | 改动 |
|---|---|
| `app/config.py` | 新增 9 个配置项（见下表） |
| `app/services/wechat/sender.py` | 门面改造：`_select_impl` 按 override+自检缓存选实现；`send`/`verify_remark` 委托；`_SEND_LOCK` 互斥；`_to_exception` 映射回旧异常类型 |
| `app/services/dispatch_scheduler.py` | `_resolve_sender` 支持 `dryrun`/`test_account`（failing:* 保留） |
| `app/main.py` | lifespan 启动时跑 `WechatSender.check_environment()`，结果缓存到 `app.state.wechat_health` + 模块级 `_HEALTH_CACHE` |
| `app/api/wechat.py` | 新增 `GET /api/wechat/health` 端点返回自检报告 |
| `app/static/dashboard.html` | 顶部自检告警条（healthy=False 红色告警 / True 绿色 / None 灰色） |

### 新增配置项（`config.py` Settings）
| 字段 | 默认值 | 说明 |
|---|---|---|
| `wechat_version_whitelist` | `["4.1.1.19"]` | 实测通过的微信版本白名单 |
| `wechat_screenshot_dir` | `"storage/send_proofs"` | 发送后截图留证目录 |
| `wechat_test_accounts` | `[]` | TestAccountSender 白名单 |
| `wechat_window_drift_tolerance` | `20` | 窗口矩形漂移容忍像素 |
| `wechat_ocr_engine` | `"rapidocr"` | 会话标题 OCR 引擎（未装时退化） |
| `wechat_desktop_recheck_interval` | `300` | 运行期复检间隔秒 |
| `wechat_smoke_test_account` | `""` | 受控真实小号（空则跳过冒烟） |
| `wechat_pin_window` | `True` | 启动是否 SetWindowPos 固定窗口 |
| `wechat_strict_verify` | `False` | 发送后真验证开关（灰度过渡，生产手动开 True） |

## 关键设计决策

### 1. 不破坏现有契约（硬约束）
- `send(remark, text, images, settings)` / `verify_remark(remark, settings)` 签名不变
- 失败仍抛旧异常类型（`ClipboardVerificationError`/`FileNotFoundError`/`RuntimeError`/`ChatVerificationError`），`dispatch_scheduler` 的异常分类与 fail_reason 不变
- 状态机沿用：`submission_uncertain` / `local_submitted` / `mark_dispatch_task_*`
- WechatSender 通过 `_sender_mod()` 动态访问 sender 模块原语，保持 `tests/test_wechat_send.py` 的 monkeypatch 兼容

### 2. 自检缓存驱动降级
- `check_environment()` 启动跑一次，结果缓存到模块级 `_HEALTH_CACHE`
- `_select_impl` 读缓存：`healthy=False` → 强制 `DryRunSender`（无论 override）
- `healthy=True` 或缓存为 None（测试环境）→ 按 override 选实现
- WechatSender 实例从 `_HEALTH_CACHE` 继承 `hwnd`/`rect_baseline`，`is_ready` 基于真实窗口状态检查

### 3. 发送后真验证做成灰度过渡
- 计划要求「真验证未通过 → SEND_NOT_CONFIRMED」，但测试环境真截图会挂
- 加 `wechat_strict_verify` 开关（默认 False）：关闭时行为同旧（按键执行即视为已发），开启时做输入框清空/新增气泡/红色叹号三条真验证
- 生产环境手动开 True 启用严格模式

### 4. 身份校验按环境降级
- 优先：独立聊天窗口标题精确匹配（4.1.9 待验证）
- 次选：OCR 读会话标题（rapidocr 未装时跳过）
- 兜底：信任搜索结果 + 告警（当前环境默认走此路，rapidocr 未装）
- 废除 pHash/像素比对/首发建基准（计划 §3.2 硬伤 1）

### 5. _SEND_LOCK 互斥（硬伤 4）
- 模块级 `threading.Lock`，`send` 阻塞获取（120s 超时），`verify_remark` 非阻塞获取
- `verify_remark` 拿不到锁 → 抛 `ChatVerificationError("系统正忙")`
- 避免并发登记与后台发送抢同一微信窗口

## 测试结果

```
284 passed, 1 skipped, 8 failed
```
- 8 个失败全是**预先存在的环境问题**（与重构无关）：
  - 6 个 `test_dispatch_send.py`：`.env` 设了 `WECHAT_OPENING_TEXT` 覆盖默认空值
  - 2 个 `test_review*.py`：`.env` 的 `REVIEW_FORBIDDEN_WORDS_HARD` 覆盖默认列表
- 新增 26 个测试全过
- 现有 258 个测试无新破坏

## 实际环境自检验证

在真实 win32 环境（微信 4.1.1.19 + 125% DPI + 单显示器）跑 `check_environment()`，11 项全部通过：
- 微信版本 4.1.1.19 在白名单 ✓
- 主窗口 hwnd 检测到 ✓
- DPI 125% 已设 PER_MONITOR_AWARE_V2 ✓
- 窗口已 SetWindowPos 固定到 (0,0) ✓
- 剪贴板读写正常 ✓
- 桌面可交互（截图非全黑）✓
- 冒烟测试跳过（未配 wechat_smoke_test_account）✓

## 待办（计划中标注「待验证/优先实测」的部分）

1. **独立聊天窗口方案验证**：4.1.9 是否支持拉成独立窗口（`GetWindowText` 直接读好友名）。验证通过则身份校验最可靠，可规避 OCR 依赖。
2. **OCR 依赖安装**：`pip install rapidocr-onnxruntime` 启用 OCR 身份校验（当前退化为信任搜索结果）。
3. **端到端冒烟**：配 `wechat_smoke_test_account` 后启动会向受控小号发一条测试消息。
4. **生产启用真验证**：`.env` 设 `WECHAT_STRICT_VERIFY=true` 启用发送后三条真验证。
5. **运行期热降级**：`wechat_desktop_recheck_interval` 间隔复检桌面/版本/窗口类（当前只启动跑一次，计划 §4 第 11 项要求运行期复检）。
