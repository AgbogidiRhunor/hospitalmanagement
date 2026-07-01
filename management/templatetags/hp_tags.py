from django import template
register = template.Library()

@register.filter
def currency(value):
    try:
        return f'₦{float(value):,.2f}'
    except:
        return value

@register.filter
def status_color(status):
    colors = {
        'pending_payment': '#f59e0b',
        'paid': '#10b981',
        'vitals': '#6366f1',
        'with_doctor': '#3b82f6',
        'lab_pending': '#f97316',
        'lab_paid': '#14b8a6',
        'lab_processing': '#8b5cf6',
        'rx_pending': '#f97316',
        'rx_paid': '#14b8a6',
        'pharmacy': '#06b6d4',
        'completed': '#22c55e',
        'pending': '#f59e0b',
        'dispensed': '#22c55e',
        'rejected': '#ef4444',
        'in_progress': '#8b5cf6',
    }
    return colors.get(status, '#6b7280')


@register.filter
def make_list(value):
    """Returns range(value) for use in templates."""
    try:
        return range(int(value))
    except (TypeError, ValueError):
        return []

@register.filter  
def split(value, delimiter=','):
    return value.split(delimiter)

@register.filter
def make_range(value):
    try:
        return range(1, int(value)+1)
    except (TypeError, ValueError):
        return []

@register.filter
def ward_filter(admissions, ward_key):
    """
    Return only admissions belonging to the given ward.
    ward_key is the Ward ID coming from ward_choices.
    """
    try:
        ward_id = int(ward_key)
    except (TypeError, ValueError):
        return []

    return [a for a in admissions if a.ward and a.ward.id == ward_id]

@register.filter
def age(dob):
    if not dob:
        return ''
    from django.utils import timezone
    today = timezone.now().date()
    return (today - dob).days // 365

@register.filter
def replace(value, args):
    try:
        old, new = args.split(',')
        return str(value).replace(old.strip(), new.strip())
    except:
        return value

@register.filter
def divide_by(value, arg):
    try:
        return float(value) / float(arg)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0

@register.filter
def unpaid_only(payments):
    """Filter a payment queryset/list to only unpaid ones."""
    try:
        return [p for p in payments if not p.is_paid]
    except Exception:
        return []

@register.filter
def add_days(value, days):
    """Add N days to a date/datetime value."""
    try:
        from datetime import timedelta
        return value + timedelta(days=int(days))
    except Exception:
        return value

import json

@register.filter
def jsonify_notes(notes_qs):
    """Serialise a DoctorNote queryset to a JSON array for openNote() calls."""
    try:
        result = [
            {
                'note': n.note,
                'created_at': n.created_at.strftime('%d %b %Y, %H:%M') if n.created_at else '',
            }
            for n in notes_qs
        ]
        return json.dumps(result)
    except Exception:
        return '[]'
