"""
Back-office — العقارات (قراءة فقط، ADMIN).

⚠️ لا طفرات هنا: تعديل عقار أو عنوانه فعل يخص مالكه وحده.
"""

from django.db.models import Count, Q

from apps.audit.services.backoffice import assert_admin

from ..models import Property


class PropertyNotFoundError(Exception):
    code = "property_not_found"


def list_properties(actor, owner_id=None, state=None, is_active=None, q=None):
    """كل العقارات — الأحدث أولًا."""
    assert_admin(actor)

    qs = Property.objects.select_related("address", "owner").order_by("-created_at")
    if owner_id is not None:
        qs = qs.filter(owner_id=owner_id)
    if state:
        qs = qs.filter(address__state=state)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    if q:
        term = q.strip()
        qs = qs.filter(
            Q(address__suburb__icontains=term)
            | Q(address__postcode__icontains=term)
            | Q(address__street_address__icontains=term)
        )
    return qs


def get_property(actor, property_id):
    assert_admin(actor)

    prop = (
        Property.objects.select_related("address", "owner")
        .annotate(bookings_count=Count("bookings"))
        .filter(pk=property_id)
        .first()
    )
    if prop is None:
        raise PropertyNotFoundError("Property not found.")
    return prop
