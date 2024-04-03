from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.CapabilityStatementView.as_view(), name='capability-statement'),
    re_path(r'^metadata$', views.CapabilityStatementView.as_view(), name='capability-statement'),
]
