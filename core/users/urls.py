from django.urls import re_path, path

from . import views

urlpatterns = [
    re_path(r'^$', views.UserListView.as_view(), name='userprofile-list'),
    re_path(r'^login/$', views.UserLoginView.as_view(), name='user-login'),
    path('<int:user_id>/', views.UserDetailView.as_view(), name='userprofile-detail'),
    path('<int:user_id>/reactivate/$', views.UserReactivateView.as_view(), name='userprofile-reactivate'),
]
