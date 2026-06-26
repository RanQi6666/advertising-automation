# GAJA Game Creative Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reusable GAJA and mini-game-pool creative strategy contexts so image briefs and 12-second first/last-frame video prompts produce ad-like assets grounded in the GAJA landing page.

**Architecture:** Introduce a small service that classifies work-order and material-generation context into `gaja_brand` or `mini_game_pool`, then stores that strategy in campaign, topic, draft, asset, and video metadata. Existing LLM providers consume the context through metadata and prompt instructions instead of hard-coding behavior in every endpoint.

**Tech Stack:** FastAPI services, SQLAlchemy models, Pydantic schemas, pytest.

## Global Constraints

- Do not submit forms or use real credentials from the GAJA landing page.
- Keep GAJA visible in brand ads from the first frame; keep mini-game-pool ads gameplay-led until the GAJA game-hub end card.
- Keep 12-second first/last-frame videos as the target workflow.
- Avoid unsupported winning guarantees or platform UI screenshots in generation prompts.
- Do not commit `AGENTS.md`, `.env`, `.env.production`, or real secrets.

---

### Task 1: Creative Strategy Classifier

**Files:**
- Create: `backend/app/services/game_creative_strategy.py`
- Test: `tests/test_game_creative_strategy.py`

**Interfaces:**
- Produces: `build_game_creative_strategy(context: Mapping[str, Any]) -> dict[str, Any] | None`
- Produces: `merge_creative_strategy(metadata: dict | None, context: Mapping[str, Any]) -> dict`

- [ ] **Step 1: Write failing tests** for GAJA brand and mini-game-pool detection.
- [ ] **Step 2: Run** `.venv\Scripts\python.exe -m pytest tests/test_game_creative_strategy.py -q` and confirm missing module failure.
- [ ] **Step 3: Implement classifier** with deterministic template payloads.
- [ ] **Step 4: Run the same tests** and confirm they pass.

### Task 2: Persist Strategy Context

**Files:**
- Modify: `backend/app/services/material_generation_service.py`
- Modify: `backend/app/services/copywriting_service.py`
- Modify: `backend/app/services/ad_generation_service.py`
- Test: `tests/test_game_creative_strategy.py`

**Interfaces:**
- Consumes: `merge_creative_strategy(...)`
- Produces: `metadata_json["creative_strategy"]` on generated campaigns, drafts, image assets, and video jobs when matching signals are present.

- [ ] **Step 1: Write failing tests** for material-generation context and copy metadata inheritance.
- [ ] **Step 2: Run targeted tests** and confirm missing metadata assertions fail.
- [ ] **Step 3: Add metadata propagation** in material context creation, copy draft creation, and publishing campaign setup.
- [ ] **Step 4: Run targeted tests** and confirm they pass.

### Task 3: Prompt Consumption

**Files:**
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/responses_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Modify: `backend/app/services/creative_service.py`
- Modify: `backend/app/services/video_service.py`
- Test: `tests/test_image_prompt_guardrails.py`
- Test: `tests/test_video_storyboard.py`

**Interfaces:**
- Consumes: `creative_strategy` from draft, campaign, and storyboard context metadata.
- Produces: image brief and storyboard prompts that explicitly distinguish first-frame and last-frame requirements for `gaja_brand` and `mini_game_pool`.

- [ ] **Step 1: Write failing prompt-capture tests** for strategy payload inclusion.
- [ ] **Step 2: Run targeted tests** and confirm expected prompt/context strings are missing.
- [ ] **Step 3: Update prompts and metadata merging** to include strategy context.
- [ ] **Step 4: Run targeted tests** and confirm they pass.

### Task 4: Verification

**Files:**
- Verify changed backend tests and lint.

- [ ] **Step 1: Run** `.venv\Scripts\python.exe -m pytest tests/test_game_creative_strategy.py tests/test_image_prompt_guardrails.py tests/test_video_storyboard.py -q`
- [ ] **Step 2: Run** `.venv\Scripts\python.exe -m ruff check backend tests`
- [ ] **Step 3: Run** `git status --short` and confirm no ignored/private files are staged.
