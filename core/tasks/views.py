from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.utils import flower_get
from core.tasks.serializers import FlowerTaskSerializer


class TaskView(APIView):
    permission_classes = (IsAuthenticated, )

    @staticmethod
    @swagger_auto_schema(responses={200: FlowerTaskSerializer()})
    def get(_, task_id):
        try:
            res = flower_get(f'api/task/info/{task_id}')
        except Exception as ex:  # pylint: disable=broad-except
            return Response(f"{str(ex)}", status=status.HTTP_400_BAD_REQUEST)
        if res.status_code != 200:
            return Response(status=res.status_code)
        return Response(res.json())
