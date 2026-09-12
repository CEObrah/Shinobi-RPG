"""Environment-built ASGI application used by Uvicorn/Railway."""

from .campaign_entrypoint import create_app_from_env


app = create_app_from_env()
