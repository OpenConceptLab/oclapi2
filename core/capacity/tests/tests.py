import json
import os
import time
from io import StringIO
from unittest.mock import patch, Mock

import fakeredis
import redis
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import RequestFactory, override_settings

from core.capacity.config import (
    get_defaults, validate, merge, diff, resolve, save_config, get_config, clear_cache, _cache)
from core.capacity.constants import (
    HEADERS, HEADER_DECISION, HEADER_LIMIT, HEADER_IN_FLIGHT, HEADER_TIER, HEADER_TIER_LIMIT, HEADER_TIER_IN_FLIGHT,
    HEADER_SUGGESTED_CONCURRENCY, ENDPOINT_MATCH, ENDPOINT_RERANK, DECISION_ADMITTED, DECISION_SHADOW_REFUSED,
    DECISION_REFUSED, DECISION_UNAVAILABLE, LANE_API_HEAVY, LANE_API_HEAVY_TASK, LANE_ES2_KNN, LANE_TIER, LANE_USER,
    LOG_EVENT, CONFIG_LOG_EVENT, SOURCE_COMMAND)
from core.capacity.limiter import (
    RedisLanes, CapacityGate, LeaseRenewer, get_tier, get_queue_ms, lane_key, get_task_id, build_redis_client,
    get_status)
from core.capacity.models import CapacityConfig
from core.common.tests import OCLTestCase, OCLAPITestCase, PREVIEW_GROUP_NAME
from core.common.utils import get_event_metadata
from core.users.constants import CORE_USER_GROUP, EARLY_ACCESS_GROUP
from core.users.tests.factories import UserProfileFactory


def make_user(group=None, **kwargs):
    user = UserProfileFactory(**kwargs)
    if group:
        user.groups.add(Group.objects.get_or_create(name=group)[0])
    return user


class CapacityTestMixin:
    """Lanes in a fake Redis (fakeredis runs the real Lua scripts), a fresh config cache, and captured log lines."""
    def setUp(self):
        super().setUp()
        url = os.environ.get('CAPACITY_TEST_REDIS_URL')  # set it to run these against a real (throwaway) Redis
        self.redis = redis.Redis.from_url(url) if url else fakeredis.FakeRedis(server=fakeredis.FakeServer())
        if url:
            self.redis.flushdb()
        RedisLanes.use_client(self.redis)
        clear_cache()
        self.limiter_emit = patch('core.capacity.limiter.emit').start()
        self.config_emit = patch('core.capacity.config.emit').start()
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.release()
        patch.stopall()
        RedisLanes.use_client(None)
        clear_cache()
        super().tearDown()

    @staticmethod
    def configure(**changes):
        return save_config(changes)

    def acquire(  # pylint: disable=too-many-arguments
            self, user, endpoint=ENDPOINT_MATCH, rows=10, semantic=True, reranker=False, metadata=None, **meta):
        extra = {'HTTP_X_OCL_EVENT_METADATA': json.dumps(metadata)} if metadata else {}
        request = RequestFactory().post('/concepts/$match/', **extra, **meta)
        request.user = user
        gate = CapacityGate(request, endpoint=endpoint, rows=rows, semantic=semantic, reranker=reranker).acquire()
        self.gates.append(gate)
        return gate

    def lines(self):
        return [call.args[0] for call in self.limiter_emit.call_args_list]


class CapacityConfigTest(OCLTestCase):
    def setUp(self):
        super().setUp()
        clear_cache()
        self.emit = patch('core.capacity.config.emit').start()

    def tearDown(self):
        patch.stopall()
        clear_cache()
        super().tearDown()

    def test_defaults_are_the_starting_numbers(self):
        defaults = validate(get_defaults())

        self.assertEqual(defaults['mode'], 'shadow')
        self.assertEqual(defaults['api_heavy'], {'cluster': 4, 'per_task': 2})
        self.assertEqual(defaults['es2_knn'], 3)
        self.assertEqual(defaults['tiers'], {'staff': 4, 'core': 4, 'early_access': 3, 'preview': 2})
        self.assertEqual(defaults['per_user'], {'staff': 4, 'core': 3, 'early_access': 2, 'preview': 1})
        self.assertEqual(defaults['reserve_single_row'], 1)

    def test_default_mode_comes_from_settings(self):
        with override_settings(CAPACITY_LIMIT_MODE='off'):
            self.assertEqual(get_defaults()['mode'], 'off')
        with override_settings(CAPACITY_LIMIT_MODE='loud'):
            self.assertEqual(get_defaults()['mode'], 'shadow')

    def test_validate_rejects_bad_configs(self):
        cases = [
            ({'bogus': 1}, 'Unknown setting "bogus".'),
            ({'tiers': {'gold': 1}}, 'Unknown setting "tiers.gold".'),
            ({'tiers': 5}, 'tiers must be an object.'),
            ({'mode': 'loud'}, '"mode" must be one of off, shadow, enforce.'),
            ({'enforce_for': 'some'}, '"enforce_for" must be one of aware, all.'),
            ({'es2_knn': -1}, '"es2_knn" must be a whole number'),
            ({'es2_knn': True}, '"es2_knn" must be a whole number'),
            ({'es2_knn': '3'}, '"es2_knn" must be a whole number'),
            ({'per_user': {'preview': 100001}}, '"per_user.preview" must be a whole number'),
            ({'reserve_single_row': 5}, '"reserve_single_row" can\'t be more than "api_heavy.cluster".'),
            ({'lease_seconds': 4, 'renew_seconds': 1}, '"lease_seconds" must be at least 5.'),
            ({'renew_seconds': 60}, '"renew_seconds" must be at least 1 and less than "lease_seconds".'),
            ({'renew_seconds': 0}, '"renew_seconds" must be at least 1 and less than "lease_seconds".'),
            ({'max_hold_seconds': 30}, '"max_hold_seconds" can\'t be less than "lease_seconds".'),
            ({'retry_after': {'base': 0}}, '"retry_after" needs'),
            ({'retry_after': {'base': 10, 'max': 5}}, '"retry_after" needs'),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError) as context:
                    validate(merge(get_defaults(), changes))
                self.assertIn(message, ' '.join(context.exception.messages))

        with self.assertRaises(ValidationError) as context:
            validate({})
        self.assertIn('"mode" is required.', context.exception.messages)
        with self.assertRaises(ValidationError):
            validate([])

    def test_merge_and_diff(self):
        base = {'a': 1, 'b': {'c': 2, 'd': 3}}

        self.assertEqual(merge(base, {'b': {'c': 5}, 'e': 6}), {'a': 1, 'b': {'c': 5, 'd': 3}, 'e': 6})
        self.assertEqual(merge(base, {'b': {'c': 5}, 'e': 6}, known_only=True), {'a': 1, 'b': {'c': 5, 'd': 3}})
        self.assertEqual(base, {'a': 1, 'b': {'c': 2, 'd': 3}})
        self.assertEqual(diff(base, {'a': 1, 'b': {'c': 5, 'd': 3}, 'e': 6}), {'b.c': [2, 5], 'e': [None, 6]})
        self.assertEqual(diff(None, {'a': 1}), {'a': [None, 1]})
        self.assertEqual(diff(base, base), {})

    def test_resolve(self):
        self.assertEqual(resolve(None), get_defaults())
        self.assertEqual(resolve({'es2_knn': 5, 'retired_setting': 1})['es2_knn'], 5)
        with self.assertLogs('oclapi', level='ERROR'):
            self.assertEqual(resolve({'es2_knn': -1}), get_defaults())

    def test_save_config_adds_a_version_with_its_history(self):
        user = UserProfileFactory()

        row, config, changes = save_config({'tiers': {'preview': 1}, 'mode': 'off'}, user=user, note='quiet')

        self.assertEqual(changes, {'mode': ['shadow', 'off'], 'tiers.preview': [2, 1]})
        self.assertEqual(config['tiers']['preview'], 1)
        self.assertEqual(row.config, config)
        self.assertEqual(row.previous_config, get_defaults())
        self.assertEqual(row.created_by, user)
        self.assertEqual((row.source, row.note), ('api', 'quiet'))
        self.assertEqual(CapacityConfig.get_latest(), row)
        self.assertEqual(get_config(), config)
        self.emit.assert_called_once_with({
            'event': CONFIG_LOG_EVENT, 'version': row.id, 'changed_by': user.username, 'source': 'api',
            'note': 'quiet', 'changes': changes, 'mode': 'off',
        })

        row2, config2, changes2 = save_config({'es2_knn': 2}, source=SOURCE_COMMAND)
        self.assertEqual(changes2, {'es2_knn': [3, 2]})
        self.assertEqual(row2.previous_config, config)
        self.assertEqual(config2['tiers']['preview'], 1)
        self.assertIsNone(row2.created_by)

        same_row, same_config, no_changes = save_config({'es2_knn': 2})
        self.assertEqual((same_row, same_config, no_changes), (row2, config2, {}))
        self.assertEqual(CapacityConfig.objects.count(), 2)

    def test_save_config_replace_starts_from_the_defaults(self):
        save_config({'tiers': {'preview': 1}, 'es2_knn': 1})

        _, config, changes = save_config({'mode': 'off'}, replace=True)

        self.assertEqual(config, {**get_defaults(), 'mode': 'off'})
        self.assertEqual(changes, {'es2_knn': [1, 3], 'mode': ['shadow', 'off'], 'tiers.preview': [1, 2]})

    def test_save_config_rejects_an_invalid_change(self):
        with self.assertRaises(ValidationError):
            save_config({'es2_knn': -1})
        self.assertFalse(CapacityConfig.objects.exists())
        self.emit.assert_not_called()

    @override_settings(CAPACITY_CONFIG_CACHE_SECONDS=60)
    def test_get_config_is_cached(self):
        self.assertEqual(get_config()['es2_knn'], 3)
        CapacityConfig.objects.create(config={**get_defaults(), 'es2_knn': 1}, source='api')

        self.assertEqual(get_config()['es2_knn'], 3)
        clear_cache()
        self.assertEqual(get_config()['es2_knn'], 1)

    def test_get_config_keeps_the_last_known_config_when_the_database_fails(self):
        with patch('core.capacity.config.get_current', side_effect=Exception('db down')):
            with self.assertLogs('oclapi', level='WARNING'):
                self.assertEqual(get_config(), get_defaults())

        save_config({'es2_knn': 1})
        get_config()
        _cache['expires_at'] = 0.0
        with patch('core.capacity.config.get_current', side_effect=Exception('db down')):
            with self.assertLogs('oclapi', level='WARNING'):
                self.assertEqual(get_config()['es2_knn'], 1)


class CapacityHelpersTest(OCLTestCase):
    def test_get_tier(self):
        self.assertEqual(get_tier(make_user(is_staff=True)), 'staff')
        self.assertEqual(get_tier(make_user(is_superuser=True)), 'staff')
        self.assertEqual(get_tier(make_user(CORE_USER_GROUP)), 'core')
        self.assertEqual(get_tier(make_user(EARLY_ACCESS_GROUP)), 'early_access')
        self.assertEqual(get_tier(make_user(PREVIEW_GROUP_NAME)), 'preview')
        self.assertEqual(get_tier(make_user()), 'preview')
        user = make_user(PREVIEW_GROUP_NAME)
        user.groups.add(Group.objects.get(name=CORE_USER_GROUP))
        self.assertEqual(get_tier(user), 'core')

    def test_get_queue_ms(self):
        def request(trace_id=None):
            return RequestFactory().post('/', **({'HTTP_X_AMZN_TRACE_ID': trace_id} if trace_id else {}))

        received = 1790000000
        root = f'Root=1-{received:x}-abcdef012345678912345678'
        self.assertEqual(get_queue_ms(request(root), now=received + 2.5), 2500)
        self.assertEqual(
            get_queue_ms(request(f'Root=1-{received - 100:x}-abc;Self=1-{received:x}-def'), now=received + 1), 1000)
        self.assertEqual(get_queue_ms(request(root), now=received - 1), 0)
        self.assertIsNone(get_queue_ms(request(root), now=received + 7200))
        self.assertIsNone(get_queue_ms(request(), now=received))
        self.assertIsNone(get_queue_ms(request('Root=1-zz-abc'), now=received))
        self.assertIsNone(get_queue_ms(request('Root=nope'), now=received))
        self.assertIsNone(get_queue_ms(request('garbage'), now=received))

    def test_get_event_metadata(self):
        def request(value=None):
            return RequestFactory().post('/', **({'HTTP_X_OCL_EVENT_METADATA': value} if value else {}))

        self.assertEqual(get_event_metadata(request()), {})
        self.assertEqual(get_event_metadata(request('{nope')), {})
        self.assertEqual(get_event_metadata(request('[1]')), {})
        self.assertEqual(
            get_event_metadata(request('{"algorithm_id": "ocl-semantic"}')), {'algorithm_id': 'ocl-semantic'})

    def test_lane_key_and_task_id(self):
        self.assertEqual(lane_key(LANE_API_HEAVY), 'ocl:capacity:api_heavy')
        self.assertEqual(lane_key(LANE_USER, 42), 'ocl:capacity:user:42')
        with override_settings(CAPACITY_TASK_ID='task-a'):
            self.assertEqual(get_task_id(), 'task-a')
        with override_settings(CAPACITY_TASK_ID=''):
            with patch('core.capacity.limiter.socket.gethostname', return_value='h'):
                self.assertEqual(get_task_id(), 'h')

    @override_settings(REDIS_SENTINELS=None, REDIS_HOST='redis-host', REDIS_PORT='6390', REDIS_PASSWORD=None,
                       CAPACITY_REDIS_TIMEOUT_SECONDS=0.25)
    def test_build_redis_client(self):
        client = build_redis_client()

        kwargs = client.connection_pool.connection_kwargs
        self.assertEqual((kwargs['host'], kwargs['port']), ('redis-host', 6390))
        self.assertEqual((kwargs['socket_timeout'], kwargs['socket_connect_timeout']), (0.25, 0.25))
        self.assertEqual(kwargs['retry']._retries, 0)  # pylint: disable=protected-access

    @override_settings(
        REDIS_SENTINELS='s1:26379;s2:26379', REDIS_SENTINELS_LIST=[('s1', 26379), ('s2', 26379)],
        REDIS_SENTINELS_MASTER='primary', REDIS_PASSWORD='secret', CAPACITY_REDIS_TIMEOUT_SECONDS=0.25)
    def test_build_redis_client_with_sentinels(self):
        with patch('core.capacity.limiter.Sentinel') as sentinel_mock:
            client = build_redis_client()

        self.assertEqual(client, sentinel_mock.return_value.master_for.return_value)
        sentinel_mock.assert_called_once_with(
            [('s1', 26379), ('s2', 26379)],
            sentinel_kwargs={'socket_timeout': 0.25, 'socket_connect_timeout': 0.25, 'password': 'secret'})
        args, kwargs = sentinel_mock.return_value.master_for.call_args
        self.assertEqual(args, ('primary',))
        self.assertEqual((kwargs['password'], kwargs['socket_timeout']), ('secret', 0.25))


class CapacityGateTest(CapacityTestMixin, OCLTestCase):
    def test_admits_and_counts_a_call_in_every_lane(self):
        user = make_user(EARLY_ACCESS_GROUP)

        gate = self.acquire(user)

        self.assertEqual(gate.decision, DECISION_ADMITTED)
        self.assertTrue(gate.holding)
        self.assertEqual(gate.tier, 'early_access')
        self.assertEqual(gate.limits, {
            LANE_API_HEAVY: 3, LANE_API_HEAVY_TASK: 2, LANE_ES2_KNN: 3, LANE_TIER: 3, LANE_USER: 2})
        self.assertEqual(gate.counts, {
            LANE_API_HEAVY: 1, LANE_API_HEAVY_TASK: 1, LANE_ES2_KNN: 1, LANE_TIER: 1, LANE_USER: 1})
        for key in gate.keys:
            self.assertEqual(self.redis.zcard(key), 1)
            self.assertGreater(self.redis.pttl(key), 0)

        gate.release()
        for key in gate.keys:
            self.assertEqual(self.redis.zcard(key), 0)
        gate.release()  # releasing twice is harmless
        self.assertEqual(len(self.lines()), 1)

    def test_shadow_mode_never_refuses_but_says_it_would_have(self):
        user = make_user(PREVIEW_GROUP_NAME)

        first = self.acquire(user)
        second = self.acquire(user)

        self.assertEqual(first.decision, DECISION_ADMITTED)
        self.assertEqual(second.decision, DECISION_SHADOW_REFUSED)
        self.assertTrue(second.holding)
        self.assertEqual(second.full, [LANE_USER])
        self.assertEqual(second.counts[LANE_USER], 2)
        self.assertEqual(second.retry_after, 5)  # 5 s for each call ahead in the fullest lane
        self.assertFalse(second.refused)

    def test_enforce_mode_refuses_without_taking_a_lease(self):
        self.configure(mode='enforce', enforce_for='all')
        user = make_user(PREVIEW_GROUP_NAME)

        first = self.acquire(user)
        second = self.acquire(user)

        self.assertEqual(first.decision, DECISION_ADMITTED)
        self.assertEqual(second.decision, DECISION_REFUSED)
        self.assertTrue(second.refused)
        self.assertFalse(second.holding)
        self.assertIsNone(second.renewer)
        self.assertEqual(second.counts[LANE_USER], 1)
        self.assertEqual(self.redis.zcard(lane_key(LANE_USER, user.id)), 1)
        response = second.get_refusal_response()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data, {
            'detail': 'Matching is busy. Please retry in 5 seconds.', 'error_code': 'capacity_exceeded',
            'scope': LANE_USER, 'paused': False, 'retry_after': 5,
        })

        first.release()
        self.assertEqual(self.acquire(user).decision, DECISION_ADMITTED)

    def test_enforce_for_aware_refuses_only_clients_that_say_they_handle_it(self):
        self.configure(mode='enforce')
        user = make_user(PREVIEW_GROUP_NAME)
        self.acquire(user)

        unaware = self.acquire(user, metadata={'algorithm_id': 'ocl-semantic'})
        aware = self.acquire(user, metadata={'capacity_aware': 'true'})

        self.assertEqual(unaware.decision, DECISION_SHADOW_REFUSED)
        self.assertFalse(unaware.enforced)
        self.assertEqual(aware.decision, DECISION_REFUSED)
        self.assertTrue(aware.enforced)

    def test_a_stale_lease_expires(self):
        user = make_user(PREVIEW_GROUP_NAME)
        # a lease left by a worker that died: its expiry has passed
        self.redis.zadd(lane_key(LANE_USER, user.id), {'dead-worker': 1000})

        gate = self.acquire(user)

        self.assertEqual(gate.decision, DECISION_ADMITTED)
        self.assertEqual(self.redis.zrange(lane_key(LANE_USER, user.id), 0, -1), [gate.token.encode()])

    def test_single_row_calls_can_use_the_reserved_slot(self):
        self.configure(mode='enforce', enforce_for='all', api_heavy={'cluster': 2, 'per_task': 5})
        bulk = self.acquire(make_user(CORE_USER_GROUP))

        second_bulk = self.acquire(make_user(CORE_USER_GROUP))
        single_row = self.acquire(make_user(CORE_USER_GROUP), rows=1)
        rerank = self.acquire(make_user(CORE_USER_GROUP), endpoint=ENDPOINT_RERANK, rows=1, semantic=False)

        self.assertEqual(bulk.decision, DECISION_ADMITTED)
        self.assertEqual(bulk.limits[LANE_API_HEAVY], 1)
        self.assertEqual((second_bulk.decision, second_bulk.full), (DECISION_REFUSED, [LANE_API_HEAVY]))
        self.assertTrue(single_row.single_row)
        self.assertEqual(single_row.limits[LANE_API_HEAVY], 2)
        self.assertEqual(single_row.decision, DECISION_ADMITTED)
        self.assertFalse(rerank.single_row)  # $rerank calls are part of Auto Match runs, never interactive
        self.assertEqual(rerank.decision, DECISION_REFUSED)

    def test_each_api_task_has_its_own_lane(self):
        self.configure(mode='enforce', enforce_for='all', api_heavy={'cluster': 4, 'per_task': 1}, reserve_single_row=0)
        with override_settings(CAPACITY_TASK_ID='task-a'):
            first = self.acquire(make_user(CORE_USER_GROUP))
            second = self.acquire(make_user(CORE_USER_GROUP))
        with override_settings(CAPACITY_TASK_ID='task-b'):
            other_task = self.acquire(make_user(CORE_USER_GROUP))

        self.assertEqual(first.decision, DECISION_ADMITTED)
        self.assertEqual((second.decision, second.full), (DECISION_REFUSED, [LANE_API_HEAVY_TASK]))
        self.assertEqual(other_task.decision, DECISION_ADMITTED)
        self.assertEqual(other_task.counts[LANE_API_HEAVY], 2)

    def test_tier_ceilings_leave_other_tiers_alone(self):
        self.configure(mode='enforce', enforce_for='all', api_heavy={'cluster': 10, 'per_task': 10}, es2_knn=10)
        self.acquire(make_user(PREVIEW_GROUP_NAME))
        self.acquire(make_user(PREVIEW_GROUP_NAME))

        third_preview = self.acquire(make_user(PREVIEW_GROUP_NAME))
        early_access = self.acquire(make_user(EARLY_ACCESS_GROUP))

        self.assertEqual((third_preview.decision, third_preview.full), (DECISION_REFUSED, [LANE_TIER]))
        self.assertEqual(third_preview.counts[LANE_TIER], 2)
        self.assertEqual(early_access.decision, DECISION_ADMITTED)
        self.assertEqual(early_access.counts[LANE_TIER], 1)

    def test_only_semantic_calls_take_an_es2_knn_lane(self):
        self.configure(mode='enforce', enforce_for='all', es2_knn=1)
        semantic = self.acquire(make_user(CORE_USER_GROUP))

        semantic_again = self.acquire(make_user(CORE_USER_GROUP))
        reranked_lexical = self.acquire(make_user(CORE_USER_GROUP), semantic=False, reranker=True)

        self.assertIn(LANE_ES2_KNN, semantic.limits)
        self.assertEqual((semantic_again.decision, semantic_again.full), (DECISION_REFUSED, [LANE_ES2_KNN]))
        self.assertNotIn(LANE_ES2_KNN, reranked_lexical.limits)
        self.assertEqual(reranked_lexical.decision, DECISION_ADMITTED)

    def test_a_paused_tier(self):
        self.configure(mode='enforce', enforce_for='all', tiers={'preview': 0})

        gate = self.acquire(make_user(PREVIEW_GROUP_NAME))

        self.assertEqual((gate.decision, gate.full), (DECISION_REFUSED, [LANE_TIER]))
        self.assertEqual(gate.retry_after, 120)
        self.assertEqual(gate.get_suggested_concurrency(), 0)
        self.assertTrue(gate.get_refusal_response().data['paused'])

    def test_retry_after_grows_with_the_calls_ahead_up_to_the_max(self):
        self.configure(tiers={'core': 10}, per_user={'core': 10}, api_heavy={'cluster': 10, 'per_task': 10})
        user = make_user(CORE_USER_GROUP)
        gates = [self.acquire(user, semantic=False, reranker=True) for _ in range(12)]

        # bulk calls get 9 of the cluster's 10
        self.assertEqual([gate.retry_after for gate in gates[8:]], [None, 5, 10, 15])
        self.configure(retry_after={'base': 5, 'max': 12, 'paused': 120})
        self.assertEqual(self.acquire(user, semantic=False, reranker=True).retry_after, 12)

    def test_suggested_concurrency(self):
        core = make_user(CORE_USER_GROUP)

        first = self.acquire(core)
        self.assertEqual(first.get_suggested_concurrency(), 3)  # alone: up to the per-user limit
        second = self.acquire(make_user(CORE_USER_GROUP))
        self.assertEqual(second.get_suggested_concurrency(), 2)  # its own + the 1 bulk slot left in the cluster
        preview = self.acquire(make_user(PREVIEW_GROUP_NAME))
        self.assertEqual(preview.get_suggested_concurrency(), 1)  # never below 1 unless paused
        self.assertIsNone(CapacityGate(Mock(), ENDPOINT_MATCH).get_suggested_concurrency())

    def test_renewal_extends_the_leases_it_still_holds(self):
        gate = self.acquire(make_user(PREVIEW_GROUP_NAME))
        key = lane_key(LANE_USER, gate.request.user.id)
        self.redis.zadd(key, {gate.token: 1000})

        gate.renew()

        self.assertGreater(self.redis.zscore(key, gate.token), time.time() * 1000)
        self.assertFalse(gate.lease_lost)
        self.redis.delete(key)
        gate.renew()
        self.assertTrue(gate.lease_lost)
        self.assertFalse(self.redis.exists(key))  # a lost lease isn't re-added

    def test_the_renewer_thread_renews_until_stopped(self):
        gate = Mock(config={'renew_seconds': 0.01, 'max_hold_seconds': 60})
        renewer = LeaseRenewer(gate)
        renewer.start()
        time.sleep(0.1)
        renewer.stop()

        self.assertFalse(renewer.is_alive())
        self.assertGreater(gate.renew.call_count, 1)

        gate = Mock(config={'renew_seconds': 0.01, 'max_hold_seconds': 0})
        renewer = LeaseRenewer(gate)
        renewer.start()
        renewer.join(1)
        self.assertFalse(renewer.is_alive())  # gave up at max_hold_seconds
        gate.renew.assert_not_called()

    def test_a_call_starts_a_renewer_and_release_stops_it(self):
        gate = self.acquire(make_user(PREVIEW_GROUP_NAME))

        self.assertTrue(gate.renewer.is_alive())
        gate.release()
        self.assertFalse(gate.renewer.is_alive())

    def test_redis_failure_fails_open_and_skips_redis_for_a_while(self):
        broken = Mock()
        broken.register_script.return_value = Mock(side_effect=redis.ConnectionError('refused'))
        RedisLanes.use_client(broken)

        gate = self.acquire(make_user(PREVIEW_GROUP_NAME))

        self.assertEqual(gate.decision, DECISION_UNAVAILABLE)
        self.assertFalse(gate.holding)
        self.assertEqual(gate.error, 'ConnectionError: refused')
        self.assertFalse(RedisLanes.is_available())

        skipped = self.acquire(make_user(PREVIEW_GROUP_NAME))
        self.assertEqual(skipped.decision, DECISION_UNAVAILABLE)
        self.assertEqual(broken.register_script.return_value.call_count, 1)
        self.assertEqual(skipped.get_suggested_concurrency(), 1)

        RedisLanes.unavailable_until = 0.0
        self.acquire(make_user(PREVIEW_GROUP_NAME))
        self.assertEqual(broken.register_script.return_value.call_count, 2)

    def test_a_failed_release_or_renewal_is_logged_not_raised(self):
        gate = self.acquire(make_user(PREVIEW_GROUP_NAME))
        with patch.object(RedisLanes, 'renew', side_effect=redis.TimeoutError('slow')):
            gate.renew()
        self.assertEqual(gate.error, 'TimeoutError: slow')

        with patch.object(RedisLanes, 'release', side_effect=redis.ConnectionError('gone')):
            gate.release()
        self.assertEqual(self.lines()[-1]['error'], 'ConnectionError: gone')
        self.assertFalse(RedisLanes.is_available())

    def test_an_unexpected_error_fails_open(self):
        with patch('core.capacity.limiter.get_tier', side_effect=KeyError('tier')):
            gate = self.acquire(make_user(PREVIEW_GROUP_NAME))

        self.assertEqual(gate.decision, DECISION_UNAVAILABLE)
        self.assertEqual(gate.error, "KeyError: 'tier'")
        self.assertTrue(RedisLanes.is_available())  # only a Redis error skips Redis
        gate.release()
        self.assertEqual(self.lines()[-1]['decision'], DECISION_UNAVAILABLE)

    def test_off_mode_does_nothing(self):
        self.configure(mode='off')
        with patch.object(RedisLanes, 'acquire') as acquire_mock:
            gate = self.acquire(make_user(PREVIEW_GROUP_NAME))
        response = {}

        gate.apply_headers(response)
        gate.release()

        acquire_mock.assert_not_called()
        self.assertIsNone(gate.decision)
        self.assertEqual(response, {})
        self.assertEqual(self.lines(), [])

    def test_headers(self):
        self.configure(mode='enforce', enforce_for='all')
        user = make_user(PREVIEW_GROUP_NAME)
        admitted, refused = self.acquire(user), self.acquire(user)
        admitted_headers, refused_headers = {}, {}

        admitted.apply_headers(admitted_headers)
        refused.apply_headers(refused_headers)

        self.assertEqual(admitted_headers, {
            HEADER_DECISION: 'admitted', HEADER_LIMIT: '3', HEADER_IN_FLIGHT: '1', HEADER_TIER: 'preview',
            HEADER_TIER_LIMIT: '2', HEADER_TIER_IN_FLIGHT: '1', HEADER_SUGGESTED_CONCURRENCY: '1',
        })
        self.assertEqual(refused_headers[HEADER_DECISION], 'refused')
        self.assertEqual(refused_headers['Retry-After'], '5')
        admitted.apply_headers(None)
        broken = Mock()
        broken.__setitem__ = Mock(side_effect=ValueError('bad header'))
        admitted.apply_headers(broken)
        self.assertEqual(admitted.error, 'ValueError: bad header')

    def test_the_log_line(self):
        user = make_user(PREVIEW_GROUP_NAME)
        self.acquire(user)
        received = int(time.time()) - 3
        gate = self.acquire(
            user, rows=25, reranker=True, metadata={'algorithm_id': 'ocl-semantic', 'automatch_run_id': '77'},
            HTTP_X_OCL_REQUEST_SOURCE='automatch', HTTP_X_AMZN_TRACE_ID=f'Root=1-{received:x}-abc')

        gate.release()

        line = self.lines()[-1]
        self.assertGreaterEqual(line.pop('queue_ms'), 3000)
        self.assertGreaterEqual(line.pop('held_ms'), 0)
        line.pop('cid')
        self.assertEqual(line, {
            'event': LOG_EVENT, 'decision': DECISION_SHADOW_REFUSED, 'mode': 'shadow', 'enforced': False,
            'endpoint': '$match', 'semantic': True, 'reranker': True, 'rows': 25, 'single_row': False,
            'tier': 'preview', 'user_id': user.id, 'scope': LANE_USER, 'full': [LANE_USER],
            'in_flight': 2, 'limit': 3, 'task_in_flight': 2, 'task_limit': 2, 'es2_knn_in_flight': 2,
            'es2_knn_limit': 3, 'tier_in_flight': 2, 'tier_limit': 2, 'user_in_flight': 2, 'user_limit': 1,
            'suggested': 1, 'retry_after': 5, 'lease_lost': None, 'error': None, 'task': get_task_id(),
            'request_source': 'automatch', 'algorithm_id': 'ocl-semantic', 'automatch_run_id': '77',
        })

    def test_a_failing_log_line_never_fails_the_call(self):
        self.limiter_emit.side_effect = OSError('stdout closed')
        gate = self.acquire(make_user(PREVIEW_GROUP_NAME))

        gate.release()

        self.assertEqual(self.limiter_emit.call_count, 1)

    def test_status(self):
        with override_settings(CAPACITY_TASK_ID='task-a'):
            gate = self.acquire(make_user(PREVIEW_GROUP_NAME))
            self.acquire(make_user(CORE_USER_GROUP), semantic=False, reranker=True)

        state = get_status()

        self.assertEqual(state['redis'], 'ok')
        self.assertEqual(state[LANE_API_HEAVY], {'in_flight': 2, 'limit': 4, 'bulk_limit': 3})
        self.assertEqual(state[LANE_ES2_KNN], {'in_flight': 1, 'limit': 3})
        self.assertEqual(state['tasks'], {'task-a': {'in_flight': 2, 'limit': 2}})
        self.assertEqual(state['tiers']['preview'], {'in_flight': 1, 'limit': 2})
        self.assertEqual(state['tiers']['core'], {'in_flight': 1, 'limit': 4})
        self.assertEqual(state['users'][str(gate.request.user.id)], {'in_flight': 1})

        with patch.object(RedisLanes, 'find_keys', side_effect=redis.ConnectionError('down')):
            self.assertEqual(get_status()['redis'], 'ConnectionError: down')


class CapacityViewsTest(CapacityTestMixin, OCLAPITestCase):
    def setUp(self):
        super().setUp()
        self.staff = make_user(is_staff=True)
        self.preview_user = make_user(PREVIEW_GROUP_NAME)

    def post(self, path, data, user=None, **extra):
        return self.client.post(
            path, data, format='json', HTTP_AUTHORIZATION=f'Token {(user or self.preview_user).get_token()}', **extra)

    def match(self, query='?semantic=true', user=None, rows=None, **extra):
        return self.post(
            f'/concepts/$match/{query}',
            {'rows': rows or [{'name': 'a'}, {'name': 'b'}], 'target_repo_url': '/orgs/org/sources/src/'},
            user=user, **extra)

    def test_cors_exposes_the_capacity_headers(self):
        for header in HEADERS:
            self.assertIn(header, settings.CORS_EXPOSE_HEADERS)

    @patch('core.concepts.views.MetadataToConceptsListView.filter_queryset', return_value=[])
    def test_semantic_match_is_counted_and_carries_the_headers(self, filter_queryset_mock):
        response = self.match()

        self.assertEqual(response.status_code, 200)
        filter_queryset_mock.assert_called_once()
        self.assertEqual(response[HEADER_DECISION], 'admitted')
        self.assertEqual(response[HEADER_TIER], 'preview')
        self.assertEqual(response[HEADER_IN_FLIGHT], '1')
        self.assertEqual(self.redis.zcard(lane_key(LANE_API_HEAVY)), 0)  # released
        line = self.lines()[0]
        self.assertEqual((line['endpoint'], line['rows'], line['semantic']), ('$match', 2, True))

    @patch('core.concepts.views.MetadataToConceptsListView.filter_queryset', return_value=[])
    def test_lexical_match_is_not_a_heavy_call(self, _):
        response = self.match(query='')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.has_header(HEADER_DECISION))
        self.assertEqual(self.lines(), [])

    @patch('core.concepts.views.MetadataToConceptsListView.filter_queryset', return_value=[])
    def test_reranked_lexical_match_is_a_heavy_call(self, _):
        response = self.match(query='?reranker=true')

        self.assertEqual(response[HEADER_DECISION], 'admitted')
        self.assertEqual(self.lines()[0]['semantic'], False)
        self.assertIsNone(self.lines()[0]['es2_knn_limit'])

    @patch('core.concepts.views.MetadataToConceptsListView.filter_queryset', return_value=[])
    def test_enforce_refuses_a_match_before_charging_any_quota(self, filter_queryset_mock):
        from core.capabilities.models import UsageEvent
        self.configure(mode='enforce', enforce_for='all', per_user={'preview': 0})

        response = self.match()

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '120')
        self.assertEqual(response[HEADER_DECISION], 'refused')
        self.assertEqual(response.data['error_code'], 'capacity_exceeded')
        filter_queryset_mock.assert_not_called()
        self.assertFalse(UsageEvent.objects.filter(user=self.preview_user).exists())

    @patch('core.concepts.views.MetadataToConceptsListView.filter_queryset', return_value=[])
    def test_shadow_mode_charges_and_matches_as_usual(self, filter_queryset_mock):
        from core.capabilities.models import UsageEvent
        self.configure(per_user={'preview': 0})

        response = self.match()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response[HEADER_DECISION], 'shadow-refused')
        self.assertFalse(response.has_header('Retry-After'))
        filter_queryset_mock.assert_called_once()
        self.assertTrue(UsageEvent.objects.filter(user=self.preview_user, action='match_concepts').exists())

    @patch('core.concepts.views.MetadataToConceptsListView.filter_queryset', side_effect=Exception('es down'))
    def test_a_failed_match_still_releases_its_lease(self, _):
        with self.assertRaises(Exception):
            self.match()

        self.assertEqual(self.redis.zcard(lane_key(LANE_API_HEAVY)), 0)
        self.assertEqual(self.lines()[0]['decision'], 'admitted')

    @patch('core.concepts.views.Reranker')
    def test_rerank_is_counted_and_carries_the_headers(self, reranker_mock):
        reranker_mock.return_value.rerank.return_value = [{'id': 1}]

        response = self.post('/concepts/$rerank/', {'rows': [{'id': 1}, {'id': 2}], 'q': 'text'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response[HEADER_DECISION], 'admitted')
        line = self.lines()[0]
        self.assertEqual((line['endpoint'], line['rows'], line['single_row']), ('$rerank', 2, False))

    @patch('core.concepts.views.Reranker')
    def test_enforce_refuses_a_rerank_before_any_work(self, reranker_mock):
        self.configure(mode='enforce', enforce_for='all', per_user={'preview': 0})

        response = self.post('/concepts/$rerank/', {'rows': [{'id': 1}], 'q': 'text'})

        self.assertEqual(response.status_code, 429)
        reranker_mock.assert_not_called()

    def test_an_invalid_rerank_is_not_counted(self):
        response = self.post('/concepts/$rerank/', {'rows': [{'id': 1}]})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.has_header(HEADER_DECISION))

    def test_config_is_staff_only(self):
        self.assertEqual(self.client.get('/capacity/config/').status_code, 401)
        for path in ['/capacity/config/', '/capacity/config/history/', '/capacity/status/']:
            response = self.client.get(path, HTTP_AUTHORIZATION=f'Token {self.preview_user.get_token()}')
            self.assertEqual(response.status_code, 403)
        response = self.client.patch(
            '/capacity/config/', {'mode': 'off'}, format='json',
            HTTP_AUTHORIZATION=f'Token {self.preview_user.get_token()}')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CapacityConfig.objects.exists())

    def test_get_config(self):
        response = self.client.get('/capacity/config/', HTTP_AUTHORIZATION=f'Token {self.staff.get_token()}')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['version'])
        self.assertEqual(response.data['config'], get_defaults())
        self.assertEqual(response.data['defaults'], get_defaults())

    def test_change_config_and_read_its_history(self):
        auth = {'HTTP_AUTHORIZATION': f'Token {self.staff.get_token()}'}

        response = self.client.patch(
            '/capacity/config/', {'tiers': {'preview': 1}, 'note': 'busy'}, format='json', **auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['changes'], {'tiers.preview': [2, 1]})
        self.assertEqual((response.data['created_by'], response.data['note']), (self.staff.username, 'busy'))
        self.assertEqual(get_config()['tiers']['preview'], 1)

        response = self.client.put('/capacity/config/', {'mode': 'off'}, format='json', **auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['changes'], {'mode': ['shadow', 'off'], 'tiers.preview': [1, 2]})

        response = self.client.get('/capacity/config/history/?limit=1', **auth)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['changes'], {'mode': ['shadow', 'off'], 'tiers.preview': [1, 2]})
        response = self.client.get('/capacity/config/history/', **auth)
        self.assertEqual([change['changes'] for change in response.data][1], {'tiers.preview': [2, 1]})

    def test_change_config_rejects_bad_input(self):
        auth = {'HTTP_AUTHORIZATION': f'Token {self.staff.get_token()}'}

        response = self.client.patch('/capacity/config/', {'es2_knn': -1}, format='json', **auth)
        self.assertEqual(response.status_code, 400)
        self.assertIn('"es2_knn" must be a whole number from 0 to 100000.', response.data['detail'])

        response = self.client.patch('/capacity/config/', {'note': 5}, format='json', **auth)
        self.assertEqual(response.status_code, 400)

        response = self.client.patch('/capacity/config/', [1], format='json', **auth)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CapacityConfig.objects.exists())

    def test_status_view(self):
        self.acquire(self.preview_user)

        response = self.client.get('/capacity/status/', HTTP_AUTHORIZATION=f'Token {self.staff.get_token()}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[LANE_API_HEAVY]['in_flight'], 1)
        self.assertEqual(response.data['mode'], 'shadow')


class CapacityCommandTest(CapacityTestMixin, OCLTestCase):
    def run_command(self, *args):
        out = StringIO()
        call_command('capacity', *args, stdout=out)
        return out.getvalue()

    def test_show_set_reset_and_history(self):
        self.assertEqual(json.loads(self.run_command('show'))['config'], get_defaults())

        output = json.loads(self.run_command('set', 'tiers.preview=1', 'mode=enforce', '--note', 'test'))

        self.assertEqual(output['changes'], {'mode': ['shadow', 'enforce'], 'tiers.preview': [2, 1]})
        latest = CapacityConfig.get_latest()
        self.assertEqual((latest.source, latest.note, latest.created_by), ('command', 'test', None))

        user = make_user(is_staff=True)
        output = json.loads(self.run_command('reset', '--user', user.username))
        self.assertEqual(output['changes'], {'mode': ['enforce', 'shadow'], 'tiers.preview': [1, 2]})
        self.assertEqual(CapacityConfig.get_latest().created_by, user)

        history = self.run_command('history', '--limit', '5')
        self.assertIn('"tiers.preview"', history)
        self.assertIn(user.username, history)

    def test_status(self):
        self.acquire(make_user(PREVIEW_GROUP_NAME))

        self.assertEqual(json.loads(self.run_command('status'))[LANE_API_HEAVY]['in_flight'], 1)

    def test_errors(self):
        for args, message in [
                (['set'], 'Give at least one'),
                (['set', 'mode'], 'Expected <setting>=<value>'),
                (['set', '=1'], 'Expected <setting>=<value>'),
                (['set', 'es2_knn=-1'], '"es2_knn" must be a whole number'),
                (['set', 'mode=off', '--user', 'nobody'], 'No user "nobody".'),
        ]:
            with self.subTest(args=args):
                with self.assertRaises(CommandError) as context:
                    self.run_command(*args)
                self.assertIn(message, str(context.exception))
        self.assertFalse(CapacityConfig.objects.exists())
