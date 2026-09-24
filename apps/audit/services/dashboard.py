"""
لوحة المؤشرات — تجمع عدّادات كل نطاق من خدمته هو.

📌 لا استعلام هنا: كل نطاق يعرّف summary_counts() في services/admin.py
   الخاصة به، وهذا الملف يركّبها فقط.
"""

from django.utils import timezone

from .backoffice import assert_admin


def dashboard_summary(actor):
    assert_admin(actor)

    from apps.accounts.services import backoffice_users
    from apps.bookings.services import admin as bookings_admin
    from apps.contractors.services import admin as contractors_admin
    from apps.payments.services import admin as payments_admin
    from apps.payouts.services import admin as payouts_admin
    from apps.support.services import admin as support_admin

    now = timezone.now()
    bookings = bookings_admin.summary_counts(now=now)
    payments = payments_admin.summary_counts(now=now)
    payouts = payouts_admin.summary_counts()
    contractors = contractors_admin.summary_counts()
    users = backoffice_users.summary_counts()
    support = support_admin.summary_counts()

    return {
        "generated_at": now,
        "bookings": bookings,
        "pending_verifications": {
            "business_registrations": contractors["pending_business_registrations"],
            "insurance_documents": contractors["pending_insurance_documents"],
            "total": contractors["pending_business_registrations"]
            + contractors["pending_insurance_documents"],
        },
        "open_support_requests": support["open"],
        "payments": {
            "needs_reconciliation": payments["needs_reconciliation"],
            "failed_last_30_days": payments["failed_last_30_days"],
        },
        "payouts": payouts,
        "gross_revenue": payments["gross_revenue"],
        "contractors_available_now": contractors["available_now"],
        "total_customers": users["customers"],
        "total_contractors": users["contractors"],
    }
