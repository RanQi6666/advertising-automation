import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.copywriting import CopyGenerateRequest
from backend.app.schemas.material_generation import MaterialCopyGenerateRequest
from backend.app.services.brand_safety_policy import scan_brand_safety
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.game_creative_strategy import build_game_creative_strategy
from backend.app.services.material_generation_service import MaterialGenerationService


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
    assert "GAJA777" in strategy["brand"]["display_name"]
    assert "dark premium mobile game lobby" in " ".join(
        strategy["first_frame"]["visual_must_include"]
    )
    assert "premium game cards" in " ".join(strategy["first_frame"]["visual_must_include"])
    assert "Register" in " ".join(strategy["last_frame"]["cta_must_include"])
    assert strategy["meta_restricted_game_ad_safe_mode"] is True
    assert "childlike puzzle blocks" in " ".join(strategy["negative_style_cues"])
    assert "restricted_review_props" in strategy["negative_style_cues"]
    assert "financial_prop_cues" in strategy["negative_style_cues"]
    assert "outcome_claim_cues" in strategy["negative_style_cues"]
    assert "0-2s" in " ".join(strategy["video_recipe"]["beats"])


def test_plain_gaja_url_uses_domain_fallback_landing_visual_reference() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    assert strategy is not None
    assert strategy["landing_visual_reference"]["source"] == "domain_fallback"


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
    assert "light GAJA777 corner logo" in " ".join(
        strategy["first_frame"]["visual_must_include"]
    )
    assert "GAJA777 casual game hub" in " ".join(strategy["last_frame"]["visual_must_include"])


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


def test_game_strategy_metadata_is_brand_safety_neutral() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60",
        }
    )

    assert strategy is not None
    assert scan_brand_safety({"metadata_json": {"creative_strategy": strategy}})["status"] == (
        "passed"
    )


def test_default_gaja_brand_strategy_metadata_passes_brand_safety_scan() -> None:
    strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "Brand ad for GAJA777.",
        }
    )

    assert strategy is not None
    assert scan_brand_safety({"metadata_json": {"creative_strategy": strategy}})["status"] == (
        "passed"
    )
    assert "treatment" not in str(strategy).lower()


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
        "reward",
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
        assert strategy["template_id"] == "mini_game_pool"
        assert topic.source_data["creative_strategy"]["template_id"] == "mini_game_pool"
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
