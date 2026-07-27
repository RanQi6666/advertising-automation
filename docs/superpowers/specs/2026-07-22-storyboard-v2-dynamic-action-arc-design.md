# Storyboard V2 Dynamic Action Arc Design

## Status

Approved by the user on July 22, 2026. This document refines the implemented Storyboard V2 3A director pipeline and authorizes implementation under the constraints below.

## Problem Statement

The current Storyboard V2 pipeline can correctly analyze a reference video's visible actions, camera behavior, effects, causal beats, and ending UI. It also creates a private director plan and validates that core director beat IDs appear in the final structured storyboard.

A real test nevertheless showed a quality failure: the director omitted a recognizable side-turn-and-forward-weapon action because that intermediate pose differed from the target last frame's frontal pose. The generated storyboard therefore protected the ending composition by keeping the subject nearly static and replacing physical action with brighter light and more particles.

The underlying mistake is treating the last-frame anchor as a constraint on the entire clip. The required interpretation is:

```text
opening instant = exact first-frame anchor
middle interval = directed freedom to depart from both endpoint poses and compositions
ending interval = controlled return to and stable hold on the exact last-frame anchor
```

Storyboard V2 must preserve or adapt a reference video's recognizable action and causal payoff without sacrificing the target first and last frames.

## Product Decision

Use **schema + director prompt + deterministic local coverage checks**.

The rejected alternatives are:

1. **Prompt-only refinement:** inexpensive to implement but cannot reliably detect when a core action was omitted, flattened into effects, or merely named without execution.
2. **A fixed fourth model review call:** may improve some outputs but increases latency, cost, provider failure surface, and queue occupancy for every request.
3. **Hard-coded action templates:** would overfit the current sword example and violate the requirement that each uploaded reference video be analyzed independently.

The selected design keeps the existing three model calls and adds only inexpensive local validation between and after existing calls.

## Goals

- Allow the target subject, character, product, or other active visual entity to depart clearly from the first- and last-frame pose, screen position, orientation, framing, and shot scale during the middle of the clip.
- Require the final movement to return naturally and accurately to the caller's last-frame visual state.
- Preserve, adapt, or replace with an equivalent the reference video's strongest transferable action and causal moment instead of substituting passive posing, generic glow, or particles.
- Make preparation, execution, visible consequence, camera support, effect support, and final-anchor return explicit and machine-checkable.
- Allocate action, payoff, return, and final hold dynamically for each requested duration. Do not use fixed seconds or fixed percentage constants in code or prompts.
- Keep a single continuous generated clip. Translate reference cuts into in-shot camera movement, reframing, occlusion, whip movement, focus changes, or other continuous-shot equivalents.
- Keep the public Storyboard V2 API unchanged; external callers continue to receive `storyboard_text`.
- Keep the current Redis/Celery text queue and the current three-call LLM pipeline.

## Non-Goals

- Do not add video editing, stitching, multiple generation segments, post-production compositing, or a separate rendering timeline.
- Do not add a fourth mandatory LLM call.
- Do not add a fixed sword, weapon, reward, character, product, logo, UI, transformation, or VFX recipe.
- Do not require every reference-video event to be transferred. Supporting and decorative events may be compressed or omitted when duration or target feasibility requires it.
- Do not weaken the exact first-frame and last-frame anchors.
- Do not reintroduce `[FINAL_TEXT_OVERLAY_LOCKS]` or a deterministic final text compositor.
- Do not expose private analysis, director structures, action-coverage diagnostics, or raw model output through polling.

## Existing Public Contract Remains Unchanged

```text
POST /api/v1/integrations/ai/storyboard-v2
GET  /api/v1/integrations/ai/jobs/{job_id}
```

The create request continues to accept the existing first-frame image, last-frame image, optional reference-video source, duration, aspect ratio, and idempotency fields.

Successful polling continues to expose the existing public shape, centered on:

```json
{
  "job_id": "...",
  "status": "succeeded",
  "storyboard_text": "...",
  "duration_seconds": 10,
  "aspect_ratio": "9:16"
}
```

No new public field, route, queue, webhook, database table, or frontend requirement is introduced.

## Existing Three-Call Pipeline Remains

For an uncached V2 task, the worker continues to execute:

```text
Call 1: target-frame + sampled-reference evidence analysis
  -> local target-duration adaptation
Call 2: private 3A director plan
  -> local action-transfer coverage review
Call 3: final frame-anchored structured storyboard
  -> local final storyboard coverage validation
  -> deterministic storyboard_text formatting
```

The new local checks are ordinary Python validation and must not call a model or media provider. Cached frame analysis/director metadata keeps the existing resume behavior.

## Anchor Semantics

### First Anchor

The opening scene must begin from the exact first-frame subject identity, product/character appearance, setting, visible text, brand, composition, and style. A short opening hold may be used when needed for readability, but it is not a requirement for every video.

### Free Middle Interval

After leaving the opening anchor and before the return interval, the director may intentionally change:

- body pose, gesture, orientation, expression, and action silhouette;
- product or prop state when supported by the target/reference mapping;
- subject position and scale in frame;
- camera distance, height, orbit, roll, focus, and movement speed;
- lighting contrast, particles, energy, environmental reaction, temporary UI, and effect intensity;
- composition and foreground/background emphasis.

These changes are not continuity failures merely because they differ from the final frame. A different intermediate pose or composition is never, by itself, a valid reason to omit a core reference action.

The target frames remain authoritative for concrete target identity and endpoint visual truth. Existing `visual_identity_mappings` continue to decide whether a reference element is preserved, replaced with a target equivalent, morphed to a target equivalent, limited to an endpoint, or carried through the final anchor.

### Last Anchor Return

Before the final lock window, the storyboard must state how temporary action, camera displacement, subject displacement, effects, and overlays resolve into the exact target last frame. The ending must not teleport into the last frame. It must specify a physically and visually plausible return, settle, reveal, or transformation that produces the last-frame state.

During the final lock window:

- target subject/product/character appearance and final pose are accurate;
- target composition, setting, brand, visible text, reward, and UI are accurate;
- camera motion and subject motion settle enough for the final image to be readable;
- required mapped reference overlays that persist to the ending remain present under the existing overlay lifecycle rules.

## Reference Action Transfer Policy

For each evidence-grounded signature moment, the director evaluates strategies in this order:

1. `preserve`: execute the transferable action substantially as observed and return to the last anchor;
2. `adapt`: change actor, object, trajectory, scale, staging, or execution details while preserving the recognizable action role, causality, and intensity;
3. `replace_with_equivalent`: when the literal action is infeasible, create a target-compatible action with comparable narrative function, motion energy, camera emphasis, and visible payoff;
4. `omit`: allowed only when both the literal/adapted action and an equivalent-intensity target action are infeasible or contradict required target-frame facts.

The following alone are not sufficient reasons for `omit`:

- the action pose differs from the final pose;
- the subject temporarily leaves the final screen position;
- the camera temporarily leaves the final framing;
- the reference uses an object or actor that must be replaced with a target equivalent;
- the target duration requires compression.

When duration is short, the director first compresses repeated, decorative, or supporting behavior. It may merge preparation, execution, and impact into one readable continuous window, but it must not replace the core physical or state-changing action with a passive pose or effect-only substitute.

## Internal Schema Changes

All additions are private Pydantic models stored in task metadata. Field names below are normative for implementation unless a directly equivalent name is required to match an existing code convention.

### Director Action Arc Window

Add a private structure:

```text
DirectorActionArcWindow
  - window_id: non-empty unique string
  - phase: anchor_hold | departure | preparation | action | impact | payoff | return | final_lock
  - start_ratio: float in [0, 1]
  - end_ratio: float in [0, 1], strictly greater than start_ratio
  - objective: non-empty string
  - subject_motion_intensity: float in [0, 1]
  - camera_intensity: float in [0, 1]
  - effect_intensity: float in [0, 1]
  - depends_on: list of known window_id values
```

`FrameAnchoredDirectorPlan` gains:

```text
  - action_arc_windows: list[DirectorActionArcWindow]
  - final_anchor_return: non-empty string when a reference core action exists
```

The phase vocabulary is generic cinematic structure, not a fixed creative formula. The model may omit phases that are not needed, combine adjacent roles in one time window, and choose all window sizes dynamically. No implementation constant assigns a climax, return, or final lock to a predetermined second or percentage.

When the reference analysis contains at least one core action or state-changing beat, the plan must include:

- at least one `action` window;
- a subsequent `return` window;
- a final `final_lock` window ending at ratio `1.0`;
- at least one interval where `subject_motion_intensity` is materially above the opening/final hold, unless the target has no movable subject and the evidence requires an equivalent non-subject transformation;
- intensity curves that distinguish subject motion, camera motion, and effects rather than allowing effects alone to impersonate action.

### Director Signature Moment

Extend `DirectorSignatureMoment` to include:

```text
  - source_behavior_beat_ids: list of exact ReferenceBehaviorBeat.beat_id values
  - transfer_role: causal_setup | primary_action | interaction | impact | visible_result | camera_emphasis | effect_emphasis | overlay_lifecycle | other
  - strategy: preserve | adapt | replace_with_equivalent | omit
  - adapted_action: string
  - temporary_divergence: string
  - camera_support: string
  - effect_support: string
  - visible_payoff: string
  - return_strategy: string
  - assigned_beat_id: existing DirectorBeat.beat_id
  - omission_reason: optional string
  - equivalent_replacement_failure: optional string
```

Validation rules:

- `source_behavior_beat_ids` must reference known behavior-graph beats when a behavior graph exists.
- `preserve`, `adapt`, and `replace_with_equivalent` require an assigned core director beat plus non-empty `adapted_action`, `visible_payoff`, and `return_strategy`.
- `primary_action`, `interaction`, and `impact` moments also require non-empty `temporary_divergence`; this field describes the allowed middle departure rather than prohibiting it.
- A camera signature requires `camera_support`; an effect signature requires `effect_support`; a combined action/effect moment requires both.
- `omit` requires both `omission_reason` and `equivalent_replacement_failure`, and may not use final-pose mismatch as either explanation.
- `target_adaptation` may be retained during implementation for backward compatibility with existing private metadata, but the new specific fields become the authoritative execution contract.

### Structured Storyboard Scene

Extend `FrameAnchoredStoryboardScene` with private validation fields:

```text
  - signature_moment_ids: list of exact DirectorSignatureMoment.moment_id values
  - subject_motion_intensity: optional float in [0, 1]
  - camera_intensity: optional float in [0, 1]
  - effect_intensity: optional float in [0, 1]
  - anchor_return_instruction: optional string
```

The signature and source-behavior IDs plus numeric intensity fields support deterministic checks. `source_behavior_beat_ids` also lets Call 3 prove execution of a core behavior beat when the local review found that Call 2 did not create a usable signature-moment link. They do not need to be rendered verbatim into public `storyboard_text`. The formatter must instead render clear natural-language action, camera, effect, payoff, and return directions. It may render a concise qualitative or numeric intensity line if doing so improves downstream video-model execution, but must not expose internal evidence IDs as user-facing prose.

## Director Prompt Requirements

The second-call director prompt must state all of the following without naming any sample-specific character, weapon, reward, UI, product, or effect:

- exact first and final anchors constrain endpoints, not every intermediate frame;
- intermediate pose, orientation, screen position, composition, shot scale, and camera placement may diverge significantly;
- every transferred core action must include preparation, execution, visible consequence, and a return strategy, with roles merged only when duration requires it;
- physical or state-changing action cannot be replaced by generic glow, particles, passive posing, or camera drift;
- a final-pose mismatch means “plan a return,” not “omit the action”;
- literal infeasibility requires an equivalent-intensity target action before omission is considered;
- action, camera, and effect intensity are planned independently;
- action windows and final-lock duration are selected dynamically from target duration, action complexity, endpoint difference, camera travel, effect readability, and final-frame readability;
- the output represents one continuous generated shot and cannot require editing or stitching.

The director prompt must continue to distinguish observed evidence from director inference and must not invent unsupported reference identity.

## Local Director Action-Coverage Review

After Call 2 and before Call 3, run a deterministic review such as:

```text
review_director_action_coverage(frame_analysis, director_plan)
```

The review produces private metadata:

```text
director_action_coverage_review
  - status: pass | corrective
  - required_core_behavior_beat_ids
  - covered_core_behavior_beat_ids
  - uncovered_core_behavior_beat_ids
  - invalid_omission_moment_ids
  - missing_execution_detail_moment_ids
  - missing_return_moment_ids
  - correction_requirements
```

The review must:

1. collect reference behavior beats marked `importance=core` whose type is action or a visible state-changing event;
2. verify each is linked through `source_behavior_beat_ids` to a signature moment;
3. accept `preserve`, `adapt`, or `replace_with_equivalent` only when the required execution, payoff, assigned core beat, and return fields exist;
4. reject an `omit` plan that lacks both infeasibility explanations;
5. verify action arc windows contain an executable action, a later return, and a final lock when core action evidence exists;
6. detect an intensity plan in which subject motion remains flat while only camera/effect intensity rises, unless the evidence and target genuinely contain no movable subject and the plan states the equivalent state-changing action.

This review must not rewrite the director plan and must not start another model call. If issues exist, it creates short, exact `correction_requirements` that are appended to the existing Call 3 input. Example categories are “execute uncovered core behavior beat,” “supply equivalent target action,” “state visible payoff,” and “state how the subject returns to the last anchor.” The correction text must be evidence- and ID-based, not sample-specific.

## Final Storyboard Generation Requirements

Call 3 receives:

- the existing target images;
- factual `frame_analysis`;
- the duration adaptation plan;
- the enhanced director plan;
- optional local `correction_requirements` from the coverage review.

For every non-omitted signature moment, the generated storyboard must:

- include its exact `moment_id` in one or more `signature_moment_ids` fields;
- execute the adapted action in `motion` and/or `action_result_requirement`, not merely mention the ID;
- include the required camera and effect support in their corresponding fields;
- show the visible payoff;
- include the return behavior before the final lock;
- end in an exact last-frame anchor scene.

A continuous scene may carry multiple moment or director beat IDs when the duration is short and the action remains readable without a cut.

## Final Local Validation

Extend the existing post-Call-3 director coverage validation. It must verify:

- every core director beat remains assigned to an executable scene;
- every non-omitted signature moment appears by exact ID;
- action/interaction/impact moments occur in a scene with non-empty motion or action-result instructions;
- camera/effect requirements appear in the matching scene fields;
- each required visible payoff is represented;
- at least one return scene or scene window contains `anchor_return_instruction` when a core action departed from the endpoint pose/composition;
- the final scene is `last_frame`, ends at the requested duration, and follows the return window;
- scene timing and action-arc timing are ordered and within the requested duration.

Validation is structural and deterministic. It must not use brittle keyword matching for a specific action, object, character, brand, or effect.

## Error and Fallback Behavior

- If there is no reference video, or the reference analysis contains no reliable core action/state-change evidence, do not force an artificial action arc. Existing frame-bridge direction remains valid.
- If evidence confidence is insufficient, the director may downgrade the event rather than assert unsupported motion.
- If Call 2 returns invalid JSON/schema, retain the existing diagnosable provider failure behavior.
- If the local director review finds gaps, continue to Call 3 once with correction requirements; do not fail early and do not call the model again.
- If Call 3 still omits required action coverage or fails final return validation, fail the task with a concise internal/provider error. Do not silently publish a flattened storyboard and do not make an automatic fourth model call.
- Preserve existing task retry semantics. A Celery retry reruns/resumes the task according to the current cached metadata rules; this feature adds no independent retry loop.
- Keep raw provider payloads and media out of logs. Log only task/request identifiers and compact validation categories.

## Private Metadata

Continue storing existing private fields and add:

```text
frame_analysis.director_plan.action_arc_windows
frame_analysis.director_plan.final_anchor_return
frame_analysis.director_plan.signature_moment_plan[*].new_fields
director_action_coverage_review
frame_anchored_storyboard_candidate.scenes[*].signature_moment_ids
frame_anchored_storyboard_candidate.scenes[*].intensity_fields
frame_anchored_storyboard_candidate.scenes[*].anchor_return_instruction
```

The accepted `frame_anchored_storyboard` remains private. Public polling must not expose any of these fields.

## Performance and Queue Impact

- LLM calls per uncached successful task remain exactly three.
- Reference frame extraction/sampling remains unchanged by this feature.
- No new Celery queue or Redis key family is introduced.
- Local coverage checks are linear in the number of behavior beats, signature moments, action windows, and scenes, which are small bounded task structures.
- Expected added CPU time is negligible compared with the existing model calls.
- Prompt size grows only by the compact new plan fields and any correction requirements; it must not duplicate the full analysis in a second review payload.

Therefore the expected end-to-end latency increase is small and should come primarily from modestly larger Call 2/Call 3 outputs, not from another network round trip.

## Implementation Scope

Expected code areas for the later implementation plan:

- `backend/app/schemas/ai.py`
  - add action-arc and signature execution fields;
  - add schema and cross-object validation helpers.
- `backend/app/integrations/llm/base.py`
  - allow Call 3 to receive optional private correction requirements.
- `backend/app/integrations/llm/openai_provider.py`
  - update director/storyboard prompts, normalization, and JSON parsing.
- `backend/app/services/external_ai_generation_service.py`
  - run the pre-Call-3 coverage review;
  - persist compact private diagnostics;
  - pass corrective requirements;
  - extend final validation and formatting as needed.
- Focused tests in the existing frame-anchored provider and external AI generation test modules.

No frontend, route, public request schema, database migration, queue configuration, video provider, final overlay service, or external system change is expected.

## Verification Plan

### Schema and Prompt Tests

- `preserve`, `adapt`, and `replace_with_equivalent` moments require action, payoff, return, and assigned beat fields.
- `omit` fails without both literal/adapted infeasibility and equivalent-replacement infeasibility.
- A final-pose mismatch alone is rejected as an omission rationale.
- Action-arc windows reject duplicate IDs, invalid dependencies, reversed ratios, and out-of-range intensity values.
- Prompt snapshots/assertions contain the endpoint-versus-middle freedom rule and contain no sample-specific action nouns.

### Coverage Review Tests

- A core reference action with no linked signature moment produces corrective requirements.
- A linked action that only raises effect intensity but lacks subject/state motion produces corrective requirements.
- A valid adapted action with payoff and return passes.
- A valid target-compatible equivalent action passes.
- No-reference and no-core-action cases do not force an action arc.
- The review does not invoke any LLM/provider method.

### Final Storyboard Validation Tests

- Missing signature moment IDs fail validation.
- A signature ID without executable motion/result fields fails validation.
- Missing camera/effect support for corresponding moment types fails validation.
- Missing return instruction after a divergent action fails validation.
- Final scene anchor/timing mismatches fail validation.
- Multiple core roles may validly share one continuous scene for a short duration.

### Contract and Runtime Tests

- Existing Storyboard V2 request and polling JSON remain unchanged.
- Public output still contains `storyboard_text` and does not expose private review structures.
- A mocked uncached successful task invokes exactly three LLM methods.
- Redis queue name and Celery task routing remain unchanged.
- Focused unit tests, backend lint, and the relevant external AI integration tests pass.

### Real Evaluation Set

After deployment, compare before/after results using several different reference-video categories rather than only the current sample:

- character or creature action with a strong pose departure and return;
- product interaction or transformation;
- vehicle/camera movement with a visible causal payoff;
- reward/UI reveal;
- a low-motion reference where no artificial action should be invented;
- short and long requested target durations.

Score each output on:

1. first-frame fidelity;
2. recognizable action transfer;
3. action-result causality;
4. subject-motion intensity;
5. camera support;
6. effect support;
7. natural return to the last frame;
8. final-frame fidelity;
9. continuous-shot executability;
10. absence of sample-specific hard-coded behavior.

A release candidate passes when the current gold sample executes a recognizable target-compatible physical action before returning to the target ending, and the diverse cases do not regress endpoint fidelity or invent irrelevant fixed actions.

## Acceptance Criteria

- Intermediate divergence from endpoint pose/composition is explicitly allowed and no longer treated as an omission reason.
- Every reliable core reference action is preserved, adapted, replaced with an equivalent-intensity target action, or omitted only with both infeasibility explanations.
- The private director plan contains a dynamic action arc, three independent intensity curves, visible payoff, and final-anchor return strategy when core action evidence exists.
- The final structured storyboard proves signature-moment execution by exact private IDs and executable scene fields.
- The generated public `storyboard_text` describes real subject/state action, camera support, effects, payoff, return, and final hold without requiring editing.
- Public API, async polling, Redis/Celery routing, and downstream video-generation contracts remain unchanged.
- No fixed sample-specific action, climax time, ratio, product, UI, reward, identity, or effect rule is introduced.
- No fourth mandatory model call is introduced.
