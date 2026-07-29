# 同物景：商品好评图 MVP

本地运行的单页工作台，用一张商品原图完成：上传与安全校验、商品事实卡识别、人工编辑、参考图生图、结果落盘与下载。

## 功能范围

- 上传 JPG/JPEG、PNG、WEBP，最大 10MB，最小边 512px，总像素不超过 4000 万
- 自动修正 EXIF 方向、转 RGB、UUID 文件名落盘
- 使用品类无关的 Pydantic FactCard；只有“商品名称”必填
- 事实卡摘要与完整 JSON 均可编辑、验证、保存、复制
- 从事实卡动态读取场景，低置信度或需人工确认的场景不会默认选中
- 支持中近景、细节照两种拍摄类型以及 3:4、1:1 两种比例
- 每次生成一张，展示 provider、model、尺寸、耗时、创建时间与 seed
- 生成图片立即保存到本地，支持下载与重新生成
- 默认 mock 全链路，也可分别切换火山视觉理解与火山图生图

保真目标是让普通用户一眼看出生成图与原图是同一件商品，并像普通手机实拍。允许轻微角度、光线和背景差异；重点防止数量、整体轮廓、主色、明显文字、肢体和结构穿模等低级穿帮，不追求像素级复刻。

## 环境要求

- Python 3.11 或更高版本
- 可选：火山方舟 API Key、视觉模型 ID、Seedream 图片模型 ID
- 浏览器可访问 CDN，以加载 Alpine.js、Tailwind CSS 和 Lucide 图标

## 微信自动发送前提

- 会话精确校验和自动发送只支持 Windows；macOS 只能使用 mock 测试，不能现场验证 UIA。
- Windows 上锁定微信桌面版 `3.9.12.x`，并关闭自动更新。微信 `4.x` 不提供本项目依赖的 UIA 界面结构。
- 请先使用测试号或小号。自动化可能触发微信退出登录或账号风控/封禁风险。
- 登记前会先校验好友备注与当前会话页头完全一致；校验失败会阻止登记，发送过程中的校验失败会转为“需人工复核”，不会自动重试。

`.env` 中的 UIA 坐标和发送间隔可按目标 Windows 设备调整：`WECHAT_SEARCH_BAR_X`、`WECHAT_SEARCH_BAR_Y`、`WECHAT_INPUT_X_OFFSET`、`WECHAT_INPUT_Y_OFFSET`、`WECHAT_SEND_INTERVAL_MIN`、`WECHAT_SEND_INTERVAL_MAX`。默认值已列在 `.env.example`。

## 好评文案配置

`REVIEW_FORBIDDEN_WORDS_HARD`、`REVIEW_FORBIDDEN_WORDS_SOFT`、`REVIEW_COLLOQUIAL_POOL` 和 `REVIEW_MINOR_FLAW_DEFAULTS` 都是 JSON 数组，必须使用例如 `["词一","词二"]` 的格式。旧配置键 `REVIEW_FORBIDDEN_WORDS` 仍被作为硬禁词列表兼容；新配置优先使用 `REVIEW_FORBIDDEN_WORDS_HARD`。模型输出会去除首尾及多余空白；命中硬禁词、空输出或重试耗尽时，会改用非空的安全兜底文案。

## 安装与启动

```bash
python -m venv .venv
```

macOS / Linux：

```bash
source .venv/bin/activate
```

Windows：

```powershell
.venv\Scripts\activate
```

安装依赖并创建配置：

```bash
pip install -r requirements.txt
cp .env.example .env
```

启动：

```bash
uvicorn app.main:app --reload
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。API 文档在 `/api/docs`。

## Mock 模式

`.env.example` 默认配置：

```dotenv
VISION_PROVIDER=mock
IMAGE_PROVIDER=mock
```

不需要 API Key。mock vision 根据图片宽高和平均颜色生成中性事实卡，不猜具体品类；所有不确定事实会明确标记。mock image 会保留原图主体，添加简单承托背景、边框和“MOCK · AI 场景示意图”角标，结果保存到 `storage/generated/`。

mock 事实卡中的候选场景是低置信度并要求人工确认，因此页面不会替用户默认选择。核对后手动点击该场景即可生成。

## 火山方舟配置

视觉理解与图片生成可以独立切换；它们使用不同的 API 路由，不共享请求组装逻辑。

```dotenv
VISION_PROVIDER=volcengine
IMAGE_PROVIDER=volcengine

ARK_API_KEY=your_server_side_key
ARK_VISION_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_IMAGE_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_VISION_MODEL=your_vision_model_id
ARK_IMAGE_MODEL=your_seedream_model_id
```

也可只将其中一个 provider 设为 `volcengine`。`ARK_BASE_URL` 是两个独立 base URL 均为空时的兼容回退值。

- Vision：`POST {ARK_VISION_BASE_URL}/chat/completions`，参考图作为 user message 的 `image_url` data URI。
- Image：`POST {ARK_IMAGE_BASE_URL}/images/generations`，参考图在 `image` 字段中以 data URI 传入，响应请求为 `b64_json` 并立即落盘。
- 映射尺寸：`1:1 -> 2048x2048`，`3:4 -> 1728x2304`（Seedream 5.0 lite 的 2K 标准尺寸）。

请求结构依据火山方舟官方[图片生成 API](https://www.volcengine.com/docs/82379/1541523)页面核对。火山会持续更新模型能力和允许尺寸；如果账户中所选模型版本不接受上述像素尺寸，请在 `app/services/image_generation/volcengine.py` 的 `SIZE_MAP` 中改成该模型文档明确允许的相邻尺寸。模型 ID 必须使用控制台实际开通的 ID，不要照抄示例名称。

当前示例中的 `doubao-seed-1-6-flash-250828` 支持多模态理解、视觉定位和结构化输出，适合生成事实卡，但官方模型列表已标记为“即将下线”。它适合短期联调，不宜作为长期生产配置；上线前应迁移到仍在维护且支持多模态理解的模型，并重新验证事实卡质量。

## Provider 切换规则

- provider 只有 `mock` 和 `volcengine`；其他值会返回 `PROVIDER_NOT_FOUND`。
- 选择 `volcengine` 但缺少 Key 或对应模型 ID，会返回 `VOLCENGINE_NOT_CONFIGURED`，不会静默回退到 mock，也不会产生费用。
- 页面顶部同时显示当前 vision 和 image provider，避免把 mock 事实卡误认为真实识别结果后直接付费生图。
- 外部调用只对超时、429 和部分 5xx 最多重试两次；参数或鉴权错误不重试。

## 图生图效果优化开关

以下开关均在 `.env` 中配置，用于优化生成图的真实感和尺寸准确性。

### 拍摄类型（仅两种）

- **中近景**：商品占画面约 80%-90%，突出主要结构，可自然裁掉次要边缘。
- **细节照**：极近特写只拍一个关键局部，明显裁切，不得改成另一版本。细节照模式下保真约束自动让位。

"完整照"已移除。

### 参考图按拍摄类型裁切 `SHRINK_REFERENCE`

- 默认 `true`。生图前根据拍摄类型对参考图做预处理裁切：
  - **中近景**：裁去背景留白和底部广告横幅，让商品紧凑占据参考图 80%-90%。
  - **细节照**：裁取商品中心区域的一个局部并放大填满参考图，让模型只看到局部。
- 原始高清原图保留不动，裁切仅用于喂给火山图生图接口。
- 设为 `false` 可关闭此预处理直接用原图送入，方便 A/B 对照。

### 道具与文字全局禁止

生图提示词硬编码以下约束（不依赖场景卡内容）：

- 背景道具最多 1 件，禁止成对/对称/等距。
- 禁止烟雾、蒸汽、火焰、燃香、水流等动态效果。
- 默认"日常静置"而非仪式/使用进行中。
- 不放任何尺寸参照物（手机、尺子、硬币等）。
- 禁止凭空生成广告横幅、尺寸/材质文字、印章、水印、logo。

### 轻度调色 `POST_GRADE`

- 默认 `true`。生成后另存一张调色版，参数可配置：
  - `GRADE_SATURATION`：饱和度系数（默认 0.75，即降 25%，重点压高彩度区域）
  - `GRADE_HIGHLIGHT`：亮度/高光系数（默认 0.88，即压高光 12%，去掉金色/釉面过亮反光）
  - `GRADE_CONTRAST`：对比度系数（默认 0.93，轻微降对比）
  - `GRADE_GRAIN`：颗粒混合强度（默认 0.015，极轻颗粒）
- 原始火山返回的高清图始终保留不覆盖。
- 页面结果区默认展示调色版，标注当前显示"原始版/调色版"，并保留一键切换查看与下载两版。
- API 响应中 `generated_image_url` 为原始版，`graded_image_url` 为调色版。
- 设为 `false` 关闭调色，`graded_image_url` 为 `null`。
- 这是把 AI 高饱和"调回真实"，不是加噪降质造假。

### 真实感随机变量池 `REALISM_POOL`

- 默认 `true`。每次生图自动从五个变量池中随机抽取一组背景上下文，注入提示词的"环境要求"字段，使批量出图时桌面/背景/凌乱感不再千篇一律。
- 五个池：
  - **承托面**（surface）：原木桌、深色木桌、白色台面、大理石纹台面等 12 种，随机 1 选 1。
  - **做旧痕迹**（wear）：划痕、水杯环渍、茶渍等 9 种 + 程度档（全新 20%/轻微使用 45%/明显使用 30%/旧 5%），全新档不抽痕迹。
  - **日常杂物**（clutter）：文具/电子/饮食/生活/纸品五大类共 20+ 种，跨类不重复抽取，一律入画不完整、不居中、不成对。
  - **凌乱等级**（clutter_level）：整洁 30%/轻 40%/中 25%/乱 5%。细节照自动降档至最多"轻"。
  - **叙事场景**（scene）：已摆在架上/刚放桌上/办公桌一角等 8 种，拆快递权重压至 ≤5%。
- 组合数上千，永不撞脸。抽取结果写入 generation metadata，支持复现与复盘。
- `REALISM_SEED`：可选，固定后每次抽到同一组合，便于对照测试。留空则每次随机。
- 设为 `false` 关闭变量池，退回中性整洁背景。

与"道具全局禁止"的边界区分：
- **仪式道具**（香炉、蜡烛、烟雾等）继续硬禁止，不进入变量池。
- **日常生活杂物**（笔、茶杯、钥匙等）由变量池按凌乱等级控制，可超过 1 件，但禁止成对/对称/等距/居中。
- 手机等强尺度物若被抽中，必须标记为"入画不完整、被边缘裁切"，不得清晰完整居中充当尺度参照。

### 保真锁 — 八维通用自检

事实卡的保真锁不再只锁最吸睛的主体，而是对当前商品逐维自检 8 个通用穿帮维度，存在风险才生成对应锁条，不预设任何固定品类部件清单：

1. **数量** — 主体/套装/重复部件各几个，锁死防增减。
2. **多部件·多材质** — 非主体结构件（底座/支架/托盘/包装/附件）各锁形态、材质、连接方式。
3. **材质对比** — 透明↔不透明、哑光↔镜面等分界不得互换。
4. **悬空·动态·复杂几何** — 卷曲/悬挑/镂空/细长结构保持姿态不得简化。
5. **透明边界** — 透明件与相邻结构边界清晰，不得糊化穿模。
6. **配色分区** — 色区归属与渐变方向防串色。
7. **文字·标识** — 能读锁原样，读不清锁"不生成文字"。
8. **对称·阵列** — 该对称的锁对称，该等距的锁规整。

核心原则：**凡承托、固定、连接主体的结构件，即使不是卖点也必须各锁一条。** 配角变形整图即假。

### 视角容差

事实卡新增 Optional 字段 `视角容差`，视觉模型判断当前商品从单张参考图换角度的安全程度：

| 等级 | 条件 | 生图约束 |
|------|------|----------|
| 高 | 规则几何（盒、杯、瓶、球），各面可从单图推断 | 允许较自由换角度 |
| 中 | 结构略复杂但主要面可推断 | 仅允许轻微角度偏移 |
| 低 | 复杂/透明/镂空/多部件/强正面文字图案 | 锁定原图视角，极小幅度浮动 |

- **保守默认**：字段缺失或无法判断时按"低"处理。穿帮代价 >> 少一个角度的代价。
- **多样性分工**：低容差商品的出图多样性靠换场景/光线/承托面/杂物（变量池），不靠转角度。视角约束优先级高于变量池。

### 尺寸效果注意

本 MVP 不含生成后自动尺寸/穿帮复检闭环。生成后人工瞄一眼商品尺寸比例再使用。

## 测试

```bash
pytest -q
```

或：

```bash
python -m pytest -q
```

测试覆盖健康检查、上传格式/大小/尺寸、通用品类 FactCard、编辑保存、mock 生成、产品 404、火山未配置、静态目录穿越、比例映射、火山图片请求体、视觉 JSON 围栏清洗、参考图预处理函数、调色函数、shot_type 注入文本和尺寸映射。测试不会调用真实 API，也不会产生费用。

## 数据与安全

- `.env` 已忽略；API Key 只从后端读取，不返回浏览器，不写入 metadata。
- 不要把真实 Key 写入代码、README、测试、截图或日志。
- 上传文件用 Pillow 实际解码，用户原始文件名不会用于存储路径。
- API 不接受任意本地文件路径；静态文件只从 `storage/` 提供。
- 图片文字、事实卡文本和模型输出都是不可信数据，不会作为系统指令执行。
- 外部返回 URL 只允许 HTTP(S)，返回内容有大小限制并会立即下载。
- 外部返回内容会分块限流并由 Pillow 完整解码验证，非图片字节不会落盘或公开。
- `storage/metadata/` 保存产品和每次生成参数，包括失败原因，但不保存 API Key。

生产环境还应增加身份认证、请求频率限制、出站网络主机白名单、恶意图片扫描和定期数据清理；这些不在本地 MVP 范围内。

## 常见错误

`IMAGE_TOO_SMALL`：图片任一边低于 512px。请上传更高分辨率原图。

`FACT_CARD_INVALID`：商品名称缺失，或 JSON 字段类型不正确。页面会保留编辑内容，请修正后再次验证。

`VOLCENGINE_NOT_CONFIGURED`：当前 provider 已设为火山，但 Key 或对应模型 ID 为空。补齐 `.env` 后重启服务。

`PROVIDER_AUTH_FAILED`：Key 无效、过期或没有模型权限。检查火山控制台授权，不要把 Key 发送到前端排查。

`PROVIDER_RATE_LIMITED` / `PROVIDER_TIMEOUT`：服务限流或超时。应用已做有限重试，稍后再次生成。

页面样式或图标缺失：确认浏览器能访问 `cdn.tailwindcss.com` 和 `unpkg.com`；API 与核心流程不依赖这些 CDN。

## 文件位置

- 原图：`storage/uploads/`
- 生成图：`storage/generated/`
- 产品与生成 metadata：`storage/metadata/`
- 视觉提示词：`app/services/prompts/fact_card.txt`
- 生图提示词：`app/services/prompts/image_generation.txt`

## MVP 暂不包含

用户与登录、订单、微信发送、定时任务、商品编号映射、云部署、资产复用、多页面后台、导演模型、自动质检闭环，以及火山之外的真实生图 provider。
