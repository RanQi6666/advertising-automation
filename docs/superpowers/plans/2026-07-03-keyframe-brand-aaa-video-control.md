# Keyframe Brand AAA Video Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement lightweight prompt control so keyframe videos use branded boss-heavy opening frames, high-impact 3A reward visuals in the middle, and branded CTA end frames.

**Architecture:** Keep the existing first/last-frame asset flow. Add focused prompt helper text to image-brief generation and video prompt construction, with tests that pin the new timing, brand, boss, VFX, and forbidden real-money rules.

**Tech Stack:** Python, pytest, existing OpenAI/Volcengine prompt construction helpers.

## Global Constraints

- Do not add image post-processing or logo compositing in this pass.
- Keep GAJA visible brand text cleaned of numeric suffixes.
- Middle 3-9 seconds may use the user's VFX library, but must forbid cash amounts, withdrawal/recharge/balance UI, and guaranteed winning language.
- Keep edits scoped to prompt construction and deterministic mock/test helpers.

---

### Task 1: Pin Image Brief Keyframe Rules

**Files:**
- Modify: `tests/test_image_prompt_guardrails.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`

**Interfaces:**
- Consumes: `OpenAILLMProvider.generate_image_briefs(...)`
- Produces: image brief system prompt text containing first-frame boss/brand rules and last-frame brand CTA rules.

- [ ] **Step 1: Write failing prompt test**

Add assertions to `test_openai_image_brief_prompt_carries_game_creative_strategy` for:

```python
assert "first_frame image rule" in system
assert "epic hero, king, warrior, bird-god-style boss, giant serpent boss, or stone guardian boss" in system
assert "last_frame image rule" in system
assert "Start, Play Now, or Explore" in system
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_image_prompt_guardrails.py::test_openai_image_brief_prompt_carries_game_creative_strategy -q`

Expected: FAIL because the new keyframe image prompt rules do not exist yet.

- [ ] **Step 3: Implement minimal prompt helper**

Add a small helper in `openai_provider.py` that returns the first/last-frame image rules, and append it in `generate_image_briefs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_image_prompt_guardrails.py::test_openai_image_brief_prompt_carries_game_creative_strategy -q`

Expected: PASS.

### Task 2: Pin Video Timing Rules

**Files:**
- Modify: `tests/test_video_storyboard.py`
- Modify: `backend/app/services/video_service.py`

**Interfaces:**
- Consumes: `_storyboard_to_prompt(...)` and `_prompt_with_creative_strategy(...)`
- Produces: video prompt text containing 0-3s, 3-9s, and 9-12s rules.

- [ ] **Step 1: Write failing prompt tests**

Add tests that assert both storyboard-generated and direct prompts include:

```python
assert "0-3s opening rule" in prompt
assert "3-9s middle VFX rule" in prompt
assert "coin explosion effects" in prompt
assert "divine light descent" in prompt
assert "portal effects" in prompt
assert "jackpot-style feedback" in prompt
assert "boss defeat" in prompt
assert "Score, Points, Stars, or Power" in prompt
assert "9-12s ending rule" in prompt
assert "Do not show cash amounts" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py::test_storyboard_prompt_includes_keyframe_brand_aaa_timing_rules tests/test_video_storyboard.py::test_direct_video_prompt_includes_keyframe_brand_aaa_timing_rules -q`

Expected: FAIL because timing rules do not exist yet.

- [ ] **Step 3: Implement minimal video prompt helper**

Add `_keyframe_brand_aaa_video_rules()` in `video_service.py`. Append it in `_storyboard_to_prompt` and `_prompt_with_creative_strategy`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_storyboard.py::test_storyboard_prompt_includes_keyframe_brand_aaa_timing_rules tests/test_video_storyboard.py::test_direct_video_prompt_includes_keyframe_brand_aaa_timing_rules -q`

Expected: PASS.

### Task 3: Regression Verification

**Files:**
- Test: `tests/test_image_prompt_guardrails.py`
- Test: `tests/test_video_storyboard.py`

- [ ] **Step 1: Run focused regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py -q`

Expected: PASS.

- [ ] **Step 2: Run lint**

Run: `.venv\Scripts\python.exe -m ruff check backend tests`

Expected: PASS.
