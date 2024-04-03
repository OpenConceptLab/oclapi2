from django.urls import path

from . import views

urlpatterns = [
    path('', views.TaskListView.as_view(), name='task-list'),
    path('<str:task_id>/', views.TaskView.as_view(), name='task-details'),
]
