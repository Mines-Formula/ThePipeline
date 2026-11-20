from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class NormalizedTelemetry:
    timestamp: int | float | str | None = None
    # Raw CAN records use an integer ID. Already-decoded protobuf telemetry
    # may use a string stream/car identifier as the CSV tag instead.
    can_id: int | str | None = None
    payload: bytes | None = None
    sensor: str | None = None
    value: Any = None
    unit: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_raw_can(self) -> bool:
        return self.can_id is not None and self.payload is not None


@dataclass(slots=True)
class DecodedRecord:
    source_format: str
    message_type: str
    value: Any
    raw_bytes: bytes
    source_offset: int
    metadata: dict[str, Any] = field(default_factory=dict)
    normalized: NormalizedTelemetry | None = None

    def as_mapping(self) -> Mapping[str, Any]:
        if isinstance(self.value, Mapping):
            return self.value
        raise TypeError("record value is not a mapping")
