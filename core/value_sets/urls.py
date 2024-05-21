from django.urls import path

from . import views

urlpatterns = [
    path('', views.ValueSetListView.as_view(), name='value-set-list'),
    path("<str:collection>/$validate-code/", views.ValueSetValidateCodeView.as_view(), name='value-set-validate-code'),
    path("$validate-code/", views.ValueSetValidateCodeView.as_view(), name='value-set-validate-code-global'),
    path("<str:collection>/$expand/", views.ValueSetExpandView.as_view(), name='value-set-validate-code'),
    path("<str:collection>/", views.ValueSetRetrieveUpdateView.as_view(), name='value-set-detail'),
]
