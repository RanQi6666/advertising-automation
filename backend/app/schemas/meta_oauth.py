from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class MetaOAuthAuthorizeUrlRead(BaseModel):
    authorization_url: str
    redirect_uri: str
    scopes: list[str] = Field(default_factory=list)
    state: str


class MetaAccountRead(TimestampedRead):
    name: str
    page_id: str | None = None
    page_name: str | None = None
    ad_account_id: str | None = None
    ad_account_name: str | None = None
    business_id: str | None = None
    business_name: str | None = None
    token_expires_at: datetime | None = None
    status: str
    access_token_configured: bool
    page_access_token_configured: bool
    available_pages: list[dict[str, Any]] = Field(default_factory=list)
    available_ad_accounts: list[dict[str, Any]] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class MetaOAuthCallbackResult(BaseModel):
    status: str
    accounts_created: int
    accounts_updated: int
    accounts: list[MetaAccountRead] = Field(default_factory=list)
