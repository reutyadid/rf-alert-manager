from fastapi import FastAPI

from alert_service.api.routes import install_error_handlers, router


def create_app(lifespan=None) -> FastAPI:
    """Routes read `app.state.service` and `app.state.health_checks`, set by main.py."""
    app = FastAPI(title="alert-service", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    install_error_handlers(app)
    return app
