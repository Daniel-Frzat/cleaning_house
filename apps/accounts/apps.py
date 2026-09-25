from django.apps import AppConfig
from django.core import checks


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Accounts (Identity Domain — skeleton only)"

    def ready(self):
        checks.register(otp_test_mode_check, checks.Tags.security, deploy=False)


def otp_test_mode_check(app_configs=None, **kwargs):
    """
    تحذير ظاهر في سجل كل نشر (migrate/check) ما دام وضع التجريب مفعّلًا
    خارج التطوير — حتى لا يُنسى قبل الإطلاق.
    """
    from django.conf import settings

    from .services.otp import test_mode_expired

    if settings.DEBUG or not getattr(settings, "OTP_TEST_NUMBERS", None) or test_mode_expired():
        return []
    admin = " (admin second factor included)" if getattr(settings, "OTP_TEST_NUMBERS_ALLOW_ADMIN", False) else ""
    until = getattr(settings, "OTP_TEST_MODE_UNTIL", "") or "no expiry date"
    return [
        checks.Warning(
            f"OTP test numbers are active{admin}: {len(settings.OTP_TEST_NUMBERS)} number(s), until {until}.",
            hint="Remove OTP_TEST_NUMBERS / OTP_TEST_NUMBERS_ALLOW_ADMIN before launch.",
            id="accounts.W001",
        )
    ]
