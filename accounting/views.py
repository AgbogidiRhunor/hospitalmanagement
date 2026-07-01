from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db import transaction
from .models import Payment
from management.models import User


@login_required
def accountant_dashboard(request):
    if request.user.role != 'accountant':
        return redirect('dashboard_home')

    from django.db.models import Q
    from collections import defaultdict

    # Get all pending payments for this accountant (or unassigned)
    pending_qs = Payment.objects.filter(
        Q(accountant=request.user) | Q(accountant__isnull=True),
        is_paid=False,
    ).select_related(
        'patient', 'visit', 'lab_request', 'prescription',
        'surgery', 'admission__ward',
    ).prefetch_related(
        'prescription__drugs__drug',
        'lab_request__tests__test',
    ).order_by('visit_id', 'payment_group', 'part_number', '-created_at')

    # Group by visit_id so every payment a patient owes is under one card
    visit_map = defaultdict(list)
    for pay in pending_qs:
        visit_map[pay.visit_id].append(pay)

    visit_sessions = []
    for visit_id, payments in visit_map.items():
        unpaid = [p for p in payments if not p.is_paid]
        paid   = [p for p in payments if p.is_paid]
        first  = payments[0]
        visit_sessions.append({
            'visit_id':      visit_id,
            'patient_name':  first.patient.display_name,
            'payments':      payments,
            'unpaid_count':  len(unpaid),
            'has_unpaid':    bool(unpaid),
            'total_pending': sum(float(p.amount) for p in unpaid),
            'total_paid':    sum(float(p.amount) for p in paid),
            'total_all':     sum(float(p.amount) for p in payments),
            'payment_count': len(payments),
            'has_surgery':   any(p.surgery_id for p in payments),
            'has_admission': any(p.admission_id for p in payments),
            'started_at':    first.created_at,
        })

    # Sort: sessions with unpaid first, then by creation time
    visit_sessions.sort(key=lambda s: (not s['has_unpaid'], s['started_at']))

    processed = Payment.objects.filter(
        accountant=request.user, is_paid=True,
        accountant_dashboard_deleted=False,
    ).select_related('patient', 'visit', 'surgery', 'admission').order_by('-paid_at')[:50]

    ctx = {
        'visit_sessions': visit_sessions,
        'processed': processed,
        'accountant': request.user,
    }
    return render(request, 'accountant.html', ctx)


@login_required
def confirm_payment(request, payment_id):
    if request.method == 'POST' and request.user.role == 'accountant':
        from django.db.models import Q
        payment = get_object_or_404(
            Payment, Q(accountant=request.user) | Q(accountant__isnull=True), pk=payment_id
        )
        if not payment.accountant:
            payment.accountant = request.user
        with transaction.atomic():
            payment.is_paid = True
            payment.paid_at = timezone.now()
            payment.save()
            visit = payment.visit

            if payment.payment_type == 'consultation':
                visit.consultation_paid_at = timezone.now()
                visit.status = 'paid'
                from records.models import PatientVisit
                from django.db.models import Max
                max_q = PatientVisit.objects.filter(
                    doctor=visit.doctor, queue_number__isnull=False
                ).aggregate(Max('queue_number'))['queue_number__max'] or 0
                visit.queue_number = max_q + 1
                visit.save()

            elif payment.payment_type == 'lab':
                lr = payment.lab_request
                if lr:
                    lr.status = 'paid'
                    lr.paid_at = timezone.now()
                    lr.save()
                visit.status = 'lab_processing'
                visit.save()

            elif payment.payment_type == 'surgery':
                surg = payment.surgery
                if surg:
                    remaining = Payment.objects.filter(
                        surgery=surg, payment_type='surgery', is_paid=False
                    ).exclude(pk=payment.pk).count()
                    # Always move to 'pending' on first/any surgery payment confirmation
                    # 'pending' means: payment received, surgery can proceed
                    # Doctor toggles pending → underway → ended
                    if surg.status in ('patient_reviewed', 'paid'):
                        surg.status = 'pending'
                        surg.save()
                    elif surg.status == 'draft':
                        surg.status = 'pending'
                        surg.save()

            elif payment.payment_type in ('admission', 'admission_medication'):
                adm = payment.admission
                if not adm and payment.payment_type == 'admission':
                    from records.models import WardAdmission
                    adm = WardAdmission.objects.filter(visit=visit, status='pending_payment').first()
                if adm and payment.payment_type == 'admission':
                    remaining = Payment.objects.filter(
                        admission=adm, payment_type='admission', is_paid=False
                    ).exclude(pk=payment.pk).count()
                    if remaining == 0 and adm.status == 'pending_payment':
                        # All parts paid — fully settled
                        adm.status = 'paid'
                        adm.save()
                    elif adm.status == 'pending_payment':
                        # First part paid — unlock admission (nurse can admit)
                        # Remaining parts still outstanding but patient can be admitted
                        adm.status = 'paid'
                        adm.save()

            elif payment.payment_type == 'prescription':
                rx = payment.prescription
                if rx:
                    rx.status = 'paid'
                    rx.paid_at = timezone.now()
                    rx.save()
                # Route ALL prescriptions to pharmacy (including surgery drug prescriptions)
                visit.status = 'pharmacy'
                visit.save()

        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def delete_processed(request, payment_id):
    if request.method == 'POST' and request.user.role == 'accountant':
        pay = get_object_or_404(Payment, pk=payment_id, accountant=request.user, is_paid=True)
        pay.accountant_dashboard_deleted = True
        pay.save()
        return JsonResponse({'status': 'ok'})


@login_required
def print_receipt(request, payment_id):
    payment = get_object_or_404(Payment, pk=payment_id)
    if request.user.role not in ['accountant'] and request.user != payment.patient:
        if not request.user.is_staff:
            return redirect('dashboard')
    return render(request, 'receipt_print.html', {'payment': payment})