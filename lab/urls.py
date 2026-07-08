from django.urls import path
from . import views

urlpatterns = [
    path('submit-result/<int:test_id>/', views.submit_test_result_single, name='submit_test_result_single'),
    path('', views.lab_dashboard, name='lab_dashboard'),
    path('result/<int:test_id>/', views.submit_test_result, name='submit_test_result'),
    path('take/<int:lr_id>/', views.take_request, name='take_lab_request'),
    path('delete/<int:lr_id>/', views.delete_completed, name='delete_completed_lab'),
    path('void/<int:lr_id>/', views.void_lab_request, name='void_lab_request'),
    path('api/tests/', views.lab_tests_api, name='lab_tests_api'),
]
