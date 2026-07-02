import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pressure_test_generation_tasks.py"


def load_script_module():
    assert MODULE_PATH.exists(), "pressure script should exist"
    spec = importlib.util.spec_from_file_location("pressure_test_generation_tasks", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pressure_script_builds_safe_monitor_requests() -> None:
    module = load_script_module()
    config = module.PressureConfig(
        base_url="http://127.0.0.1/api/v1",
        token="test-token",
        users=2,
        requests_per_user=3,
        mode="monitor",
        campaign_id=None,
        operator_id="operator-test-1",
    )

    requests = list(module.build_requests(config))

    assert len(requests) == 6
    assert {request.method for request in requests} == {"GET"}
    assert {request.path for request in requests} == {"/generation-tasks?limit=200"}
    assert requests[0].headers == {
        "X-Operator-Id": "operator-test-1",
        "Authorization": "Bearer test-token",
    }


def test_pressure_script_topic_mode_requires_explicit_campaign_id() -> None:
    module = load_script_module()
    config = module.PressureConfig(
        base_url="http://127.0.0.1/api/v1",
        token=None,
        users=1,
        requests_per_user=1,
        mode="topic",
        campaign_id=None,
        operator_id="operator-test-1",
    )

    with pytest.raises(ValueError, match="campaign-id"):
        list(module.build_requests(config))

    config.campaign_id = "campaign-1"
    request = next(iter(module.build_requests(config)))

    assert request.method == "POST"
    assert request.path == "/topics/generate/task"
    assert request.headers == {"X-Operator-Id": "operator-test-1"}
    assert request.json["campaign_id"] == "campaign-1"
    assert request.json["limit"] == 3
    assert request.json["signals"]["pressure_user"] == 1
