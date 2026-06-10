from typing import Protocol

from backend.app.schemas.ai import GeneratedImage, ImageBrief


class ImageProvider(Protocol):
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        """Generate images from creative briefs."""
