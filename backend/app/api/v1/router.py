from fastapi import APIRouter, Depends

from backend.app.api.deps import require_ai_ads_access_token
from backend.app.api.v1.endpoints import (
    ad_generation,
    campaigns,
    copywriting,
    creatives,
    health,
    landing_pages,
    legal,
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
api_router.include_router(videos.router, tags=["videos"], dependencies=protected_dependencies)
api_router.include_router(reviews.router, tags=["reviews"], dependencies=protected_dependencies)
api_router.include_router(legal.router, tags=["legal"], dependencies=protected_dependencies)
