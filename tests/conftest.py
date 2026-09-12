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
