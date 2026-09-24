"""
Back-office — عدّادات المقاولين للوحة المؤشرات (قراءة فقط).
"""

from ..models import (
    AvailabilityStatus,
    BusinessRegistration,
    ContractorProfile,
    InsuranceDocument,
    VerificationStatus,
)


def summary_counts():
    """
    📌 available_now = ملفات AVAILABLE — لا يفحص الأهلية ولا حداثة الموقع؛
       هو ما أعلنه المقاولون عن أنفسهم، لا عدد من يصله عرض فعلًا.
    """
    return {
        "available_now": ContractorProfile.objects.filter(
            availability_status=AvailabilityStatus.AVAILABLE
        ).count(),
        "pending_business_registrations": BusinessRegistration.objects.filter(
            status=VerificationStatus.PENDING
        ).count(),
        "pending_insurance_documents": InsuranceDocument.objects.filter(
            status=VerificationStatus.PENDING
        ).count(),
    }
