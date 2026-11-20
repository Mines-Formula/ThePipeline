from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from google.protobuf import descriptor, message
from google.protobuf.duration_pb2 import Duration
from google.protobuf.timestamp_pb2 import Timestamp

from .config import ErrorPolicy, InputConfig, InputFormat, LengthPrefixEncoding
from .models import DecodedRecord, NormalizedTelemetry
from .schema import SchemaRegistry


class ProtobufDecodeError(ValueError):
    def __init__(
        self,
        detail: str,
        *,
        input_format: str,
        message_type: str,
        schema: str,
        offset: int,
    ) -> None:
        super().__init__(
            f"{detail} (format={input_format}, message_type={message_type}, "
            f"schema={schema}, offset={offset})"
        )
        self.offset = offset
        self.input_format = input_format
        self.message_type = message_type
        self.schema = schema


class ProtobufDecoder:
    def __init__(self, registry: SchemaRegistry, config: InputConfig) -> None:
        config.validate()
        if config.input_format not in {
            InputFormat.PROTOBUF_BINARY,
            InputFormat.PROTOBUF_DELIMITED,
        }:
            raise ValueError("ProtobufDecoder requires a protobuf input format")
        self.registry = registry
        self.config = config
        self.message_class = registry.resolve(config.message_type or "")
        self.errors: list[ProtobufDecodeError] = []

    def decode(
        self, source: str | Path | BinaryIO
    ) -> Iterator[DecodedRecord]:
        self.config.runtime_metrics["records_skipped"] = 0
        source_name = _source_name(source)
        stream, close = _open_binary(source)
        try:
            if self.config.input_format == InputFormat.PROTOBUF_BINARY:
                payload = stream.read(self.config.maximum_message_size + 1)
                if len(payload) > self.config.maximum_message_size:
                    error = self._error("message exceeds maximum_message_size", 0)
                    if not self._handle_error(error):
                        raise error
                    return
                try:
                    yield self._parse(payload, 0, source_name)
                except ProtobufDecodeError as error:
                    if not self._handle_error(error):
                        raise
                return
            yield from self._decode_delimited(stream, source_name)
        finally:
            if close:
                stream.close()

    def _decode_delimited(
        self, stream: BinaryIO, source_name: str | None
    ) -> Iterator[DecodedRecord]:
        offset = 0
        while True:
            frame_offset = offset
            try:
                length, prefix_size = self._read_length(stream, offset)
            except EOFError as exc:
                error = self._error(str(exc), frame_offset)
                if not self._handle_error(error):
                    raise error from exc
                return
            if length is None:
                return
            offset += prefix_size
            if length > self.config.maximum_message_size:
                error = self._error(
                    f"message length {length} exceeds maximum_message_size "
                    f"{self.config.maximum_message_size}",
                    frame_offset,
                )
                if not self._handle_error(error):
                    raise error
                _discard(stream, length)
                offset += length
                continue
            payload = stream.read(length)
            if len(payload) != length:
                error = self._error(
                    f"truncated message: expected {length} bytes, got {len(payload)}",
                    frame_offset,
                )
                if not self._handle_error(error):
                    raise error
                return
            try:
                yield self._parse(payload, frame_offset, source_name)
            except ProtobufDecodeError as error:
                if not self._handle_error(error):
                    raise
            offset += length

    def _read_length(
        self, stream: BinaryIO, offset: int
    ) -> tuple[int | None, int]:
        if self.config.length_prefix_encoding == LengthPrefixEncoding.FIXED32:
            raw = stream.read(4)
            if not raw:
                return None, 0
            if len(raw) != 4:
                raise EOFError(f"truncated fixed32 length prefix: got {len(raw)} bytes")
            return int.from_bytes(raw, self.config.byte_order), 4

        value = 0
        for index in range(10):
            raw = stream.read(1)
            if not raw:
                if index == 0:
                    return None, 0
                raise EOFError("truncated varint length prefix")
            byte = raw[0]
            value |= (byte & 0x7F) << (7 * index)
            if not byte & 0x80:
                return value, index + 1
        raise EOFError("invalid varint length prefix (more than 10 bytes)")

    def _parse(
        self, payload: bytes, offset: int, source_name: str | None
    ) -> DecodedRecord:
        protobuf_message = self.message_class()
        try:
            protobuf_message.ParseFromString(payload)
        except message.DecodeError as exc:
            raise self._error(f"malformed protobuf message: {exc}", offset) from exc
        if not self.config.preserve_unknown_fields:
            protobuf_message.DiscardUnknownFields()
        if _message_depth(protobuf_message) > self.config.maximum_nesting_depth:
            raise self._error(
                f"message exceeds maximum_nesting_depth "
                f"{self.config.maximum_nesting_depth}",
                offset,
            )
        try:
            mapping = message_to_mapping(protobuf_message)
            normalized = normalize_message(protobuf_message, self.config)
        except (TypeError, ValueError) as exc:
            raise self._error(f"protobuf normalization failed: {exc}", offset) from exc
        return DecodedRecord(
            source_format="protobuf",
            message_type=protobuf_message.DESCRIPTOR.full_name,
            value=mapping,
            raw_bytes=payload,
            source_offset=offset,
            metadata={
                "typed_message": protobuf_message,
                **({"source_file": source_name} if source_name else {}),
            },
            normalized=normalized,
        )

    def _error(self, detail: str, offset: int) -> ProtobufDecodeError:
        return ProtobufDecodeError(
            detail,
            input_format=self.config.input_format.value,
            message_type=self.config.message_type or "<unset>",
            schema=str(
                self.config.schema or self.config.generated_module or "<unset>"
            ),
            offset=offset,
        )

    def _handle_error(self, error: ProtobufDecodeError) -> bool:
        if self.config.error_policy == ErrorPolicy.FAIL_FAST:
            return False
        self.config.runtime_metrics["records_skipped"] += 1
        if self.config.error_policy == ErrorPolicy.COLLECT:
            self.errors.append(error)
        return True


def message_to_mapping(value: message.Message) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, field_value in value.ListFields():
        if field.is_repeated:
            if field.message_type and field.message_type.GetOptions().map_entry:
                result[field.name] = {
                    key: _field_value(field.message_type.fields_by_name["value"], item)
                    for key, item in field_value.items()
                }
            else:
                result[field.name] = [
                    _field_value(field, item) for item in field_value
                ]
        else:
            result[field.name] = _field_value(field, field_value)
    oneofs = {
        oneof.name: value.WhichOneof(oneof.name)
        for oneof in value.DESCRIPTOR.oneofs
    }
    if oneofs:
        result["_oneofs"] = oneofs
    return result


def _field_value(field, value: Any) -> Any:
    if field.type == descriptor.FieldDescriptor.TYPE_MESSAGE:
        if value.DESCRIPTOR.full_name == Timestamp.DESCRIPTOR.full_name:
            return _timestamp_isoformat(value)
        if value.DESCRIPTOR.full_name == Duration.DESCRIPTOR.full_name:
            return timedelta(
                seconds=value.seconds, microseconds=value.nanos / 1000
            )
        return message_to_mapping(value)
    if field.type == descriptor.FieldDescriptor.TYPE_ENUM:
        enum_value = field.enum_type.values_by_number.get(int(value))
        return enum_value.name if enum_value else int(value)
    return value


def normalize_message(
    protobuf_message: message.Message, config: InputConfig
) -> NormalizedTelemetry:
    mapping = config.field_mapping
    timestamp = _field_path(protobuf_message, mapping.get("timestamp"))
    can_id = _field_path(protobuf_message, mapping.get("can_id"))
    payload = _field_path(protobuf_message, mapping.get("payload"))
    sensor = _field_path(protobuf_message, mapping.get("sensor"))
    value = _field_path(protobuf_message, mapping.get("value"))
    unit = _field_path(protobuf_message, mapping.get("unit"))
    if payload is not None and not isinstance(payload, bytes):
        try:
            payload = bytes(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("mapped protobuf payload field must contain bytes") from exc
    return NormalizedTelemetry(
        timestamp=_normalize_timestamp(timestamp),
        can_id=int(can_id) if can_id is not None else None,
        payload=payload,
        sensor=str(sensor) if sensor is not None else None,
        value=value,
        unit=str(unit) if unit is not None else None,
    )


def _field_path(value: Any, path: str | None) -> Any:
    if not path:
        return None
    current = value
    for component in path.split("."):
        if isinstance(current, dict):
            if component not in current:
                return None
            current = current[component]
        else:
            field = current.DESCRIPTOR.fields_by_name.get(component)
            if field is None:
                return None
            if field.has_presence and not current.HasField(component):
                return None
            current = getattr(current, component)
    return current


def _normalize_timestamp(value: Any) -> int | float | str | None:
    if value is None:
        return None
    if (
        isinstance(value, message.Message)
        and value.DESCRIPTOR.full_name == Timestamp.DESCRIPTOR.full_name
    ):
        return _timestamp_isoformat(value)
    # MF26 uses its own Timestamp message instead of the Google well-known
    # type. Keep it numeric so the existing CSV/Influx/Rerun stages can use it.
    if isinstance(value, message.Message):
        fields = value.DESCRIPTOR.fields_by_name
        if "seconds" in fields and "nanos" in fields:
            return int(value.seconds) * 1000 + int(value.nanos) // 1_000_000
    return value


def _timestamp_isoformat(value: message.Message) -> str:
    instant = datetime.fromtimestamp(value.seconds, tz=timezone.utc)
    instant += timedelta(microseconds=value.nanos / 1000)
    return instant.isoformat()


def _open_binary(source: str | Path | BinaryIO) -> tuple[BinaryIO, bool]:
    if isinstance(source, (str, Path)):
        return open(source, "rb"), True
    return source, False


def _source_name(source: str | Path | BinaryIO) -> str | None:
    if isinstance(source, (str, Path)):
        return str(source)
    name = getattr(source, "name", None)
    return str(name) if name is not None else None


def _discard(stream: BinaryIO, amount: int) -> None:
    remaining = amount
    while remaining:
        chunk = stream.read(min(remaining, 64 * 1024))
        if not chunk:
            return
        remaining -= len(chunk)


def _message_depth(value: message.Message, current: int = 1) -> int:
    deepest = current
    for field, field_value in value.ListFields():
        if field.type != descriptor.FieldDescriptor.TYPE_MESSAGE:
            continue
        if field.is_repeated:
            children = (
                field_value.values()
                if field.message_type.GetOptions().map_entry
                and field.message_type.fields_by_name["value"].type
                == descriptor.FieldDescriptor.TYPE_MESSAGE
                else field_value
            )
            for child in children:
                if isinstance(child, message.Message):
                    deepest = max(deepest, _message_depth(child, current + 1))
        else:
            deepest = max(deepest, _message_depth(field_value, current + 1))
    return deepest
