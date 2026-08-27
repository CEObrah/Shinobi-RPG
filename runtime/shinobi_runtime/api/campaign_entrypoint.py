"""Production ASGI bootstrap for the single Jianghu campaign."""
def create_app_from_env():
    from shinobi_runtime.api.app import create_app_from_env as factory
    return factory()
__all__=['create_app_from_env']
