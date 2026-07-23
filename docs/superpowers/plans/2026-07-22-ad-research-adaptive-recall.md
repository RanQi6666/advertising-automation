# Ad Research Adaptive Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise useful ad recall without lowering quality by accepting ads active for at least one day, recording technical rejection causes, and feeding prior query and rejection evidence back to GPT-5.4 mini for each supplemental collection round.

**Architecture:** Add a structured, generic media qualification result in the media inspector while preserving the existing boolean compatibility method. The orchestrator will inspect each unique candidate once, aggregate technical and model exclusion reasons, keep round summaries, and globally deduplicate model-generated queries before collection. The query planner remains category/country agnostic and receives diagnostic context so GPT-5.4 mini can change its retrieval direction rather than relying on a hard-coded India gambling dictionary.

**Tech Stack:** Python 3.12, FastAPI service layer, Pydantic schemas, SQLAlchemy async sessions, httpx Responses-compatible model gateway, pytest, Ruff.

---

### Task 1: Technical qualification diagnostics

**Files:**
- Modify: `backend/app/services/ad_research_media.py`
- Test: `tests/test_ad_research_media.py`

- [ ] Add failing tests for `days_running` values `None`, `0`, and `1`, duration values `30` and `30.1`, and simultaneous rejection reasons.
- [ ] Run `pytest tests/test_ad_research_media.py -q` and verify the new tests fail because structured inspection and the one-day threshold do not exist.
- [ ] Add immutable `TechnicalQualification`, set `MIN_ACTIVE_DAYS = 1`, implement `inspect()`, and keep `is_technically_qualified()` as a compatibility wrapper.
- [ ] Re-run the media tests and verify all pass.

### Task 2: Adaptive multi-round orchestration

**Files:**
- Modify: `backend/app/services/ad_research_orchestrator.py`
- Test: `tests/test_ad_research_orchestrator.py`

- [ ] Add failing tests proving the second planning call receives `previous_queries`, technical rejection counts, model exclusion counts, and duplicate counts.
- [ ] Add a failing test proving a query repeated in a later round is not collected twice.
- [ ] Add a failing test proving kept ads survive later rounds and a target of 25 does not finish after one result.
- [ ] Add a failing test proving final summaries expose thresholds, rejection summaries, and per-round statistics.
- [ ] Run the orchestrator tests and verify expected failures.
- [ ] Implement case-insensitive global query deduplication, one-time candidate inspection, reason aggregation, model exclusion aggregation, and round summaries.
- [ ] Preserve the existing cross-round result map and stop only at `target_count`, four rounds, or 500 raw candidates.
- [ ] Re-run orchestrator tests and verify all pass.

### Task 3: Diagnostic-aware GPT query planning

**Files:**
- Modify: `backend/app/services/ad_research_model.py`
- Test: `tests/test_ad_research_model.py`

- [ ] Add a failing gateway payload test proving historical queries and rejection summaries reach the model.
- [ ] Add a failing prompt assertion requiring the planner to avoid previous queries and adapt to duration, category mismatch, and duplicate signals.
- [ ] Add a test proving duplicate model queries are deduplicated within one response.
- [ ] Run model tests and verify the prompt-related tests fail.
- [ ] Update the generic lawful public-library planner prompt without adding country/category-specific keyword dictionaries or circumvention instructions.
- [ ] Re-run model tests and verify all pass.

### Task 4: Documentation and regression verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-21-ad-research-engine-design.md`
- Modify: `docs/ad-research-api.md`

- [ ] Change the active-time hard gate to `>= 1 day` and document diagnostic summary fields.
- [ ] Run targeted API and worker regression tests.
- [ ] Run all advertisement-research tests, full pytest, and Ruff.
- [ ] Run `git diff --check`, inspect `git status --short`, and confirm no `AGENTS.md`, `.env*`, credentials, or unrelated files are staged.
- [ ] Commit only the approved files on `codex/ad-research-adaptive-recall`.
