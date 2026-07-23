# Storyboard V2 Final Fix Task 1 - Round 13 Report

- Date: 2026-07-23
- Workspace: `C:\Users\panda\Documents\Advertising Automation\.worktrees\storyboard-v2-dynamic-action-arc`
- Branch: `codex/storyboard-v2-dynamic-action-arc`
- Baseline HEAD: `ba1f6a4487f412d6341e49dce24297e1aaeb9a1e`
- Scope: only the one remaining Important in `.superpowers/sdd/final-fix-12-review.md`

## Result

This round removes the required-core truncation from per-moment final coverage. Every non-omit moment now gets one complete, deduplicated, whitespace-normalized source set. When a reference behavior graph exists, that set is validated fail-closed against the graph beat namespace. The existing no-reference path retains a plan-local allowed source namespace. The same complete moment source set is then required for the execution scene, affirmed target execution evidence, and preparation/action/payoff/return binding.

The global required-core set is now limited to global transfer-or-valid-omit obligations and executed-core accounting. It no longer reduces a moment's declared supporting or decorative binding.

## Root Cause and Production Fix

The root cause in `validate_final_storyboard_action_coverage(...)` was:

```text
required_id_set.intersection(moment.source_behavior_beat_ids)
```

That intersection reduced the complete moment source set to required-core sources, so a core+supporting or core+decorative moment could pass with only the core source bound.

Production changes:

1. Build a trimmed and deduplicated source set for every non-omit moment.
2. When a reference behavior graph exists, reject every source ID outside its beat namespace.
3. Preserve the existing no-reference Storyboard V2 path through a plan-local allowed namespace.
4. Normalize scene source IDs in `_scene_links_moment(...)` and require the complete moment source set.
5. Use the complete source set for execution scene, affirmed execution evidence, and preparation/action/payoff/return checks.
6. Count global executed core obligations only through `required core AND moment source set AND evidence source set`, preserving the old fail-closed behavior when no moment actually declares a required core source.
7. Preserve ba1f6a4 behavior for assigned-beat same-scene binding, strict positive time overlap, legal multi-moment joint scenes, 3/2 calls, and private/public boundaries.

## Strict TDD Evidence

### RED

Tests were added before production code. Command:

```text
python -m pytest tests/test_storyboard_director_coverage_service.py -k "missing_supporting_source_from_mixed_execution or missing_decorative_source_from_mixed_preparation or allows_complete_normalized_mixed_importance_binding or rejects_unknown_moment_source_id" -q
```

Observed result:

```text
3 failed, 1 passed, 61 deselected in 0.41s
```

Expected failures:

1. A core+supporting moment omitted the supporting source from execution scene/evidence without rejection.
2. A core+decorative moment omitted the decorative source from preparation without rejection.
3. A moment declared an unknown graph source without rejection.

The positive complete mixed-importance fixture already passed during RED, proving that the legal fixture itself was valid.

### GREEN

The same four tests after the minimal production fix:

```text
4 passed, 61 deselected in 0.12s
```

Complete coverage-service regression after the final adjustment:

```text
65 passed in 0.22s
```

## Fresh Pre-Commit Verification on 2026-07-23

### Focused Three Files

```text
python -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

Result:

```text
218 passed, 1 warning in 22.44s
```

### Ruff

```text
python -m ruff check backend tests
```

Result:

```text
All checks passed!
```

### Full Pytest

```text
python -m pytest -q
```

Result:

```text
724 passed, 2 warnings in 109.00s
```

### 3/2 Calls and Private/Public Boundary Focus

Covered:

- uncached success uses exactly 3 LLM calls;
- invalid omit stops exactly after Call 2;
- formatter hides registered private IDs;
- polling preserves unregistered longer private-like tokens and does not broaden scrub boundaries.

Result:

```text
4 passed, 1 warning in 4.02s
```

## Diff, Scope, and Sensitive Scans

- `git diff --check`: passed.
- Strict UTF-8 decode and replacement-character checks: passed.
- Added-line sensitive scan found no private key, GitHub token, OpenAI-style key, or environment secret assignment.
- No `AGENTS.md` or `.env*` change.
- No public API/schema, polling/storyboard_text, queue/worker/provider/frontend/migration change.
- Added production lines contain no `FINAL_TEXT_OVERLAY_LOCKS`, sword, diamond, `x200,000`, or `200,000` creative hardcoding.
- Did not address final branch review items #3-#7 or any Minor.
- No push, merge, or rebase.

## Changed Files

- `backend/app/services/storyboard_director_coverage_service.py`
- `tests/test_storyboard_director_coverage_service.py`
- `.superpowers/sdd/progress.md`
- `.superpowers/sdd/final-review-fix-13-report.md`

## Concern

No functional concern remains for this finding. The compatibility boundary is deliberate: if no reference behavior graph exists, non-omit source IDs belong to the plan-local allowed namespace; if a graph exists, its beat IDs are authoritative and unknown IDs fail closed.

The warnings were not introduced by this logic change: the existing Starlette `httpx`/`httpx2` deprecation warning, plus one full-suite aiosqlite worker warning caused by a callback reaching an already-closed event loop. All 724 tests still passed.
