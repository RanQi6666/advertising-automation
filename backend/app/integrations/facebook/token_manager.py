from backend.app.core.config import Settings, get_settings

FACEBOOK_PAGE_TOKEN_REF = "facebook_page_default"
FACEBOOK_AD_TOKEN_REF = "facebook_ad_default"


class FacebookTokenManager:
    """Resolve token references without storing raw tokens in publish jobs."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def resolve_token(self, access_token_ref: str | None) -> str | None:
        if not access_token_ref:
            return None
        if access_token_ref == FACEBOOK_PAGE_TOKEN_REF:
            return self.settings.facebook_page_access_token
        if access_token_ref == FACEBOOK_AD_TOKEN_REF:
            return self.settings.facebook_ad_access_token
        if access_token_ref.startswith("raw:"):
            return access_token_ref.removeprefix("raw:")
        return access_token_ref
