import re

from openai import AsyncOpenAI

from backend.app.core.errors import ProviderError
from backend.app.schemas.ai import GeneratedImage, ImageBrief
from backend.app.services.brand_safety_policy import BRAND_SAFETY_VISUAL_BAN

_PLATFORM_BRAND_PATTERN = re.compile(r"\b(Facebook|Meta|Instagram)\b", re.IGNORECASE)
_PLATFORM_LABEL_PATTERN = re.compile(r"\bSponsored(?:\s+labels?)?\b", re.IGNORECASE)


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
    visual_direction = _platform_neutral_text(brief.visual_direction)
    prompt = (
        "请生成一张独立的移动端信息流广告素材图片。\n"
        f"图片序号：{brief.image_index}\n"
        f"主题：{brief.title}\n"
        f"画面文字：{brief.short_text}\n"
        f"画面方向：{visual_direction}\n"
        f"广告比例要求：{brief.size}\n"
        "核心要求：画面清晰，主体明确，产品或使用场景突出，商业质感强，构图适合移动端信息流。\n"
        "文字要求：只使用上方“画面文字”字段提供的文字；如果该字段为空，不要额外生成文字。"
        "文字需简洁、易读、不能遮挡主体。\n"
        "禁止元素：任何社交平台品牌标识、平台 Logo、应用界面、信息流页面截图、"
        "赞助/广告标签、点赞/评论/分享按钮、浏览器边框、手机系统截图、二维码、水印、版权标识。"
    )
    return f"{prompt}\n{BRAND_SAFETY_VISUAL_BAN}"


def _platform_neutral_text(value: str) -> str:
    text = _PLATFORM_BRAND_PATTERN.sub("mobile feed", value)
    return _PLATFORM_LABEL_PATTERN.sub("ad labels", text).strip()
