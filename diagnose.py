"""诊断脚本：检查视觉 API 调用链路，找出 Windows 上 fallback 到 mock 的根因。

运行方式: python diagnose.py
"""

import base64
import io
import os
import platform
import site
import subprocess
import sys
import time
import traceback
from pathlib import Path

# ────────────── helpers ──────────────

SCRIPT_DIR = Path(__file__).resolve().parent
CWD = Path.cwd().resolve()

_OK = "OK"
_FAIL = "FAIL"


def _banner(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _check(label: str, ok: bool, detail: str = ""):
    status = _OK if ok else _FAIL
    print(f"  [{status}] {label}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"         {line}")


def _ensure_no_try(block):
    """执行 block，不 try 任何异常；异常会直接炸出脚本。
    用于要求"不许 try 掉"的检查项。"""
    return block()


# ────────────── 1) 环境信息 ──────────────

_banner("1) Python 版本、操作系统、工作目录、脚本目录")
_check("Python 版本", True, sys.version)
_check("操作系统", True, f"{platform.system()} {platform.release()} ({platform.version()})")
_check("当前工作目录 CWD", True, str(CWD))
_check("脚本所在目录", True, str(SCRIPT_DIR))

# ────────────── 2) .env 文件 ──────────────

_banner("2) .env 文件检查")

env_candidates = [
    # 按优先级：pydantic-settings 默认策略
    CWD / ".env",
    SCRIPT_DIR / ".env",
    SCRIPT_DIR.parent / ".env",
]
found_env = None
for p in env_candidates:
    if p.is_file():
        found_env = p
        break

if found_env:
    raw = found_env.read_bytes()
    encoding = "utf-8-sig" if raw[:3] == b"\xef\xbb\xbf" else "utf-8"
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    detail = (
        f"绝对路径: {found_env.resolve()}\n"
        f"文件编码: {encoding}\n"
        f"包含 BOM: {has_bom}\n"
        f"文件大小: {len(raw)} 字节"
    )
    _check("文件存在", True, detail)

    # 尝试用 load_dotenv
    try:
        from dotenv import load_dotenv

        loaded = load_dotenv(found_env, override=True)
        _check("load_dotenv 成功", loaded, f"加载路径: {found_env.resolve()}")
    except Exception as e:
        _check("load_dotenv 调用", False, f"异常: {type(e).__name__}: {e}")
else:
    _check("文件存在", False, f"搜索路径: {[str(p) for p in env_candidates]}")

# ────────────── 3) API Key 环境变量 ──────────────

_banner("3) 环境变量 — API Key")

for var_name in ["ARK_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY"]:
    val = os.environ.get(var_name, "")
    if val:
        has_invisible = any(c not in val for c in val if c.isprintable() and c not in " \t")
        # 检查不可见字符
        invisible_chars = [repr(c) for c in val if not c.isprintable() and c not in "\n\r"]
        # 检查首尾空白
        stripped = val.strip()
        has_whitespace = val != stripped
        detail = (
            f"长度: {len(val)}\n"
            f"前4位: {val[:4]!r}\n"
            f"后4位: {val[-4:]!r}\n"
            f"含首尾空白: {has_whitespace}\n"
            f"含不可见字符: {invisible_chars if invisible_chars else '无'}"
        )
        _check(f"{var_name} 已读取", True, detail)
    else:
        _check(f"{var_name} 已读取", False, "环境变量为空或未设置")

# ────────────── 4) 网络连通性 ──────────────

_banner("4) 网络连通性")

ARK_BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

# 环境代理
for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"]:
    v = os.environ.get(proxy_var, "")
    if v:
        _check(f"代理 {proxy_var}", True, f"当前值: {v}")

try:
    import httpx
    import certifi
    _check("httpx 版本", True, httpx.__version__)
    _check("certifi 版本", True, certifi.__version__)
except ImportError as e:
    _check("httpx/certifi 可用", False, str(e))

try:
    import requests
    _check("requests 版本", True, requests.__version__)
except ImportError as e:
    _check("requests 可用", False, str(e))

# HTTPS 探测
target = ARK_BASE_URL.rstrip("/") + "/chat/completions"
print(f"\n   探测 URL: {target}")
print(f"   方法: HEAD")

# 用 httpx.Client 探测（不 send 真实 payload，仅测连通性）
try:
    started = time.perf_counter()
    with httpx.Client(timeout=10.0, verify=certifi.where()) as client:
        resp = client.get(ARK_BASE_URL, headers={"Accept": "application/json"})
    elapsed = round((time.perf_counter() - started) * 1000)
    _check("HTTPS 连通性", True, f"状态码: {resp.status_code}, 耗时: {elapsed}ms")
except Exception as e:
    elapsed = round((time.perf_counter() - started) * 1000)
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    _check("HTTPS 连通性", False, f"耗时: {elapsed}ms\n{tb}")

# ────────────── 5) 测试图片 ──────────────

_banner("5) 测试图片文件")

# 用一张已有的上传图片作为测试
test_image_candidates = sorted(Path(SCRIPT_DIR / "storage" / "uploads").glob("*.jpg"))
if not test_image_candidates:
    test_image_candidates = sorted(Path(SCRIPT_DIR / "storage" / "uploads").glob("*.png"))

if test_image_candidates:
    TEST_IMAGE = test_image_candidates[0].resolve()
else:
    # 用一张测试 fixtures 同款合成图
    TEST_IMAGE = SCRIPT_DIR / "diagnose_test_image.jpg"
    img = __import__("PIL").Image.new("RGB", (720, 960), (38, 122, 98))
    img.save(str(TEST_IMAGE), format="JPEG")
    TEST_IMAGE = TEST_IMAGE.resolve()

print(f"   测试图片路径: {TEST_IMAGE}")

_check("文件存在", TEST_IMAGE.is_file(), f"字节大小: {TEST_IMAGE.stat().st_size}")

try:
    from PIL import Image as PILImage
except ImportError:
    _check("PIL 可用", False, "未安装 Pillow")
    PILImage = None

if PILImage:
    try:
        with PILImage.open(TEST_IMAGE) as img:
            img.load()
            _check(
                "PIL 打开成功",
                True,
                f"宽: {img.width}, 高: {img.height}, 格式: {img.format}, 模式: {img.mode}",
            )
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        _check("PIL 打开成功", False, tb)

    try:
        with PILImage.open(TEST_IMAGE) as img:
            rgb = img.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            _check(
                "base64 编码",
                True,
                f"前50字符: {b64[:50]!r}\n         base64 总长度: {len(b64)}",
            )
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        _check("base64 编码", False, tb)

# ────────────── 6) 真实 API 调用 ──────────────

_banner("6) 真实豆包识图 API 调用")

ARK_API_KEY = os.environ.get("ARK_API_KEY", "")
ARK_VISION_MODEL = os.environ.get("ARK_VISION_MODEL", "")
ARK_VISION_BASE_URL = (
    os.environ.get("ARK_VISION_BASE_URL", "") or ARK_BASE_URL
)

print(f"   请求 URL: {ARK_VISION_BASE_URL}/chat/completions")
print(f"   Model ID: {ARK_VISION_MODEL}")
print(f"   API Key:  {'***' + ARK_API_KEY[-4:] if ARK_API_KEY else '空'}")

if not ARK_API_KEY or not ARK_VISION_MODEL:
    _check("API 调用", False, "ARK_API_KEY 或 ARK_VISION_MODEL 为空，跳过调用")
else:
    prompt_text = "请用中文描述这张图片中的商品，输出 JSON 格式的事实卡。"
    schema_text = (
        '{"商品名称": "...", "识别置信度": "高/中/低", "商品品类": "...", '
        '"商品形态": "...", "整体特征": "...", "关键结构": [...], '
        '"颜色与材质观感": [...], "自然场景": [...], "不确定项": [...]}'
    )
    full_prompt = prompt_text + "\n\nJSON Schema:\n" + schema_text

    # 构建消息
    with PILImage.open(TEST_IMAGE) as img:
        rgb = img.convert("RGB")
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG")
        b64_img = base64.b64encode(buf.getvalue()).decode("ascii")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": full_prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                },
            ],
        }
    ]
    payload = {"model": ARK_VISION_MODEL, "messages": messages, "temperature": 0.1}
    headers = {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=120.0, verify=certifi.where()) as client:
            resp = client.post(
                f"{ARK_VISION_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )
        elapsed = round((time.perf_counter() - started) * 1000)
        body = resp.text
        _check(
            "HTTP 状态码",
            resp.status_code == 200,
            f"{resp.status_code} ({elapsed}ms)",
        )
        print(f"\n   原始响应体（前 2000 字符）:\n{body[:2000]}")
        if resp.status_code == 200:
            # 尝试解析
            import json
            try:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                print(f"\n   模型回复内容（前 500 字符）:\n{content[:500]}")
            except Exception as e:
                print(f"\n   响应解析失败: {type(e).__name__}: {e}")
    except Exception as e:
        elapsed = round((time.perf_counter() - started) * 1000)
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        _check("API 调用", False, f"耗时: {elapsed}ms\n{tb}")

# ────────────── 7) 关键依赖版本 ──────────────

_banner("7) 关键依赖版本")

PACKAGES = ["requests", "httpx", "openai", "volcengine", "pillow", "python-dotenv"]

try:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        _check("pip freeze", False, f"stderr: {result.stderr[:500]}")
    else:
        freeze_lines = result.stdout.strip().splitlines()
        found = []
        not_found = []
        for pkg in PACKAGES:
            matched = [ln for ln in freeze_lines if ln.lower().startswith(pkg.lower() + "==")]
            if matched:
                found.append(matched[0])
            else:
                not_found.append(pkg)
        detail_lines = list(found)
        if not_found:
            detail_lines.append("")
            detail_lines.append(f"  未安装: {', '.join(not_found)}")
        _check("版本清单", True, "\n".join(detail_lines))
except Exception as e:
    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    _check("pip freeze", False, tb)

print(f"\n{'='*60}")
print(f"  诊断完成  |  脚本目录: {SCRIPT_DIR}")
print(f"{'='*60}")
