import logging
import threading

import requests
from django.conf import settings
from pydash import get

logger = logging.getLogger(__name__)


class AnalyticsEventEmitter:  # pragma: no cover
    ANALYTICS_ENDPOINT = settings.ANALYTICS_API + '/api-transactions/'
    TIMEOUT_SECONDS = 0.1  # 100ms hard cap
    SENSITIVE_HEADERS = {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-amz-security-token",
        "proxy-authorization",
    }
    IGNORED_HEADERS = {
        'wsgi.input',
        'wsgi.errors',
        'wsgi.version',
        'wsgi.run_once',
        'wsgi.url_scheme',
        'wsgi.multithread',
        'wsgi.multiprocess',
        'wsgi.file_wrapper',
        'path_info',
        'path',
        'api_base_url',
        'api_internal_base_url',
        'api_superuser_password',
        'app_home',
        'csrf_cookie',
        'db_host',
        'vary',
        'allow'
    }
    ALLOWED_REQUEST_HEADERS = {
        'http_host',
        'http_user_agent',
        'http_origin',
        'http_referer',
        'origin',
        'referer',
        'remote_addr',
        'http_x_forwarded_for',
        'http_x_ocl_client',
        'content_type',
        'content_length',
        'tz',

    }
    REDACTED = "[REDACTED]"
    ANONYMOUS = "Anonymous"

    def __init__(self, request, response, duration_ms):
        self.request = request
        self.response = response
        self.duration_ms = duration_ms

    def emit(self):
        thread = threading.Thread(target=self._send, args=(self._build_payload(), ), daemon=True)
        thread.start()

    # private
    def _build_payload(self):
        request = self.request
        response = self.response

        return {
            "ocl_service": settings.SERVICE_NAME,
            "request": {
                "method": request.method,
                "path": request.get_full_path(),
                "headers": self._safe_request_headers(),
                "client": request.META.get("HTTP_X_OCL_CLIENT")
            },
            "response": {
                "status": response.status_code,
                "headers": self._safe_response_headers(),
            },
            "context": {
                "user_id": self._user_id(),
                "username": self._username(),
                "processing_time_ms": self.duration_ms,
            },
        }

    def _send(self, payload):
        try:
            requests.post(self.ANALYTICS_ENDPOINT, json=payload, timeout=self.TIMEOUT_SECONDS)
        except Exception as exc:
            logger.debug("Analytics emit failed: %s", exc)

    def _user_id(self):
        return get(self.request, 'user.id') or None

    def _username(self):
        return get(self.request, 'user.username') or self.ANONYMOUS

    def _safe_request_headers(self):
        headers = {}
        for key, value in self.request.META.items():
            if key.lower() not in self.ALLOWED_REQUEST_HEADERS:
                continue
            header_name = (key[5:].replace("_", "-").lower())
            headers[key] = self.REDACTED if header_name in self.SENSITIVE_HEADERS else value
        return headers

    def _safe_response_headers(self):
        headers = {}
        for key, value in self.response.items():
            if key.lower() in self.IGNORED_HEADERS:
                continue
            header_name = key.lower()
            headers[header_name] = self.REDACTED if header_name in self.SENSITIVE_HEADERS else value
        return headers
