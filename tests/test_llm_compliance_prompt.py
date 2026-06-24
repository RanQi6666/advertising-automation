import inspect

import pytest

from backend.app.integrations.llm.compliance import (
    META_AD_COMPLIANCE_PROMPT_VERSION,
    META_AD_COMPLIANCE_SYSTEM_PROMPT,
    with_meta_ad_compliance,
)
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider
from backend.app.services.brand_safety_policy import BRAND_SAFETY_PROMPT_GUARDRAILS


def test_meta_ad_compliance_prompt_contains_core_guardrails() -> None:
    assert META_AD_COMPLIANCE_PROMPT_VERSION == "meta_ad_compliance.v1"
    assert "Do not try to bypass" in META_AD_COMPLIANCE_SYSTEM_PROMPT
    assert "personal attributes" in META_AD_COMPLIANCE_SYSTEM_PROMPT
    assert "unsupported" in META_AD_COMPLIANCE_SYSTEM_PROMPT
    assert "unlicensed third-party IP" in META_AD_COMPLIANCE_SYSTEM_PROMPT
    assert "landing page" in META_AD_COMPLIANCE_SYSTEM_PROMPT


def test_meta_ad_compliance_prompt_includes_brand_safety_guardrails() -> None:
    assert BRAND_SAFETY_PROMPT_GUARDRAILS in META_AD_COMPLIANCE_SYSTEM_PROMPT


def test_with_meta_ad_compliance_appends_guardrails_to_task_prompt() -> None:
    prompt = with_meta_ad_compliance("Generate ad topics.")

    assert prompt.startswith("Generate ad topics.")
    assert META_AD_COMPLIANCE_SYSTEM_PROMPT in prompt


@pytest.mark.parametrize(
    "method_name",
    [
        "generate_topics",
        "generate_copy",
        "revise_copy",
        "generate_image_briefs",
        "generate_video_storyboard",
    ],
)
def test_openai_content_generators_use_meta_ad_compliance(method_name: str) -> None:
    source = inspect.getsource(getattr(OpenAILLMProvider, method_name))

    assert "with_meta_ad_compliance" in source
