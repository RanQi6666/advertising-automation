# Storyboard V2 3A Director Agent Design

## Status

Approved product direction; implementation has not started. This design extends the existing Storyboard V2 internal generation pipeline. It does **not** change the public request, polling, or video-generation contracts.

## Product Definition

Storyboard V2 is not merely a reference-video visual-description feature. It acts as a **3A game / commercial cinematics director agent**:

1. observe the supplied first frame, last frame, and reference-video evidence;
2. infer transferable directing language: narrative beats, attention flow, camera grammar, action causality, tension escalation, and visual-effect timing;
3. adapt that language to the visual world established by the caller's first and last frames;
4. produce a single, executable `storyboard_text` for one video-model generation.

The target outcome is a high-tension, commercially directed video rather than a flat, continuous character-motion demonstration.

## Goals

- Preserve the existing external V2 result field: `storyboard_text`.
- Keep a single generated video. Do not introduce post-production editing, multi-segment video generation, or video stitching.
- Treat caller frames as visual anchors:
  - **first frame:** opening subject, product, character, setting, composition, and style;
  - **last frame:** required ending state, product, brand, rewards, visible text, and composition.
- Transfer from the reference video its observed action, causal structure, camera rhythm, tension curve, effects timing, UI/reward lifecycle, and directing intent.
- Dynamically derive all creative direction from the current reference video. Do not add fixed rules for a particular character, weapon, game, reward, product category, or visual effect.
- Keep reference-video private analysis internal. The polling response continues to expose only the current public V2 fields.

## Non-Goals

- Do not copy the reference video's exact character identity, product, brand, logo, copyrighted IP, text, or setting when those facts are not supplied by the caller frames.
- Do not add a public structured storyboard schema or alter the existing `storyboard_text` contract.
- Do not alter the legacy storyboard endpoint.
- Do not invoke the video provider from Storyboard V2 itself.
- Do not create a separate editing/timeline-rendering service.
- Do not guarantee that a downstream video model will perfectly execute every virtual camera cut; this design improves its instruction quality and makes compliance measurable.

## Public Contract (Unchanged)

```text
POST /api/v1/integrations/ai/storyboard-v2
GET  /api/v1/integrations/ai/jobs/{job_id}
```

The create endpoint continues to accept first-frame image, last-frame image, optional reference video, duration, aspect ratio, and idempotency inputs. The result remains:

```json
{
  "job_id": "...",
  "status": "succeeded",
  "storyboard_text": "...",
  "duration_seconds": 12,
  "aspect_ratio": "9:16"
}
```

The downstream video-generation endpoint remains responsible for turning the two frame images and this plain-text `storyboard_text` into an MP4.

## Core Principle: Evidence and Direction Are Different

The pipeline has two internal conceptual layers. They may use the same multimodal provider, but their prompts, output schemas, and responsibilities remain separate.

### Layer A: Visual Evidence Analysis

This layer records only what is supported by frames and sampled reference-video moments.

It must identify:

- reference-video shot/beat order and approximate timing;
- visible subject actions, state changes, and action-result causality;
- shot scale, point of view, camera motion, speed changes, and transitions;
- effects, lighting shifts, particles, impact moments, and their temporal relationship to action;
- UI, rewards, and text appearance, persistence, replacement, disappearance, and final-frame presence;
- first-frame and last-frame visual facts;
- mappings that distinguish transferable behavior from non-transferable visual identity;
- evidence confidence and places where the video does not provide enough evidence.

This layer must **not** invent commercial drama, infer unsupported story facts, or write a final video prompt.

### Layer B: 3A Director Reasoning

This layer converts the evidence into an adapted cinematic plan for the caller's frames.

It must produce the following internal direction:

| Field | Meaning |
| --- | --- |
| `narrative_objective` | What the video needs the viewer to feel and understand by its end. |
| `attention_path` | Ordered focus of viewer attention across the video. |
| `tension_curve` | Setup, trigger, escalation, climax, resolution, and their relative timing. |
| `climax_beats` | The non-flattenable cause-and-effect moments that must receive distinct attention. |
| `shot_separation_requirements` | Where a change of camera scale, framing, viewpoint, tempo, or emphasis is required inside the single generated video. |
| `action_emphasis` | Which motion must feel forceful, readable, and causally connected to its result. |
| `effect_peak_requirements` | Which effects peak at which trigger, action, impact, or result moment. |
| `overlay_lifecycle_plan` | When visible UI/reward/text appears, persists, is replaced by target-frame content, or remains in the final composition. |
| `anchor_adaptation_plan` | How each transferable reference beat is restaged using the first- and last-frame visual world. |
| `anti_flattening_constraints` | Concrete instructions that prevent the director's distinct beats from collapsing into one uniformly paced long shot. |

The director may reorganize how target-frame elements are staged, but it must preserve the reference video's observed causal order when evidence supports it. It must never require copying the reference video's private visual identity.

## Reference-Video Sampling Upgrade

Fixed-interval sampling alone can miss short close-ups, impact frames, flashes, transitions, and reward lock-ins. Sampling will be upgraded to an evidence-preserving hybrid strategy:

1. retain a low-cost baseline temporal coverage across the reference-video duration;
2. detect candidate high-change regions using lightweight video-frame difference / scene-change signals;
3. densify extraction around candidate transitions, fast movement, flashes, impact peaks, and final reward/overlay moments;
4. include exact opening and final reference frames;
5. preserve each extracted frame's timestamp and reason for selection.

The sampler does not decide creative intent. It improves the evidence supplied to Layer A.

### Sampling Limits

- Reference-video duration limits remain bounded by the existing V2 policy.
- Adaptive sampling must have configurable maximum frame count and extraction work limits.
- If scene-change detection is unavailable or fails, execution falls back to the current fixed-interval strategy rather than failing the task.

## Internal Execution Flow

```text
first frame + last frame + optional reference video
  -> prepare video and adaptive keyframes
  -> Layer A: visual evidence analysis
  -> deterministic duration adaptation of observed beat timings
  -> Layer B: 3A director plan, grounded in evidence and target frames
  -> structured frame-anchored storyboard
  -> readable storyboard_text
  -> existing external video-generation API (separate caller action)
```

The existing `execute_frame_anchored_video_storyboard` lifecycle remains the orchestration point. Its private task metadata stores evidence analysis, director plan, and structured storyboard for diagnosis; public polling output remains unchanged.

## Storyboard Text Requirements

`storyboard_text` remains plain text but becomes a director's execution brief, not a generic prose prompt. It should include:

1. duration and aspect ratio;
2. opening and final anchor commitments;
3. ordered time windows / cinematic beats;
4. per-beat framing and camera intent;
5. subject action and required causal result;
6. tension and pacing instruction;
7. effects trigger and peak timing;
8. UI/reward/text lifecycle and final-frame handling;
9. explicit anti-flattening requirements;
10. one overall directing rationale.

The text must clearly state that camera changes happen **inside the one generated video**, not as a post-production request.

### Example of Generic, Dynamic Direction

The system may describe a reference-derived sequence like:

```text
Establish target-world subject and environment
-> isolate the visible trigger with a tighter attention phase
-> escalate anticipation through camera energy and lighting
-> release the primary action with its effect peak synchronized to impact
-> let the visible result resolve into the caller's final-frame composition
```

This is a transferable directing pattern, not a hard-coded instruction to use a particular face close-up, glowing eyes, sword, flame, reward value, logo, or game genre.

## UI, Reward, and Text Adaptation

The director reasons per visible overlay element rather than applying a global final-text lock.

For each significant reference UI/reward/text element, it decides one of:

- `replace_with_target`: use the target-frame counterpart;
- `preserve`: retain the observed element if it remains compatible with caller frames;
- `preserve_through_last_anchor`: retain it until and including the final shot;
- `endpoint_only`: use it only as a final result element;
- `omit`: do not carry an element whose role cannot be supported by the target visual world.

If the reference video has a meaningful final reward/text and the caller's final frame has no corresponding text, the director can choose `preserve_through_last_anchor` when evidence shows it is part of the result state. This is a creative decision inside the generated storyboard, not a later image-overlay post-process.

## Time Adaptation

Reference-video and requested output durations may differ. The system will adapt **beats**, not blindly stretch timestamps.

Rules:

- preserve ordering and causal dependencies;
- reserve time for setup, trigger, action release, impact, and final resolution according to their importance;
- compress low-importance traversal or repeated motion before compressing a critical climax beat;
- ensure the final anchor receives a readable resolution window;
- record original and target timing in private metadata for debugging;
- never claim exact temporal equivalence when target duration makes it impossible.

## Data Model Evolution

Existing frame analysis and frame-anchored storyboard models will be extended internally with optional director-oriented structures. The public API response model does not change.

Indicative additions:

```text
ReferenceVideoAnalysis
  - observed_shots / evidence events
  - adaptive sampling metadata
  - existing behavior and identity mappings

FrameAnalysis
  - existing frame facts and reference analysis
  - director_plan (optional before migration completion)

FrameAnchoredStoryboardScene
  - cinematic beat / attention objective
  - camera instruction
  - tension level
  - action-result requirement
  - effect timing
  - overlay instruction
```

Validation must ensure that a structured storyboard still starts at `first_frame`, ends at `last_frame`, and that its required high-importance director beats are represented before formatting.

## Prompt and Safety Boundaries

- Reference content is a source of motion, causal, camera, rhythm, and effect evidence, not an identity asset.
- First/last caller frames remain the authority for concrete target visual identity and final state.
- Existing provider-boundary safety policy remains in effect for any directly supplied `storyboard_text` video-generation request.
- Creative-direction prompts must not introduce category-specific substitute content or unrelated safety-copy transformations.
- Private raw analysis and provider responses remain internal task metadata and are not returned through polling.

## Observability and Diagnostics

For each V2 task, private metadata should make it possible to determine whether a weak video came from:

1. insufficient adaptive keyframe evidence;
2. factual video-analysis failure;
3. director-plan failure;
4. structured storyboard / text formatting loss;
5. downstream video-model instruction non-compliance.

Store compact, redacted metadata such as selected timestamps, selection reasons, evidence events, director plan, duration adaptation, and generated storyboard. Avoid storing unnecessary raw media or sensitive request payloads in logs.

## Acceptance Criteria

### Contract and Compatibility

- Existing V2 create and polling routes, auth, async task behavior, and public output fields remain compatible.
- `storyboard_text` remains the only public storyboard payload.
- Existing external video-generation endpoint remains unchanged and accepts the resulting plain text with its existing two-image contract.

### Reference Understanding

- Short transition / close-up / impact / flash / final reward moments have a better chance of appearing in evidence than with fixed two-second-only sampling.
- Analysis clearly separates observed facts from director-inferred creative guidance.
- Analysis never presents reference visual identity as target-frame identity.

### Direction Quality

- The generated structured storyboard has an explicit attention path, tension curve, and at least one evidence-grounded climax plan when the reference supports one.
- High-importance trigger, release, impact, and resolution beats are not silently merged into one uniform scene.
- Camera, action, effect, and UI instructions are causally synchronized rather than independently listed.
- The final scene remains anchored to the caller's last frame.

### Reliability

- Adaptive sampling failure falls back safely to the existing extraction method.
- Invalid internal director output fails with a diagnosable provider/schema error instead of emitting malformed public data.
- Unit tests cover keyframe selection fallback, director-plan schema validation, time adaptation, text formatting, identity isolation, and public contract stability.

## Rollout and Evaluation

1. Implement behind the existing V2 route with no external schema change.
2. Run the current gold reference sample plus diverse reference categories such as product reveal, vehicle motion, character action, reward/result, and transformation.
3. Compare old versus new `storyboard_text` on: beat clarity, camera separation, causal action, climax specificity, final-frame alignment, and non-copying of reference identity.
4. Generate real single-pass videos for a selected evaluation set.
5. Label failures by evidence, direction, formatting, or provider execution before changing prompts again.

## Intentional Exclusions

- No automatic final-frame text compositor or post-generation overlay mechanism.
- No forced hard-coded AAA shot formula.
- No extra public endpoint, webhook, task queue, database table, or frontend migration solely for this enhancement.
- No guarantee of literal cuts where the chosen video provider cannot represent them in one generation.