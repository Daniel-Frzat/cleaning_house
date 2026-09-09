"""
إعدادات pytest المشتركة.

بيئة الاختبارات تستخدم DevConsoleSMSAdapter، لذا نُفعّل SMS_DEV_ALLOW_INSECURE
حتى لا يرفض الـadapter العمل عندما DEBUG=False أثناء الاختبارات.
"""

import pytest


@pytest.fixture(autouse=True)
def _allow_dev_sms_adapter(settings):
    settings.SMS_DEV_ALLOW_INSECURE = True
