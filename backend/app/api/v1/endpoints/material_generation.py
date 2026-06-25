from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.app.api.deps import DbSession, require_material_generation_access_token
from backend.app.schemas.material_generation import (
    MATERIAL_CODE_VALIDATION_ERROR,
    MaterialCopyGenerateRequest,
    MaterialGenerationAPIError,
    MaterialGenerationEnvelope,
    MaterialImageGenerateRequest,
    MaterialVideoGenerateRequest,
)
from backend.app.services.material_generation_service import MaterialGenerationService

service = MaterialGenerationService()


class MaterialGenerationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except HTTPException as exc:
                if _is_external_error_body(exc.detail):
                    return JSONResponse(
                        status_code=exc.status_code,
                        content=exc.detail,
                        headers=exc.headers,
                    )
                raise
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": MATERIAL_CODE_VALIDATION_ERROR,
                        "message": "request validation failed",
                        "data": {"errors": jsonable_encoder(exc.errors())},
                    },
                )

        return custom_route_handler


router = APIRouter(
    prefix="/integrations/material-generation",
    dependencies=[Depends(require_material_generation_access_token)],
    route_class=MaterialGenerationRoute,
)


@router.post("/copy", response_model=MaterialGenerationEnvelope)
async def generate_copy(payload: MaterialCopyGenerateRequest, session: DbSession):
    try:
        return await service.generate_copy(session, payload)
    except MaterialGenerationAPIError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )


@router.post("/images", response_model=MaterialGenerationEnvelope)
async def generate_images(payload: MaterialImageGenerateRequest, session: DbSession):
    try:
        return await service.generate_images(session, payload)
    except MaterialGenerationAPIError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )


@router.post(
    "/videos",
    response_model=MaterialGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video(payload: MaterialVideoGenerateRequest, session: DbSession):
    try:
        result = await service.create_video(session, payload)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=result.model_dump(),
        )
    except MaterialGenerationAPIError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )


@router.get("/jobs/{job_id}", response_model=MaterialGenerationEnvelope)
async def get_job(job_id: str, session: DbSession):
    try:
        return await service.get_video_job(session, job_id)
    except MaterialGenerationAPIError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )


def _is_external_error_body(detail: object) -> bool:
    if not isinstance(detail, dict):
        return False
    return {"code", "message", "data"}.issubset(detail)
