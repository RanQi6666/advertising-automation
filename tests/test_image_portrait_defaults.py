from backend.app.core.config import Settings
from backend.app.integrations.image.cpa_gemini_provider import CpaGeminiImageProvider
from backend.app.integrations.image.factory import get_image_provider
from backend.app.schemas.ai import ImageBrief
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.schemas.external_image_generation import ExternalImageGenerationCreate
from backend.app.schemas.material_generation import MaterialImageGenerateRequest


def test_image_generation_defaults_use_vertical_nine_by_sixteen() -> None:
    brief = ImageBrief(
        image_index=1,
        title="Portrait ad",
        short_text="Install now",
        visual_direction="Keep the subject centered.",
    )

    assert ExternalImageGenerationCreate(prompt="Create an ad.").size == "9:16"
    assert CreativeGenerateRequest(draft_id="draft-1").size == "9:16"
    assert MaterialImageGenerateRequest().size == "9:16"
    assert brief.size == "9:16"


def test_provider_defaults_map_portrait_ratio_to_each_upstream_protocol() -> None:
    settings = Settings()

    assert settings.model_gateway_image_size == "1024x1536"
    assert settings.jbb_grok_image_size == "1024x1536"
    assert settings.jbb_gpt_image_size == "1024x1536"
    assert settings.dm_fox_gpt_image_size == "1024x1536"
    assert settings.newcli_gemini_image_aspect_ratio == "9:16"
    assert settings.model_gateway_gemini_image_aspect_ratio == "9:16"
    assert settings.volcengine_image_size == "1152x2048"

def test_cpa_gemini_factory_passes_configured_portrait_ratio() -> None:
    provider = get_image_provider(
        Settings(
            image_provider="cpa_gemini",
            model_gateway_api_key="test-key",
            model_gateway_base_url="https://cpa.example/v1",
            model_gateway_gemini_image_model="gemini-3.1-flash-image",
            model_gateway_gemini_image_aspect_ratio="9:16",
        )
    )

    assert isinstance(provider, CpaGeminiImageProvider)
    assert provider.aspect_ratio == "9:16"