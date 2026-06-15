import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.models.client import Client
from backend.app.db.models.facebook_account import FacebookAccount

DEFAULT_CLIENT_NAME = "Meta OAuth Accounts"


class MetaOAuthService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _refresh_settings(self) -> None:
        cached_settings = get_settings()
        if self.settings is not cached_settings:
            return
        get_settings.cache_clear()
        self.settings = get_settings()

    def build_authorization_url(self, return_url: str | None = None) -> dict[str, Any]:
        self._refresh_settings()
        if not self.settings.facebook_app_id:
            raise ProviderError("FACEBOOK_APP_ID is required for Meta OAuth.")
        if not self.settings.facebook_app_secret:
            raise ProviderError("FACEBOOK_APP_SECRET is required for Meta OAuth.")

        scopes = _split_scopes(self.settings.facebook_oauth_scopes)
        state = self._sign_state(
            {
                "nonce": secrets.token_urlsafe(24),
                "created_at": int(datetime.now(UTC).timestamp()),
                "return_url": return_url or self.settings.facebook_oauth_success_redirect_url,
            }
        )
        params = {
            "client_id": self.settings.facebook_app_id,
            "redirect_uri": self.settings.facebook_oauth_redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(scopes),
        }
        if self.settings.facebook_login_config_id:
            params["config_id"] = self.settings.facebook_login_config_id

        version = self.settings.facebook_graph_api_version
        authorization_url = f"https://www.facebook.com/{version}/dialog/oauth?{urlencode(params)}"
        return {
            "authorization_url": authorization_url,
            "redirect_uri": self.settings.facebook_oauth_redirect_uri,
            "scopes": scopes,
            "state": state,
        }

    async def handle_callback(
        self,
        session: AsyncSession,
        code: str,
        state: str,
    ) -> dict[str, Any]:
        self._refresh_settings()
        state_payload = self._verify_state(state)
        short_lived = await self._exchange_code_for_token(code)
        long_lived = await self._exchange_for_long_lived_token(short_lived["access_token"])
        user_access_token = str(long_lived["access_token"])
        expires_at = _expires_at(long_lived.get("expires_in"))

        ad_accounts, pages, user_profile = await self._fetch_assets(user_access_token)
        default_client = await self._ensure_default_client(session)
        saved_accounts = []
        created = 0
        updated = 0
        default_page = pages[0] if pages else None
        available_ad_accounts = [_strip_tokens(account) for account in ad_accounts]
        page_access_token_refs = {
            str(page["id"]): f"raw:{page['access_token']}"
            for page in pages
            if page.get("id") and page.get("access_token")
        }

        for ad_account in ad_accounts:
            account_id = _normalize_ad_account_id(
                ad_account.get("id") or ad_account.get("account_id")
            )
            if not account_id:
                continue

            existing = await self._find_account(session, default_client.id, account_id)
            if existing:
                account = existing
                updated += 1
            else:
                account = FacebookAccount(client_id=default_client.id, name="")
                session.add(account)
                created += 1

            account.name = _account_name(ad_account, default_page)
            account.ad_account_id = account_id
            account.page_id = str(default_page["id"]) if default_page else None
            account.access_token_ref = f"raw:{user_access_token}"
            account.token_expires_at = expires_at
            account.status = "active"
            account.metadata_json = {
                **(account.metadata_json or {}),
                "auth_type": "oauth",
                "oauth_authorized_at": datetime.now(UTC).isoformat(),
                "oauth_return_url": state_payload.get("return_url"),
                "oauth_user": _strip_tokens(user_profile),
                "ad_account": _strip_tokens(ad_account),
                "business": _business_from_ad_account(ad_account),
                "page": _strip_tokens(default_page) if default_page else None,
                "available_pages": [_strip_tokens(page) for page in pages],
                "available_ad_accounts": available_ad_accounts,
                "page_access_token_ref": (
                    f"raw:{default_page['access_token']}"
                    if default_page and default_page.get("access_token")
                    else None
                ),
                "page_access_token_refs": page_access_token_refs,
            }
            saved_accounts.append(account)

        await session.commit()
        for account in saved_accounts:
            await session.refresh(account)

        return {
            "status": "connected",
            "accounts_created": created,
            "accounts_updated": updated,
            "accounts": [self.to_read_model(account) for account in saved_accounts],
            "return_url": state_payload.get("return_url"),
        }

    async def list_accounts(self, session: AsyncSession) -> list[dict[str, Any]]:
        result = await session.execute(
            select(FacebookAccount).order_by(FacebookAccount.created_at.desc())
        )
        return [self.to_read_model(account) for account in result.scalars().all()]

    def to_read_model(self, account: FacebookAccount) -> dict[str, Any]:
        metadata = account.metadata_json or {}
        ad_account = metadata.get("ad_account") or {}
        business = metadata.get("business") or {}
        page = metadata.get("page") or {}
        available_ad_accounts = metadata.get("available_ad_accounts") or []
        if not isinstance(available_ad_accounts, list):
            available_ad_accounts = []
        if not available_ad_accounts and ad_account:
            available_ad_accounts = [ad_account]
        page_access_token_refs = metadata.get("page_access_token_refs") or {}
        page_access_token_configured = bool(
            metadata.get("page_access_token_ref")
            or (
                isinstance(page_access_token_refs, dict)
                and account.page_id
                and page_access_token_refs.get(account.page_id)
            )
        )
        return {
            "id": account.id,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
            "name": account.name,
            "page_id": account.page_id,
            "page_name": page.get("name"),
            "ad_account_id": account.ad_account_id,
            "ad_account_name": ad_account.get("name"),
            "business_id": business.get("id"),
            "business_name": business.get("name"),
            "token_expires_at": account.token_expires_at,
            "status": account.status,
            "access_token_configured": bool(account.access_token_ref),
            "page_access_token_configured": page_access_token_configured,
            "available_pages": metadata.get("available_pages") or [],
            "available_ad_accounts": [
                _strip_tokens(item) for item in available_ad_accounts if isinstance(item, dict)
            ],
            "metadata_json": _public_metadata(metadata),
        }

    async def _exchange_code_for_token(self, code: str) -> dict[str, Any]:
        params = {
            "client_id": self.settings.facebook_app_id,
            "client_secret": self.settings.facebook_app_secret,
            "redirect_uri": self.settings.facebook_oauth_redirect_uri,
            "code": code,
        }
        return await self._graph_get("oauth/access_token", params)

    async def _exchange_for_long_lived_token(self, token: str) -> dict[str, Any]:
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.settings.facebook_app_id,
            "client_secret": self.settings.facebook_app_secret,
            "fb_exchange_token": token,
        }
        return await self._graph_get("oauth/access_token", params)

    async def _fetch_assets(
        self,
        access_token: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        user_response = await self._graph_get(
            "me",
            {
                "fields": "id,name",
                "access_token": access_token,
            },
        )
        ad_accounts_response = await self._graph_get(
            "me/adaccounts",
            {
                "fields": (
                    "id,account_id,name,account_status,currency,"
                    "timezone_name,business{id,name}"
                ),
                "limit": 100,
                "access_token": access_token,
            },
        )
        pages_response = await self._graph_get(
            "me/accounts",
            {
                "fields": "id,name,category,access_token,tasks",
                "limit": 100,
                "access_token": access_token,
            },
        )
        return ad_accounts_response.get("data", []), pages_response.get("data", []), user_response

    async def _graph_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        version = self.settings.facebook_graph_api_version
        url = f"{self.settings.facebook_graph_api_base_url}/{version}/{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, params=params)
        if response.is_error:
            raise ProviderError(f"Meta OAuth request failed: {response.text}")
        return response.json()

    async def _ensure_default_client(self, session: AsyncSession) -> Client:
        result = await session.execute(select(Client).where(Client.name == DEFAULT_CLIENT_NAME))
        client = result.scalar_one_or_none()
        if client:
            return client
        client = Client(
            name=DEFAULT_CLIENT_NAME,
            industry="advertising",
            notes="Default client bucket for Meta OAuth authorized accounts.",
            metadata_json={"system": True},
        )
        session.add(client)
        await session.flush()
        return client

    async def _find_account(
        self,
        session: AsyncSession,
        client_id: str,
        ad_account_id: str,
    ) -> FacebookAccount | None:
        result = await session.execute(
            select(FacebookAccount).where(
                FacebookAccount.client_id == client_id,
                FacebookAccount.ad_account_id == ad_account_id,
            )
        )
        return result.scalar_one_or_none()

    def _sign_state(self, payload: dict[str, Any]) -> str:
        raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        encoded_payload = base64.urlsafe_b64encode(raw_payload).decode().rstrip("=")
        signature = hmac.new(
            self.settings.secret_key.encode(),
            encoded_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded_payload}.{signature}"

    def _verify_state(self, state: str) -> dict[str, Any]:
        try:
            encoded_payload, signature = state.split(".", 1)
        except ValueError as exc:
            raise ProviderError("Invalid Meta OAuth state.") from exc
        expected = hmac.new(
            self.settings.secret_key.encode(),
            encoded_payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ProviderError("Invalid Meta OAuth state signature.")
        padded = encoded_payload + "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        created_at = int(payload.get("created_at") or 0)
        now = int(datetime.now(UTC).timestamp())
        if now - created_at > 3600:
            raise ProviderError("Meta OAuth state has expired.")
        return payload


def _split_scopes(scopes: str) -> list[str]:
    return [scope.strip() for scope in scopes.split(",") if scope.strip()]


def _expires_at(expires_in: Any) -> datetime | None:
    if not expires_in:
        return None
    try:
        return datetime.now(UTC) + timedelta(seconds=int(expires_in))
    except (TypeError, ValueError):
        return None


def _normalize_ad_account_id(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    return text if text.startswith("act_") else f"act_{text}"


def _account_name(ad_account: dict[str, Any], _page: dict[str, Any] | None) -> str:
    ad_name = ad_account.get("name") or _normalize_ad_account_id(ad_account.get("id"))
    return str(ad_name or "Meta Ad Account")


def _business_from_ad_account(ad_account: dict[str, Any]) -> dict[str, Any] | None:
    business = ad_account.get("business")
    if isinstance(business, dict):
        return {"id": business.get("id"), "name": business.get("name")}
    return None


def _strip_tokens(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {key: item for key, item in value.items() if "token" not in key.lower()}


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if "token" not in key.lower()
        and key not in {"available_pages", "available_ad_accounts"}
    }
