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
    from records.models import PatientVisit

    # ── All active visits (not yet ended by doctor) ──────────────────────────
    # A visit is "active" until status = 'completed'. We show a tab for every
    # visit that has at least one payment assigned to this accountant (or
    # unassigned), regardless of whether payments are paid or not, until the
    # doctor ends the visit.
    active_visits = (
        PatientVisit.objects
        .exclude(status='completed')
        .filter(accountant=request.user)
        .select_related('patient', 'doctor')
        .order_by('created_at')
    )

    # All payments for those visits (paid AND unpaid) so the tab persists
    all_payments = (
        Payment.objects
        .filter(
            Q(accountant=request.user) | Q(accountant__isnull=True),
            visit__in=active_visits,
        )
        .select_related(
            'patient', 'visit', 'lab_request', 'prescription',
            'surgery', 'admission',
        )
        .prefetch_related(
            'prescription__drugs__drug',
            'lab_request__tests__test',
        )
        .order_by('visit_id', 'payment_group', 'part_number', 'created_at')
    )

    # Group payments by visit
    pay_map = defaultdict(list)
    for p in all_payments:
        pay_map[p.visit_id].append(p)

    visit_sessions = []
    for v in active_visits:
        payments = pay_map.get(v.id, [])
        unpaid   = [p for p in payments if not p.is_paid]
        paid     = [p for p in payments if p.is_paid]
        visit_sessions.append({
            'visit':         v,
            'visit_id':      v.id,
            'patient_name':  v.patient.display_name,
            'payments':      payments,
            'unpaid_count':  len(unpaid),
            'has_unpaid':    bool(unpaid),
            'total_pending': sum(float(p.amount) for p in unpaid),
            'total_paid':    sum(float(p.amount) for p in paid),
            'total_all':     sum(float(p.amount) for p in payments),
            'payment_count': len(payments),
            'has_surgery':   any(p.surgery_id for p in payments),
            'has_admission': any(p.admission_id for p in payments),
            'started_at':    v.created_at,
        })

    # Sort: unpaid sessions first, then by creation time
    visit_sessions.sort(key=lambda s: (not s['has_unpaid'], s['started_at']))

    ctx = {
        'visit_sessions': visit_sessions,
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
                if surg and surg.status in ('patient_reviewed', 'paid', 'draft'):
                    surg.status = 'pending'
                    surg.save()

            elif payment.payment_type in ('admission', 'admission_medication'):
                adm = payment.admission
                if not adm and payment.payment_type == 'admission':
                    from records.models import WardAdmission
                    adm = WardAdmission.objects.filter(visit=visit, status='pending_payment').first()
                if adm and payment.payment_type == 'admission':
                    adm.status = 'paid'
                    adm.save()

            elif payment.payment_type == 'prescription':
                rx = payment.prescription
                if rx:
                    rx.status = 'paid'
                    rx.paid_at = timezone.now()
                    rx.save()
                visit.status = 'pharmacy'
                visit.save()

        # Return updated totals so the UI can refresh without a page reload
        all_visit_payments = Payment.objects.filter(visit=payment.visit)
        total_paid    = sum(float(p.amount) for p in all_visit_payments if p.is_paid)
        total_pending = sum(float(p.amount) for p in all_visit_payments if not p.is_paid)
        return JsonResponse({
            'status': 'ok',
            'total_paid': total_paid,
            'total_pending': total_pending,
        })
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
            return redirect('dashboard_home')
    return render(request, 'receipt_print.html', {'payment': payment})
