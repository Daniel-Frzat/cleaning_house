"""يجعل CleaningHouseAdminSite هو admin.site الافتراضي — فكل @admin.register يسجّل عليه."""

from django.contrib.admin.apps import AdminConfig


class CleaningHouseAdminConfig(AdminConfig):
    default_site = "config.admin_site.CleaningHouseAdminSite"
