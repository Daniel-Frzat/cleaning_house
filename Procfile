# ⚠️ Railway يقرأ railway.json (preDeployCommand للـmigrations، startCommand،
#    فحص الجاهزية). هذا الملف احتياط لمنصات Heroku-style فقط.
release: python manage.py migrate --noinput
web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 60
# المهام الدورية: خدمة Railway Cron (railway.cron.json) تشغّل run_periodic_tasks.
# بديل Celery إن توفر Redis:
#   worker: celery -A config worker --loglevel=info
#   beat:   celery -A config beat --loglevel=info
