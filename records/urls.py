from django.urls import path
from . import views

urlpatterns = [
    path('api/note/<int:visit_id>/', views.add_doctor_note, name='add_doctor_note'),
    path('api/prescribe/<int:visit_id>/', views.prescribe, name='prescribe'),
    path('api/order-lab/<int:visit_id>/', views.order_lab, name='order_lab'),
    path('api/admit/<int:visit_id>/', views.admit_patient, name='admit_patient'),
    path('api/admission-discount/<int:admission_id>/', views.update_admission_payment, name='update_admission_payment'),
    path('api/surgery/<int:visit_id>/', views.create_surgery, name='create_surgery'),
    path('api/surgery-discount/<int:surgery_id>/', views.update_surgery_discount, name='update_surgery_discount'),
    path('api/surgery-review/<int:surgery_id>/', views.patient_review_surgery, name='patient_review_surgery'),
    path('api/surgery-decline/<int:surgery_id>/', views.decline_surgery, name='decline_surgery'),
    path('api/surgery-toggle/<int:surgery_id>/', views.toggle_surgery_status, name='toggle_surgery_status'),
    path('api/ward-occupancy/', views.ward_occupancy, name='ward_occupancy'),
    path('api/drug-search/', views.drug_search, name='drug_search'),
    path('api/lab-test-search/', views.lab_test_search, name='lab_test_search'),
    path('print/lab-results/<int:lr_id>/', views.print_lab_results, name='print_lab_results'),
    path('api/ai-suggest/', views.ai_suggest, name='ai_suggest'),
    # External prescription print (new)
    path('print/external-rx/<int:visit_id>/', views.print_external_rx, name='print_external_rx'),
    path('api/notes/<int:visit_id>/', views.get_doctor_notes, name='get_doctor_notes'),
]
