from fastapi import APIRouter

from backend.app.api.v1.endpoints import (
    campaigns,
    copywriting,
    creatives,
    health,
    insights,
    landing_pages,
    publishing,
    reviews,
    topics,
    videos,
    work_orders,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(campaigns.router, tags=["campaigns"])
api_router.include_router(work_orders.router, tags=["work-orders"])
api_router.include_router(landing_pages.router, tags=["landing-pages"])
api_router.include_router(topics.router, tags=["topics"])
api_router.include_router(copywriting.router, tags=["copywriting"])
api_router.include_router(creatives.router, tags=["creatives"])
api_router.include_router(videos.router, tags=["videos"])
api_router.include_router(reviews.router, tags=["reviews"])
api_router.include_router(publishing.router, tags=["publishing"])
api_router.include_router(insights.router, tags=["insights"])
