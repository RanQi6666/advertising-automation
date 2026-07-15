# Ad Analysis Media Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make external ad-analysis media downloads resilient to temporary network failures and require usable first and last video frames before full visual analysis.

**Architecture:** Keep SSRF validation, file limits, and the external async job contract intact. Add a bounded retry wrapper around the existing safe downloader, with analysis-specific image/video time budgets. Replace the fixed video frame schedule with mandatory boundary frames plus bounded, difference-filtered middle frames.

**Tech Stack:** Python 3.13, asyncio, httpx, Pillow, FFmpeg/ffprobe, pytest, Docker Compose/Celery.

## Global Constraints

- Image download attempt timeout is 30 seconds; video download attempt timeout is 80 seconds.
- At most one retry after a one-second delay, only for temporary transfer errors and 408/429/5xx responses.
- Preserve public HTTP/HTTPS validation, DNS SSRF checks, redirect revalidation, 100 MB video cap, 20-second video cap, and MP4/MOV/WebM verification.
- Video visual completeness requires both `first_frame.jpg` and `last_frame.jpg`; middle frames are optional and capped at three.
- Media failure must not change a completed ad-analysis task into a failed task.

---

### Task 1: Add bounded retry configuration and safe downloader behavior

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `.env.example`
- Modify: `.env.production.example`
- Modify: `backend/app/services/safe_public_http.py`
- Test: `tests/test_safe_public_http.py`

**Interfaces:**
- Produces `download_public_http_file(..., retry_attempts: int = 0, retry_delay_seconds: float = 1.0)`.
- Produces `is_retryable_media_download_error(error: BaseException) -> bool`.
- Produces settings for image timeout, video timeout, retry count, and retry delay.

- [ ] **Step 1: Write failing retry tests**

```python
async def test_download_retries_a_transient_timeout_once(monkeypatch, tmp_path):
    attempts = 0
    async def fake_download_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SafePublicHTTPError("media download timed out after 30 seconds")
        return expected_download
    monkeypatch.setattr(safe_public_http, "_download_public_http_file", fake_download_once)
    result = await download_public_http_file(url, destination, max_bytes=1024, retry_attempts=1)
    assert result == expected_download
    assert attempts == 2
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_safe_public_http.py -q`

Expected: the retry test fails because `retry_attempts` is not accepted or no retry occurs.

- [ ] **Step 3: Implement retry wrapper**

Wrap each fresh `_download_public_http_file` attempt in the existing timeout/cleanup behavior. Retry only after `is_retryable_media_download_error`; delete partial and destination files before retry; sleep one second; preserve the final exception when attempts are exhausted.

- [ ] **Step 4: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_safe_public_http.py -q`

Expected: all safe downloader tests pass.

### Task 2: Apply analysis media budgets and persist download diagnostics

**Files:**
- Modify: `backend/app/services/ad_analysis_media_service.py`
- Test: `tests/test_ad_analysis_media_service.py`

**Interfaces:**
- Produces `download` summary entries containing `attempts` and `retry_used`.
- Uses 30-second image and 80-second video attempt timeouts with one retry.

- [ ] **Step 1: Write failing service tests**

```python
def test_video_media_uses_80_second_timeout_and_one_retry(monkeypatch, tmp_path):
    captured = {}
    async def fake_download(url, destination, **kwargs):
        captured.update(kwargs)
        return valid_video_download
    monkeypatch.setattr(media_module, "download_public_http_file", fake_download)
    asyncio.run(AdAnalysisMediaService().process_media(video_payload, analysis_id="ana-video"))
    assert captured["timeout_seconds"] == 80
    assert captured["retry_attempts"] == 1
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_media_service.py -q`

Expected: test fails because the service still passes one shared 20-second timeout and no retry arguments.

- [ ] **Step 3: Implement minimal service wiring**

Add settings defaults and pass the appropriate media type timeout plus retry options to all downloader calls. Copy returned attempt metadata into public-safe media summary fields.

- [ ] **Step 4: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_media_service.py -q`

Expected: all media-service tests pass.

### Task 3: Require boundary frames and select dynamic middle frames

**Files:**
- Modify: `backend/app/services/ad_analysis_media_service.py`
- Modify: `backend/app/integrations/llm/openai_provider.py`
- Modify: `backend/app/services/facebook_ad_analysis_assembler.py`
- Test: `tests/test_ad_analysis_media_service.py`
- Test: `tests/test_facebook_ad_analysis_assembler.py`

**Interfaces:**
- Produces video `local_artifacts.first_frame_path`, `last_frame_path`, and ordered `keyframe_paths`.
- Produces summary fields `first_frame_generated`, `last_frame_generated`, `middle_frame_count`, `frame_count`, and `video_visual_complete`.

- [ ] **Step 1: Write failing boundary-frame tests**

```python
def test_video_media_requires_first_and_last_frame_for_complete_visual_status(monkeypatch, tmp_path):
    monkeypatch.setattr(media_module, "_extract_video_keyframes", fake_only_first_frame)
    result = asyncio.run(AdAnalysisMediaService().process_media(video_payload, analysis_id="ana-video"))
    assert result.summary["first_frame_generated"] is True
    assert result.summary["last_frame_generated"] is False
    assert result.summary["video_visual_complete"] is False
    assert result.summary["status"] == "partial"
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_media_service.py -q`

Expected: test fails because current code only reports a generic list of fixed-time keyframes.

- [ ] **Step 3: Implement boundary extraction and dynamic selection**

Extract first frame near `0.1s`, last frame near `duration - 0.1s`, and candidates across the remaining interval. Compare small grayscale thumbnails with Pillow; retain at most three candidates with meaningful difference. Put first frame first and last frame last. Mark visual completion only when both boundary files exist.

- [ ] **Step 4: Update visual-input and final-result consumers**

Make the OpenAI provider read first, selected middle, then last local paths. Make the assembler treat video `partial` / `video_visual_complete=false` as an incomplete visual-analysis result with an explicit limitation.

- [ ] **Step 5: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ad_analysis_media_service.py tests/test_facebook_ad_analysis_assembler.py -q`

Expected: all targeted tests pass.

### Task 4: Document, build, and validate production-like runtime

**Files:**
- Modify: `docs/外部系统Facebook投放数据分析处理流程说明.md`
- Test: existing targeted tests and Docker runtime

- [ ] **Step 1: Update processing document**

Document that media download is asynchronous, temporarily retried once, and video analysis derives mandatory opening and ending frames plus adaptive middle frames internally. Do not require external callers to send thumbnails or keyframes.

- [ ] **Step 2: Run repository checks**

Run: `.venv\Scripts\python.exe -m pytest tests/test_safe_public_http.py tests/test_ad_analysis_media_service.py tests/test_facebook_ad_analysis_assembler.py -q`

Run: `.venv\Scripts\python.exe -m ruff check backend tests`

Expected: all pass.

- [ ] **Step 3: Rebuild runtime services**

Run:

```powershell
docker compose -f docker-compose.prod.yml --env-file .env.production build backend worker_ad_analysis
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --no-deps --force-recreate backend worker_ad_analysis web
```

- [ ] **Step 4: Execute real MP4 async regression**

POST a new request using `https://auto.ggcss.xyz/files/storage/facebook/20260715/ai_6a5732e1a10829.98224884.mp4`, poll to terminal state, and verify `succeeded`, `first_frame_generated=true`, `last_frame_generated=true`, `video_visual_complete=true`, and no media downgrade.
