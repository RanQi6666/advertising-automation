from fastapi import APIRouter, Depends

from backend.app.api.deps import require_ai_ads_access_token
from backend.app.api.v1.endpoints import (
    ad_generation,
    ad_performance,
    campaigns,
    copywriting,
    creatives,
    external_ai_generation,
    external_image_generation,
    external_video_generation,
    generation_attempts,
    generation_tasks,
    health,
    landing_pages,
    legal,
    material_generation,
    model_options,
    operators,
    reviews,
    topics,
    videos,
    work_orders,
)

api_router = APIRouter()
protected_dependencies = [Depends(require_ai_ads_access_token)]

api_router.include_router(health.router, tags=["health"])
api_router.include_router(
    ad_generation.router,
    tags=["ad-generation"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    ad_performance.router,
    tags=["ad-performance"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    campaigns.router,
    tags=["campaigns"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    work_orders.router,
    tags=["work-orders"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    landing_pages.router,
    tags=["landing-pages"],
    dependencies=protected_dependencies,
)
api_router.include_router(topics.router, tags=["topics"], dependencies=protected_dependencies)
api_router.include_router(
    copywriting.router,
    tags=["copywriting"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    creatives.router,
    tags=["creatives"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    generation_attempts.router,
    tags=["generation-attempts"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    generation_tasks.router,
    tags=["generation-tasks"],
    dependencies=protected_dependencies,
)
api_router.include_router(videos.router, tags=["videos"], dependencies=protected_dependencies)
api_router.include_router(reviews.router, tags=["reviews"], dependencies=protected_dependencies)
api_router.include_router(legal.router, tags=["legal"], dependencies=protected_dependencies)
api_router.include_router(
    model_options.router,
    tags=["model-options"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    operators.router,
    tags=["operators"],
    dependencies=protected_dependencies,
)
api_router.include_router(
    material_generation.router,
    tags=["material-generation"],
)
api_router.include_router(
    external_ai_generation.router,
    tags=["ai-generation"],
)
api_router.include_router(
    external_image_generation.router,
    tags=["image-generation"],
)
api_router.include_router(
    external_video_generation.router,
    tags=["video-generation"],
)
