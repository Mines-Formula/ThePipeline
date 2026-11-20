"""Streaming input adapters for Formula binary and Protocol Buffers telemetry."""

from .config import (
    ErrorPolicy,
    InputConfig,
    InputFormat,
    LengthPrefixEncoding,
)
from .models import DecodedRecord, NormalizedTelemetry
from .pipeline import decode_to_csv, iter_decoded_records
from .protobuf import ProtobufDecodeError, ProtobufDecoder
from .schema import SchemaRegistry, SchemaResolutionError

__all__ = [
    "DecodedRecord",
    "ErrorPolicy",
    "InputConfig",
    "InputFormat",
    "LengthPrefixEncoding",
    "NormalizedTelemetry",
    "ProtobufDecodeError",
    "ProtobufDecoder",
    "SchemaRegistry",
    "SchemaResolutionError",
    "decode_to_csv",
    "iter_decoded_records",
]
