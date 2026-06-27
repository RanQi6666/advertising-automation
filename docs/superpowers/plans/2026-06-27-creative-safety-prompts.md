# Creative Safety Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make generated image and video prompts avoid gambling-review text and visual props such as `777`, `Luck`, `赢钱`, `提现`, `金币雨`, and `赌场桌面`.

**Architecture:** Add one shared creative-safety prompt module for image/video generation. Wire it into GAJA creative strategy metadata, OpenAI image/video instructions, mock image/video output, and final video provider prompts so both text and visual objects are blocked consistently.

**Tech Stack:** Python services, pytest, Ruff, local Docker production compose.

## Global Constraints

- Keep the implementation scoped to backend creative strategy and prompt-generation paths.
- Do not add OCR or generated-asset post-review in this pass; the user chose only the visual-direction and hard prompt-ban layers.
- User-facing generated image/video content must avoid the banned visible words: `777`, `Luck`, `赢钱`, `提现`, `金币雨`, `赌场桌面`.
- Prompt instructions must also ban equivalent visual props: casino tables, chips, roulette, cards used as gambling props, coins, cash, wallets, payout buttons, money rain, jackpot panels, balance counters, and withdrawal UI.
- Preserve the premium neon game-lobby style, but express branding through abstract `G` marks, icon badges, neon app-lobby shapes, and game cards without risky text.
- Do not stage or commit `AGENTS.md`, `.env`, `.env.production`, real keys, or local stash files.

---

### Task 1: Shared Creative-Safety Prompt Rules

**Files:**
- Create: `backend/app/services/creative_safety_prompts.py`
- Modify: `tests/test_image_prompt_guardrails.py`
- Modify: `tests/test_video_storyboard.py`

**Interfaces:**
- Produces: `CREATIVE_VISIBLE_TEXT_BAN: str`, `CREATIVE_VISUAL_PROP_BAN: str`, `CREATIVE_LOW_TEXT_DIRECTION: str`, `creative_safety_prompt_block() -> str`, `contains_creative_safety_risk(value: str) -> bool`
- Consumes: Existing tests and prompt builders.

- [ ] **Step 1: Write failing tests**

Add tests that import the new module and assert:

```python
from backend.app.services.creative_safety_prompts import (
    creative_safety_prompt_block,
    contains_creative_safety_risk,
)

def test_creative_safety_prompt_blocks_banned_visible_words() -> None:
    block = creative_safety_prompt_block()
    for banned in ("777", "Luck", "赢钱", "提现", "金币雨", "赌场桌面"):
        assert banned in block
    assert "abstract G mark" in block
    assert "no visible brand-number text" in block

def test_creative_safety_risk_detector_catches_text_and_visual_props() -> None:
    for value in ("GAJA777", "Luck badge", "提现 button", "金币雨", "赌场桌面", "casino table", "cash rain"):
        assert contains_creative_safety_risk(value)
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_image_prompt_guardrails.py::test_creative_safety_prompt_blocks_banned_visible_words tests/test_image_prompt_guardrails.py::test_creative_safety_risk_detector_catches_text_and_visual_props -q
```

Expected: import failure for `backend.app.services.creative_safety_prompts`.

- [ ] **Step 3: Implement shared module**

Create `creative_safety_prompts.py` with:

```python
CREATIVE_VISIBLE_TEXT_BAN = (
    "Visible text hard ban: do not show or request visible text containing 777, "
    "Luck, 赢钱, 提现, 金币雨, 赌场桌面, jackpot, casino, cash, coin, wallet, payout, "
    "withdraw, bonus, or balance."
)

CREATIVE_VISUAL_PROP_BAN = (
    "Visual prop hard ban: no casino tables, card-table layouts, chips, roulette, "
    "slot machines, dice, cash, coins, money rain, wallets, bank cards, payout buttons, "
    "balance counters, jackpot panels, withdrawal UI, or gambling-like reward effects."
)

CREATIVE_LOW_TEXT_DIRECTION = (
    "Use a low-text or no-text visual style. Express brand presence through an abstract "
    "G mark, icon badge, neon app-lobby shapes, and game-card silhouettes; use no visible "
    "brand-number text."
)

CREATIVE_SAFE_CTA = "CTA text should stay limited to safe words such as Start, Play Now, Join, or Explore."

def creative_safety_prompt_block() -> str:
    return "\n".join(
        (
            "Creative safety hard rules:",
            CREATIVE_VISIBLE_TEXT_BAN,
            CREATIVE_VISUAL_PROP_BAN,
            CREATIVE_LOW_TEXT_DIRECTION,
            CREATIVE_SAFE_CTA,
        )
    )

def contains_creative_safety_risk(value: str) -> bool:
    normalized = value.casefold()
    return any(term in normalized for term in CREATIVE_SAFETY_RISK_TERMS)
```

- [ ] **Step 4: Run GREEN**

Run the two new tests and expect PASS.

### Task 2: Wire Rules Into Image And Video Prompts

**Files:**
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Modify: `backend/app/services/video_service.py`
- Modify: `tests/test_image_prompt_guardrails.py`
- Modify: `tests/test_video_storyboard.py`

**Interfaces:**
- Consumes: `creative_safety_prompt_block()`, `contains_creative_safety_risk()`
- Produces: image brief system prompts, mock image briefs, mock video storyboard visuals, and final video prompts containing the hard safety block.

- [ ] **Step 1: Write failing tests**

Add tests that assert:

```python
@pytest.mark.asyncio
async def test_openai_image_prompt_includes_creative_safety_hard_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        return {"briefs": [{"image_index": 1, "title": "Safe", "short_text": "Start", "visual_direction": "low text app lobby", "size": "9:16"}]}

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    await provider.generate_image_briefs(draft=draft, count=1, size="9:16")
    system = str(captured["system"])
    assert "Visible text hard ban" in system
    assert "777" in system
    assert "abstract G mark" in system

@pytest.mark.asyncio
async def test_mock_image_briefs_use_low_text_gaja_direction() -> None:
    briefs = await provider.generate_image_briefs(draft=draft, count=2, size="9:16", storyboard_context=storyboard_context)
    combined = " ".join(brief.visual_direction for brief in briefs)
    assert "abstract G mark" in combined
    assert "no visible brand-number text" in combined
    assert "GAJA777" not in combined

def test_video_storyboard_prompt_includes_creative_text_and_prop_bans() -> None:
    prompt = _storyboard_to_prompt([{"scene_index": 1, "visual": "Open with a neon app lobby."}])
    assert "Visible text hard ban" in prompt
    assert "casino tables" in prompt
    assert "no visible brand-number text" in prompt

@pytest.mark.asyncio
async def test_mock_provider_gaja_video_avoids_banned_text_and_props() -> None:
    storyboard = await provider.generate_video_storyboard(campaign=campaign, draft=draft, assets=[], duration_seconds=12, aspect_ratio="9:16", context={"creative_strategy": creative_strategy})
    combined = " ".join(scene.visual for scene in storyboard.scenes)
    for banned in ("777", "Luck", "casino", "cash", "coin"):
        assert banned.lower() not in combined.lower()
```

- [ ] **Step 2: Run RED**

Run:

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py -q -k "creative_safety or low_text_gaja or banned_text"
```

Expected: failures because the hard block is missing and mock output still uses `GAJA777`.

- [ ] **Step 3: Implement prompt wiring**

Add `creative_safety_prompt_block()` to OpenAI image and video storyboard system prompts. Add it to `_storyboard_to_prompt()` and `_prompt_with_creative_strategy()` via `_creative_strategy_prompt_block()`. Use `contains_creative_safety_risk()` in mock strategy image hints and scene visuals to avoid risky phrases.

- [ ] **Step 4: Run GREEN**

Run the new targeted tests and expect PASS.

### Task 3: Update GAJA Strategy To Prefer Low-Text Safe Branding

**Files:**
- Modify: `backend/app/services/game_creative_strategy.py`
- Modify: `tests/test_game_creative_strategy.py`

**Interfaces:**
- Produces: GAJA strategy metadata that avoids banned visible words while still steering premium neon game-lobby visuals.

- [ ] **Step 1: Write failing tests**

Add tests that assert default GAJA strategy does not contain `777`, `Luck`, `赢钱`, `提现`, `金币雨`, or `赌场桌面`, and that it contains abstract G mark / no visible brand-number text direction.

- [ ] **Step 2: Run RED**

Run:

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_game_creative_strategy.py -q -k low_text
```

Expected: failure because existing GAJA strategy still contains `GAJA777`.

- [ ] **Step 3: Implement strategy metadata changes**

Replace prompt-facing GAJA-visible strings with `abstract G mark`, `premium neon game lobby`, and `no visible brand-number text`. Keep `landing_domain` as `gaja777.game` because it is URL metadata, not prompt-facing visible text.

- [ ] **Step 4: Run GREEN**

Run targeted strategy tests and expect PASS.

### Task 4: Verification And Docker Sync

**Files:**
- No new source files unless earlier tasks require them.

- [ ] **Step 1: Run targeted tests**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py tests/test_game_creative_strategy.py -q
```

- [ ] **Step 2: Run full tests and Ruff**

```powershell
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m pytest -q
& "C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe" -m ruff check backend tests
```

- [ ] **Step 3: Rebuild local Docker backend**

```powershell
$env:APP_ENV_FILE='C:\Users\panda\Documents\Advertising Automation\.env.production'
docker compose --project-name advertising-automation -f "C:\Users\panda\Documents\Advertising Automation\.worktrees\codex-creative-safety-prompts\docker-compose.prod.yml" --env-file "C:\Users\panda\Documents\Advertising Automation\.env.production" up -d --build backend
Remove-Item Env:\APP_ENV_FILE
```

- [ ] **Step 4: Verify health**

```powershell
curl.exe -i http://127.0.0.1/api/v1/health/live
docker compose --project-name advertising-automation -f "C:\Users\panda\Documents\Advertising Automation\.worktrees\codex-creative-safety-prompts\docker-compose.prod.yml" --env-file "C:\Users\panda\Documents\Advertising Automation\.env.production" ps backend
```

Expected: HTTP 200 `{"status":"ok"}` and backend `healthy`.
