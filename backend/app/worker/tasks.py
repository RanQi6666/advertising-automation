import asyncio

from backend.app.services.ad_generation_service import AdGenerationService
from backend.app.services.generation_task_service import GenerationTaskService
from backend.app.worker.celery_app import celery_app


@celery_app.task(name="generation_tasks.process", ignore_result=True)
def process_generation_task(task_id: str) -> None:
    asyncio.run(GenerationTaskService().process_task(task_id))


@celery_app.task(name="ad_generation_jobs.process", ignore_result=True)
def process_ad_generation_job(job_id: str) -> None:
    asyncio.run(AdGenerationService().run_job(job_id))
