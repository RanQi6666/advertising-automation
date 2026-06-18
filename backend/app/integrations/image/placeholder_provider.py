from uuid import uuid4

from backend.app.schemas.ai import GeneratedImage, ImageBrief


class PlaceholderImageProvider:
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        images: list[GeneratedImage] = []
        for brief in briefs:
            images.append(
                GeneratedImage(
                    prompt=f"{brief.title}: {brief.visual_direction}. Text: {brief.short_text}",
                    storage_key=f"placeholder://creative/{uuid4()}",
                    alt_text=brief.short_text,
                    size=brief.size,
                    metadata={"image_index": brief.image_index, "provider": "placeholder"},
                )
            )
        return images
