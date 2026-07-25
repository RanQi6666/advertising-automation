from backend.app.core.config import Settings, get_settings
from backend.app.schemas.model_options import ModelOptionRead, ModelOptionsRead

VOLCENGINE_TEXT_OPTION_ID = "doubao-seed-2-0-pro"
VOLCENGINE_TEXT_OPTION_LABEL = "Doubao-Seed-2.0-pro"
VOLCENGINE_IMAGE_OPTION_ID = "doubao-seedream-4-5"
VOLCENGINE_IMAGE_OPTION_LABEL = "Doubao-Seedream-4.5"


def settings_for_text_model(settings: Settings, model_id: str | None) -> Settings:
    selected_model = _clean_model_id(model_id)
    if not selected_model:
        return settings

    if _is_volcengine_text_selection(settings, selected_model):
        return settings.model_copy(update={"llm_provider": "volcengine"})

    updates: dict[str, str] = {"llm_model": selected_model}
    if settings.llm_provider == "gateway":
        updates["model_gateway_text_model"] = selected_model
    elif settings.llm_provider == "volcengine":
        updates["volcengine_model"] = selected_model
    return settings.model_copy(update=updates)


def settings_for_image_model(settings: Settings, model_id: str | None) -> Settings:
    selected_model = _clean_model_id(model_id)
    if not selected_model:
        return settings

    if _is_volcengine_image_selection(settings, selected_model):
        return settings.model_copy(update={"image_provider": "volcengine"})

    updates: dict[str, str] = {}
    if settings.image_provider == "gateway":
        updates["model_gateway_image_model"] = selected_model
    elif settings.image_provider == "volcengine":
        updates["volcengine_image_model"] = selected_model
    elif settings.image_provider == "jbb_grok":
        updates["jbb_grok_image_model"] = selected_model
    elif settings.image_provider == "jbb_gpt_image":
        updates["jbb_gpt_image_model"] = selected_model
    return settings.model_copy(update=updates)


def effective_text_model(settings: Settings, model_id: str | None = None) -> str:
    selected_model = _clean_model_id(model_id)
    if selected_model:
        return selected_model
    if settings.llm_provider == "gateway":
        return settings.model_gateway_text_model or settings.llm_model
    if settings.llm_provider == "volcengine":
        return settings.volcengine_model
    return settings.llm_model


def effective_image_model(settings: Settings, model_id: str | None = None) -> str:
    selected_model = _clean_model_id(model_id)
    if selected_model:
        return selected_model
    if settings.image_provider == "gateway":
        return settings.model_gateway_image_model or ""
    if settings.image_provider == "volcengine":
        return settings.volcengine_image_model
    if settings.image_provider == "cpa_gemini":
        return settings.model_gateway_gemini_image_model or ""
    if settings.image_provider == "jbb_grok":
        return settings.jbb_grok_image_model or ""
    if settings.image_provider == "jbb_gpt_image":
        return settings.jbb_gpt_image_model or ""
    if settings.image_provider == "dm_fox_gpt_image":
        return settings.dm_fox_gpt_image_model or ""
    if settings.image_provider == "newcli_gemini":
        return settings.newcli_gemini_image_model or ""
    return settings.image_provider


def get_model_options(settings: Settings | None = None) -> ModelOptionsRead:
    settings = settings or get_settings()
    default_text_model = _default_text_option_id(settings)
    default_image_model = _default_image_option_id(settings)
    text_models = _dedupe_models(
        [*settings.model_gateway_text_models, _gateway_text_model(settings)]
    )
    image_models = _dedupe_models(
        [*settings.model_gateway_image_models, _gateway_image_model(settings)]
    )
    text_options = [
        ModelOptionRead(
            id=model,
            label=model,
            provider="gateway" if settings.model_gateway_base_url else settings.llm_provider,
            is_default=model == default_text_model,
        )
        for model in text_models
    ]
    image_options = [
        ModelOptionRead(
            id=model,
            label=model,
            provider="gateway" if settings.model_gateway_base_url else settings.image_provider,
            is_default=model == default_image_model,
        )
        for model in image_models
    ]
    text_options = _append_option(
        text_options,
        ModelOptionRead(
            id=VOLCENGINE_TEXT_OPTION_ID,
            label=VOLCENGINE_TEXT_OPTION_LABEL,
            provider="volcengine",
            is_default=default_text_model == VOLCENGINE_TEXT_OPTION_ID,
        ),
    )
    image_options = _append_option(
        image_options,
        ModelOptionRead(
            id=VOLCENGINE_IMAGE_OPTION_ID,
            label=VOLCENGINE_IMAGE_OPTION_LABEL,
            provider="volcengine",
            is_default=default_image_model == VOLCENGINE_IMAGE_OPTION_ID,
        ),
    )
    return ModelOptionsRead(
        text=text_options,
        image=image_options,
        defaults={
            "text": default_text_model or None,
            "image": default_image_model or None,
        },
    )


def _clean_model_id(model_id: str | None) -> str | None:
    if not model_id:
        return None
    cleaned = model_id.strip()
    return cleaned or None


def _default_text_option_id(settings: Settings) -> str:
    if settings.llm_provider == "volcengine":
        return VOLCENGINE_TEXT_OPTION_ID
    return effective_text_model(settings)


def _default_image_option_id(settings: Settings) -> str:
    if settings.image_provider == "volcengine":
        return VOLCENGINE_IMAGE_OPTION_ID
    return effective_image_model(settings)


def _gateway_text_model(settings: Settings) -> str:
    if settings.model_gateway_text_model:
        return settings.model_gateway_text_model
    if settings.llm_provider == "gateway":
        return settings.llm_model
    return ""


def _gateway_image_model(settings: Settings) -> str:
    if settings.model_gateway_image_model:
        return settings.model_gateway_image_model
    if settings.image_provider == "gateway":
        return effective_image_model(settings)
    return ""


def _is_volcengine_text_selection(settings: Settings, model_id: str) -> bool:
    return model_id in {VOLCENGINE_TEXT_OPTION_ID, settings.volcengine_model}


def _is_volcengine_image_selection(settings: Settings, model_id: str) -> bool:
    return model_id in {VOLCENGINE_IMAGE_OPTION_ID, settings.volcengine_image_model}


def _dedupe_models(models: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for model in models:
        cleaned = _clean_model_id(model)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _append_option(
    options: list[ModelOptionRead],
    option: ModelOptionRead,
) -> list[ModelOptionRead]:
    if any(existing.id == option.id for existing in options):
        return options
    return [*options, option]
