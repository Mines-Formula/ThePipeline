from __future__ import annotations

import io
import importlib
import importlib.util
from pathlib import Path

import pytest

from telemetry_input import (
    ErrorPolicy,
    InputConfig,
    InputFormat,
    ProtobufDecodeError,
    ProtobufDecoder,
    SchemaRegistry,
    SchemaResolutionError,
)
from telemetry_input.formula import FormulaDecodeError, iter_formula_records
from telemetry_input.pipeline import decode_to_csv
from grpc_tools import protoc


def _schemas(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "common.proto").write_text(
        """
        syntax = "proto3";
        package telemetry;
        message Metadata { map<string, string> labels = 1; }
        """
    )
    (tmp_path / "telemetry.proto").write_text(
        """
        syntax = "proto3";
        package telemetry;
        import "common.proto";
        import "google/protobuf/timestamp.proto";
        enum State { UNKNOWN = 0; RUNNING = 1; }
        message CanFrame {
          uint64 timestamp = 1;
          uint32 can_id = 2;
          bytes payload = 3;
        }
        message Sensor {
          google.protobuf.Timestamp timestamp = 1;
          string sensor = 2;
          double value = 3;
          string unit = 4;
          repeated int32 samples = 5;
          Metadata metadata = 6;
          State state = 7;
          oneof source { string ecu = 8; uint32 channel = 9; }
          optional string note = 10;
        }
        """
    )
    return tmp_path


def _registry(tmp_path):
    return SchemaRegistry().load_proto_directory(_schemas(tmp_path))


def _varint(value):
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def test_schema_directory_imports_and_qualified_resolution(tmp_path):
    registry = _registry(tmp_path)
    assert registry.resolve("telemetry.CanFrame").DESCRIPTOR.full_name == "telemetry.CanFrame"
    assert registry.resolve(".telemetry.Sensor").DESCRIPTOR.full_name == "telemetry.Sensor"


def test_schema_directory_ignores_virtual_environment_proto_files(tmp_path):
    schemas = _schemas(tmp_path / "schema")
    ignored = schemas / ".venv" / "lib" / "python" / "site-packages" / "vendor.proto"
    ignored.parent.mkdir(parents=True)
    ignored.write_text('this is not a schema source; it must not be compiled')

    registry = SchemaRegistry().load_proto_directory(schemas)

    assert registry.resolve("telemetry.CanFrame").DESCRIPTOR.full_name == "telemetry.CanFrame"


def test_schema_compile_error_includes_protoc_diagnostic(tmp_path):
    (tmp_path / "invalid.proto").write_text('syntax = "proto3"; message {')

    with pytest.raises(SchemaResolutionError, match="Expected message name"):
        SchemaRegistry().load_proto_directory(tmp_path)


def test_single_message_preserves_types_and_normalizes_raw_can(tmp_path):
    registry = _registry(tmp_path)
    message_class = registry.resolve("telemetry.CanFrame")
    payload = message_class(timestamp=2**55, can_id=123, payload=b"\x01\xff").SerializeToString()
    config = InputConfig(
        input_format="protobuf_binary",
        schema=tmp_path,
        message_type="telemetry.CanFrame",
    )
    record = next(ProtobufDecoder(registry, config).decode(io.BytesIO(payload)))
    assert record.value["timestamp"] == 2**55
    assert record.value["payload"] == b"\x01\xff"
    assert record.normalized.is_raw_can
    assert record.source_offset == 0


def test_delimited_stream_offsets_nested_enum_oneof_and_optional(tmp_path):
    registry = _registry(tmp_path)
    message_class = registry.resolve("telemetry.Sensor")
    first = message_class(sensor="Speed", value=42.5, unit="mph", samples=[1, 2], ecu="VCU")
    first.metadata.labels["side"] = "left"
    first.state = 1
    second = message_class(sensor="RPM", value=8000, channel=2, note="peak")
    chunks = [item.SerializeToString() for item in (first, second)]
    stream = b"".join(_varint(len(item)) + item for item in chunks)
    config = InputConfig(
        input_format="protobuf_delimited",
        schema=tmp_path,
        message_type="telemetry.Sensor",
    )
    records = list(ProtobufDecoder(registry, config).decode(io.BytesIO(stream)))
    assert [item.value["sensor"] for item in records] == ["Speed", "RPM"]
    assert records[0].value["state"] == "RUNNING"
    assert records[0].value["_oneofs"]["source"] == "ecu"
    assert records[1].value["note"] == "peak"
    assert records[1].source_offset == len(_varint(len(chunks[0]))) + len(chunks[0])


def test_fixed32_little_endian_framing(tmp_path):
    registry = _registry(tmp_path)
    message_class = registry.resolve("telemetry.CanFrame")
    payload = message_class(timestamp=1, can_id=2, payload=b"abc").SerializeToString()
    config = InputConfig(
        input_format="protobuf_delimited",
        schema=tmp_path,
        message_type="telemetry.CanFrame",
        length_prefix_encoding="fixed32",
        byte_order="little",
    )
    records = list(
        ProtobufDecoder(registry, config).decode(
            io.BytesIO(len(payload).to_bytes(4, "little") + payload)
        )
    )
    assert records[0].normalized.payload == b"abc"


def test_truncated_delimited_message_reports_context(tmp_path):
    registry = _registry(tmp_path)
    config = InputConfig(
        input_format=InputFormat.PROTOBUF_DELIMITED,
        schema=tmp_path,
        message_type="telemetry.CanFrame",
    )
    with pytest.raises(ProtobufDecodeError, match=r"offset=0"):
        list(ProtobufDecoder(registry, config).decode(io.BytesIO(b"\x05\x01")))


def test_collect_policy_keeps_errors(tmp_path):
    registry = _registry(tmp_path)
    config = InputConfig(
        input_format="protobuf_delimited",
        schema=tmp_path,
        message_type="telemetry.CanFrame",
        error_policy=ErrorPolicy.COLLECT,
    )
    decoder = ProtobufDecoder(registry, config)
    assert list(decoder.decode(io.BytesIO(b"\x02\xff\xff"))) == []
    assert len(decoder.errors) == 1


def test_decoded_telemetry_writes_common_csv(tmp_path):
    registry = _registry(tmp_path / "schema")
    message_class = registry.resolve("telemetry.Sensor")
    payload = message_class(sensor="RPM", value=7200, unit="rpm").SerializeToString()
    output = tmp_path / "result.csv"
    config = InputConfig(
        input_format="protobuf_binary",
        schema=tmp_path / "schema",
        message_type="telemetry.Sensor",
    )
    assert decode_to_csv(io.BytesIO(payload), output, config, registry) == 1
    assert output.read_text().splitlines() == [
        "Timestamp,CANID,Sensor,Value,Unit",
        ",,RPM,7200.0,rpm",
    ]


def test_generated_python_module_resolution(tmp_path, monkeypatch):
    schemas = _schemas(tmp_path / "schema")
    generated = tmp_path / "generated"
    generated.mkdir()
    grpc_include = (
        Path(importlib.util.find_spec("grpc_tools").origin).parent / "_proto"
    )
    assert (
        protoc.main(
            [
                "protoc",
                f"-I{schemas}",
                f"-I{grpc_include}",
                f"--python_out={generated}",
                "common.proto",
                "telemetry.proto",
            ]
        )
        == 0
    )
    monkeypatch.syspath_prepend(str(generated))
    module = importlib.import_module("telemetry_pb2")
    registry = SchemaRegistry().load_generated_module(module)
    assert registry.resolve("telemetry.Sensor") is module.Sensor


def test_formula_adapter_streams_and_rejects_truncation():
    packet = bytes([2]) + (123).to_bytes(4, "big") + (45).to_bytes(4, "big") + b"\xaa\xbb"
    record = next(iter_formula_records(io.BytesIO(packet)))
    assert record.normalized.timestamp == 123
    assert record.normalized.can_id == 45
    assert record.normalized.payload == b"\xaa\xbb"
    with pytest.raises(FormulaDecodeError, match="truncated"):
        list(iter_formula_records(io.BytesIO(packet[:-1])))
