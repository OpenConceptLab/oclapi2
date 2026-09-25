"""
Launch guardrails for bulk import (OpenConceptLab/ocl_online#230).

Limits come from the caller's groups, like every other capability:
- `imports.file_size_kb` caps one import (0 = unlimited, -1 = blocked; a user with no row gets the
  `preview` value, see UserProfile.get_capability_limit).
- `users.bulk_import_advanced`: zip files, FHIR/NPM packages, OCL repository exports, imports from a URL.
- `users.bulk_import_priority`: the `concurrent` queue and more than 5 threads.
Staff and superusers are never limited.
"""
import json

import requests
from pydash import get
from rest_framework import status
from rest_framework.exceptions import APIException

from core.capabilities.constants import IMPORTS_FILE_SIZE_CAPABILITY_ID
from core.common.utils import is_zip_file
from core.users.constants import BULK_IMPORT_ADVANCED_PERMISSION, BULK_IMPORT_PRIORITY_PERMISSION

KB = 1024
ENVELOPE_SLACK_MIN_BYTES = 64 * KB  # multipart boundaries, JSON escaping; the data itself is checked exactly
DEFAULT_IMPORT_THREADS = 5
PRIORITY_QUEUES = ('concurrent', )
DOWNLOAD_CHUNK_BYTES = 64 * KB


class ImportLimitError(APIException):
    """
    403 whose body is `detail`. `detail` is set directly (as MapProjectCapacityExceeded does) so that
    `limit`/`requested` stay numbers in the JSON instead of becoming strings.
    """
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, detail):  # pylint: disable=super-init-not-called
        self.detail = detail


def is_unrestricted(user):
    return bool(user and (user.is_staff or user.is_superuser))


def get_max_import_bytes(user):
    """Bytes one import may have; None = unlimited."""
    if not user or is_unrestricted(user):
        return None
    limit = user.get_capability_limit(IMPORTS_FILE_SIZE_CAPABILITY_ID)
    return limit * KB if limit and limit > 0 else None


def check_import_size(user, size_bytes):
    if not user or is_unrestricted(user):
        return
    limit = user.get_capability_limit(IMPORTS_FILE_SIZE_CAPABILITY_ID)
    requested = -(-int(size_bytes or 0) // KB)  # KB, rounded up
    if limit is None or limit < 0:
        raise ImportLimitError({
            'detail': 'You do not have access to bulk import.',
            'error_code': 'imports_file_size_not_entitled', 'limit': None, 'requested': requested,
        })
    if limit and requested > limit:
        raise ImportLimitError({
            'detail': f'This import is {requested:,} KB. Your limit is {limit:,} KB per import.',
            'error_code': 'imports_file_size_limit_reached', 'limit': limit, 'requested': requested,
        })


def check_advanced_import(user, feature):
    if is_unrestricted(user) or user.has_perm(BULK_IMPORT_ADVANCED_PERMISSION):
        return
    raise ImportLimitError({
        'detail': 'Your plan can bulk import JSON, JSON lines or CSV files. Zip files, FHIR/NPM packages, '
                  'OCL repository exports and imports from a URL need more access.',
        'error_code': 'imports_advanced_not_entitled', 'feature': feature,
    })


def check_request_body_size(request):
    """
    Before the body is parsed: refuse a body far over the limit. Multipart boundaries and JSON escaping make the
    body bigger than the data it carries, so this allows slack; check_import_payload checks the data exactly.
    """
    user = request.user
    max_bytes = get_max_import_bytes(user)
    content_length = str(request.META.get('CONTENT_LENGTH') or '')
    if not max_bytes or not content_length.isdigit():
        return
    if int(content_length) > max_bytes + max(ENVELOPE_SLACK_MIN_BYTES, max_bytes // 4):
        check_import_size(user, int(content_length))


def check_import_payload(request):
    """After parsing: the exact upload / inline data size, and features that need `bulk_import_advanced`."""
    user = request.user
    data = request.data
    if not isinstance(data, dict):
        return
    if 'import_type' in data:
        check_advanced_import(user, 'import_type')
    if data.get('file_url'):
        check_advanced_import(user, 'file_url')
    upload = data.get('file')
    if upload is not None and not isinstance(upload, str):
        if is_zip_file(name=get(upload, 'name')):
            check_advanced_import(user, 'zip')
        check_import_size(user, get(upload, 'size') or 0)
    text = data.get('data')
    if isinstance(text, str):
        check_import_size(user, len(text.encode('utf-8')))
    elif text is not None:
        check_import_size(user, len(json.dumps(text).encode('utf-8')))


def enforce_import_request_limits(request):
    check_request_body_size(request)
    check_import_payload(request)


def has_import_priority(user):
    return is_unrestricted(user) or user.has_perm(BULK_IMPORT_PRIORITY_PERMISSION)


def sanitize_import_queue(user, import_queue):
    if import_queue in PRIORITY_QUEUES and not has_import_priority(user):
        return None
    return import_queue


def sanitize_import_threads(user, threads):
    try:
        threads = int(threads or DEFAULT_IMPORT_THREADS)
    except (TypeError, ValueError):
        threads = DEFAULT_IMPORT_THREADS
    if has_import_priority(user):
        return threads
    return max(1, min(threads, DEFAULT_IMPORT_THREADS))


def download_import_file(url, user=None, timeout=30):
    """
    Streams `url`, refusing as soon as it passes the user's import size limit.
    Returns (response, content bytes); content is None when the response isn't OK.
    """
    headers = {'User-Agent': 'OCL'}  # user-agent required by mod_security on some servers
    response = requests.get(url, headers=headers, stream=True, timeout=timeout)
    if not response.ok:
        return response, None
    max_bytes = get_max_import_bytes(user)
    declared = str(response.headers.get('Content-Length') or '')
    if max_bytes and declared.isdigit() and int(declared) > max_bytes:
        response.close()
        check_import_size(user, int(declared))
    chunks, total = [], 0
    for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
        total += len(chunk)
        if max_bytes and total > max_bytes:
            response.close()
            check_import_size(user, total)
        chunks.append(chunk)
    return response, b''.join(chunks)
