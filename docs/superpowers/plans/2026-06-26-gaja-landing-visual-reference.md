# GAJA Landing Visual Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GAJA777 image and video generation use landing-page-inspired premium neon game-lobby direction instead of childish casual puzzle visuals.

**Architecture:** Add a focused visual-reference service that creates a compact, prompt-ready GAJA visual reference from metadata or domain fallback. Store that reference in landing page snapshot JSON, merge it into GAJA creative strategy, and carry it through image brief generation, mock generation, video storyboard prompts, and final video provider prompts without adding a database migration.

**Tech Stack:** FastAPI service layer, SQLAlchemy JSON metadata fields, Pydantic schemas, OpenAI/mock LLM providers, pytest, ruff.

## Global Constraints

- Do not commit `AGENTS.md`, `.env`, `.env.production`, real tokens, API keys, database passwords, or deployment secrets.
- Do not use visible "first recharge", deposit, reward, winning, casino, slot, cash, coin, jackpot, or money claims in generated creative direction.
- Keep `creative_strategy` metadata brand-safety-scan neutral: store risky exclusions as safe labels such as `restricted_review_props`, `financial_prop_cues`, and `outcome_claim_cues`, not as raw banned ad words.
- Keep GAJA777 video flow as a 12-second first/last-frame workflow: 0-2s hook, 2-7s card carousel, 7-10s lobby reveal, 10-12s Register / Play Now CTA.
- First implementation does not require a database migration.
- First implementation does not require browser screenshot capture; use provided reference metadata when present and deterministic GAJA domain fallback otherwise.
- If a generated file touches runnable backend behavior, sync and verify local Docker after tests when executing the plan.

---

## File Structure

- Create `backend/app/services/landing_visual_reference.py`
  - Owns deterministic visual-reference construction.
  - No database, HTTP, browser, or LLM dependency.
  - Exposes helper functions used by landing page and strategy services.
- Modify `backend/app/services/landing_page_service.py`
  - Calls the visual-reference service after HTML extraction.
  - Stores `extracted_data["visual_reference"]` when a reference is available.
- Modify `backend/app/services/game_creative_strategy.py`
  - Updates GAJA brand template from casual/puzzle style to premium neon game-lobby style.
  - Merges `landing_visual_reference` into returned strategy when context provides one.
- Modify `backend/app/integrations/llm/openai_provider.py`
  - Keeps new strategy fields during compaction.
  - Strengthens system guidance for visual-reference-driven GAJA creative.
- Modify `backend/app/integrations/llm/mock_provider.py`
  - Removes old risky mock cues.
  - Makes mock image/video output reflect premium neon game-lobby direction.
- Modify `backend/app/services/video_service.py`
  - Merges latest landing visual reference into strategy during storyboard and video job creation.
  - Includes visual reference, negative style cues, and 12-second beats in final video prompt block.
- Test `tests/test_landing_visual_reference.py`
  - Covers deterministic reference construction and extraction helpers.
- Test `tests/test_game_creative_strategy.py`
  - Updates current expectations and verifies no childish/risky raw cues remain.
- Test `tests/test_image_prompt_guardrails.py`
  - Covers OpenAI payload compaction and mock image brief direction.
- Test `tests/test_video_storyboard.py`
  - Covers video prompt block and mock storyboard direction.
- Optional Test `tests/test_landing_page_visual_reference.py`
  - Covers `LandingPageService._fetch_snapshot()` storing `visual_reference` with a fake HTTP client.

---

### Task 1: Landing Visual Reference Service

**Files:**
- Create: `backend/app/services/landing_visual_reference.py`
- Test: `tests/test_landing_visual_reference.py`

**Interfaces:**
- Produces: `build_landing_visual_reference(url: str, metadata: Mapping[str, Any] | None = None, title: str | None = None, text_excerpt: str | None = None) -> dict[str, Any] | None`
- Produces: `extract_landing_visual_reference(context: Mapping[str, Any] | None) -> dict[str, Any] | None`
- Produces: `merge_landing_visual_reference(strategy: dict[str, Any] | None, landing_page_context: Mapping[str, Any] | None) -> dict[str, Any] | None`

- [ ] **Step 1: Write failing tests for GAJA fallback, reference-image source, non-GAJA empty result, and extraction.**

Add `tests/test_landing_visual_reference.py`:

```python
from backend.app.services.landing_visual_reference import (
    build_landing_visual_reference,
    extract_landing_visual_reference,
    merge_landing_visual_reference,
)


GAJA_URL = "https://www.gaja777.game/#/?invite=YBG71118&register=true"


def test_gaja_domain_returns_premium_visual_fallback() -> None:
    reference = build_landing_visual_reference(GAJA_URL)

    assert reference is not None
    assert reference["source"] == "domain_fallback"
    assert reference["status"] == "fallback"
    assert "near-black navy background" in reference["palette"]
    assert "dark premium mobile game lobby" in reference["surface_style"]
    assert "mythic fire warrior card" in reference["original_game_card_archetypes"]
    assert "childlike puzzle blocks" in reference["negative_style_cues"]
    assert reference["video_recipe"]["duration_seconds"] == 12
    assert reference["video_recipe"]["beats"][0].startswith("0-2s")


def test_reference_images_mark_source_without_network_fetch() -> None:
    reference = build_landing_visual_reference(
        GAJA_URL,
        metadata={
            "reference_images": [
                "C:/Users/panda/AppData/Local/Temp/codex-clipboard-e7477206.png"
            ]
        },
    )

    assert reference is not None
    assert reference["source"] == "reference_image"
    assert reference["status"] == "analyzed"
    assert reference["reference_image_count"] == 1
    assert reference["analysis_note"] == (
        "Using operator-provided landing page screenshots as visual style anchors."
    )


def test_non_gaja_without_reference_images_returns_none() -> None:
    assert build_landing_visual_reference("https://example.com/app") is None


def test_extract_landing_visual_reference_accepts_snapshot_context_shapes() -> None:
    reference = build_landing_visual_reference(GAJA_URL)
    context = {"extracted_data": {"visual_reference": reference}}

    assert extract_landing_visual_reference(context) == reference
    assert extract_landing_visual_reference({"visual_reference": reference}) == reference
    assert extract_landing_visual_reference(None) is None


def test_merge_landing_visual_reference_copies_strategy() -> None:
    reference = build_landing_visual_reference(GAJA_URL)
    strategy = {"template_id": "gaja_brand", "brand": {"display_name": "GAJA777"}}

    merged = merge_landing_visual_reference(strategy, {"extracted_data": {"visual_reference": reference}})

    assert merged is not strategy
    assert merged["landing_visual_reference"] == reference
    assert strategy.get("landing_visual_reference") is None
```

- [ ] **Step 2: Run tests to verify they fail.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_landing_visual_reference.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.services.landing_visual_reference'`.

- [ ] **Step 3: Implement the service.**

Create `backend/app/services/landing_visual_reference.py`:

```python
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

GAJA_DOMAIN = "gaja777.game"

_GAJA_BASE_REFERENCE: dict[str, Any] = {
    "confidence": 0.8,
    "palette": [
        "near-black navy background",
        "electric cyan edge light",
        "magenta and violet glow",
        "orange CTA accent",
        "gold metallic title highlight",
    ],
    "surface_style": [
        "dark premium mobile game lobby",
        "glossy rectangular game cards",
        "metallic 3D title text",
        "cinematic neon rim light",
        "high contrast card carousel",
    ],
    "original_game_card_archetypes": [
        "mythic fire warrior card",
        "ice energy hero card",
        "flame fortress adventure card",
        "glossy jewel and fruit matching card",
        "abstract 777 neon brand numeral card",
    ],
    "composition_cues": [
        "GAJA777 identity visible in the first frame",
        "phone-screen vertical lobby composition",
        "multiple premium cards angled in depth",
        "clear orange Register or Play Now CTA in final frame",
    ],
    "negative_style_cues": [
        "childlike puzzle blocks",
        "bubble-pop toys",
        "flat preschool cartoon style",
        "plain runner-game track",
        "generic falling-block game look",
        "restricted review props",
        "financial prop cues",
        "outcome claim cues",
    ],
    "video_recipe": {
        "duration_seconds": 12,
        "beats": [
            "0-2s: dark neon GAJA777 lobby hook with premium cards",
            "2-7s: fast carousel through original fantasy and jewel game cards",
            "7-10s: coherent app lobby reveal matching landing page style",
            "10-12s: Register / Play Now end card",
        ],
    },
}


def build_landing_visual_reference(
    url: str,
    metadata: Mapping[str, Any] | None = None,
    title: str | None = None,
    text_excerpt: str | None = None,
) -> dict[str, Any] | None:
    metadata = metadata if isinstance(metadata, Mapping) else {}
    reference_images = _reference_images(metadata.get("reference_images"))
    is_gaja = _is_gaja_url(url) or _has_gaja_text(title, text_excerpt)
    if not is_gaja and not reference_images:
        return None
    if not is_gaja:
        return None

    reference = deepcopy(_GAJA_BASE_REFERENCE)
    if reference_images:
        reference.update(
            {
                "source": "reference_image",
                "status": "analyzed",
                "confidence": 0.86,
                "reference_image_count": len(reference_images),
                "analysis_note": (
                    "Using operator-provided landing page screenshots as visual style anchors."
                ),
            }
        )
    else:
        reference.update({"source": "domain_fallback", "status": "fallback"})
    return reference


def extract_landing_visual_reference(
    context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(context, Mapping):
        return None
    direct = context.get("visual_reference")
    if isinstance(direct, dict):
        return direct
    extracted_data = context.get("extracted_data")
    if isinstance(extracted_data, Mapping):
        nested = extracted_data.get("visual_reference")
        if isinstance(nested, dict):
            return nested
    return None


def merge_landing_visual_reference(
    strategy: dict[str, Any] | None,
    landing_page_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(strategy, dict):
        return strategy
    reference = extract_landing_visual_reference(landing_page_context)
    if not reference:
        return strategy
    merged = deepcopy(strategy)
    merged["landing_visual_reference"] = reference
    return merged


def _is_gaja_url(url: str) -> bool:
    domain = urlparse(str(url or "")).netloc.lower()
    return domain.endswith(GAJA_DOMAIN)


def _has_gaja_text(*values: str | None) -> bool:
    text = " ".join(value for value in values if value).lower()
    return "gaja" in text or "gaja777" in text


def _reference_images(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]
```

- [ ] **Step 4: Run tests to verify they pass.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_landing_visual_reference.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1.**

Run:

```powershell
git status --short
git add backend/app/services/landing_visual_reference.py tests/test_landing_visual_reference.py
git commit -m "feat: add GAJA landing visual reference service"
```

Expected: commit succeeds and does not include private files.

---

### Task 2: Landing Page Snapshot Integration

**Files:**
- Modify: `backend/app/services/landing_page_service.py`
- Test: `tests/test_landing_page_visual_reference.py`

**Interfaces:**
- Consumes: `build_landing_visual_reference(...)`
- Produces: `LandingPageSnapshot.extracted_data["visual_reference"]` for GAJA snapshots.
- Produces: `snapshot_to_context(snapshot)["extracted_data"]["visual_reference"]`.

- [ ] **Step 1: Write failing integration tests with a fake HTTP client.**

Add `tests/test_landing_page_visual_reference.py`:

```python
from backend.app.db.models.campaign import Campaign
from backend.app.services.landing_page_service import LandingPageService, snapshot_to_context


class FakeResponse:
    is_error = False
    status_code = 200
    text = (
        "<html><head><title>GAJA777</title>"
        '<meta name="description" content="Premium game lobby">'
        "</head><body><h1>GAJA777</h1></body></html>"
    )
    url = "https://www.gaja777.game/#/?invite=YBG71118&register=true"


class FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str):
        return FakeResponse()


async def test_fetch_snapshot_stores_gaja_visual_reference(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.services.landing_page_service.httpx.AsyncClient",
        FakeAsyncClient,
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777",
        product_name="GAJA777",
        metadata_json={},
    )

    snapshot = await LandingPageService()._fetch_snapshot(
        campaign=campaign,
        url="https://www.gaja777.game/#/?invite=YBG71118&register=true",
        metadata={"reference_images": ["C:/temp/gaja-reference.png"]},
    )

    visual_reference = snapshot.extracted_data["visual_reference"]
    assert visual_reference["source"] == "reference_image"
    assert visual_reference["status"] == "analyzed"
    assert "dark premium mobile game lobby" in visual_reference["surface_style"]
    assert snapshot_to_context(snapshot)["extracted_data"]["visual_reference"] == visual_reference
```

- [ ] **Step 2: Run test to verify it fails before integration.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_landing_page_visual_reference.py -q
```

Expected: FAIL with missing `visual_reference` key.

- [ ] **Step 3: Integrate visual reference construction into `_fetch_snapshot()`.**

Modify `backend/app/services/landing_page_service.py` imports:

```python
from backend.app.services.landing_visual_reference import build_landing_visual_reference
```

Modify `_fetch_snapshot()` after `extracted_data = {...}`:

```python
            visual_reference = build_landing_visual_reference(
                url=str(response.url),
                metadata=metadata,
                title=parser.title,
                text_excerpt=text_content[:3000],
            )
            if visual_reference:
                extracted_data["visual_reference"] = visual_reference
```

Do not add a visual-reference call in the `except httpx.HTTPError` branch for the first version. Failed network fetches should still return the normal failed snapshot.

- [ ] **Step 4: Run landing page tests.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_landing_visual_reference.py tests/test_landing_page_visual_reference.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2.**

Run:

```powershell
git status --short
git add backend/app/services/landing_page_service.py tests/test_landing_page_visual_reference.py
git commit -m "feat: store GAJA landing visual references"
```

Expected: commit succeeds and does not include private files.

---

### Task 3: Premium GAJA Creative Strategy

**Files:**
- Modify: `backend/app/services/game_creative_strategy.py`
- Test: `tests/test_game_creative_strategy.py`

**Interfaces:**
- Consumes: `extract_landing_visual_reference(...)`
- Produces: GAJA `creative_strategy` with `landing_visual_reference`, `negative_style_cues`, and `video_recipe`.

- [ ] **Step 1: Update and add failing tests for premium GAJA strategy.**

Modify `tests/test_game_creative_strategy.py`:

```python
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
    assert "0-2s" in " ".join(strategy["video_recipe"]["beats"])
```

Add:

```python
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
```

Modify `test_gaja_strategy_avoids_meta_gambling_review_triggers()` final assertion:

```python
    assert "meta_restricted_game_ad_safe_mode" in strategy_text
    assert "dark premium mobile game lobby" in strategy_text
    assert "casual game hub" not in strategy_text
```

Keep `test_game_strategy_metadata_is_brand_safety_neutral()` unchanged. This protects against raw banned terms inside metadata.

- [ ] **Step 2: Run test to verify it fails against current casual strategy.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_game_creative_strategy.py -q
```

Expected: FAIL on assertions expecting premium neon lobby cues and no casual hub text.

- [ ] **Step 3: Update GAJA strategy implementation.**

Modify imports in `backend/app/services/game_creative_strategy.py`:

```python
from backend.app.services.landing_visual_reference import (
    extract_landing_visual_reference,
)
```

Modify `build_game_creative_strategy()`:

```python
    if _has_mini_game_signal(haystack):
        strategy = _mini_game_pool_strategy()
    else:
        strategy = _gaja_brand_strategy()
    visual_reference = extract_landing_visual_reference(context.get("landing_page"))
    if visual_reference:
        strategy = {**strategy, "landing_visual_reference": visual_reference}
    return strategy
```

Replace `_gaja_brand_strategy()` with premium, scan-neutral metadata:

```python
def _gaja_brand_strategy() -> dict[str, Any]:
    video_recipe = {
        "duration_seconds": 12,
        "beats": [
            "0-2s: dark neon GAJA777 lobby hook with premium cards",
            "2-7s: fast carousel through original fantasy and jewel game cards",
            "7-10s: coherent app lobby reveal matching landing page style",
            "10-12s: Register / Play Now end card",
        ],
    }
    return {
        "template_id": GAJA_TEMPLATE_ID,
        "template_name": "GAJA premium neon game lobby ad",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
        "brand": {
            "display_name": "GAJA777",
            "landing_domain": GAJA_DOMAIN,
            "palette": [
                "near-black navy",
                "electric cyan",
                "magenta violet glow",
                "orange CTA",
                "metallic gold highlight",
            ],
            "real_page_signals": [
                "GAJA777 wordmark",
                "dark premium mobile game lobby",
                "glossy rectangular game cards",
                "orange Register button",
            ],
        },
        "meta_restricted_game_ad_safe_mode": True,
        "first_frame": {
            "role": "premium game-lobby hook poster",
            "visual_must_include": [
                "large GAJA777 wordmark",
                "dark premium mobile game lobby",
                "premium game cards angled in depth",
                "metallic 3D title treatment",
                "cinematic neon rim light",
            ],
            "composition": (
                "Make GAJA777 readable in the first second. Use a mature, high-contrast "
                "game lobby poster with glossy cards and cinematic depth."
            ),
        },
        "last_frame": {
            "role": "conversion end card",
            "visual_must_include": [
                "GAJA777 premium game lobby",
                "Register / Play Now CTA",
                "orange CTA button matching the landing page",
                "clean phone registration cue when appropriate",
            ],
            "cta_must_include": ["Register", "Play Now"],
            "composition": (
                "End on a clear registration card connected to the GAJA777 landing page "
                "and premium game lobby style."
            ),
        },
        "motion_direction": [
            "Start with a strong dark neon poster frame.",
            "Move through glossy fantasy and jewel game cards with energy transitions.",
            "Reveal the coherent GAJA777 lobby before the CTA.",
            "Hold the final Register or Play Now end card long enough to read.",
        ],
        "negative_style_cues": [
            "childlike puzzle blocks",
            "bubble-pop toys",
            "flat preschool cartoon style",
            "plain runner-game track",
            "generic falling-block game look",
            "restricted review props",
            "financial prop cues",
            "outcome claim cues",
        ],
        "video_recipe": video_recipe,
        "compliance_guardrails": [
            "Use original game-card visuals inspired by the landing page style.",
            "Keep copy about navigation, variety, simple start, and app experience.",
            "Keep visuals broad-review friendly and non-regulated.",
            "Avoid fake Facebook or browser UI screenshots.",
            "Avoid reward, value-return, payment, or outcome-claim framing.",
        ],
    }
```

Do not change `_mini_game_pool_strategy()` in this task except if current tests require import formatting. Mini-game-pool remains a separate intentional template.

- [ ] **Step 4: Run strategy tests.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_game_creative_strategy.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3.**

Run:

```powershell
git status --short
git add backend/app/services/game_creative_strategy.py tests/test_game_creative_strategy.py
git commit -m "feat: upgrade GAJA creative strategy style"
```

Expected: commit succeeds and does not include private files.

---

### Task 4: Image Brief Prompt and Mock Provider Consumption

**Files:**
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Test: `tests/test_image_prompt_guardrails.py`

**Interfaces:**
- Consumes: `creative_strategy["landing_visual_reference"]`
- Produces: compact LLM payload retaining `landing_visual_reference`, `negative_style_cues`, and `video_recipe`.
- Produces: mock image visual directions that mention premium neon lobby cues.

- [ ] **Step 1: Add failing tests for visual-reference compaction and mock direction.**

Modify `tests/test_image_prompt_guardrails.py` by adding:

```python
@pytest.mark.asyncio
async def test_openai_image_brief_payload_preserves_landing_visual_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "briefs": [
                {
                    "image_index": 1,
                    "title": "Premium lobby",
                    "short_text": "Register",
                    "visual_direction": "Dark neon GAJA777 lobby with premium game cards.",
                    "size": "9:16",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "landing_page": {
                "extracted_data": {
                    "visual_reference": {
                        "source": "reference_image",
                        "status": "analyzed",
                        "palette": ["near-black navy background"],
                        "surface_style": ["dark premium mobile game lobby"],
                        "video_recipe": {
                            "duration_seconds": 12,
                            "beats": ["0-2s: dark neon GAJA777 lobby hook with premium cards"],
                        },
                    }
                }
            },
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        headline="Register",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    await provider.generate_image_briefs(draft=draft, count=1, size="9:16")

    payload = captured["payload"]
    assert isinstance(payload, dict)
    strategy = payload["draft_metadata"]["creative_strategy"]
    assert strategy["landing_visual_reference"]["surface_style"] == [
        "dark premium mobile game lobby"
    ]
    assert "negative_style_cues" in strategy
    assert "video_recipe" in strategy
    assert "landing visual reference" in captured["system"]


@pytest.mark.asyncio
async def test_mock_image_briefs_use_premium_gaja_brand_direction() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        headline="Register",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=2, size="9:16")

    assert "dark neon GAJA777 lobby" in briefs[0].visual_direction
    assert "premium game cards" in briefs[0].visual_direction
    assert "GAJA777 premium game lobby" in briefs[1].visual_direction
    brief_text = " ".join(brief.visual_direction for brief in briefs).lower()
    for risky_term in ("casino", "slot", "jackpot", "cash", "coin", "money", "recharge"):
        assert risky_term not in brief_text
```

Update existing `test_mock_image_briefs_include_game_strategy_direction()` only if the new GAJA default changes its assumptions. Keep the mini-game-pool test by passing a mini-game brief so it still expects mini-game behavior.

- [ ] **Step 2: Run image tests to verify they fail.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_image_prompt_guardrails.py -q
```

Expected: FAIL because `_compact_creative_strategy()` drops new keys and mock provider still emits old GAJA cues.

- [ ] **Step 3: Update OpenAI creative strategy instruction and compaction.**

Modify `_creative_strategy_system_instruction()` in `backend/app/integrations/llm/openai_provider.py`:

```python
        "ad-direction context. Honor its template_id, duration_seconds, first_frame, "
        "last_frame, motion_direction, compliance_guardrails, negative_style_cues, "
        "video_recipe, and landing_visual_reference. If landing_visual_reference is "
        "present, treat it as mandatory art direction: follow its palette, surface_style, "
        "composition_cues, original_game_card_archetypes, and video_recipe while avoiding "
        "negative_style_cues. For a 12-second first/last-frame workflow, make the "
```

Also update the GAJA sentence:

```python
        "finish on a GAJA777 game hub end card. For gaja_brand, use a dark premium "
        "GAJA777 neon game lobby with glossy cards from the first frame and finish on a "
        "Register or Play Now CTA. Keep all claims about "
```

Modify `_compact_creative_strategy()` key list to include:

```python
        "landing_visual_reference",
        "negative_style_cues",
        "video_recipe",
```

- [ ] **Step 4: Update mock provider strategy hints.**

Modify `backend/app/integrations/llm/mock_provider.py` in `_mock_strategy_image_hint()`:

```python
    if template_id == "gaja_brand":
        if role == "last_frame":
            return (
                " Follow creative_strategy gaja_brand: last-frame GAJA777 premium game "
                "lobby with Register / Play Now CTA and orange button."
            )
        return (
            " Follow creative_strategy gaja_brand: first-frame dark neon GAJA777 lobby "
            "with metallic title treatment and premium game cards."
        )
```

Add a helper below `_mock_strategy_image_hint()`:

```python
def _mock_visual_reference_hint(creative_strategy: dict[str, Any] | None) -> str:
    if not isinstance(creative_strategy, dict):
        return ""
    reference = creative_strategy.get("landing_visual_reference")
    if not isinstance(reference, dict):
        return ""
    surface_style = reference.get("surface_style")
    palette = reference.get("palette")
    parts: list[str] = []
    if isinstance(surface_style, list) and surface_style:
        parts.append(f" Match landing visual style: {', '.join(str(item) for item in surface_style[:3])}.")
    if isinstance(palette, list) and palette:
        parts.append(f" Use palette: {', '.join(str(item) for item in palette[:3])}.")
    return "".join(parts)
```

Append this helper output in `generate_image_briefs()` after `strategy_hint`:

```python
            visual_reference_hint = _mock_visual_reference_hint(creative_strategy)
```

and in `visual_direction`:

```python
                          f"{strategy_hint}"
                          f"{visual_reference_hint}"
```

- [ ] **Step 5: Run image tests.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_image_prompt_guardrails.py tests/test_landing_visual_reference.py tests/test_game_creative_strategy.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4.**

Run:

```powershell
git status --short
git add backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/mock_provider.py tests/test_image_prompt_guardrails.py
git commit -m "feat: carry GAJA visual reference into image prompts"
```

Expected: commit succeeds and does not include private files.

---

### Task 5: Video Storyboard and Final Prompt Consumption

**Files:**
- Modify: `backend/app/services/video_service.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Test: `tests/test_video_storyboard.py`

**Interfaces:**
- Consumes: `merge_landing_visual_reference(...)`
- Produces: storyboard context where `creative_strategy["landing_visual_reference"]` is available.
- Produces: `_creative_strategy_prompt_block(...)` text with landing visual reference summary and 12-second beats.

- [ ] **Step 1: Add failing video prompt tests.**

Modify `tests/test_video_storyboard.py`:

```python
def test_video_storyboard_prompt_includes_landing_visual_reference() -> None:
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "landing_page": {
                "extracted_data": {
                    "visual_reference": {
                        "source": "reference_image",
                        "status": "analyzed",
                        "palette": ["near-black navy background"],
                        "surface_style": ["dark premium mobile game lobby"],
                        "composition_cues": ["premium cards angled in depth"],
                        "negative_style_cues": ["childlike puzzle blocks"],
                        "video_recipe": {
                            "duration_seconds": 12,
                            "beats": [
                                "0-2s: dark neon GAJA777 lobby hook with premium cards",
                                "10-12s: Register / Play Now end card",
                            ],
                        },
                    }
                }
            },
        }
    )

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 2,
                "visual": "Open on GAJA777.",
            }
        ],
        creative_strategy=creative_strategy,
    )

    assert "Landing visual reference" in prompt
    assert "dark premium mobile game lobby" in prompt
    assert "near-black navy background" in prompt
    assert "0-2s: dark neon GAJA777 lobby hook with premium cards" in prompt
    assert "Avoid style cues: childlike puzzle blocks" in prompt
    assert scan_brand_safety({"prompt": prompt})["status"] == "passed"
```

Update `test_mock_provider_uses_game_strategy_for_video_storyboard()` for the GAJA brand path by adding a new test rather than changing the mini-game-pool test:

```python
@pytest.mark.asyncio
async def test_mock_provider_uses_premium_gaja_brand_storyboard() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777 campaign",
        product_name="GAJA777",
        audience_description="India users",
        metadata_json={"creative_strategy": creative_strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        primary_text="Explore GAJA777 game lobby.",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": creative_strategy},
        instructions=None,
    )

    assert "dark neon GAJA777 lobby" in storyboard.scenes[0].visual
    assert "premium game cards" in storyboard.scenes[0].visual
    assert "GAJA777 premium game lobby" in storyboard.scenes[-1].visual
    assert storyboard.scenes[-1].subtitle in {"Register", "Play Now"}
```

- [ ] **Step 2: Run video tests to verify they fail.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py -q
```

Expected: FAIL because prompt block does not mention landing visual reference and mock provider still uses old GAJA scene copy.

- [ ] **Step 3: Merge landing visual reference into video context.**

Modify imports in `backend/app/services/video_service.py`:

```python
from backend.app.services.landing_visual_reference import merge_landing_visual_reference
```

Modify `_video_context()` after `creative_strategy = _creative_strategy_from_sources(...)`:

```python
        creative_strategy = merge_landing_visual_reference(
            creative_strategy,
            landing_page_context,
        )
```

Modify `create_video_job()` after `creative_strategy = _creative_strategy_from_sources(...)`:

```python
        creative_strategy = merge_landing_visual_reference(
            creative_strategy,
            landing_page_context,
        )
```

- [ ] **Step 4: Extend final video prompt block.**

Modify `_creative_strategy_prompt_block()` in `backend/app/services/video_service.py`:

```python
    landing_visual_reference = creative_strategy.get("landing_visual_reference")
    negative_style_cues = creative_strategy.get("negative_style_cues")
    video_recipe = creative_strategy.get("video_recipe")
```

Add after template-specific rules:

```python
    reference_block = _landing_visual_reference_summary(landing_visual_reference)
    if reference_block:
        lines.append(reference_block)
    if isinstance(video_recipe, dict):
        beats = video_recipe.get("beats")
        if isinstance(beats, list) and beats:
            lines.append(f"12-second beats: {'; '.join(str(item) for item in beats[:4])}")
    if isinstance(negative_style_cues, list) and negative_style_cues:
        lines.append(
            f"Avoid style cues: {'; '.join(str(item) for item in negative_style_cues[:6])}"
        )
```

Add helper near `_strategy_frame_summary()`:

```python
def _landing_visual_reference_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = ["Landing visual reference:"]
    for key, label in (
        ("palette", "palette"),
        ("surface_style", "surface style"),
        ("original_game_card_archetypes", "game-card archetypes"),
        ("composition_cues", "composition cues"),
    ):
        item = value.get(key)
        if isinstance(item, list) and item:
            parts.append(f"{label}: {', '.join(str(entry) for entry in item[:4])}")
    return " | ".join(parts) if len(parts) > 1 else ""
```

- [ ] **Step 5: Update mock video scene copy.**

Modify `_mock_strategy_scene_visual()` in `backend/app/integrations/llm/mock_provider.py`:

```python
    if template_id == "gaja_brand":
        if role == "last_frame":
            return "End on the GAJA777 premium game lobby with Register CTA and orange button."
        return (
            "Open with a dark neon GAJA777 lobby, metallic title treatment, "
            "premium game cards, and cinematic depth."
        )
```

Do not include raw risky ad-review terms in this copy.

- [ ] **Step 6: Run video tests.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py tests/test_game_creative_strategy.py tests/test_landing_visual_reference.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5.**

Run:

```powershell
git status --short
git add backend/app/services/video_service.py backend/app/integrations/llm/mock_provider.py tests/test_video_storyboard.py
git commit -m "feat: carry GAJA visual reference into video prompts"
```

Expected: commit succeeds and does not include private files.

---

### Task 6: End-to-End Verification and Local Docker Sync

**Files:**
- Verify changed backend tests and lint.
- No new source files unless a previous task uncovered a focused fix.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified backend behavior ready for local Docker use.

- [ ] **Step 1: Run targeted backend tests.**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_landing_visual_reference.py tests/test_landing_page_visual_reference.py tests/test_game_creative_strategy.py tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py -q
```

Expected: PASS.

- [ ] **Step 2: Run lint.**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
```

Expected: PASS.

- [ ] **Step 3: Inspect Git state before Docker sync.**

Run:

```powershell
git status --short
```

Expected: only intentional tracked changes from the current task, and no staged `AGENTS.md`, `.env`, `.env.production`, real secrets, or unrelated user files.

- [ ] **Step 4: Sync runnable changes into local Docker.**

Run:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build backend
```

Expected: backend image rebuilds and container starts.

- [ ] **Step 5: Verify backend health.**

Run:

```powershell
curl.exe -i http://127.0.0.1/api/v1/health/live
```

Expected: HTTP 200 with body containing `{"status":"ok"}`.

- [ ] **Step 6: Check backend logs.**

Run:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail=120 backend
```

Expected: no import errors, startup tracebacks, or repeated request failures related to landing visual reference, game creative strategy, LLM provider, or video service.

- [ ] **Step 7: Final Git status.**

Run:

```powershell
git status --short
```

Expected: `.env.production.bak.local-model-test` and any pre-existing unrelated untracked files may remain untracked, but no private files are staged.

- [ ] **Step 8: Commit final verification-only fix if needed.**

If Step 1 through Step 6 uncovered and fixed a small issue, commit only the relevant tracked files:

```powershell
git add <specific changed source and test files>
git commit -m "fix: verify GAJA visual reference flow"
```

Expected: no commit is created if there were no verification fixes.

---

## Self-Review Notes

- Spec coverage: landing visual reference construction is Task 1; snapshot storage is Task 2; GAJA premium strategy is Task 3; image brief and compact payload handling is Task 4; video storyboard and provider prompt handling is Task 5; targeted tests, lint, and Docker sync are Task 6.
- Scope: browser screenshot capture and real vision-model analysis are intentionally outside first implementation. The plan supports operator-provided reference images and GAJA domain fallback now, which is enough to stop the current childish default.
- Brand safety: the plan intentionally avoids raw risky terms inside `creative_strategy` metadata and mock outputs because current tests scan metadata and prompt strings.
- Type consistency: helper names are consistent across tasks: `build_landing_visual_reference`, `extract_landing_visual_reference`, and `merge_landing_visual_reference`.
