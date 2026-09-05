"""Start Baraq AI in local standalone Lab mode."""

from __future__ import annotations

import os


def main() -> None:
    os.environ.setdefault("BARAQ_RUNTIME_MODE", "lab")
    os.environ.setdefault("PROVIDER_MODE", "mock")

    import uvicorn

    from app.core.config import get_settings

    settings = get_settings()
    if settings.app_env == "production":
        raise SystemExit("Lab mode is blocked when APP_ENV=production")
    uvicorn.run(
        "app.main:app",
        host=settings.lab_host,
        port=settings.lab_port,
        reload=settings.app_env == "development",
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
