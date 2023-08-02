from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.CodeSystemListView.as_view(), name='code-system-list'),
    re_path(r'^\$lookup/$', views.CodeSystemListLookupView.as_view(), name='code-system-list-lookup'),
    re_path(r'^\$validate-code/$', views.CodeSystemValidateCodeView.as_view(),
            name='code-system-validate-code'),
    re_path(
        fr"^(?P<source>{NAMESPACE_PATTERN})/$",
        views.CodeSystemRetrieveUpdateView.as_view(),
        name='code-system-detail'
    )
]
