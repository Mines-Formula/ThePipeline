from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from .models import DecodedRecord, NormalizedTelemetry


class FormulaDecodeError(ValueError):
    pass


def iter_formula_records(
    source: str | Path | BinaryIO, maximum_record_size: int = 64 * 1024 * 1024
) -> Iterator[DecodedRecord]:
    source_name = _source_name(source)
    stream, close = _open(source)
    offset = 0
    try:
        while True:
            header = stream.read(1)
            if not header:
                return
            frame_offset = offset
            offset += 1
            length = header[0]
            if length > 127:
                string_length = length - 127
                raw = _read_exact(stream, string_length, frame_offset, "string")
                offset += string_length
                yield DecodedRecord(
                    source_format="formula_binary",
                    message_type="formula.string",
                    value=raw.decode("ascii", errors="ignore"),
                    raw_bytes=header + raw,
                    source_offset=frame_offset,
                    metadata=(
                        {"source_file": source_name} if source_name else {}
                    ),
                )
                continue
            if length > maximum_record_size:
                raise FormulaDecodeError(
                    f"record at offset {frame_offset} exceeds maximum size"
                )
            metadata = _read_exact(stream, 8, frame_offset, "CAN metadata")
            payload = _read_exact(stream, length, frame_offset, "CAN payload")
            offset += 8 + length
            timestamp = int.from_bytes(metadata[:4], "big")
            can_id = int.from_bytes(metadata[4:], "big")
            normalized = NormalizedTelemetry(
                timestamp=timestamp, can_id=can_id, payload=payload
            )
            yield DecodedRecord(
                source_format="formula_binary",
                message_type="formula.can.Frame",
                value={
                    "timestamp": timestamp,
                    "can_id": can_id,
                    "payload": payload,
                },
                raw_bytes=header + metadata + payload,
                source_offset=frame_offset,
                metadata={"source_file": source_name} if source_name else {},
                normalized=normalized,
            )
    finally:
        if close:
            stream.close()


def _read_exact(
    stream: BinaryIO, length: int, offset: int, description: str
) -> bytes:
    value = stream.read(length)
    if len(value) != length:
        raise FormulaDecodeError(
            f"truncated {description} at offset {offset}: expected {length} "
            f"bytes, got {len(value)}"
        )
    return value


def _open(source: str | Path | BinaryIO) -> tuple[BinaryIO, bool]:
    if isinstance(source, (str, Path)):
        return open(source, "rb"), True
    return source, False


def _source_name(source: str | Path | BinaryIO) -> str | None:
    if isinstance(source, (str, Path)):
        return str(source)
    name = getattr(source, "name", None)
    return str(name) if name is not None else None
