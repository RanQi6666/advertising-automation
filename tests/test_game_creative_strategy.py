import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.copywriting import CopyGenerateRequest
from backend.app.schemas.material_generation import MaterialCopyGenerateRequest
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.game_creative_strategy import build_game_creative_strategy
from backend.app.services.material_generation_service import MaterialGenerationService

PROMPT_FACING_GAJA_BANNED_TEXT = (
    "777",
    "Luck",
    "\u8d62\u94b1",
    "\u63d0\u73b0",
    "\u91d1\u5e01\u96e8",
    "\u8d4c\u573a\u684c\u9762",
)
FIXED_CARD_STYLE_TERMS = (
    "premium cards",
    "game cards",
    "card carousel",
    "fast carousel",
    "end card",
    "hero card",
    "jewel card",
    "game-card",
)


def test_gaja_landing_url_uses_brand_template() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "event_name": "first deposit",
            "country": "India",
            "brief": "Promote registration.",
        }
    )

    assert strategy is not None
    assert strategy["template_id"] == "gaja_brand"
    assert strategy["duration_seconds"] == 12
    assert strategy["brand"]["display_name"] == "GAJA"
    assert "GAJA wordmark" in " ".join(strategy["brand"]["real_page_signals"])
    assert "dark premium mobile game lobby" in " ".join(
        strategy["first_frame"]["visual_must_include"]
    )
    assert "metallic GAJA wordmark" in " ".join(
        strategy["first_frame"]["visual_must_include"]
    )
    assert "visible challenge setup" in " ".join(
        strategy["first_frame"]["visual_must_include"]
    )
    assert "reward unlock cue" in " ".join(strategy["last_frame"]["visual_must_include"])
    assert "Start" in " ".join(strategy["last_frame"]["cta_must_include"])
    assert "Play Now" in " ".join(strategy["last_frame"]["cta_must_include"])
    assert strategy["meta_restricted_game_ad_safe_mode"] is True
    assert "childlike puzzle blocks" in " ".join(strategy["negative_style_cues"])
    assert "restricted_review_props" in strategy["negative_style_cues"]
    assert "financial_prop_cues" in strategy["negative_style_cues"]
    assert "outcome_claim_cues" in strategy["negative_style_cues"]
    assert "0-2s" in " ".join(strategy["video_recipe"]["beats"])
    strategy_text = str(strategy).casefold()
    assert not any(term in strategy_text for term in FIXED_CARD_STYLE_TERMS)


def test_gaja_india_work_order_uses_country_epic_style_pack() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "\u5370\u5ea6",
            "event_name": "\u9996\u5145",
            "media": "fb",
            "audience": "\u5e74\u9f8418-65",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    assert strategy is not None
    style_pack = strategy["country_style_pack"]
    assert style_pack["country_code"] == "IN"
    assert style_pack["country_label"] == "India"
    style_text = " ".join(style_pack["style_cues"])
    assert "original Indian epic guardian" in style_text
    assert "mandala light geometry" in style_text
    assert "palace archways" in style_text
    assert "monsoon storm clouds" in style_text
    assert "gold cinematic rim light" in style_text

    prompt_facing_text = str(
        {
            "country_style_pack": strategy["country_style_pack"],
            "visual_concepts": strategy["visual_concepts"],
            "text_layout_rules": strategy["text_layout_rules"],
            "first_three_seconds": strategy["first_three_seconds"],
        }
    ).lower()
    for banned in (
        "ganesha",
        "prayer",
        "777",
        "recharge",
        "deposit",
        "casino",
        "cash",
        "coin",
        "slot",
        "jackpot",
    ):
        assert banned not in prompt_facing_text
    assert " om " not in f" {prompt_facing_text} "


def test_gaja_country_visual_concepts_provide_three_distinct_variants() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "India",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    assert strategy is not None
    concepts = strategy["visual_concepts"]
    assert [concept["variant_index"] for concept in concepts] == [1, 2, 3]
    assert {concept["concept_id"] for concept in concepts} == {
        "india_epic_guardian",
        "india_royal_portal",
        "india_mythic_neon_lobby",
    }
    assert len({concept["first_frame_visual"] for concept in concepts}) == 3
    assert len({concept["last_frame_visual"] for concept in concepts}) == 3
    for concept in concepts:
        concept_text = str(concept)
        assert "GAJA" in concept_text
        assert "777" not in concept_text


def test_gaja_text_layout_rules_allow_longer_safe_copy_without_overflow() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "India",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    assert strategy is not None
    layout = strategy["text_layout_rules"]
    assert layout["max_visible_text_layers"] == 3
    assert layout["safe_area_width_pct"] <= 86
    assert layout["top_bottom_margin_pct"] >= 10
    assert layout["auto_fit"] is True
    assert layout["line_limits"]["brand"] == 1
    assert layout["line_limits"]["headline"] <= 2
    assert layout["max_chars"]["headline"] >= 28
    assert layout["max_chars"]["subheadline"] >= 36
    assert layout["allowed_visible_text"] == [
        "GAJA",
        "Enter an Epic Game World",
        "Start Your Quest Now",
    ]
    assert "visible text hard ban" in layout["banned_visible_text_policy"]
    assert "brand-number text" in layout["banned_visible_text_policy"]
    assert "no overflow outside the image or video frame" in layout["layout_instruction"]


def test_gaja_country_style_pack_changes_with_work_order_country() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "\u7f8e\u56fd",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    assert strategy is not None
    style_pack = strategy["country_style_pack"]
    assert style_pack["country_code"] == "US"
    style_text = " ".join(style_pack["style_cues"]).lower()
    assert "cinematic urban skyline" in style_text
    assert "neon tech arena" in style_text
    assert "space-grade game portal" in style_text
    for india_only in ("mandala", "indian", "palace archways"):
        assert india_only not in style_text


def test_plain_gaja_url_does_not_use_builtin_landing_visual_reference() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    assert strategy is not None
    assert "landing_visual_reference" not in strategy


def test_gaja_strategy_uses_landing_visual_reference() -> None:
    visual_reference = {
        "source": "reference_image",
        "status": "analyzed",
        "palette": ["near-black navy background"],
        "surface_style": ["dark premium mobile game lobby"],
    }

    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "landing_page": {"extracted_data": {"visual_reference": visual_reference}},
        }
    )

    assert strategy is not None
    assert strategy["landing_visual_reference"] == visual_reference


def test_mini_game_pool_brief_overrides_gaja_brand_template() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "event_name": "registration",
            "country": "India",
            "brief": "小游戏流量池，先用小游戏玩法吸引点击，最后导到 GAJA 小游戏合集。",
        }
    )

    assert strategy is not None
    assert strategy["template_id"] == "mini_game_pool"
    assert "Color Match" in strategy["game_pool_examples"]
    assert "Bubble Pop" in strategy["game_pool_examples"]
    assert "small metallic GAJA corner logo" in " ".join(
        strategy["first_frame"]["visual_must_include"]
    )
    assert "metallic GAJA game hub" in " ".join(
        strategy["last_frame"]["visual_must_include"]
    )
    assert strategy["brand"]["display_name"] == "GAJA"


def test_mini_game_pool_detects_real_chinese_terms() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": (
                "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60\uff0c\u6700\u7ec8"
                "\u5bfc\u5230 GAJA \u91cc\u7684\u5c0f\u6e38\u620f\u5408\u96c6"
            ),
        }
    )

    assert strategy is not None
    assert strategy["template_id"] == "mini_game_pool"


def test_unrelated_product_has_no_game_strategy() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "Daily Planner",
            "landing_url": "https://example.com/planner",
            "brief": "Promote a productivity app.",
        }
    )

    assert strategy is None


def test_game_strategy_metadata_uses_prompt_safe_labels() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60",
        }
    )

    assert strategy is not None
    prompt_facing_strategy = {
        key: value
        for key, value in strategy.items()
        if key not in {"brand", "landing_visual_reference"}
    }
    strategy_text = str(prompt_facing_strategy)
    for banned in PROMPT_FACING_GAJA_BANNED_TEXT:
        assert banned not in strategy_text


def test_default_gaja_brand_strategy_avoids_treatment_wording() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "Brand ad for GAJA777.",
        }
    )

    assert strategy is not None
    assert "treatment" not in str(strategy).lower()


def test_default_gaja_brand_strategy_uses_low_text_safe_branding() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "Brand ad for GAJA777.",
        }
    )

    assert strategy is not None
    prompt_facing_strategy = {
        key: value for key, value in strategy.items() if key not in {"brand"}
    }
    prompt_text = str(prompt_facing_strategy)
    for banned in PROMPT_FACING_GAJA_BANNED_TEXT:
        assert banned not in prompt_text
    assert "GAJA wordmark" in prompt_text
    assert "metallic GAJA logo" in prompt_text
    assert "no visible brand-number text" in prompt_text
    assert "premium neon game lobby" in prompt_text


def test_gaja_brand_strategy_keeps_gaja_visible_without_numeric_suffix() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "Brand ad for GAJA777.",
        }
    )

    assert strategy is not None
    prompt_facing_strategy = {
        key: value for key, value in strategy.items() if key not in {"brand"}
    }
    prompt_text = str(prompt_facing_strategy)
    assert "GAJA" in prompt_text
    assert "GAJA777" not in prompt_text
    assert "777" not in prompt_text
    assert "metallic GAJA wordmark" in prompt_text
    assert "no visible numeric suffix" in prompt_text


def test_gaja_strategy_rejects_lookalike_domain() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "Daily Planner",
            "landing_url": "https://badgaja777.game/welcome",
            "brief": "Promote a productivity app.",
        }
    )

    assert strategy is None


def test_gaja_strategy_avoids_meta_gambling_review_triggers() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "event_name": "first_recharge",
            "media": "fb",
            "brief": "首充",
        }
    )

    assert strategy is not None
    strategy_text = str(strategy).lower()
    for risky_term in (
        "welcome bonus",
        "bonus",
        "ganesha",
        "dice",
        "aviator",
        "deposit",
        "recharge",
        "casino",
        "jackpot",
        "slot",
        "poker",
        "chip",
        "cash",
        "prize",
        "winning",
    ):
        assert risky_term not in strategy_text
    for risky_term in (
        "cash reward",
        "money reward",
        "reward amount",
        "value-return",
        "payment",
        "casino",
        "slot",
        "cash",
        "coin",
        "jackpot",
        "money",
        "recharge",
        "deposit",
        "winning",
    ):
        assert risky_term not in strategy_text
    assert "meta_restricted_game_ad_safe_mode" in strategy_text
    assert "dark premium mobile game lobby" in strategy_text
    assert "casual game hub" not in strategy_text
    assert "restricted review props" not in strategy_text
    assert "financial prop cues" not in strategy_text
    assert "outcome claim cues" not in strategy_text


async def _session_factory(tmp_path):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'game-strategy.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_material_context_persists_mini_game_strategy(tmp_path) -> None:
    engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            campaign, topic = await MaterialGenerationService()._create_context(
                session,
                MaterialCopyGenerateRequest(
                    external_request_id="req-1",
                    product_name="GAJA777",
                    landing_url="https://www.gaja777.game/#/?invite=YBG71118&register=true",
                    country="India",
                    event_name="registration",
                    brief="小游戏流量池，先用小游戏玩法吸引点击，最后导到 GAJA 小游戏合集。",
                ),
            )

        strategy = campaign.metadata_json["creative_strategy"]
        assert strategy["schema_version"] == "creative_strategy.v2"
        assert topic.source_data["creative_strategy"]["schema_version"] == "creative_strategy.v2"
        assert (
            topic.source_data["creative_strategy"]["schema_version"]
            == strategy["schema_version"]
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_copy_generation_inherits_campaign_creative_strategy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            campaign = Campaign(
                name="GAJA777",
                product_name="GAJA777",
                objective="first deposit",
                audience_description="India users",
                metadata_json={
                    "creative_strategy": build_game_creative_strategy(
                        {
                            "product_name": "GAJA777",
                            "landing_url": (
                                "https://www.gaja777.game/#/?invite=YBG71118&register=true"
                            ),
                            "brief": "Brand ad for first deposit.",
                        }
                    ),
                    "landing_page": {
                        "url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
                        "title": "GAJA777",
                    },
                },
            )
            session.add(campaign)
            await session.flush()
            topic = ContentTopic(
                campaign_id=campaign.id,
                title="GAJA777 launch",
                angle="Promote first deposit registration.",
                audience="India users",
                source_data={},
            )
            session.add(topic)
            await session.commit()

            draft = await CopywritingService().generate_copy(
                session,
                CopyGenerateRequest(topic_id=topic.id),
            )

        assert draft.metadata_json["creative_strategy"]["template_id"] == "gaja_brand"
    finally:
        get_settings.cache_clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_copy_generation_inherits_v2_creative_strategy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            strategy = {
                "schema_version": "creative_strategy.v2",
                "vertical": "ecommerce",
                "market_context": {"country_code": "SG", "language": "English"},
                "audience_lens": {"age_range": "25-34", "gender": "Female"},
                "copy_guidance": {"tone": ["efficient", "quality-led"]},
            }
            campaign = Campaign(
                name="Glow Serum",
                product_name="Glow Serum",
                objective="purchase",
                audience_description="Female 25-34",
                metadata_json={
                    "creative_strategy": strategy,
                    "landing_page": {
                        "url": "https://shop.example.sg",
                        "title": "Glow Serum",
                    },
                },
            )
            session.add(campaign)
            await session.flush()
            topic = ContentTopic(
                campaign_id=campaign.id,
                title="Busy-day skincare",
                angle="scenario_resonance: workday skincare routine",
                audience="Female 25-34",
                source_data={"creative_strategy": strategy},
            )
            session.add(topic)
            await session.commit()

            draft = await CopywritingService().generate_copy(
                session,
                CopyGenerateRequest(topic_id=topic.id),
            )

        assert draft.metadata_json["creative_strategy"]["schema_version"] == "creative_strategy.v2"
        assert draft.metadata_json["creative_strategy"]["vertical"] == "ecommerce"
    finally:
        get_settings.cache_clear()
        await engine.dispose()
