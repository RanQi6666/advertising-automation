# External Image Priority Fallback Design

## Goal

Route new external text-to-image jobs with this ordered policy:

1. JBB GPT Image and DM Fox GPT Image share normal traffic in strict 1:1 round-robin order.
2. CPA Gemini is the first fallback after a technical failure.
3. Volcengine is the final fallback after another technical failure.
4. Volcengine never receives normal traffic or traffic diverted merely because a preferred provider is queued.

## Scope

This policy applies to external text-to-image jobs at every supported `count`. It does not change the current behavior of:

- internal image-generation tasks;
- external edits, reference-image jobs, or revisions;
Multi-image jobs receive an initial JBB or DM Fox primary route like one-image jobs. They never advance to CPA Gemini or Volcengine after failure because their images are submitted concurrently to one upstream provider; a partial success followed by a different-provider retry could duplicate images and billing.

## Route Selection

Add a dedicated route mode, `priority_fallback`, independent of the existing `round_robin` mode.

For a new eligible job, use a Redis counter dedicated to the primary pair. The first route is selected from this stable list:

```text
jbb_gpt_image,dm_fox_gpt_image
```

The counter selects `(sequence - 1) % 2`, so JBB and DM Fox receive equal normal traffic. The task metadata records the selected provider, model, sequence, strategy, and fallback plan:

```text
jbb_gpt_image -> cpa_gemini -> volcengine
dm_fox_gpt_image -> cpa_gemini -> volcengine
```

The task continues to expose the same public create and poll API. The caller sees one `job_id` throughout the fallback chain.

## Failure Handling

On a failed eligible one-image task, the existing task retry path classifies the error before deciding whether to replace the stored route.

The route advances only for these technical errors:

- `provider_timeout`;
- `provider_429`;
- `unknown_provider_error`, including connection failures and upstream HTTP 5xx responses.

For a matching failure:

1. The current failed provider and error are appended to `metadata.image_route_history`.
2. The task's `metadata.image_route` is changed to the next provider in its fallback plan.
3. The existing delayed Celery retry schedules the same task ID.
4. The next attempt reads the replacement route and submits once to that provider.

After Volcengine fails, the task reaches its normal terminal failed state. The expected maximum attempt count is three: primary provider, CPA Gemini, and Volcengine.

These errors do not advance to another provider:

- validation and provider 400 errors;
- moderation or content-policy errors;
- unsupported model, edit, or reference-image capability errors;
- post-generation storage or external-image-download errors;
- task interruption or stale-task recovery errors.

Multi-image jobs do not advance providers for any error. They retain the selected JBB or DM Fox route so their existing retry behavior remains isolated to one upstream provider.

Post-generation download failures are specifically excluded because the upstream may already have generated an image. Re-submitting to another provider would duplicate output and billing.

## Timeout Tradeoff

The user explicitly accepts timeout fallback. A provider may finish generation after the client times out, so a timeout followed by CPA Gemini or Volcengine can produce an unobserved duplicate upstream image and duplicate billing. The application will keep only the successful image from the terminal attempt, but it cannot cancel or recover an upstream request whose response was lost.

## Configuration

The priority mode uses explicit settings rather than overloading the ordinary provider list:

```env
EXTERNAL_IMAGE_ROUTE_MODE=priority_fallback
EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS=jbb_gpt_image,dm_fox_gpt_image
EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS=cpa_gemini,volcengine
```

The mode validates that the primary list contains exactly JBB and DM Fox once each, and that the fallback list is exactly CPA Gemini followed by Volcengine. Existing `round_robin` deployments remain valid and unchanged.

## Observability

Persist non-sensitive routing state in `GenerationTask.metadata_json`:

```json
{
  "image_route": {
    "strategy": "priority_fallback",
    "sequence": 17,
    "provider": "cpa_gemini",
    "model": "gemini-3.1-flash-image"
  },
  "image_route_history": [
    {
      "attempt": 1,
      "provider": "jbb_gpt_image",
      "error_code": "provider_timeout",
      "next_provider": "cpa_gemini"
    }
  ]
}
```

Do not include API keys, prompts, response bodies, or signed image URLs in the route history.

## Tests

Add focused tests for:

1. strict JBB and DM Fox alternation for initial one-image and multi-image jobs;
2. JBB or DM Fox technical failure switching to CPA Gemini;
3. CPA Gemini technical failure switching to Volcengine;
4. Volcengine technical failure reaching a terminal failure;
5. timeout, 429, and unknown-provider errors advancing the plan;
6. validation, moderation, capability, and storage failures retaining the route;
7. `count > 1` jobs retaining their selected JBB or DM Fox route after failure;
8. edit, reference-image, and revision jobs retaining their existing route behavior;
9. existing `round_robin` selection remaining unchanged.

## Deployment and Validation

Deployment changes require the new route-mode environment variables and a rebuild of `backend` and `worker_image`. No database migration or data backfill is required because route history uses the existing JSON metadata column.

Runtime validation is a small authorized three-step failure simulation or provider mock test, followed by one controlled live request only if explicitly approved. The live request must retain its one `external_request_id`, one `job_id`, and poll that same job to a terminal state.
