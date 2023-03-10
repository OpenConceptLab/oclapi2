from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from core.toggles.models import Toggle


class TogglesView(APIView):
    permission_classes = (IsAuthenticatedOrReadOnly,)

    @staticmethod
    def get(_):
        return Response(Toggle.to_dict())
