from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.common.utils import flower_get
from core.tasks.serializers import FlowerTaskSerializer


class AbstractTaskView(APIView):
    permission_classes = (IsAuthenticated, )
    FLOWER_URL = 'api/task/info/{}'

    @swagger_auto_schema(responses={200: FlowerTaskSerializer()})
    def get(self, _, task_id):
        try:
            res = flower_get(self.FLOWER_URL.format(task_id))
        except Exception as ex:  # pylint: disable=broad-except
            return Response(f"{str(ex)}", status=status.HTTP_400_BAD_REQUEST)
        if res.status_code != 200:
            return Response(status=res.status_code)
        return Response(res.json())


# Returns state of task and gist of result if Task is completed
class TaskView(AbstractTaskView):
    FLOWER_URL = 'api/task/info/{}'


# Returns full result
class TaskResultView(AbstractTaskView):
    FLOWER_URL = 'api/task/result/{}'
