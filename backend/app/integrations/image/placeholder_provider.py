from html import escape
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.schemas.ai import GeneratedImage, ImageBrief


class PlaceholderImageProvider:
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        images: list[GeneratedImage] = []
        storage_root = Path(get_settings().local_storage_root)
        placeholder_dir = storage_root / "images" / "placeholder"
        placeholder_dir.mkdir(parents=True, exist_ok=True)
        for brief in briefs:
            image_id = str(uuid4())
            relative_path = Path("images") / "placeholder" / f"{image_id}.svg"
            (storage_root / relative_path).write_text(_svg_for_brief(brief), encoding="utf-8")
            images.append(
                GeneratedImage(
                    prompt=f"{brief.title}: {brief.visual_direction}. Text: {brief.short_text}",
                    storage_key=f"local://{relative_path.as_posix()}",
                    alt_text=brief.short_text,
                    size=brief.size,
                    metadata={"image_index": brief.image_index, "provider": "placeholder"},
                )
            )
        return images


def _svg_for_brief(brief: ImageBrief) -> str:
    title = escape(brief.title or "Generated placeholder")
    direction = escape(brief.visual_direction or "Product visual")
    short_text = escape(brief.short_text or "Generated image")
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024"',
            '  viewBox="0 0 1024 1024">',
            '  <rect width="1024" height="1024" fill="#f4f7fb"/>',
            '  <rect x="96" y="96" width="832" height="832" rx="36"',
            '    fill="#ffffff" stroke="#2f6fed" stroke-width="8"/>',
            '  <circle cx="512" cy="396" r="148" fill="#dbeafe"/>',
            '  <path d="M288 720c96-128 184-192 264-192 72 0 132 48',
            '    184 144l72 48v96H216v-56z" fill="#93c5fd"/>',
            '  <text x="512" y="232" text-anchor="middle"',
            f'    font-family="Arial, sans-serif" font-size="48" fill="#172554">{title}</text>',
            '  <text x="512" y="820" text-anchor="middle"',
            (
                '    font-family="Arial, sans-serif" font-size="36" '
                f'fill="#1e3a8a">{short_text}</text>'
            ),
            '  <text x="512" y="876" text-anchor="middle"',
            (
                '    font-family="Arial, sans-serif" font-size="24" '
                f'fill="#475569">{direction}</text>'
            ),
            "</svg>",
            "",
        ]
    )
