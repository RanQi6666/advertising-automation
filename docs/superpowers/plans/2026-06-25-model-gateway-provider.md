# Model Gateway Provider Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Route text and image model calls through a configurable
OpenAI-compatible model gateway while preserving the current Volcengine paths.

## Tasks

- [x] Write the model gateway design spec.
- [x] Add `MODEL_GATEWAY_*` settings and `gateway` provider options.
- [x] Reuse `OpenAILLMProvider` when `LLM_PROVIDER=gateway`.
- [x] Add `GatewayImageProvider` for OpenAI-compatible image generation.
- [x] Update image factory to support `IMAGE_PROVIDER=gateway`.
- [x] Add unit tests for gateway text factory and image response handling.
- [x] Update `.env.example`, `.env.production.example`, and `README.md`.
- [x] Run backend verification.

## Verification

```powershell
.venv\Scripts\python.exe -m ruff check backend tests
.venv\Scripts\python.exe -m pytest tests/test_model_gateway_provider.py tests/test_image_prompt_guardrails.py -q
```
