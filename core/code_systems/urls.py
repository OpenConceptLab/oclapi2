from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.CodeSystemListView.as_view(), name='code-system-list'),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/$",
        views.CodeSystemRetrieveUpdateView.as_view(),
        name='code-system-detail'
    )
]
