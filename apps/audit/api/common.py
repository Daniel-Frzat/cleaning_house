"""
أدوات مشتركة لمسارات لوحة التحكم (/api/admin/*) — Back-office.

📌 كل قائمة إدارية جديدة تعيد الشكل نفسه:
       {"count": <إجمالي المطابق>, "items": [...]}
   وتقبل limit (افتراضي 50، أقصى 200) و offset.

⚠️ لا ORM هنا: الخدمات تعيد QuerySet مرشَّحًا، وهذا الملف يقطّعه فقط.
"""

from datetime import date
from typing import Optional

from ninja import Schema
from pydantic import Field

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class ErrorOut(Schema):
    """نفس شكل الخطأ الموحّد في بقية النطاقات."""

    code: str
    detail: str


class PageQuery(Schema):
    """ترقيم القوائم الإدارية."""

    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(0, ge=0)


class CreatedRangeQuery(PageQuery):
    """نطاق تاريخ الإنشاء — شامل للطرفين، بالتقويم المحلي للمنصة."""

    created_from: Optional[date] = None
    created_to: Optional[date] = None


def error(status, code, detail):
    return status, {"code": code, "detail": detail}


def forbidden(exc):
    """طبقة الخدمة رفضت الدور — نفس رمز AdminJWTAuth."""
    return error(403, "admin_required", str(exc))


def page(queryset, query, serialize):
    """يقطّع QuerySet ويعيد {"count", "items"} — العدّ قبل التقطيع."""
    count = queryset.count()
    rows = queryset[query.offset : query.offset + query.limit]
    return {"count": count, "items": [serialize(row) for row in rows]}
