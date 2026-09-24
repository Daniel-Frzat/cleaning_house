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
