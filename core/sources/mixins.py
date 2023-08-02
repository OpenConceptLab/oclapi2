from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response


class SummaryMixin:
    def get_object(self, _=None):
        instance = get_object_or_404(self.get_queryset())
        self.check_object_permissions(self.request, instance)
        return instance

    def put(self, request, **kwargs):  # pylint: disable=unused-argument
        instance = self.get_object()
        if instance.has_edit_access(request.user):
            instance.update_children_counts()
            return Response(status=status.HTTP_202_ACCEPTED)
        raise PermissionDenied()
