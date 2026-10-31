"""Ensure typed date/time values survive the real memory HTTP and encrypted-storage path."""

import pytest
from test_product_memory import memory_key, native_run  # noqa: F401


@pytest.mark.parametrize(
    "typed",
    [
        {
            "memory_type": "commute_preference",
            "origin": "home",
            "destination": "office",
            "travel_mode": "transit",
            "timezone": "Asia/Shanghai",
            "weekdays": [1, 3],
            "preferred_departure": "08:30:00",
        },
        {
            "memory_type": "time_preference",
            "activity": "reading",
            "timezone": "Asia/Shanghai",
            "preferred_time": "20:00:00",
        },
        {
            "memory_type": "experience",
            "occurred_on": "2020-01-01",
            "description": "Synthetic experience",
        },
    ],
)
@pytest.mark.parametrize("management", [False, True])
@pytest.mark.usefixtures("memory_key")
async def test_typed_memory_http_serializes_dates_and_times(resource_client, typed, management):
    """HTTP validation must not leave Python temporal objects in persisted request JSON."""
    run = await native_run(resource_client, "typed memory fixture")
    path = (
        "/api/v1/me/user-memories" if management else f"/api/v1/runs/{run['run_id']}/user-memories"
    )
    command = {
        "action": "save",
        "key": "typed-http",
        "text": "Synthetic typed memory",
        "typed_data": typed,
        "confirmed_type": True,
        "confirmed_sensitive": True,
    }
    saved = await resource_client.post(path, json=command)
    assert saved.status_code == 200, saved.text
    repeated = await resource_client.post(path, json=command)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["entry_id"] == saved.json()["entry_id"]
