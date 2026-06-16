from fastapi import APIRouter

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
api_router.include_router(health.router, tags=["health"])
api_router.include_router(ad_generation.router, tags=["ad-generation"])
api_router.include_router(campaigns.router, tags=["campaigns"])
api_router.include_router(work_orders.router, tags=["work-orders"])
api_router.include_router(landing_pages.router, tags=["landing-pages"])
api_router.include_router(topics.router, tags=["topics"])
api_router.include_router(copywriting.router, tags=["copywriting"])
api_router.include_router(creatives.router, tags=["creatives"])
api_router.include_router(videos.router, tags=["videos"])
api_router.include_router(reviews.router, tags=["reviews"])
api_router.include_router(legal.router, tags=["legal"])
