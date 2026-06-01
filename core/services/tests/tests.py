from django.test import RequestFactory

from core.common.tests import OCLTestCase
from core.services.analytics_event_emitter import AnalyticsEventEmitter


class AnalyticsEventEmitterTest(OCLTestCase):
    def test_attribution_headers_are_forwarded(self):
        # The #105 attribution headers must survive the request-header allow-list,
        # since the emitter forwards only allow-listed headers into the
        # api-transactions payload it POSTs to ocl-analytics-api. If they were
        # dropped, the analytics-api parser (ocl_online#111) would receive nothing
        # and every oclapi2-served row would stay request_source='unknown'/{}.
        request = RequestFactory().get(
            '/concepts/',
            HTTP_X_OCL_REQUEST_SOURCE='automatch',
            HTTP_X_OCL_EVENT_METADATA='{"automatch_run_id":"4827","row_index":"141"}',
            HTTP_X_OCL_REQUEST_IDEMPOTENCY_KEY='a7c3f2b1',
            HTTP_X_OCL_PROMPT_TEMPLATE_KEY='match-recommend-normalized-icd11',
            HTTP_X_OCL_PROMPT_TEMPLATE_VERSION='3',
            HTTP_X_NOT_ALLOWLISTED='dropped',
        )
        emitter = AnalyticsEventEmitter(request, response=None, duration_ms=0)
        headers = emitter._safe_request_headers()  # pylint: disable=protected-access

        # Forwarded verbatim under the raw WSGI key, matching the existing
        # forwarded headers (e.g. HTTP_USER_AGENT) — this is what #111 parses.
        self.assertEqual(headers.get('HTTP_X_OCL_REQUEST_SOURCE'), 'automatch')
        self.assertEqual(
            headers.get('HTTP_X_OCL_EVENT_METADATA'), '{"automatch_run_id":"4827","row_index":"141"}')
        self.assertEqual(headers.get('HTTP_X_OCL_REQUEST_IDEMPOTENCY_KEY'), 'a7c3f2b1')
        self.assertEqual(headers.get('HTTP_X_OCL_PROMPT_TEMPLATE_KEY'), 'match-recommend-normalized-icd11')
        self.assertEqual(headers.get('HTTP_X_OCL_PROMPT_TEMPLATE_VERSION'), '3')
        # The allow-list is still a strict allow-list, not a denylist.
        self.assertNotIn('HTTP_X_NOT_ALLOWLISTED', headers)
