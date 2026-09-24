"""
لوحة Django — موقع إدارة مخصّص بدخول على خطوتين.

    /admin/login/          البريد (أو الهاتف) + كلمة السر
    /admin/login/verify/   رمز SMS على هاتف الأدمن (+ "تذكّر هذا الجهاز")

📌 القواعد نفسها حرفيًا كموقع لوحة التحكم (apps/accounts/services/admin_auth.py):
   القفل بعد المحاولات الخاطئة، الجهاز الموثوق، منع غير ACTIVE.

🔒 إضافات اللوحة:
   - has_permission يشترط دور ADMIN وحالة ACTIVE (لا is_staff وحده).
   - حساب عليه must_change_password يُحوَّل إلى تغيير كلمة السر قبل أي صفحة.
   - تغيير كلمة السر يمر بالخدمة: يمسح العلم ويبطل الجلسات والأجهزة الأخرى.
"""

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth import REDIRECT_FIELD_NAME, update_session_auth_hash
from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import PasswordChangeForm
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils.http import url_has_allowed_host_and_scheme

DEVICE_COOKIE = "ch_admin_device"
SESSION_CHALLENGE = "admin_2fa_challenge"
SESSION_NEXT = "admin_2fa_next"


class AdminLoginForm(forms.Form):
    identifier = forms.CharField(label="Email or phone", max_length=254)
    password = forms.CharField(label="Password", widget=forms.PasswordInput, strip=False)


class AdminCodeForm(forms.Form):
    code = forms.CharField(label="SMS code", max_length=12)
    remember_device = forms.BooleanField(
        label="Trust this device (skip the SMS code on it for a while)", required=False
    )


class CleaningHouseAdminSite(admin.AdminSite):
    site_header = "Cleaning House — Administration"
    site_title = "Cleaning House Admin"
    index_title = "Operations"

    # ------------------------------------------------------------
    # الصلاحية
    # ------------------------------------------------------------
    def has_permission(self, request):
        from apps.accounts.models import UserStatus

        user = request.user
        return bool(
            user.is_active
            and user.is_staff
            and user.has_admin_access()
            and user.status == UserStatus.ACTIVE
        )

    def admin_view(self, view, cacheable=False):
        """يحوّل من عليه must_change_password إلى تغيير كلمة السر."""
        wrapped = super().admin_view(view, cacheable)

        def inner(request, *args, **kwargs):
            user = request.user
            if (
                user.is_authenticated
                and getattr(user, "must_change_password", False)
                and request.resolver_match
                and request.resolver_match.url_name
                not in ("password_change", "password_change_done", "logout", "login", "login_verify")
            ):
                return HttpResponseRedirect(reverse("admin:password_change", current_app=self.name))
            return wrapped(request, *args, **kwargs)

        inner.admin_site = self
        return inner

    def get_urls(self):
        return [
            path("login/verify/", self.login_verify, name="login_verify"),
        ] + super().get_urls()

    # ------------------------------------------------------------
    # الخطوة 1
    # ------------------------------------------------------------
    def _next_url(self, request):
        target = request.POST.get(REDIRECT_FIELD_NAME) or request.GET.get(REDIRECT_FIELD_NAME)
        if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return target
        return reverse("admin:index", current_app=self.name)

    def _finish(self, request, user, next_url, device_token=None):
        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.pop(SESSION_CHALLENGE, None)
        request.session.pop(SESSION_NEXT, None)
        response = HttpResponseRedirect(next_url)
        if device_token:
            response.set_cookie(
                DEVICE_COOKIE,
                device_token,
                max_age=getattr(settings, "ADMIN_TRUSTED_DEVICE_DAYS", 30) * 86400,
                httponly=True,
                secure=not settings.DEBUG,
                samesite="Lax",
                path="/admin/",
            )
        return response

    def login(self, request, extra_context=None):
        from apps.accounts.api.auth import _client_ip
        from apps.accounts.services import admin_auth
        from apps.accounts.services import otp as otp_service

        if request.method == "GET" and self.has_permission(request):
            return HttpResponseRedirect(reverse("admin:index", current_app=self.name))

        form = AdminLoginForm(request.POST or None)
        next_url = self._next_url(request)
        if request.method == "POST" and form.is_valid():
            try:
                result = admin_auth.begin_login(
                    form.cleaned_data["identifier"],
                    form.cleaned_data["password"],
                    device_token=request.COOKIES.get(DEVICE_COOKIE),
                    ip=_client_ip(request),
                )
            except admin_auth.AdminAuthError as exc:
                form.add_error(None, str(exc))
            except otp_service.OTPError as exc:
                form.add_error(None, f"The SMS code could not be sent ({exc.code}). Try again shortly.")
            else:
                if result["state"] == "authenticated":
                    return self._finish(request, result["user"], next_url)
                request.session[SESSION_CHALLENGE] = str(result["challenge"].id)
                request.session[SESSION_NEXT] = next_url
                messages.info(request, f"We sent a code to {result['phone_hint']}.")
                return HttpResponseRedirect(reverse("admin:login_verify", current_app=self.name))

        context = {
            **self.each_context(request),
            "title": "Log in",
            "form": form,
            "step": "password",
            REDIRECT_FIELD_NAME: next_url,
            **(extra_context or {}),
        }
        request.current_app = self.name
        return render(request, "admin/cleaning_house_login.html", context)

    # ------------------------------------------------------------
    # الخطوة 2
    # ------------------------------------------------------------
    def login_verify(self, request):
        from apps.accounts.api.auth import _client_ip
        from apps.accounts.services import admin_auth
        from apps.accounts.services import otp as otp_service

        challenge_id = request.session.get(SESSION_CHALLENGE)
        if not challenge_id:
            return HttpResponseRedirect(reverse("admin:login", current_app=self.name))

        form = AdminCodeForm(request.POST or None)
        if request.method == "POST" and "resend" in request.POST:
            try:
                result = admin_auth.resend_code(challenge_id, ip=_client_ip(request))
                messages.info(request, f"A new code was sent to {result['phone_hint']}.")
            except (admin_auth.AdminAuthError, otp_service.OTPError) as exc:
                messages.error(request, str(exc))
            return HttpResponseRedirect(reverse("admin:login_verify", current_app=self.name))

        if request.method == "POST" and form.is_valid():
            try:
                result = admin_auth.complete_login(
                    challenge_id,
                    form.cleaned_data["code"],
                    remember_device=form.cleaned_data["remember_device"],
                    device_label=("Django admin — " + request.META.get("HTTP_USER_AGENT", ""))[:255],
                    ip=_client_ip(request),
                )
            except admin_auth.ChallengeInvalidError as exc:
                request.session.pop(SESSION_CHALLENGE, None)
                messages.error(request, str(exc))
                return HttpResponseRedirect(reverse("admin:login", current_app=self.name))
            except (admin_auth.AdminAuthError, otp_service.OTPError) as exc:
                form.add_error("code", getattr(exc, "args", [str(exc)])[0] or "Invalid code.")
            else:
                next_url = request.session.get(SESSION_NEXT) or reverse("admin:index")
                return self._finish(request, result["user"], next_url, result["device_token"])

        context = {
            **self.each_context(request),
            "title": "Verification code",
            "form": form,
            "step": "code",
        }
        request.current_app = self.name
        return render(request, "admin/cleaning_house_login.html", context)

    # ------------------------------------------------------------
    # تغيير كلمة السر — عبر الخدمة
    # ------------------------------------------------------------
    def password_change(self, request, extra_context=None):
        from apps.accounts.services import admin_auth

        form = PasswordChangeForm(request.user, request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                admin_auth.change_own_password(
                    request.user,
                    form.cleaned_data["old_password"],
                    form.cleaned_data["new_password1"],
                    request=request,
                )
            except admin_auth.AdminAuthError as exc:
                form.add_error(None, str(exc))
            else:
                request.user.refresh_from_db()
                update_session_auth_hash(request, request.user)
                return HttpResponseRedirect(reverse("admin:password_change_done", current_app=self.name))

        context = {
            **self.each_context(request),
            "title": "Password change",
            "form": form,
            "form_url": "",
            **(extra_context or {}),
        }
        request.current_app = self.name
        return render(request, "registration/password_change_form.html", context)
