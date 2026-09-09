"""
Accounts Models — Identity Domain (Phase 1 — Step 1)

يحتوي على User Model المخصص فقط.

يحتوي: User، OTPVerification، SocialAccount (Identity Domain — Phase 1).

قرارات محسومة مطبَّقة هنا:
  - phone هو المعرّف الأساسي لتسجيل الدخول (OTP-based auth) — لا يوجد username.
  - PropertyManager = CUSTOMER (Change Set — قسم 12)، لذلك الأدوار تبقى
    CUSTOMER / CONTRACTOR / ADMIN فقط (راجع roles.py).
"""

import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .roles import ConfirmedRole


class UserStatus(models.TextChoices):
    """حالة الحساب على مستوى دورة حياته (منفصلة عن is_active التقني)."""

    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    SUSPENDED = "SUSPENDED", "Suspended"


class UserManager(BaseUserManager):
    """
    Manager مخصص: phone هو المعرّف بدلاً من username.

    ملاحظة: المصادقة الفعلية تتم عبر OTP، لكن Django يتطلب حقل password
    (موروث من AbstractBaseUser). المستخدمون المُنشأون عبر create_user بدون
    كلمة مرور يحصلون على unusable password — وهو السلوك الصحيح لحسابات OTP.
    """

    use_in_migrations = True

    @staticmethod
    def normalize_phone(phone):
        """تطبيع بسيط: إزالة الفراغات الطرفية. لا تُطبَّق هنا أي قواعد ترقيم دولية."""
        return phone.strip() if isinstance(phone, str) else phone

    def _create_user(self, phone, email=None, password=None, **extra_fields):
        phone = self.normalize_phone(phone)
        if not phone:
            raise ValueError("The phone number must be set")

        # email اختياري، لكنه unique إن وُجد → الفراغ يُخزَّن كـNULL لتفادي
        # تعارض قيد التفرد بين عدة حسابات بلا بريد.
        email = self.normalize_email(email) if email else None

        user = self.model(phone=phone, email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password", "last_login"])
        user.save(using=self._db)
        return user

    def create_user(self, phone, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", ConfirmedRole.CUSTOMER)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone, email=email, password=password, **extra_fields)

    def create_superuser(self, phone, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", ConfirmedRole.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(phone, email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    المستخدم الأساسي للنظام — مشترك بين كل الأدوار.

    AUTH_USER_MODEL = "accounts.User"
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    phone = models.CharField(
        max_length=32,
        unique=True,
        verbose_name="Phone number",
        help_text="Primary login identifier (OTP-based auth).",
    )
    email = models.EmailField(
        max_length=254,
        unique=True,
        blank=True,
        null=True,
        help_text="Optional. Must be unique if provided.",
    )
    full_name = models.CharField(max_length=255, blank=True)

    role = models.CharField(
        max_length=16,
        choices=ConfirmedRole.choices,
        default=ConfirmedRole.CUSTOMER,
    )
    status = models.CharField(
        max_length=16,
        choices=UserStatus.choices,
        default=UserStatus.ACTIVE,
    )

    # is_active: بوابة تقنية لتسجيل الدخول (يفحصها Django/JWT).
    # status: حالة الحساب على مستوى العمل. الحقلان منفصلان عمدًا.
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(
        default=False,
        help_text="Designates whether the user can log into the Django admin site.",
    )

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    EMAIL_FIELD = "email"
    # phone مُستبعَد تلقائيًا (هو USERNAME_FIELD). لا نطلب أي حقل إضافي
    # في createsuperuser عدا كلمة المرور.
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-date_joined"]
        indexes = [
            models.Index(fields=["role"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.phone} ({self.get_role_display()})"

    def clean(self):
        super().clean()
        self.phone = UserManager.normalize_phone(self.phone)
        # يمنع تخزين "" في حقل unique قد يتكرر عبر عدة حسابات.
        if not self.email:
            self.email = None

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.phone


class OTPPurpose(models.TextChoices):
    """
    غرض الرمز. LOGIN فقط حاليًا — الحقل مصمم ليتوسع لاحقًا
    (تأكيد رقم، استعادة حساب، ...) دون تغيير المخطط.
    """

    LOGIN = "LOGIN", "Login"


class OTPStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    VERIFIED = "VERIFIED", "Verified"
    EXPIRED = "EXPIRED", "Expired"
    FAILED = "FAILED", "Failed"


class OTPVerification(models.Model):
    """
    سجل تحقق OTP واحد.

    🔒 لا يُخزَّن الرمز الخام إطلاقًا — فقط code_hash.

    ملاحظة معمارية: هذا الـModel يحمل بيانات وحالة فقط. منطق التوليد
    والتحقق يعيش في طبقة الخدمة (services/otp.py) وليس هنا.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # phone وليس FK إلى User: الرمز يُطلب قبل وجود حساب بالضرورة
    # (تسجيل دخول/تسجيل أول مرة عبر الهاتف).
    phone = models.CharField(max_length=32, db_index=True)

    code_hash = models.CharField(
        max_length=128,
        help_text="Hash of the OTP code. The raw code is never stored.",
    )

    purpose = models.CharField(
        max_length=32,
        choices=OTPPurpose.choices,
        default=OTPPurpose.LOGIN,
    )
    status = models.CharField(
        max_length=16,
        choices=OTPStatus.choices,
        default=OTPStatus.PENDING,
        db_index=True,
    )

    expires_at = models.DateTimeField()
    attempts_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(
        help_text="Snapshot of the policy value at creation time.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "OTP verification"
        verbose_name_plural = "OTP verifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["phone", "purpose", "status"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"OTP {self.purpose} for {self.phone} ({self.status})"

    def is_expired(self, now=None):
        return (now or timezone.now()) >= self.expires_at

    def attempts_exhausted(self):
        return self.attempts_count >= self.max_attempts


class SocialProvider(models.TextChoices):
    """
    مزوّدو تسجيل الدخول الاجتماعي المؤكدون في Phase 1
    (Sign in with Apple / Google — SOURCE-confirmed).
    """

    APPLE = "APPLE", "Apple"
    GOOGLE = "GOOGLE", "Google"


class SocialAccount(models.Model):
    """
    ربط بين حساب User داخلي وهوية لدى مزوّد خارجي.

    مفتاح الهوية هو (provider, provider_user_id) وليس البريد:
    البريد قد يتغير، وقد يُخفى (Apple Private Relay)، وقد لا يُرسَل إطلاقًا.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="social_accounts",
    )

    provider = models.CharField(max_length=16, choices=SocialProvider.choices)
    provider_user_id = models.CharField(
        max_length=255,
        help_text="Stable identifier at the provider (e.g. the OIDC 'sub' claim).",
    )

    # قد يختلف عن User.email، وقد يكون فارغًا (المزوّد لم يُرسله)
    email = models.EmailField(
        max_length=254,
        blank=True,
        null=True,
        help_text="Email as reported by the provider; may differ from User.email.",
    )

    raw_profile = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw provider payload, kept for debugging/audit.",
    )

    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Social account"
        verbose_name_plural = "Social accounts"
        ordering = ["-linked_at"]
        constraints = [
            # هوية المزوّد الواحدة لا تُربط بأكثر من حساب — يمنع ازدواج المستخدمين
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"],
                name="uniq_social_provider_identity",
            ),
            # حساب واحد لكل مزوّد لكل مستخدم
            models.UniqueConstraint(
                fields=["user", "provider"],
                name="uniq_social_user_provider",
            ),
        ]
        indexes = [
            models.Index(fields=["provider", "provider_user_id"]),
        ]

    def __str__(self):
        return f"{self.get_provider_display()} account for {self.user.phone}"
