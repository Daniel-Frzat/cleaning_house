# Cleaning House — Backend

**الحالة:** Phase 0 (Foundation) — مكتمل · Identity Domain — ✅ مكتمل
(User، OTPVerification، SocialAccount، JWT Auth Endpoints — 77/77 اختبارًا ناجحًا)
**Architecture:** v1.1 (Frozen Baseline)
**Framework:** Django + Django Ninja + PostgreSQL

---

## ✅ ما هو موجود في Phase 0

- هيكل مشروع Django + Ninja كامل، مقسّم إلى Settings حسب البيئة (`dev` / `staging` / `production`)
- اتصال PostgreSQL عبر متغيرات بيئية (`.env`)
- JWT Auth مُفعّل بنيويًا (`django-ninja-jwt`) — بدون أي منطق أعمال
- هيكل أدوار (`apps/accounts/roles.py`) يحتوي فقط الأدوار المؤكدة: `Customer`, `Contractor`, `Admin`
- Celery + Redis مُعدّان مع Health-check Task واحد فقط
- Logging مركزي (Console + Rotating File)
- API Docs تلقائية عبر Django Ninja (`/api/docs`)
- Testing infra (`pytest` + `pytest-django`) مع اختبار Smoke واحد
- `adapters/` — طبقة Interfaces مجردة (Abstract) لكل تكامل خارجي، **بدون أي تنفيذ فعلي**

## ❌ ما هو غير موجود عمدًا (ولن يُضاف قبل حسم القرارات المرتبطة)

- **لا Domain Models فعلية.** `apps/accounts/models.py` فارغ عمدًا.
- **لا منطق أعمال (Business Logic) من أي نوع.**
- **لا تنفيذ فعلي لأي Provider خارجي** (Payment, SMS, Storage, Push, GPS, Address Validation, Business Registry) — فقط Interfaces مجردة في `adapters/`.
> ℹ️ الأدوار: `CUSTOMER` / `CONTRACTOR` / `ADMIN` — **نهائية ومؤكدة**.
> PropertyManager ليس دورًا مستقلًا: حُسم أنه نفس كيان `CUSTOMER`
> (راجع Change Set — قسم 12، محسوم بتاريخ 2026-09-09).

---

## 🚀 التشغيل المحلي (بدون Docker)

```bash
# 1) إنشاء بيئة افتراضية
python3 -m venv venv
source venv/bin/activate        # على Windows: venv\Scripts\activate

# 2) تثبيت الحزم
pip install -r requirements.txt

# 3) إعداد متغيرات البيئة
cp .env.example .env
# عدّل القيم داخل .env حسب بيئتك المحلية (خصوصًا DB_* و SECRET_KEY)

# 4) تأكد من تشغيل PostgreSQL و Redis محليًا
#    (يجب تثبيتهما بشكل منفصل على جهازك — غير مضمّنين هنا لأنك اخترت عدم استخدام Docker)

# 5) تطبيق الهجرات الأساسية (Django الافتراضية فقط — لا Domain migrations بعد)
python manage.py migrate

# 6) تشغيل الخادم
python manage.py runserver

# 7) (اختياري) تشغيل Celery Worker في نافذة طرفية منفصلة
celery -A config worker -l info
```

## 🧪 تشغيل الاختبارات

```bash
pytest
```

## 📖 API Docs

بعد تشغيل الخادم، افتح:
http://127.0.0.1:8000/api/docs


---

## 📌 الخطوة التالية (بعد اعتماد Phase 0)

بحسب "Recommended Implementation Order" (قسم 29 من المرجع المعماري):

> **الخطوة 2: Identity Domain + Roles** — ✅ مكتملة.
> قرار PropertyManager محسوم: PropertyManager = CUSTOMER، وليس دورًا
> مستقلًا (Change Set — قسم 12).

الأدوار المعتمدة نهائيًا: `CUSTOMER` / `CONTRACTOR` / `ADMIN`.

---

*هذا الملف يوثّق فقط نطاق Phase 0. راجع `Cleaning_House_Project_Reference.md` للـArchitecture الكامل، و`External_Requirements_Cleaning_House.md` لكل التكاملات الخارجية المعلّقة.*