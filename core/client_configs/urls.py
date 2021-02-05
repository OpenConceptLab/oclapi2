from django.urls import path

from core.client_configs import views

urlpatterns = [
    path('<int:id>/', views.ClientConfigView.as_view(), name='client-config-detail'),
]
