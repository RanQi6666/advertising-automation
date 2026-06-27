# Image Generation Mode Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one image-generation button with a mode switch for copy-based images versus video-script keyframes, and redesign the image screen as a premium AIGC production console.

**Architecture:** Keep the existing backend creative stream endpoint and introduce frontend helpers for UI generation modes. `App.tsx` owns mode state and request branching; `CreativesView` renders the mode switch, script workbench, single generate action, and current review surface. `workflowArtifacts.ts` continues to separate normal ad images from keyframe schemes for preview and video reference behavior.

**Tech Stack:** React 19, TypeScript, Vite, Node test runner, FastAPI backend, existing creative stream API.

## Global Constraints

- Keep one `Generate images` action on the image screen.
- Add a segmented mode switch with `Copy images` and `Video keyframes`.
- `Copy images` mode sends `generation_mode: "standard"`, `count = 2`, and no storyboard context.
- `Video keyframes` mode requires a non-empty script before image generation and never silently creates the script inside `Generate images`.
- Preserve the existing 1/2/3 keyframe scheme selector; each scheme has 2 frames.
- Approved copy-image assets appear in the ad preview.
- Keyframe assets stay grouped as video reference schemes and do not appear in the copy ad preview.
- The image screen should feel like a premium AIGC production cockpit using carbon, graphite, electric cyan, laser magenta, warm signal, and paper white accents.
- Do not stage or commit `AGENTS.md`, `.env`, `.env.production`, real secrets, or local env backups.

---

## File Structure

- Modify `frontend/web-admin/src/lib/creativeKeyframes.ts`: add UI mode helpers for copy-image and keyframe generation plans.
- Modify `frontend/web-admin/tests/creativeKeyframes.test.ts`: test mode-specific image counts and request intent.
- Modify `frontend/web-admin/src/lib/workflowArtifacts.ts`: export a helper that identifies normal ad-preview images.
- Modify `frontend/web-admin/tests/workflowArtifacts.test.ts`: test ad-preview filtering excludes keyframes and includes approved normal images.
- Modify `frontend/web-admin/src/App.tsx`: wire mode state, explicit request branching, keyframe script requirement, and moved script handlers into `CreativesView`.
- Modify `frontend/web-admin/src/styles.css`: add the premium image-console visual system and responsive controls.

---

### Task 1: Add Generation Mode Helpers

**Files:**
- Modify: `frontend/web-admin/src/lib/creativeKeyframes.ts`
- Test: `frontend/web-admin/tests/creativeKeyframes.test.ts`

**Interfaces:**
- Consumes: existing `KeyframeVariantCount`, `KEYFRAME_FRAMES_PER_VARIANT`, and `normalizeKeyframeVariantCount`.
- Produces:
  - `type CreativeGenerationUiMode = "copy_images" | "video_keyframes"`
  - `COPY_IMAGE_GENERATION_COUNT = 2`
  - `imageGenerationPlanForMode(mode, durationSeconds, aspectRatio, keyframeVariantCount): CreativeGenerationPlan`
  - `isVideoKeyframeMode(mode): boolean`

- [ ] **Step 1: Write the failing tests**

Append these tests to `frontend/web-admin/tests/creativeKeyframes.test.ts`:

```ts
test("copy image mode generates exactly two standard images without keyframe metadata", () => {
  const plan = imageGenerationPlanForMode("copy_images", 12, "9:16", 3);

  assert.equal(plan.count, 2);
  assert.equal(plan.size, "1:1");
  assert.equal(plan.generationMode, "standard");
  assert.equal(plan.isKeyframeVariant, false);
  assert.equal(plan.variantCount, undefined);
  assert.equal(plan.framesPerVariant, undefined);
});

test("video keyframe mode preserves selectable first and last frame schemes", () => {
  const plan = imageGenerationPlanForMode("video_keyframes", 12, "9:16", 3);

  assert.equal(plan.count, 6);
  assert.equal(plan.size, "9:16");
  assert.equal(plan.generationMode, "video_keyframe_variants");
  assert.equal(plan.isKeyframeVariant, true);
  assert.equal(plan.variantCount, 3);
  assert.equal(plan.framesPerVariant, 2);
  assert.equal(plan.videoDurationSeconds, 12);
});
```

Also update the import list:

```ts
import {
  imageGenerationPlanForMode,
  normalizeKeyframeVariantCount,
} from "../src/lib/creativeKeyframes.ts";
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd frontend/web-admin
npm test -- tests/creativeKeyframes.test.ts
```

Expected: FAIL because `imageGenerationPlanForMode` is not exported.

- [ ] **Step 3: Write minimal implementation**

Add this to `frontend/web-admin/src/lib/creativeKeyframes.ts`:

```ts
export type CreativeGenerationUiMode = "copy_images" | "video_keyframes";
export const COPY_IMAGE_GENERATION_COUNT = 2;

export function isVideoKeyframeMode(mode: CreativeGenerationUiMode): boolean {
  return mode === "video_keyframes";
}

export function imageGenerationPlanForMode(
  mode: CreativeGenerationUiMode,
  durationSeconds: number,
  aspectRatio: string,
  keyframeVariantCount: KeyframeVariantCount = DEFAULT_KEYFRAME_VARIANT_COUNT,
): CreativeGenerationPlan {
  if (isVideoKeyframeMode(mode)) {
    return {
      count: keyframeVariantCount * KEYFRAME_FRAMES_PER_VARIANT,
      size: aspectRatio || "9:16",
      generationMode: "video_keyframe_variants",
      isKeyframeVariant: true,
      variantCount: keyframeVariantCount,
      framesPerVariant: KEYFRAME_FRAMES_PER_VARIANT,
      videoDurationSeconds: durationSeconds,
    };
  }

  return {
    count: COPY_IMAGE_GENERATION_COUNT,
    size: "1:1",
    generationMode: "standard",
    isKeyframeVariant: false,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
cd frontend/web-admin
npm test -- tests/creativeKeyframes.test.ts
```

Expected: PASS for all creative keyframe tests.

---

### Task 2: Add Preview Filtering Helpers

**Files:**
- Modify: `frontend/web-admin/src/lib/workflowArtifacts.ts`
- Test: `frontend/web-admin/tests/workflowArtifacts.test.ts`

**Interfaces:**
- Consumes: existing `isKeyframeVariantAsset` and `buildCreativeReviewState`.
- Produces:
  - `adPreviewCreativeOptions(creatives: CreativeAsset[], selectedDraft: CopyDraft | null): CreativeAsset[]`

- [ ] **Step 1: Write the failing test**

Append this test to `frontend/web-admin/tests/workflowArtifacts.test.ts`:

```ts
test("ad preview options include approved normal images and exclude keyframe assets", () => {
  const selectedDraft = draft({ id: "draft-preview" });
  const approvedNormal = creative({
    id: "approved-normal",
    draft_id: selectedDraft.id,
    status: "approved",
    metadata_json: { image_index: 1 },
  });
  const generatedNormal = creative({
    id: "generated-normal",
    draft_id: selectedDraft.id,
    status: "generated",
    metadata_json: { image_index: 2 },
  });
  const keyframe = keyframeCreative("approved-keyframe", 1, 1, 1, "approved");

  const options = adPreviewCreativeOptions([keyframe, generatedNormal, approvedNormal], selectedDraft);

  assert.deepEqual(options.map((asset) => asset.id), ["approved-normal"]);
});
```

Update the import list to include `adPreviewCreativeOptions`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd frontend/web-admin
npm test -- tests/workflowArtifacts.test.ts
```

Expected: FAIL because `adPreviewCreativeOptions` is not exported.

- [ ] **Step 3: Write minimal implementation**

Add this exported helper to `frontend/web-admin/src/lib/workflowArtifacts.ts`:

```ts
export function adPreviewCreativeOptions(
  creatives: CreativeAsset[],
  selectedDraft: CopyDraft | null,
): CreativeAsset[] {
  const draftId = selectedDraft?.id ?? null;
  return currentCreativeAssets(
    creatives.filter(
      (asset) =>
        Boolean(asset.url) &&
        asset.status === "approved" &&
        !isKeyframeVariantAsset(asset) &&
        (!draftId || asset.draft_id === draftId),
    ),
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
cd frontend/web-admin
npm test -- tests/workflowArtifacts.test.ts
```

Expected: PASS for all workflow artifact tests.

---

### Task 3: Wire Explicit Mode Branching in App State

**Files:**
- Modify: `frontend/web-admin/src/App.tsx`

**Interfaces:**
- Consumes: `CreativeGenerationUiMode`, `imageGenerationPlanForMode`, `isVideoKeyframeMode`, and `adPreviewCreativeOptions`.
- Produces:
  - top-level `creativeGenerationMode` state
  - explicit image generation request branch
  - no use of `ensureStoryboardForImageGeneration` inside normal copy-image generation

- [ ] **Step 1: Add mode imports and state**

Update imports from `creativeKeyframes`:

```ts
import {
  DEFAULT_KEYFRAME_VARIANT_COUNT,
  KEYFRAME_FRAMES_PER_VARIANT,
  KEYFRAME_VARIANT_OPTIONS,
  buildKeyframePlanProgress,
  imageGenerationPlanForMode,
  isVideoKeyframeMode,
  keyframeGroupSlotIndices,
  normalizeKeyframeVariantCount,
  type CreativeGenerationUiMode,
  type KeyframeVariantCount,
} from "./lib/creativeKeyframes";
```

Add to the existing top-level state near `keyframeVariantCount`:

```ts
const [creativeGenerationMode, setCreativeGenerationMode] =
  useState<CreativeGenerationUiMode>("copy_images");
```

- [ ] **Step 2: Replace generation plan calls**

In `handleGenerateCreatives`, replace:

```ts
const generationPlan = creativeGenerationPlan(videoDurationSeconds, videoAspectRatio, keyframeVariantCount);
```

with:

```ts
const generationPlan = imageGenerationPlanForMode(
  creativeGenerationMode,
  videoDurationSeconds,
  videoAspectRatio,
  keyframeVariantCount,
);
```

Apply the same replacement in `handleRetryCreativeSlot` and inside `CreativesView`.

- [ ] **Step 3: Make storyboard context explicit**

In `handleGenerateCreatives`, replace the unconditional storyboard call with:

```ts
const storyboardContext = isVideoKeyframeMode(creativeGenerationMode)
  ? currentStoryboardContextForKeyframes()
  : null;
if (isVideoKeyframeMode(creativeGenerationMode) && !storyboardContext) {
  setError("Generate or paste a video script before creating keyframes.", "image");
  markLoadingCreativeSlotsFailed("Waiting for a video script.");
  return;
}
```

Add this helper near `ensureStoryboardForImageGeneration`, then remove the old `ensureStoryboardForImageGeneration` function when no call sites remain:

```ts
function currentStoryboardContextForKeyframes(): {
  storyboard: Record<string, unknown>[];
  storyboardText: string;
} | null {
  const storyboardText = videoStoryboardText.trim();
  if (!storyboardText) return null;
  return {
    storyboard: videoStoryboardDirty
      ? storyboardPayloadFromText(videoStoryboardText, videoStoryboard)
      : videoStoryboard,
    storyboardText: videoStoryboardText,
  };
}
```

- [ ] **Step 4: Pass the branch into `api.generateCreativesStream`**

Use:

```ts
storyboardContext?.storyboard
storyboardContext?.storyboardText
```

inside options so copy-image mode sends neither field.

- [ ] **Step 5: Update retry branch**

In `handleRetryCreativeSlot`, build `storyboardContext` the same way. If mode is keyframe and no script exists, set image error and set the slot back to error.

- [ ] **Step 6: Run TypeScript build**

Run:

```powershell
cd frontend/web-admin
npm run build
```

Expected: no TypeScript errors.

---

### Task 4: Move Script Workbench Into the Image Screen

**Files:**
- Modify: `frontend/web-admin/src/App.tsx`

**Interfaces:**
- Consumes: existing `handleGenerateVideoStoryboard`, `handleRewriteVideoStoryboard`, and storyboard state.
- Produces: `CreativesView` props for mode, script state, and script actions.

- [ ] **Step 1: Pass new props to `CreativesView`**

Add these props at the `CreativesView` call site:

```tsx
generationMode={creativeGenerationMode}
setGenerationMode={setCreativeGenerationMode}
storyboardText={videoStoryboardText}
setStoryboardText={(value) => {
  setVideoStoryboardText(value);
  setVideoStoryboardDirty(true);
}}
storyboardFeedback={videoStoryboardFeedback}
setStoryboardFeedback={setVideoStoryboardFeedback}
videoInstructions={videoInstructions}
setVideoInstructions={setVideoInstructions}
onGenerateStoryboard={() => void handleGenerateVideoStoryboard()}
onRewriteStoryboard={() => void handleRewriteVideoStoryboard()}
```

- [ ] **Step 2: Extend `CreativesView` props**

Add prop names and types to the component signature:

```ts
generationMode: CreativeGenerationUiMode;
setGenerationMode: (mode: CreativeGenerationUiMode) => void;
storyboardText: string;
setStoryboardText: (value: string) => void;
storyboardFeedback: string;
setStoryboardFeedback: (value: string) => void;
videoInstructions: string;
setVideoInstructions: (value: string) => void;
onGenerateStoryboard: () => void;
onRewriteStoryboard: () => void;
```

- [ ] **Step 3: Render the mode switch**

Inside the `CreativesView` panel header action area, add:

```tsx
<div className="generation-mode-switch" role="tablist" aria-label="Image generation mode">
  <button
    className={generationMode === "copy_images" ? "active" : ""}
    type="button"
    onClick={() => setGenerationMode("copy_images")}
    disabled={Boolean(loading)}
  >
    <Image size={16} />
    <span>Copy images</span>
  </button>
  <button
    className={generationMode === "video_keyframes" ? "active" : ""}
    type="button"
    onClick={() => setGenerationMode("video_keyframes")}
    disabled={Boolean(loading)}
  >
    <Film size={16} />
    <span>Video keyframes</span>
  </button>
</div>
```

- [ ] **Step 4: Render script workbench only in keyframe mode**

Below the header and before progress, render:

```tsx
{generationMode === "video_keyframes" && (
  <section className="script-console">
    <div className="script-console-head">
      <div>
        <span className="section-eyebrow">SCRIPT ENGINE</span>
        <h3>Video creative script</h3>
      </div>
      <div className="script-console-actions">
        <button className="secondary-button" type="button" onClick={onGenerateStoryboard} disabled={Boolean(loading)}>
          {loading === "video-storyboard" ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}
          <span>Generate script</span>
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={onRewriteStoryboard}
          disabled={!storyboardText.trim() || !storyboardFeedback.trim() || Boolean(loading)}
        >
          {loading === "video-storyboard-rewrite" ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
          <span>Rewrite script</span>
        </button>
      </div>
    </div>
    <textarea
      className="storyboard-input script-console-textarea"
      value={storyboardText}
      onChange={(event) => setStoryboardText(event.target.value)}
      placeholder="Generate or paste a video script before creating keyframes."
      disabled={loading === "video-storyboard" || loading === "video-storyboard-rewrite"}
    />
    <div className="script-console-grid">
      <textarea
        className="video-instructions"
        value={videoInstructions}
        onChange={(event) => setVideoInstructions(event.target.value)}
        placeholder="Style and camera direction"
      />
      <textarea
        className="storyboard-feedback-input"
        value={storyboardFeedback}
        onChange={(event) => setStoryboardFeedback(event.target.value)}
        placeholder="Rewrite notes"
      />
    </div>
  </section>
)}
```

- [ ] **Step 5: Keep video page task controls**

Leave `VideosView` creation, current script text, and review controls intact. Script generation buttons can stay for compatibility in the first implementation, but the image screen is now the primary script workbench.

---

### Task 5: Apply Premium Console Styling

**Files:**
- Modify: `frontend/web-admin/src/styles.css`

**Interfaces:**
- Consumes: existing `.panel`, `.button-row`, `.secondary-button`, `.primary-button`, `.storyboard-input`, and `.video-instructions`.
- Produces: image-console styling classes that do not affect unrelated pages.

- [ ] **Step 1: Add console rail styles**

Append:

```css
.generation-mode-switch {
  display: inline-grid;
  grid-template-columns: repeat(2, minmax(132px, 1fr));
  gap: 4px;
  padding: 4px;
  border: 1px solid rgba(57, 213, 255, 0.24);
  border-radius: 8px;
  background: linear-gradient(135deg, #101417, #1f2930);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.generation-mode-switch button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 38px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #9fb2bf;
  font-weight: 780;
}

.generation-mode-switch button.active {
  color: #f6f8fb;
  background:
    linear-gradient(135deg, rgba(57, 213, 255, 0.22), rgba(255, 79, 216, 0.14)),
    #182028;
  box-shadow: 0 0 18px rgba(57, 213, 255, 0.22);
}
```

- [ ] **Step 2: Add script console styles**

Append:

```css
.script-console {
  margin: 18px 0;
  padding: 18px;
  border: 1px solid rgba(57, 213, 255, 0.22);
  border-radius: 8px;
  background:
    linear-gradient(135deg, rgba(57, 213, 255, 0.08), rgba(255, 79, 216, 0.06)),
    #101417;
  color: #f6f8fb;
  box-shadow: 0 20px 44px rgba(10, 15, 18, 0.28);
}

.script-console-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.script-console-actions,
.script-console-grid {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.script-console-textarea {
  min-height: 220px;
  color: #f6f8fb;
  background: #0c1115;
  border-color: rgba(57, 213, 255, 0.2);
}
```

- [ ] **Step 3: Add responsive guardrails**

Append:

```css
@media (max-width: 780px) {
  .generation-mode-switch {
    grid-template-columns: 1fr;
    width: 100%;
  }

  .script-console-head,
  .script-console-grid {
    display: grid;
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .generation-mode-switch button.active {
    box-shadow: none;
  }
}
```

---

### Task 6: Verify and Prepare Runtime

**Files:**
- Modify only if required by test failures: files touched above.

**Interfaces:**
- Consumes: all completed tasks.
- Produces: verified local implementation.

- [ ] **Step 1: Run frontend tests**

Run:

```powershell
cd frontend/web-admin
npm test
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```powershell
cd frontend/web-admin
npm run build
```

Expected: PASS.

- [ ] **Step 3: Run targeted backend tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_creative_streaming.py tests/test_image_prompt_guardrails.py
```

Expected: PASS.

- [ ] **Step 4: Sync production-like Docker if feasible**

Run:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl.exe -i http://127.0.0.1/api/v1/health/live
```

Expected: services are up and health returns `{"status":"ok"}`.

- [ ] **Step 5: Commit implementation**

Check status:

```powershell
git status --short
```

Stage only files from this implementation:

```powershell
git add frontend/web-admin/src/lib/creativeKeyframes.ts frontend/web-admin/tests/creativeKeyframes.test.ts frontend/web-admin/src/lib/workflowArtifacts.ts frontend/web-admin/tests/workflowArtifacts.test.ts frontend/web-admin/src/App.tsx frontend/web-admin/src/styles.css docs/superpowers/plans/2026-06-27-image-generation-mode-switch.md
git status --short
```

Confirm `AGENTS.md`, `.env`, `.env.production`, and `.env.production.bak.local-model-test` are not staged.

Commit:

```powershell
git commit -m "feat: add image generation mode switch"
```
