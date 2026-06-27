from backend.app.services.landing_visual_reference import (
    build_landing_visual_reference,
    extract_landing_visual_reference,
    merge_landing_visual_reference,
)

GAJA_URL = "https://www.gaja777.game/#/?invite=YBG71118&register=true"


def test_gaja_domain_returns_premium_visual_fallback() -> None:
    reference = build_landing_visual_reference(GAJA_URL)

    assert reference is not None
    assert reference["source"] == "domain_fallback"
    assert reference["status"] == "fallback"
    assert "near-black navy background" in reference["palette"]
    assert "dark premium mobile game lobby" in reference["surface_style"]
    assert "mythic fire warrior card" in reference["original_game_card_archetypes"]
    assert "restricted_review_props" in reference["negative_style_cues"]
    assert "financial_prop_cues" in reference["negative_style_cues"]
    assert "outcome_claim_cues" in reference["negative_style_cues"]
    assert reference["video_recipe"]["duration_seconds"] == 12
    assert reference["video_recipe"]["beats"][0].startswith("0-2s")


def test_lookalike_host_does_not_match_gaja_domain() -> None:
    assert build_landing_visual_reference("https://badgaja777.game") is None


def test_gaja_host_with_port_still_matches() -> None:
    reference = build_landing_visual_reference(
        "https://www.gaja777.game:443/#/?invite=YBG71118&register=true"
    )

    assert reference is not None
    assert reference["source"] == "domain_fallback"


def test_reference_images_mark_source_without_network_fetch() -> None:
    reference = build_landing_visual_reference(
        GAJA_URL,
        metadata={
            "reference_images": [
                "C:/Users/panda/AppData/Local/Temp/codex-clipboard-e7477206.png"
            ]
        },
    )

    assert reference is not None
    assert reference["source"] == "reference_image"
    assert reference["status"] == "analyzed"
    assert reference["reference_image_count"] == 1
    assert reference["analysis_note"] == (
        "Using operator-provided landing page screenshots as visual style anchors."
    )


def test_non_gaja_without_reference_images_returns_none() -> None:
    assert build_landing_visual_reference("https://example.com/app") is None


def test_non_gaja_reference_images_do_not_return_gaja_reference() -> None:
    reference = build_landing_visual_reference(
        "https://example.com/app",
        metadata={"reference_images": ["C:/tmp/example.png"]},
    )

    assert reference is None


def test_extract_landing_visual_reference_accepts_snapshot_context_shapes() -> None:
    reference = build_landing_visual_reference(GAJA_URL)
    context = {"extracted_data": {"visual_reference": reference}}

    assert extract_landing_visual_reference(context) == reference
    assert extract_landing_visual_reference({"visual_reference": reference}) == reference
    assert extract_landing_visual_reference(None) is None


def test_merge_landing_visual_reference_copies_strategy() -> None:
    reference = build_landing_visual_reference(GAJA_URL)
    strategy = {"template_id": "gaja_brand", "brand": {"display_name": "GAJA777"}}

    merged = merge_landing_visual_reference(
        strategy,
        {"extracted_data": {"visual_reference": reference}},
    )

    assert merged is not strategy
    assert merged["landing_visual_reference"] == reference
    assert strategy.get("landing_visual_reference") is None
