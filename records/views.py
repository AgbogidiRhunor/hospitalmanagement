import json
import urllib.request
import urllib.error
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.utils import timezone
from django.db import transaction
from .models import PatientVisit, VitalSigns, DoctorNote
from pharmacy.models import Prescription, PrescriptionDrug, Drug
from lab.models import LabRequest, LabRequestTest, LabTest
from accounting.models import Payment
from management.models import User


def _is_role(user, role):
    return getattr(user, 'role', None) == role


@login_required
def add_doctor_note(request, visit_id):
    if request.method == 'POST' and request.user.role == 'doctor':
        visit = get_object_or_404(PatientVisit, pk=visit_id, doctor=request.user)
        note = request.POST.get('note', '').strip()
        if note:
            DoctorNote.objects.create(visit=visit, doctor=request.user, note=note)
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def prescribe(request, visit_id):
    """Doctor submits prescription → creates Prescription + sends to accountant for payment."""
    if request.method == 'POST' and request.user.role == 'doctor':
        visit = get_object_or_404(PatientVisit, pk=visit_id, doctor=request.user)
        d = request.POST

        pharmacist_id = d.get('pharmacist_id')
        pharmacist = None
        if pharmacist_id:
            pharmacist = User.objects.filter(pk=pharmacist_id, role='pharmacist').first()

        accountant_id = d.get('accountant_id')
        accountant = None
        if accountant_id:
            accountant = User.objects.filter(pk=accountant_id, role='accountant').first()
        if not accountant:
            accountant = visit.accountant

        drug_ids = d.getlist('drug_ids[]')
        dosages = d.getlist('dosages[]')
        quantities = d.getlist('quantities[]')

        if not drug_ids:
            return JsonResponse({'error': 'No drugs selected'}, status=400)

        with transaction.atomic():
            rx = Prescription.objects.create(
                visit=visit,
                patient=visit.patient,
                doctor=request.user,
                pharmacist=pharmacist,
                accountant=accountant,
                doctor_note=d.get('note', ''),
                status='pending_payment',
            )
            total = 0
            for drug_id, dosage, qty in zip(drug_ids, dosages, quantities):
                try:
                    drug = Drug.objects.get(pk=drug_id)
                    qty = int(qty) if qty else 1
                    price = drug.price * qty
                    inj_days = request.POST.getlist('injection_days[]')
                    inj_times = request.POST.getlist('injection_times[]')
                    idx = list(drug_ids).index(drug_id)
                    inj_d = int(inj_days[idx]) if inj_days and idx < len(inj_days) and inj_days[idx] else None
                    inj_t = int(inj_times[idx]) if inj_times and idx < len(inj_times) and inj_times[idx] else None
                    PrescriptionDrug.objects.create(
                        prescription=rx, drug=drug,
                        dosage=dosage, quantity=qty,
                        price_at_time=drug.price,
                        injection_days=inj_d,
                        injection_times_per_day=inj_t,
                    )
                    total += price
                except Drug.DoesNotExist:
                    pass
            rx.total_price = total
            rx.save()

            Payment.objects.create(
                visit=visit,
                patient=visit.patient,
                accountant=accountant,
                payment_type='prescription',
                amount=total,
                prescription=rx,
            )
            visit.status = 'rx_pending'
            visit.save()

        return JsonResponse({'status': 'ok', 'rx_id': rx.pk})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def order_lab(request, visit_id):
    """Doctor orders lab tests → sends to accountant first."""
    
    if request.method != 'POST' or request.user.role != 'doctor':
        return JsonResponse({'error': 'forbidden'}, status=403)

    visit = get_object_or_404(PatientVisit, pk=visit_id, doctor=request.user)
    d = request.POST

    # Lab Attendant (optional but validated properly)
    lab_attendant = None
    lab_attendant_id = d.get('lab_attendant_id')

    if lab_attendant_id:
        lab_attendant = User.objects.filter(
            pk=lab_attendant_id,
            role='lab_attendant'
        ).first()

        # optional safety fallback (important fix)
        if lab_attendant_id and not lab_attendant:
            return JsonResponse({'error': 'Invalid lab attendant selected'}, status=400)

    # Accountant (required fallback to visit.accountant) 
    accountant = None
    accountant_id = d.get('accountant_id')

    if accountant_id:
        accountant = User.objects.filter(
            pk=accountant_id,
            role='accountant'
        ).first()

    if not accountant:
        accountant = visit.accountant

    if not accountant:
        return JsonResponse({'error': 'No accountant assigned'}, status=400)

    # Tests 
    test_ids = d.getlist('test_ids[]')
    test_notes = d.getlist('test_notes[]')

    if not test_ids:
        return JsonResponse({'error': 'No tests selected'}, status=400)

    # prevent mismatch bugs
    if len(test_notes) < len(test_ids):
        test_notes += [''] * (len(test_ids) - len(test_notes))

    with transaction.atomic():
        lr = LabRequest.objects.create(
            visit=visit,
            patient=visit.patient,
            doctor=request.user,
            lab_attendant=lab_attendant,
            accountant=accountant,
            doctor_note=d.get('note', ''),
            status='pending_payment',
        )

        total = 0

        for test_id, note in zip(test_ids, test_notes):
            test = LabTest.objects.filter(pk=test_id).first()
            if not test:
                continue

            LabRequestTest.objects.create(
                request=lr,
                test=test,
                doctor_note=note,
                price_at_time=test.price,
            )
            total += test.price

        lr.total_price = total
        lr.save(update_fields=['total_price'])

        Payment.objects.create(
            visit=visit,
            patient=visit.patient,
            accountant=accountant,
            payment_type='lab',
            amount=total,
            lab_request=lr,
        )

        visit.status = 'lab_pending'
        visit.save(update_fields=['status'])

    return JsonResponse({'status': 'ok', 'lr_id': lr.pk})

def _create_part_payments(visit, patient, accountant, payment_type, total_amount,
                           discount_amount, num_parts, part_amounts,
                           obj_fk_name, obj_instance, payment_group):
    """
    Helper: creates 1-5 part payment records.
    part_amounts: list of float amounts (len == num_parts). If empty, splits evenly.
    """
    net = float(total_amount) - float(discount_amount)
    if net < 0:
        net = 0

    if not part_amounts or len(part_amounts) != num_parts:
        # Split evenly
        base = round(net / num_parts, 2)
        part_amounts = [base] * num_parts
        # Fix rounding so sum == net
        diff = round(net - sum(part_amounts), 2)
        part_amounts[0] = round(part_amounts[0] + diff, 2)

    payments = []
    for i, amt in enumerate(part_amounts, 1):
        kwargs = {
            'visit': visit,
            'patient': patient,
            'accountant': accountant,
            'payment_type': payment_type,
            'amount': amt,
            'part_number': i,
            'total_parts': num_parts,
            'payment_group': payment_group,
            obj_fk_name: obj_instance,
        }
        p = Payment.objects.create(**kwargs)
        payments.append(p)
    return payments


@login_required
def admit_patient(request, visit_id):
    """Doctor submits admission form with optional part-payments and discount."""
    if request.method == 'POST' and request.user.role == 'doctor':
        from records.models import (
            Ward,
            WardAdmission,
            AdmissionPrescription,
            AdmissionPrescriptionItem,
        )

        visit = get_object_or_404(PatientVisit, pk=visit_id, doctor=request.user)
        d = request.POST

        ward = get_object_or_404(Ward, pk=d.get('ward'))
        bed_number = int(d.get('bed_number', 1))
        daily_fee = float(d.get('daily_ward_fee', 0) or 0)
        total_fee = float(d.get('total_admission_fee', 0) or 0)
        discount = float(d.get('discount_amount', 0) or 0)
        est_days = max(1, int(d.get('est_days', 1) or 1))

        # Validate bed number against ward capacity
        if bed_number < 1 or bed_number > ward.capacity:
            return JsonResponse(
                {'error': f'Bed number must be between 1 and {ward.capacity}'},
                status=400
            )

        # Instalment plan: one part per day (daily_fee per part)
        # JS sends est_days; each instalment = daily_fee
        num_parts = est_days

        # Each part = daily_fee (net of discount spread evenly)
        part_amounts = []  # let _create_part_payments split evenly (daily_fee per part)

        occupied = WardAdmission.objects.filter(
            ward=ward,
            bed_number=bed_number,
            status__in=['paid', 'admitted']
        ).exists()

        if occupied:
            return JsonResponse(
                {'error': f'Bed {bed_number} is already occupied'},
                status=400
            )

        drug_ids = d.getlist('drug_ids[]')
        dosages = d.getlist('dosages[]')
        quantities = d.getlist('quantities[]')

        with transaction.atomic():
            admission = WardAdmission.objects.create(
                visit=visit,
                patient=visit.patient,
                doctor=request.user,
                nurse=visit.nurse,
                accountant=visit.accountant,
                ward=ward,
                bed_number=bed_number,
                admission_reason=d.get('admission_reason', ''),
                daily_ward_fee=daily_fee,
                total_admission_fee=total_fee,
                discount_amount=discount,
                admission_fee_parts=num_parts,
                status='pending_payment',
            )

            group = f'admission-{admission.pk}'

            _create_part_payments(
                visit=visit,
                patient=visit.patient,
                accountant=visit.accountant,
                payment_type='admission',
                total_amount=total_fee,
                discount_amount=discount,
                num_parts=num_parts,
                part_amounts=part_amounts,
                obj_fk_name='admission',
                obj_instance=admission,
                payment_group=group,
            )

            # Create initial admission prescription if drugs provided
            if drug_ids:
                rx = AdmissionPrescription.objects.create(
                    admission=admission,
                    doctor=request.user,
                    notes='Initial admission medication',
                    status='active',
                )

                med_total = 0

                for drug_id, dosage, qty in zip(drug_ids, dosages, quantities):
                    try:
                        drug = Drug.objects.get(pk=drug_id)
                        qty_int = int(qty) if qty else 1
                        item_price = drug.price * qty_int

                        AdmissionPrescriptionItem.objects.create(
                            prescription=rx,
                            drug=drug,
                            drug_name=drug.name,
                            dosage=dosage,
                            quantity=qty_int,
                            price=item_price,
                        )

                        med_total += item_price

                    except Drug.DoesNotExist:
                        pass

                if med_total > 0:
                    Payment.objects.create(
                        visit=visit,
                        patient=visit.patient,
                        accountant=visit.accountant,
                        payment_type='admission_medication',
                        amount=med_total,
                        admission=admission,
                        payment_group=group,  # same group so it appears in same accountant tab
                    )

        return JsonResponse({'status': 'ok', 'admission_id': admission.pk})

    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def update_admission_payment(request, admission_id):
    """Doctor adds/modifies discount on an existing admission payment."""
    if request.method == 'POST' and request.user.role == 'doctor':
        from records.models import WardAdmission

        admission = get_object_or_404(
            WardAdmission,
            pk=admission_id,
            doctor=request.user
        )

        new_discount = float(
            request.POST.get('discount_amount', 0) or 0
        )

        with transaction.atomic():
            # Update unpaid admission payments with new discount spread
            unpaid = Payment.objects.filter(
                admission=admission,
                payment_type='admission',
                is_paid=False
            ).order_by('part_number')

            if unpaid.exists():
                count = unpaid.count()

                # Recalculate net amount per part from total_admission_fee and new discount
                net = float(admission.total_admission_fee) - float(new_discount)

                if net < 0:
                    net = 0

                base = round(net / count, 2)
                amounts = [base] * count

                diff = round(net - sum(amounts), 2)
                amounts[0] = round(amounts[0] + diff, 2)

                for p, amt in zip(unpaid, amounts):
                    p.amount = amt
                    p.save()

            admission.discount_amount = new_discount
            admission.save()

        return JsonResponse({'status': 'ok'})

    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def create_surgery(request, visit_id):
    """Doctor submits surgery consent form with optional drugs, labs, and part-payments."""
    if request.method == 'POST' and request.user.role == 'doctor':
        from records.models import Surgery, WardAdmission, SurgeryDrug, SurgeryLabTest
        visit = get_object_or_404(PatientVisit, pk=visit_id, doctor=request.user)
        d = request.POST

        # JS sends 'surg_parts', fallback to 'surgery_fee_parts' for ward dashboard
        num_parts = max(1, min(5, int(d.get('surg_parts') or d.get('surgery_fee_parts', 1) or 1)))
        surgery_fee = float(d.get('surgery_fee', 0) or 0)
        surgery_discount = float(d.get('surgery_discount_amount', 0) or 0)

        # Parse part amounts if doctor specified custom splits
        part_amounts_raw = d.getlist('surg_part_amounts[]')
        part_amounts = [float(x) for x in part_amounts_raw if x] if part_amounts_raw else []

        # Drug items — JS sends 'surg_drug_quantities[]'
        drug_ids = d.getlist('surg_drug_ids[]')
        drug_dosages = d.getlist('surg_drug_dosages[]')
        drug_qtys = d.getlist('surg_drug_quantities[]')  # fixed: was surg_drug_qtys[]

        # Lab test items — JS sends 'surg_test_ids[]'
        lab_test_ids = d.getlist('surg_test_ids[]')  # fixed: was surg_lab_ids[]

        with transaction.atomic():
            surgery = Surgery.objects.create(
                visit=visit, patient=visit.patient, doctor=request.user,
                procedure_name=d.get('procedure_name', ''),
                purpose_and_benefits=d.get('purpose_and_benefits', ''),
                known_risks=d.get('known_risks', ''),
                alternative_treatments=d.get('alternative_treatments', ''),
                anesthesia_type=d.get('anesthesia_type', ''),
                additional_procedures_auth=d.get('additional_procedures_auth') == '1',
                tissue_disposal_auth=d.get('tissue_disposal_auth') == '1',
                residents_involved=d.get('residents_involved') == '1',
                observers_permitted=d.get('observers_permitted') == '1',
                photography_permitted=d.get('photography_permitted') == '1',
                blood_transfusion_consent=d.get('blood_transfusion_consent', 'na'),
                financial_disclosure=d.get('financial_disclosure', ''),
                advance_directives=d.get('advance_directives', ''),
                postop_instructions=d.get('postop_instructions', ''),
                patient_acknowledged=d.get('patient_acknowledged') == '1',
                witness_name=d.get('witness_name', ''),
                surgery_fee=surgery_fee,
                surgery_fee_parts=num_parts,
                surgery_discount_amount=surgery_discount,
                admit_after_surgery=d.get('admit_after_surgery') == '1',
                status='draft',
            )

            # Add surgery drugs
            drug_total = 0
            for drug_id, dosage, qty in zip(drug_ids, drug_dosages, drug_qtys):
                try:
                    drug = Drug.objects.get(pk=drug_id)
                    qty_int = int(qty) if qty else 1
                    price = drug.price * qty_int
                    SurgeryDrug.objects.create(
                        surgery=surgery, drug=drug,
                        drug_name=drug.name, dosage=dosage,
                        quantity=qty_int, price_at_time=drug.price,
                    )
                    drug_total += price
                except Drug.DoesNotExist:
                    pass
            surgery.surgery_drug_total = drug_total

            # Add surgery lab tests
            lab_total = 0
            for test_id in lab_test_ids:
                try:
                    test = LabTest.objects.get(pk=test_id)
                    SurgeryLabTest.objects.create(
                        surgery=surgery, test=test,
                        test_name=test.name,
                        price_at_time=test.price,
                    )
                    lab_total += test.price
                except LabTest.DoesNotExist:
                    pass
            surgery.surgery_lab_total = lab_total
            surgery.save()

            # Post-op admission if requested
            if surgery.admit_after_surgery and d.get('ward'):
                ward = d.get('ward')
                bed_number = int(d.get('bed_number', 1))
                daily_fee = float(d.get('daily_ward_fee', 0) or 0)
                total_fee = float(d.get('total_admission_fee', 0) or 0)
                adm_discount = float(d.get('adm_discount') or d.get('adm_discount_amount', 0) or 0)
                adm_parts = max(1, min(5, int(d.get('adm_parts') or d.get('adm_fee_parts', 1) or 1)))
                adm_part_amounts_raw = d.getlist('adm_part_amounts[]')
                adm_part_amounts = [float(x) for x in adm_part_amounts_raw if x] if adm_part_amounts_raw else []

                admission = WardAdmission.objects.create(
                    visit=visit, patient=visit.patient, doctor=request.user,
                    ward=ward, bed_number=bed_number,
                    admission_reason=f'Post-surgery: {surgery.procedure_name}',
                    daily_ward_fee=daily_fee, total_admission_fee=total_fee,
                    discount_amount=adm_discount,
                    admission_fee_parts=adm_parts,
                    status='pending_payment',
                )
                surgery.admission = admission
                surgery.save()

        return JsonResponse({'status': 'ok', 'surgery_id': surgery.pk})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def update_surgery_discount(request, surgery_id):
    """Doctor modifies discount/parts on an existing surgery payment (before or after payment)."""
    if request.method == 'POST' and request.user.role == 'doctor':
        from records.models import Surgery
        surgery = get_object_or_404(Surgery, pk=surgery_id, doctor=request.user)
        new_discount = float(request.POST.get('surgery_discount_amount', 0) or 0)

        with transaction.atomic():
            # Update only unpaid surgery payments
            unpaid = Payment.objects.filter(
                surgery=surgery, payment_type='surgery', is_paid=False
            ).order_by('part_number')
            if unpaid.exists():
                count = unpaid.count()
                net = float(surgery.surgery_fee) - float(new_discount)
                if net < 0:
                    net = 0
                base = round(net / count, 2)
                amounts = [base] * count
                diff = round(net - sum(amounts), 2)
                amounts[0] = round(amounts[0] + diff, 2)
                for p, amt in zip(unpaid, amounts):
                    p.amount = amt
                    if p.amount < 0:
                        p.amount = 0
                    p.save()
            surgery.surgery_discount_amount = new_discount
            surgery.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def ward_occupancy(request):
    """Returns current bed occupancy per ward."""
    from records.models import Ward, WardAdmission

    result = {}

    wards = Ward.objects.all().order_by('name')

    for ward in wards:
        occupied_beds = list(
            WardAdmission.objects.filter(
                ward=ward,
                status__in=['paid', 'admitted']
            ).values_list('bed_number', flat=True)
        )

        result[str(ward.id)] = {
            'label': ward.name,
            'capacity': ward.capacity,
            'occupied': occupied_beds,
            'available': [
                b for b in range(1, ward.capacity + 1)
                if b not in occupied_beds
            ],
        }

    return JsonResponse(result)


@login_required
def print_lab_results(request, lr_id):
    from lab.models import LabRequest

    lr = get_object_or_404(LabRequest, pk=lr_id)
    return render(request, 'lab_results_print.html', {'lr': lr})


@login_required
def patient_review_surgery(request, surgery_id):
    """Patient reviews and signs the surgery consent form, then payments are created."""
    from records.models import Surgery
    if request.method == 'POST' and request.user.role == 'patient':
        # Use select_for_update inside atomic to prevent duplicate submissions
        with transaction.atomic():
            try:
                surgery = Surgery.objects.select_for_update().get(
                    pk=surgery_id, patient=request.user, status='draft'
                )
            except Surgery.DoesNotExist:
                return JsonResponse({'error': 'This consent form has already been submitted or does not exist.'}, status=400)
            d = request.POST
            surgery.patient_full_name_signed = d.get('patient_full_name_signed', '').strip()
            surgery.patient_questions = d.get('patient_questions', '')
            surgery.patient_understanding = d.get('patient_understanding') == '1'
            surgery.patient_voluntary = d.get('patient_voluntary') == '1'
            surgery.patient_acknowledged = d.get('patient_acknowledged') == '1'
            surgery.patient_signed_at = timezone.now()
            surgery.status = 'patient_reviewed'
            surgery.save()

        accountant = surgery.visit.accountant
        group = f'surgery-{surgery.pk}'

        with transaction.atomic():
            # Surgery fee: split into parts with discount
            if surgery.surgery_fee > 0:
                _create_part_payments(
                    visit=surgery.visit, patient=surgery.patient,
                    accountant=accountant,
                    payment_type='surgery',
                    total_amount=surgery.surgery_fee,
                    discount_amount=surgery.surgery_discount_amount,
                    num_parts=surgery.surgery_fee_parts,
                    part_amounts=[],
                    obj_fk_name='surgery', obj_instance=surgery,
                    payment_group=group,
                )

            # Surgery drugs: full payment (no parts, no discount)
            if surgery.surgery_drug_total > 0:
                # Create a Prescription for the surgery drugs so pharmacist can handle it
                from pharmacy.models import Prescription, PrescriptionDrug
                rx = Prescription.objects.create(
                    visit=surgery.visit,
                    patient=surgery.patient,
                    doctor=surgery.doctor,
                    accountant=accountant,
                    doctor_note=f'Surgery drugs for: {surgery.procedure_name}',
                    status='pending_payment',
                )
                for sd in surgery.surgery_drugs.select_related('drug'):
                    if sd.drug:
                        PrescriptionDrug.objects.create(
                            prescription=rx, drug=sd.drug,
                            dosage=sd.dosage, quantity=sd.quantity,
                            price_at_time=sd.price_at_time,
                        )
                rx.total_price = surgery.surgery_drug_total
                rx.save()
                Payment.objects.create(
                    visit=surgery.visit, patient=surgery.patient,
                    accountant=accountant,
                    payment_type='prescription',
                    amount=surgery.surgery_drug_total,
                    prescription=rx,
                    surgery=surgery,
                    payment_group=group,
                )

            # Surgery lab tests: full payment (no parts, no discount)
            if surgery.surgery_lab_total > 0:
                lr = LabRequest.objects.create(
                    visit=surgery.visit,
                    patient=surgery.patient,
                    doctor=surgery.doctor,
                    accountant=accountant,
                    doctor_note=f'Pre-surgery tests for: {surgery.procedure_name}',
                    status='pending_payment',
                )
                for sl in surgery.surgery_labs.select_related('test'):
                    if sl.test:
                        LabRequestTest.objects.create(
                            request=lr, test=sl.test,
                            price_at_time=sl.price_at_time,
                        )
                lr.total_price = surgery.surgery_lab_total
                lr.save()
                Payment.objects.create(
                    visit=surgery.visit, patient=surgery.patient,
                    accountant=accountant,
                    payment_type='lab',
                    amount=surgery.surgery_lab_total,
                    lab_request=lr,
                    surgery=surgery,
                    payment_group=group,
                )

            # Post-surgery admission: use SAME group as surgery so accountant sees one tab
            if surgery.admission:
                adm = surgery.admission
                if adm.total_admission_fee > 0:
                    _create_part_payments(
                        visit=surgery.visit, patient=surgery.patient,
                        accountant=accountant,
                        payment_type='admission',
                        total_amount=adm.total_admission_fee,
                        discount_amount=adm.discount_amount,
                        num_parts=adm.admission_fee_parts,
                        part_amounts=[],
                        obj_fk_name='admission', obj_instance=adm,
                        payment_group=group,  # same as surgery group: 'surgery-{surgery.pk}'
                    )

        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def drug_search(request):
    """Return ALL active drugs for client-side filtering. Cached 5 min."""
    from pharmacy.models import Drug
    from django.views.decorators.cache import cache_page
    drugs = Drug.objects.filter(is_active=True).order_by('name').values(
        'id', 'name', 'strength', 'dosage_form', 'price', 'is_injection'
    )
    results = [{
        'id': d['id'], 'name': d['name'], 'strength': d['strength'] or '',
        'form': d['dosage_form'] or '', 'price': float(d['price']),
        'is_injection': d['is_injection'],
    } for d in drugs]
    resp = JsonResponse({'results': results})
    resp['Cache-Control'] = 'private, max-age=300'
    return resp


@login_required
def lab_test_search(request):
    """Return ALL lab tests for client-side filtering. Cached 5 min."""
    tests = LabTest.objects.all().order_by('name').values('id', 'name', 'category', 'price')
    results = [{'id': t['id'], 'name': t['name'], 'category': t['category'] or '', 'price': float(t['price'])} for t in tests]
    resp = JsonResponse({'results': results})
    resp['Cache-Control'] = 'private, max-age=300'
    return resp


@login_required
def decline_surgery(request, surgery_id):
    """Patient declines a surgery consent form."""
    if request.method == 'POST' and request.user.role == 'patient':
        from records.models import Surgery
        surgery = get_object_or_404(Surgery, pk=surgery_id, patient=request.user, status='draft')
        surgery.status = 'cancelled'
        surgery.save()
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
def toggle_surgery_status(request, surgery_id):
    """Doctor toggles surgery status: pending → underway → ended."""
    if request.method == 'POST' and request.user.role == 'doctor':
        from records.models import Surgery
        surgery = get_object_or_404(Surgery, pk=surgery_id, doctor=request.user)
        transitions = {
            'pending': 'underway',
            'underway': 'ended',
        }
        new_status = transitions.get(surgery.status)
        if not new_status:
            return JsonResponse({'error': 'Cannot toggle from this status'}, status=400)
        surgery.status = new_status
        if new_status == 'underway':
            surgery.scheduled_at = timezone.now()
        elif new_status == 'ended':
            surgery.completed_at = timezone.now()
        surgery.save()
        return JsonResponse({'status': 'ok', 'new_status': new_status, 'display': surgery.get_status_display()})
    return JsonResponse({'error': 'forbidden'}, status=403)


@login_required
@require_POST
def ai_suggest(request):
    """
    Generate medication and laboratory suggestions from a doctor's
    clinical note using OpenRouter.
    """

    # Restrict access to doctors only
    if not _is_role(request.user, 'doctor'):
        return JsonResponse(
            {'error': 'Forbidden'},
            status=403
        )

    # Parse the JSON request payload
    try:
        body = json.loads(request.body)
        note = body.get('note', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse(
            {'error': 'Invalid request body'},
            status=400
        )

    # Prevent unnecessary AI calls for empty or very short notes
    if len(note) < 10:
        return JsonResponse(
            {'error': 'Clinical note is too short'},
            status=400
        )

    # Load the OpenRouter API key from Django settings
    api_key = getattr(settings, 'OPENROUTER_API_KEY', '')

    if not api_key:
        return JsonResponse(
            {'error': 'AI service is not configured'},
            status=503
        )

    model_name = "openai/gpt-4o-mini"

    # Instruct the model to return structured JSON only
    system_prompt = """
You are a Clinical Decision Support assistant helping hospital doctors.

Based on the doctor's clinical note:

1. Suggest appropriate medications.
2. Suggest appropriate laboratory investigations.
3. Provide brief clinical reasoning.

Return ONLY valid JSON:

{
  "drugs": [
    {
      "name": "Drug Name",
      "dosage": "Dose/Frequency",
      "reason": "Reason"
    }
  ],
  "labs": [
    {
      "name": "Test Name",
      "reason": "Reason"
    }
  ],
  "summary": "Short clinical reasoning"
}

Maximum 5 drugs and 5 laboratory tests.
"""

    # OpenRouter chat completion request payload
    payload = {
        "model": model_name,
        "temperature": 0.2,
        "max_tokens": 1000,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": f"Doctor's clinical note:\n\n{note}",
            },
        ],
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://hospital-mykw.onrender.com",
            "X-Title": "Rhudesi Hospital CDS",
        },
        method="POST",
    )

    try:
        # Submit request to OpenRouter
        with urllib.request.urlopen(req, timeout=30) as response:
            response_data = json.loads(
                response.read().decode("utf-8")
            )

        # Extract the model's response content
        choices = response_data.get("choices", [])

        if not choices:
            return JsonResponse(
                {'error': 'AI returned no response'},
                status=502
            )

        suggestion = (
            choices[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

        if not suggestion:
            return JsonResponse(
                {'error': 'AI returned an empty response'},
                status=502
            )

        return JsonResponse({
            'success': True,
            'suggestion': suggestion
        })

    # OpenRouter returned an HTTP error (4xx/5xx)
    except urllib.error.HTTPError as e:
        return JsonResponse(
            {
                'error': 'AI service request failed',
                'status': e.code
            },
            status=502
        )

    # Network issue, timeout, DNS failure, etc.
    except urllib.error.URLError:
        return JsonResponse(
            {
                'error': 'Unable to reach AI service'
            },
            status=502
        )

    # AI response could not be parsed as JSON
    except json.JSONDecodeError:
        return JsonResponse(
            {
                'error': 'Invalid response received from AI service'
            },
            status=502
        )

    # Catch any other unexpected server-side error
    except Exception:
        return JsonResponse(
            {
                'error': 'Unexpected server error'
            },
            status=500
        )