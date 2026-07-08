from django.urls import path
from . import views

urlpatterns = [
    path('', views.pharmacist_dashboard, name='pharmacist_dashboard'),
    path('dispense/<int:rx_id>/', views.dispense_prescription, name='dispense_prescription'),
    path('void/<int:rx_id>/', views.void_prescription, name='void_prescription'),
    path('reject/<int:rx_id>/', views.reject_prescription, name='reject_prescription'),
    path('delete/<int:rx_id>/', views.delete_dispensed, name='delete_dispensed'),
    path('print/<int:rx_id>/', views.print_prescription, name='print_prescription'),
    path('api/drugs/', views.drug_search, name='drug_search'),
    path('take/<int:rx_id>/', views.take_prescription, name='take_prescription'),
    path('api/drug/add/', views.add_drug, name='add_drug'),
    path('api/drug/<int:drug_id>/delete/', views.delete_drug, name='delete_drug'),
    path('api/drug/<int:drug_id>/edit/', views.edit_drug, name='edit_drug'),
    path('dispensed/', views.pharmacist_dispensed, name='pharmacist_dispensed'),
    path('inventory/', views.pharmacist_inventory, name='pharmacist_inventory'),
    path('drug/<int:drug_id>/edit/', views.edit_drug, name='edit_drug'),
    path('rx/<int:rx_id>/print/', views.print_prescription, name='print_prescription')
]
