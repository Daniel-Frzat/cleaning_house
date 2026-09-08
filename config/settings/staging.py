"""Staging environment settings."""

from decouple import config

from .base import *  # noqa

DEBUG = False

# ------------------------------------------------------------
# Database — PostgreSQL (Staging Server) — Change Set قسم 4B
# ------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}