from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.throttling import ThrottleUtil
from core.toggles.models import Toggle


class TogglesView(APIView):
    permission_classes = (IsAuthenticatedOrReadOnly,)

    def get_throttles(self):
        return ThrottleUtil.get_throttles_by_user_plan(self.request.user)

    @staticmethod
    def get(_):
        return Response(Toggle.to_dict())  # pragma: no cover
