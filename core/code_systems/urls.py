from django.urls import path

from . import views

urlpatterns = [
    path('', views.CodeSystemListView.as_view(), name='code-system-list'),
    path('$lookup/', views.CodeSystemListLookupView.as_view(), name='code-system-list-lookup'),
    path('$validate-code/', views.CodeSystemValidateCodeView.as_view(), name='code-system-validate-code'),
    path("<str:source>/", views.CodeSystemRetrieveUpdateView.as_view(), name='code-system-detail')
]
