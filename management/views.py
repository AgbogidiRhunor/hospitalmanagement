import logging
from datetime import datetime
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import ConsultingRoom, SPECIALIZATIONS, User

logger = logging.getLogger(__name__)

def home(request):
    if request.user.is_authenticated:
        return redirect('/dashboard/')
    return render(request, 'landing.html')


def _render_signup(request, status=200):
    return render(request, 'signup.html', {}, status=status)


def _is_role(user, role):
    return getattr(user, 'role', None) == role


def _forbidden_json(message='forbidden', status=403):
    return JsonResponse({'error': message}, status=status)


def _bad_request_json(message='invalid', status=400):
    return JsonResponse({'error': message}, status=status)


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if not username or not password:
            messages.error(request, 'Username and password are required.')
            return render(request, 'login.html')

        try:
            user = authenticate(request, username=username, password=password)
        except Exception:
            logger.exception("Login authentication failed for username=%s", username)
            messages.error(request, 'Unable to sign in right now. Please try again.')
            return render(request, 'login.html')

        if user is None:
            messages.error(request, 'Invalid username or password.')
            return render(request, 'login.html')

        if not getattr(user, 'is_approved', True) and not user.is_staff:
            messages.error(request, 'Your account is pending approval.')
            return render(request, 'login.html')

        login(request, user)
        request.session.cycle_key()
        return redirect('dashboard_home')

    return render(request, 'login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# PATIENT SELF-SIGNUP 
# Patients are the ONLY role that can self-register. Every other role
# (doctor, nurse, pharmacist, lab_attendant, receptionist, accountant) must
# be created explicitly by an admin via /admin/, who also hands them their
# login credentials directly. This view is intentionally patient-only —
# there is no 'role' field exposed in the signup form.
def signup_view(request):
    if request.method == 'POST':
        d = request.POST
        username = d.get('username', '').strip()
        password = d.get('password', '')
        password2 = d.get('password2', '')
        email = d.get('email', '').strip()
        first_name = d.get('first_name', '').strip()
        last_name = d.get('last_name', '').strip()
        phone = d.get('phone', '').strip()

        if not username or not password or not first_name or not last_name:
            messages.error(request, 'Please fill in all required fields.')
            return _render_signup(request)

        if password != password2:
            messages.error(request, 'Passwords do not match.')
            return _render_signup(request)

        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
            return _render_signup(request)

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, 'Username already taken.')
            return _render_signup(request)

        if email and User.objects.filter(email__iexact=email).exists():
            messages.error(request, 'An account with this email already exists.')
            return _render_signup(request)

        try:
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                email=email,
                role='patient',
                phone=phone,
            )
            # Patients are auto-approved — no admin gate needed for self-signup
            user.is_approved = True
            user.save(update_fields=['is_approved'])
        except Exception:
            logger.exception("Patient signup failed for username=%s", username)
            messages.error(request, 'Unable to create account right now. Please try again.')
            return _render_signup(request)

        messages.success(request, 'Account created successfully! Please sign in.')
        return redirect('login')

    return _render_signup(request)


@login_required
def dashboard(request):
    role = request.user.role
    if role == 'patient':
        return redirect('patient_dashboard')
    if role == 'doctor':
        return redirect('doctor_dashboard')
    if role == 'nurse':
        return redirect('nurse_dashboard')
    if role == 'pharmacist':
        return redirect('pharmacist_dashboard')
    if role == 'lab_attendant':
        return redirect('lab_dashboard')
    if role == 'receptionist':
        return redirect('receptionist_dashboard')
    if role == 'accountant':
        return redirect('accountant_dashboard')
    if request.user.is_staff:
        return redirect('/admin/')
    return redirect('login')


# DOCTOR
@login_required
def doctor_dashboard(request):
    if not _is_role(request.user, 'doctor'):
        return redirect('dashboard_home')

    from records.models import PatientVisit, WardAdmission, Ward

    doctor = request.user
    admitted_visit_ids = WardAdmission.objects.filter(
        doctor=doctor,
        status__in=['pending_payment', 'paid', 'admitted'],
    ).values_list('visit_id', flat=True)

    visits = (
        PatientVisit.objects.filter(doctor=doctor)
        .exclude(status__in=['pending_payment', 'completed'])
        .exclude(id__in=admitted_visit_ids)
        .select_related('patient', 'nurse')
        .prefetch_related(
            'doctor_notes',
            'payments',
            'prescriptions__drugs__drug',
            'lab_requests__tests__test',
            'surgeries__surgery_drugs__drug',
            'surgeries__surgery_labs__test',
            'surgeries__admission',
        )
        .order_by('queue_number', 'created_at')
    )

    # FIX: room.current_doc was being set but never had a usable related_name
    # to count occupants. ConsultingRoom.current_doctor property already
    # exists on the model (filters occupying_doctors by is_available) —
    # use that consistently so the template can show who's in each room.
    rooms = ConsultingRoom.objects.filter(is_active=True)
    for room in rooms:
        room.current_doc = (
            User.objects.filter(
                consulting_room=room,
                role='doctor',
                is_available=True,
            )
            .exclude(pk=doctor.pk)
            .first()
        )

    available_pharmacists = User.objects.filter(
        role='pharmacist',
        is_approved=True,
        is_available=True,
    )
    available_lab_attendants = User.objects.filter(
        role='lab_attendant',
        is_approved=True,
        is_available=True,
    )

    waiting_count = (
        PatientVisit.objects.filter(doctor=doctor)
        .exclude(status__in=['pending_payment', 'completed'])
        .exclude(id__in=admitted_visit_ids)
        .count()
    )

    doctor_admissions = (
        WardAdmission.objects.filter(
            doctor=doctor,
            status__in=['paid', 'admitted'],
        )
        .select_related('patient', 'nurse')
        .prefetch_related('prescriptions__items__drug')
        .order_by('ward', 'bed_number')
    )

    ward_choices = Ward.objects.order_by("name").values_list("id", "name")

    ctx = {
        'visits': visits,
        'rooms': rooms,
        'available_pharmacists': available_pharmacists,
        'available_lab_attendants': available_lab_attendants,
        'doctor': doctor,
        'waiting_count': waiting_count,
        'doctor_admissions': doctor_admissions,
        'ward_choices': ward_choices,
    }
    return render(request, 'doctor.html', ctx)


@login_required
def toggle_availability(request):
    if request.method != 'POST':
        return _forbidden_json('POST only', status=405)

    user = request.user
    field = request.POST.get('field', 'is_available')

    if field not in ['is_available', 'is_on_sit', 'is_vital_signs_nurse']:
        return _bad_request_json('Invalid field')

    if field == 'is_vital_signs_nurse' and not user.is_vital_signs_nurse:
        count = User.objects.filter(role='nurse', is_vital_signs_nurse=True).count()
        if count >= 2:
            return JsonResponse(
                {'error': 'Maximum 2 vital-sign nurses at once', 'max_reached': True},
                status=400,
            )

    new_value = not getattr(user, field)
    setattr(user, field, new_value)

    save_fields = [field]
    if field == 'is_available' and not new_value and user.role == 'doctor':
        user.consulting_room = None
        save_fields.append('consulting_room')

    user.save(update_fields=save_fields)
    return JsonResponse({'status': 'ok', 'value': new_value})


@login_required
def select_consulting_room(request):
    if request.method != 'POST' or not _is_role(request.user, 'doctor'):
        return _bad_request_json('invalid')

    room_id = request.POST.get('room_id', '').strip()

    if room_id == '':
        request.user.consulting_room = None
        request.user.save(update_fields=['consulting_room'])
        return JsonResponse({'status': 'ok', 'room': None})

    room = get_object_or_404(ConsultingRoom, pk=room_id, is_active=True)
    other = (
        User.objects.filter(
            consulting_room=room,
            role='doctor',
            is_available=True,
        )
        .exclude(pk=request.user.pk)
        .first()
    )
    if other:
        return JsonResponse(
            {'error': f'Room taken by Dr. {other.display_name}'},
            status=400,
        )

    request.user.consulting_room = room
    request.user.is_available = True
    request.user.save(update_fields=['consulting_room', 'is_available'])
    return JsonResponse({'status': 'ok', 'room': room.display_name})


@login_required
def end_visit(request, visit_id):
    if request.method != 'POST' or not _is_role(request.user, 'doctor'):
        return _forbidden_json()

    from accounting.models import Payment
    from records.models import PatientVisit

    visit = get_object_or_404(PatientVisit, pk=visit_id, doctor=request.user)
    pending = Payment.objects.filter(visit=visit, is_paid=False).count()

    if pending:
        return JsonResponse(
            {
                'error': (
                    f'Cannot end visit: {pending} payment(s) still pending. '
                    'All payments must be settled first.'
                )
            },
            status=400,
        )

    visit.status = 'completed'
    visit.save(update_fields=['status'])
    return JsonResponse({'status': 'ok'})


@login_required
def delete_lab_request(request, lr_id):
    if request.method != 'POST' or not _is_role(request.user, 'doctor'):
        return _forbidden_json()

    from lab.models import LabRequest

    lr = get_object_or_404(LabRequest, pk=lr_id, doctor=request.user)
    lr.delete()
    return JsonResponse({'status': 'ok'})


@login_required
def delete_prescription(request, rx_id):
    if request.method != 'POST' or not _is_role(request.user, 'doctor'):
        return _forbidden_json()

    from pharmacy.models import Prescription

    rx = get_object_or_404(Prescription, pk=rx_id, doctor=request.user)
    rx.delete()
    return JsonResponse({'status': 'ok'})


# NURSE 
# NOTE: There is only ONE 'nurse' role in the system (see ROLES in models.py).
# "Ward nurse" was never a separate role — nurses already access both the
# Patients dashboard (vitals queue) and the Ward dashboard (admitted patients).
# The fix here is purely navigational: nurse.html and ward_dashboard.html
# now cross-link to each other in the sidebar so it reads as one cohesive
# nurse workspace instead of two disconnected pages.
@login_required
def nurse_dashboard(request):
    if not _is_role(request.user, 'nurse'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    nurse = request.user
    visits = (
        PatientVisit.objects.filter(nurse=nurse, status__in=['paid', 'vitals'])
        .select_related('patient', 'doctor')
        .order_by('queue_number', 'created_at')
    )

    waiting_count = visits.count()

    ctx = {'visits': visits, 'nurse': nurse, 'waiting_count': waiting_count}
    return render(request, 'nurse_dashboard.html', ctx)


@login_required
def submit_vitals(request, visit_id):
    if request.method != 'POST' or not _is_role(request.user, 'nurse'):
        return _forbidden_json()

    from records.models import PatientVisit, VitalSigns

    visit = get_object_or_404(PatientVisit, pk=visit_id, nurse=request.user)
    d = request.POST

    VitalSigns.objects.update_or_create(
        visit=visit,
        defaults={
            'nurse': request.user,
            'blood_pressure': d.get('blood_pressure', '').strip(),
            'pulse_rate': d.get('pulse_rate', '').strip(),
            'temperature': d.get('temperature', '').strip(),
            'respiratory_rate': d.get('respiratory_rate', '').strip(),
            'oxygen_saturation': d.get('oxygen_saturation', '').strip(),
            'weight': d.get('weight', '').strip(),
            'height': d.get('height', '').strip(),
            'bmi': d.get('bmi', '').strip(),
            'pain_level': d.get('pain_level', '').strip(),
            'nurse_note': d.get('nurse_note', '').strip(),
        },
    )

    visit.status = 'with_doctor'
    visit.save(update_fields=['status'])
    return JsonResponse({'status': 'ok'})


@login_required
def nurse_delete_history(request, visit_id):
    if request.method != 'POST' or not _is_role(request.user, 'nurse'):
        return _forbidden_json()

    from records.models import PatientVisit

    visit = get_object_or_404(PatientVisit, pk=visit_id, nurse=request.user)
    visit.nurse_history_deleted = True
    visit.save(update_fields=['nurse_history_deleted'])
    return JsonResponse({'status': 'ok'})


@login_required
def nurse_history(request):
    if not _is_role(request.user, 'nurse'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    history = (
        PatientVisit.objects.filter(
            nurse=request.user,
            nurse_history_deleted=False,
        )
        .exclude(status__in=['pending_payment', 'paid', 'vitals'])
        .select_related('patient', 'doctor')
        .prefetch_related('doctor_notes')
        .order_by('-updated_at')
    )
    return render(request, 'nurse_history.html', {'history': history})


# RECEPTIONIST
@login_required
def receptionist_dashboard(request):
    if not _is_role(request.user, 'receptionist'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    accountants = User.objects.filter(role='accountant', is_approved=True)
    nurses = User.objects.filter(role='nurse', is_approved=True)
    general_doctors = User.objects.filter(role='doctor', is_approved=True, doctor_type='general')
    specialist_doctors = User.objects.filter(role='doctor', is_approved=True, doctor_type='specialist')

    recent_visits = (
        PatientVisit.objects.filter(receptionist=request.user)
        .select_related('patient', 'doctor', 'nurse', 'accountant')
        .order_by('-created_at')[:50]
    )

    ctx = {
        'accountants': accountants,
        'nurses': nurses,
        'general_doctors': general_doctors,
        'specialist_doctors': specialist_doctors,
        'recent_visits': recent_visits,
        'specializations': SPECIALIZATIONS,
    }
    return render(request, 'receptionist.html', ctx)


@login_required
def search_patient(request):
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})

    patients = (
        User.objects.filter(role='patient', is_approved=True)
        .filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(preferred_name__icontains=q)
        )[:10]
    )

    results = [{'id': p.pk, 'name': p.display_name, 'username': p.username} for p in patients]
    return JsonResponse({'results': results})


@login_required
def search_doctors(request):
    q = request.GET.get('q', '').strip()
    dtype = request.GET.get('type', '').strip()
    spec = request.GET.get('specialization', '').strip()

    qs = User.objects.filter(role='doctor', is_approved=True)

    if dtype:
        qs = qs.filter(doctor_type=dtype)
    if spec:
        qs = qs.filter(specialization=spec)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(preferred_name__icontains=q)
            | Q(username__icontains=q)
        )

    results = [
        {
            'id': d.pk,
            'name': d.display_name,
            'type': d.get_doctor_type_display() if d.doctor_type else '',
            'specialization': d.get_specialization_display() if d.specialization else '',
            'available': d.is_available,
            'room': d.consulting_room.display_name if d.consulting_room else '',
        }
        for d in qs[:20]
    ]
    return JsonResponse({'results': results})


@login_required
def create_visit(request):
    if request.method != 'POST' or not _is_role(request.user, 'receptionist'):
        return _forbidden_json()

    from accounting.models import Payment
    from records.models import PatientVisit

    d = request.POST

    patient = User.objects.filter(pk=d.get('patient_id'), role='patient').first()
    doctor = User.objects.filter(pk=d.get('doctor_id'), role='doctor').first()
    nurse = User.objects.filter(pk=d.get('nurse_id'), role='nurse').first()
    accountant = User.objects.filter(pk=d.get('accountant_id'), role='accountant').first()

    if not patient:
        return _bad_request_json('Patient not found')
    if not doctor:
        return _bad_request_json('Doctor not found')
    if not nurse:
        return _bad_request_json('Nurse not found')
    if not accountant:
        return _bad_request_json('Accountant not found')

    try:
        fee = float(d.get('consultation_fee', 0) or 0)
    except (TypeError, ValueError):
        fee = 0

    with transaction.atomic():
        today = timezone.now().date()
        last_q = (
            PatientVisit.objects.select_for_update()
            .filter(created_at__date=today)
            .order_by('-queue_number')
            .values_list('queue_number', flat=True)
            .first()
        )
        queue_number = (last_q or 0) + 1

        visit = PatientVisit.objects.create(
            patient=patient,
            receptionist=request.user,
            doctor=doctor,
            nurse=nurse,
            accountant=accountant,
            consultation_fee=fee,
            status='pending_payment',
            queue_number=queue_number,
            chief_complaint=d.get('chief_complaint', '').strip(),
        )

        Payment.objects.create(
            visit=visit,
            patient=patient,
            accountant=accountant,
            payment_type='consultation',
            amount=fee,
            is_paid=False,
        )

    return JsonResponse({'status': 'ok', 'visit_id': visit.pk})


@login_required
def receptionist_history(request):
    if not _is_role(request.user, 'receptionist'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    visits = (
        PatientVisit.objects.filter(receptionist=request.user)
        .select_related('patient', 'doctor', 'nurse', 'accountant')
        .order_by('-created_at')
    )
    return render(request, 'receptionist_history.html', {'visits': visits})


# PATIENT
@login_required
def patient_dashboard(request):
    if not _is_role(request.user, 'patient'):
        return redirect('dashboard_home')

    from records.models import PatientVisit, Surgery

    try:
        active_visit = (
            PatientVisit.objects.filter(patient=request.user)
            .exclude(status='completed')
            .select_related('doctor', 'nurse', 'accountant', 'vitals')
            .prefetch_related(
                'doctor_notes',
                'payments',
                'prescriptions__drugs__drug',
                'lab_requests__tests__test',
                'surgeries__surgery_drugs__drug',
                'surgeries__surgery_labs__test',
                'surgeries__admission',
                'surgeries__payments',
                'admissions__prescriptions__items__drug',
                'admissions__payments',
            )
            .first()
        )

        pending_surgery_reviews = (
            Surgery.objects.filter(patient=request.user, status='draft')
            .select_related('doctor', 'visit')
            .prefetch_related('surgery_drugs__drug', 'surgery_labs__test', 'admission')
        )

        ctx = {
            'user': request.user,
            'active_visit': active_visit,
            'pending_surgery_reviews': pending_surgery_reviews,
        }
        return render(request, 'patient.html', ctx)
    except Exception:
        logger.exception("Patient dashboard failed for user_id=%s", request.user.pk)
        messages.error(request, 'Unable to load your dashboard right now.')
        return redirect('patient_records')


@login_required
def patient_records(request):
    if not _is_role(request.user, 'patient'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    try:
        visits = (
            PatientVisit.objects.filter(patient=request.user)
            .select_related('doctor', 'nurse', 'accountant')
            .prefetch_related(
                'doctor_notes',
                'payments',
                'prescriptions__drugs__drug',
                'lab_requests__tests__test',
                'admissions',
                'surgeries',
            )
            .order_by('-created_at')
        )

        ctx = {'visits': visits, 'user': request.user}
        return render(request, 'patient_records.html', ctx)

    except Exception:
        logger.exception("Patient records failed for user_id=%s", request.user.pk)
        messages.error(request, 'Unable to load your records right now.')
        return redirect('patient_dashboard')


@login_required
def patient_profile(request):
    if not _is_role(request.user, 'patient'):
        return redirect('dashboard_home')

    if request.method == 'POST':
        u = request.user
        d = request.POST

        u.first_name = d.get('first_name', u.first_name).strip()
        u.last_name = d.get('last_name', u.last_name).strip()
        u.preferred_name = d.get('preferred_name', u.preferred_name).strip()
        u.email = d.get('email', u.email).strip()
        u.phone = d.get('phone', u.phone).strip()
        u.address = d.get('address', u.address).strip()
        u.gender = d.get('gender', u.gender)

        dob = d.get('date_of_birth', '').strip()
        if dob:
            try:
                u.date_of_birth = datetime.strptime(dob, '%Y-%m-%d').date()
            except ValueError:
                pass

        u.blood_group = d.get('blood_group', u.blood_group)
        u.genotype = d.get('genotype', u.genotype)
        u.allergies = d.get('allergies', u.allergies).strip()
        u.medical_history = d.get('medical_history', u.medical_history).strip()
        u.current_medications = d.get('current_medications', u.current_medications).strip()
        u.family_history = d.get('family_history', u.family_history).strip()
        u.surgical_history = d.get('surgical_history', u.surgical_history).strip()
        u.immunizations = d.get('immunizations', u.immunizations).strip()
        u.occupation = d.get('occupation', u.occupation).strip()
        u.marital_status = d.get('marital_status', u.marital_status)
        u.nationality = d.get('nationality', u.nationality).strip()
        u.religion = d.get('religion', u.religion).strip()
        u.next_of_kin_name = d.get('next_of_kin_name', u.next_of_kin_name).strip()
        u.next_of_kin_phone = d.get('next_of_kin_phone', u.next_of_kin_phone).strip()
        u.next_of_kin_relationship = d.get('next_of_kin_relationship', u.next_of_kin_relationship).strip()
        u.emergency_contact_name = d.get('emergency_contact_name', u.emergency_contact_name).strip()
        u.emergency_contact_phone = d.get('emergency_contact_phone', u.emergency_contact_phone).strip()
        u.emergency_contact_relationship = d.get(
            'emergency_contact_relationship',
            u.emergency_contact_relationship,
        ).strip()
        u.disabilities = d.get('disabilities', u.disabilities).strip()
        u.home_phone = d.get('home_phone', u.home_phone).strip()
        u.work_phone = d.get('work_phone', u.work_phone).strip()
        u.temporary_address = d.get('temporary_address', u.temporary_address).strip()
        u.employer = d.get('employer', u.employer).strip()
        u.sex_at_birth = d.get('sex_at_birth', u.sex_at_birth).strip()
        u.has_support_person = d.get('has_support_person') == 'yes'
        u.has_legal_guardian = d.get('has_legal_guardian') == 'yes'

        update_fields = [
            'first_name', 'last_name', 'preferred_name', 'email', 'phone',
            'address', 'gender', 'date_of_birth', 'blood_group', 'genotype',
            'allergies', 'medical_history', 'current_medications', 'family_history',
            'surgical_history', 'immunizations', 'occupation', 'marital_status',
            'nationality', 'religion', 'next_of_kin_name', 'next_of_kin_phone',
            'next_of_kin_relationship', 'emergency_contact_name', 'emergency_contact_phone',
            'emergency_contact_relationship', 'disabilities', 'home_phone', 'work_phone',
            'temporary_address', 'employer', 'sex_at_birth', 'has_support_person',
            'has_legal_guardian',
        ]

        if 'profile_photo' in request.FILES:
            u.profile_photo = request.FILES['profile_photo']
            update_fields.append('profile_photo')

        u.save(update_fields=update_fields)

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type != 'multipart/form-data':
            return JsonResponse({'status': 'ok'})
        messages.success(request, 'Profile updated successfully.')
        return redirect('patient_profile')

    from management.models import BLOOD_GROUPS, GENOTYPES, MARITAL_STATUS_CHOICES

    return render(
        request,
        'patient_profile.html',
        {
            'u': request.user,
            'blood_groups': BLOOD_GROUPS,
            'genotypes': GENOTYPES,
            'marital_status_choices': MARITAL_STATUS_CHOICES,
        },
    )


@login_required
def update_visit_summary(request, visit_id):
    if request.method != 'POST' or not _is_role(request.user, 'patient'):
        return _forbidden_json()

    from records.models import PatientVisit

    visit = get_object_or_404(PatientVisit, pk=visit_id, patient=request.user)
    visit.visit_summary = request.POST.get('summary', '').strip()
    visit.save(update_fields=['visit_summary'])
    return JsonResponse({'status': 'ok'})


# HISTORY PAGES (each role gets its own dedicated template)
@login_required
def doctor_history(request):
    if not _is_role(request.user, 'doctor'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    visits = (
        PatientVisit.objects.filter(doctor=request.user, status='completed')
        .select_related('patient', 'nurse')
        .prefetch_related(
            'doctor_notes',
            'payments',
            'prescriptions__drugs__drug',
            'lab_requests__tests__test',
            'surgeries',
            'admissions',
        )
        .order_by('-created_at')
    )
    return render(request, 'doctor_history.html', {'visits': visits})


@login_required
def pharmacist_history(request):
    if not _is_role(request.user, 'pharmacist'):
        return redirect('dashboard_home')

    from pharmacy.models import Prescription

    rxs = (
        Prescription.objects.filter(pharmacist=request.user)
        .exclude(status__in=['pending_payment'])
        .select_related('patient', 'visit', 'doctor')
        .prefetch_related('drugs__drug')
        .order_by('-created_at')
    )
    return render(request, 'pharmacist_history.html', {'rxs': rxs})


@login_required
def lab_attendant_history(request):
    if not _is_role(request.user, 'lab_attendant'):
        return redirect('dashboard_home')

    from lab.models import LabRequest

    requests = (
    LabRequest.objects.filter(
        lab_attendant=request.user,
        status='completed'
    )
    .select_related('patient', 'visit', 'doctor')
    .prefetch_related('tests__test')
    .order_by('-created_at')
    )
    return render(request, 'lab_history.html', {'requests': requests})


@login_required
def accountant_history(request):
    """
    FIX: payments are now grouped by session (payment_group) rather than
    shown as flat individual rows. Payments with an empty payment_group
    (standalone consultation/lab/rx) are bucketed under their visit id
    so the accountant still sees one card per patient encounter instead
    of N separate line items for the same visit.
    """
    if not _is_role(request.user, 'accountant'):
        return redirect('dashboard_home')

    from accounting.models import Payment
    from itertools import groupby
    from operator import attrgetter

    payments = list(
        Payment.objects.filter(accountant=request.user, is_paid=True)
        .select_related('patient', 'visit', 'surgery', 'admission', 'prescription', 'lab_request')
        .prefetch_related('prescription__drugs__drug', 'lab_request__tests__test')
        .order_by('visit_id', 'payment_group', '-paid_at')
    )

    # Build a stable session key: explicit payment_group if set, else visit id
    def session_key(p):
        return p.payment_group or f'visit-{p.visit_id}'

    payments.sort(key=lambda p: (session_key(p), p.paid_at), reverse=False)
    payments.sort(key=session_key)

    sessions = []
    for key, group in groupby(payments, key=session_key):
        group_list = list(group)
        group_list.sort(key=lambda p: p.paid_at or p.created_at, reverse=True)
        first = group_list[0]
        total = sum(float(p.amount) for p in group_list)
        sessions.append({
            'key': key,
            'patient': first.patient,
            'visit': first.visit,
            'surgery': first.surgery,
            'admission': first.admission,
            'payments': group_list,
            'total': total,
            'latest_paid_at': max((p.paid_at for p in group_list if p.paid_at), default=None),
            'is_surgery': key.startswith('surgery-'),
            'is_admission': key.startswith('admission-'),
        })

    sessions.sort(key=lambda s: s['latest_paid_at'] or timezone.now(), reverse=True)

    return render(request, 'accountant_history.html', {'sessions': sessions})


@login_required
def ward_dashboard(request):
    if request.user.role not in ['nurse', 'doctor']:
        return redirect('dashboard_home')

    from records.models import Ward, WardAdmission

    admissions = (
        WardAdmission.objects.filter(status__in=['paid', 'admitted'])
        .select_related('patient', 'doctor', 'nurse', 'visit', 'ward')
        .prefetch_related(
            'prescriptions__items__drug',
            'visit__lab_requests__tests__test',
            'visit__surgeries__surgery_drugs__drug',
            'visit__surgeries__surgery_labs__test',
            'visit__surgeries__payments',
            'payments',
        )
        .order_by('ward__name', 'bed_number')
    )

    ward_choices = [
        (ward.id, ward.name)
        for ward in Ward.objects.all().order_by('name')
    ]

    available_lab_attendants = User.objects.filter(
        role='lab_attendant',
        is_approved=True,
        is_available=True,
    )

    available_pharmacists = User.objects.filter(
        role='pharmacist',
        is_approved=True,
        is_available=True,
    )

    ctx = {
        'nurse': request.user,
        'user': request.user,
        'admissions': admissions,
        'ward_choices': ward_choices,
        'available_lab_attendants': available_lab_attendants,
        'available_pharmacists': available_pharmacists,
    }

    return render(request, 'ward_dashboard.html', ctx)


@login_required
def admit_ward_patient(request, admission_id):
    if request.method != 'POST' or not _is_role(request.user, 'nurse'):
        return _forbidden_json()

    from records.models import WardAdmission

    admission = get_object_or_404(WardAdmission, pk=admission_id, status='paid')
    admission.status = 'admitted'
    admission.admitted_at = timezone.now()
    admission.nurse = request.user
    admission.save(update_fields=['status', 'admitted_at', 'nurse'])
    return JsonResponse({'status': 'ok'})


@login_required
def discharge_patient(request, admission_id):
    if request.method != 'POST' or request.user.role not in ['nurse', 'doctor']:
        return _forbidden_json()

    from accounting.models import Payment
    from records.models import WardAdmission

    admission = get_object_or_404(WardAdmission, pk=admission_id, status='admitted')
    outstanding = Payment.objects.filter(visit=admission.visit, is_paid=False).count()

    if outstanding:
        return JsonResponse(
            {
                'error': (
                    f'Cannot discharge: {outstanding} payment(s) still outstanding '
                    '(including surgery). All payments must be cleared before discharge.'
                )
            },
            status=400,
        )

    admission.status = 'discharged'
    admission.discharged_at = timezone.now()
    admission.save(update_fields=['status', 'discharged_at'])
    return JsonResponse({'status': 'ok'})


@login_required
def add_admission_prescription(request, admission_id):
    if request.method != 'POST' or not _is_role(request.user, 'doctor'):
        return _forbidden_json()

    from accounting.models import Payment
    from pharmacy.models import Drug
    from records.models import AdmissionPrescription, AdmissionPrescriptionItem, WardAdmission

    admission = get_object_or_404(WardAdmission, pk=admission_id, status__in=['paid', 'admitted'])
    d = request.POST
    drug_ids = d.getlist('drug_ids[]')
    dosages = d.getlist('dosages[]')
    quantities = d.getlist('quantities[]')

    if not drug_ids:
        return _bad_request_json('No drugs selected')

    with transaction.atomic():
        rx = AdmissionPrescription.objects.create(
            admission=admission,
            doctor=request.user,
            notes=d.get('notes', '').strip(),
            status='active',
        )

        total = 0
        for drug_id, dosage, qty in zip(drug_ids, dosages, quantities):
            try:
                drug = Drug.objects.get(pk=drug_id)
            except Drug.DoesNotExist:
                continue

            try:
                qty_int = int(qty) if qty else 1
            except (TypeError, ValueError):
                qty_int = 1

            item_total = drug.price * qty_int
            AdmissionPrescriptionItem.objects.create(
                prescription=rx,
                drug=drug,
                drug_name=drug.name,
                dosage=(dosage or '').strip(),
                quantity=qty_int,
                price=item_total,
            )
            total += item_total

        if total > 0:
            Payment.objects.create(
                visit=admission.visit,
                patient=admission.patient,
                accountant=admission.accountant,
                payment_type='admission_medication',
                amount=total,
                admission=admission,
            )

    return JsonResponse({'status': 'ok', 'rx_id': rx.pk})


@login_required
def void_admission_prescription(request, rx_id):
    if request.method != 'POST' or not _is_role(request.user, 'doctor'):
        return _forbidden_json()

    from records.models import AdmissionPrescription

    rx = get_object_or_404(AdmissionPrescription, pk=rx_id, status='active')
    rx.status = 'voided'
    rx.voided_at = timezone.now()
    rx.save(update_fields=['status', 'voided_at'])
    return JsonResponse({'status': 'ok'})


@login_required
def download_visit_summary(request, visit_id):
    if not _is_role(request.user, 'patient'):
        return redirect('dashboard_home')

    from records.models import PatientVisit

    visit = get_object_or_404(
        PatientVisit.objects.select_related('doctor', 'nurse', 'accountant').prefetch_related(
            'doctor_notes',
            'payments',
            'prescriptions__drugs__drug',
            'lab_requests__tests__test',
            'admissions',
            'surgeries',
        ),
        pk=visit_id,
        patient=request.user,
    )
    return render(request, 'visit_summary_print.html', {'visit': visit, 'patient': request.user})


@login_required
def doctor_view_patient(request, patient_id):
    if not _is_role(request.user, 'doctor'):
        return _forbidden_json()

    from records.models import PatientVisit

    patient = get_object_or_404(User, pk=patient_id, role='patient')
    visits = (
        PatientVisit.objects.filter(patient=patient)
        .select_related('doctor')
        .prefetch_related(
            'doctor_notes',
            'prescriptions__drugs__drug',
            'lab_requests__tests__test',
        )
        .order_by('-created_at')[:20]
    )

    data = {
        'id': patient.pk,
        'name': patient.display_name,
        'username': patient.username,
        'gender': patient.gender or '',
        'dob': str(patient.date_of_birth) if patient.date_of_birth else '',
        'phone': patient.phone or '',
        'blood_group': patient.blood_group or '',
        'genotype': patient.genotype or '',
        'allergies': patient.allergies or '',
        'medical_history': patient.medical_history or '',
        'current_medications': patient.current_medications or '',
        'family_history': patient.family_history or '',
        'surgical_history': patient.surgical_history or '',
        'emergency_contact': patient.emergency_contact_name or '',
        'emergency_phone': patient.emergency_contact_phone or '',
        'emergency_rel': patient.emergency_contact_relationship or '',
        'visit_count': visits.count(),
    }
    return JsonResponse({'status': 'ok', 'patient': data})
