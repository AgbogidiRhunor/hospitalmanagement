from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.db import transaction
from .models import Drug, Prescription, PrescriptionDrug
from management.models import User


@login_required
def pharmacist_dashboard(request):
    if request.user.role != 'pharmacist':
        return redirect('dashboard')

    pharmacist = request.user

    my_pending = Prescription.objects.filter(
        pharmacist=pharmacist,
        status='paid'
    ).prefetch_related('drugs__drug').select_related('patient', 'visit', 'doctor')

    unassigned = Prescription.objects.filter(
        pharmacist__isnull=True,
        status='paid'
    ).prefetch_related('drugs__drug').select_related('patient', 'visit')

    dispensed = Prescription.objects.filter(
        pharmacist=pharmacist,
        status='dispensed',
        pharmacist_deleted=False
    ).prefetch_related('drugs__drug').select_related('patient').order_by('-dispensed_at')[:50]

    return render(request, 'pharmacist.html', {
        'my_pending': my_pending,
        'unassigned': unassigned,
        'dispensed': dispensed,
        'pharmacist': pharmacist,
    })


@login_required
def dispense_prescription(request, rx_id):
    if request.method != 'POST' or request.user.role != 'pharmacist':
        return JsonResponse({'error': 'forbidden'}, status=403)

    # IMPORTANT: must be assigned to this pharmacist
    rx = get_object_or_404(
        Prescription,
        pk=rx_id,
        pharmacist=request.user,
        status='paid'
    )

    status = request.POST.get('status', 'dispensed')

    if status not in ('dispensed', 'rejected'):
        return JsonResponse({'error': 'Invalid status'}, status=400)

    note = request.POST.get('pharmacist_note', '').strip()

    with transaction.atomic():
        rx.status = status
        rx.pharmacist_note = note

        if status == 'dispensed':
            rx.dispensed_at = timezone.now()

            visit = rx.visit
            visit.status = 'with_doctor'
            visit.save()

        rx.save()

    return JsonResponse({'status': 'ok'})


@login_required
def reject_prescription(request, rx_id):
    if request.method == 'POST' and request.user.role == 'pharmacist':
        rx = get_object_or_404(Prescription, pk=rx_id)
        rx.status = 'rejected'
        rx.pharmacist = request.user
        rx.pharmacist_note = request.POST.get('note', '')
        rx.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def delete_dispensed(request, rx_id):
    if request.method == 'POST' and request.user.role == 'pharmacist':
        rx = get_object_or_404(Prescription, pk=rx_id, pharmacist=request.user, status='dispensed')
        rx.pharmacist_deleted = True
        rx.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def void_prescription(request, rx_id):
    """
    Pharmacist voids a prescription — patient no longer wants the drugs
    or has left. Sets status to 'voided' so it disappears from the
    pending dashboard but is still auditable.
    """
    if request.method != 'POST' or request.user.role != 'pharmacist':
        return JsonResponse({'error': 'forbidden'}, status=403)
    rx = get_object_or_404(
        Prescription, pk=rx_id,
        pharmacist=request.user,
        status='paid'
    )
    rx.status = 'voided'
    rx.pharmacist_note = request.POST.get('note', 'Voided by pharmacist')
    rx.save()
    return JsonResponse({'status': 'ok'})


@login_required
def print_prescription(request, rx_id):
    rx = get_object_or_404(Prescription, pk=rx_id)
    if request.user not in [rx.patient, rx.pharmacist, rx.doctor] and request.user.role not in ['accountant']:
        if not request.user.is_staff:
            return redirect('dashboard')
    return render(request, 'prescription_print.html', {'rx': rx})


@login_required
def drug_search(request):
    q = request.GET.get('q', '').strip()
    drugs = Drug.objects.filter(is_active=True, name__icontains=q)[:15]
    results = [{'id': d.pk, 'name': str(d), 'price': str(d.price)} for d in drugs]
    return JsonResponse({'results': results})


@login_required
def take_prescription(request, rx_id):
    if request.method != 'POST' or request.user.role != 'pharmacist':
        return JsonResponse({'error': 'forbidden'}, status=403)

    with transaction.atomic():
        rx = (
            Prescription.objects
            .select_for_update()
            .filter(pk=rx_id, pharmacist__isnull=True, status='paid')
            .first()
        )

        if not rx:
            return JsonResponse({
                'error': 'Already taken by another pharmacist'
            }, status=409)

        rx.pharmacist = request.user
        rx.save()

    return JsonResponse({'status': 'ok'})

@login_required
def add_drug(request):
    """Pharmacist adds a new drug to the database."""
    if request.method == 'POST' and request.user.role == 'pharmacist':
        from django.db import transaction
        d = request.POST
        name = d.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Drug name is required'}, status=400)
        try:
            price = float(d.get('price', 0))
        except:
            price = 0
        drug = Drug.objects.create(
            name=name,
            dosage_form=d.get('dosage_form', ''),
            strength=d.get('strength', ''),
            price=price,
            is_active=True,
            is_injection=d.get('is_injection') == '1',
        )
        return JsonResponse({'status': 'ok', 'id': drug.pk, 'name': str(drug)})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def delete_drug(request, drug_id):
    """Pharmacist soft-deletes (deactivates) a drug."""
    if request.method == 'POST' and request.user.role == 'pharmacist':
        drug = get_object_or_404(Drug, pk=drug_id)
        drug.is_active = False
        drug.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def edit_drug(request, drug_id):
    """Pharmacist edits a drug."""
    if request.method == 'POST' and request.user.role == 'pharmacist':
        drug = get_object_or_404(Drug, pk=drug_id)
        d = request.POST
        drug.name = d.get('name', drug.name).strip() or drug.name
        drug.dosage_form = d.get('dosage_form', drug.dosage_form)
        drug.strength = d.get('strength', drug.strength)
        drug.is_injection = d.get('is_injection') == '1'
        try:
            drug.price = float(d.get('price', drug.price))
        except:
            pass
        drug.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def pharmacist_dispensed(request):
    if request.user.role != 'pharmacist':
        return redirect('dashboard_home')

    dispensed = (
        Prescription.objects.filter(
            pharmacist=request.user,
            status__in=['dispensed', 'rejected'],
            dispensed_at__isnull=False   # IMPORTANT FIX
        )
        .select_related('patient', 'doctor')
        .prefetch_related('drugs__drug')
        .order_by('-dispensed_at')
        [:100]
    )

    return render(request, 'pharmacist_dispensed.html', {
        'dispensed': dispensed
    })

@login_required
def pharmacist_inventory(request):
    if request.user.role != 'pharmacist':
        return redirect('dashboard_home')
    from pharmacy.models import Drug
    drugs = Drug.objects.all().order_by('name')
    return render(request, 'pharmacist_inventory.html', {'drugs': drugs})