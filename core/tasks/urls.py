from django.urls import path

from . import views

urlpatterns = [
    path('<str:task_id>/result/', views.TaskResultView.as_view(), name='task-result'),
    path('<str:task_id>/', views.TaskView.as_view(), name='task-details'),
]
