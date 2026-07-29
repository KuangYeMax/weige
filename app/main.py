from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.dispatch import create_dispatch_router
from app.api.products import create_router
from app.api.settings import create_settings_router
from app.api.wechat import create_wechat_router
from app.config import PROJECT_ROOT, Settings
from app.errors import AppError
from app.services.db import init_db
from app.services.dispatch_scheduler import run_dispatch_scheduler


STATIC_ROOT = PROJECT_ROOT / "app" / "static"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    for directory in ("uploads", "generated", "metadata", "dispatch"):
        (resolved_settings.storage_root / directory).mkdir(parents=True, exist_ok=True)

    init_db(resolved_settings.db_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        stop_event = asyncio.Event()
        logger = logging.getLogger("app.dispatch")
        model = resolved_settings.dispatch_image_model or resolved_settings.bailian_image_model
        logger.info(
            "[DISPATCH] image provider = %s | model = %s",
            resolved_settings.dispatch_image_provider,
            model,
        )

        # 微信发送层启动自检（feat/uia-sender，计划 §4）
        # 仅在 win32 跑；自检不通过会设 _HEALTH_CACHE.healthy=False，
        # sender._select_impl 据此强制降级 DryRunSender
        if sys.platform == "win32":
            try:
                from app.services.wechat.wechat_sender import WechatSender, set_health_cache
                health = WechatSender(resolved_settings).check_environment()
                set_health_cache(health)
                application.state.wechat_health = health
                if health.healthy:
                    logger.info("[WECHAT] 自检通过：%s", health.details.replace("\n", " | ")[:200])
                else:
                    logger.warning(
                        "[WECHAT] 自检未通过，已强制演习模式。失败项=%s",
                        health.failed_checks,
                    )
            except Exception:
                logger.exception("[WECHAT] 启动自检异常，保持默认（未跑自检，real 模式仍可用）")
                application.state.wechat_health = None
        else:
            application.state.wechat_health = None

        scheduler_task = asyncio.create_task(run_dispatch_scheduler(resolved_settings, stop_event))
        application.state.dispatch_stop_event = stop_event
        application.state.dispatch_scheduler_task = scheduler_task
        try:
            yield
        finally:
            stop_event.set()
            await scheduler_task

    application = FastAPI(title="商品场景图 MVP", docs_url="/api/docs", lifespan=lifespan)
    application.state.settings = resolved_settings

    @application.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "REQUEST_INVALID",
                    "message": "请求参数未通过校验，请检查后重试",
                }
            },
        )

    application.include_router(create_router())
    application.include_router(create_dispatch_router())
    application.include_router(create_settings_router())
    application.include_router(create_wechat_router())
    application.mount(
        "/storage/uploads",
        StaticFiles(directory=resolved_settings.storage_root / "uploads", check_dir=True),
        name="uploads",
    )
    application.mount(
        "/storage/generated",
        StaticFiles(directory=resolved_settings.storage_root / "generated", check_dir=True),
        name="generated",
    )
    application.mount(
        "/storage/dispatch",
        StaticFiles(directory=resolved_settings.storage_root / "dispatch", check_dir=True),
        name="dispatch_files",
    )
    application.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_ROOT / "dashboard.html")

    @application.get("/dashboard", include_in_schema=False)
    def dashboard_page():
        return FileResponse(STATIC_ROOT / "dashboard.html")

    @application.get("/products", include_in_schema=False)
    def products_page():
        return FileResponse(STATIC_ROOT / "products.html")

    @application.get("/product/{product_id}", include_in_schema=False)
    def product_detail_page():
        return FileResponse(STATIC_ROOT / "product_detail.html")

    @application.get("/dispatch", include_in_schema=False)
    def dispatch_page():
        return FileResponse(STATIC_ROOT / "dispatch.html")

    @application.get("/dispatch/{task_id}", include_in_schema=False)
    def dispatch_detail_page():
        return FileResponse(STATIC_ROOT / "dispatch_detail.html")

    @application.get("/logs", include_in_schema=False)
    def logs_page():
        return FileResponse(STATIC_ROOT / "logs.html")

    @application.get("/settings", include_in_schema=False)
    def settings_page():
        return FileResponse(STATIC_ROOT / "settings.html")

    @application.get("/workbench", include_in_schema=False)
    def workbench_page():
        return FileResponse(STATIC_ROOT / "index.html")

    return application


app = create_app()
