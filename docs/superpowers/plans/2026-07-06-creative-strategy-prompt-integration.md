# Creative Strategy Prompt Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the new strategy fields actively influence image and video prompt generation, especially India game boss-challenge direction.

**Architecture:** Keep the existing generation services and prompt surfaces. Extend the OpenAI creative-strategy system instruction and the video-service `creative_strategy.v2` prompt block to name and summarize `brand_profile`, `style_pack_id`, `style_pack`, `country_overlay`, `boss_guidance`, and `reference_signal_pack`.

**Tech Stack:** Python 3, pytest, existing backend LLM provider and video prompt helpers, no new runtime dependencies.

## Global Constraints

- Preserve existing dirty changes in `backend/app/integrations/llm/openai_provider.py` and `tests/test_image_prompt_guardrails.py`.
- Do not implement RAG, reference-video upload UI, or long-term video storage in this phase.
- Reference signals must guide the current generation only; they must not override brand, compliance, or selected style pack.
- Gambling boss guidance remains VFX-driver guidance; game boss guidance remains playable challenge guidance.

---

### Task 1: Add Prompt Behavior Tests

**Files:**
- Modify: `tests/test_image_prompt_guardrails.py`
- Modify: `tests/test_video_storyboard.py`

**Interfaces:**
- Consumes: `build_creative_strategy()`
- Consumes: `OpenAILLMProvider.generate_image_briefs()`
- Consumes: `backend.app.services.video_service._storyboard_to_prompt()`

- [ ] **Step 1: Add image-prompt test requiring new field names in system instruction**
- [ ] **Step 2: Add video-prompt test requiring new field summaries in prompt text**
- [ ] **Step 3: Run targeted tests and verify they fail because prompt support is missing**

### Task 2: Implement Prompt Integration

**Files:**
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/services/video_service.py`

**Interfaces:**
- Produces: OpenAI system instruction that explicitly follows `brand_profile`, `style_pack_id`, `style_pack`, `country_overlay`, `boss_guidance`, and `reference_signal_pack`
- Produces: video prompt text containing safe summaries of the same fields

- [ ] **Step 1: Extend `_creative_strategy_system_instruction()`**
- [ ] **Step 2: Extend `_creative_strategy_v2_prompt_block()`**
- [ ] **Step 3: Add small summary helpers where needed**
- [ ] **Step 4: Run targeted tests until green**

### Task 3: Verification

**Files:**
- Test only

- [ ] **Step 1: Run prompt tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py -q`

Expected: PASS.

- [ ] **Step 2: Run lint on touched files**

Run: `.venv\Scripts\python.exe -m ruff check backend/app/integrations/llm/openai_provider.py backend/app/services/video_service.py tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py`

Expected: PASS.
