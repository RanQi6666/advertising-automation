import re

from openai import AsyncOpenAI

from backend.app.core.errors import ProviderError
from backend.app.schemas.ai import GeneratedImage, ImageBrief
from backend.app.services.creative_safety_prompts import (
    creative_safety_prompt_block,
    sanitize_creative_safety_text,
)

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
            prompt = brief.raw_prompt or _prompt_from_brief(brief)
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
    title = sanitize_creative_safety_text(brief.title)
    short_text = sanitize_creative_safety_text(brief.short_text)
    visual_direction = sanitize_creative_safety_text(
        _platform_neutral_text(brief.visual_direction)
    )
    prompt = (
        "请生成一张独立的移动端信息流广告素材图片。\n"
        f"图片序号：{brief.image_index}\n"
        f"主题：{title}\n"
        f"画面文字：{short_text}\n"
        f"画面方向：{visual_direction}\n"
        f"广告比例要求：{brief.size}\n"
        "Full-frame composition rule: the main subject, hero/boss, logo, VIP mark, CTA, "
        "and all visible text must appear complete inside the central safe area and "
        "must not be cropped. Keep clear margins on every edge.\n"
        "Brand/VIP layout rule: when a brand lockup is requested, place VIP directly "
        "under the cleaned brand name or logo, fully visible, centered or upper-middle, "
        "with enough padding. Do not place brand text, VIP, or CTA at the bottom edge.\n"
        "核心要求：画面清晰，主体明确，产品或使用场景突出，商业质感强，构图适合移动端信息流。\n"
        "文字要求：只使用上方“画面文字”字段提供的文字；如果该字段为空，不要额外生成文字。"
        "文字需简洁、易读、不能遮挡主体。\n"
        "禁止元素：任何社交平台品牌标识、平台 Logo、应用界面、信息流页面截图、"
        "赞助/广告标签、点赞/评论/分享按钮、浏览器边框、手机系统截图、二维码、水印、版权标识。"
    )
    return f"{prompt}\n{creative_safety_prompt_block()}"


def _platform_neutral_text(value: str) -> str:
    text = _PLATFORM_BRAND_PATTERN.sub("mobile feed", value)
    return _PLATFORM_LABEL_PATTERN.sub("ad labels", text).strip()
