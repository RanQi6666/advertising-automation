from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
)


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
async def generate_copy(payload: MaterialCopyGenerateRequest, _session: DbSession):
    try:
        _validate_base_payload(payload)
        return MaterialGenerationEnvelope(code=0, message="success", data={})
    except MaterialGenerationAPIError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )


def _validate_base_payload(payload: MaterialCopyGenerateRequest) -> None:
    if not (payload.product_name or "").strip():
        raise MaterialGenerationAPIError(
            "product_name is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )
    has_brief = bool((payload.brief or "").strip())
    has_selling_points = any(point.strip() for point in payload.selling_points)
    if not has_brief and not has_selling_points:
        raise MaterialGenerationAPIError(
            "brief or selling_points is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )


def _is_external_error_body(detail: object) -> bool:
    if not isinstance(detail, dict):
        return False
    return {"code", "message", "data"}.issubset(detail)
