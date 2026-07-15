# Creative Strategy Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor creative strategy construction into a small package-based resolver while preserving the existing `build_creative_strategy()` entry point and current India gambling behavior.

**Architecture:** Keep `backend/app/services/creative_strategy_builder.py` as a compatibility wrapper. Move strategy package data, dynamic brand resolution, reference signal handling, validation, and final resolution into `backend/app/services/creative_strategy/`.

**Tech Stack:** Python 3, pytest, existing backend service modules, no new runtime dependencies.

## Global Constraints

- Do not modify unrelated dirty files.
- Keep `schema_version` as `creative_strategy.v2` for this phase.
- Do not implement RAG, reference-video upload UI, long-term video storage, or automatic style-pack learning in this phase.
- Brand must come from the current work order context, not from a hardcoded GAJA default.
- Gambling base package owns the common `boss_matrix`, `scene_pool`, `reveal_mechanism_pool`, `cta_pool`, and `gambling_safety_rules`; country overlays only adjust preferences and safety notes.
- Existing callers must continue importing `build_creative_strategy` and `compact_creative_strategy` from `backend.app.services.creative_strategy_builder`.

---

### Task 1: Add Behavior Tests For New Strategy Shape

**Files:**
- Modify: `tests/test_creative_strategy_builder.py`

**Interfaces:**
- Consumes: `build_creative_strategy(context: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]`
- Produces: failing tests for `brand_profile`, `brand_policy_pack`, `style_pack_id`, `style_pack`, `country_overlay`, `reference_signal_pack`, and `boss_guidance`

- [ ] **Step 1: Add tests for dynamic brand profile and gambling package composition**

```python
def test_strategy_resolver_adds_dynamic_brand_profile_and_brand_policy() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "project_name": "Royal Spin 88",
            "product_name": "Royal Spin 88",
            "country": "India",
            "brief": "Premium safe gambling-like spectacle.",
        },
        today=date(2026, 7, 6),
    )

    assert strategy["brand_profile"] == {
        "raw_name": "Royal Spin 88",
        "visible_name": "Royal Spin",
        "source_field": "product_name",
        "digit_policy": "remove_digits_for_visible_brand",
    }
    assert strategy["brand_policy_pack"]["source"] == "universal_brand_policy"
    assert "0-3s" in strategy["brand_policy_pack"]["visible_text_windows"]
    assert "9-12s" in strategy["brand_policy_pack"]["visible_text_windows"]
    assert "Do not invent brand names." in strategy["brand_policy_pack"]["forbidden"]
```

- [ ] **Step 2: Add tests for base plus country overlay behavior**

```python
def test_india_gambling_uses_base_style_pack_with_country_overlay() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "product_name": "GAJA777",
            "country": "India",
            "brief": "Create a premium gambling-like short video without money claims.",
        },
        today=date(2026, 7, 6),
    )

    assert strategy["style_pack_id"] == "gambling/IN/vfx_spectacle_current"
    assert strategy["style_pack"]["base_pack_id"] == "gambling/base/vfx_spectacle"
    assert strategy["style_pack"]["country_overlay_id"] == "gambling/overlays/IN"
    assert strategy["country_overlay"]["country_code"] == "IN"
    assert "golden_light_column" in strategy["country_overlay"]["preferred_reveal_mechanisms"]
    assert "real deity names or real religious figures" in strategy["country_overlay"]["avoid"]
    assert "bird_god" in strategy["boss_matrix"]["sky_rupture_spectacle"]
    assert "sandstone_festival_city" in strategy["scene_pool"]
    assert "Boss or mysterious energy source is a VFX driver, not a combat character." in strategy["gambling_safety_rules"]
```

- [ ] **Step 3: Add tests for game style packs and reference signal pass-through**

```python
def test_game_strategy_uses_india_boss_challenge_style_pack() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "game",
            "product_name": "Puzzle Quest 2",
            "country": "India",
            "brief": "Create a cinematic game ad with a playable boss challenge.",
        },
        today=date(2026, 7, 6),
    )

    assert strategy["style_pack_id"] == "game/IN/boss_challenge_fantasy"
    assert strategy["boss_guidance"]["role"] == "playable challenge obstacle"
    assert "player action" in strategy["boss_guidance"]["must_show"]
    assert "real-money gambling mechanics" in strategy["boss_guidance"]["must_avoid"]
    assert strategy["market_game_style_pack"]["style_pack_id"] == "game/IN/boss_challenge_fantasy"


def test_reference_signal_pack_is_preserved_without_overriding_style_pack() -> None:
    reference_signal_pack = {
        "source": "manual_reference_video_analysis",
        "rhythm_bias": ["0-3s brand plus boss arrival", "3-9s low-text VFX"],
        "visual_bias": ["golden_light_column", "storm_eye"],
    }
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "product_name": "Royal Spin 88",
            "country": "India",
            "reference_signal_pack": reference_signal_pack,
        },
        today=date(2026, 7, 6),
    )

    assert strategy["style_pack_id"] == "gambling/IN/vfx_spectacle_current"
    assert strategy["reference_signal_pack"] == reference_signal_pack
    assert "golden_light_column" in strategy["reveal_mechanism_pool"]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_creative_strategy_builder.py -q`

Expected: FAIL because the new package fields do not exist yet.

### Task 2: Create Creative Strategy Package Modules

**Files:**
- Create: `backend/app/services/creative_strategy/__init__.py`
- Create: `backend/app/services/creative_strategy/packages.py`
- Create: `backend/app/services/creative_strategy/brand.py`
- Create: `backend/app/services/creative_strategy/reference.py`
- Create: `backend/app/services/creative_strategy/validators.py`
- Create: `backend/app/services/creative_strategy/resolver.py`
- Modify: `backend/app/services/creative_strategy_builder.py`

**Interfaces:**
- Produces: `resolve_creative_strategy(context: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]`
- Produces: `resolve_brand_profile(context: Mapping[str, Any]) -> dict[str, Any]`
- Produces: `normalize_reference_signal_pack(value: Any) -> dict[str, Any] | None`

- [ ] **Step 1: Move strategy package constants into `packages.py`**
- [ ] **Step 2: Add dynamic brand profile resolver in `brand.py`**
- [ ] **Step 3: Add reference signal normalizer in `reference.py`**
- [ ] **Step 4: Add final strategy validator in `validators.py`**
- [ ] **Step 5: Implement `resolve_creative_strategy()` in `resolver.py`**
- [ ] **Step 6: Keep `creative_strategy_builder.py` as compatibility entry point**

### Task 3: Update Compacting And Regression Coverage

**Files:**
- Modify: `backend/app/services/creative_strategy_builder.py`
- Modify: `tests/test_creative_strategy_builder.py`

**Interfaces:**
- Consumes: `compact_creative_strategy(value: Any) -> dict[str, Any] | None`

- [ ] **Step 1: Allow new v2 fields in `compact_creative_strategy()`**
- [ ] **Step 2: Add compacting assertions for `brand_profile`, `style_pack_id`, and `reference_signal_pack`**
- [ ] **Step 3: Run targeted tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_creative_strategy_builder.py -q`

Expected: PASS.

### Task 4: Verification

**Files:**
- Test only

- [ ] **Step 1: Run strategy tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_creative_strategy_builder.py -q`

Expected: PASS.

- [ ] **Step 2: Run prompt guardrail tests if dependencies allow**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py tests/test_image_prompt_guardrails.py -q`

Expected: PASS, or report existing unrelated failures without hiding them.

- [ ] **Step 3: Run lint on changed backend files**

Run: `.venv\Scripts\python.exe -m ruff check backend/app/services/creative_strategy_builder.py backend/app/services/creative_strategy tests/test_creative_strategy_builder.py`

Expected: PASS.
