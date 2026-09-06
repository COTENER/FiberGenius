from django.apps import AppConfig


class MapasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mapas'

    def ready(self):
        from . import signals  # noqa: F401
