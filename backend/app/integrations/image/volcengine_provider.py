from openai import AsyncOpenAI

from backend.app.core.errors import ProviderError
from backend.app.schemas.ai import GeneratedImage, ImageBrief


class VolcengineImageProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider_size: str,
        watermark: bool,
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.provider_size = provider_size
        self.watermark = watermark

    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        images: list[GeneratedImage] = []
        for brief in briefs:
            prompt = _prompt_from_brief(brief)
            response = await self.client.images.generate(
                model=self.model,
                prompt=prompt,
                size=self.provider_size,
                response_format="url",
                extra_body={"watermark": self.watermark},
            )
            if not response.data or not response.data[0].url:
                raise ProviderError("Volcengine image API returned no image URL.")

            images.append(
                GeneratedImage(
                    prompt=prompt,
                    url=response.data[0].url,
                    storage_key=f"volcengine://{self.model}/{brief.image_index}",
                    alt_text=brief.short_text,
                    size=brief.size,
                    metadata={
                        "provider": "volcengine",
                        "model": self.model,
                        "provider_size": self.provider_size,
                        "watermark": self.watermark,
                        "image_index": brief.image_index,
                        "brief_title": brief.title,
                    },
                )
            )
        return images


def _prompt_from_brief(brief: ImageBrief) -> str:
    return (
        "请生成一张用于 Facebook 广告投放的图片。\n"
        f"图片序号：{brief.image_index}\n"
        f"主题：{brief.title}\n"
        f"画面文字：{brief.short_text}\n"
        f"画面方向：{brief.visual_direction}\n"
        f"广告比例要求：{brief.size}\n"
        "要求：画面清晰，广告感强，主体明确，文字简洁易读，适合移动端信息流。"
    )
