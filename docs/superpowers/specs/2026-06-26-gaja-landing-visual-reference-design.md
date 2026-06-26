# GAJA777 Landing Visual Reference Design

## Background

The GAJA777 work order currently produces visuals that feel too childish. The main reason is that the existing GAJA strategy explicitly asks for casual mini-game elements such as puzzle, runner, bubble, tile, and quick-tap scenes. That was review-safe, but it does not match the landing page screenshots the operator provided.

The target direction is a mature, premium, high-energy mobile game lobby: dark navy or black UI, neon edge light, metallic 3D title treatment, glossy game cards, fantasy character cards, fire and ice energy, cinematic transitions, and a clear Register / Play Now ending. The final output should be useful for Meta ad delivery, so the visual direction must avoid gambling, casino, slot, coin, money, jackpot, recharge, and winning claims. The work order event "first recharge" is treated as internal optimization context, not visible ad copy.

## Goals

1. Use landing page visual references to steer GAJA777 image and video generation.
2. Replace the childish GAJA defaults with a premium neon game-lobby strategy.
3. Keep the 12-second video structure conversion-oriented: strong first frame, fast game-card rhythm, lobby reveal, and registration CTA.
4. Preserve brand-safety guardrails so generated materials remain suitable for broad ad review.
5. Store visual reference data in existing JSON fields, avoiding a database migration for the first version.
6. Add deterministic fallback behavior so failed screenshot or vision analysis does not return to the current casual puzzle style.

## Non-Goals

The first version will not implement a full visual plagiarism detector. It should instruct the model to use original game-card archetypes inspired by the landing page style and avoid copying exact third-party game names, character identities, logos, or IP.

The first version will not make "first recharge" visible in images or videos. It remains a campaign/event signal only.

The first version will not require a schema migration. If future analysis needs indexed visual metadata, that can be added later.

## Recommended Approach

Use a hybrid landing-visual-reference pipeline:

1. Accept or capture landing page visual references when available.
2. Analyze those references into a structured `visual_reference` object.
3. Store the object on the latest `LandingPageSnapshot` under `extracted_data.visual_reference`.
4. Copy the same object into campaign metadata through the existing `landing_page` context.
5. Merge the visual reference into `creative_strategy.landing_visual_reference`.
6. Pass the merged strategy into image briefs, first/last-frame variants, video storyboard generation, and final video prompts.
7. Fall back to a deterministic GAJA premium game-lobby strategy if reference analysis is unavailable.

This is stronger than prompt-only tuning because the generation chain receives concrete visual attributes instead of vague words like "cool" or "high-tech".

## Visual Reference Shape

The structured object should be compact and prompt-ready:

```json
{
  "source": "reference_image | auto_capture | domain_fallback",
  "status": "analyzed | fallback | failed",
  "confidence": 0.85,
  "palette": [
    "near-black navy background",
    "electric cyan edge light",
    "magenta and violet glow",
    "orange CTA accent",
    "gold metallic title highlight"
  ],
  "surface_style": [
    "dark premium mobile game lobby",
    "glossy rectangular game cards",
    "metallic 3D title text",
    "cinematic neon rim light",
    "high contrast card carousel"
  ],
  "original_game_card_archetypes": [
    "mythic fire warrior card",
    "ice energy hero card",
    "flame fortress adventure card",
    "glossy jewel and fruit matching card",
    "777-inspired neon reel motif without slot-machine framing"
  ],
  "composition_cues": [
    "GAJA777 identity visible in the first frame",
    "phone-screen vertical lobby composition",
    "multiple premium cards angled in depth",
    "clear orange Register or Play Now CTA in final frame"
  ],
  "negative_cues": [
    "childlike puzzle blocks",
    "bubble-pop toys",
    "flat cartoon preschool style",
    "plain runner-game track",
    "generic Tetris blocks",
    "cash, coins, chips, casino floor, slot machine, jackpot text"
  ],
  "video_recipe": {
    "duration_seconds": 12,
    "beats": [
      "0-2s: dark neon GAJA777 lobby hook with premium cards",
      "2-7s: fast carousel through original fantasy and jewel game cards",
      "7-10s: coherent app lobby reveal matching landing page style",
      "10-12s: Register / Play Now end card"
    ]
  }
}
```

The "777-inspired" motif is allowed only as abstract brand-adjacent geometry or glowing numerals. It must not become a slot machine, jackpot, casino, chips, coins, or money scene.

## Components

### Landing Visual Reference Service

Add a small backend service responsible for building the `visual_reference` object. It should support three sources:

1. Reference images provided through `LandingPageAnalyzeRequest.metadata_json.reference_images`.
2. Optional automatic screenshot capture behind a config flag, if browser capture is available in the runtime.
3. A deterministic GAJA domain fallback when the landing URL matches `gaja777.game`.

The service should be isolated so the first version can ship without making the landing page fetcher depend directly on a browser runtime.

### Landing Page Service Integration

Extend `LandingPageService._fetch_snapshot()` after HTML parsing:

1. Build the normal `extracted_data` from HTML as it does today.
2. Ask the visual reference service for a visual reference using URL, title, text excerpt, and optional reference images.
3. Store the result at `extracted_data["visual_reference"]` when available.
4. Continue storing the snapshot even if visual analysis fails.

`snapshot_to_context()` already includes `extracted_data`, so video context can receive the visual reference without a schema change.

### GAJA Creative Strategy

Update `backend/app/services/game_creative_strategy.py` so GAJA no longer defaults to casual puzzle/bubble/runner visuals.

The GAJA strategy should become:

- `template_id`: keep `gaja_brand` unless a new ID is needed for clarity.
- `brand.palette`: dark navy, electric cyan, magenta/violet glow, orange CTA, metallic gold.
- `first_frame.visual_must_include`: GAJA777 wordmark, dark neon game lobby, premium game cards, cinematic depth, high-energy hook.
- `last_frame.visual_must_include`: GAJA777 lobby, Register / Play Now CTA, orange button, clean phone registration cue.
- `motion_direction`: 12-second premium card-carousel flow.
- `compliance_guardrails`: original game-card visuals only; no gambling, casino, slot, coins, cash, chip, money, winning, or recharge claims.
- `negative_cues`: no childlike puzzle, bubble-pop toy, block game, preschool cartoon, simple runner track.

If `landing_page.extracted_data.visual_reference` exists, merge it into `creative_strategy["landing_visual_reference"]`.

### Image Brief Generation

Extend the LLM compacting logic so `landing_visual_reference`, `negative_cues`, and `video_recipe` survive `_compact_creative_strategy()`.

Image briefs should be instructed to:

1. Use the visual reference as mandatory art direction.
2. Produce coherent first-frame and last-frame pairs for the same 12-second video idea.
3. Avoid childish mini-game motifs.
4. Keep visible text in the target audience language, using short safe CTAs such as Register or Play Now.

### Video Storyboard and Prompt Generation

Extend `_creative_strategy_prompt_block()` so final video prompts include:

1. Landing visual reference summary.
2. 12-second beat structure.
3. Mature premium lobby style.
4. Negative cues.
5. Brand-safety constraints.

The video storyboard should consistently follow:

| Time | Creative job |
| --- | --- |
| 0-2s | Strong hook: GAJA777 dark neon lobby with premium game cards |
| 2-7s | Fast carousel: original fantasy, jewel, fire, ice, and adventure card archetypes |
| 7-10s | Lobby reveal: coherent game hub matching the landing page visual system |
| 10-12s | Conversion end card: Register / Play Now, orange CTA, no risky claims |

## Error Handling

Visual reference analysis should fail soft:

- If reference images are missing, use the GAJA domain fallback.
- If reference images are invalid or inaccessible, store `status = "fallback"` with an error note.
- If the LLM vision call fails, store the snapshot normally and use the deterministic GAJA fallback.
- If the URL is not GAJA and no references exist, leave `visual_reference` empty and keep the existing generic flow.

Brand-safety remains strict. Generated prompts must not include gambling, casino, slot, coin, money, jackpot, recharge, profit, income, or winning claims.

## Testing Strategy

Backend unit tests:

1. GAJA strategy no longer contains puzzle, bubble, quick-tap, or runner as required visual directions.
2. GAJA strategy contains premium neon game-lobby cues and negative childish cues.
3. Landing visual reference service returns the GAJA fallback for `gaja777.game`.
4. Landing page snapshots store `extracted_data.visual_reference` when reference metadata is provided.
5. `_compact_creative_strategy()` preserves `landing_visual_reference`, `negative_cues`, and `video_recipe`.
6. Image brief prompts include landing visual cues and negative cues.
7. Video storyboard prompts include the 12-second conversion beat structure.
8. Brand-safety tests confirm risky visible words and props remain banned.

Manual verification:

1. Create a GAJA777 work order with country India, media fb, event first recharge, audience 18-65, and the GAJA landing URL.
2. Run landing page analysis with the provided visual reference screenshots or the domain fallback.
3. Generate first/last-frame image variants.
4. Confirm the images feel like a premium neon game lobby, not a children casual game.
5. Generate a 12-second video storyboard and final prompt.
6. Confirm the final CTA is registration-oriented and does not mention recharge, money, winning, jackpot, casino, or slot-machine framing.

## Acceptance Criteria

The change is successful when a GAJA777 work order produces image and video prompts that clearly describe:

- dark neon GAJA777 game lobby,
- metallic/premium game-card design,
- fantasy and glossy card carousel energy,
- coherent 12-second ad flow,
- Register / Play Now ending,
- no childish puzzle/bubble/block visuals,
- no money, gambling, casino, slot, jackpot, recharge, or winning claims.

The implementation should be covered by targeted tests and should not require a database migration.
