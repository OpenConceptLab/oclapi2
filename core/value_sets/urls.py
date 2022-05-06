from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.ValueSetListView.as_view(), name='value-set-list'),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/$",
        views.ValueSetRetrieveUpdateView.as_view(),
        name='value-set-detail'
    )
]
