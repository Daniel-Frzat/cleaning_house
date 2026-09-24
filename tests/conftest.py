"""
إعدادات pytest المشتركة.

بيئة الاختبارات تستخدم adapters وهمية محميّة بصمّامات أمان ترفض العمل عند
DEBUG=False. نُفعّل تلك الصمّامات هنا صراحةً — وهو بالضبط الاستخدام المقصود
منها (بيئة اختبار معلومة)، لا التفاف عليها.
"""

import pytest


@pytest.fixture(autouse=True)
def _allow_dev_sms_adapter(settings):
    settings.SMS_DEV_ALLOW_INSECURE = True


@pytest.fixture(autouse=True)
def _allow_fake_payment_adapter(settings):
    """
    FakePaymentAdapter يرفض العمل عند DEBUG=False ما لم يُفعَّل هذا الخيار
    (نفس نمط DevConsoleSMSAdapter — Change Set §31).
    """
    settings.PAYMENTS_ALLOW_FAKE_ADAPTER = True


@pytest.fixture(autouse=True)
def _allow_fake_storage_adapter(settings):
    """
    FakeStorageAdapter يهمل محتوى الملفات، فيرفض العمل عند DEBUG=False ما
    لم يُفعَّل هذا الخيار (Infra §7).
    """
    settings.JOBS_ALLOW_FAKE_STORAGE_ADAPTER = True


@pytest.fixture(autouse=True)
def _allow_fake_payout_adapter(settings):
    """
    FakePayoutAdapter يرفض العمل عند DEBUG=False ما لم يُفعَّل هذا الخيار
    (نفس نمط FakePaymentAdapter — §43).
    """
    settings.PAYOUTS_ALLOW_FAKE_ADAPTER = True


# ============================================================
# أدوات مشتركة للتدفّق الجديد (§8، §12، §14)
# ============================================================
# 📌 تعيش هنا لا في كل ملف: الإسناد صار يتطلب موقعًا حديثًا من الهاتف،
#    وتأكيد الدفع صار شرطًا للإسناد. تكرار ذلك في كل ملف اختبار كان
#    سيجعل أي تغيير لاحق في التدفّق تعديلًا في ثلاثين موضعًا.


@pytest.fixture(autouse=True)
def _allow_fake_directions_adapter(settings):
    """
    FakeDirectionsAdapter يشتق الـETA من سرعة مفترضة، فيرفض العمل عند
    DEBUG=False ما لم يُفعَّل هذا الخيار (§9).
    """
    settings.DIRECTIONS_ALLOW_FAKE_ADAPTER = True


@pytest.fixture(autouse=True)
def _allow_haversine_fallback(settings):
    """
    بيئة الاختبار تسمح بالارتداد إلى haversine: لا مزوّد اتجاهات حقيقيًا،
    والاختبارات التي تعنيها سياسة الارتداد تضبطها بنفسها صراحةً.
    """
    settings.DISPATCH_ALLOW_HAVERSINE_FALLBACK = True


def set_contractor_location(profile, coords, recorded_at=None):
    """
    يضبط موقع المقاول الحالي — شرط أهليته للإسناد الفوري (§8).

    ⚠️ ليس عنوان العمل: ContractorProfile.latitude/longitude تبقى كما هي،
       وهذا موقع الهاتف الذي يقرأه الإسناد.

    📌 إحداثيات None تعني "مقاول بلا موقع" — لا يُنشأ له صف، فيسقط من
       الترشيح كما يقصد الاختبار. الكتابة بـNone كانت ستكسر قيد العمود.
    """
    from django.utils import timezone as _tz

    from apps.contractors.models import ContractorCurrentLocation

    if coords is None or coords[0] is None or coords[1] is None:
        return None

    return ContractorCurrentLocation.objects.update_or_create(
        contractor=profile,
        defaults={
            "latitude": coords[0],
            "longitude": coords[1],
            "recorded_at": recorded_at or _tz.now(),
        },
    )[0]


def pay_and_assign(booking):
    """
    يُكمل مسار الدفع حتى الإسناد — للاختبارات التي تحتاج حجزًا مُسنَدًا.

    📌 القبول وحده لم يعد يُسنِد (§12): الإسناد يقع بعد تأكيد الدفع.
    """
    from apps.payments.models import Payment
    from apps.payments.services import payments as payments_svc

    payment = Payment.objects.filter(booking=booking).first()
    if payment is None:
        payment = payments_svc.start_charge_for_booking(booking)

    payments_svc.confirm_payment(payment.id)
    booking.refresh_from_db()
    return booking
