from backend.app.services.landing_visual_reference import (
    build_landing_visual_reference,
    extract_landing_visual_reference,
    merge_landing_visual_reference,
)

GAJA_URL = "https://www.gaja777.game/#/?invite=YBG71118&register=true"


def test_gaja_domain_returns_no_builtin_visual_reference() -> None:
    assert build_landing_visual_reference(GAJA_URL) is None


def test_lookalike_host_does_not_match_gaja_domain() -> None:
    assert build_landing_visual_reference("https://badgaja777.game") is None


def test_gaja_host_with_port_returns_no_builtin_visual_reference() -> None:
    assert (
        build_landing_visual_reference(
            "https://www.gaja777.game:443/#/?invite=YBG71118&register=true"
        )
        is None
    )


def test_reference_images_do_not_create_builtin_gaja_reference() -> None:
    reference = build_landing_visual_reference(
        GAJA_URL,
        metadata={
            "reference_images": [
                "C:/Users/panda/AppData/Local/Temp/codex-clipboard-e7477206.png"
            ]
        },
    )

    assert reference is None


def test_non_gaja_without_reference_images_returns_none() -> None:
    assert build_landing_visual_reference("https://example.com/app") is None


def test_non_gaja_reference_images_do_not_return_gaja_reference() -> None:
    reference = build_landing_visual_reference(
        "https://example.com/app",
        metadata={"reference_images": ["C:/tmp/example.png"]},
    )

    assert reference is None


def test_extract_landing_visual_reference_accepts_snapshot_context_shapes() -> None:
    reference = {"source": "manual_reference", "surface_style": ["operator supplied"]}
    context = {"extracted_data": {"visual_reference": reference}}

    assert extract_landing_visual_reference(context) == reference
    assert extract_landing_visual_reference({"visual_reference": reference}) == reference
    assert extract_landing_visual_reference(None) is None


def test_merge_landing_visual_reference_copies_strategy() -> None:
    reference = {"source": "manual_reference", "surface_style": ["operator supplied"]}
    strategy = {"template_id": "gaja_brand", "brand": {"display_name": "GAJA777"}}

    merged = merge_landing_visual_reference(
        strategy,
        {"extracted_data": {"visual_reference": reference}},
    )

    assert merged is not strategy
    assert merged["landing_visual_reference"] == reference
    assert strategy.get("landing_visual_reference") is None
