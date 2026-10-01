from django.core.exceptions import ValidationError
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.capacity.config import get_current, get_defaults, save_config, diff
from core.capacity.constants import SOURCE_API
from core.capacity.limiter import get_status
from core.capacity.models import CapacityConfig
from core.common.utils import to_int

CONFIG_BODY = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description='Capacity-limit settings, e.g. {"mode": "shadow", "tiers": {"preview": 1}}, plus an optional "note" '
                'recorded with the change.'
)


def version_data(row):
    if row is None:
        return {'version': None, 'created_by': None, 'created_at': None, 'source': None, 'note': None}
    return {
        'version': row.id,
        'created_by': row.created_by.username if row.created_by else None,
        'created_at': row.created_at,
        'source': row.source,
        'note': row.note or None,
    }


class CapacityConfigView(APIView):
    """
    The capacity limit on heavy calls (semantic $match, $rerank), for staff. GET returns the config in force, PATCH
    merges changes into it, and PUT replaces it (anything left out takes its default). A change applies across the
    API within CAPACITY_CONFIG_CACHE_SECONDS, with no deploy or restart, and is kept in the history.
    """
    permission_classes = (IsAdminUser,)

    @swagger_auto_schema(operation_summary='The capacity-limit config in force (staff)')
    def get(self, _):
        row, config = get_current()
        return Response({**version_data(row), 'config': config, 'defaults': get_defaults()})

    @swagger_auto_schema(operation_summary='Change the capacity-limit config (staff)', request_body=CONFIG_BODY)
    def patch(self, request):
        return self.save(request, replace=False)

    @swagger_auto_schema(operation_summary='Replace the capacity-limit config (staff)', request_body=CONFIG_BODY)
    def put(self, request):
        return self.save(request, replace=True)

    @staticmethod
    def save(request, replace):
        if not isinstance(request.data, dict):
            return Response({'detail': 'Send a JSON object.'}, status=status.HTTP_400_BAD_REQUEST)
        changes = dict(request.data)
        note = changes.pop('note', '')
        if not isinstance(note, str):
            return Response({'detail': '"note" must be a string.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            row, config, changed = save_config(
                changes, user=request.user, source=SOURCE_API, note=note, replace=replace)
        except ValidationError as ex:
            return Response({'detail': ex.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response({**version_data(row), 'config': config, 'changes': changed})


class CapacityConfigHistoryView(APIView):
    """Every change to the capacity-limit config, newest first: who, when, and each value's old and new setting."""
    permission_classes = (IsAdminUser,)

    @swagger_auto_schema(
        operation_summary='Changes to the capacity-limit config (staff)',
        manual_parameters=[openapi.Parameter('limit', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, default=50)]
    )
    def get(self, request):
        limit = min(max(to_int(request.query_params.get('limit'), 50), 1), 500)
        rows = CapacityConfig.objects.select_related('created_by').order_by('-id')[:limit]
        return Response(
            [{**version_data(row), 'changes': diff(row.previous_config, row.config)} for row in rows])


class CapacityStatusView(APIView):
    """Heavy calls in flight now, in each lane, against its limit (staff)."""
    permission_classes = (IsAdminUser,)

    @swagger_auto_schema(operation_summary='Heavy calls in flight now (staff)')
    def get(self, _):
        _, config = get_current()
        return Response(get_status(config))
