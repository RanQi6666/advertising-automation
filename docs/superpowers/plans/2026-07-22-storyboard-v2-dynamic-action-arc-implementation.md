# Storyboard V2 Dynamic Action Arc Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Storyboard V2 transfer a reference video's strongest physical or state-changing action into one continuous generated shot, while allowing deliberate middle-frame divergence and still returning naturally to the exact supplied last frame.

**Architecture:** Keep the existing three-model-call Celery workflow. Add private Pydantic action-arc contracts, a pure-Python pre-Call-3 coverage review, provider prompt/normalization support, and deterministic final storyboard validation. Pass only compact correction requirements into the existing third call and continue returning the same public `storyboard_text` shape.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy async ORM, Celery/Redis, OpenAI-compatible Responses provider, pytest, Ruff.

## Global Constraints

- Keep `POST /api/v1/integrations/ai/storyboard-v2` and `GET /api/v1/integrations/ai/jobs/{job_id}` unchanged.
- Keep public output centered on `storyboard_text`, `duration_seconds`, and `aspect_ratio`; never expose analysis, director plans, reviews, evidence IDs, or raw provider output.
- Keep `TEXT_QUEUE_NAME`, Redis/Celery routing, retries, cached-resume behavior, and exactly three LLM calls for an uncached success.
- Do not add a fourth model review, queue, migration, frontend change, video editing, stitching, compositing, or downstream video-provider change.
- First and last images constrain endpoints. Middle pose, orientation, position, composition, scale, camera, lighting, temporary UI, and effects may diverge deliberately.
- A final-pose mismatch means plan a return; it is not enough to omit a core action.
- Strategy order is `preserve` -> `adapt` -> `replace_with_equivalent` -> `omit`.
- `omit` requires both literal/adapted infeasibility and equivalent-intensity replacement infeasibility.
- Plan subject motion, camera motion, and effects independently. Effects alone cannot impersonate action.
- Allocate action, return, and final lock dynamically. Do not add fixed seconds, fixed ratios, or sample-specific recipes.
- Produce one continuous generated shot; reference cuts become in-shot camera behavior.
- Do not hard-code any weapon, reward, character, product, brand, UI, transformation, or effect example.
- Do not restore `[FINAL_TEXT_OVERLAY_LOCKS]`.
- Stage literal files only; never stage `AGENTS.md`, `.env*`, credentials, backups, or unrelated dirt.

## File Map

- `backend/app/schemas/ai.py`: private action-arc, signature-transfer, coverage-review, and scene contracts.
- `backend/app/services/storyboard_director_coverage_service.py`: pure deterministic preflight review and final validation.
- `backend/app/integrations/llm/base.py`: optional Call-3 correction argument.
- `backend/app/integrations/llm/openai_provider.py`: generic prompts, normalization, correction forwarding.
- `backend/app/integrations/llm/mock_provider.py`: valid mock output for the new contract.
- `backend/app/services/external_ai_generation_service.py`: review orchestration, private metadata, final validation, public formatting.
- `tests/test_storyboard_director_coverage_service.py`: isolated review/validation tests.
- `tests/test_frame_anchored_storyboard_provider.py`: schema, prompt, and normalization tests.
- `tests/test_external_ai_generation.py`: three-call, metadata, retry, formatter, and public-contract tests.

---

### Task 1: Add private action-arc and scene execution schemas

**Files:**
- Modify: `backend/app/schemas/ai.py:242-412`
- Test: `tests/test_frame_anchored_storyboard_provider.py:1-110`

**Interfaces:**
- Produces `DirectorActionArcWindow` and `DirectorActionCoverageReview`.
- Extends `DirectorSignatureMoment`, `FrameAnchoredDirectorPlan`, and `FrameAnchoredStoryboardScene`.
- Keeps `target_adaptation` for cached metadata; new execution fields are authoritative.

- [ ] **Step 1: Write failing schema tests**

Add `DirectorActionArcWindow` to imports, extend `_director_plan_payload()` with a generic action/return/final-lock arc and authoritative signature fields, then add:

```python
def test_director_action_arc_window_rejects_reversed_ratio() -> None:
    with pytest.raises(ValidationError, match="action arc window end ratio"):
        DirectorActionArcWindow(
            window_id="action_window",
            phase="action",
            start_ratio=0.6,
            end_ratio=0.4,
            objective="Execute the evidence-backed action.",
            subject_motion_intensity=0.8,
            camera_intensity=0.5,
            effect_intensity=0.3,
        )


def test_director_plan_rejects_unknown_action_window_dependency() -> None:
    payload = _director_plan_payload()
    payload["action_arc_windows"][0]["depends_on"] = ["missing_window"]
    with pytest.raises(ValidationError, match="action arc dependencies"):
        FrameAnchoredDirectorPlan.model_validate(payload)


def test_non_omitted_signature_requires_action_payoff_and_return() -> None:
    payload = _director_plan_payload()
    payload["signature_moment_plan"][0]["adapted_action"] = ""
    with pytest.raises(ValidationError, match="requires an adapted action"):
        FrameAnchoredDirectorPlan.model_validate(payload)


def test_equivalent_replacement_is_valid() -> None:
    payload = _director_plan_payload(signature_strategy="replace_with_equivalent")
    assert FrameAnchoredDirectorPlan.model_validate(payload).signature_moment_plan[0].strategy == (
        "replace_with_equivalent"
    )


def test_omit_requires_equivalent_replacement_failure() -> None:
    payload = _director_plan_payload(
        signature_strategy="omit",
        assigned_beat_id=None,
        omission_reason="Literal and adapted execution contradict target facts.",
    )
    payload["signature_moment_plan"][0]["equivalent_replacement_failure"] = None
    with pytest.raises(ValidationError, match="equivalent replacement failure"):
        FrameAnchoredDirectorPlan.model_validate(payload)


def test_endpoint_pose_mismatch_is_not_an_omission_reason() -> None:
    payload = _director_plan_payload(
        signature_strategy="omit",
        assigned_beat_id=None,
        omission_reason="The action pose differs from the final pose.",
    )
    payload["signature_moment_plan"][0]["equivalent_replacement_failure"] = (
        "The middle framing differs from the final composition."
    )
    with pytest.raises(ValidationError, match="endpoint mismatch"):
        FrameAnchoredDirectorPlan.model_validate(payload)
```

Use this default signature shape in `_director_plan_payload()`:

```python
{
    "moment_id": "signature_001",
    "moment_type": "combined",
    "source_evidence": ["The reference contains action, emphasis, and a consequence."],
    "source_behavior_beat_ids": ["reference_action"],
    "transfer_role": "primary_action",
    "strategy": signature_strategy,
    "target_adaptation": "Legacy-compatible summary.",
    "adapted_action": "Execute the target-compatible causal action." if signature_strategy != "omit" else "",
    "temporary_divergence": "Allow a different middle pose and composition." if signature_strategy != "omit" else "",
    "camera_support": "Reframe continuously around execution." if signature_strategy != "omit" else "",
    "effect_support": "Support the consequence without replacing action." if signature_strategy != "omit" else "",
    "visible_payoff": "Show the resulting target-state change." if signature_strategy != "omit" else "",
    "return_strategy": "Settle continuously into the exact final anchor." if signature_strategy != "omit" else "",
    "assigned_beat_id": assigned_beat_id,
    "omission_reason": omission_reason,
    "equivalent_replacement_failure": (
        "No target-compatible equivalent preserves the causal role."
        if signature_strategy == "omit" and omission_reason
        else None
    ),
}
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_frame_anchored_storyboard_provider.py -k "action_arc or equivalent_replacement or endpoint_pose" -q
```

Expected: FAIL because the new types and fields do not exist.

- [ ] **Step 3: Implement intrinsic schemas**

Add to `backend/app/schemas/ai.py`:

```python
DirectorActionArcPhase = Literal[
    "anchor_hold", "departure", "preparation", "action",
    "impact", "payoff", "return", "final_lock",
]
DirectorSignatureTransferRole = Literal[
    "causal_setup", "primary_action", "interaction", "impact",
    "visible_result", "camera_emphasis", "effect_emphasis",
    "overlay_lifecycle", "other",
]


class DirectorActionArcWindow(BaseModel):
    window_id: str = Field(min_length=1)
    phase: DirectorActionArcPhase
    start_ratio: float = Field(ge=0, le=1)
    end_ratio: float = Field(gt=0, le=1)
    objective: str = Field(min_length=1)
    subject_motion_intensity: float = Field(ge=0, le=1)
    camera_intensity: float = Field(ge=0, le=1)
    effect_intensity: float = Field(ge=0, le=1)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_window(self) -> "DirectorActionArcWindow":
        if self.end_ratio <= self.start_ratio:
            raise ValueError("action arc window end ratio must be after its start ratio")
        return self


class DirectorActionCoverageReview(BaseModel):
    status: Literal["pass", "corrective"]
    required_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    covered_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    uncovered_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    invalid_omission_moment_ids: list[str] = Field(default_factory=list)
    missing_execution_detail_moment_ids: list[str] = Field(default_factory=list)
    missing_return_moment_ids: list[str] = Field(default_factory=list)
    correction_requirements: list[str] = Field(default_factory=list)
```

Extend `DirectorSignatureMoment` with the approved fields and strategy. Its validator must require assigned core beat, `adapted_action`, `visible_payoff`, and `return_strategy` for non-omitted moments; require `temporary_divergence` for action/interaction/impact; require camera/effect support by moment type; and require both omission explanations for `omit`.

Add a generic helper that rejects endpoint-only explanations without naming any sample content:

```python
def _endpoint_mismatch_only(*reasons: str) -> bool:
    text = " ".join(reason.casefold() for reason in reasons)
    endpoint = any(term in text for term in ("final", "last frame", "endpoint", "ending"))
    mismatch = any(term in text for term in ("pose", "position", "orientation", "framing", "composition", "scale"))
    infeasible = any(term in text for term in ("infeasible", "impossible", "contradict", "no compatible", "cannot execute"))
    return endpoint and mismatch and not infeasible
```

Extend `FrameAnchoredDirectorPlan`:

```python
action_arc_windows: list[DirectorActionArcWindow] = Field(default_factory=list)
final_anchor_return: str = ""
```

Validate unique ordered window IDs and known dependencies. Extend `FrameAnchoredStoryboardScene`:

```python
signature_moment_ids: list[str] = Field(default_factory=list)
source_behavior_beat_ids: list[str] = Field(default_factory=list)
subject_motion_intensity: float | None = Field(default=None, ge=0, le=1)
camera_intensity: float | None = Field(default=None, ge=0, le=1)
effect_intensity: float | None = Field(default=None, ge=0, le=1)
anchor_return_instruction: str | None = None
```

- [ ] **Step 4: Run schema tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_frame_anchored_storyboard_provider.py -k "director_plan or signature or action_arc" -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git status --short
git add backend/app/schemas/ai.py tests/test_frame_anchored_storyboard_provider.py
git commit -m "feat: add storyboard action arc schemas"
```

---

### Task 2: Add deterministic director review and final storyboard validation

**Files:**
- Create: `backend/app/services/storyboard_director_coverage_service.py`
- Create: `tests/test_storyboard_director_coverage_service.py`

**Interfaces:**
- Produces `review_director_action_coverage(frame_analysis: FrameAnalysis, director_plan: FrameAnchoredDirectorPlan) -> DirectorActionCoverageReview`.
- Produces `validate_final_storyboard_action_coverage(storyboard: FrameAnchoredStoryboard, frame_analysis: FrameAnalysis, review: DirectorActionCoverageReview) -> None`.
- Performs no I/O, provider call, persistence, plan mutation, or sample-specific keyword matching.

- [ ] **Step 1: Write failing preflight-review tests**

Create `tests/test_storyboard_director_coverage_service.py` with builders for a generic target frame pair, one core `ReferenceBehaviorBeat`, a core `DirectorBeat`, one signature moment, and action/return/final-lock windows. Add:

```python
def test_review_marks_unlinked_core_behavior_for_correction() -> None:
    review = review_director_action_coverage(_analysis(), _plan(source_ids=[]))
    assert review.status == "corrective"
    assert review.uncovered_core_behavior_beat_ids == ["core_behavior"]
    assert review.correction_requirements == [
        "Execute uncovered core behavior beat core_behavior in subject/state motion and show its visible payoff."
    ]


def test_review_passes_valid_adapted_action() -> None:
    review = review_director_action_coverage(
        _analysis(), _plan(source_ids=["core_behavior"])
    )
    assert review.status == "pass"
    assert review.covered_core_behavior_beat_ids == ["core_behavior"]
    assert review.correction_requirements == []


def test_review_rejects_effect_only_flattening() -> None:
    plan = _plan(source_ids=["core_behavior"])
    plan = plan.model_copy(
        update={
            "action_arc_windows": [
                window.model_copy(
                    update={
                        "subject_motion_intensity": 0.05,
                        "effect_intensity": 0.9 if window.phase == "action" else 0.1,
                    }
                )
                for window in plan.action_arc_windows
            ]
        }
    )
    review = review_director_action_coverage(_analysis(), plan)
    assert review.status == "corrective"
    assert any("subject/state motion" in item for item in review.correction_requirements)


def test_review_does_not_force_action_without_reference_core_behavior() -> None:
    analysis = _analysis(include_reference=False)
    plan = _plan(source_ids=[]).model_copy(
        update={"signature_moment_plan": [], "action_arc_windows": [], "final_anchor_return": ""}
    )
    assert review_director_action_coverage(analysis, plan).status == "pass"
```

The `_analysis()` builder must use the real `ReferenceBehaviorGraph` types and classify only `importance="core"` plus `behavior_type in {"action", "state"}` as required action evidence.

Use these exact builders above the tests so every referenced helper is local to the new test module:

```python
def _analysis(
    *,
    include_reference: bool = True,
    behavior_type: str = "action",
) -> FrameAnalysis:
    payload: dict[str, object] = {
        "first_frame": {
            "visible_subjects": ["target subject"],
            "visible_text": [],
            "environment": "target environment",
            "composition": "opening composition",
            "camera_perspective": "eye level",
            "visual_style": "cinematic",
            "color_and_lighting": "controlled contrast",
            "opening_state": "stable opening anchor",
        },
        "last_frame": {
            "visible_subjects": ["target subject"],
            "visible_text": [],
            "environment": "target environment",
            "composition": "ending composition",
            "camera_perspective": "eye level",
            "visual_style": "cinematic",
            "color_and_lighting": "controlled contrast",
            "ending_state": "stable ending anchor",
        },
        "transition_brief": {
            "shared_visual_facts": ["same target subject"],
            "continuity_requirements": ["begin and end on supplied anchors"],
            "visual_transition": "diverge for action, then return continuously",
            "narrative_arc": "setup, action, payoff, return, lock",
        },
        "language_analysis": {
            "first_frame_visible_languages": [],
            "last_frame_visible_languages": [],
            "recommended_output_language": "en",
            "reason": "No visible text requires preservation.",
        },
    }
    if include_reference:
        payload["reference_video_analysis"] = {
            "duration_seconds": 6,
            "sample_interval_seconds": 2,
            "segments": [
                {
                    "start_second": 0,
                    "end_second": 6,
                    "subject_presence": {
                        "state": "ready then changed",
                        "visibility": "clear",
                        "screen_position": "centered",
                        "movement": "readable causal movement",
                        "appearance": "reference-only identity",
                        "action": "physical or state-changing action",
                        "interaction": "visible consequence",
                    },
                    "camera": {"movement": "continuous emphasis", "intensity": "high"},
                    "transition": {"type": "continuous", "description": "no edit"},
                    "effects": ["supportive effect peak"],
                    "confidence": "high",
                }
            ],
            "adapted_constraints": {
                "subject_presence": {"strength": "preferred", "instruction": "transfer behavior only"},
                "camera_pattern": {"strength": "preferred", "instruction": "adapt camera emphasis"},
                "transition_pattern": {"strength": "preferred", "instruction": "keep one shot"},
                "effects_pattern": {"strength": "preferred", "instruction": "support action"},
            },
            "behavior_graph": {
                "entities": ["reference subject"],
                "beats": [
                    {
                        "beat_id": "core_behavior",
                        "reference_start_second": 1,
                        "reference_end_second": 4,
                        "description": "A reliable causal action changes the visible state.",
                        "visible_evidence": ["subject motion precedes a visible payoff"],
                        "behavior_type": behavior_type,
                        "importance": "core",
                        "minimum_readable_duration_seconds": 0.8,
                        "depends_on": [],
                    }
                ],
            },
        }
    return FrameAnalysis.model_validate(payload)


def _plan(*, source_ids: list[str]) -> FrameAnchoredDirectorPlan:
    return FrameAnchoredDirectorPlan.model_validate(
        {
            "narrative_objective": "Execute a target-compatible causal action and return.",
            "attention_path": ["anchor", "action", "payoff", "return", "final lock"],
            "tension_curve": ["setup", "climax", "resolution"],
            "climax_beats": [
                {
                    "beat_id": "core_peak",
                    "stage": "climax",
                    "source_evidence": ["The reference contains a reliable causal peak."],
                    "start_ratio": 0.2,
                    "end_ratio": 0.7,
                    "attention_objective": "Keep the causal action readable.",
                    "camera_instruction": "Support without replacing action.",
                    "action_requirement": "Execute subject/state motion.",
                    "effect_requirement": "Reveal the visible payoff.",
                    "importance": "core",
                }
            ],
            "signature_moment_plan": [
                {
                    "moment_id": "signature_action",
                    "moment_type": "combined",
                    "source_evidence": ["Action, emphasis, and consequence are jointly visible."],
                    "source_behavior_beat_ids": source_ids,
                    "transfer_role": "primary_action",
                    "strategy": "adapt",
                    "target_adaptation": "Adapt the causal role to target facts.",
                    "adapted_action": "Execute target-compatible subject/state motion.",
                    "temporary_divergence": "Allow a distinct middle pose and composition.",
                    "camera_support": "Reframe continuously around execution.",
                    "effect_support": "Support the consequence without replacing action.",
                    "visible_payoff": "Show the resulting target-state change.",
                    "return_strategy": "Settle continuously into the exact last anchor.",
                    "assigned_beat_id": "core_peak",
                }
            ],
            "action_arc_windows": [
                {
                    "window_id": "action",
                    "phase": "action",
                    "start_ratio": 0.15,
                    "end_ratio": 0.58,
                    "objective": "Execute readable subject/state motion.",
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.65,
                    "effect_intensity": 0.35,
                    "depends_on": [],
                },
                {
                    "window_id": "payoff",
                    "phase": "payoff",
                    "start_ratio": 0.5,
                    "end_ratio": 0.72,
                    "objective": "Show the visible causal result.",
                    "subject_motion_intensity": 0.55,
                    "camera_intensity": 0.5,
                    "effect_intensity": 0.8,
                    "depends_on": ["action"],
                },
                {
                    "window_id": "return",
                    "phase": "return",
                    "start_ratio": 0.68,
                    "end_ratio": 0.9,
                    "objective": "Return continuously to the final anchor.",
                    "subject_motion_intensity": 0.45,
                    "camera_intensity": 0.4,
                    "effect_intensity": 0.25,
                    "depends_on": ["payoff"],
                },
                {
                    "window_id": "final_lock",
                    "phase": "final_lock",
                    "start_ratio": 0.88,
                    "end_ratio": 1,
                    "objective": "Hold the exact supplied last frame.",
                    "subject_motion_intensity": 0.05,
                    "camera_intensity": 0.05,
                    "effect_intensity": 0.05,
                    "depends_on": ["return"],
                },
            ],
            "final_anchor_return": "Return after payoff and stabilize on the exact last frame.",
            "anchor_adaptation_plan": ["Use endpoints as anchors, not middle-frame pose locks."],
            "anti_flattening_constraints": ["Do not replace action with camera or effects."],
        }
    )


def _valid_final_inputs() -> tuple[
    FrameAnchoredStoryboard,
    FrameAnalysis,
    DirectorActionCoverageReview,
]:
    plan = _plan(source_ids=["core_behavior"])
    analysis = _analysis().model_copy(update={"director_plan": plan})
    review = review_director_action_coverage(analysis, plan)
    storyboard = FrameAnchoredStoryboard.model_validate(
        {
            "duration_seconds": 10,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 1,
                    "frame_anchor": "first_frame",
                    "visual": "Hold the exact supplied opening anchor.",
                },
                {
                    "scene_index": 2,
                    "start_second": 1,
                    "end_second": 6,
                    "frame_anchor": "transition",
                    "visual": "Execute the target-compatible causal action and payoff.",
                    "motion": "The subject completes readable state-changing motion.",
                    "cinematic_beats": ["core_peak"],
                    "camera_instruction": "Reframe continuously around the action.",
                    "action_result_requirement": "Show the visible target-state change.",
                    "effect_timing": "Peak only after the causal motion reads.",
                    "signature_moment_ids": ["signature_action"],
                    "source_behavior_beat_ids": ["core_behavior"],
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.65,
                    "effect_intensity": 0.8,
                },
                {
                    "scene_index": 3,
                    "start_second": 6,
                    "end_second": 8,
                    "frame_anchor": "transition",
                    "visual": "Resolve the payoff and return to the supplied ending composition.",
                    "motion": "The subject settles toward the final state.",
                    "anchor_return_instruction": "Continuously restore final pose, framing, and camera.",
                },
                {
                    "scene_index": 4,
                    "start_second": 8,
                    "end_second": 10,
                    "frame_anchor": "last_frame",
                    "visual": "Lock the exact supplied last frame.",
                },
            ],
        }
    )
    return storyboard, analysis, review


def _valid_short_final_inputs() -> tuple[
    FrameAnchoredStoryboard,
    FrameAnalysis,
    DirectorActionCoverageReview,
]:
    storyboard, analysis, review = _valid_final_inputs()
    short_storyboard = storyboard.model_copy(
        update={
            "duration_seconds": 4,
            "scenes": [
                storyboard.scenes[0].model_copy(update={"end_second": 0.5}),
                storyboard.scenes[1].model_copy(
                    update={
                        "start_second": 0.5,
                        "end_second": 3.25,
                        "anchor_return_instruction": "Return continuously after the payoff.",
                    }
                ),
                storyboard.scenes[-1].model_copy(
                    update={"scene_index": 3, "start_second": 3.25, "end_second": 4}
                ),
            ],
        }
    )
    return short_storyboard, analysis, review
```

- [ ] **Step 2: Write failing final-validation tests**

Build a four-scene 10-second storyboard: exact opening, executable transition action, return scene, exact last-frame scene. Add:

```python
def test_final_validation_rejects_missing_signature_id() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].signature_moment_ids = []
    with pytest.raises(ValueError, match="missing required signature moment"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_id_without_action_direction() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].motion = None
    storyboard.scenes[1].action_result_requirement = None
    with pytest.raises(ValueError, match="lacks executable action or result direction"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_missing_return() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[2].anchor_return_instruction = None
    with pytest.raises(ValueError, match="missing anchor return instruction"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_wrong_final_end_time() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[-1].end_second = 9.5
    with pytest.raises(ValueError, match="final scene must end at requested duration"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
```

- [ ] **Step 3: Run tests and verify module import failure**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_storyboard_director_coverage_service.py -q
```

Expected: FAIL because the service does not exist.

- [ ] **Step 4: Implement the pure review**

Create `backend/app/services/storyboard_director_coverage_service.py`. Use these exact helpers and behavior:

```python
def _required_core_behavior_beats(frame_analysis: FrameAnalysis) -> list[ReferenceBehaviorBeat]:
    reference = frame_analysis.reference_video_analysis
    graph = reference.behavior_graph if reference is not None else None
    if graph is None:
        return []
    return [
        beat for beat in graph.beats
        if beat.importance == "core" and beat.behavior_type in {"action", "state"}
    ]


def _has_required_arc(plan: FrameAnchoredDirectorPlan) -> bool:
    phases = [window.phase for window in plan.action_arc_windows]
    if not {"action", "return", "final_lock"}.issubset(phases):
        return False
    action = phases.index("action")
    returning = phases.index("return")
    lock = phases.index("final_lock")
    return action < returning < lock and plan.action_arc_windows[lock].end_ratio == 1


def _effect_only_flattened(
    core_beats: list[ReferenceBehaviorBeat],
    plan: FrameAnchoredDirectorPlan,
) -> bool:
    if not any(beat.behavior_type == "action" for beat in core_beats):
        return False
    action_windows = [
        window for window in plan.action_arc_windows if window.phase in {"action", "impact"}
    ]
    if not action_windows:
        return True
    peak_subject = max(window.subject_motion_intensity for window in action_windows)
    peak_effect = max(window.effect_intensity for window in action_windows)
    hold_subject = min(
        (
            window.subject_motion_intensity
            for window in plan.action_arc_windows
            if window.phase in {"anchor_hold", "final_lock"}
        ),
        default=0,
    )
    return peak_effect > peak_subject and peak_subject <= hold_subject
```

`review_director_action_coverage()` must:

1. collect required core action/state IDs;
2. map them through `source_behavior_beat_ids`;
3. accept only non-omitted moments with assigned core beat, action, payoff, and return;
4. record invalid omissions and missing details;
5. require action -> return -> final lock only when core evidence exists;
6. add an effect-only correction for physical action flattening;
7. return stable, deduplicated, ID-based correction strings.

Use these exact correction templates:

```python
f"Execute uncovered core behavior beat {beat_id} in subject/state motion and show its visible payoff."
f"Provide executable action, assigned core beat, and visible payoff for signature moment {moment_id}."
f"State how signature moment {moment_id} returns continuously to the exact last anchor."
f"Replace invalid omission {moment_id} with preserve, adapt, or an equivalent target action."
"Provide dynamically ordered action, return, and final_lock windows ending at ratio 1.0."
"Increase subject/state motion for the core action; camera or effects alone cannot execute it."
"State the continuous final-anchor return before the final lock."
```

Return the review without mutating `director_plan`:

```python
return DirectorActionCoverageReview(
    status="corrective" if corrections else "pass",
    required_core_behavior_beat_ids=required_ids,
    covered_core_behavior_beat_ids=covered_ids,
    uncovered_core_behavior_beat_ids=uncovered_ids,
    invalid_omission_moment_ids=invalid_omission_ids,
    missing_execution_detail_moment_ids=missing_execution_ids,
    missing_return_moment_ids=missing_return_ids,
    correction_requirements=list(dict.fromkeys(corrections)),
)
```

- [ ] **Step 5: Implement final deterministic validation**

Add:

```python
def _scene_has_execution(scene: FrameAnchoredStoryboardScene) -> bool:
    return bool(
        (scene.motion or "").strip()
        or (scene.action_result_requirement or "").strip()
    )


def validate_final_storyboard_action_coverage(
    storyboard: FrameAnchoredStoryboard,
    frame_analysis: FrameAnalysis,
    review: DirectorActionCoverageReview,
) -> None:
    plan = frame_analysis.director_plan
    if plan is None:
        return

    scene_by_signature: dict[str, list[FrameAnchoredStoryboardScene]] = {}
    for scene in storyboard.scenes:
        for moment_id in scene.signature_moment_ids:
            scene_by_signature.setdefault(moment_id, []).append(scene)

    for moment in plan.signature_moment_plan:
        if moment.strategy == "omit":
            continue
        scenes = scene_by_signature.get(moment.moment_id, [])
        if not scenes:
            raise ValueError(f"storyboard is missing required signature moment: {moment.moment_id}")
        if moment.transfer_role in {"primary_action", "interaction", "impact"} and not any(
            _scene_has_execution(scene) for scene in scenes
        ):
            raise ValueError(
                f"signature moment {moment.moment_id} lacks executable action or result direction"
            )
        if moment.moment_type in {"camera", "combined"} and not any(
            (scene.camera_instruction or "").strip() for scene in scenes
        ):
            raise ValueError(f"signature moment {moment.moment_id} lacks camera support")
        if moment.moment_type in {"effect", "combined"} and not any(
            (scene.effect_timing or "").strip() for scene in scenes
        ):
            raise ValueError(f"signature moment {moment.moment_id} lacks effect support")
        if not any((scene.action_result_requirement or "").strip() for scene in scenes):
            raise ValueError(f"signature moment {moment.moment_id} lacks visible payoff")

    covered_source_ids = {
        beat_id for scene in storyboard.scenes for beat_id in scene.source_behavior_beat_ids
    }
    covered_source_ids.update(
        beat_id
        for moment in plan.signature_moment_plan
        if moment.strategy != "omit" and moment.moment_id in scene_by_signature
        for beat_id in moment.source_behavior_beat_ids
    )
    missing_source_ids = set(review.required_core_behavior_beat_ids) - covered_source_ids
    if missing_source_ids:
        raise ValueError(
            "storyboard is missing required source behavior beats: "
            + ", ".join(sorted(missing_source_ids))
        )

    divergent = any(
        moment.strategy != "omit" and moment.temporary_divergence.strip()
        for moment in plan.signature_moment_plan
    )
    if divergent and not any(
        (scene.anchor_return_instruction or "").strip() for scene in storyboard.scenes[:-1]
    ):
        raise ValueError("storyboard is missing anchor return instruction")

    if storyboard.scenes[-1].end_second != storyboard.duration_seconds:
        raise ValueError("final scene must end at requested duration")
    for scene in storyboard.scenes:
        if scene.start_second is None or scene.end_second is None:
            continue
        if not 0 <= scene.start_second < scene.end_second <= storyboard.duration_seconds:
            raise ValueError("storyboard scene timing must be ordered within requested duration")
```

Task 4 must call the existing `validate_director_coverage(storyboard, plan)` immediately before this function, preserving core cinematic-beat validation.

- [ ] **Step 6: Run pure service tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_storyboard_director_coverage_service.py -q
```

Expected: PASS without creating or monkeypatching an LLM provider.

- [ ] **Step 7: Commit Task 2**

```powershell
git status --short
git add backend/app/services/storyboard_director_coverage_service.py tests/test_storyboard_director_coverage_service.py
git commit -m "feat: validate storyboard action coverage"
```

---

### Task 3: Teach Call 2 and Call 3 the dynamic action contract

**Files:**
- Modify: `backend/app/integrations/llm/base.py:99-117`
- Modify: `backend/app/integrations/llm/openai_provider.py:531-577,1869-1985,2550-2858`
- Modify: `backend/app/integrations/llm/mock_provider.py:85-190`
- Test: `tests/test_frame_anchored_storyboard_provider.py:300-370,1140-1375,1540-1660`

**Interfaces:**
- Extends this exact protocol/provider signature:

```python
async def generate_frame_anchored_video_storyboard(
    self,
    first_frame_image_url: str,
    last_frame_image_url: str,
    frame_analysis: FrameAnalysis,
    duration_seconds: int,
    aspect_ratio: str,
    director_correction_requirements: list[str] | None = None,
) -> FrameAnchoredStoryboard:
```
- Normalizes new Call-2 and Call-3 private fields without inventing ratios or actions.

- [ ] **Step 1: Write failing prompt and provider tests**

Using the existing captured Responses request helpers, add:

Add this local response helper first:

```python
def _action_storyboard_response() -> dict[str, object]:
    return {
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 1,
                "frame_anchor": "first_frame",
                "visual": "Hold the exact supplied opening anchor.",
            },
            {
                "scene_index": 2,
                "start_second": 1,
                "end_second": 7,
                "frame_anchor": "transition",
                "visual": "Execute the target-compatible causal action.",
                "motion": "The subject completes readable state-changing motion.",
                "cinematic_beats": ["impact"],
                "camera_instruction": "Reframe continuously around execution.",
                "action_result_requirement": "Show the visible target-state change.",
                "effect_timing": "Peak after the subject motion reads.",
                "signature_moment_ids": ["signature_action"],
                "source_behavior_beat_ids": ["core_behavior"],
                "subject_motion_intensity": 0.9,
                "camera_intensity": 0.65,
                "effect_intensity": 0.8,
            },
            {
                "scene_index": 3,
                "start_second": 7,
                "end_second": 10,
                "frame_anchor": "transition",
                "visual": "Resolve the payoff and return to the ending composition.",
                "motion": "Settle continuously toward the final state.",
                "anchor_return_instruction": "Restore final pose, framing, and camera continuously.",
            },
            {
                "scene_index": 4,
                "start_second": 10,
                "end_second": 12,
                "frame_anchor": "last_frame",
                "visual": "Lock the exact supplied last frame.",
            },
        ],
        "sound_design": {"music": "continuous rise and resolve", "ambience": None},
        "rationale": "Action, payoff, return, and final lock remain one continuous shot.",
    }
```

```python
@pytest.mark.asyncio
async def test_director_prompt_allows_middle_divergence_and_dynamic_return() -> None:
    provider, captured = _gateway_provider_with_responses(_director_plan_payload())
    await provider.direct_frame_anchored_video_storyboard(
        FIRST_FRAME_URL, LAST_FRAME_URL, _analysis(), 12, "9:16"
    )
    prompt = captured[0]["input"][0]["content"].casefold()
    assert "endpoints, not constraints on every intermediate frame" in prompt
    assert "final-pose mismatch" in prompt
    assert "replace_with_equivalent" in prompt
    assert "subject motion, camera motion, and effect intensity independently" in prompt
    assert "one continuous" in prompt
    for sample_noun in ("sword", "diamond", "eagle", "x200,000"):
        assert sample_noun not in prompt


@pytest.mark.asyncio
async def test_storyboard_call_receives_compact_corrections() -> None:
    provider, captured = _gateway_provider_with_responses(_action_storyboard_response())
    analysis = _analysis().model_copy(update={"director_plan": FrameAnchoredDirectorPlan.model_validate(_director_plan_payload())})
    await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        analysis,
        12,
        "9:16",
        director_correction_requirements=[
            "Execute uncovered core behavior beat core_behavior in subject/state motion and show its visible payoff."
        ],
    )
    payload = captured[0]["input"][1]["content"][0]["text"]
    assert "director_correction_requirements" in payload
    assert "core_behavior" in payload
    assert payload.count("frame_analysis") == 1


@pytest.mark.asyncio
async def test_provider_normalizes_scene_execution_fields() -> None:
    provider, _ = _gateway_provider_with_responses(_action_storyboard_response())
    analysis = _analysis().model_copy(update={"director_plan": FrameAnchoredDirectorPlan.model_validate(_director_plan_payload())})
    storyboard = await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL, LAST_FRAME_URL, analysis, 12, "9:16"
    )
    action = storyboard.scenes[1]
    assert action.signature_moment_ids == ["signature_action"]
    assert action.source_behavior_beat_ids == ["core_behavior"]
    assert action.subject_motion_intensity == 0.9
    assert storyboard.scenes[-2].anchor_return_instruction
```

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_frame_anchored_storyboard_provider.py -k "middle_divergence or compact_corrections or scene_execution_fields" -q
```

Expected: FAIL because the argument, prompts, and normalizers are missing.

- [ ] **Step 3: Extend protocol and Call-3 payload**

In `base.py`, `openai_provider.py`, `mock_provider.py`, and all test fakes, use:

```python
async def generate_frame_anchored_video_storyboard(
    self,
    first_frame_image_url: str,
    last_frame_image_url: str,
    frame_analysis: FrameAnalysis,
    duration_seconds: int,
    aspect_ratio: str,
    director_correction_requirements: list[str] | None = None,
) -> FrameAnchoredStoryboard:
```

The OpenAI user payload becomes:

```python
{
    "duration_seconds": duration_seconds,
    "aspect_ratio": aspect_ratio,
    "frame_analysis": frame_analysis.model_dump(mode="json"),
    "director_correction_requirements": director_correction_requirements or [],
}
```

- [ ] **Step 4: Add the approved generic prompt rules**

Preserve evidence-vs-inference, target identity, mappings, overlay lifecycle, JSON-only output, and continuous-shot rules. Add to Call 2:

```python
"Treat the supplied first and last frames as exact endpoints, not constraints on every "
"intermediate frame. During the free middle interval, pose, orientation, screen position, "
"composition, shot scale, and camera placement may diverge significantly. A final-pose "
"mismatch means plan a continuous return; it is not a reason to omit a core action. For "
"each core action or state change, choose preserve, then adapt, then "
"replace_with_equivalent, and omit only when literal/adapted and equivalent-intensity "
"execution are infeasible. Generic glow, particles, passive posing, or camera drift cannot "
"replace physical or state-changing action. Plan subject motion, camera motion, and effect "
"intensity independently. Select action_arc_windows dynamically from target duration, "
"action complexity, endpoint difference, camera travel, effect readability, and final-frame "
"readability; do not use fixed seconds or fixed percentages. Produce one continuous shot "
"without editing or stitching. "
```

Name every Task-1 output field in the JSON contract. Add to Call 3:

```python
"For every non-omitted signature moment, copy its exact moment_id into "
"signature_moment_ids, execute its action in motion and/or action_result_requirement, "
"include camera and effect support in matching fields, show the visible payoff, and state "
"anchor_return_instruction before final lock. Copy relevant source behavior IDs into "
"source_behavior_beat_ids. One continuous scene may carry multiple IDs when duration is "
"short. The final last_frame scene must end exactly at duration_seconds. "
```

- [ ] **Step 5: Normalize Call-2 JSON**

Add shape-only normalizers:

```python
def _normalize_director_action_arc_windows(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _director_items(value):
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "window_id": _director_first_text(item, "window_id", "id"),
                "phase": _director_first_text(item, "phase", "role"),
                "start_ratio": item.get("start_ratio"),
                "end_ratio": item.get("end_ratio"),
                "objective": _director_first_text(item, "objective", "instruction"),
                "subject_motion_intensity": item.get("subject_motion_intensity"),
                "camera_intensity": item.get("camera_intensity"),
                "effect_intensity": item.get("effect_intensity"),
                "depends_on": _frame_string_list(item.get("depends_on")),
            }
        )
    return result


def _normalize_director_signature_moments(value: Any) -> list[dict[str, Any]]:
    fields = (
        "adapted_action", "temporary_divergence", "camera_support", "effect_support",
        "visible_payoff", "return_strategy", "omission_reason",
        "equivalent_replacement_failure",
    )
    result: list[dict[str, Any]] = []
    for item in _director_items(value):
        if not isinstance(item, dict):
            continue
        normalized = {
            "moment_id": _director_first_text(item, "moment_id", "id"),
            "moment_type": _director_first_text(item, "moment_type", "type"),
            "source_evidence": _director_text_list(item.get("source_evidence")),
            "source_behavior_beat_ids": _frame_string_list(item.get("source_behavior_beat_ids")),
            "transfer_role": _director_first_text(item, "transfer_role", "role"),
            "strategy": _director_first_text(item, "strategy", "decision"),
            "target_adaptation": _director_first_text(item, "target_adaptation", "adaptation"),
            "assigned_beat_id": _director_first_text(item, "assigned_beat_id") or None,
        }
        normalized.update({field: _director_text(item.get(field)) or None for field in fields})
        result.append(normalized)
    return result
```

Assign both lists plus `final_anchor_return` in `_frame_anchored_director_plan_from_data()` before Pydantic validation. Do not manufacture creative fields when the provider omitted them; invalid output must remain diagnosable.

- [ ] **Step 6: Normalize Call-3 scene JSON**

In `_frame_anchored_storyboard_from_data()`, deduplicate:

```python
"signature_moment_ids": list(
    dict.fromkeys(_frame_string_list(scene.get("signature_moment_ids")))
),
"source_behavior_beat_ids": list(
    dict.fromkeys(_frame_string_list(scene.get("source_behavior_beat_ids")))
),
```

Leave numeric intensity values and `anchor_return_instruction` in the scene dict for Pydantic to validate. Do not infer ratios or IDs locally.

- [ ] **Step 7: Update mock provider**

The mock director output must include one generic non-omitted signature and action/return/final-lock windows only when core reference action/state evidence exists. The mock final storyboard must accept corrections and include signature/source IDs, action/result, camera/effect support, three intensity values, return instruction, and a final scene ending at `duration_seconds`.

- [ ] **Step 8: Run provider regression tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_frame_anchored_storyboard_provider.py -q
```

Expected: PASS, including existing behavior graph, timeline adaptation, identity mapping, overlay lifecycle, and invalid JSON diagnostics.

- [ ] **Step 9: Commit Task 3**

```powershell
git status --short
git add backend/app/integrations/llm/base.py backend/app/integrations/llm/openai_provider.py backend/app/integrations/llm/mock_provider.py tests/test_frame_anchored_storyboard_provider.py
git commit -m "feat: direct dynamic storyboard action arcs"
```

---

### Task 4: Integrate review, corrections, final validation, and safe formatting

**Files:**
- Modify: `backend/app/services/external_ai_generation_service.py:15-22,384-489,840-916`
- Modify: `tests/test_external_ai_generation.py:140-430,1040-1220`

**Interfaces:**
- Stores `director_action_coverage_review` only in task metadata.
- Passes `review.correction_requirements` into the existing third call.
- Runs existing core-beat validation and Task-2 final action validation before publishing.
- Renders natural execution direction but no private IDs.

- [ ] **Step 1: Update the fake and write failing orchestration tests**

Make `FakeExternalAILLM.generate_frame_anchored_video_storyboard` accept `director_correction_requirements` and record it in `call_details`. Add:

```python
@pytest.mark.asyncio
async def test_storyboard_v2_stores_review_and_forwards_corrections(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "review_director_action_coverage",
        lambda frame_analysis, director_plan: DirectorActionCoverageReview(
            status="corrective",
            required_core_behavior_beat_ids=["core_behavior"],
            uncovered_core_behavior_beat_ids=["core_behavior"],
            correction_requirements=[
                "Execute uncovered core behavior beat core_behavior in subject/state motion and show its visible payoff."
            ],
        ),
    )
    monkeypatch.setattr(
        external_ai_service_module,
        "validate_final_storyboard_action_coverage",
        lambda storyboard, frame_analysis, review: None,
    )
    engine, session_factory = await _session_factory(tmp_path, "storyboard-v2-review.db")
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="storyboard-v2-review",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        result = await service.execute_frame_anchored_video_storyboard(session, task)
        await session.refresh(task)
        assert result["storyboard_text"]
        assert task.metadata_json["director_action_coverage_review"]["status"] == "corrective"
        assert fake_llm.call_details[-1]["director_correction_requirements"] == [
            "Execute uncovered core behavior beat core_behavior in subject/state motion and show its visible payoff."
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_storyboard_v2_uncached_success_uses_exactly_three_llm_calls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    engine, session_factory = await _session_factory(
        tmp_path, "storyboard-v2-three-calls.db"
    )
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="storyboard-v2-three-calls",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        await service.execute_frame_anchored_video_storyboard(session, task)
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
        ]
    await engine.dispose()


def test_frame_anchored_formatter_hides_private_ids() -> None:
    storyboard = FrameAnchoredStoryboard.model_validate(
        {
            "duration_seconds": 4,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 1,
                    "frame_anchor": "first_frame",
                    "visual": "Hold the exact supplied opening anchor.",
                },
                {
                    "scene_index": 2,
                    "start_second": 1,
                    "end_second": 3,
                    "frame_anchor": "transition",
                    "visual": "Execute a target-compatible causal action.",
                    "motion": "Complete readable subject/state motion.",
                    "camera_instruction": "Support the action continuously.",
                    "action_result_requirement": "Show the visible result.",
                    "effect_timing": "Peak after the action reads.",
                    "signature_moment_ids": ["signature_action"],
                    "source_behavior_beat_ids": ["core_behavior"],
                    "cinematic_beats": ["core_peak"],
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.6,
                    "effect_intensity": 0.7,
                    "anchor_return_instruction": "Return continuously to the final anchor.",
                },
                {
                    "scene_index": 3,
                    "start_second": 3,
                    "end_second": 4,
                    "frame_anchor": "last_frame",
                    "visual": "Lock the exact supplied last frame.",
                },
            ],
        }
    )
    text = _format_frame_anchored_storyboard_text(storyboard)
    assert "signature_action" not in text
    assert "core_behavior" not in text
    assert "core_peak" not in text
    assert "Subject motion intensity:" in text
    assert "Camera intensity:" in text
    assert "Effect intensity:" in text
    assert "Return to final anchor:" in text
```

These tests deliberately use the existing `_session_factory()` directly; do not add another persistence helper.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py -k "stores_review or exactly_three_llm_calls or formatter_hides_private_ids" -q
```

Expected: FAIL because review persistence, correction forwarding, and safe formatting are missing.

- [ ] **Step 3: Integrate the review before Call 3**

Import:

```python
from backend.app.services.storyboard_director_coverage_service import (
    review_director_action_coverage,
    validate_final_storyboard_action_coverage,
)
```

After Call 2 or cached-plan restoration:

```python
director_plan = frame_analysis.director_plan
if director_plan is None:
    raise ProviderError("Frame-anchored director plan is missing.")
director_review = review_director_action_coverage(frame_analysis, director_plan)
await _store_frame_anchored_private_metadata(
    session,
    task,
    director_action_coverage_review=director_review.model_dump(mode="json"),
)
```

Pass to Call 3:

```python
director_correction_requirements=director_review.correction_requirements,
```

This review must run whether the director plan is newly created or restored from cached `frame_analysis`.

- [ ] **Step 4: Validate the candidate once, without a fourth model call**

After storing `frame_anchored_storyboard_candidate`:

```python
try:
    validate_director_coverage(storyboard, director_plan)
    validate_final_storyboard_action_coverage(
        storyboard,
        frame_analysis,
        director_review,
    )
except ValueError as exc:
    raise ProviderError(
        "LLM storyboard does not execute the required director action and final-anchor return."
    ) from exc
```

Keep candidate-before-validation persistence for diagnosis. Keep existing task retries; do not add an independent loop.

- [ ] **Step 5: Persist compact private review metadata**

Extend `_store_frame_anchored_private_metadata` with:

```python
director_action_coverage_review: dict[str, Any] | None = None,
```

Merge under the exact key `director_action_coverage_review`. Do not change any response schema or polling serializer.

- [ ] **Step 6: Render natural execution fields and suppress IDs**

Remove `Cinematic beat:` and `Cinematic beats:` from `_format_frame_anchored_storyboard_text`. Never render signature or source-behavior IDs. Add:

```python
f"Subject motion intensity: {_format_optional_intensity(scene.subject_motion_intensity)}",
f"Camera intensity: {_format_optional_intensity(scene.camera_intensity)}",
f"Effect intensity: {_format_optional_intensity(scene.effect_intensity)}",
f"Return to final anchor: {scene.anchor_return_instruction or '-'}",
```

and:

```python
def _format_optional_intensity(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".rstrip("0").rstrip(".")
```

Keep visual, motion, camera instruction, transition goal, action-result requirement, effect timing, overlays, sound, and rationale.

- [ ] **Step 7: Run V2 orchestration/retry tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_external_ai_generation.py -k "storyboard_v2 or frame_anchored_formatter" -q
```

Expected: PASS. A retry test may record four calls across two executions when the first director call fails; a single uncached successful execution must record exactly three.

- [ ] **Step 8: Commit Task 4**

```powershell
git status --short
git add backend/app/services/external_ai_generation_service.py tests/test_external_ai_generation.py
git commit -m "feat: enforce storyboard action execution"
```

---

### Task 5: Prove compatibility, queue stability, and complete regression coverage

**Files:**
- Test: `tests/test_storyboard_director_coverage_service.py`
- Test: `tests/test_frame_anchored_storyboard_provider.py`
- Test: `tests/test_external_ai_generation.py`
- Verify unchanged: `backend/app/api/v1/endpoints/external_ai_generation.py`
- Verify unchanged: `backend/app/schemas/external_ai_generation.py`
- Verify unchanged: `backend/app/services/generation_task_service.py`
- Verify unchanged: `backend/app/worker/celery_app.py`

**Interfaces:**
- Produces no new runtime interface.
- Confirms public polling, private metadata boundaries, queue routing, and three-call behavior.

- [ ] **Step 1: Add public-result privacy assertions**

Extend the successful V2 API polling test:

```python
data = polled.json()["data"]
assert data["status"] == "succeeded"
assert data["storyboard_text"]
assert data["duration_seconds"] == 12
assert data["aspect_ratio"] == "9:16"
for private_field in (
    "frame_analysis",
    "director_plan",
    "director_action_coverage_review",
    "frame_anchored_storyboard_candidate",
    "frame_anchored_storyboard",
    "signature_moment_ids",
    "source_behavior_beat_ids",
):
    assert private_field not in data
```

- [ ] **Step 2: Add low-motion and short-duration tests**

Add to `tests/test_storyboard_director_coverage_service.py`:

```python
def test_low_motion_reference_does_not_invent_core_action() -> None:
    analysis = _analysis(behavior_type="overlay")
    plan = _plan(source_ids=[]).model_copy(
        update={"signature_moment_plan": [], "action_arc_windows": [], "final_anchor_return": ""}
    )
    review = review_director_action_coverage(analysis, plan)
    assert review.status == "pass"
    assert review.correction_requirements == []


def test_short_duration_may_share_action_payoff_and_return_scene() -> None:
    storyboard, analysis, review = _valid_short_final_inputs()
    validate_director_coverage(storyboard, analysis.director_plan)
    validate_final_storyboard_action_coverage(storyboard, analysis, review)
```

The local `_valid_short_final_inputs()` builder above uses exactly those three scenes and ends the final last-frame hold at `4` seconds.

- [ ] **Step 3: Run all focused tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py -q
```

Expected: PASS.

- [ ] **Step 4: Run backend lint and full tests**

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest
```

Expected: PASS. Record any unrelated pre-existing failure exactly; do not weaken V2 assertions.

- [ ] **Step 5: Prove route, schema, queue, and worker files did not change**

```powershell
git diff team/main -- backend/app/api/v1/endpoints/external_ai_generation.py backend/app/schemas/external_ai_generation.py backend/app/services/generation_task_service.py backend/app/worker/celery_app.py
```

Expected: no output.

- [ ] **Step 6: Scan for forbidden sample rules and final overlay lock**

```powershell
git grep -n -E "FINAL_TEXT_OVERLAY_LOCKS|sword|diamond|x200,000" -- backend/app/schemas/ai.py backend/app/services/storyboard_director_coverage_service.py backend/app/integrations/llm/openai_provider.py backend/app/services/external_ai_generation_service.py
```

Expected: no output.

- [ ] **Step 7: Inspect literal scope and whitespace**

```powershell
git status --short
git diff --check
git diff --stat team/main
git diff --name-only team/main
```

Expected runtime/test files are limited to:

```text
backend/app/schemas/ai.py
backend/app/services/storyboard_director_coverage_service.py
backend/app/integrations/llm/base.py
backend/app/integrations/llm/openai_provider.py
backend/app/integrations/llm/mock_provider.py
backend/app/services/external_ai_generation_service.py
tests/test_storyboard_director_coverage_service.py
tests/test_frame_anchored_storyboard_provider.py
tests/test_external_ai_generation.py
```

The approved spec and this plan are expected documentation changes. `AGENTS.md`, `.env*`, frontend files, routes, queue config, migrations, and video-provider files must not appear.

- [ ] **Step 8: Commit final test alignment only if needed**

```powershell
git add tests/test_storyboard_director_coverage_service.py tests/test_frame_anchored_storyboard_provider.py tests/test_external_ai_generation.py
git commit -m "test: cover storyboard action arc regressions"
```

Skip the commit if Task 5 creates no diff.

---

## Implementation Completion Gate

1. Every reliable core reference action/state beat is linked to valid execution or appears in a correction requirement.
2. Final accepted scenes prove action, visible payoff, camera/effect support, return, and exact final hold.
3. The last scene ends at the requested duration; no fixed climax/return ratio exists in runtime code or prompts.
4. `storyboard_text` contains natural action direction and no private IDs.
5. One uncached success makes exactly three LLM calls.
6. Public create/poll schemas and Redis/Celery routing remain unchanged.
7. No sample-specific rule or `[FINAL_TEXT_OVERLAY_LOCKS]` logic exists.
8. Focused tests, Ruff, full pytest, `git diff --check`, and scope review pass.
