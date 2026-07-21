# 同物景：商品好评图 MVP

本地运行的单页工作台，用一张商品原图完成：上传与安全校验、商品事实卡识别、人工编辑、参考图生图、结果落盘与下载。

## 功能范围

- 上传 JPG/JPEG、PNG、WEBP，最大 10MB，最小边 512px，总像素不超过 4000 万
- 自动修正 EXIF 方向、转 RGB、UUID 文件名落盘
- 使用品类无关的 Pydantic FactCard；只有“商品名称”必填
- 事实卡摘要与完整 JSON 均可编辑、验证、保存、复制
- 从事实卡动态读取场景，低置信度或需人工确认的场景不会默认选中
- 支持完整照、中近景、细节照以及 3:4、1:1 两种比例
- 每次生成一张，展示 provider、model、尺寸、耗时、创建时间与 seed
- 生成图片立即保存到本地，支持下载与重新生成
- 默认 mock 全链路，也可分别切换火山视觉理解与火山图生图

保真目标是让普通用户一眼看出生成图与原图是同一件商品，并像普通手机实拍。允许轻微角度、光线和背景差异；重点防止数量、整体轮廓、主色、明显文字、肢体和结构穿模等低级穿帮，不追求像素级复刻。

## 环境要求

- Python 3.11 或更高版本
- 可选：火山方舟 API Key、视觉模型 ID、Seedream 图片模型 ID
- 浏览器可访问 CDN，以加载 Alpine.js、Tailwind CSS 和 Lucide 图标

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

## 测试

```bash
pytest -q
```

或：

```bash
python -m pytest -q
```

测试覆盖健康检查、上传格式/大小/尺寸、通用品类 FactCard、编辑保存、mock 生成、产品 404、火山未配置、静态目录穿越、比例映射、火山图片请求体和视觉 JSON 围栏清洗。测试不会调用真实 API，也不会产生费用。

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
