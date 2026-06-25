# Model Gateway Provider Design

## Background

The project currently supports text generation through `mock`, `openai`, and
`volcengine`, while image generation supports `placeholder` and `volcengine`.
Production has been using Volcengine Ark / Doubao-compatible settings for both
text and image generation.

The new requirement is to route model calls through a locally deployed model
gateway. The gateway supports text output and image generation. The referenced
session is not available inside this workspace, so this design treats the
gateway as an OpenAI-compatible relay: chat completions for text and images
generation for images.

## Goals

1. Allow text generation to use the model gateway without changing business
   services.
2. Allow image generation to use the same gateway without going through
   Volcengine-specific configuration.
3. Keep existing `volcengine` and `openai` options working so rollback is only
   an environment-variable change.
4. Support both image URL responses and base64 image responses from the gateway.
5. Document the exact environment variables needed for local and production
   deployment.

## Non-Goals

Video generation is not moved in this change. The current gateway requirement
only confirms text and image support. Video can stay on `volcengine` or
`placeholder` until the gateway exposes a compatible video API.

This change does not alter work-order creation, final review package delivery,
external callback behavior, or ad-performance analysis payloads.

## Configuration

Add a first-class `gateway` option:

```env
LLM_PROVIDER=gateway
IMAGE_PROVIDER=gateway
MODEL_GATEWAY_BASE_URL=http://127.0.0.1:3000/v1
MODEL_GATEWAY_API_KEY=your-gateway-key
MODEL_GATEWAY_TEXT_MODEL=your-text-model
MODEL_GATEWAY_IMAGE_MODEL=your-image-model
MODEL_GATEWAY_IMAGE_SIZE=1024x1024
MODEL_GATEWAY_IMAGE_RESPONSE_FORMAT=
```

`MODEL_GATEWAY_IMAGE_RESPONSE_FORMAT` is optional. When empty, the request does
not send `response_format`. If a gateway requires it, set it to `url` or
`b64_json`.

## Architecture

Text generation reuses `OpenAILLMProvider`, because that provider already uses
OpenAI-compatible chat completions.

Image generation gets a new `GatewayImageProvider`. It uses OpenAI-compatible
`client.images.generate` and normalizes the result into the existing
`GeneratedImage` schema:

- If the gateway returns `url`, existing `CreativeService` downloads and stores
  the image through `ImageStorageService`.
- If the gateway returns `b64_json`, the provider writes the image under local
  storage and returns a `local://...` storage key.

## Acceptance Criteria

1. `LLM_PROVIDER=gateway` initializes an OpenAI-compatible LLM provider using
   `MODEL_GATEWAY_*` settings.
2. `IMAGE_PROVIDER=gateway` initializes an OpenAI-compatible image provider.
3. Gateway image provider passes model, prompt, size, and optional response
   format to the image API.
4. Gateway image provider supports URL and base64 image responses.
5. Existing `volcengine`, `openai`, `mock`, and `placeholder` paths continue to
   pass tests.
