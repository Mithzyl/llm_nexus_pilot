"""Check required semantic units in typed memory without model or external requests."""

import pytest
from pydantic import TypeAdapter, ValidationError

from nexuspilot_api.features.memory.schemas.typed_memory import TypedMemory


@pytest.mark.parametrize(
    "value",
    [
        {"memory_type": "place", "label": "home", "address": "synthetic"},
        {
            "memory_type": "place",
            "label": "home",
            "address": "synthetic",
            "coordinate_system": "GCJ02",
            "latitude": 25,
        },
        {
            "memory_type": "time_preference",
            "activity": "meeting",
            "preferred_time": "09:00",
            "timezone": "Not/AZone",
        },
        {
            "memory_type": "commute_preference",
            "origin": "A",
            "destination": "B",
            "travel_mode": "transit",
            "weekdays": [1, 1],
            "timezone": "Asia/Shanghai",
        },
        {"memory_type": "experience", "occurred_on": "2999-01-01", "description": "future"},
    ],
)
def test_typed_memory_rejects_ambiguous_or_inconsistent_fields(value):
    """Missing coordinate units, invalid recurrence and future experiences cannot be stored."""
    with pytest.raises(ValidationError):
        TypeAdapter(TypedMemory).validate_python(value)
