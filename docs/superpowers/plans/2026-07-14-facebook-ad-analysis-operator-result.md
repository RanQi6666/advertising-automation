# Facebook Ad Analysis Operator Result Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the public contents of `facebook_ad_analysis_v1` with the approved concise, operator-readable result while preserving the existing async API, job IDs, media processing, public research, and persistence flow.

**Architecture:** Keep the deterministic Facebook metrics engine as the source of truth, but introduce a strict operator-result schema and rebuild the final assembler around nine public fields. Update the LLM contract to produce operator-oriented sections, while retaining compatibility adapters and deterministic fallbacks so provider failures or older output shapes cannot fail the job.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy async services, existing OpenAI-compatible LLM providers, pytest, Ruff, Markdown/JSON documentation.

---

## File Map

- Modify `backend/app/schemas/facebook_ad_analysis.py`: strict public Pydantic models and limits.
- Modify `backend/app/services/facebook_ad_analysis_assembler.py`: deterministic assembly, compatibility mapping, fallbacks, sorting, truncation, and research/media sanitization.
- Modify `backend/app/services/external_ad_performance_analysis_service.py`: advertise the new result contract to the LLM while preserving task flow.
- Modify `backend/app/integrations/llm/openai_provider.py`: new JSON prompt and normalized provider output.
- Modify `backend/app/integrations/llm/mock_provider.py`: valid mock output for local and CI verification.
- Modify `tests/test_facebook_ad_analysis_assembler.py`: public contract, fallbacks, truth ownership, media safety, and research limits.
- Modify `tests/test_external_ad_performance_prompt.py`: prompt requirements and no-breakdown safeguards.
- Modify `docs/外部系统投放数据分析对接说明.md`: replace the old large result section with the approved result and field rules.
- Modify `tests/test_external_ad_performance_docs_examples.py`: validate the response example embedded in the integration document.

### Task 1: Define the strict operator result schema

**Files:**
- Modify: `backend/app/schemas/facebook_ad_analysis.py`
- Test: `tests/test_facebook_ad_analysis_assembler.py`

- [ ] **Step 1: Replace old schema expectations with failing contract tests**

Add tests that validate these exact top-level keys:

```python
assert set(result) == {
    "schema_version",
    "platform",
    "summary",
    "overall_decision",
    "targeting_analysis",
    "adjustment_plans",
    "copywriting_analysis",
    "media_analysis",
    "market_intelligence",
    "data_gaps",
}
assert len(result["summary"]) <= 100
assert 1 <= len(result["adjustment_plans"]) <= 5
```

Add direct Pydantic rejection tests for an old key and over-limit arrays.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_analysis_assembler.py -q
```

Expected: failures because the current schema still requires the old analysis tree.

- [ ] **Step 3: Implement the Pydantic models**

Define strict models for:

```text
OverallDecision
TargetingAnalysisItem
AdjustmentPlan
CopywritingAnalysis
MediaImprovement
MediaAnalysis
MarketReference
MarketIntelligence
FacebookAdAnalysisResult
```

Use `Literal` for all enums, `max_length=100` for short summaries, `max_length=3` for targeting/media/reference/data-gap lists, and `min_length=1, max_length=5` for adjustment plans. Keep `extra="forbid"` at every public object boundary.

- [ ] **Step 4: Run schema tests**

Run the focused test command again.

Expected: schema-specific tests pass; assembler tests may still fail because assembly has not migrated.

### Task 2: Rebuild the assembler with deterministic fallbacks

**Files:**
- Modify: `backend/app/services/facebook_ad_analysis_assembler.py`
- Test: `tests/test_facebook_ad_analysis_assembler.py`

- [ ] **Step 1: Write failing behavior tests**

Cover:

```text
- rule-owned action, priority, and main problem override conflicting LLM values
- first adjustment plan addresses the rule-owned primary bottleneck
- missing landing-page views produces an event-tracking verification plan
- targeting returns only meaningful issues and never claims dimension performance
- missing media produces no invented improvements
- local_artifacts and /app/storage never appear in the result
- market references are limited to three and omit technical evidence metadata
- copy recommendations are null when the source copy is missing
- LLM None still yields a valid result with at least one plan
```

- [ ] **Step 2: Run focused tests and verify failure**

Run the focused assembler test command and expect failures against old fields.

- [ ] **Step 3: Implement operator assembly helpers**

Replace the old result builder with focused helpers:

```text
_summary
_overall_decision
_targeting_analysis
_adjustment_plans
_copywriting_analysis
_media_analysis
_market_intelligence
_data_gaps
```

Requirements:

- derive `overall_decision` from `rule_analysis.executive_summary`;
- convert `verdict` to `action` and normalize priority to `high|medium|low`;
- create a deterministic landing-page plan when landing-page evidence exists;
- otherwise create a tracking verification plan when downstream events are missing;
- accept new structured LLM sections, but map old `recommended_actions`, `creative_feedback`, `audience_feedback`, `visual_analysis`, and `summary` when needed;
- deduplicate plans, cap at five, and sort by priority;
- do not output private media summaries or paths;
- translate research records into at most three public references;
- validate through `FacebookAdAnalysisResult.model_validate(...).model_dump(mode="json")`.

- [ ] **Step 4: Run focused tests**

Expected: all assembler tests pass.

### Task 3: Change the LLM result contract and provider normalization

**Files:**
- Modify: `backend/app/services/external_ad_performance_analysis_service.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/integrations/llm/mock_provider.py`
- Test: `tests/test_external_ad_performance_prompt.py`

- [ ] **Step 1: Add failing prompt tests**

Assert the system prompt requires the exact operator sections and states:

```text
- no country, age, gender, device, or placement performance claims without submitted breakdowns
- targeting_analysis contains only meaningful issues and at most three items
- adjustment_plans contains one to five concrete operator actions
- public research cannot verify actual performance
- media analysis must not invent visual observations after processing failure
- output uses Simplified Chinese except recommended ad copy keeps source language
```

- [ ] **Step 2: Run prompt tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_prompt.py -q
```

- [ ] **Step 3: Update the service contract payload**

Replace old `rule_owned_fields` with an operator contract descriptor listing:

```text
summary
overall_decision
targeting_analysis
adjustment_plans
copywriting_analysis
media_analysis
market_intelligence
data_gaps
```

State that metrics, the primary bottleneck, and data-quality facts remain rule-owned.

- [ ] **Step 4: Update OpenAI prompt and normalization**

Make the prompt request valid JSON with the exact new section names. Normalize nested dictionaries and arrays with hard caps rather than flattening all advice to strings. Preserve old-key parsing only as a compatibility fallback in the assembler.

- [ ] **Step 5: Update mock provider output**

Return a compact valid contribution with a summary, structured plans, copywriting analysis, media analysis, optional targeting issues, and data gaps. Mock output must not claim dimension-level performance.

- [ ] **Step 6: Run prompt and assembler tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_prompt.py tests/test_facebook_ad_analysis_assembler.py -q
```

Expected: pass.

### Task 4: Rewrite the external integration result documentation

**Files:**
- Modify: `docs/外部系统投放数据分析对接说明.md`
- Modify: `tests/test_external_ad_performance_docs_examples.py`

- [ ] **Step 1: Add a failing documentation contract test**

Extract the primary successful GET response JSON from a marker-delimited code block and assert:

```python
result = payload["data"]["result"]
assert result["schema_version"] == "facebook_ad_analysis_v1"
assert "adjustment_plans" in result
assert "executive_summary" not in result
assert "recommended_actions" not in result
```

- [ ] **Step 2: Run docs tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ad_performance_docs_examples.py -q
```

- [ ] **Step 3: Replace the old result section**

Preserve the approved endpoint, auth, request, media, polling, and error sections. Replace the old large result tree with the eight business fields, one complete successful GET example, field tables, enum values, array limits, no-breakdown rules, media/research failure behavior, and a migration table from old fields to new fields.

- [ ] **Step 4: Validate JSON and Markdown structure**

Run docs tests plus a script that parses every `json` fenced block and checks balanced fences.

Expected: all document examples parse and no old public result fields remain in the current contract section.

### Task 5: Run regression and runtime-oriented verification

**Files:**
- Test only, unless failures reveal required scoped fixes.

- [ ] **Step 1: Run targeted external-analysis tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_facebook_ad_metrics.py tests/test_facebook_ad_analysis_assembler.py tests/test_external_ad_performance_contract.py tests/test_external_ad_performance_prompt.py tests/test_external_ad_performance_jobs.py tests/test_external_ad_performance_docs_examples.py -q
```

Expected: all pass.

- [ ] **Step 2: Run Ruff**

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
```

Expected: no violations.

- [ ] **Step 3: Run the full test suite**

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Validate the working tree scope**

Run:

```powershell
git diff --check
git status --short
```

Confirm `AGENTS.md`, `.env`, `.env.production`, real tokens, and unrelated untracked files are not staged.

- [ ] **Step 5: Commit scoped implementation**

Stage only the schema, assembler, provider/service, tests, approved plan, and integration document. Keep pre-existing unrelated `.env.example`, `.env.production.example`, `.cursor/`, and unrelated plan/document files out of the commit.

Use:

```powershell
git commit -m "feat: simplify facebook analysis result for operators"
```
