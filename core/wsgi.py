"""
WSGI config for core project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/howto/deployment/wsgi/
"""

import codecs
import os

from django.core.exceptions import BadRequest, SuspiciousOperation
from django.core.handlers.wsgi import WSGIRequest
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')


class FhirWSGIRequest(WSGIRequest):
    """Let FHIR middleware answer malformed Content-Type parameters rejected during request construction."""

    def _set_content_type_params(self, meta):
        """Preserve the invalid raw header and mark FHIR requests for a safe JSON 400 response."""
        from core.fhir.utils import is_fhir_path

        try:
            super()._set_content_type_params(meta)
        except (LookupError, ValueError, BadRequest, SuspiciousOperation):
            if not is_fhir_path(self.path):
                raise
            self._mark_bad_content_type()
            return
        charset = self.content_params.get('charset')
        # Django accepts any codec as the charset, but decoding the query with a non-text one (base64) fails later.
        if charset and is_fhir_path(self.path) and not self._is_text_encoding(charset):
            self._mark_bad_content_type()

    @staticmethod
    def _is_text_encoding(charset):
        try:
            return codecs.lookup(charset)._is_text_encoding  # pylint: disable=protected-access
        except LookupError:
            return True  # Django ignores an unknown charset name, so the query decodes with the default

    def _mark_bad_content_type(self):
        self.fhir_content_type_error = True
        self.content_type = ''
        self.content_params = {}
        # Bypass the encoding setter, which can parse the rejected query again.
        self._encoding = None

application = get_wsgi_application()
application.request_class = FhirWSGIRequest
