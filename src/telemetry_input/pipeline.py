from __future__ import annotations

import csv
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from can_to_csv import decode

from .config import InputConfig, InputFormat
from .formula import iter_formula_records
from .models import DecodedRecord
from .protobuf import ProtobufDecoder
from .schema import SchemaRegistry


def build_registry(config: InputConfig) -> SchemaRegistry:
    registry = SchemaRegistry()
    if config.generated_module:
        registry.load_generated_module(config.generated_module)
    if config.schema:
        if config.schema.is_dir():
            registry.load_proto_directory(config.schema, config.include_paths)
        else:
            registry.load_proto_file(config.schema, config.include_paths)
    return registry


def iter_decoded_records(
    source: str | Path | BinaryIO,
    config: InputConfig,
    registry: SchemaRegistry | None = None,
) -> Iterator[DecodedRecord]:
    config.validate()
    selected = config.input_format
    if selected == InputFormat.AUTO:
        selected = InputFormat.FORMULA_BINARY
    if selected == InputFormat.FORMULA_BINARY:
        yield from iter_formula_records(source, config.maximum_message_size)
        return
    decoder = ProtobufDecoder(registry or build_registry(config), config)
    yield from decoder.decode(source)


def decode_to_csv(
    source: str | Path | BinaryIO,
    output_path: str | Path,
    config: InputConfig,
    registry: SchemaRegistry | None = None,
) -> int:
    """Decode any supported input into the pipeline's stable five-column CSV."""
    output = Path(output_path)
    records = iter_decoded_records(source, config, registry)
    if config.input_format in {
        InputFormat.PROTOBUF_BINARY,
        InputFormat.PROTOBUF_DELIMITED,
    }:
        return _write_telemetry_csv(records, output)
    return _write_raw_can_via_dbc(records, output)


def _write_raw_can_via_dbc(
    records: Iterator[DecodedRecord], output: Path
) -> int:
    count = 0
    with tempfile.TemporaryDirectory() as temporary_directory:
        raw_path = Path(temporary_directory) / "raw_can.data"
        with raw_path.open("w", newline="") as raw:
            # make_known historically discards the first two lines.
            raw.write("Telemetry input adapter\nTimestamp,CANID,DataBytes\n")
            for record in records:
                normalized = record.normalized
                if record.message_type == "formula.string":
                    continue
                if normalized is None or not normalized.is_raw_can:
                    raise ValueError(
                        f"raw CAN record is missing mapped can_id or payload "
                        f"(message_type={record.message_type}, "
                        f"offset={record.source_offset})"
                    )
                payload = ",".join(str(item) for item in normalized.payload or b"")
                suffix = f",{payload}" if payload else ""
                raw.write(f"{normalized.timestamp},{normalized.can_id}{suffix}\n")
                count += 1
        decode.make_known(str(raw_path), str(output))
    return count


def _write_telemetry_csv(
    records: Iterator[DecodedRecord], output: Path
) -> int:
    count = 0
    with output.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Timestamp", "CANID", "Sensor", "Value", "Unit"])
        for record in records:
            normalized = record.normalized
            if normalized is None:
                continue
            rows = _mapped_telemetry_rows(normalized)
            if rows is None:
                rows = _flatten_message_record(record)
            for row in rows:
                writer.writerow(row)
                count += 1
    return count


def _mapped_telemetry_rows(normalized):
    """Return the traditional one-row representation when it is populated."""
    if normalized.sensor is None or normalized.value is None:
        return None
    return [[
        "" if normalized.timestamp is None else normalized.timestamp,
        "" if normalized.can_id is None else normalized.can_id,
        normalized.sensor,
        normalized.value,
        "" if normalized.unit is None else normalized.unit,
    ]]


def _flatten_message_record(record: DecodedRecord):
    """Flatten an already-decoded protobuf message into telemetry rows.

    Vehicle schemas such as MF26.v3.CarFrame contain nested ECU/DAQ/GPS/PDM
    values rather than a single sensor/value pair. The leaf field path is a
    stable sensor name, and ``car_id`` is retained as the CSV tag.
    """
    value = record.value
    timestamp = _timestamp_mapping(value.get("timestamp")) if isinstance(value, dict) else None
    can_id = value.get("car_id", "") if isinstance(value, dict) else ""
    rows = []
    if isinstance(value, dict):
        for path, leaf in _leaf_values(value):
            if (
                path == "timestamp"
                or path.startswith("timestamp.")
                or path.endswith(".timestamp.seconds")
                or path.endswith(".timestamp.nanos")
                or path == "car_id"
            ):
                continue
            rows.append([timestamp, can_id, path, leaf, ""])
    return rows


def _leaf_values(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "_oneofs":
                continue
            path = f"{prefix}.{key}" if prefix else key
            yield from _leaf_values(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaf_values(child, f"{prefix}[{index}]")
    else:
        yield prefix, value


def _timestamp_mapping(value):
    if isinstance(value, dict) and "seconds" in value and "nanos" in value:
        return int(value["seconds"]) * 1000 + int(value["nanos"]) // 1_000_000
    return value
