from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.integrations.llm.language import build_target_language_context
from backend.app.integrations.llm.mock_provider import MockLLMProvider


def test_target_language_uses_country_from_work_order_signals() -> None:
    campaign = Campaign(name="India TV", metadata_json={})

    target_language = build_target_language_context(
        campaign=campaign,
        signals={
            "work_order": {
                "country": "印度",
                "parsed_fields": {"age_min": 25, "age_max": 45, "gender": "male"},
            }
        },
    )

    assert target_language["country_code"] == "IN"
    assert "Hindi" in target_language["label"]
    assert "Do not default to Chinese" not in target_language["instruction"]


def test_target_language_is_preserved_from_draft_metadata() -> None:
    target_language = build_target_language_context(
        draft_metadata={
            "target_language": {
                "label": "Hindi for broad India consumer ads",
                "instruction": "Use Hindi for broad India consumer ad hooks by default.",
                "country_code": "IN",
                "source": "country",
            }
        }
    )

    assert target_language["country_code"] == "IN"
    assert target_language["label"] == "Hindi for broad India consumer ads"


async def test_mock_topics_use_target_market_language_for_india() -> None:
    provider = MockLLMProvider()
    campaign = Campaign(
        id="campaign-1",
        name="India TV",
        product_name="India TV",
        audience_description="Male 25-45",
        metadata_json={},
    )

    topics = await provider.generate_topics(
        campaign=campaign,
        limit=1,
        signals={"work_order": {"country": "IN", "parsed_fields": {"country": "IN"}}},
    )

    assert topics[0].title != "India TV: Pain point hook"
    assert "देखने" in topics[0].title


async def test_mock_image_briefs_keep_visible_text_in_target_language() -> None:
    provider = MockLLMProvider()
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Hindi copy",
        headline="Hindi headline",
        version=1,
        metadata_json={
            "target_language": {
                "label": "Hindi for broad India consumer ads",
                "instruction": "Use Hindi for broad India consumer ad hooks by default.",
                "country_code": "IN",
                "source": "country",
            }
        },
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=1, size="1:1")

    assert briefs[0].title == "मुख्य फायदा"
