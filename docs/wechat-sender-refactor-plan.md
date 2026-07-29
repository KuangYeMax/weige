# 微信发送层重构方案（坐标方案加固版）

> 分支：`feat/uia-sender` ｜ 状态：方案待确认 ｜ 日期：2026-07-29

## 0. 背景与方案变更说明

### 原任务前提
原任务要求把发送层从「坐标点击」重构为「基于 Windows UIAutomation 的控件定位」。Step 0 探针在当前环境（Win + 微信 4.1.9.57 + 125% DPI）实测后确认：**UIA 路线在当前环境彻底走不通**。

### Step 0 实测结论（三变量全部证伪）
| 变量 | 试过的值 | 结果 |
|---|---|---|
| 微信版本 | 4.1.1.19 → 4.1.9.57 → 回退 4.1.1.19（最终锁定） | UIA 控件树均不暴露（`children=0~2`、`descendants=0~1`，`class_name` 始终是 `Qt51514QWindowIcon` 而非库期望的 `mmui::MainWindow`）；坐标方案在 4.1.1.19 已验证可用，故回退并锁定该版本 |
| 账号 | 账号 A → 账号 B | 同上 |
| 讲述人 | 不开 → 开（pycaw 静音） | 同上，讲述人激活方式已失效 |

根因（pyweixin 官方 `Weixin4.0.md` + issue#84 确认）：微信 4.0.6+ 主动屏蔽 UIAutomation（Qt 属性 `WA_MSAADisabled` / 重写控件），inspect.exe/spy++ 也看不到 UI 结构，讲述人方式已失效，且是账号/设备级限制。**这不是调参能解决的，是微信在底层屏蔽。**

### 方案转向
放弃 UIA 控件定位，在**现有坐标点击方案**（`app/services/wechat/sender.py` + `win32.py`，已知能工作）上加固，达成任务的核心价值：**失败可见 + 安全护栏 + 启动自检 + 发送前置检查 + 可观测性**。原任务的「真正回读验证」因 UIA 不可用，降级为「多重前置确认 + 截图留证」。

### 与原任务约束的差异
| 原约束 | 调整后 |
|---|---|
| 基于 UIAutomation 控件定位 | 坐标点击定位（UIA 不可用） |
| 好友精确匹配（控件 window_text） | 搜索框定位 + **搜索后校验当前会话名==预期**（不一致即失败）+ 备注名唯一性在产品配置层保证 |
| 删除旧坐标代码路径 | 取消——坐标是主路径，但重构成走新抽象接口 |
| 发送后回读消息比对 | 降级为：多重前置确认（前台+无弹窗+剪贴板写入+发送键执行）+ 截图留证落盘 + 状态机「宁可漏发不重发」沿用 |

---

## 1. 总体架构

### 1.1 模块布局
```
app/services/wechat/
├── sender_base.py     # 新增：抽象接口 + SendResult/HealthReport 数据结构 + Protocol
├── wechat_sender.py   # 新增：WechatSender（真实坐标实现，复用 win32.py）
├── dryrun_sender.py   # 新增：DryRunSender（演习，只打印+落盘，绝不真发）
├── test_account_sender.py  # 新增：TestAccountSender（只发给白名单测试账号）
├── sender.py          # 改造：保留 send()/verify_remark() 门面，内部委托给选定的实现
├── win32.py           # 保留：底层 win32 原语（找窗口/前台/剪贴板/弹窗检测），clip_set_image 复用
├── uia.py             # 保留：ChatVerificationResult 等数据类（dispatch_scheduler 仍 import，不改其契约）
└── failing_sender.py  # 保留：测试用 failing sender
```

### 1.2 上层衔接（不破坏现有契约）
- 调度器入口不变：`from app.services.wechat.sender import send, verify_remark`（`dispatch_scheduler.py:507`）
- `send(remark, text, images, settings)` 签名不变（测试 monkeypatch 此函数，签名不可变）
- 状态机沿用：`submission_uncertain` / `local_submitted` / `mark_dispatch_task_*`（`dispatch_scheduler.py`）
- `settings.test_wechat_sender_override` 扩展取值：`"real"`(默认) / `"failing"` / `"dryrun"` / `"test_account"`

### 1.3 实现选择
启动时根据 `settings.test_wechat_sender_override` + `check_environment()` 结果选定实现：
- 自检不通过 → 强制 `DryRunSender`（无论 override 是什么）
- 自检通过 + override=`"real"` → `WechatSender`
- override=`"dryrun"` → `DryRunSender`
- override=`"test_account"` → `TestAccountSender`
- override=`"failing"` → `failing_sender`（测试用，保留）

---

## 2. Step 1 — 抽象接口定义

### 2.1 数据结构（`sender_base.py`）
```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Protocol

class SendReason(str, Enum):
    OK = "ok"
    FRIEND_NOT_FOUND = "friend_not_found"        # 搜索后校验会话名不符
    FRIEND_DUPLICATE = "friend_duplicate"          # 备注名重名（产品层保证唯一，此为兜底）
    WINDOW_ABNORMAL = "window_abnormal"           # 窗口丢失/最小化/被遮挡/矩形漂移
    NOT_LOGGED_IN = "not_logged_in"
    IMAGE_INVALID = "image_invalid"               # 图片不存在/超10MB/格式不符
    CLIPBOARD_FAILED = "clipboard_failed"          # 剪贴板写入失败
    SEND_NOT_CONFIRMED = "send_not_confirmed"      # 发送后真验证未通过（输入框未清空/无新增气泡/红色叹号）
    UNKNOWN = "unknown"

@dataclass
class SendResult:
    success: bool
    reason: SendReason
    message: str = ""
    raw_exception: str = ""
    screenshot_path: str | None = None   # 发送后截图留证路径
    elapsed_ms: int = 0

@dataclass
class HealthReport:
    healthy: bool
    failed_checks: list[str] = field(default_factory=list)   # 失败项名
    environment: dict = field(default_factory=dict)          # 版本/DPI/分辨率/显示器/hwnd 快照
    details: str = ""

class WechatSenderProtocol(Protocol):
    def check_environment(self) -> HealthReport: ...
    def is_ready(self) -> bool: ...
    def send_text(self, friend_remark: str, text: str) -> SendResult: ...
    def send_image(self, friend_remark: str, image_path: str) -> SendResult: ...
```

### 2.2 三个实现
- **`WechatSender`**：真实坐标发送，复用 `win32.py` 原语 + 现有 `sender.py` 逻辑重构
- **`DryRunSender`**：演习模式，把要发的内容（好友/文本/图片路径）打印 + 落盘到 `storage/send_dryrun/`，绝不真发；`check_environment`/`is_ready` 恒返回通过
- **`TestAccountSender`**：包一层 `WechatSender`，`send_text`/`send_image` 前校验 `friend_remark in settings.wechat_test_accounts`，不在白名单则返回 `FRIEND_NOT_FOUND`（拒绝发送）

---

## 3. Step 2 — WechatSender 坐标实现

### 3.1 发送流程（send_text / send_image 共用）
1. `is_ready()` 不通过 → 返回 `SendResult(success=False, reason=WINDOW_ABNORMAL)`
2. **打开会话（一次定位，锁内连发）**：新增 `open_chat(remark)` 原语（见 §3.3），复用 `_search_and_open` 的「搜索→从结果中精确选中等候项→打开」，并**用 OCR 读取会话标题文字，与备注名做精确字符串比对**（见 §3.2 身份校验）。定位成功后锁定该会话，后续多条消息（开场语/图/文案）不再重复搜索。
3. **会话身份校验（见 §3.2）**：`open_chat` 打开后用 OCR 读标题文字与备注名精确比对；**不再用截图 pHash/像素比对，不再建首发基准图**（评审 硬伤 1）。不符 → `FRIEND_NOT_FOUND`，绝不发送。
4. **发送前弹窗守卫**：`ensure_wechat_clear(hwnd, ...)`（现有 `win32.py` 已实现，前台+无模态弹窗+点击位置无视觉遮挡）
5. **发送**：
   - 图片：`clip_set_image(path)`（`Clipboard.SetImage`，Image 对象→图片消息，非文件附件；**禁止透明通道**，PNG 透明背景会经 DIB 变黑，生成图统一转不透明 JPEG）+ `ctrl,v` + **等待粘贴渲染完成**（图片需 1.5~2s，文本 0.3s；等待用「输入框出现缩略图/文本」显式确认，不靠固定 sleep 赌时序）+ `alt,s`
   - 文本：`clip_set_text(text)` + `ctrl,v` + `alt,s`
6. **发送后真验证（取代「按键执行=已发」的错觉，见评审 硬伤 2）**——三重本地确认之外，补三条能真正证明消息离手的检测：
   - (a) **输入框已清空**：`alt+s` 后等待并截图输入区，输入框无文字/无图片缩略图 = 明确已发；仍有内容 = 明确未发（最便宜、最可靠，必须做）
   - (b) **会话区底部新增气泡**：发送前后各截一次会话底部，做像素差异；无新增气泡 = 高概率未发（这里用图像比对才用对地方：判「有无新增内容」本就是图像级问题）
   - (c) **红色叹号模板匹配**：模板匹配发送失败叹号，抓掉线/被拦导致的发送失败（成本极低）
   - 任一未通过 → 返回 `SendResult(success=False, reason=SEND_NOT_CONFIRMED)`，绝不写 `local_submitted`
7. **截图留证**：发送后截会话窗口，存 `storage/send_proofs/<task_id>_<remark>_<timestamp>.png`（供事后核查，不再当判据）
8. 返回 `SendResult(success=True, reason=OK, screenshot_path=..., verified=True)`

### 3.2 硬性要求

#### 会话身份校验（OCR 精确判等，取代「截图 pHash + 首发建基准」）
- **废除像素/感知哈希比对与会话头基准图**：pHash 判「整体相似」而非「文字相等」，张三/张三丰、李伟/李玮 在同一头部区域哈希距离近 0 → 该拦的拦不住；像素级严格比对又过敏感（未读红点、正在输入、置顶/免打扰图标、深浅色主题、字号、DPI 全让它不等）→ 天天误报。更致命的是「基准图由首次发送建立」会把首发错误固化成「正确基准」，安全属性从 fail-safe 反转为 fail-confident（评审 硬伤 1）。
- **改用 OCR 读标题文字 + 精确字符串比对**：离线 RapidOCR / PaddleOCR 识别一小块标题区域，几十~几百毫秒，无需基准图、无需「首发赌一次」、跨主题跨 DPI 稳健，且是真的在判等。截图继续留证，但不再当判据。
- **（待验证、优先实测）独立聊天窗口方案**：若 4.1.9 仍支持把单聊拉成独立窗口（`win32gui.GetWindowText` 直接读到好友名，UIA/OCR 全免），则同时拿到三样：可靠身份校验、每人独立窗口（坐标基于该窗口客户区相对偏移、不受主窗口列表滚动影响）、天然会话隔离。验证成本极低、收益极大，**排在所有编码之前**先测；验证通过则以窗口标题方案为主、OCR 为兜底。
- 校验失败 → 返回 `FRIEND_NOT_FOUND`，**绝不发送**。

#### 其它硬性要求
- **图片必须以图片消息形式发出**：用 `Clipboard.SetImage`（Image 对象），不用 `copy_files_to_clipboard`（那会发成文件附件）。复用 `win32.py:clip_set_image`。**注意 `Clipboard.SetImage` 经 DIB 丢失透明通道**（PNG 透明背景会变黑并重新编码），生成图统一转不透明 JPEG 再发；「JPEG/PNG、≤10MB」预检与剪贴板路径无关（剪贴板不携带文件格式），**真实约束是像素尺寸**，预检聚焦尺寸上限。
- **发送前粘贴渲染显式等待**：图片粘贴后等待「输入框出现缩略图」再 `alt,s`（剪贴板发图最经典的坑是 `alt+s` 打在渲染完成前），不靠固定 sleep 赌时序。
- **每发送一项后强制输入框清空校验**（见 §3.1 步骤 5a），未清空即判未发。
- **随机间隔**：每条消息间 `random.uniform(wechat_send_interval_min, max)`（1~3 秒，沿用）。
- **全局互斥锁**：`threading.Lock`（模块级 `_SEND_LOCK`），**发送路径与 `verify_remark` 共用同一把锁**——`verify_remark` 拿不到锁即返回「系统正忙，请稍后」，不与后台发送抢同一微信窗口（评审 硬伤 4）。
- **单会话内多次发送**：`open_chat` 一次定位后，开场语/图/文案在锁内连发，每条发后做 §3.1 步骤 5 的真验证（评审 中等问题：缺 open_chat）。
- **旧坐标代码不删除**：坐标是主路径，但重构进 `WechatSender` 类，走新接口。

### 3.3 `open_chat(remark)` 原语（一次定位 + 精确选中等候项）
- 点搜索栏 → `ctrl+a` → 粘贴备注名 → **等待搜索结果异步加载完成**（轮询直到结果列表稳定，不靠固定 sleep）→ **从结果中精确匹配标题 == 备注名 且类型为「联系人」的条目**（排除群聊/公众号/聊天记录/网络结果，解决 `FRIEND_DUPLICATE` 常态问题，评审 中等问题）→ 打开该会话
- 打开后用 OCR 读标题文字与备注名精确比对（§3.2），不符返回 `FRIEND_NOT_FOUND`
- 会话保持打开，后续 `send_text`/`send_image` 不再重复搜索

---

## 4. Step 3 — 启动自检 check_environment()

程序启动时执行，**越早失败越好**排序：

| 序 | 检查项 | 失败动作 |
|---|---|---|
| 1 | win32 平台 + 依赖加载（win32gui/pyautogui/win32com） | 失败→降级演习 |
| 2 | 微信进程存在（`Weixin.exe`） | 失败→降级演习 |
| 3 | **微信版本号在白名单**（`settings.wechat_version_whitelist`，当前 `["4.1.1.19"]`） | 失败→降级演习，告警写实际版本号 |
| 4 | 主窗口 hwnd != 0（`FindWindow('Qt51514QWindowIcon','微信')`） | 失败→降级演习 |
| 5 | 已登录（无登录二维码窗口 + 主窗口可见） | 失败→降级演习 |
| 6 | **DPI 感知 + 分辨率 + 显示器数拦截（不再仅记录）**：启动即 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`；DPI/分辨率/显示器数/主窗口尺寸与约定不符 → **拦截降级**（仅记录会让非 DPI-aware 进程被 Windows 缩放坐标而全盘错位，自检却「通过」） | 不符→降级演习，告警写实际值 |
| 7 | **锁定窗口基准矩形 + 启动时 `SetWindowPos` 固定尺寸位置**（幂等） | 用窗口客户区相对偏移；主动约束窗口，被动「漂移降级」仅作兜底 |
| 8 | 剪贴板读写正常（写文本→读回比对；写图→`ContainsImage` 校验；含 **重试+退避**，应对输入法/云剪贴板/剪贴板管理器抢锁 `OpenClipboard` 失败） | 失败→降级演习 |
| 9 | **桌面可交互检测**（无人值守杀手）：`OpenInputDesktop` 成功 + 截图非全黑（防锁屏/休眠/屏保/远程桌面断开后无交互桌面、截图全黑、点空气） | 失败→降级演习 |
| 10 | **端到端冒烟测试（两类目标）**：①「文件传输助手」发 1 文本 + 1 小图（验剪贴板/发送键/留证）；②**向受控真实小号发 1 条文本**（验搜索歧义/身份校验/回复干扰——文件传输助手无这些风险，单独用它验不到真问题） | 失败→降级演习 |
| 11 | **微信自动更新已关闭**（注册表/策略）；运行期周期性复检版本号与窗口类，不符即热降级（版本检查不能只在启动跑一次，静默更新重启后坐标全废仍 real 模式发送=灾难） | 不符→热降级演习 |

> **自检只跑一次并缓存**：`check_environment()` 仅在进程启动时执行一次（含第 10 项冒烟测试），结果缓存进 `HealthReport`；`_select_impl` 只读缓存，**绝不每次 `send` 重跑自检、绝不重复向文件传输助手/小号发测试消息**（评审 中等问题：`_select_impl` 每次 send 调用）。

### 拦截规则（严格）
- **任一项不通过 → 程序不得进入真实发送模式**，自动降级 `DryRunSender`
- 前端界面最显眼位置红色告警：「自检未通过：<具体项> <原因>，已切换演习模式」
- 版本不在白名单时提示要写清实际版本号
- HealthReport 完整落盘（`storage/health_report.json`），含环境快照

---

## 5. Step 4 — 发送前置检查 is_ready()

**每条消息发送前**都跑一次，轻量快速：

1. 微信窗口仍存在（hwnd 有效）
2. 微信在前台可见、非最小化
3. 无陌生模态弹窗遮挡（`_has_wechat_popup` 现有逻辑 + `WindowFromPoint` 视觉遮挡检测）
4. **窗口矩形 == 启动基准**（防窗口被移动/缩放致坐标错位；漂移超过阈值→不通过）
5. 登录态（窗口存在性 + 无登录二维码窗口兜底）

**分级处理策略（统一 skip / degrade 语义，消除原文档自相矛盾）**：
- **瞬时/可恢复**（最小化、模态弹窗、RDP 短暂断开后已重连、桌面短暂不可交互）：→ **跳过本轮**、记录告警、等待下一轮重试，**绝不硬发**（这些状态可能在下个轮询恢复）。
- **持久/硬失效**（版本号不符、DPI/分辨率/显示器数不符、桌面持续不可交互、微信未运行）：→ **热降级 DryRunSender** 并在界面红色告警，直到人工修复并重启；不靠「跳过」蒙混，因为下一轮仍会失败且可能已坐标错位。
> 原 §5「跳过本轮」与验收标准「降级演习」表述冲突，此处统一：能否在下轮自愈决定 skip 还是 degrade。

---

## 6. Step 5 — 发送后验证（降级版：多重确认 + 截图留证）

### 原则
UIA 不可用 → 无法可靠回读消息内容。**宁可漏发，绝不重发。** 状态只能单向前进。

### 发送后真验证（取代「按键执行即已发」的错觉）
`alt,s` 无异常只代表按键投递给了系统，不代表微信收到、不代表焦点在输入框、不代表消息离手（焦点丢失导致 `ctrl+v` 落别处、图片粘贴渲染延迟、`alt+s` 打在渲染完成前、微信离线挂红色叹号、被频率限制——这些三重本地确认全绿但对方什么也没收到，评审 硬伤 2）。因此「已发送」必须满足 §3.1 步骤 5 的三条真验证（输入框清空 / 新增气泡 / 红色叹号），否则 `local_submitted` 不配叫 submitted，应改名为 `keys_pressed`：

- ✅ 输入框已清空（发送后截图确认，最可靠、最便宜，必须做）
- ✅ 会话区底部出现新增气泡（前后像素差异——这里用图像比对才用对地方）
- ✅ 无红色叹号（模板匹配，抓掉线/被拦）
- ✅ 发送键 `alt,s` 调用无异常（仍记录，但不再是充分条件）
- ✅ 剪贴板写入成功（本地正确性，非发送证明）
- ✅ 发送后无弹窗遮挡
- ✅ 截图留证落盘（事后核查，非判据）

### 失败进度可观测（评审 中等问题）
`send()` 多图发送中途失败时，异常须携带「已成功条目数 / 已成功图片索引」；并把 per-item 结果写入 manifest。转人工时人能知道对方实际收到了几张，避免瞎猜或重发（重发违反「绝不重发」原则）。

### 状态机衔接（沿用现有）
- 多重确认全通过 → manifest 该项 `status = "local_submitted"`
- 任一确认失败 → `status = "submission_uncertain"`（**禁止自动重试**，需人工 retry-after-review）
- 全组 local_submitted → `mark_dispatch_task_sent`
- 存在 submission_uncertain → `mark_dispatch_task_needs_review`（不标 sent）

> 「已发送」状态不可回退；submission_uncertain 的任务不自动重试（沿用 `dispatch_scheduler.py` 现有容错逻辑）。

---

## 7. Step 6 — 日志与可观测性

### 7.1 每次发送记录
时间 / 好友备注名 / 消息类型 / 成功-失败 / 失败原因分类 / 耗时 / 截图路径 → 落盘 `storage/send_log.jsonl` + 结构化日志

### 7.2 自检结果落盘
`storage/health_report.json`：完整环境快照（微信版本、DPI、分辨率、显示器数、hwnd、窗口基准矩形）+ 各检查项通过/失败 + 失败原因

### 7.3 界面告警
- 自检不通过 → 界面最显眼处红色告警条，下次启动主动提示未处理项
- 失败发送记录在界面显眼处保留，程序下次启动主动提示未处理项

---

## 8. 配置项（`config.py` Settings）

### 沿用现有
- `wechat_search_bar_x/y`、`wechat_input_x_offset/y_offset`、`wechat_send_interval_min/max`、`wechat_popup_wait_timeout/interval`、`wechat_opening_text`
- `test_wechat_sender_override`（扩展取值 `real`/`failing`/`dryrun`/`test_account`）

### 新增
| 字段 | 默认值 | 说明 |
|---|---|---|
| `wechat_version_whitelist: list[str]` | `["4.1.1.19"]` | 实测通过的微信版本白名单（当前锁定 4.1.1.19：沿用该版本已验证过的坐标，回退自 4.1.9.57） |
| `wechat_screenshot_dir: str` | `"storage/send_proofs"` | 发送后截图留证目录 |
| `wechat_test_accounts: list[str]` | `[]` | TestAccountSender 白名单 |
| `wechat_window_drift_tolerance: int` | `20` | 窗口矩形漂移容忍像素（被动兜底，主约束靠启动 SetWindowPos 固定） |
| `wechat_ocr_engine: str` | `"rapidocr"` | 会话标题 OCR 引擎（rapidocr/paddleocr），用于身份精确比对 |
| `wechat_desktop_recheck_interval: int` | `300` | 运行期复检桌面可交互/微信版本/窗口类的间隔秒（热降级用） |
| `wechat_smoke_test_account: str` | `""` | 受控真实小号，端到端冒烟第二条目标（空则跳过真实账号冒烟） |
| `wechat_pin_window: bool` | `true` | 启动是否 `SetWindowPos` 固定微信窗口尺寸位置（幂等） |

所有新增字段同样接 `.env` 持久化（沿用 `app/api/settings.py` 的 `_persist_env_value` 机制）。

---

## 9. 状态机与上层衔接

### 9.1 门面（`sender.py` 改造）
```python
# 保留对外签名不变
def send(remark, text, images, settings) -> None:
    impl = _select_impl(settings)   # 根据 override + 自检结果选实现
    for img in images:
        r = impl.send_image(remark, img)
        if not r.success: raise _to_exception(r)
    if text:
        r = impl.send_text(remark, text)
        if not r.success: raise _to_exception(r)

def verify_remark(remark, settings) -> ChatVerificationResult:
    impl = _select_impl(settings)
    # 复用现有 verify_remark 逻辑（走 impl 的定位+校验）
```

### 9.2 与 dispatch_scheduler 衔接（不动）
- `dispatch_scheduler._send_ready_task` 仍调 `send`/`verify_remark`，异常分类（`ClipboardVerificationError` 等）沿用
- `submission_uncertain` / `local_submitted` manifest 逻辑沿用
- 开场语 `wechat_opening_text` 编排沿用（每任务首次独立 send 调用）

---

## 10. 验收标准（调整后）

1. ✅ 分支 `feat/uia-sender` 已创建，改动只在该分支
2. ✅ Step 0 探针脚本 `scripts/probe_env.py` 可独立运行（已完成，证伪 UIA）
3. ✅ **自检不通过时，程序 100% 无法进入真实发送模式**（写测试用例验证：mock 各检查项失败 → 确认走 DryRunSender）
4. ✅ 连续向「文件传输助手」+ 受控真实小号 发送 30 轮「1 图 + 1 段文案」，**依赖 §3.1 步骤 5 的真验证（输入框清空/新增气泡/红色叹号）自动判定不丢失、不乱序、不重复**；多重确认本身观测不到「丢失」，原标准不可判定，现以真验证使其可机判（若真验证未接入则明确改为人工核对 30 轮）
5. ✅ 手动最小化微信窗口 / 弹模态窗口 → 程序正确跳过并告警，不乱点（`ensure_wechat_clear` 已有 + is_ready）
6. ✅ 窗口被移动/缩放 → 检测矩形漂移 → 降级演习（新增护栏）
7. ✅ 图片以图片消息形式发出（`Clipboard.SetImage`），对方可保存为图片（非文件附件）
8. ⚠️ 回读验证降级为「截图留证 + 真发送验证（输入框清空/新增气泡/红色叹号）」，不再依赖 UIA 读控件文本（已对齐）
9. ✅ 会话身份用 OCR 精确判等（或独立窗口标题），**不再用 pHash/像素比对/首发建基准**；构造「张三 vs 张三丰」同名近邻用例验证不误发（评审 硬伤 1）
10. ✅ 无人值守三杀手覆盖：锁屏/休眠/RDP 断开 → 桌面可交互检测拦截；DPI 非感知 → 启动拦截；微信静默更新 → 运行期热降级（评审 硬伤 3）
11. ✅ `verify_remark` 与后台发送共用 `_SEND_LOCK`，并发登记不抢窗口（评审 硬伤 4）
12. ✅ `open_chat` 一次定位 + 锁内连发多条 + 每条后校验输入框清空；`send()` 失败携带已成功条目（评审 中等问题）

---

## 11. 明确不做的事

- ❌ UIAutomation 控件定位（当前环境微信屏蔽，不可用）
- ❌ 真正的消息内容回读（坐标方案做不到可靠，硬做引入误判）
- ❌ 改动 AI 生成、任务调度、数据库结构、前端页面（仅前端加自检告警条）
- ❌ 引入逆向 Hook 类方案（封号风险）
- ❌ 为「兼容更多微信版本」做多套适配分支（本期只锁 4.1.1.19）
- ❌ 自动重试失败的发送（一条失败转人工）

---

## 12. 实施顺序

1. **Step 1**：`sender_base.py`（接口 + 数据结构 + Protocol）
2. **Step 2a**：`wechat_sender.py`（坐标实现，复用 win32.py + sender.py 重构）
3. **Step 2b**：`dryrun_sender.py` + `test_account_sender.py`
4. **Step 2c**：`sender.py` 门面改造（send/verify_remark 委托 + _select_impl）
5. **Step 3**：`check_environment()` 实现 + 启动集成
6. **Step 4**：`is_ready()` 实现
7. **Step 5**：多重确认 + 截图留证 + 状态机衔接
8. **Step 6**：日志落盘 + 前端自检告警条
9. **测试**：自检拦截用例、30 轮发送、最小化/弹窗/窗口漂移场景

---

## 附：Step 0 探针产物

- `scripts/probe_env.py`：9 步环境探针（已证伪 UIA，保留作环境诊断工具）
- `D:\WeChatInstallers\WeChatWin_4.1.1.19.exe`：4.1.1.19 安装包（SHA256 待下载落盘后校验，锁版本用）
- 探针结论：UIA 在当前环境不可用，详见本文档第 0 节

---

## 13. 外部 AI 评审采纳清单（2026-07-29）

另一 AI 审阅了本方案，指出 4 处硬伤 + 7 处中等问题。**逐条核对真实代码（`sender.py` / `dispatch_scheduler.py` / `win32.py`）后，全部采纳**，已在前述章节落实。评估结论：

### 硬伤（全部采纳）
- **硬伤 1（身份校验方向错）— 完全合理，采纳。** pHash/像素比对本就不判文字相等，且「首发建基准」会把错误固化成 fail-confident。改为 OCR 精确判等；独立窗口标题方案作为优先实测的更优解（§3.2）。
- **硬伤 2（确认证明不了已发）— 完全合理，采纳。** 三重本地确认全是「发送前/本地」状态，观测不到「丢失」。补输入框清空 / 新增气泡 / 红色叹号三条真验证（§3.1 步骤 6）；状态语义从 `local_submitted` 收紧为「真验证通过才提交」（§6）。
- **硬伤 3（无人值守三杀手）— 完全合理，采纳。** DPI 从「仅记录」升为拦截 + 启动设 `PER_MONITOR_AWARE_V2`；补桌面可交互检测；版本检查改为运行期周期复检 + 热降级 + 关闭自动更新（§4 第 6/9/11 项）；并统一 skip/degrade 语义（§5）。
- **硬伤 4（verify_remark 抢窗口）— 完全合理，采纳。** `verify_remark` 必须共用 `_SEND_LOCK`，拿不到锁返回「系统正忙」（§3.2 其它硬性要求）。

### 中等问题（全部采纳）
- `_select_impl` 每次 `send` 重跑自检会重复发测试消息 → 自检启动跑一次并缓存（§4 末注）。
- `send()` 多图失败不报进度 → 异常携带已成功条目 + manifest 记 per-item（§6 末）。
- 缺 `open_chat`，每条消息重搜 3 次 → 一次定位锁内连发（§3.1 步骤 2、§3.3）。
- 搜索栏定位不可靠、`FRIEND_DUPLICATE` 是常态 → `open_chat` 精确选中等候项 + OCR 校验（§3.3）。
- 剪贴板全局资源 → 重试退避 + 粘贴前再确认；透明通道经 DIB 变黑，预检聚焦像素尺寸（§3.2 其它硬性要求）。
- 验收 #4 不可判定 → 用真验证使其可机判（§10 第 4 项）。
- 文件传输助手验不到真风险 → 增补受控真实小号端到端（§4 第 10 项）。

### 对评审的补充说明（非反驳，落地注意）
- 独立窗口方案在 4.1.9 是否仍可用**尚未验证**，标为「优先实测、待验证」；若不可用则退回 OCR 主方案，不影响整体设计。
- OCR 引入离线推理依赖（RapidOCR/PaddleOCR），部署体积增大；若独立窗口方案验证通过可规避该依赖。
- 核对发现：当前代码 `send()` 已用窗口矩形相对偏移（每次重读 `rect`），故窗口移动不致命；真正缺口是 DPI 感知缺失与窗口尺寸未锁定，已补（§4 第 6/7 项）。
- 当前实际代码**尚无任何互斥锁**（计划新增 `_SEND_LOCK`）；评审所指「锁只保护发送路径」是方案层面的设计意图，落实时务必让 `verify_remark` 与发送共用同一把锁。
