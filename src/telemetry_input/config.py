from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class InputFormat(str, Enum):
    AUTO = "auto"
    FORMULA_BINARY = "formula_binary"
    PROTOBUF_BINARY = "protobuf_binary"
    PROTOBUF_DELIMITED = "protobuf_delimited"


class ErrorPolicy(str, Enum):
    FAIL_FAST = "fail_fast"
    SKIP_MALFORMED = "skip_malformed"
    COLLECT = "collect"


class LengthPrefixEncoding(str, Enum):
    VARINT = "varint"
    FIXED32 = "fixed32"


@dataclass(slots=True)
class InputConfig:
    input_format: InputFormat | str = InputFormat.AUTO
    schema: str | Path | None = None
    generated_module: str | None = None
    message_type: str | None = None
    include_paths: tuple[str | Path, ...] = ()
    length_prefix_encoding: LengthPrefixEncoding | str = LengthPrefixEncoding.VARINT
    byte_order: str = "big"
    error_policy: ErrorPolicy | str = ErrorPolicy.FAIL_FAST
    maximum_message_size: int = 64 * 1024 * 1024
    maximum_nesting_depth: int = 100
    preserve_unknown_fields: bool = True
    field_mapping: dict[str, str] = field(
        default_factory=lambda: {
            "timestamp": "timestamp",
            "can_id": "can_id",
            "payload": "payload",
            "sensor": "sensor",
            "value": "value",
            "unit": "unit",
        }
    )
    runtime_metrics: dict[str, int] = field(
        default_factory=lambda: {"records_skipped": 0}, init=False
    )

    def __post_init__(self) -> None:
        self.input_format = InputFormat(self.input_format)
        self.length_prefix_encoding = LengthPrefixEncoding(self.length_prefix_encoding)
        self.error_policy = ErrorPolicy(self.error_policy)
        self.include_paths = tuple(Path(path) for path in self.include_paths)
        if self.schema is not None:
            self.schema = Path(self.schema)
        if self.byte_order not in {"big", "little"}:
            raise ValueError("byte_order must be 'big' or 'little'")
        if self.maximum_message_size <= 0:
            raise ValueError("maximum_message_size must be positive")
        if self.maximum_nesting_depth <= 0:
            raise ValueError("maximum_nesting_depth must be positive")

    def validate(self) -> None:
        if self.input_format in {
            InputFormat.PROTOBUF_BINARY,
            InputFormat.PROTOBUF_DELIMITED,
        }:
            if not self.message_type:
                raise ValueError("protobuf input requires a fully qualified message_type")
            if self.schema is None and not self.generated_module:
                raise ValueError(
                    "protobuf input requires schema or generated_module configuration"
                )
