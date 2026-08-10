"""Environment-built ASGI application used by Uvicorn/Railway."""

from .app import create_app_from_env


app = create_app_from_env()

