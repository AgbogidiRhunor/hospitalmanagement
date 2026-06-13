from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('signup/', views.signup_view, name='signup'),
    path('dashboard/', views.dashboard, name='dashboard_home'),
    path('dashboard/patient/', views.patient_dashboard, name='patient_dashboard'),
    path('dashboard/patient/records/', views.patient_records, name='patient_records'),
    path('dashboard/patient/profile/', views.patient_profile, name='patient_profile'),
    path('dashboard/doctor/', views.doctor_dashboard, name='doctor_dashboard'),
    path('dashboard/nurse/', views.nurse_dashboard, name='nurse_dashboard'),
    path('dashboard/receptionist/', views.receptionist_dashboard, name='receptionist_dashboard'),

    # API
    path('api/toggle-availability/', views.toggle_availability, name='toggle_availability'),
    path('api/select-room/', views.select_consulting_room, name='select_room'),
    path('api/search-patient/', views.search_patient, name='search_patient'),
    path('api/search-doctors/', views.search_doctors, name='search_doctors'),
    path('api/create-visit/', views.create_visit, name='create_visit'),
    path('api/end-visit/<int:visit_id>/', views.end_visit, name='end_visit'),
    path('api/submit-vitals/<int:visit_id>/', views.submit_vitals, name='submit_vitals'),
    path('api/nurse-delete-history/<int:visit_id>/', views.nurse_delete_history, name='nurse_delete_history'),
    path('api/update-visit-summary/<int:visit_id>/', views.update_visit_summary, name='update_visit_summary'),
    path('api/patient-profile/<int:patient_id>/', views.doctor_view_patient, name='doctor_view_patient'),
    path('api/delete-lab/<int:lr_id>/', views.delete_lab_request, name='delete_lab_request'),
    path('api/delete-prescription/<int:rx_id>/', views.delete_prescription, name='delete_prescription'),
    path('dashboard/nurse/ward/', views.ward_dashboard, name='ward_dashboard'),
    path('api/admit-ward-patient/<int:admission_id>/', views.admit_ward_patient, name='admit_ward_patient'),
    path('api/discharge-patient/<int:admission_id>/', views.discharge_patient, name='discharge_patient'),
    path('api/add-admission-rx/<int:admission_id>/', views.add_admission_prescription, name='add_admission_prescription'),
    path('api/void-admission-rx/<int:rx_id>/', views.void_admission_prescription, name='void_admission_prescription'),
    
    # History pages
    path('history/doctor/', views.doctor_history, name='doctor_history'),
    path('history/pharmacist/', views.pharmacist_history, name='pharmacist_history'),
    path('history/lab/', views.lab_attendant_history, name='lab_attendant_history'),
    path('history/accountant/', views.accountant_history, name='accountant_history'),
    path('history/nurse/', views.nurse_history, name='nurse_history'),
    path('api/download-visit-summary/<int:visit_id>/', views.download_visit_summary, name='download_visit_summary'),
]
