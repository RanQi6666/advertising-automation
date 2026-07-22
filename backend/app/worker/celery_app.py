from celery import Celery

from backend.app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "advertising_automation",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["backend.app.worker.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_default_queue="text_queue",
    timezone="Asia/Shanghai",
    beat_schedule={
        "recover-stale-generation-tasks": {
            "task": "generation_tasks.recover_stale",
            "schedule": settings.generation_task_recovery_interval_seconds,
        },
        "cleanup-expired-ad-research-results": {
            "task": "ad_research_jobs.cleanup_expired",
            "schedule": 15 * 60,
        },
    },
)
