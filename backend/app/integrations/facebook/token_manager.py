class FacebookTokenManager:
    """Token resolver placeholder.

    In production this should resolve encrypted tokens from a secret store instead of
    treating the reference as the token itself.
    """

    async def resolve_token(self, access_token_ref: str | None) -> str | None:
        return access_token_ref
