# Full-Chain Creative Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a generic `creative_strategy.v2` layer that uses country, audience, landing page, vertical, and date context across topic, copy, image, storyboard, and video generation without changing external APIs.

**Architecture:** Add a deterministic internal strategy builder, wire it into existing campaign/topic/draft/asset/video metadata, and update LLM payload compaction and prompts to consume the new contract. Keep existing GAJA-specific helpers for backward-compatible tests and any legacy internals, but stop using them as the default strategy for new work-order and material-generation flows.

**Tech Stack:** FastAPI service layer, SQLAlchemy models with JSON metadata, Pydantic schemas, existing LLM provider abstraction, pytest, ruff.

## Global Constraints

- Do not modify external request or response schemas for publishing work orders, material generation, review callbacks, or video generation.
- Do not change authentication, route paths, query token names, callback payload shape, or return URL behavior.
- Do not require external systems to send new fields.
- Do not hard-code GAJA, mini-game, or any single brand as the default strategy.
- Generate three topics from different angles by default when `limit=3`.
- Use frontend-provided `duration_seconds` for storyboard/video structure; do not hard-code a 30-second script.
- Do not invent real-time local trends. Only use deterministic holiday/seasonal context unless a reliable trend source is explicitly added later.
- Keep Meta/Facebook compliance guardrails: audience traits may guide strategy internally, but generated user-facing copy must not directly assert sensitive or personal attributes.
- Preserve existing landing-page fetch failure behavior: generation should continue with URL/domain/work-order context.

---

## File Structure

- Create `backend/app/services/creative_strategy_builder.py`: builds `creative_strategy.v2`, classifies `game | ecommerce | unknown`, normalizes country/audience, creates angle plans, market calendar context, copy/image/video guidance, and compact summaries.
- Modify `backend/app/services/ad_generation_service.py`: replace default `build_game_creative_strategy()` usage with `build_creative_strategy()` while preserving external result payload shape.
- Modify `backend/app/services/material_generation_service.py`: use the generic builder for external material-generation contexts without changing API schemas.
- Modify `backend/app/services/topic_service.py`: use generic strategy in topic signals and store per-topic angle metadata in `source_data`.
- Modify `backend/app/schemas/ai.py`: add optional internal `angle_type` to `TopicCandidate`; do not expose it as a new top-level API field.
- Modify `backend/app/integrations/llm/openai_provider.py`: compact the v2 strategy keys and update prompt instructions for angle diversity, copy guidance, image guidance, and duration-adaptive video.
- Modify `backend/app/integrations/llm/mock_provider.py`: make deterministic local output follow `creative_strategy.v2`.
- Modify `backend/app/services/video_service.py`: update video prompt strategy summary to support v2 and keep legacy strategy summary fallback.
- Test `tests/test_creative_strategy_builder.py`: new unit tests for strategy construction.
- Test `tests/test_topic_context_slimming.py`: update/extend tests for v2 strategy, compact payload, and topic angle metadata.
- Test `tests/test_publishing_ad_generation.py`: update strategy persistence expectations and add external compatibility regression coverage.
- Test `tests/test_material_generation_integration.py`: assert material-generation API still accepts existing payloads and stores v2 strategy internally.
- Test `tests/test_video_storyboard.py`: add duration-adaptive and v2 prompt summary tests.
- Optional docs update only if implementation changes operator-facing documentation; do not edit external API docs unless behavior shown there changes.

---

### Task 1: Add Generic Creative Strategy Builder

**Files:**
- Create: `backend/app/services/creative_strategy_builder.py`
- Create: `tests/test_creative_strategy_builder.py`

**Interfaces:**
- Consumes: `Mapping[str, Any]` with existing internal context keys: `raw_content`, `structured_fields`, `reviewed_fields`, `product_name`, `campaign_name`, `audience_description`, `landing_url`, `landing_page`, `work_order`, `brief`, `event_name`, `country`, `media`.
- Produces: `build_creative_strategy(context: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]`.
- Produces: `compact_creative_strategy(value: Any) -> dict[str, Any] | None`.
- Produces: `CREATIVE_STRATEGY_SCHEMA_VERSION = "creative_strategy.v2"`.

- [ ] **Step 1: Write failing builder tests**

Add `tests/test_creative_strategy_builder.py`:

```python
from datetime import date

from backend.app.services.creative_strategy_builder import (
    CREATIVE_STRATEGY_SCHEMA_VERSION,
    build_creative_strategy,
    compact_creative_strategy,
)


def test_builds_singapore_ecommerce_strategy_for_female_25_34() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Glow Serum",
            "landing_url": "https://shop.example.sg/products/glow-serum",
            "country": "Singapore",
            "work_order": {
                "parsed_fields": {
                    "gender": "Female",
                    "age_min": 25,
                    "age_max": 34,
                    "audience_description_raw": "Female 25-34, office workers",
                }
            },
            "landing_page": {
                "title": "Glow Serum",
                "description": "Bright-looking skin for busy routines",
                "text_excerpt": "Shop now. 30% off. Daily skincare for humid weather.",
                "headings": ["Hydrating glow", "Fast routine", "Limited offer"],
            },
            "brief": "Promote skincare for office workers.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["schema_version"] == CREATIVE_STRATEGY_SCHEMA_VERSION
    assert strategy["vertical"] == "ecommerce"
    assert strategy["market_context"]["country_code"] == "SG"
    assert strategy["market_context"]["language"] == "English"
    assert strategy["market_context"]["buying_power"] == "high"
    assert strategy["audience_lens"]["gender"] == "Female"
    assert strategy["audience_lens"]["age_range"] == "25-34"
    assert "work pressure" in strategy["audience_lens"]["pain_points"]
    assert "quality of life" in strategy["audience_lens"]["buying_motivations"]
    assert [item["slot"] for item in strategy["topic_angle_plan"]] == [1, 2, 3]
    assert len({item["angle_type"] for item in strategy["topic_angle_plan"]}) == 3
    assert {item["angle_type"] for item in strategy["topic_angle_plan"]} <= {
        "pain_point",
        "scenario_resonance",
        "social_recommendation",
        "value_offer",
        "before_after_safe",
    }
    assert strategy["video_guidance"]["duration_adaptive"] is True
    assert "Do not claim guaranteed results." in strategy["compliance_guardrails"]


def test_builds_game_strategy_without_gaja_default_template() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Puzzle Quest",
            "landing_url": "https://play.example.com/level-challenge",
            "country": "US",
            "landing_page": {
                "title": "Puzzle level challenge",
                "text_excerpt": "Can you beat level 10? Play the puzzle challenge.",
            },
            "brief": "Make a game ad with a level challenge.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["schema_version"] == "creative_strategy.v2"
    assert strategy["vertical"] == "game"
    assert "template_id" not in strategy
    assert strategy["classification"]["confidence"] >= 0.65
    assert {item["angle_type"] for item in strategy["topic_angle_plan"]} == {
        "challenge_failure",
        "comeback_growth",
        "reward_burst",
    }
    assert "0-3s" not in str(strategy["video_guidance"])
    assert "duration_seconds" not in str(strategy["video_guidance"])


def test_unknown_vertical_uses_conservative_generic_plan() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Daily Planner",
            "landing_url": "https://example.com/info",
            "country": "Malaysia",
            "brief": "Promote a practical daily-use app.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["vertical"] == "unknown"
    assert strategy["market_context"]["country_code"] == "MY"
    assert strategy["topic_angle_plan"][0]["angle_type"] == "scenario_resonance"
    assert strategy["classification"]["confidence"] < 0.65


def test_nearby_holidays_are_deterministic_and_do_not_invent_trends() -> None:
    strategy = build_creative_strategy(
        {"product_name": "Shop", "country": "Singapore", "landing_url": "https://shop.sg"},
        today=date(2026, 7, 30),
    )

    holiday_names = [item["name"] for item in strategy["market_context"]["nearby_holidays"]]
    assert "National Day" in holiday_names
    assert strategy["market_context"]["local_trend_notes"] == []
    assert "Do not invent local trending topics." in strategy["compliance_guardrails"]


def test_compact_strategy_keeps_v2_fields_and_drops_large_unknown_blob() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Puzzle Quest",
            "landing_url": "https://play.example.com/game",
            "country": "US",
            "brief": "game challenge",
        },
        today=date(2026, 6, 28),
    )
    strategy["raw_content"] = "SHOULD NOT LEAK"
    compact = compact_creative_strategy(strategy)

    assert compact is not None
    assert compact["schema_version"] == "creative_strategy.v2"
    assert compact["vertical"] == "game"
    assert "topic_angle_plan" in compact
    assert "raw_content" not in compact
    assert "SHOULD NOT LEAK" not in str(compact)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_creative_strategy_builder.py -q`

Expected: FAIL because `backend.app.services.creative_strategy_builder` does not exist.

- [ ] **Step 3: Create the builder module**

Create `backend/app/services/creative_strategy_builder.py` with these public functions and deterministic data tables:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlparse

CREATIVE_STRATEGY_SCHEMA_VERSION = "creative_strategy.v2"

GAME_KEYWORDS = (
    "game",
    "play",
    "level",
    "challenge",
    "reward",
    "character",
    "battle",
    "puzzle",
    "quest",
    "runner",
    "mini game",
    "game lobby",
)
ECOMMERCE_KEYWORDS = (
    "shop",
    "cart",
    "price",
    "discount",
    "shipping",
    "cod",
    "product",
    "review",
    "before after",
    "skincare",
    "fitness",
    "serum",
    "limited offer",
)

COUNTRY_CONTEXT = {
    "SG": {
        "country": "Singapore",
        "language": "English",
        "buying_power": "high",
        "culture_notes": ["concise English", "quality and trust matter", "urban routine"],
        "religion_or_customs": ["multicultural market; avoid stereotypes"],
    },
    "US": {
        "country": "United States",
        "language": "English",
        "buying_power": "high",
        "culture_notes": ["direct benefit-led messaging", "clear proof and convenience"],
        "religion_or_customs": ["avoid political or identity assumptions"],
    },
    "IN": {
        "country": "India",
        "language": "Hindi or English",
        "buying_power": "value_sensitive",
        "culture_notes": ["value clarity", "mobile-first usage", "festival season sensitivity"],
        "religion_or_customs": ["avoid sacred symbols, rituals, or religious stereotypes"],
    },
    "ID": {
        "country": "Indonesia",
        "language": "Indonesian",
        "buying_power": "value_sensitive",
        "culture_notes": ["mobile-first", "promo-sensitive", "community proof"],
        "religion_or_customs": ["respect Ramadan and modest cultural cues"],
    },
    "MY": {
        "country": "Malaysia",
        "language": "Malay or English",
        "buying_power": "medium",
        "culture_notes": ["practical value", "trust", "multilingual context"],
        "religion_or_customs": ["respect Ramadan and halal/modesty sensitivities"],
    },
    "TH": {
        "country": "Thailand",
        "language": "Thai",
        "buying_power": "value_sensitive",
        "culture_notes": ["friendly tone", "visual clarity", "promo sensitivity"],
        "religion_or_customs": ["avoid religious imagery and royal references"],
    },
    "VN": {
        "country": "Vietnam",
        "language": "Vietnamese",
        "buying_power": "value_sensitive",
        "culture_notes": ["deal clarity", "fast mobile commerce", "practical benefits"],
        "religion_or_customs": ["avoid political and sensitive historical references"],
    },
    "BR": {
        "country": "Brazil",
        "language": "Brazilian Portuguese",
        "buying_power": "medium",
        "culture_notes": ["energetic tone", "social proof", "mobile-first"],
        "religion_or_customs": ["avoid stereotypes around region, race, or religion"],
    },
    "MX": {
        "country": "Mexico",
        "language": "Spanish for Mexico",
        "buying_power": "medium",
        "culture_notes": ["clear value", "family and daily-life scenes when relevant"],
        "religion_or_customs": ["avoid religious or cultural costume stereotypes"],
    },
}

COUNTRY_ALIASES = {
    "singapore": "SG",
    "sg": "SG",
    "新加坡": "SG",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "美国": "US",
    "india": "IN",
    "in": "IN",
    "印度": "IN",
    "indonesia": "ID",
    "id": "ID",
    "印尼": "ID",
    "malaysia": "MY",
    "my": "MY",
    "马来西亚": "MY",
    "thailand": "TH",
    "th": "TH",
    "泰国": "TH",
    "vietnam": "VN",
    "vn": "VN",
    "越南": "VN",
    "brazil": "BR",
    "br": "BR",
    "巴西": "BR",
    "mexico": "MX",
    "mx": "MX",
    "墨西哥": "MX",
}

HOLIDAYS = {
    "SG": [("National Day", (8, 9)), ("Singles' Day", (11, 11)), ("Christmas", (12, 25))],
    "US": [("Independence Day", (7, 4)), ("Black Friday", (11, 27)), ("Christmas", (12, 25))],
    "IN": [("Independence Day", (8, 15)), ("Diwali season", (11, 8))],
    "ID": [("Independence Day", (8, 17)), ("Singles' Day", (11, 11))],
    "MY": [("National Day", (8, 31)), ("Singles' Day", (11, 11))],
    "TH": [("Mother's Day", (8, 12)), ("Singles' Day", (11, 11))],
    "VN": [("National Day", (9, 2)), ("Singles' Day", (11, 11))],
    "BR": [("Independence Day", (9, 7)), ("Black Friday", (11, 27))],
    "MX": [("Independence Day", (9, 16)), ("Buen Fin season", (11, 15))],
}


def build_creative_strategy(context: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]:
    current_date = today or date.today()
    country_code = _country_code(context)
    market_context = _market_context(country_code, current_date)
    audience_lens = _audience_lens(context)
    vertical, classification = _classify_vertical(context)
    topic_angle_plan = _topic_angle_plan(vertical)
    return {
        "schema_version": CREATIVE_STRATEGY_SCHEMA_VERSION,
        "vertical": vertical,
        "classification": classification,
        "market_context": market_context,
        "audience_lens": audience_lens,
        "topic_angle_plan": topic_angle_plan,
        "copy_guidance": _copy_guidance(vertical, market_context, audience_lens),
        "image_guidance": _image_guidance(vertical),
        "video_guidance": _video_guidance(vertical),
        "compliance_guardrails": _compliance_guardrails(),
    }


def compact_creative_strategy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "schema_version",
        "vertical",
        "classification",
        "market_context",
        "audience_lens",
        "topic_angle_plan",
        "copy_guidance",
        "image_guidance",
        "video_guidance",
        "compliance_guardrails",
        # legacy keys remain allowed so older metadata still works downstream
        "template_id",
        "template_name",
        "duration_seconds",
        "aspect_ratio",
        "brand",
        "first_frame",
        "last_frame",
        "motion_direction",
        "landing_visual_reference",
        "negative_style_cues",
        "video_recipe",
        "country_style_pack",
        "visual_concepts",
        "text_layout_rules",
        "first_three_seconds",
    )
    compact = {key: value[key] for key in allowed if value.get(key) not in (None, "", [])}
    return compact or None
```

In the same file, add private helpers `_collect_text`, `_country_code`, `_market_context`, `_nearby_holidays`, `_audience_lens`, `_classify_vertical`, `_topic_angle_plan`, `_copy_guidance`, `_image_guidance`, `_video_guidance`, and `_compliance_guardrails`. Keep them deterministic; do not call an LLM or the network.

Implement `_topic_angle_plan()` exactly enough for this rollout:

```python
def _topic_angle_plan(vertical: str) -> list[dict[str, Any]]:
    if vertical == "game":
        return [
            {
                "slot": 1,
                "angle_type": "challenge_failure",
                "purpose": "Test whether failure and challenge hooks drive curiosity.",
                "avoid_repeating": ["comeback_growth", "reward_burst"],
            },
            {
                "slot": 2,
                "angle_type": "comeback_growth",
                "purpose": "Test weak-to-strong or wrong-to-right progression.",
                "avoid_repeating": ["challenge_failure", "reward_burst"],
            },
            {
                "slot": 3,
                "angle_type": "reward_burst",
                "purpose": "Test visual satisfaction, rewards, upgrades, and payoff.",
                "avoid_repeating": ["challenge_failure", "comeback_growth"],
            },
        ]
    if vertical == "ecommerce":
        return [
            {
                "slot": 1,
                "angle_type": "pain_point",
                "purpose": "Test whether the audience recognizes the problem.",
                "avoid_repeating": ["scenario_resonance", "value_offer"],
            },
            {
                "slot": 2,
                "angle_type": "scenario_resonance",
                "purpose": "Test whether a daily-life scene creates self-recognition.",
                "avoid_repeating": ["pain_point", "value_offer"],
            },
            {
                "slot": 3,
                "angle_type": "value_offer",
                "purpose": "Test value, offer, or proof without unsupported claims.",
                "avoid_repeating": ["pain_point", "scenario_resonance"],
            },
        ]
    return [
        {
            "slot": 1,
            "angle_type": "scenario_resonance",
            "purpose": "Test a practical daily-life use case.",
            "avoid_repeating": ["benefit_demo", "trust_builder"],
        },
        {
            "slot": 2,
            "angle_type": "benefit_demo",
            "purpose": "Test a clear product benefit demonstration.",
            "avoid_repeating": ["scenario_resonance", "trust_builder"],
        },
        {
            "slot": 3,
            "angle_type": "trust_builder",
            "purpose": "Test credibility and low-risk next step.",
            "avoid_repeating": ["scenario_resonance", "benefit_demo"],
        },
    ]
```

- [ ] **Step 4: Run builder tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_creative_strategy_builder.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git status --short
git add backend/app/services/creative_strategy_builder.py tests/test_creative_strategy_builder.py
git commit -m "feat: add generic creative strategy builder"
```

Before committing, confirm `AGENTS.md`, `.env`, `.env.production`, and `.env.production.bak.local-model-test` are not staged.

---

### Task 2: Wire Generic Strategy Into Work-Order and Material Context Creation

**Files:**
- Modify: `backend/app/services/ad_generation_service.py`
- Modify: `backend/app/services/material_generation_service.py`
- Modify: `tests/test_publishing_ad_generation.py`
- Modify: `tests/test_material_generation_integration.py`

**Interfaces:**
- Consumes: `build_creative_strategy(context: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]`.
- Produces: `campaign.metadata_json["creative_strategy"]["schema_version"] == "creative_strategy.v2"` for new work-order/material contexts.
- External API schemas remain unchanged.

- [ ] **Step 1: Write failing publishing work-order tests**

In `tests/test_publishing_ad_generation.py`, replace the old GAJA persistence expectation test with this v2-focused test:

```python
@pytest.mark.asyncio
async def test_publishing_ad_generation_persists_generic_creative_strategy() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-strategy-v2",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: puzzle game\n"
                        "Country: Singapore\n"
                        "Audience: Female 25-34\n"
                        "Event: traffic\n"
                        "Landing: https://play.example.sg/level-challenge\n"
                        "Brief: Make a level challenge game ad."
                    ),
                    delivery_extraction=_delivery_extraction(),
                    structured_fields={
                        "product_name": "Puzzle Quest",
                        "country": "Singapore",
                        "landing_url": "https://play.example.sg/level-challenge",
                        "audience_description_raw": "Female 25-34",
                    },
                ),
            ),
        )
        completed = await service.process_job(session, job.id)
        campaign = await session.get(
            Campaign,
            completed.result_payload["metadata_json"]["campaign_id"],
        )

    assert campaign is not None
    strategy = campaign.metadata_json["creative_strategy"]
    assert strategy["schema_version"] == "creative_strategy.v2"
    assert strategy["vertical"] == "game"
    assert strategy["market_context"]["country_code"] == "SG"
    assert len({item["angle_type"] for item in strategy["topic_angle_plan"]}) == 3
    assert completed.result_payload["metadata_json"]["creative_strategy"]["schema_version"] == (
        "creative_strategy.v2"
    )

    await engine.dispose()
```

Add a compatibility assertion to an existing external-flow test, not a new endpoint:

```python
assert set(result.keys()) >= {
    "job_id",
    "external_order_id",
    "status",
    "campaign_payload",
    "adset_payload",
    "creative_payload",
    "assets",
    "review",
    "metadata_json",
}
assert "creative_strategy" in result["metadata_json"]
```

- [ ] **Step 2: Write failing material-generation test**

In `tests/test_material_generation_integration.py`, add:

```python
@pytest.mark.asyncio
async def test_material_generation_context_uses_generic_creative_strategy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            campaign, topic = await MaterialGenerationService()._create_context(
                session,
                MaterialCopyGenerateRequest(
                    external_request_id="strategy-v2-material",
                    product_name="Glow Serum",
                    landing_url="https://shop.example.sg/products/glow-serum",
                    country="Singapore",
                    audience="Female 25-34",
                    event_name="purchase",
                    brief="Skincare for busy office workers.",
                    selling_points=["Fast routine", "Hydrating glow"],
                ),
            )

        strategy = campaign.metadata_json["creative_strategy"]
        assert strategy["schema_version"] == "creative_strategy.v2"
        assert strategy["vertical"] == "ecommerce"
        assert strategy["market_context"]["country_code"] == "SG"
        assert topic.source_data["creative_strategy"]["schema_version"] == "creative_strategy.v2"
    finally:
        get_settings.cache_clear()
        await engine.dispose()
```

If `_session_factory` does not exist in that test file, copy the helper shape already used in `tests/test_game_creative_strategy.py`.

- [ ] **Step 3: Run targeted tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_publishing_ad_generation.py::test_publishing_ad_generation_persists_generic_creative_strategy tests/test_material_generation_integration.py::test_material_generation_context_uses_generic_creative_strategy -q
```

Expected: FAIL because services still call `build_game_creative_strategy()`.

- [ ] **Step 4: Update service imports**

In `backend/app/services/ad_generation_service.py`, replace:

```python
from backend.app.services.game_creative_strategy import build_game_creative_strategy
```

with:

```python
from backend.app.services.creative_strategy_builder import build_creative_strategy
```

In `backend/app/services/material_generation_service.py`, make the same import replacement.

- [ ] **Step 5: Replace builder calls**

In `backend/app/services/ad_generation_service.py`, replace `creative_strategy = build_game_creative_strategy({...})` with:

```python
        creative_strategy = build_creative_strategy(
            {
                "raw_content": raw_content,
                "structured_fields": structured_fields,
                "reviewed_fields": reviewed_fields,
                "product_name": _structured_value(structured_fields, "product_name"),
                "project_name": _campaign_name(structured_fields, work_order.project_name),
                "landing_url": landing_url,
                "event_name": event_name,
                "country": country_value or work_order.country,
                "audience_description": _field_value(
                    reviewed_fields,
                    "audience_description_raw",
                ),
                "work_order": {
                    "raw_content": raw_content,
                    "parsed_fields": work_order.parsed_fields,
                    "country": work_order.country,
                    "media": work_order.media,
                    "landing_url": landing_url,
                    "report_timezone": work_order.report_timezone,
                },
            }
        )
```

In `backend/app/services/material_generation_service.py`, replace:

```python
        creative_strategy = build_game_creative_strategy(external_context)
```

with:

```python
        creative_strategy = build_creative_strategy(external_context)
```

- [ ] **Step 6: Run targeted tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_publishing_ad_generation.py::test_publishing_ad_generation_persists_generic_creative_strategy tests/test_material_generation_integration.py::test_material_generation_context_uses_generic_creative_strategy -q
```

Expected: PASS.

- [ ] **Step 7: Run compatibility tests for external surfaces**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_publishing_ad_generation.py tests/test_material_generation_integration.py -q
```

Expected: PASS. If a test fails only because it expected `template_id == "mini_game_pool"` in newly generated campaign metadata, update the assertion to `schema_version == "creative_strategy.v2"` and assert `vertical == "game"` instead. Do not change request schemas.

- [ ] **Step 8: Commit**

```powershell
git status --short
git add backend/app/services/ad_generation_service.py backend/app/services/material_generation_service.py tests/test_publishing_ad_generation.py tests/test_material_generation_integration.py
git commit -m "feat: use generic strategy for generated contexts"
```

Confirm no secrets or local env backup files are staged.

---

### Task 3: Add Topic Angle Metadata and Provider Payload Support

**Files:**
- Modify: `backend/app/schemas/ai.py`
- Modify: `backend/app/services/topic_service.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Modify: `tests/test_topic_context_slimming.py`

**Interfaces:**
- Consumes: `creative_strategy.topic_angle_plan`.
- Produces: each generated topic stores `source_data["topic_angle"]` when an angle-plan slot exists.
- Produces: LLM topic payload contains compact v2 strategy and requires angle diversity.

- [ ] **Step 1: Write failing topic-service test**

Add to `tests/test_topic_context_slimming.py`:

```python
def test_topic_source_data_records_angle_plan_slot() -> None:
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "game",
        "topic_angle_plan": [
            {
                "slot": 1,
                "angle_type": "challenge_failure",
                "purpose": "Test failure hook.",
                "avoid_repeating": ["reward_burst"],
            }
        ],
    }

    topic = TopicService()._topic_from_candidate(  # noqa: SLF001
        campaign_id="campaign-game",
        candidate=TopicCandidate(
            title="Can you beat level 10?",
            angle="challenge_failure: Show the failed attempt.",
            angle_type="challenge_failure",
            audience="game users",
            selling_points=["Fast challenge"],
            risk_notes="Avoid guarantees.",
            rationale="Uses slot 1.",
            score=0.9,
        ),
        signals={"creative_strategy": strategy},
        streamed=False,
        provider="mock",
        model="mock-model",
        angle_plan_item=strategy["topic_angle_plan"][0],
    )

    assert topic.source_data["topic_angle"]["slot"] == 1
    assert topic.source_data["topic_angle"]["angle_type"] == "challenge_failure"
```

Add to the OpenAI payload test:

```python
assert signals["creative_strategy"]["schema_version"] == "creative_strategy.v2"
assert "topic_angle_plan" in signals["creative_strategy"]
assert "raw_content" not in json.dumps(signals["creative_strategy"], ensure_ascii=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_topic_context_slimming.py::test_topic_source_data_records_angle_plan_slot tests/test_topic_context_slimming.py::test_openai_topic_generation_sends_compact_payload -q
```

Expected: FAIL because `TopicCandidate.angle_type` and `_topic_from_candidate(..., angle_plan_item=...)` do not exist.

- [ ] **Step 3: Add internal candidate field**

In `backend/app/schemas/ai.py`, change `TopicCandidate` to:

```python
class TopicCandidate(BaseModel):
    title: str
    angle: str
    angle_type: str | None = None
    audience: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    risk_notes: str | None = None
    rationale: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)
```

- [ ] **Step 4: Store angle slot metadata in topic service**

In `backend/app/services/topic_service.py`, import the new builder:

```python
from backend.app.services.creative_strategy_builder import (
    build_creative_strategy,
    compact_creative_strategy,
)
```

Replace the old `build_game_creative_strategy` import and old local `_compact_creative_strategy` usage.

Change the generate loop:

```python
        angle_plan = _topic_angle_plan(effective_signals)
        for index, candidate in enumerate(candidates):
            topic = self._topic_from_candidate(
                campaign_id=payload.campaign_id,
                candidate=candidate,
                signals=effective_signals,
                streamed=False,
                provider=llm_settings.llm_provider,
                model=model_name,
                angle_plan_item=_angle_plan_item(angle_plan, index),
            )
```

Change the stream loop similarly:

```python
        angle_plan = _topic_angle_plan(effective_signals)
        ...
                topic = self._topic_from_candidate(
                    campaign_id=payload.campaign_id,
                    candidate=candidate,
                    signals=effective_signals,
                    streamed=True,
                    provider=llm_settings.llm_provider,
                    model=model_name,
                    angle_plan_item=_angle_plan_item(angle_plan, generated_count - 1),
                )
```

Change `_topic_from_candidate` signature and `source_data`:

```python
    def _topic_from_candidate(
        self,
        campaign_id: str,
        candidate: TopicCandidate,
        signals: dict,
        streamed: bool,
        provider: str,
        model: str,
        angle_plan_item: dict | None = None,
    ) -> ContentTopic:
        source_data = {
            "signals": signals,
            "provider": provider,
            "model": model,
            "streamed": streamed,
            **(
                {"creative_strategy": signals["creative_strategy"]}
                if isinstance(signals.get("creative_strategy"), dict)
                else {}
            ),
            **({"topic_angle": angle_plan_item} if angle_plan_item else {}),
            **({"angle_type": candidate.angle_type} if candidate.angle_type else {}),
        }
        return ContentTopic(
            campaign_id=campaign_id,
            title=candidate.title,
            angle=candidate.angle,
            audience=candidate.audience,
            selling_points=candidate.selling_points,
            risk_notes=candidate.risk_notes,
            rationale=candidate.rationale,
            score=candidate.score,
            source_data=source_data,
        )
```

Add helpers:

```python
def _topic_angle_plan(signals: dict) -> list[dict]:
    strategy = signals.get("creative_strategy")
    if not isinstance(strategy, dict):
        return []
    plan = strategy.get("topic_angle_plan")
    return [item for item in plan if isinstance(item, dict)] if isinstance(plan, list) else []


def _angle_plan_item(plan: list[dict], index: int) -> dict | None:
    if index < 0 or index >= len(plan):
        return None
    return plan[index]
```

- [ ] **Step 5: Replace topic strategy construction**

In `_topic_creative_strategy`, replace the call to `build_game_creative_strategy(...)` with:

```python
    strategy = build_creative_strategy(
        {
            "product_name": campaign.product_name,
            "campaign_name": campaign.name,
            "objective": campaign.objective,
            "audience_description": campaign.audience_description,
            "landing_url": landing_url,
            "landing_page": landing_page,
            "work_order": work_order,
            "structured_fields": parsed_fields,
            "reviewed_fields": _dict_value(work_order.get("reviewed_delivery_fields")),
            "brief": _first_text(
                work_order.get("brief"),
                parsed_fields.get("brief"),
                parsed_fields.get("description"),
                parsed_fields.get("audience_description_raw"),
            ),
            "event_name": _first_text(
                work_order.get("event_name"),
                parsed_fields.get("event_name"),
                parsed_fields.get("objective"),
                campaign.objective,
            ),
            "country": _first_text(work_order.get("country"), parsed_fields.get("country")),
            "media": _first_text(work_order.get("media"), parsed_fields.get("media")),
        }
    )
    return compact_creative_strategy(strategy)
```

Use `compact_creative_strategy()` from the new builder when reading existing strategy metadata.

- [ ] **Step 6: Update OpenAI provider compaction and topic prompt**

In `backend/app/integrations/llm/openai_provider.py`, import:

```python
from backend.app.services.creative_strategy_builder import compact_creative_strategy
```

Replace the body of the local `_compact_creative_strategy()` helper with:

```python
def _compact_creative_strategy(value: Any) -> dict[str, Any] | None:
    return compact_creative_strategy(value)
```

Update `_creative_strategy_system_instruction()` to include v2 language:

```python
        "If creative_strategy.schema_version is creative_strategy.v2, treat it as the "
        "mandatory internal creative brief. For topic generation, create one topic per "
        "topic_angle_plan slot when possible; do not repeat angle_type across the three "
        "topics. Return angle_type when the schema allows it. For copy, image, storyboard, "
        "and video, follow market_context, audience_lens, copy_guidance, image_guidance, "
        "video_guidance, and compliance_guardrails. Use audience traits only as internal "
        "strategy; do not directly assert sensitive personal attributes in user-facing text. "
```

Keep the existing legacy-template instruction after the v2 paragraph so old metadata still works.

Update `_topic_candidate_from_data`:

```python
    normalized = {
        "title": _coerce_text(data.get("title")),
        "angle": _coerce_text(data.get("angle")),
        "angle_type": _coerce_optional_text(data.get("angle_type")),
        "audience": _coerce_optional_text(data.get("audience")),
        "selling_points": _coerce_text_list(data.get("selling_points")),
        "risk_notes": _coerce_optional_text(data.get("risk_notes")),
        "rationale": _coerce_optional_text(data.get("rationale")),
        "score": _coerce_score(data.get("score")),
    }
```

- [ ] **Step 7: Update mock topic generation**

In `backend/app/integrations/llm/mock_provider.py`, inside `generate_topics`, derive angle slots:

```python
        creative_strategy = signals.get("creative_strategy") if isinstance(signals, dict) else {}
        angle_plan = (
            creative_strategy.get("topic_angle_plan")
            if isinstance(creative_strategy, dict)
            else None
        )
        angle_items = [item for item in angle_plan if isinstance(item, dict)] if isinstance(angle_plan, list) else []
```

When appending candidates, set:

```python
                    angle_type=(
                        str(angle_items[index - 1].get("angle_type"))
                        if index - 1 < len(angle_items)
                        else None
                    ),
```

And prefix the angle with the plan purpose when present:

```python
                    angle=_append_context(
                        (
                            f"{angle_items[index - 1].get('angle_type')}: "
                            f"{angle_items[index - 1].get('purpose')}"
                        )
                        if index - 1 < len(angle_items)
                        else angle,
                        country=country,
                        event_name=event_name,
                        landing_title=landing_title,
                    ),
```

- [ ] **Step 8: Run topic tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_topic_context_slimming.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git status --short
git add backend/app/schemas/ai.py backend/app/services/topic_service.py backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/mock_provider.py tests/test_topic_context_slimming.py
git commit -m "feat: apply strategy angle plan to topics"
```

Confirm no external request schema files are staged unless the diff is only test-import fallout.

---

### Task 4: Carry V2 Strategy Through Copy and Image Generation

**Files:**
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Modify: `tests/test_game_creative_strategy.py`
- Modify: `tests/test_image_prompt_guardrails.py` or `tests/test_creative_streaming.py`

**Interfaces:**
- Consumes: campaign/topic/draft metadata with `creative_strategy.v2`.
- Produces: copy payload includes compact strategy explicitly.
- Produces: image brief generation sees `image_guidance`, `market_context`, and `audience_lens`.

- [ ] **Step 1: Write failing copy inheritance test**

Add to `tests/test_game_creative_strategy.py` or a new `tests/test_full_chain_creative_strategy.py`:

```python
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
                    "landing_page": {"url": "https://shop.example.sg", "title": "Glow Serum"},
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
```

- [ ] **Step 2: Write failing OpenAI copy payload test**

Add near existing OpenAI provider tests:

```python
@pytest.mark.asyncio
async def test_openai_copy_payload_includes_compact_v2_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "body": "A clear daily-use message.",
            "primary_text": "A clear daily-use message.",
            "headline": "Simple daily glow",
            "description": "Designed for busy routines.",
            "cta": "Shop Now",
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "market_context": {"country_code": "SG", "language": "English"},
        "audience_lens": {"age_range": "25-34", "gender": "Female"},
        "raw_content": "SHOULD NOT LEAK",
    }
    campaign = Campaign(
        id="campaign-1",
        name="Glow Serum",
        product_name="Glow Serum",
        metadata_json={"creative_strategy": strategy},
    )
    topic = ContentTopic(
        id="topic-1",
        campaign_id="campaign-1",
        title="Busy-day skincare",
        angle="scenario_resonance",
        source_data={"creative_strategy": strategy},
    )

    await provider.generate_copy(campaign=campaign, topic=topic, constraints={})

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["creative_strategy"]["schema_version"] == "creative_strategy.v2"
    assert "SHOULD NOT LEAK" not in json.dumps(payload, ensure_ascii=False)
    assert "creative_strategy.v2" in captured["system"]
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_game_creative_strategy.py::test_copy_generation_inherits_v2_creative_strategy tests/test_topic_context_slimming.py::test_openai_copy_payload_includes_compact_v2_strategy -q
```

Expected: FAIL until payload is updated. If the OpenAI test is in another file, use the actual test path.

- [ ] **Step 4: Update OpenAI copy payload**

In `OpenAILLMProvider.generate_copy`, before `_json_completion`, add:

```python
        campaign_metadata = campaign.metadata_json if isinstance(campaign.metadata_json, dict) else {}
        topic_source_data = topic.source_data if isinstance(topic.source_data, dict) else {}
        creative_strategy = _compact_creative_strategy(
            campaign_metadata.get("creative_strategy")
            or topic_source_data.get("creative_strategy")
        )
```

In the user payload, add:

```python
                    "creative_strategy": creative_strategy,
```

Keep `campaign.metadata` only if existing tests require it, but do not rely on full metadata for strategy.

- [ ] **Step 5: Update image prompt/provider behavior**

In `OpenAILLMProvider.generate_image_briefs`, the payload already includes `draft_metadata` and `storyboard_context`. Ensure `_compact_creative_strategy()` keeps `image_guidance`, `market_context`, and `audience_lens`. Add a test assertion to the existing image brief payload test or create one:

```python
assert payload["draft_metadata"]["creative_strategy"]["image_guidance"]["visual_hooks"]
assert payload["draft_metadata"]["creative_strategy"]["market_context"]["country_code"] == "SG"
```

In `MockLLMProvider._mock_strategy_image_hint`, add v2 behavior before legacy `template_id` behavior:

```python
    schema_version = creative_strategy.get("schema_version")
    if schema_version == "creative_strategy.v2":
        vertical = str(creative_strategy.get("vertical") or "unknown")
        image_guidance = creative_strategy.get("image_guidance")
        hooks = image_guidance.get("visual_hooks") if isinstance(image_guidance, dict) else []
        hook_text = ", ".join(str(item) for item in hooks[:3]) if isinstance(hooks, list) else ""
        if vertical == "game":
            return f" Follow creative_strategy.v2 game visual direction: {hook_text}."
        if vertical == "ecommerce":
            return f" Follow creative_strategy.v2 ecommerce visual direction: {hook_text}."
        return f" Follow creative_strategy.v2 general visual direction: {hook_text}."
```

- [ ] **Step 6: Run copy and creative tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_game_creative_strategy.py tests/test_image_prompt_guardrails.py tests/test_creative_streaming.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git status --short
git add backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/mock_provider.py tests/test_game_creative_strategy.py tests/test_image_prompt_guardrails.py tests/test_creative_streaming.py
git commit -m "feat: carry strategy through copy and image prompts"
```

Only add test files that were actually changed.

---

### Task 5: Make Storyboard and Video Prompts Duration-Adaptive

**Files:**
- Modify: `backend/app/services/video_service.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Modify: `tests/test_video_storyboard.py`

**Interfaces:**
- Consumes: `duration_seconds` from existing video/storyboard request schemas.
- Produces: video prompt strategy block no longer says every strategy is a 12-second first/last-frame workflow.
- Produces: storyboard prompt tells model to compress or expand beats based on `duration_seconds`.

- [ ] **Step 1: Write failing video prompt tests**

Add to `tests/test_video_storyboard.py`:

```python
def test_video_storyboard_prompt_includes_v2_duration_adaptive_strategy() -> None:
    creative_strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "game",
        "market_context": {"country_code": "SG", "language": "English"},
        "audience_lens": {"age_range": "25-34", "expression_style": ["short", "energetic"]},
        "topic_angle_plan": [
            {"slot": 1, "angle_type": "challenge_failure", "purpose": "Test challenge hook."}
        ],
        "video_guidance": {
            "duration_adaptive": True,
            "short_video_rules": ["One strong hook, one payoff, one CTA."],
            "medium_video_rules": ["Hook, conflict, payoff, CTA."],
            "long_video_rules": ["Full story with proof and CTA."],
            "beats_by_vertical": ["challenge", "failure", "correct move", "reward"],
        },
        "compliance_guardrails": ["Do not imply guaranteed wins."],
    }

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 6,
                "visual": "Open with a level challenge.",
                "subtitle": "Can you pass?",
            }
        ],
        creative_strategy=creative_strategy,
    )

    assert "creative_strategy: creative_strategy.v2 game" in prompt
    assert "duration-adaptive" in prompt
    assert "12-second first/last-frame workflow" not in prompt
    assert "challenge_failure" in prompt
    assert "One strong hook, one payoff, one CTA." in prompt
    assert scan_brand_safety({"prompt": prompt})["status"] == "passed"
```

Add a mock provider duration test:

```python
@pytest.mark.asyncio
async def test_mock_provider_v2_storyboard_respects_short_duration() -> None:
    provider = MockLLMProvider()
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "video_guidance": {
            "duration_adaptive": True,
            "short_video_rules": ["Pain point, product, CTA."],
        },
    }
    campaign = Campaign(
        id="campaign-1",
        name="Glow Serum",
        product_name="Glow Serum",
        audience_description="Female 25-34",
        metadata_json={"creative_strategy": strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="A quick skincare routine.",
        metadata_json={"creative_strategy": strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=6,
        aspect_ratio="9:16",
        context={"creative_strategy": strategy},
        instructions=None,
    )

    assert storyboard.duration_seconds == 6
    assert storyboard.scenes[-1].end_second == 6
    assert len(storyboard.scenes) <= 3
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py::test_video_storyboard_prompt_includes_v2_duration_adaptive_strategy tests/test_video_storyboard.py::test_mock_provider_v2_storyboard_respects_short_duration -q
```

Expected: FAIL because current prompt block is legacy-only.

- [ ] **Step 3: Update video prompt block with v2 branch**

In `backend/app/services/video_service.py`, change `_creative_strategy_prompt_block()`:

```python
def _creative_strategy_prompt_block(creative_strategy: dict | None) -> str:
    if not isinstance(creative_strategy, dict):
        return ""
    if creative_strategy.get("schema_version") == "creative_strategy.v2":
        return _creative_strategy_v2_prompt_block(creative_strategy)
    return _legacy_creative_strategy_prompt_block(creative_strategy)
```

Move the current body into `_legacy_creative_strategy_prompt_block(creative_strategy: dict) -> str`.

Add:

```python
def _creative_strategy_v2_prompt_block(creative_strategy: dict) -> str:
    vertical = _brand_safe_prompt_text(str(creative_strategy.get("vertical") or "unknown"))
    market = creative_strategy.get("market_context")
    audience = creative_strategy.get("audience_lens")
    video_guidance = creative_strategy.get("video_guidance")
    topic_plan = creative_strategy.get("topic_angle_plan")
    guardrails = creative_strategy.get("compliance_guardrails")

    lines = [f"creative_strategy: creative_strategy.v2 {vertical}".strip()]
    lines.append("Use this as duration-adaptive video direction; fit beats to duration_seconds.")

    if isinstance(market, dict):
        country = _brand_safe_prompt_text(str(market.get("country_code") or market.get("country") or ""))
        language = _brand_safe_prompt_text(str(market.get("language") or ""))
        if country or language:
            lines.append(f"Market context: {country} {language}".strip())

    if isinstance(audience, dict):
        age_range = _brand_safe_prompt_text(str(audience.get("age_range") or ""))
        style_items = audience.get("expression_style")
        safe_style = _brand_safe_prompt_list(style_items[:4]) if isinstance(style_items, list) else []
        audience_parts = [part for part in [f"age range {age_range}" if age_range else "", ", ".join(safe_style)] if part]
        if audience_parts:
            lines.append(f"Audience lens for internal strategy only: {'; '.join(audience_parts)}")

    if isinstance(topic_plan, list) and topic_plan:
        safe_angles = []
        for item in topic_plan[:3]:
            if isinstance(item, dict):
                angle_type = _brand_safe_prompt_text(str(item.get("angle_type") or ""))
                purpose = _brand_safe_prompt_text(str(item.get("purpose") or ""))
                if angle_type:
                    safe_angles.append(f"{angle_type}: {purpose}".strip())
        if safe_angles:
            lines.append(f"Topic angles: {'; '.join(safe_angles)}")

    if isinstance(video_guidance, dict):
        for key, label in (
            ("short_video_rules", "Short duration rules"),
            ("medium_video_rules", "Medium duration rules"),
            ("long_video_rules", "Long duration rules"),
            ("beats_by_vertical", "Vertical beats"),
        ):
            values = video_guidance.get(key)
            if isinstance(values, list) and values:
                safe_values = _brand_safe_prompt_list(values[:5])
                if safe_values:
                    lines.append(f"{label}: {'; '.join(safe_values)}")

    if isinstance(guardrails, list) and guardrails:
        safe_guardrails = _brand_safe_prompt_list(guardrails[:6])
        if safe_guardrails:
            lines.append(f"Compliance guardrails: {'; '.join(safe_guardrails)}")
    return "\n".join(lines)
```

- [ ] **Step 4: Update OpenAI storyboard prompts**

In `OpenAILLMProvider.generate_video_storyboard`, `stream_video_storyboard_text`, `revise_video_storyboard`, and `stream_video_storyboard_revision_text`, ensure the system prompt includes:

```python
"Use duration_seconds as the source of truth. Compress or expand the creative_strategy.video_guidance beats to fit that duration; do not assume a fixed 30-second or 12-second structure. "
```

Do not change method signatures.

- [ ] **Step 5: Update mock storyboard duration handling**

In `MockLLMProvider.generate_video_storyboard`, before old legacy-strategy scene handling, add v2 branch:

```python
        if isinstance(creative_strategy, dict) and creative_strategy.get("schema_version") == "creative_strategy.v2":
            scene_count = 2 if duration_seconds <= 6 else 3 if duration_seconds <= 12 else 4
            step = max(1, duration_seconds // scene_count)
            scenes = []
            for index in range(scene_count):
                start = index * step
                end = duration_seconds if index == scene_count - 1 else min(duration_seconds, (index + 1) * step)
                scenes.append(
                    VideoStoryboardScene(
                        scene_index=index + 1,
                        start_second=start,
                        end_second=end,
                        visual=_mock_v2_strategy_scene_visual(creative_strategy, index, scene_count, safe_product),
                        subtitle="Shop Now" if creative_strategy.get("vertical") == "ecommerce" and index == scene_count - 1 else "Try Now",
                        motion="Fast readable motion.",
                        voiceover=None,
                        source_asset_ids=[assets[index % len(assets)].id] if assets else [],
                        notes="Duration-adaptive v2 strategy scene.",
                    )
                )
            return VideoStoryboardCandidate(
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                scenes=scenes,
                rationale="Mock storyboard follows creative_strategy.v2 and requested duration_seconds.",
            )
```

Add `_mock_v2_strategy_scene_visual()`:

```python
def _mock_v2_strategy_scene_visual(
    creative_strategy: dict[str, Any],
    index: int,
    scene_count: int,
    product: str,
) -> str:
    vertical = str(creative_strategy.get("vertical") or "unknown")
    if vertical == "game":
        beats = ["challenge hook", "failure moment", "correct move", "reward payoff"]
    elif vertical == "ecommerce":
        beats = ["pain point scene", "product appears", "benefit demonstration", "clear CTA"]
    else:
        beats = ["practical scenario", "product benefit", "clear next step"]
    beat = beats[min(index, len(beats) - 1)]
    if index == scene_count - 1:
        beat = "clear CTA"
    return f"{product}: {beat} following creative_strategy.v2."
```

- [ ] **Step 6: Run video tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git status --short
git add backend/app/services/video_service.py backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/mock_provider.py tests/test_video_storyboard.py
git commit -m "feat: make strategy video guidance duration adaptive"
```

---

### Task 6: Full-Chain Metadata Propagation Regression

**Files:**
- Create or modify: `tests/test_full_chain_creative_strategy.py`
- Modify only if required by failures: `backend/app/services/copywriting_service.py`, `backend/app/services/creative_service.py`, `backend/app/services/video_service.py`

**Interfaces:**
- Produces: one v2 strategy traceable from campaign to topic to draft to creative asset metadata to storyboard/video metadata.
- No new external fields required.

- [ ] **Step 1: Write failing full-chain test**

Create `tests/test_full_chain_creative_strategy.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.copywriting import CopyGenerateRequest
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.schemas.topic import TopicGenerateRequest
from backend.app.schemas.video import VideoGenerateRequest, VideoStoryboardGenerateRequest
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.creative_service import CreativeService
from backend.app.services.creative_strategy_builder import build_creative_strategy
from backend.app.services.topic_service import TopicService
from backend.app.services.video_service import VideoService


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_creative_strategy_v2_propagates_across_generation_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "mock")
    get_settings.cache_clear()
    engine, session_factory = await _session_factory()
    try:
        async with session_factory() as session:
            strategy = build_creative_strategy(
                {
                    "product_name": "Puzzle Quest",
                    "landing_url": "https://play.example.sg/level-challenge",
                    "country": "Singapore",
                    "audience_description": "Female 25-34",
                    "brief": "Create a level challenge game ad.",
                }
            )
            campaign = Campaign(
                name="Puzzle Quest SG",
                objective="traffic",
                product_name="Puzzle Quest",
                audience_description="Female 25-34",
                metadata_json={
                    "creative_strategy": strategy,
                    "work_order": {
                        "country": "Singapore",
                        "landing_url": "https://play.example.sg/level-challenge",
                        "parsed_fields": {
                            "gender": "Female",
                            "age_min": 25,
                            "age_max": 34,
                        },
                    },
                    "landing_page": {
                        "url": "https://play.example.sg/level-challenge",
                        "status": "provided",
                    },
                },
            )
            session.add(campaign)
            await session.commit()

            topics = await TopicService().generate_topics(
                session,
                TopicGenerateRequest(campaign_id=campaign.id, limit=3),
            )
            assert len(topics) == 3
            assert len({topic.source_data["topic_angle"]["angle_type"] for topic in topics}) == 3

            draft = await CopywritingService().generate_copy(
                session,
                CopyGenerateRequest(topic_id=topics[0].id),
            )
            assert draft.metadata_json["creative_strategy"]["schema_version"] == "creative_strategy.v2"

            assets = await CreativeService().generate_creatives(
                session,
                CreativeGenerateRequest(draft_id=draft.id, count=1, size="1:1"),
            )
            assert assets[0].metadata_json["creative_strategy"]["schema_version"] == "creative_strategy.v2"

            storyboard = await VideoService().generate_storyboard(
                session,
                VideoStoryboardGenerateRequest(
                    campaign_id=campaign.id,
                    creative_asset_ids=[assets[0].id],
                    draft_id=draft.id,
                    duration_seconds=6,
                    aspect_ratio="9:16",
                ),
            )
            assert storyboard.metadata_json["creative_strategy"]["schema_version"] == "creative_strategy.v2"
            assert storyboard.duration_seconds == 6

            video = await VideoService().create_video_job(
                session,
                VideoGenerateRequest(
                    campaign_id=campaign.id,
                    creative_asset_ids=[assets[0].id],
                    draft_id=draft.id,
                    prompt=storyboard.prompt,
                    storyboard=storyboard.storyboard,
                    duration_seconds=6,
                    aspect_ratio="9:16",
                    metadata_json=storyboard.metadata_json,
                ),
            )
            assert video.metadata_json["creative_strategy"]["schema_version"] == "creative_strategy.v2"
            assert video.duration_seconds == 6
    finally:
        get_settings.cache_clear()
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify failure or pass**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_full_chain_creative_strategy.py -q
```

Expected: PASS if earlier tasks fully propagated metadata. If it fails, fix only the missing propagation point.

- [ ] **Step 3: Fix copy propagation if needed**

If draft metadata lacks strategy, in `backend/app/services/copywriting_service.py`, ensure this existing expression remains:

```python
        creative_strategy = campaign_metadata.get("creative_strategy") or topic_source_data.get(
            "creative_strategy"
        )
```

And metadata includes:

```python
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
```

- [ ] **Step 4: Fix creative propagation if needed**

If creative asset metadata lacks strategy, in `backend/app/services/creative_service.py`, ensure `_creative_strategy_from_draft(draft)` is included in both streamed and non-streamed metadata:

```python
                    **({"creative_strategy": creative_strategy} if creative_strategy else {}),
```

- [ ] **Step 5: Fix video propagation if needed**

If storyboard or video metadata lacks strategy, in `backend/app/services/video_service.py`, ensure `_creative_strategy_from_sources()` checks metadata, campaign, draft, and assets, and that `generate_storyboard()` and `create_video_job()` include:

```python
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
```

- [ ] **Step 6: Run full-chain test again**

Run: `.venv\Scripts\python.exe -m pytest tests/test_full_chain_creative_strategy.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git status --short
git add tests/test_full_chain_creative_strategy.py backend/app/services/copywriting_service.py backend/app/services/creative_service.py backend/app/services/video_service.py
git commit -m "test: cover full-chain strategy propagation"
```

Only add service files that actually changed.

---

### Task 7: Final Verification and No-External-Interface Guard

**Files:**
- Modify only if needed: tests from earlier tasks
- Do not modify: `backend/app/schemas/ad_generation.py`, `backend/app/schemas/material_generation.py`, `backend/app/schemas/work_order.py`, `backend/app/schemas/video.py` unless a previous task accidentally changed them and this task reverts that accidental change.

**Interfaces:**
- Produces: verified backend tests, lint pass, and no accidental external API schema changes.

- [ ] **Step 1: Confirm external schema files are unchanged**

Run:

```powershell
git diff -- backend/app/schemas/ad_generation.py backend/app/schemas/material_generation.py backend/app/schemas/work_order.py backend/app/schemas/video.py
```

Expected: no output.

- [ ] **Step 2: Run focused strategy test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_creative_strategy_builder.py tests/test_topic_context_slimming.py tests/test_publishing_ad_generation.py tests/test_material_generation_integration.py tests/test_game_creative_strategy.py tests/test_image_prompt_guardrails.py tests/test_creative_streaming.py tests/test_video_storyboard.py tests/test_full_chain_creative_strategy.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full backend tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest
```

Expected: PASS.

- [ ] **Step 4: Run lint**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
```

Expected: PASS.

- [ ] **Step 5: Check git status for secrets and unrelated files**

Run:

```powershell
git status --short
git diff --cached --name-only
```

Expected:

- No `AGENTS.md`.
- No `.env`.
- No `.env.production`.
- No `.env.production.bak.local-model-test`.
- No unrelated frontend files unless explicitly changed during implementation.

- [ ] **Step 6: Final commit if verification-only fixes were needed**

If Task 7 required fixes, commit them:

```powershell
git add <only files fixed in Task 7>
git commit -m "chore: verify creative strategy rollout"
```

If no files changed in Task 7, do not create an empty commit.

---

## Self-Review

**Spec coverage:** This plan covers generic strategy construction, country/audience/date context, game/ecommerce classification, three distinct topic angles, copy/image/video guidance, duration-adaptive storyboard logic, full-chain propagation, and external API compatibility.

**No placeholders:** The plan avoids placeholder language and gives concrete paths, test snippets, implementation snippets, and commands.

**Type consistency:** Public strategy builder functions are `build_creative_strategy()` and `compact_creative_strategy()`. Strategy schema version is consistently `creative_strategy.v2`. Topic angle metadata is consistently stored under `source_data["topic_angle"]`.

**Execution boundary:** This is a plan only. Code implementation should start in an isolated branch/worktree if the current workspace remains dirty.
