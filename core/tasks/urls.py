from django.urls import path

from . import views

urlpatterns = [
    path('<str:task_id>/', views.TaskView.as_view(), name='task-details'),
]
