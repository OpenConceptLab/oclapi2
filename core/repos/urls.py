from django.urls import path

from core.repos import views

urlpatterns = [
    path('', views.ReposListView.as_view(), name='repo-list'),
]
