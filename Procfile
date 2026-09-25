# Railway يشغّل سطر web من هذا الملف (Config-as-code ملغاة في Railway ولا
# تعمل لهذه الخدمات — راجع سجل 2026-09-25).
#
# خدمة cleaning_house (الموقع):
#   - أمر التشغيل: سطر web أدناه (migrate ثم collectstatic ثم gunicorn)
#   - Settings > Deploy > Healthcheck Path = /api/health/ready
#
# خدمة cleaning_house-cron (المهام الدورية):
#   - Settings > Deploy > Custom Start Command = python manage.py run_periodic_tasks
#   - Settings > Deploy > Cron Schedule = */5 * * * *
#   - بلا Healthcheck وبلا Public Domain، ومتغيراتها نسخة من متغيرات الموقع
#
# سطر release خاص بـHeroku ولا ينفّذه Railway — لذلك migrate داخل سطر web.
release: python manage.py migrate --noinput
web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 60
