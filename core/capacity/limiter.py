"""
The capacity limit on heavy calls (OpenConceptLab/ocl_online#275): semantic `$match` (kNN searches and/or the
in-request rerank) and `$rerank`. It caps how many run at once across all users, to protect the API workers and
Elasticsearch. It isn't a quota: it charges nothing, and a call it refuses (in enforce mode only) is asked to retry
shortly.

Each lane is a Redis sorted set of leases: member = a random token per call, score = the lease's expiry in ms by
Redis's own clock, so the API tasks' clocks don't matter. A call takes a lease in every lane that applies to it, in
one atomic script, renews them from a background thread while it runs, and releases them when it finishes. A lease
that isn't renewed expires, so a worker that dies frees its slots within `lease_seconds`.

If Redis can't be reached, calls go ahead uncounted and it's logged (fail open): the limiter must never cause an
outage. It uses its own Redis client with short timeouts and no retries, and waits at most
CAPACITY_REDIS_DEADLINE_SECONDS for any Redis operation, DNS and Sentinel discovery included. After an error, each
process skips Redis (acquire, renewal and release) for CAPACITY_REDIS_RETRY_SECONDS, and leases it can't release
expire. So an outage delays at most one call per process in that time, by at most the deadline.
"""
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager

import redis
from cid.locals import get_cid
from django.conf import settings
from redis.backoff import NoBackoff
from redis.retry import Retry
from redis.sentinel import Sentinel
from rest_framework import status
from rest_framework.response import Response

from core.capacity.config import get_config
from core.capacity.constants import (
    TIERS, MODE_OFF, MODE_ENFORCE, ENFORCE_FOR_ALL, CAPACITY_AWARE_METADATA_KEY, TIER_STAFF, TIER_CORE,
    TIER_EARLY_ACCESS, TIER_PREVIEW, LANE_API_HEAVY, LANE_API_HEAVY_TASK, LANE_ES2_KNN, LANE_TIER, LANE_USER,
    DECISION_ADMITTED, DECISION_SHADOW_REFUSED, DECISION_REFUSED, DECISION_UNAVAILABLE, ENDPOINT_MATCH,
    HEADER_DECISION, HEADER_LIMIT, HEADER_IN_FLIGHT, HEADER_TIER, HEADER_TIER_LIMIT, HEADER_TIER_IN_FLIGHT,
    HEADER_SUGGESTED_CONCURRENCY, CAPACITY_EXCEEDED_ERROR_CODE, LOG_EVENT, REDIS_KEY_PREFIX)
from core.capacity.logs import emit
from core.common.utils import get_event_metadata
from core.users.constants import CORE_USER_GROUP, EARLY_ACCESS_GROUP

_NOW_MS = """
if redis.replicate_commands then redis.replicate_commands() end
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
-- Extend a lane's TTL to cover a new lease, never shorten it: other calls' leases may be longer.
local function keep(key, ms)
  if redis.call('PTTL', key) < ms then redis.call('PEXPIRE', key, ms) end
end
"""

# KEYS: one per lane. ARGV: token, lease ms, force ('1': take the lease even when a lane is full), then one limit
# per key. Returns {1 if the lease was taken else 0, then each lane's count before this call}.
ACQUIRE_SCRIPT = _NOW_MS + """
local lease_ms = tonumber(ARGV[2])
local result = {0}
local full = false
for i, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms)
  local count = redis.call('ZCARD', key)
  result[i + 1] = count
  if count >= tonumber(ARGV[3 + i]) then full = true end
end
if ARGV[3] == '1' or not full then
  result[1] = 1
  for _, key in ipairs(KEYS) do
    redis.call('ZADD', key, now_ms + lease_ms, ARGV[1])
    keep(key, lease_ms * 2)
  end
end
return result
"""

# KEYS: the call's lanes. ARGV: token, lease ms. Renews the call's leases only if it still holds every one of them
# unexpired, so a lease that expired is never revived and a call is never counted in only some lanes. Returns how
# many it still held.
RENEW_SCRIPT = _NOW_MS + """
local lease_ms = tonumber(ARGV[2])
local held = 0
for _, key in ipairs(KEYS) do
  local expiry = redis.call('ZSCORE', key, ARGV[1])
  if expiry and tonumber(expiry) > now_ms then held = held + 1 end
end
if held == #KEYS then
  for _, key in ipairs(KEYS) do
    redis.call('ZADD', key, now_ms + lease_ms, ARGV[1])
    keep(key, lease_ms * 2)
  end
end
return held
"""

# KEYS: lanes. Returns each lane's count of unexpired leases.
COUNT_SCRIPT = _NOW_MS + """
local counts = {}
for i, key in ipairs(KEYS) do
  counts[i] = redis.call('ZCOUNT', key, '(' .. now_ms, '+inf')
end
return counts
"""


def lane_key(lane, *parts):
    return ':'.join([REDIS_KEY_PREFIX, lane, *[str(part) for part in parts]])


def get_task_id():
    """Names this API task's lane. The API tasks run on separate hosts, so the hostname tells them apart."""
    return settings.CAPACITY_TASK_ID or socket.gethostname()


def build_redis_client():
    timeout = settings.CAPACITY_REDIS_TIMEOUT_SECONDS
    options = {
        'socket_timeout': timeout, 'socket_connect_timeout': timeout, 'retry': Retry(NoBackoff(), 0),
        'retry_on_timeout': False, 'health_check_interval': 0,
    }
    if settings.REDIS_SENTINELS:
        sentinel_options = {
            'socket_timeout': timeout, 'socket_connect_timeout': timeout, 'retry': Retry(NoBackoff(), 0)}
        if settings.REDIS_PASSWORD:
            sentinel_options['password'] = settings.REDIS_PASSWORD
        sentinel = Sentinel(settings.REDIS_SENTINELS_LIST, sentinel_kwargs=sentinel_options)
        return sentinel.master_for(
            settings.REDIS_SENTINELS_MASTER, db=settings.REDIS_DB, password=settings.REDIS_PASSWORD, **options)
    return redis.Redis(
        host=settings.REDIS_HOST, port=int(settings.REDIS_PORT), db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD, **options)


class RedisLanes:
    """The lanes in Redis, with a per-process switch that skips Redis for a while after it fails."""
    client = None
    scripts = {}
    unavailable_until = 0.0
    lock = threading.Lock()
    executor = None
    executor_pid = None

    @classmethod
    def use_client(cls, client):
        with cls.lock:
            cls.client = client
            cls.scripts = {}
            cls.unavailable_until = 0.0
            if cls.executor is not None:
                cls.executor.shutdown(wait=False, cancel_futures=True)
            cls.executor = None

    @classmethod
    def get_executor(cls):
        if cls.executor is None or cls.executor_pid != os.getpid():
            with cls.lock:
                if cls.executor is None or cls.executor_pid != os.getpid():
                    cls.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ocl-capacity-redis')
                    cls.executor_pid = os.getpid()
        return cls.executor

    @classmethod
    def call(cls, operation, *args):
        """
        Run a Redis operation on this process's Redis thread, and wait at most CAPACITY_REDIS_DEADLINE_SECONDS for
        it in all: socket timeouts don't bound DNS or a walk through the Sentinels. An operation still queued when
        its caller gives up is skipped. One already running finishes, so an acquire that lands late holds a lease
        nobody renews, which expires.
        """
        abandoned = threading.Event()

        def run():
            return None if abandoned.is_set() else operation(*args)

        future = cls.get_executor().submit(run)
        try:
            return future.result(timeout=settings.CAPACITY_REDIS_DEADLINE_SECONDS)
        except FutureTimeoutError as ex:
            abandoned.set()
            raise redis.TimeoutError(
                f'no answer from Redis within {settings.CAPACITY_REDIS_DEADLINE_SECONDS} s') from ex

    @classmethod
    def get_client(cls):
        if cls.client is None:
            with cls.lock:
                if cls.client is None:
                    cls.client = build_redis_client()
                    cls.scripts = {}
        return cls.client

    @classmethod
    def run_script(cls, source, keys, args=()):
        client = cls.get_client()
        script = cls.scripts.get(source)
        if script is None or script.registered_client is not client:
            script = cls.scripts[source] = client.register_script(source)
        return script(keys=keys, args=args)

    @classmethod
    def is_available(cls):
        return time.monotonic() >= cls.unavailable_until

    @classmethod
    def mark_unavailable(cls):
        cls.unavailable_until = time.monotonic() + settings.CAPACITY_REDIS_RETRY_SECONDS

    @classmethod
    def acquire(cls, keys, limits, token, lease_ms, force):  # pylint: disable=too-many-arguments
        """(whether the lease was taken, each lane's count before this call)"""
        result = cls.call(cls.run_script, ACQUIRE_SCRIPT, keys, [token, lease_ms, '1' if force else '0', *limits])
        return bool(result[0]), [int(count) for count in result[1:]]

    @classmethod
    def renew(cls, keys, token, lease_ms):
        """How many of its leases the call still held; they're renewed only if that's all of them."""
        return int(cls.call(cls.run_script, RENEW_SCRIPT, keys, [token, lease_ms]))

    @classmethod
    def release(cls, keys, token):
        def remove():
            pipeline = cls.get_client().pipeline(transaction=False)
            for key in keys:
                pipeline.zrem(key, token)
            pipeline.execute()
        cls.call(remove)

    @classmethod
    def count(cls, keys):
        return [int(count) for count in cls.call(cls.run_script, COUNT_SCRIPT, keys)] if keys else []

    @classmethod
    def find_keys(cls, lane):
        def scan():
            return sorted(key.decode() if isinstance(key, bytes) else key
                          for key in cls.get_client().scan_iter(match=lane_key(lane, '*'), count=100))
        return cls.call(scan)


def get_tier(user):
    """The user's highest plan tier, as capabilities resolve it: staff > core > early_access > preview."""
    if user.is_staff or user.is_superuser:
        return TIER_STAFF
    groups = set(user.groups.values_list('name', flat=True))
    if CORE_USER_GROUP in groups:
        return TIER_CORE
    if EARLY_ACCESS_GROUP in groups:
        return TIER_EARLY_ACCESS
    return TIER_PREVIEW


def get_queue_ms(request, now=None):
    """
    How long the request waited before the view started, when an AWS load balancer stamped it. The load balancer
    adds X-Amzn-Trace-Id with the time it received the request, in whole seconds (in `Self`, or in `Root` when it
    started the trace), so this reads up to a second high. Mostly it's time spent queued for a free API worker.
    """
    fields = {key.strip(): value.strip() for key, value in (
        part.split('=', 1) for part in (request.META.get('HTTP_X_AMZN_TRACE_ID') or '').split(';') if '=' in part)}
    try:
        received = int((fields.get('Self') or fields.get('Root')).split('-')[1], 16)
    except (AttributeError, IndexError, ValueError):
        return None
    waited = (now or time.time()) - received
    return max(int(waited * 1000), 0) if -2 < waited < 3600 else None


class LeaseRenewer(threading.Thread):
    """Renews a call's leases every `renew_seconds` until it's stopped, or for `max_hold_seconds` at most."""
    def __init__(self, gate):
        super().__init__(name='ocl-capacity-lease', daemon=True)
        self.gate = gate
        self.interval = gate.config['renew_seconds']
        self.deadline = time.monotonic() + gate.config['max_hold_seconds']
        self.finished = threading.Event()

    def run(self):
        while not self.finished.wait(self.interval) and time.monotonic() < self.deadline:
            self.gate.renew()
            if self.gate.lease_lost:
                return  # its leases expired: renewing the rest would count the call in only some lanes

    def stop(self):
        # No need to wait out a renewal in flight: renewing only extends leases the call still holds, so one that
        # lands after the release changes nothing.
        self.finished.set()
        self.join(timeout=0.1)


class CapacityGate:
    """
    Admission for one heavy call. `acquire()` takes a lease in each lane that applies, `release()` gives it back and
    writes the call's log line. In shadow mode (and in enforce mode for clients it doesn't enforce for), a call that
    finds a lane full still goes ahead, and the line says it would have been refused. Never raises.
    """
    def __init__(self, request, endpoint, rows=0, semantic=False, reranker=False):  # pylint: disable=too-many-arguments
        self.request = request
        self.endpoint = endpoint
        self.rows = rows
        self.semantic = semantic
        self.reranker = reranker
        self.single_row = endpoint == ENDPOINT_MATCH and rows == 1
        self.token = uuid.uuid4().hex
        self.config = None
        self.tier = None
        self.lanes = []  # [(lane, Redis key, limit)]
        self.counts = {}  # lane: calls in flight, counting this one if it holds a lease
        self.full = []  # lanes that were already at their limit
        self.decision = None  # None: the limit is off, or the call isn't gated
        self.enforced = False
        self.holding = False
        self.retry_after = None
        self.queue_ms = None
        self.error = None
        self.lease_lost = False
        self.renewer = None
        self.started_at = None
        self.released_at = None

    @property
    def refused(self):
        return self.decision == DECISION_REFUSED

    @property
    def keys(self):
        return [key for _, key, _ in self.lanes]

    @property
    def limits(self):
        return {lane: limit for lane, _, limit in self.lanes}

    @property
    def lease_ms(self):
        return self.config['lease_seconds'] * 1000

    @property
    def metadata(self):
        return get_event_metadata(self.request)

    def acquire(self):
        self.started_at = time.monotonic()
        try:
            self.config = get_config()
            if self.config['mode'] == MODE_OFF:
                return self
            self.queue_ms = get_queue_ms(self.request)
            self.tier = get_tier(self.request.user)
            self.lanes = self.get_lanes()
            self.enforced = self.is_enforced()
            if not RedisLanes.is_available():
                self.decision, self.error = DECISION_UNAVAILABLE, 'skipped: Redis failed recently'
                return self
            taken, before = RedisLanes.acquire(
                self.keys, [limit for _, _, limit in self.lanes], self.token, self.lease_ms, force=not self.enforced)
            self.holding = taken
            self.full = [lane for (lane, _, limit), count in zip(self.lanes, before) if count >= limit]
            self.counts = {lane: count + int(taken) for (lane, _, _), count in zip(self.lanes, before)}
            if not self.full:
                self.decision = DECISION_ADMITTED
            else:
                self.decision = DECISION_SHADOW_REFUSED if taken else DECISION_REFUSED
                self.retry_after = self.get_retry_after(dict(zip([lane for lane, _, _ in self.lanes], before)))
            if taken:
                renewer = LeaseRenewer(self)
                renewer.start()
                self.renewer = renewer
        except Exception as ex:
            self.record_error(ex)
            if not self.holding or not self.decision:  # never refuse because the limiter itself failed
                self.decision, self.retry_after = DECISION_UNAVAILABLE, None
        return self

    def get_lanes(self):
        config = self.config
        cluster = config['api_heavy']['cluster']
        if not self.single_row:
            cluster = max(cluster - config['reserve_single_row'], 0)
        lanes = [
            (LANE_API_HEAVY, lane_key(LANE_API_HEAVY), cluster),
            (LANE_API_HEAVY_TASK, lane_key(LANE_API_HEAVY_TASK, get_task_id()), config['api_heavy']['per_task']),
        ]
        if self.semantic:
            lanes.append((LANE_ES2_KNN, lane_key(LANE_ES2_KNN), config['es2_knn']))
        lanes.append((LANE_TIER, lane_key(LANE_TIER, self.tier), config['tiers'][self.tier]))
        lanes.append((LANE_USER, lane_key(LANE_USER, self.request.user.id), config['per_user'][self.tier]))
        return lanes

    def is_enforced(self):
        if self.config['mode'] != MODE_ENFORCE:
            return False
        if self.config['enforce_for'] == ENFORCE_FOR_ALL:
            return True
        return str(self.metadata.get(CAPACITY_AWARE_METADATA_KEY)).lower() in ('true', '1')

    def get_retry_after(self, before):
        retry_after, limits = self.config['retry_after'], self.limits
        if any(limits[lane] == 0 for lane in self.full):
            return retry_after['paused']
        ahead = max(before[lane] - limits[lane] + 1 for lane in self.full)
        return min(retry_after['max'], retry_after['base'] * ahead)

    def get_suggested_concurrency(self):
        """How many calls this user should keep in flight right now: 0 while a lane is paused, else at least 1."""
        limits = self.limits
        if not limits:
            return None
        if 0 in limits.values():
            return 0
        if not self.counts:
            return limits[LANE_USER]
        headroom = min(limit - self.counts[lane] for lane, limit in limits.items() if lane != LANE_API_HEAVY_TASK)
        return max(1, min(limits[LANE_USER], self.counts[LANE_USER] + headroom))

    def renew(self):
        try:
            if not RedisLanes.is_available():
                return
            if RedisLanes.renew(self.keys, self.token, self.lease_ms) < len(self.lanes):
                self.lease_lost = True
        except Exception as ex:
            self.record_error(ex)

    def release(self):
        if self.released_at is not None:
            return
        self.released_at = time.monotonic()
        try:
            if self.renewer:
                self.renewer.stop()
        except Exception as ex:
            self.record_error(ex)
        try:
            if self.holding and not RedisLanes.is_available():
                self.error = self.error or 'release skipped: Redis failed recently; the leases will expire'
            elif self.holding:
                RedisLanes.release(self.keys, self.token)
        except Exception as ex:
            self.record_error(ex)
        if self.decision:
            try:
                self.log()
            except Exception:  # a log line must never fail the call
                pass

    def record_error(self, ex):
        if isinstance(ex, redis.RedisError):
            RedisLanes.mark_unavailable()
        self.error = f'{ex.__class__.__name__}: {ex}'[:300]

    def get_refusal_response(self):
        scope = self.full[0]
        return Response(
            {
                'detail': f'Matching is busy. Please retry in {self.retry_after} seconds.',
                'error_code': CAPACITY_EXCEEDED_ERROR_CODE,
                'scope': scope,
                'paused': self.limits[scope] == 0,
                'retry_after': self.retry_after,
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS
        )

    def apply_headers(self, response):
        if not self.decision or response is None:
            return
        try:
            limits, counts = self.limits, self.counts
            headers = {
                HEADER_DECISION: self.decision,
                HEADER_LIMIT: limits.get(LANE_API_HEAVY),
                HEADER_IN_FLIGHT: counts.get(LANE_API_HEAVY),
                HEADER_TIER: self.tier,
                HEADER_TIER_LIMIT: limits.get(LANE_TIER),
                HEADER_TIER_IN_FLIGHT: counts.get(LANE_TIER),
                HEADER_SUGGESTED_CONCURRENCY: self.get_suggested_concurrency(),
            }
            if self.refused:
                headers['Retry-After'] = self.retry_after
            for name, value in headers.items():
                if value is not None:
                    response[name] = str(value)
        except Exception as ex:
            self.record_error(ex)

    def log(self):
        limits, counts, metadata = self.limits, self.counts, self.metadata

        def lane_fields(lane, prefix):
            return {f'{prefix}in_flight': counts.get(lane), f'{prefix}limit': limits.get(lane)}

        emit({
            'event': LOG_EVENT,
            'decision': self.decision,
            'mode': self.config['mode'],
            'enforced': self.enforced,
            'endpoint': self.endpoint,
            'semantic': self.semantic,
            'reranker': self.reranker,
            'rows': self.rows,
            'single_row': self.single_row,
            'tier': self.tier,
            'user_id': self.request.user.id,
            'scope': self.full[0] if self.full else None,
            'full': self.full or None,
            **lane_fields(LANE_API_HEAVY, ''),
            **lane_fields(LANE_API_HEAVY_TASK, 'task_'),
            **lane_fields(LANE_ES2_KNN, 'es2_knn_'),
            **lane_fields(LANE_TIER, 'tier_'),
            **lane_fields(LANE_USER, 'user_'),
            'suggested': self.get_suggested_concurrency(),
            'retry_after': self.retry_after,
            'held_ms': int((self.released_at - self.started_at) * 1000) if self.holding else None,
            'queue_ms': self.queue_ms,
            'lease_lost': self.lease_lost or None,
            'error': self.error,
            'task': get_task_id(),
            'request_source': str(self.request.META.get('HTTP_X_OCL_REQUEST_SOURCE') or '')[:50] or None,
            'algorithm_id': str(metadata.get('algorithm_id') or '')[:100] or None,
            'automatch_run_id': str(metadata.get('automatch_run_id') or '')[:20] or None,
            'cid': get_cid(),
        })


class CapacityLimitMixin:
    """For views with heavy calls: gate a call with `capacity_gate`, and every response gets the capacity headers."""
    capacity_gate_instance = None

    @contextmanager
    def capacity_gate(self, request, **call):
        gate = self.capacity_gate_instance = CapacityGate(request, **call).acquire()
        try:
            yield gate
        finally:
            gate.release()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if self.capacity_gate_instance is not None:
            self.capacity_gate_instance.apply_headers(response)
        return response


def get_status(config=None):
    """What's in flight in each lane now, against its limit. For staff; reads Redis directly."""
    config = config or get_config()
    status_ = {'mode': config['mode'], 'enforce_for': config['enforce_for']}
    try:
        task_keys = RedisLanes.find_keys(LANE_API_HEAVY_TASK)
        user_keys = RedisLanes.find_keys(LANE_USER)
        tier_keys = [lane_key(LANE_TIER, tier) for tier in TIERS]
        keys = [lane_key(LANE_API_HEAVY), lane_key(LANE_ES2_KNN), *tier_keys, *task_keys, *user_keys]
        counts = dict(zip(keys, RedisLanes.count(keys)))
    except Exception as ex:
        status_['redis'] = f'{ex.__class__.__name__}: {ex}'[:300]
        return status_

    def suffix(key):
        return key.rsplit(':', 1)[-1]

    status_.update({
        'redis': 'ok',
        LANE_API_HEAVY: {'in_flight': counts[lane_key(LANE_API_HEAVY)], 'limit': config['api_heavy']['cluster'],
                         'bulk_limit': config['api_heavy']['cluster'] - config['reserve_single_row']},
        LANE_ES2_KNN: {'in_flight': counts[lane_key(LANE_ES2_KNN)], 'limit': config['es2_knn']},
        'tasks': {suffix(key): {'in_flight': counts[key], 'limit': config['api_heavy']['per_task']}
                  for key in task_keys if counts[key]},
        'tiers': {tier: {'in_flight': counts[lane_key(LANE_TIER, tier)], 'limit': config['tiers'][tier]}
                  for tier in TIERS},
        'users': {suffix(key): {'in_flight': counts[key]} for key in user_keys if counts[key]},
    })
    return status_
