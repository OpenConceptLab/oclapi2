from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.ValueSetListView.as_view(), name='value-set-list'),
    re_path(fr"^(?P<collection>{NAMESPACE_PATTERN})/\$validate-code/$", views.ValueSetValidateCodeView.as_view(),
            name='value-set-validate-code'),
    re_path(r"^\$validate-code/$", views.ValueSetValidateCodeView.as_view(),
            name='value-set-validate-code-global'),
    re_path(fr"^(?P<collection>{NAMESPACE_PATTERN})/\$expand/$", views.ValueSetExpandView.as_view(),
            name='value-set-validate-code'),
    re_path(
        fr"^(?P<collection>{NAMESPACE_PATTERN})/$",
        views.ValueSetRetrieveUpdateView.as_view(),
        name='value-set-detail'
    ),
]
