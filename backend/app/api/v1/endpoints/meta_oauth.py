from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from backend.app.api.deps import DbSession
from backend.app.schemas.meta_oauth import (
    MetaAccountRead,
    MetaOAuthAuthorizeUrlRead,
    MetaOAuthCallbackResult,
)
from backend.app.services.meta_oauth_service import MetaOAuthService

router = APIRouter()
service = MetaOAuthService()


@router.get("/meta-oauth/authorize-url", response_model=MetaOAuthAuthorizeUrlRead)
async def build_authorize_url(return_url: str | None = None):
    return service.build_authorization_url(return_url=return_url)


@router.get("/meta-oauth/callback", response_model=MetaOAuthCallbackResult)
async def meta_oauth_callback(
    session: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    as_json: bool = Query(default=False),
):
    if error:
        target = f"/?meta_oauth=error&reason={error}"
        if error_description:
            target = f"{target}&message={error_description}"
        return RedirectResponse(target)
    if not code or not state:
        return RedirectResponse("/?meta_oauth=error&reason=missing_code_or_state")

    result = await service.handle_callback(session, code=code, state=state)
    if as_json:
        return result

    return_url = str(result.get("return_url") or "/")
    separator = "&" if "?" in return_url else "?"
    url = (
        f"{return_url}{separator}meta_oauth=success"
        f"&created={result['accounts_created']}&updated={result['accounts_updated']}"
    )
    return RedirectResponse(url)


@router.get("/meta-oauth/accounts", response_model=list[MetaAccountRead])
async def list_meta_accounts(session: DbSession):
    return await service.list_accounts(session)
