from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(
        r"^(?P<mapping>{pattern})/$".format(pattern=NAMESPACE_PATTERN),
        views.MappingRetrieveUpdateDestroyView.as_view(),
        name='mapping-detail'
    ),
]
