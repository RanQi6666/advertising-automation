from backend.app.services.brand_safety_policy import (
    BRAND_SAFETY_PROMPT_GUARDRAILS,
    scan_brand_safety,
)


def test_brand_safety_scanner_blocks_price_promotion_terms() -> None:
    report = scan_brand_safety(
        {
            "creative_payload": {
                "message": "Get this low price deal today",
                "ads_name": "限时优惠",
                "description": "免费体验，主打高性价比。",
            }
        }
    )

    assert report["status"] == "blocked"
    assert report["highest_severity"] == "high"
    assert {item["category"] for item in report["findings"]} >= {"price_promotion"}


def test_brand_safety_scanner_blocks_gambling_medicine_and_money_terms() -> None:
    report = scan_brand_safety(
        {
            "topic": "casino betting angle",
            "image_prompt": "show pills, cash, chips, and a bank card",
        }
    )

    assert report["status"] == "blocked"
    assert {item["category"] for item in report["findings"]} >= {
        "gambling",
        "medicine",
        "money",
    }


def test_brand_safety_scanner_blocks_chinese_only_terms() -> None:
    report = scan_brand_safety(
        {
            "copy": "不要出现优惠、低价、赌场、药片、现金这类内容。",
        }
    )

    assert report["status"] == "blocked"
    assert {item["category"] for item in report["findings"]} >= {
        "gambling",
        "medicine",
        "money",
        "price_promotion",
    }


def test_brand_safety_scanner_allows_neutral_value_language() -> None:
    report = scan_brand_safety(
        {
            "creative_payload": {
                "message": "A smooth daily experience with clear setup steps.",
                "ads_name": "Start with a simple guide",
                "description": "Learn more about daily use.",
            },
            "assets": {
                "images": [
                    {
                        "url": "https://cdn.example/assets/free-safe-path/image.png",
                        "prompt": "Clean product scene with simple onboarding visuals.",
                    }
                ]
            },
        }
    )

    assert report["status"] == "passed"
    assert report["highest_severity"] is None
    assert report["findings"] == []


def test_brand_safety_prompt_guardrails_name_the_hard_bans() -> None:
    assert "gambling" in BRAND_SAFETY_PROMPT_GUARDRAILS
    assert "medicine" in BRAND_SAFETY_PROMPT_GUARDRAILS
    assert "discount" in BRAND_SAFETY_PROMPT_GUARDRAILS
    assert "low price" in BRAND_SAFETY_PROMPT_GUARDRAILS
