from django.urls import path

from .views import TogglesView

app_name = 'core.toggles'
urlpatterns = [
    path('', TogglesView.as_view(), name='toggles'),
]
