"""
WSGI config for core project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/howto/deployment/wsgi/
"""

import os

from django.core.handlers.wsgi import WSGIRequest
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')


class FhirWSGIRequest(WSGIRequest):
    """Let FHIR middleware answer malformed Content-Type parameters rejected during request construction."""

    def _set_content_type_params(self, meta):
        """Preserve the invalid raw header and mark FHIR requests for a safe JSON 400 response."""
        try:
            super()._set_content_type_params(meta)
        except (LookupError, ValueError):
            from core.fhir.base import is_fhir_path

            if not is_fhir_path(self.path):
                raise
            self.fhir_content_type_error = True
            self.content_type = ''
            self.content_params = {}


application = get_wsgi_application()
application.request_class = FhirWSGIRequest
