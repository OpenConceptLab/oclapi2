from django.urls import re_path, include, path
from rest_framework.authtoken.views import obtain_auth_token

from core.common.constants import NAMESPACE_PATTERN
from . import views

urlpatterns = [
    re_path(r'^$', views.UserListView.as_view(), name='userprofile-list'),
    path('login/', obtain_auth_token, name='api_token_auth'),
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
    re_path(r'^(?P<user>' + NAMESPACE_PATTERN + ')/sources/', include('core.sources.urls')),
]
