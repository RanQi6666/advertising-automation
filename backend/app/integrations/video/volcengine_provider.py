from urllib.parse import quote

import httpx

from backend.app.core.errors import ProviderError
from backend.app.integrations.video.base import (
    VideoGenerationRequest,
    VideoGenerationStart,
    VideoGenerationStatus,
    VideoSourceImage,
)


class VolcengineVideoProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        resolution: str,
        image_mode: str,
        min_duration_seconds: int,
        max_duration_seconds: int,
        max_reference_images: int,
        generate_audio: bool,
        watermark: bool,
        return_last_frame: bool,
        execution_expires_after: int,
        priority: int,
        safety_identifier: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.resolution = resolution
        self.image_mode = image_mode
        self.min_duration_seconds = min_duration_seconds
        self.max_duration_seconds = max_duration_seconds
        self.max_reference_images = max_reference_images
        self.generate_audio = generate_audio
        self.watermark = watermark
        self.return_last_frame = return_last_frame
        self.execution_expires_after = execution_expires_after
        self.priority = priority
        self.safety_identifier = safety_identifier
        self.timeout_seconds = timeout_seconds
        self.tasks_url = f"{base_url.rstrip('/')}/contents/generations/tasks"

    async def start_generation(self, request: VideoGenerationRequest) -> VideoGenerationStart:
        payload = self._build_payload(request)
        response = await self._request("POST", self.tasks_url, json=payload)
        provider_job_id = response.get("id")
        if not provider_job_id:
            raise ProviderError("Volcengine video API returned no task id.")
        return VideoGenerationStart(
            provider_job_id=str(provider_job_id),
            provider_status=str(response.get("status") or "queued"),
            raw_response=response,
            request_payload=payload,
        )

    async def get_generation_status(self, provider_job_id: str) -> VideoGenerationStatus:
        task_url = f"{self.tasks_url}/{quote(provider_job_id, safe='')}"
        response = await self._request("GET", task_url)
        task = _unwrap_task_response(response, provider_job_id)
        content = task.get("content") if isinstance(task.get("content"), dict) else {}
        return VideoGenerationStatus(
            provider_job_id=str(task.get("id") or provider_job_id),
            provider_status=str(task.get("status") or "unknown"),
            video_url=_string_or_none(content.get("video_url") or task.get("video_url")),
            last_frame_url=_string_or_none(
                content.get("last_frame_url") or task.get("last_frame_url")
            ),
            error_message=_error_message(task.get("error")),
            raw_response=response,
        )

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        if not request.prompt.strip():
            raise ProviderError("Video prompt is required for Volcengine Seedance generation.")
        if not self.min_duration_seconds <= request.duration_seconds <= self.max_duration_seconds:
            raise ProviderError(
                "Volcengine Seedance supports duration between "
                f"{self.min_duration_seconds} and {self.max_duration_seconds} seconds."
            )
        if len(request.source_images) > self.max_reference_images:
            raise ProviderError(
                "Volcengine Seedance supports at most "
                f"{self.max_reference_images} first/last frame images for this endpoint."
            )
        if self.image_mode == "first_last_frame" and len(request.source_images) > 2:
            raise ProviderError("Seedance first/last frame mode supports at most 2 images.")

        content: list[dict] = [{"type": "text", "text": request.prompt}]
        content.extend(self._image_content_items(request.source_images))

        payload: dict = {
            "model": self.model,
            "content": content,
            "resolution": self.resolution,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "generate_audio": self.generate_audio,
            "watermark": self.watermark,
            "return_last_frame": self.return_last_frame,
            "execution_expires_after": self.execution_expires_after,
            "priority": self.priority,
        }
        if self.safety_identifier:
            payload["safety_identifier"] = self.safety_identifier
        return payload

    def _image_content_items(self, images: list[VideoSourceImage]) -> list[dict]:
        if self.image_mode == "first_last_frame":
            roles = ["first_frame", "last_frame"]
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": image.url},
                    "role": roles[index],
                }
                for index, image in enumerate(images)
            ]
        return [
            {
                "type": "image_url",
                "image_url": {"url": image.url},
                "role": image.role,
            }
            for image in images
        ]

    async def _request(self, method: str, url: str, **kwargs: object) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            message = str(exc) or exc.__class__.__name__
            raise ProviderError(f"Volcengine video API request failed: {message}") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"Volcengine video API returned {response.status_code}: {_response_text(response)}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Volcengine video API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ProviderError("Volcengine video API returned an unexpected response shape.")
        return data


def _error_message(error: object) -> str | None:
    if not error:
        return None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if code and message:
            return f"{code}: {message}"
        if message:
            return str(message)
        if code:
            return str(code)
    return str(error)


def _unwrap_task_response(response: dict, provider_job_id: str) -> dict:
    items = response.get("items")
    if items is not None:
        if not isinstance(items, list):
            raise ProviderError("Volcengine video API returned invalid task items.")
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "") == provider_job_id:
                return item
        raise ProviderError(f"Volcengine video task not found: {provider_job_id}")
    response_id = response.get("id")
    if response_id is not None and str(response_id) != provider_job_id:
        raise ProviderError(f"Volcengine video API returned mismatched task id: {response_id}")
    return response


def _response_text(response: httpx.Response) -> str:
    try:
        return response.text
    except Exception:
        return "<unreadable response>"


def _string_or_none(value: object) -> str | None:
    return str(value) if value else None
