from backend.app.workers.celery_app import celery_app


@celery_app.task(name="backend.app.workers.tasks.generate_topics")
def generate_topics_task(campaign_id: str) -> dict[str, str]:
    return {"campaign_id": campaign_id, "status": "queued"}


@celery_app.task(name="backend.app.workers.tasks.publish_job")
def publish_job_task(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "status": "queued"}
