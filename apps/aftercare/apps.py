from django.apps import AppConfig


class AftercareConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.aftercare"
    verbose_name = "Aftercare (ratings, re-clean guarantee, invoices)"
