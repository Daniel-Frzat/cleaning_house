"""
Firebase Cloud Messaging — HTTP v1 API.

المصادقة بحساب خدمة (service account) من أحد مصدرين:
  - FIREBASE_CREDENTIALS_JSON: محتوى الملف كاملًا (الاستضافة)
  - FIREBASE_CREDENTIALS_FILE: مسار الملف (التطوير المحلي)

📌 لماذا google-auth + requests لا firebase-admin؟ نحتاج الإرسال وحده؛
   firebase-admin تجلب gRPC و Firestore و Storage. النتيجة واحدة بتبعيات
   أقل بكثير.

🔒 المفتاح لا يُسجَّل ولا يُعاد في أي خطأ. توكن الوصول (OAuth) يُخزَّن
   في الذاكرة ويُجدَّد تلقائيًا قبل انتهائه.
"""

import json
import logging
import threading

import requests
from django.conf import settings

from .base import BasePushNotificationAdapter, PushMessage, PushProviderError, PushResult

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
TIMEOUT_SECONDS = 10

# أخطاء FCM التي تعني أن التوكن ميت نهائيًا
INVALID_TOKEN_CODES = {"UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"}

_lock = threading.Lock()
_credentials = None


def _load_service_account():
    raw = getattr(settings, "FIREBASE_CREDENTIALS_JSON", "") or ""
    path = getattr(settings, "FIREBASE_CREDENTIALS_FILE", "") or ""
    if raw.strip():
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise PushProviderError("FIREBASE_CREDENTIALS_JSON is not valid JSON.") from exc
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as exc:
            raise PushProviderError("FIREBASE_CREDENTIALS_FILE could not be read.") from exc
    raise PushProviderError(
        "Firebase is not configured: set FIREBASE_CREDENTIALS_JSON or FIREBASE_CREDENTIALS_FILE."
    )


def _get_credentials():
    global _credentials
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    with _lock:
        if _credentials is None:
            info = _load_service_account()
            _credentials = service_account.Credentials.from_service_account_info(
                info, scopes=[FCM_SCOPE]
            )
        if not _credentials.valid:
            _credentials.refresh(Request())
        return _credentials


class FCMPushAdapter(BasePushNotificationAdapter):
    def __init__(self):
        self.credentials = _get_credentials()
        self.project_id = self.credentials.project_id
        if not self.project_id:
            raise PushProviderError("The service account has no project_id.")

    def _payload(self, token, message: PushMessage):
        android = {"priority": "HIGH" if message.priority == "high" else "NORMAL"}
        if message.android_channel_id:
            android["notification"] = {"channel_id": message.android_channel_id}
        apns_headers = {"apns-priority": "10" if message.priority == "high" else "5"}
        return {
            "message": {
                "token": token,
                "notification": {"title": message.title, "body": message.body},
                "data": {k: str(v) for k, v in (message.data or {}).items()},
                "android": android,
                "apns": {"headers": apns_headers, "payload": {"aps": {"sound": "default"}}},
            }
        }

    def send(self, tokens, message: PushMessage) -> PushResult:
        result = PushResult()
        url = FCM_URL.format(project_id=self.project_id)
        headers = {
            "Authorization": f"Bearer {_get_credentials().token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        session = requests.Session()
        for token in tokens:
            try:
                response = session.post(
                    url, headers=headers, json=self._payload(token, message), timeout=TIMEOUT_SECONDS
                )
            except requests.RequestException as exc:
                result.failed[token] = f"network: {type(exc).__name__}"
                continue

            if response.status_code == 200:
                result.delivered.append(token)
                continue

            code = _error_code(response)
            if code in INVALID_TOKEN_CODES:
                result.invalid.append(token)
            else:
                result.failed[token] = f"{response.status_code} {code}"
                if response.status_code in (401, 403):
                    logger.error("FCM rejected the credentials (HTTP %s)", response.status_code)
        return result


def _error_code(response):
    """
    أدق رمز خطأ متاح: FCM يضع UNREGISTERED في details، والحالة العامة
    (NOT_FOUND / INVALID_ARGUMENT) في error.status.
    """
    try:
        error = response.json().get("error", {})
    except ValueError:
        return "UNKNOWN"
    for detail in error.get("details", []) or []:
        code = detail.get("errorCode")
        if code:
            return code
    return error.get("status") or "UNKNOWN"
