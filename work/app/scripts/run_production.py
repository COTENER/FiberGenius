"""Production entry point for a supervised service behind local IIS.

No service is installed and no database is migrated by this script.
"""
import os
from pathlib import Path
import sys


def main():
    project = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project))
    os.chdir(project)
    module = os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'fibergenius.settings_production')
    if module != 'fibergenius.settings_production':
        raise RuntimeError('This entry point requires fibergenius.settings_production.')
    from django.conf import settings
    from fibergenius.wsgi import application
    from waitress import serve

    serve(
        application,
        host='127.0.0.1', port=8001, threads=8,
        trusted_proxy='127.0.0.1', trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host'},
        clear_untrusted_proxy_headers=True,
        max_request_body_size=settings.DATA_UPLOAD_MAX_MEMORY_SIZE,
    )


if __name__ == '__main__':
    main()
