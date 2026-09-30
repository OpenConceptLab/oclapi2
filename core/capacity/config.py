"""
The capacity limit's settings: the mode and every number, changed at runtime by staff through
`/capacity/config/` or `manage.py capacity`, with no deploy or restart. Each change adds a CapacityConfig row
(who, when, the old config and the new one) and writes one log line. API processes re-read the newest row at
most every CAPACITY_CONFIG_CACHE_SECONDS, so a change applies across the cluster within that time.
"""
import copy
import logging
import threading
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from core.capacity.constants import (
    MODES, MODE_SHADOW, ENFORCE_FOR, ENFORCE_FOR_AWARE, TIER_STAFF, TIER_CORE, TIER_EARLY_ACCESS,
    TIER_PREVIEW, CONFIG_LOG_EVENT, SOURCE_API)
from core.capacity.logs import emit

logger = logging.getLogger('oclapi')

# The starting numbers (OpenConceptLab/ocl_online#275). Counts are heavy calls in flight at once.
DEFAULTS = {
    'mode': MODE_SHADOW,  # the boot default comes from settings.CAPACITY_LIMIT_MODE
    'enforce_for': ENFORCE_FOR_AWARE,
    'api_heavy': {
        'cluster': 4,  # across all API tasks: half their workers, so the rest stay free for everything else
        'per_task': 2,  # on each API task, since the load balancer doesn't see how busy a task is
    },
    'es2_knn': 3,  # semantic $match calls, which run kNN searches in Elasticsearch
    'reserve_single_row': 1,  # cluster slots only single-row $match calls may use, so interactive calls don't wait
    'tiers': {TIER_STAFF: 4, TIER_CORE: 4, TIER_EARLY_ACCESS: 3, TIER_PREVIEW: 2},  # ceilings, not reservations
    'per_user': {TIER_STAFF: 4, TIER_CORE: 3, TIER_EARLY_ACCESS: 2, TIER_PREVIEW: 1},
    'lease_seconds': 60,  # a call's lease expires this long after its last renewal, so a dead worker's frees
    'renew_seconds': 20,
    'max_hold_seconds': 900,  # stop renewing after this, in case a call never finishes
    'retry_after': {
        'base': 5,  # seconds per call ahead in the fullest lane
        'max': 30,
        'paused': 120,  # when a lane's limit is 0
    },
}
CHOICES = {'mode': MODES, 'enforce_for': ENFORCE_FOR}
MAX_NUMBER = 100000
ADVISORY_LOCK_ID = 275275  # serializes config changes, so a PATCH never merges onto a stale version

_cache = {'config': None, 'expires_at': 0.0}
_cache_lock = threading.Lock()


def get_defaults():
    defaults = copy.deepcopy(DEFAULTS)
    mode = getattr(settings, 'CAPACITY_LIMIT_MODE', None)
    if mode in MODES:
        defaults['mode'] = mode
    return defaults


def merge(base, changes, known_only=False):
    """`base` with `changes` merged in, dict by dict. With known_only, keys `base` doesn't have are dropped."""
    result = copy.deepcopy(base)
    for key, value in (changes or {}).items():
        if known_only and key not in base:
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value, known_only)
        else:
            result[key] = copy.deepcopy(value)
    return result


def diff(old, new, prefix=''):
    """{dotted.path: [old, new]} for every value that differs."""
    changes = {}
    for key in sorted(set(old or {}) | set(new or {})):
        before, after = (old or {}).get(key), (new or {}).get(key)
        path = f'{prefix}{key}'
        if isinstance(before, dict) and isinstance(after, dict):
            changes.update(diff(before, after, f'{path}.'))
        elif before != after:
            changes[path] = [before, after]
    return changes


def _validate_shape(config, template, prefix, errors):
    if not isinstance(config, dict):
        errors.append(f'{prefix.rstrip(".") or "config"} must be an object.')
        return
    for key in config:
        if key not in template:
            errors.append(f'Unknown setting "{prefix}{key}".')
    for key, default in template.items():
        path = f'{prefix}{key}'
        if key not in config:
            errors.append(f'"{path}" is required.')
        elif isinstance(default, dict):
            _validate_shape(config[key], default, f'{path}.', errors)
        elif key in CHOICES:
            if config[key] not in CHOICES[key]:
                errors.append(f'"{path}" must be one of {", ".join(CHOICES[key])}.')
        elif isinstance(config[key], bool) or not isinstance(config[key], int) or not (
                0 <= config[key] <= MAX_NUMBER):
            errors.append(f'"{path}" must be a whole number from 0 to {MAX_NUMBER}.')


def validate(config):
    """Raise ValidationError unless `config` is a complete, consistent config."""
    errors = []
    _validate_shape(config, DEFAULTS, '', errors)
    if not errors:
        if config['reserve_single_row'] > config['api_heavy']['cluster']:
            errors.append('"reserve_single_row" can\'t be more than "api_heavy.cluster".')
        if config['lease_seconds'] < 5:
            errors.append('"lease_seconds" must be at least 5.')
        if not 1 <= config['renew_seconds'] < config['lease_seconds']:
            errors.append('"renew_seconds" must be at least 1 and less than "lease_seconds".')
        if config['max_hold_seconds'] < config['lease_seconds']:
            errors.append('"max_hold_seconds" can\'t be less than "lease_seconds".')
        retry_after = config['retry_after']
        if retry_after['base'] < 1 or retry_after['paused'] < 1 or retry_after['max'] < retry_after['base']:
            errors.append('"retry_after" needs "base" and "paused" of at least 1, and "max" of at least "base".')
    if errors:
        raise ValidationError(errors)
    return config


def resolve(stored):
    """The config a stored version puts in force: the defaults, overlaid with what it sets."""
    config = merge(get_defaults(), stored, known_only=True)
    try:
        return validate(config)
    except ValidationError as ex:
        logger.error('Capacity config is invalid (%s); using the defaults', '; '.join(ex.messages))
        return get_defaults()


def get_current():
    """(the newest CapacityConfig row or None, the config in force), read from the database."""
    from core.capacity.models import CapacityConfig
    latest = CapacityConfig.get_latest()
    return latest, resolve(latest.config if latest else None)


def get_config():
    """The config in force, cached per process for CAPACITY_CONFIG_CACHE_SECONDS. Never raises."""
    now = time.monotonic()
    config = _cache['config']
    if config is not None and now < _cache['expires_at']:
        return config
    with _cache_lock:
        if _cache['config'] is not None and time.monotonic() < _cache['expires_at']:
            return _cache['config']  # another thread just refreshed it
        try:
            _, config = get_current()
        except Exception as ex:
            # Keep the last good config (or the defaults) rather than fail the request.
            logger.warning('Capacity config could not be read (%s); using the last known config', ex)
            config = _cache['config'] or get_defaults()
        _cache['config'] = config
        _cache['expires_at'] = now + settings.CAPACITY_CONFIG_CACHE_SECONDS
    return config


def clear_cache():
    with _cache_lock:
        _cache['config'] = None
        _cache['expires_at'] = 0.0


def save_config(changes, user=None, source=SOURCE_API, note='', replace=False):
    """
    Put a new version in force: `changes` merged onto the config in force, or onto the defaults with replace.
    Raises ValidationError if the result isn't valid. Returns (row, config, {path: [old, new]}); when nothing
    changes, no row is added and row is the current one (or None).
    """
    from core.capacity.models import CapacityConfig
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [ADVISORY_LOCK_ID])
        latest, previous = get_current()
        config = validate(merge(get_defaults() if replace else previous, changes))
        changed = diff(previous, config)
        if not changed:
            return latest, previous, {}
        row = CapacityConfig.objects.create(
            config=config, previous_config=previous, created_by=user, source=source, note=note or '')
    clear_cache()
    emit({
        'event': CONFIG_LOG_EVENT, 'version': row.id, 'changed_by': getattr(user, 'username', None),
        'source': source, 'note': note or None, 'changes': changed, 'mode': config['mode'],
    })
    return row, config, changed
