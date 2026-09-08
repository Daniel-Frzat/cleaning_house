"""
GPS / Distance Data Adapter — Abstract Interface ONLY

🟢 Provider غير محسوم. لا تنفيذ فعلي هنا.

نطاق هذا الـAdapter محدد صراحة بالمرجع المعماري (§11):
    IN SCOPE: بيانات المسافة/Proximity لـDispatch
    OUT OF SCOPE: أي Turn-by-turn Navigation UI أو Routing engine مرئي
"""

from abc import ABC, abstractmethod


class BaseGPSDistanceAdapter(ABC):
    @abstractmethod
    def calculate_distance(self, origin, destination, *args, **kwargs):
        raise NotImplementedError("GPS/Distance Provider غير محسوم بعد")