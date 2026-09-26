"""
أدوات مشتركة للاختبارات.

JPEG_BYTES: بايتات تبدأ بتوقيع JPEG حقيقي — رفع الصور يفحص المحتوى نفسه
            (magic bytes)، فالبايتات العشوائية تُرفض.
mark_paid:  دفعة عميل ناجحة لحجز مؤكَّد — بدء المهمة والدفع للمقاول
            مشروطان بها.
"""

from apps.payments.models import Payment, PaymentMethod, PaymentStatus

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def mark_paid(booking):
    """ينشئ دفعة SUCCEEDED بقيمة السعر المجمّد (دون المرور بالمزوّد)."""
    return Payment.objects.create(
        booking=booking,
        amount=booking.computed_price,
        method=PaymentMethod.CARD,
        status=PaymentStatus.SUCCEEDED,
        provider_reference="test-paid",
    )


def arrive(job, user):
    """يعلن وصول المقاول من موقع العقار نفسه (مرحلة ARRIVED — 2026-09-26)."""
    from decimal import Decimal

    from apps.bookings.services.distance import property_coordinates
    from apps.jobs.services import jobs as jobs_svc

    coords = property_coordinates(job.booking.property) or (Decimal("-33.8688"), Decimal("151.2093"))
    return jobs_svc.arrive_job(job, user, coords[0], coords[1])


def start_job_for_tests(job, user):
    """ASSIGNED → ARRIVED → IN_PROGRESS — لتجهيز مهمة جارية في الاختبارات."""
    from apps.jobs.services import jobs as jobs_svc

    if job.status == "ASSIGNED":
        arrive(job, user)
    return jobs_svc.start_job(job, user)
