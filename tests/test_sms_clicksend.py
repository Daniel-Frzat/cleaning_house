"""
ClickSendAdapter Tests — SMS Adapter Layer (Change Set §21)

يغطي: مطابقة العقد، مسار النجاح، تمييز مسارات الفشل الثلاثة، الفشل المبكر
عند نقص الاعتماد، وأهم من ذلك: ألا يتسرّب الاعتماد إلى سجل أو استثناء.

⚠️ لا اتصال شبكيًا إطلاقًا: كل استدعاء لـrequests.post مُموّه. المشروع لا
   يستخدم مكتبة responses (غير مثبّتة)، والـadapters الوهمية الأخرى لا
   تُجري HTTP أصلًا فلا نمط تمويه سابقًا نتبعه — فنلتزم unittest.mock
   المستخدمة في باقي المجموعة.
"""

import logging
from unittest.mock import patch

import pytest
import requests
from django.core.exceptions import ImproperlyConfigured

from adapters.sms import get_sms_adapter
from adapters.sms.base import (
    BaseSMSAdapter,
    SMSAuthError,
    SMSDeliveryError,
    SMSRejectedError,
    SMSTransientError,
)
from adapters.sms.clicksend import ClickSendAdapter

# اعتماد وهمي مميّز الشكل — أي ظهور له في سجل أو استثناء يعني تسريبًا.
FAKE_USERNAME = "clicksend-user-CANARY"
FAKE_API_KEY = "SECRET-API-KEY-CANARY-0123456789"
FAKE_SENDER = "CleanHouse"

PHONE = "+96550001234"
CODE = "123456"

ADAPTER_PATH = "adapters.sms.clicksend.ClickSendAdapter"


@pytest.fixture
def clicksend_settings(settings):
    """يفعّل ClickSend كـadapter نشط باعتماد وهمي كامل."""
    settings.SMS_ADAPTER = ADAPTER_PATH
    settings.CLICKSEND_USERNAME = FAKE_USERNAME
    settings.CLICKSEND_API_KEY = FAKE_API_KEY
    settings.CLICKSEND_SENDER_ID = FAKE_SENDER
    return settings


class _Response:
    """أبسط بديل لكائن استجابة requests — رمز الحالة وحده يُقرأ."""

    def __init__(self, status_code, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data or {}

    def json(self):
        return self._json


# ============================================================
# 1) مطابقة العقد
# ============================================================
def test_adapter_implements_base_contract(clicksend_settings):
    """الإنشاء ينجح — أي ميثود مجرّدة غير منفَّذة كانت سترفع TypeError."""
    adapter = ClickSendAdapter()

    assert isinstance(adapter, BaseSMSAdapter)
    assert callable(adapter.send_otp)


def test_selector_returns_clicksend_without_code_change(clicksend_settings):
    """
    التبديل يتم بإعداد واحد: نفس get_sms_adapter() يعيد الآن ClickSend.

    📌 هذا ما يثبت أن طبقة الـDomain لا تتغيّر عند تبديل المزوّد.
    """
    assert isinstance(get_sms_adapter(), ClickSendAdapter)


def test_selector_still_returns_dev_console_by_default(settings):
    """التبديل ذهابًا وإيابًا — الافتراضي يبقى الـadapter التطويري."""
    from adapters.sms.dev_console import DevConsoleSMSAdapter

    settings.SMS_ADAPTER = "adapters.sms.dev_console.DevConsoleSMSAdapter"

    assert isinstance(get_sms_adapter(), DevConsoleSMSAdapter)


# ============================================================
# 2) مسار النجاح
# ============================================================
def test_successful_send_posts_expected_payload(clicksend_settings):
    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.return_value = _Response(200)

        result = ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert result["provider"] == "clicksend"
    assert result["delivered"] is True

    (url,), kwargs = mock_post.call_args
    assert url == "https://rest.clicksend.com/v3/sms/send"

    message = kwargs["json"]["messages"][0]
    assert message["to"] == PHONE
    assert message["from"] == FAKE_SENDER
    assert CODE in message["body"]

    # Basic Auth لا Bearer، ومهلة صريحة لا مفتوحة.
    assert kwargs["auth"] == (FAKE_USERNAME, FAKE_API_KEY)
    assert kwargs["timeout"] is not None
    assert "headers" not in kwargs or "Authorization" not in kwargs.get("headers", {})


# ============================================================
# 3) مسارات الفشل — كلٌّ بنوعه
# ============================================================
@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_raises_non_retryable(clicksend_settings, status):
    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.return_value = _Response(status)

        with pytest.raises(SMSAuthError) as excinfo:
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert excinfo.value.retryable is False
    # لم تُعَد المحاولة داخليًا: نداء واحد فقط.
    assert mock_post.call_count == 1


def test_timeout_raises_transient_error(clicksend_settings):
    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.side_effect = requests.Timeout("connection timed out")

        with pytest.raises(SMSTransientError) as excinfo:
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert excinfo.value.retryable is True
    # لا إعادة محاولة داخل الـAdapter — القرار للمستدعي.
    assert mock_post.call_count == 1


def test_connection_error_raises_transient_error(clicksend_settings):
    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError("dns failure")

        with pytest.raises(SMSTransientError):
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)


def test_server_error_is_retryable(clicksend_settings):
    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.return_value = _Response(503)

        with pytest.raises(SMSTransientError) as excinfo:
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert excinfo.value.retryable is True
    assert mock_post.call_count == 1


@pytest.mark.parametrize("status", [400, 404, 422])
def test_invalid_number_raises_non_retryable(clicksend_settings, status):
    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.return_value = _Response(
            status, text='{"response_msg": "Invalid recipient"}'
        )

        with pytest.raises(SMSRejectedError) as excinfo:
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert excinfo.value.retryable is False
    assert mock_post.call_count == 1


def test_all_failures_share_one_domain_base(clicksend_settings):
    """المستدعي يستطيع التقاط SMSDeliveryError وحدها دون معرفة المزوّد."""
    for exc_type in (SMSAuthError, SMSRejectedError, SMSTransientError):
        assert issubclass(exc_type, SMSDeliveryError)


# ============================================================
# 4) 🔒 الاعتماد لا يتسرّب — يُثبَت بالاختبار لا بالمراجعة
# ============================================================
@pytest.mark.parametrize(
    "side_effect, return_value",
    [
        (None, _Response(401)),
        (None, _Response(403)),
        (None, _Response(400, text=f"echo {FAKE_API_KEY}")),
        (None, _Response(500)),
        (requests.Timeout("timed out"), None),
        (requests.ConnectionError("refused"), None),
    ],
)
def test_credentials_never_leak_on_any_failure_path(
    clicksend_settings, caplog, side_effect, return_value
):
    """
    على كل مسار فشل: لا الاسم ولا المفتاح في نص الاستثناء، ولا في args،
    ولا في أي سطر سجل، ولا في سلسلة الاستثناءات المرتبطة (__cause__).
    """
    caplog.set_level(logging.DEBUG)

    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        if side_effect is not None:
            mock_post.side_effect = side_effect
        else:
            mock_post.return_value = return_value

        with pytest.raises(SMSDeliveryError) as excinfo:
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    haystacks = [str(excinfo.value), repr(excinfo.value.args)]

    # سلسلة الأسباب المرتبطة تظهر في التتبّع المطبوع — تُفحص أيضًا.
    cause = excinfo.value.__cause__
    while cause is not None:
        haystacks.append(str(cause))
        cause = cause.__cause__

    haystacks.extend(record.getMessage() for record in caplog.records)
    haystacks.append(caplog.text)

    for haystack in haystacks:
        assert FAKE_API_KEY not in haystack
        assert FAKE_USERNAME not in haystack


def test_raw_otp_code_never_logged_on_success(clicksend_settings, caplog):
    """🔒 الرمز الخام يمرّ عبر الـAdapter ولا يجوز أن يستقر في سجل."""
    caplog.set_level(logging.DEBUG)

    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.return_value = _Response(200, text=f"sent {CODE}")
        ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert CODE not in caplog.text
    assert FAKE_API_KEY not in caplog.text


def test_response_body_is_not_echoed_into_exception(clicksend_settings):
    """جسم الاستجابة قد يُعيد صدى الرسالة (أي الرمز) — لا يُدرَج في الاستثناء."""
    body = f"rejected: body={CODE} from={FAKE_SENDER} key={FAKE_API_KEY}"

    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        mock_post.return_value = _Response(400, text=body)

        with pytest.raises(SMSRejectedError) as excinfo:
            ClickSendAdapter().send_otp(phone_number=PHONE, code=CODE)

    assert CODE not in str(excinfo.value)
    assert body not in str(excinfo.value)


# ============================================================
# 5) فشل مبكر عند نقص الاعتماد
# ============================================================
@pytest.mark.parametrize(
    "missing_setting",
    ["CLICKSEND_USERNAME", "CLICKSEND_API_KEY", "CLICKSEND_SENDER_ID"],
)
def test_missing_credential_raises_configuration_error(
    clicksend_settings, missing_setting
):
    """خطأ إعداد واضح — لا AttributeError ولا KeyError خامًا."""
    setattr(clicksend_settings, missing_setting, "")

    with pytest.raises(ImproperlyConfigured) as excinfo:
        ClickSendAdapter()

    assert missing_setting in str(excinfo.value)


def test_missing_credential_fails_before_any_http_call(clicksend_settings):
    """الفشل يقع عند الإنشاء — لا إرسال باعتماد فارغ إطلاقًا."""
    clicksend_settings.CLICKSEND_API_KEY = ""

    with patch("adapters.sms.clicksend.requests.post") as mock_post:
        with pytest.raises(ImproperlyConfigured):
            get_sms_adapter()

    mock_post.assert_not_called()


def test_configuration_error_does_not_leak_present_credentials(clicksend_settings):
    """رسالة الإعداد تذكر أسماء الإعدادات الناقصة لا قيم الموجود منها."""
    clicksend_settings.CLICKSEND_API_KEY = ""

    with pytest.raises(ImproperlyConfigured) as excinfo:
        ClickSendAdapter()

    assert FAKE_USERNAME not in str(excinfo.value)


def test_whitespace_only_credential_is_treated_as_missing(clicksend_settings):
    """قيمة بيضاء ليست اعتمادًا — تُعامل كناقصة لا كموجودة."""
    clicksend_settings.CLICKSEND_API_KEY = "   "

    with pytest.raises(ImproperlyConfigured):
        ClickSendAdapter()
