# External Storyboard V2 Frame-Anchored Design

## Status

Approved product design. This document defines the new V2 storyboard endpoint only. It does not change the legacy storyboard endpoint or the external video-generation endpoint.

## Goal

Generate a readable video storyboard by treating two caller-supplied images as the source of truth:

```text
first frame -> visually coherent transition -> last frame
```

The system first understands both frames with a multimodal model, then uses that analysis plus the same two images to create the storyboard. The model must not be driven by upstream copy, a product name, a fixed creative strategy, or a preselected game-ad template.

## Non-Goals

- Do not alter `POST /api/v1/integrations/ai/storyboard`.
- Do not change `POST /api/v1/integrations/video-generation/videos`.
- Do not modify the external Pixel system in this implementation.
- Do not start video generation from the V2 storyboard endpoint.
- Do not add human-review, brand-conflict, safety-conflict, or frame-relatedness blocking states.
- Do not introduce a separate TTS or audio-generation service.

## Public API

### Create Job

```http
POST /api/v1/integrations/ai/storyboard-v2
```

```json
{
  "external_request_id": "wf-storyboard-v2-001",
  "first_frame_image_url": "https://example.com/first.png",
  "last_frame_image_url": "https://example.com/last.png",
  "duration_seconds": 12,
  "aspect_ratio": "9:16"
}
```

| Field | Required | Rule |
| --- | --- | --- |
| `external_request_id` | No | Existing idempotency behavior; maximum 128 characters. |
| `first_frame_image_url` | Yes | Public URL for the exact opening video frame. |
| `last_frame_image_url` | Yes | Public URL for the exact ending video frame. |
| `duration_seconds` | No | Default 12; existing 1-300 validation range. |
| `aspect_ratio` | No | Default `9:16`; existing string validation. |

The schema uses `extra="forbid"`. It does not accept `product_name`, `brief`, `prompt`, `language`, `output_language`, or `image_urls`.

Create and polling envelopes stay compatible with the current external AI API. Polling uses the existing route:

```http
GET /api/v1/integrations/ai/jobs/{job_id}
```

Successful public V2 results expose only:

```json
{
  "job_id": "job-uuid",
  "status": "succeeded",
  "request_id": "wf-storyboard-v2-001",
  "storyboard_text": "...",
  "duration_seconds": 12,
  "aspect_ratio": "9:16"
}
```

The poll response never exposes `frame_analysis`, structured storyboard data, sound design, or raw provider output.

## Task Flow

V2 creates a new task type in the existing text queue:

```text
queue_name: text_queue
task_type: external_video_storyboard_v2
business_type: external_ai
campaign_id: null
```

The lifecycle remains:

```text
POST -> GenerationTask -> worker_text -> GET polling result
```

One worker execution makes two sequential multimodal calls:

```text
first and last images
  -> frame analysis
  -> frame analysis + same images
  -> structured storyboard
  -> readable storyboard_text
```

Only technical failures produce a failed task: image URL not usable by the provider, provider timeout/error, invalid provider JSON, or invalid required storyboard structure. There is no `needs_review` state; creating a bridge between the supplied frames is the model's responsibility.

## Multimodal Provider Contract

V2 uses actual Responses API image input, not image URLs embedded in a text JSON object:

```json
{
  "model": "gpt-5.5",
  "input": [
    {"role": "system", "content": "..."},
    {
      "role": "user",
      "content": [
        {"type": "input_text", "text": "FIRST FRAME ..."},
        {"type": "input_image", "image_url": "first-frame-url"},
        {"type": "input_text", "text": "LAST FRAME ..."},
        {"type": "input_image", "image_url": "last-frame-url"}
      ]
    }
  ]
}
```

The deployed gateway and `gpt-5.5` model were verified to accept two `input_image` items and distinguish the first image from the second image.

## Model Call 1: Frame Analysis

Inputs are the first-frame image, last-frame image, duration, and aspect ratio.

The analysis prompt is factual and neutral. It directs the model to identify visible subjects, existing text, environment, composition, visual style, color, lighting, visual state, shared facts, continuity requirements, a plausible visual transition, and a recommended output language inferred from frame text and visual context.

It must not direct the model to write a storyboard, create copy/CTA/logo/subtitles/voiceover, sanitize or reinterpret image content, apply Meta/Facebook policy, apply creative-safety policy, apply gambling keyword substitutions, apply fixed game/Boss/VIP/CTA/VFX templates, or apply `creative_strategy`.

The validated internal output is:

```json
{
  "first_frame": {
    "visible_subjects": [],
    "visible_text": [],
    "environment": "",
    "composition": "",
    "camera_perspective": "",
    "visual_style": "",
    "color_and_lighting": "",
    "opening_state": ""
  },
  "last_frame": {
    "visible_subjects": [],
    "visible_text": [],
    "environment": "",
    "composition": "",
    "camera_perspective": "",
    "visual_style": "",
    "color_and_lighting": "",
    "ending_state": ""
  },
  "transition_brief": {
    "shared_visual_facts": [],
    "continuity_requirements": [],
    "visual_transition": "",
    "narrative_arc": ""
  },
  "language_analysis": {
    "first_frame_visible_languages": [],
    "last_frame_visible_languages": [],
    "recommended_output_language": "",
    "reason": ""
  }
}
```

The analysis is stored only in task metadata and passed to call 2.

## Model Call 2: Storyboard Generation

Inputs are the same two real images, full `frame_analysis`, duration, aspect ratio, and the internally recommended output language. The images are sent again so the model does not rely only on a textual summary.

The system prompt is limited to frame continuity, duration, aspect ratio, output structure, and language behavior. It requires the first scene to begin from the actual first-frame visual state and the final scene to arrive at the actual last-frame visual state. It permits only the motion, camera work, lighting change, environmental change, or action needed to bridge those frames.

The model must not introduce a key brand, logo, product, major character, core setting, or generic end card absent from both frames. It must not claim an element exists in a supplied frame unless visible in the image or confirmed by `frame_analysis`. It must not translate or rewrite supplied-image text.

The symmetric frame rule is mandatory:

```text
If the first frame has no brand, logo, hook, or text, do not add one as a first-frame fact.
If the last frame has no brand, logo, VIP label, CTA, or end card, do not add one or replace the final frame with one.
```

The prompt must not include Meta/Facebook compliance, creative-safety rules, gambling/cash substitutions, `creative_strategy`, CopyDraft content, `brief`, `product_name`, or fixed 3A game/Boss/VIP/CTA/VFX templates.

The structured result is:

```json
{
  "duration_seconds": 12,
  "aspect_ratio": "9:16",
  "scenes": [
    {
      "scene_index": 1,
      "start_second": 0,
      "end_second": 3,
      "frame_anchor": "first_frame",
      "visual": "",
      "motion": "",
      "transition_goal": "",
      "subtitle": null,
      "voiceover": null,
      "sound_effects": [],
      "notes": ""
    }
  ],
  "sound_design": {
    "music": "",
    "ambience": ""
  },
  "rationale": ""
}
```

Validation requires the first scene to use `first_frame`, the final scene to use `last_frame`, and every middle scene to use `transition` as `frame_anchor`.

Subtitles and voiceover are optional. New subtitle, voiceover, and visible text use the internally recommended language. Existing frame text is only recognized and continued.

## Readable Storyboard Text

The backend deterministically formats the validated result into `storyboard_text` for external human review:

```text
Video duration: 12 seconds
Aspect ratio: 9:16

Scene 1 | 0-3s | First-frame anchor
Visual: ...
Camera: ...
Subtitle: None
Voiceover: ...
Sound effects: ...
Transition goal: ...

Scene 2 | 3-9s | Transition
...

Scene 3 | 9-12s | Last-frame anchor
...

Overall sound design:
Music: ...
Ambience: ...
```

V2 has no confirmation, editing, or automatic video-generation behavior.

## Audio Boundary

V2 produces only text directions: optional voiceover, per-scene sound effects, music direction, and ambience. It creates no audio asset and does not call a video provider. A future caller can include these directions in the existing video-generation prompt. The current provider configuration uses `generate_audio=true`; the provider synthesizes any resulting audio. No separate TTS integration is part of V2.

## Storage and Compatibility

`GenerationTask.payload` stores the public V2 request. `GenerationTask.metadata` stores internal `frame_analysis` and the structured storyboard. `GenerationTask.result` stores only public success fields.

V2 creates no Campaign, CopyDraft, CreativeAsset, or VideoAsset records.

The legacy storyboard route/schema remain unchanged. V2 reuses the existing authentication, async envelope, text queue, idempotency handling, and polling route. Updating the external Pixel system to submit both locked frame URLs is a later change.

## Verification Plan

Focused tests must cover:

1. Missing first/last URLs and forbidden legacy fields fail validation.
2. V2 creates idempotent `external_video_storyboard_v2` work in `text_queue`.
3. Analysis runs before storyboard generation.
4. Both calls receive distinct first/last images in the documented order.
5. Call 2 receives frame analysis but no legacy brief/copy/strategy data.
6. Structured output enforces first/transition/last anchors.
7. Public polling exposes only public result fields.
8. Metadata contains internal analysis and structured storyboard data.
9. V2 does not create internal business-table records.
10. Legacy external AI tests remain passing.

After implementation, run a local production-like Docker rebuild and one controlled V2 request with public images. Verify the public polling envelope and the internal task metadata.

## Reference Video Extension

This approved extension adds an optional motion and presentation reference to the same V2 endpoint. It does not change the legacy storyboard or video-generation endpoints.

### Public Contract

The create request may include exactly one of these strict source objects:

```json
{"reference_video": {"source_type": "url", "video_url": "https://example.com/reference.mp4"}}
```

```json
{"reference_video": {"source_type": "uploaded_asset", "upload_asset_id": "opaque-upload-id"}}
```

```json
{"reference_video": {"source_type": "video_asset", "video_asset_id": "video-asset-uuid"}}
```

Each source object uses `extra="forbid"` and accepts only the identifier matching its `source_type`. Omitting `reference_video` preserves the existing first/last-frame behavior.

Uploaded reference sources are created through:

```http
POST /api/v1/integrations/ai/storyboard-v2/reference-video
Content-Type: multipart/form-data
```

The upload endpoint accepts MP4, MOV, or WebM, stores a private temporary source under local storage, and returns an opaque `upload_asset_id`. It creates no Campaign, CreativeAsset, or VideoAsset rows.

### Media Processing

The worker resolves the supplied URL, upload reference, or existing `VideoAsset`, then verifies the file with FFprobe. Reference videos longer than 30 seconds are rejected.

FFmpeg extracts downscaled JPEG analysis frames sequentially at:

```text
0s, 2s, 4s, ... and the exact final frame
```

Duplicate final timestamps are removed. Images are not analyzed in parallel and the system does not make one model request per frame.

### Joint Visual Analysis

The first model call receives all images in this exact order:

```text
TARGET FIRST FRAME
TARGET LAST FRAME
REFERENCE 0s
REFERENCE 2s
REFERENCE 4s
...
REFERENCE FINAL
```

Target frames decide what is actually present in the generated video. Reference frames describe only how content moves and is presented. The advertising request decides what the video expresses.

The model extracts only:

```text
subject presence
camera movement
transitions
visible effects
```

It must not analyze reference audio, speech, lyrics, voiceover, narration, subtitles, or transcripts. It must not copy reference characters, brands, text, products, or setting into the target video.

The existing `FrameAnalysis` gains an optional validated `reference_video_analysis` containing chronological segments and adapted constraints. Subject-presence continuity has strength `required`. Camera, transition, and effects patterns have strength `preferred` and may adapt where target-frame truth requires it.

The second model call receives the full private analysis. Its priority order is:

```text
1. Target first/last frame truth
2. Required subject-presence continuity
3. Advertising objective
4. Preferred camera, transition, and effects patterns
```

Reference analysis remains private task metadata and is never returned by public polling.

### Failure Semantics

When `reference_video` is present, any source-resolution, download, format, duration, FFprobe, FFmpeg extraction, joint-model analysis, or reference-schema failure stops the task before storyboard generation. The worker must not silently fall back to first/last-frame inference.

Verification must additionally prove source-schema validation, 2-second chronological sampling with exact opening/final coverage, one-request image ordering, required/preferred prompt rules, failure before call 2, upload privacy, and unchanged no-reference behavior.
