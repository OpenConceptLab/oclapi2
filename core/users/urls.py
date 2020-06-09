from django.urls import re_path

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.UserListView.as_view(), name='userprofile-list'),
    re_path(r'^login/$', views.UserLoginView.as_view(), name='user-login'),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/$',
        views.UserDetailView.as_view(),
        name='userprofile-detail'
    ),
    re_path(
        r'^(?P<user>' + NAMESPACE_PATTERN + ')/reactivate/$',
        views.UserReactivateView.as_view(),
        name='userprofile-reactivate'
    ),
]
