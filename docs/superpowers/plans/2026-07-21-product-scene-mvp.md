# 商品场景图 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个默认离线可运行、可切换火山方舟的商品事实卡与参考图生图单页 MVP。

**Architecture:** FastAPI 托管 API、静态页面和受限文件目录，Pydantic 定义通用品类事实卡，两个轻量 provider 边界分别处理视觉和生图。JSON 元数据落盘，前端用 Alpine.js 管理四阶段状态。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, httpx, Pillow, pytest, Alpine.js CDN, Tailwind CSS CDN

---

### Task 1: 契约与安全输入

**Files:**
- Create: `tests/test_health.py`
- Create: `tests/test_fact_card.py`
- Create: `tests/test_upload.py`
- Create: `app/config.py`
- Create: `app/schemas.py`
- Create: `app/main.py`
- Create: `app/api/products.py`

- [ ] 写健康检查、两类不同商品事实卡、缺少商品名称、合法图片、伪图片和超大文件测试。
- [ ] 运行 `pytest tests/test_health.py tests/test_fact_card.py tests/test_upload.py -q`，确认因 `app` 不存在而失败。
- [ ] 实现带别名和默认值的 Pydantic 模型、统一错误、Pillow 解码与 UUID 落盘。
- [ ] 重跑同一命令，预期全部通过。

### Task 2: 可编辑事实卡与 mock 生成闭环

**Files:**
- Create: `tests/test_generate.py`
- Create: `app/services/vision/mock.py`
- Create: `app/services/image_generation/mock.py`
- Create: `app/services/fact_card_compress.py`
- Create: `app/services/prompts/fact_card.txt`
- Create: `app/services/prompts/image_generation.txt`

- [ ] 写事实卡保存、场景选择、尺寸映射、mock 图片可访问、产品 404 和静态目录穿越测试。
- [ ] 运行 `pytest tests/test_generate.py -q`，确认端点缺失导致失败。
- [ ] 实现中性 mock 事实卡、短提示拼装、mock 图片和完整 generation metadata。
- [ ] 重跑生成测试，预期全部通过。

### Task 3: 火山方舟适配器

**Files:**
- Create: `tests/test_volcengine.py`
- Create: `app/services/vision/volcengine.py`
- Create: `app/services/image_generation/volcengine.py`
- Create: `app/services/http.py`

- [ ] 写 vision JSON 清洗/一次修复、image 请求体参考图字段、未配置密钥和响应图片解码测试。
- [ ] 运行 `pytest tests/test_volcengine.py -q`，确认 provider 尚不存在而失败。
- [ ] 实现独立路由的 vision/image 请求、限定重试、错误映射和结果立即落盘。
- [ ] 重跑 provider 测试，预期全部通过且不发送真实请求。

### Task 4: 单页界面与运行文档

**Files:**
- Create: `app/static/index.html`
- Create: `app/static/app.js`
- Create: `app/static/styles.css`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `README.md`

- [ ] 实现拖拽预览、loading/防重、事实卡摘要与 JSON 编辑、动态场景、生成详情和下载。
- [ ] 在 1440px 和 390px 视口检查无重叠、文本不溢出、完整 mock 操作可执行。
- [ ] 编写安装、配置、启动、测试、安全、错误排查和非目标文档。
- [ ] 运行 `pytest -q`，预期所有测试通过。
- [ ] 运行 `uvicorn app.main:app --host 127.0.0.1 --port 8000` 并请求 `/api/health`。

### Task 5: 审查与交付

**Files:**
- Review: all changed files

- [ ] 对照需求逐项检查功能、错误格式、安全边界和 metadata 字段。
- [ ] 运行代码审查并修复所有 Critical/Important 问题。
- [ ] 全新运行 `pytest -q`、Python 编译检查和 HTTP smoke test。
- [ ] 保持开发服务器运行，报告访问 URL、测试数量和真实 provider 的人工配置项。
