from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.products import create_router
from app.config import PROJECT_ROOT, Settings
from app.errors import AppError


STATIC_ROOT = PROJECT_ROOT / "app" / "static"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings()
    for directory in ("uploads", "generated", "metadata"):
        (resolved_settings.storage_root / directory).mkdir(parents=True, exist_ok=True)

    application = FastAPI(title="商品场景图 MVP", docs_url="/api/docs")
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
    application.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_ROOT / "index.html")

    return application


app = create_app()
