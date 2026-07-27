import pytest

from backend.app.core.config import get_settings
from backend.app.integrations.image.factory import get_image_provider
from backend.app.integrations.image.gateway_provider import GatewayImageProvider


@pytest.mark.parametrize(
    ("provider_name", "prefix", "model_name"),
    [
        ("jbb_grok", "JBB_GROK_IMAGE", "grok-imagine-image-quality"),
        ("jbb_gpt_image", "JBB_GPT_IMAGE", "gpt-image-2"),
        ("alita_gpt_image", "ALITA_GPT_IMAGE", "gpt-image-2"),
    ],
)
def test_jbb_image_provider_uses_its_own_openai_compatible_configuration(
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    prefix: str,
    model_name: str,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER", provider_name)
    monkeypatch.setenv(f"{prefix}_API_KEY", f"test-{provider_name}-key")
    monkeypatch.setenv(f"{prefix}_BASE_URL", "https://downstream.example.test/v1")
    monkeypatch.setenv(f"{prefix}_MODEL", model_name)
    monkeypatch.setenv(f"{prefix}_SIZE", "1024x1024")
    get_settings.cache_clear()

    try:
        provider = get_image_provider()
    finally:
        get_settings.cache_clear()

    assert isinstance(provider, GatewayImageProvider)
    assert provider.api_key == f"test-{provider_name}-key"
    assert provider.base_url == "https://downstream.example.test/v1"
    assert provider.model == model_name
    assert provider.provider_size == "1024x1024"
    assert provider.edit_enabled is False


@pytest.mark.parametrize(
    ("provider_name", "configured_field", "configured_model"),
    [
        ("jbb_grok", "jbb_grok_image_model", "grok-imagine-image-quality"),
        ("jbb_gpt_image", "jbb_gpt_image_model", "gpt-image-2"),
        ("dm_fox_gpt_image", "dm_fox_gpt_image_model", "gpt-image-2"),
        ("alita_gpt_image", "alita_gpt_image_model", "gpt-image-2"),
    ],
)
def test_selected_jbb_image_model_stays_with_its_dedicated_provider_configuration(
    provider_name: str,
    configured_field: str,
    configured_model: str,
) -> None:
    from backend.app.core.config import Settings
    from backend.app.services.model_selection import (
        effective_image_model,
        settings_for_image_model,
    )

    settings = Settings(
        image_provider=provider_name,
        **{configured_field: configured_model},
    )

    selected = settings_for_image_model(settings, "replacement-image-model")

    assert effective_image_model(selected) == "replacement-image-model"
