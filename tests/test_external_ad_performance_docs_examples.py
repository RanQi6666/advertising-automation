from __future__ import annotations

import json
from pathlib import Path

from backend.app.schemas.external_ad_performance_analysis import ExternalAdPerformanceAnalysisCreate

_SAMPLE_FILENAMES = [
    "外部系统投放数据分析请求示例.json",
    "投放分析_图片广告测试包.json",
    "投放分析_视频广告测试包.json",
]


def test_external_ad_performance_sample_payloads_match_async_contract():
    for filename in _SAMPLE_FILENAMES:
        path = Path("docs") / filename
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload.get("external_request_id"), f"{filename} must include external_request_id"
        creative = payload.get("creative") or {}
        assert "thumbnail_url" not in creative, f"{filename} must not require caller thumbnail_url"
        assert "video_keyframes" not in creative, (
            f"{filename} must not require caller video_keyframes"
        )

        model = ExternalAdPerformanceAnalysisCreate.model_validate(payload)
        if creative.get("creative_type") == "image":
            assert model.creative.get("image_url", "").startswith(
                "https://newpixel.messrocts.com/uploads/"
            )
        if creative.get("creative_type") == "video":
            assert model.creative.get("video_url", "").startswith(
                "https://newpixel.messrocts.com/uploads/"
            )
