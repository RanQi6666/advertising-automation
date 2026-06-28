# Task 3 Report: Add Topic Angle Metadata and Provider Payload Support

## Scope

- Updated `backend/app/schemas/ai.py`
- Updated `backend/app/services/topic_service.py`
- Updated `backend/app/integrations/llm/openai_provider.py`
- Updated `backend/app/integrations/llm/mock_provider.py`
- Updated `tests/test_topic_context_slimming.py`

## TDD Log

### Red test command

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_topic_context_slimming.py::test_topic_source_data_records_angle_plan_slot tests/test_topic_context_slimming.py::test_openai_topic_generation_sends_compact_payload -q
```

### Expected failing output captured

```text
FF
E   TypeError: TopicService._topic_from_candidate() got an unexpected keyword argument 'angle_plan_item'
E   KeyError: 'schema_version'
```

The failures matched the brief: topic metadata storage did not accept `angle_plan_item`, and the OpenAI compact payload did not preserve v2 `creative_strategy` fields.

## Implementation summary

1. Added internal `TopicCandidate.angle_type` support.
2. Switched topic strategy construction to `build_creative_strategy()` and `compact_creative_strategy()`.
3. Added angle-plan lookup helpers in `TopicService` and stored:
   - `source_data["topic_angle"]`
   - `source_data["angle_type"]`
4. Updated topic generation and streaming loops to align candidate index with `creative_strategy.topic_angle_plan`.
5. Updated OpenAI topic prompt guidance to require angle diversity for v2 strategy payloads and parse returned `angle_type`.
6. Reused the shared strategy compactor in the OpenAI provider so v2 payload keys survive slimming.
7. Updated the mock provider so topic candidates inherit per-slot `angle_type` and angle-purpose context from the plan.
8. Refreshed topic-context tests from legacy GAJA template expectations to v2 strategy expectations.

## Verification

### Targeted green run

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_topic_context_slimming.py::test_topic_source_data_records_angle_plan_slot tests/test_topic_context_slimming.py::test_openai_topic_generation_sends_compact_payload -q
```

Result:

```text
..
2 passed in 1.25s
```

### Required full test run

```powershell
& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_topic_context_slimming.py -q
```

Result:

```text
........
8 passed in 1.25s
```

## Constraints check

- No external request/response schemas were changed.
- No auth, route, token-name, callback, or return-url behavior was changed.
- No new required inbound fields were introduced.
- No single-brand default strategy was hard-coded.
- Topic strategy uses compact v2 metadata and supports three default angle slots when present.
- Prompt guidance preserves Meta/Facebook compliance guardrails around sensitive attributes.

## Fix follow-up

### What I changed

- Updated `backend/app/services/topic_service.py` so topic-angle resolution prefers a unique `candidate.angle_type` match from `creative_strategy.topic_angle_plan`, then falls back to the original positional slot mapping when `angle_type` is missing or ambiguous.
- Updated both topic generation paths to use the new resolver so regular and streaming topic creation store the correct `source_data["topic_angle"]`.
- Added focused regression coverage in `tests/test_topic_context_slimming.py` for:
  - out-of-slot-order candidate mapping by `angle_type`
  - mock-provider topic candidates inheriting distinct `angle_type` values from `creative_strategy.topic_angle_plan`

### Tests run

- `& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_topic_context_slimming.py::test_angle_plan_item_prefers_unique_angle_type_match_over_position tests/test_topic_context_slimming.py::test_mock_provider_uses_strategy_plan_angle_types -q`
  - Result: `2 passed in 0.96s`
- `& 'C:\Users\panda\Documents\Advertising Automation\.venv\Scripts\python.exe' -m pytest tests/test_topic_context_slimming.py -q`
  - Result: `10 passed in 1.07s`

### Commit SHA

- `PENDING`
