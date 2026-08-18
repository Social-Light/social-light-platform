import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'socialmonitor.settings')

app = Celery('socialmonitor')

# All Celery settings live in Django settings under the CELERY_ namespace.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py modules in every installed app (e.g. monitor/tasks.py).
app.autodiscover_tasks()
