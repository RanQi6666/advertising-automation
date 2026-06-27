# Image Generation Mode Switch Design

## Background

The current image generation path implicitly creates or reuses a video creative script before generating images. That makes ordinary ad-image generation and video keyframe generation feel like one hidden workflow. The operator wants this split into an explicit choice on the image screen: one mode creates normal ad images from copy, and the other mode creates first/last-frame image schemes from a video creative script.

There is already backend support for two creative generation modes:

- `standard` for normal image candidates.
- `video_keyframe_variants` for grouped first/last-frame variants.

The existing frontend also has recent work for keyframe grouping, scheme review, topic-scoped artifacts, and video reference options. This design should extend those boundaries instead of replacing them.

## Goals

1. Keep one `Generate images` action on the image screen.
2. Add a clear mode switch so the operator chooses whether that action means copy-based image generation or video-script keyframe generation.
3. Move video creative script generation and rewrite controls into the image screen when the keyframe mode is active.
4. Preserve the current 1/2/3 keyframe scheme choice for video-script image generation.
5. Generate exactly 2 normal ad images in copy-image mode.
6. Stop the old implicit behavior where normal image generation first creates a video script.
7. Show approved copy-image results in the ad preview.
8. Keep video keyframe images grouped as video reference schemes and out of the normal ad preview.
9. Upgrade the image screen visual design so it feels like a premium AIGC production console rather than a plain form.

## Non-Goals

This change will not create new asset tables or migrate existing creative assets.

This change will not change image or video provider integrations.

This change will not remove video creation, video preview, or video review from the video screen. It only moves the main script generation and rewrite controls to the image screen.

This change will not make keyframe images appear in the copy ad preview. The preview should stay focused on ordinary ad images that can be used directly as image ads.

## Recommended Flow

The image screen gets a mode switch with two values:

- `Copy images`
- `Video keyframes`

The switch changes the meaning of the single `Generate images` button.

### Copy Images Mode

The operator chooses `Copy images` and clicks `Generate images`.

The frontend calls the existing creative stream endpoint with:

```json
{
  "generation_mode": "standard",
  "count": 2,
  "size": "1:1"
}
```

The request must not include `storyboard` or `storyboard_text`.

Generated assets are shown as two normal image candidates. Each can be approved, rejected, retried, or regenerated with feedback. Approved normal images are eligible for the copy ad preview and final payload.

### Video Keyframes Mode

The operator chooses `Video keyframes`.

The image screen reveals a script workbench with:

- aspect ratio
- duration
- style and camera instructions
- generate script
- rewrite script
- editable script text

The operator can select 1/2/3 schemes. Each scheme still means one first frame and one last frame.

When the operator clicks `Generate images`, the frontend requires a non-empty script. If the script is empty, the UI prompts the operator to generate or write the script first. It must not silently auto-generate the script inside the image generation action.

The frontend calls the existing creative stream endpoint with:

```json
{
  "generation_mode": "video_keyframe_variants",
  "count": 2 | 4 | 6,
  "variant_count": 1 | 2 | 3,
  "frames_per_variant": 2,
  "video_duration_seconds": 12,
  "storyboard": [],
  "storyboard_text": "..."
}
```

Generated assets are shown as grouped schemes. A complete scheme has a first frame and a last frame. Review actions operate on the scheme as a pair. Approved schemes become selectable reference options on the video screen.

## Frontend Architecture

### State

Add a small UI state for the active image generation mode:

```ts
type CreativeGenerationUiMode = "copy_images" | "video_keyframes";
```

The default should be `copy_images`, because it is the direct advertising-image path and avoids accidental video-script work.

Existing video script state can remain at the top-level `App` component for the first version:

- `videoStoryboardText`
- `videoStoryboard`
- `videoStoryboardDirty`
- `videoStoryboardFeedback`
- `videoInstructions`
- `videoAspectRatio`
- `videoDurationSeconds`

The script controls move visually into `CreativesView`, but can still receive handlers and state from `App`.

### Generation Handler

Replace the current implicit `ensureStoryboardForImageGeneration` dependency with an explicit branch:

- `copy_images`: call creative generation with `standard`, `count = 2`, and no storyboard context.
- `video_keyframes`: require existing script text, then call creative generation with the keyframe plan and storyboard context.

Retrying a failed slot should use the mode implied by that slot or the current active mode. For the first version, using the current mode is acceptable if the UI keeps the failed slots visible only within the active mode.

Regenerating an existing asset should preserve its existing metadata, which is already handled for keyframe metadata in the backend.

### Preview Filtering

Ad preview image selection should include only approved normal image assets. A normal image asset is one whose metadata does not contain `generation_mode: "video_keyframe_variants"`.

Video reference options should include approved keyframe schemes first. If no approved keyframe schemes exist, the existing fallback to approved normal images may remain for compatibility, but the primary path should be approved first/last-frame schemes.

## Visual Design Direction

The image screen should feel like a premium AIGC production cockpit: dark, precise, high-contrast, and slightly cinematic without becoming decorative clutter.

### Token Direction

Use a restrained, multi-hue palette:

- `carbon`: `#101417` for the working surface.
- `graphite`: `#1f2930` for panels and rails.
- `electric cyan`: `#39d5ff` for active mode, generation progress, and focus accents.
- `laser magenta`: `#ff4fd8` for secondary highlights and script energy.
- `warm signal`: `#f7b955` for warnings, pending states, and review attention.
- `paper white`: `#f6f8fb` for high-priority text.

Avoid making the page a one-note purple, blue, beige, or orange theme. Cyan and magenta should act as energy traces, not full backgrounds.

### Layout Signature

The signature element is a `generation console rail` at the top of the image screen:

- left: mode switch with two explicit choices
- center: current mode status, model selector, and scheme count when relevant
- right: single `Generate images` action

Under the rail, the keyframe mode reveals a script workbench that feels like a compact editing console. The script workbench should use a darker inset surface, thin luminous focus states, and a subtle progress/readiness indicator. Normal copy-image mode should keep the area denser and quieter.

### Interaction Details

Use motion sparingly:

- a short mode-switch transition
- loading shimmer on active generation slots
- a calm glow on the active generate button
- respect reduced-motion settings

Controls should stay operational and clear:

- segmented mode switch, not a vague toggle
- icon plus label for important actions
- no nested cards inside cards
- no decorative gradient orbs
- fixed card dimensions for image slots so loading, errors, and labels do not shift the layout

### Copy Tone

Interface labels should be direct:

- `Copy images`
- `Video keyframes`
- `Generate images`
- `Generate script`
- `Rewrite script`
- `Schemes`
- `First frame`
- `Last frame`

Errors should tell the operator what to do, for example: `Generate or paste a video script before creating keyframes.`

## Backend Impact

No backend schema change is required.

The existing `CreativeGenerateRequest` already supports:

- `generation_mode`
- `variant_count`
- `frames_per_variant`
- `video_duration_seconds`
- `storyboard`
- `storyboard_text`

The implementation may add or adjust tests to ensure normal image generation can be requested without storyboard context and keyframe generation keeps the expected metadata.

## Testing Plan

Frontend unit tests:

- creative mode helpers return `count = 2` for copy images.
- creative mode helpers return `count = variant_count * 2` for keyframes.
- ad preview filters out keyframe assets and includes approved normal assets.
- video reference options still group approved first/last-frame schemes.

Backend tests:

- standard generation does not set keyframe metadata.
- keyframe generation still sets `generation_mode`, `keyframe_group`, `keyframe_role`, and `keyframe_position`.

Manual verification:

- switch to `Copy images`, generate 2 images, approve one, confirm it appears in ad preview.
- switch to `Video keyframes`, generate or edit a script, generate 1/2/3 schemes, approve a scheme, confirm it appears as a video reference option.
- confirm clicking `Generate images` in keyframe mode without a script shows a clear error and does not create a hidden storyboard request.
- run frontend build and targeted backend/frontend tests.

## Rollout Notes

This feature affects the runnable frontend and generation flow. After implementation, sync the runnable app into the local Docker environment when feasible and verify the production-like entry still loads.

Do not stage or commit `AGENTS.md`, `.env`, `.env.production`, real API keys, database passwords, or local backup env files.
