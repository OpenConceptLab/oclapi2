from django.urls import path

from . import views

urlpatterns = [
    path('', views.ValueSetListView.as_view(), name='value-set-list'),
    path("<str:collection>/$validate-code/", views.ValueSetValidateCodeView.as_view(), name='value-set-validate-code'),
    path("<str:collection>/$validate-code", views.ValueSetValidateCodeView.as_view(),
         name='value-set-validate-code_no_slash'),
    path("$validate-code/", views.ValueSetValidateCodeView.as_view(), name='value-set-validate-code-global'),
    path("$validate-code", views.ValueSetValidateCodeView.as_view(), name='value-set-validate-code-global_no_slash'),
    path("<str:collection>/$expand/", views.ValueSetExpandView.as_view(), name='value-set-expand'),
    path("<str:collection>/$expand", views.ValueSetExpandView.as_view(), name='value-set-expand_no_slash'),
    path("<str:collection>/", views.ValueSetRetrieveUpdateView.as_view(), name='value-set-detail'),
    path("<str:collection>", views.ValueSetRetrieveUpdateView.as_view(), name='value-set-detail_no_slash'),
]
