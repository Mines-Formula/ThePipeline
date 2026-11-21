from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app import app as app_module
from app.classification_store import ClassificationStore, canonical_run_id
from telemetry_input import InputConfig, SchemaRegistry, decode_to_csv
from telemetry_input.vehicle_classifier import (
    VEHICLE_EV,
    VEHICLE_IC,
    VEHICLE_UNKNOWN,
    classify_csv_file,
)

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "src" / "schemas"
SCHEMAS_V3_DIR = SCHEMAS_DIR / "MF26" / "v3"
GENERATOR_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "ProtobufFiles"
    / "tools"
    / "generate_sample_protobuf.py"
)


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _delimited_stream(messages) -> bytes:
    chunks = []
    for msg in messages:
        payload = msg.SerializeToString()
        chunks.append(_varint(len(payload)) + payload)
    return b"".join(chunks)


@pytest.fixture(scope="module")
def mf26_v3_registry():
    return SchemaRegistry().load_proto_directory(SCHEMAS_DIR)


def _decode_carframes(
    tmp_path: Path, mf26_v3_registry, frames, name: str = "capture"
):
    stream = _delimited_stream(frames)
    pb_path = tmp_path / f"{name}.binpb"
    csv_path = tmp_path / f"{name}.csv"
    pb_path.write_bytes(stream)
    config = InputConfig(
        input_format="protobuf_delimited",
        schema=SCHEMAS_DIR,
        message_type="MF26.v3.CarFrame",
    )
    decode_to_csv(pb_path, csv_path, config, mf26_v3_registry)
    return config.runtime_metrics["vehicle_classification"], csv_path


@pytest.mark.parametrize("module_field", ["module1", "module2", "module3", "module4", "module5"])
def test_ev_bms_modules_classify_as_ev(tmp_path, mf26_v3_registry, module_field):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_000
    getattr(frame.ev, module_field).voltage_g1 = 3.95

    result, csv_path = _decode_carframes(tmp_path, mf26_v3_registry, [frame], module_field)
    assert result.vehicle_type == VEHICLE_EV
    assert module_field in result.matched_ev
    assert classify_csv_file(csv_path).vehicle_type == VEHICLE_EV


@pytest.mark.parametrize("precharge_state", [True, False])
def test_ev_pre_charge_status_classified_even_when_false(
    tmp_path, mf26_v3_registry, precharge_state
):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_001
    frame.ev.pre_charge_status.precharge_state = precharge_state

    result, _ = _decode_carframes(tmp_path, mf26_v3_registry, [frame], f"precharge_{precharge_state}")
    assert result.vehicle_type == VEHICLE_EV
    assert "pre_charge_status" in result.matched_ev


def test_ev_inverter_motor_speed_rpm_has_no_ic_false_conflict(tmp_path, mf26_v3_registry):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_002
    frame.ev.m165_motor_position_info.motor_speed_rpm = 4500
    frame.ev.m176_fast_info.fast_motor_speed_rpm = 4510

    result, csv_path = _decode_carframes(tmp_path, mf26_v3_registry, [frame], "inverter")
    assert result.vehicle_type == VEHICLE_EV
    assert result.source == "protobuf_ev_signals"
    assert classify_csv_file(csv_path).vehicle_type == VEHICLE_EV


def test_ev_nested_under_vehicle_state_classifies_as_ev(tmp_path, mf26_v3_registry):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_003
    frame.vehicle_state.ev.bms_battery_pack.charge_pct = 88.5

    result, csv_path = _decode_carframes(tmp_path, mf26_v3_registry, [frame], "vs_ev")
    assert result.vehicle_type == VEHICLE_EV
    assert classify_csv_file(csv_path).vehicle_type == VEHICLE_EV


def test_ic_ecu_data_and_ic_pdm_channels_classify_as_ic(tmp_path, mf26_v3_registry):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")

    ecu_frame = CarFrame()
    ecu_frame.timestamp.seconds = 1_700_000_004
    ecu_frame.ecu.engine.engine_speed_rpm = 8200.0
    ecu_result, ecu_csv = _decode_carframes(tmp_path, mf26_v3_registry, [ecu_frame], "ecu")
    assert ecu_result.vehicle_type == VEHICLE_IC
    assert classify_csv_file(ecu_csv).vehicle_type == VEHICLE_IC

    pdm_frame = CarFrame()
    pdm_frame.timestamp.seconds = 1_700_000_005
    pdm_frame.pdm.currents.fuel_pump_a = 4.1
    pdm_result, pdm_csv = _decode_carframes(tmp_path, mf26_v3_registry, [pdm_frame], "pdm_ic")
    assert pdm_result.vehicle_type == VEHICLE_IC
    assert classify_csv_file(pdm_csv).vehicle_type == VEHICLE_IC


def test_shared_daq_ev_pedal_fields_do_not_false_positive_as_ev(tmp_path, mf26_v3_registry):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_006
    frame.daq.EV_Pedal_Position = 0.65
    frame.daq.EV_Brake_Pressure = 120.0
    frame.daq.EV_LatchingState = True
    frame.daq.BSPD_LatchingState = False

    result, csv_path = _decode_carframes(tmp_path, mf26_v3_registry, [frame], "daq_only")
    assert result.vehicle_type == VEHICLE_UNKNOWN
    assert result.source == "unclassified"
    assert classify_csv_file(csv_path).vehicle_type == VEHICLE_UNKNOWN


def test_shared_pdm_only_channels_classify_as_unknown(tmp_path, mf26_v3_registry):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_007
    frame.pdm.currents.fan_a = 3.2
    frame.pdm.voltages.water_pump_v = 12.1

    result, csv_path = _decode_carframes(tmp_path, mf26_v3_registry, [frame], "pdm_shared")
    assert result.vehicle_type == VEHICLE_UNKNOWN
    assert classify_csv_file(csv_path).vehicle_type == VEHICLE_UNKNOWN


@pytest.mark.parametrize(
    ("car_id", "expected_type", "expected_source"),
    [
        ("EV_01", VEHICLE_EV, "car_id"),
        ("IC_01", VEHICLE_IC, "car_id"),
        ("", VEHICLE_UNKNOWN, "unclassified"),
        ("TEST_RIG", VEHICLE_UNKNOWN, "unclassified"),
    ],
)
def test_car_id_secondary_signal_on_shared_telemetry(
    tmp_path, mf26_v3_registry, car_id, expected_type, expected_source
):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    frame = CarFrame()
    frame.timestamp.seconds = 1_700_000_008
    if car_id:
        frame.car_id = car_id
    frame.gps.latitude_deg = 39.75
    frame.gps.longitude_deg = -105.22

    result, csv_path = _decode_carframes(
        tmp_path, mf26_v3_registry, [frame], f"gps_{car_id or 'empty'}"
    )
    assert result.vehicle_type == expected_type
    assert result.source == expected_source
    assert classify_csv_file(csv_path).vehicle_type == expected_type


def test_conflicting_ev_and_ic_signals_classify_as_unknown_conflict(
    tmp_path, mf26_v3_registry
):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    ev_frame = CarFrame()
    ev_frame.timestamp.seconds = 1_700_000_009
    ev_frame.ev.module1.voltage_g1 = 4.0

    ic_frame = CarFrame()
    ic_frame.timestamp.seconds = 1_700_000_010
    ic_frame.ecu.engine.engine_speed_rpm = 7500.0

    result, csv_path = _decode_carframes(
        tmp_path, mf26_v3_registry, [ev_frame, ic_frame], "conflict"
    )
    assert result.vehicle_type == VEHICLE_UNKNOWN
    assert result.source == "conflict"
    csv_result = classify_csv_file(csv_path)
    assert csv_result.vehicle_type == VEHICLE_UNKNOWN
    assert csv_result.source == "conflict"


def test_custom_schema_boundary_avoids_module10_false_positive(tmp_path):
    schema_dir = tmp_path / "custom_schema"
    schema_dir.mkdir()
    (schema_dir / "custom.proto").write_text(
        """
        syntax = "proto3";
        package custom;
        message Packet {
          double module10 = 1;
        }
        """,
        encoding="utf-8",
    )
    registry = SchemaRegistry().load_proto_directory(schema_dir)
    Packet = registry.resolve("custom.Packet")
    pb_path = tmp_path / "custom.binpb"
    pb_path.write_bytes(_delimited_stream([Packet(module10=3.7)]))
    cfg = InputConfig(
        input_format="protobuf_delimited",
        schema=schema_dir,
        message_type="custom.Packet",
    )
    decode_to_csv(pb_path, tmp_path / "custom.csv", cfg, registry)
    assert cfg.runtime_metrics["vehicle_classification"].vehicle_type == VEHICLE_UNKNOWN


def test_legacy_can_csv_classification_patterns(tmp_path):
    ic_csv = tmp_path / "can_ic.csv"
    with ic_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Timestamp", "CANID", "Sensor", "Value", "Unit"])
        writer.writerow(["1000", "512", "RPM", "6500", "rpm"])
        writer.writerow(["1010", "512", "Fuel_Pressure", "300", "kPa"])
    assert classify_csv_file(ic_csv).vehicle_type == VEHICLE_IC

    gps_only_csv = tmp_path / "can_gps_only.csv"
    with gps_only_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Timestamp", "CANID", "Sensor", "Value", "Unit"])
        writer.writerow(["1000", "256", "Latitude", "39.75", "deg"])
        writer.writerow(["1000", "256", "Longitude", "-105.22", "deg"])
    assert classify_csv_file(gps_only_csv).vehicle_type == VEHICLE_UNKNOWN


def test_pipeline_resets_classification_between_shared_config_calls(
    tmp_path, mf26_v3_registry
):
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    ev_frame = CarFrame()
    ev_frame.ev.module1.voltage_g1 = 3.9
    gps_frame = CarFrame()
    gps_frame.gps.latitude_deg = 39.75

    shared_cfg = InputConfig(
        input_format="protobuf_delimited",
        schema=SCHEMAS_DIR,
        message_type="MF26.v3.CarFrame",
    )

    ev_pb = tmp_path / "ev.binpb"
    ev_pb.write_bytes(_delimited_stream([ev_frame]))
    decode_to_csv(ev_pb, tmp_path / "ev.csv", shared_cfg, mf26_v3_registry)
    assert shared_cfg.runtime_metrics["vehicle_classification"].vehicle_type == VEHICLE_EV

    unk_pb = tmp_path / "unk.binpb"
    unk_pb.write_bytes(_delimited_stream([gps_frame]))
    decode_to_csv(unk_pb, tmp_path / "unk.csv", shared_cfg, mf26_v3_registry)
    assert shared_cfg.runtime_metrics["vehicle_classification"].vehicle_type == VEHICLE_UNKNOWN


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    csv_dir = tmp_path / "csv"
    rerun_dir = tmp_path / "rerun"
    manifest = tmp_path / "vehicle_classifications.json"
    raw_dir.mkdir()
    csv_dir.mkdir()
    rerun_dir.mkdir()

    monkeypatch.setattr(app_module, "RAW_DIR", raw_dir)
    monkeypatch.setattr(app_module, "CSV_DIR", csv_dir)
    monkeypatch.setattr(app_module, "RERUN_DIR", rerun_dir)
    monkeypatch.setattr(app_module, "CLASSIFICATIONS_FILE", manifest)
    app_module.app.config["TESTING"] = True
    app_module.app.config["tasks"].clear()
    return app_module.app.test_client(), raw_dir, csv_dir, rerun_dir, manifest


def test_legacy_migration_seeds_existing_files_as_ic_and_writes_nothing_when_empty(
    tmp_path,
):
    raw_dir = tmp_path / "raw"
    csv_dir = tmp_path / "csv"
    rerun_dir = tmp_path / "rerun"
    raw_dir.mkdir()
    csv_dir.mkdir()
    rerun_dir.mkdir()
    manifest = tmp_path / "vehicle_classifications.json"

    store = ClassificationStore(manifest)
    assert store.seed_legacy_runs(raw_dir, csv_dir, rerun_dir) == 0
    assert not manifest.exists()

    (raw_dir / "raw_old_run.data").write_bytes(b"123")
    (csv_dir / "old_run.csv").write_text("Timestamp,CANID,Sensor,Value,Unit\n", encoding="utf-8")
    (csv_dir / "old_run_unixtime.csv").write_text("Timestamp,CANID,Sensor,Value,Unit\n", encoding="utf-8")
    (rerun_dir / "old_run.rrd").write_bytes(b"rrd")
    (rerun_dir / "orphan_only.rrd").write_bytes(b"orphan")

    seeded = store.seed_legacy_runs(raw_dir, csv_dir, rerun_dir)
    assert seeded == 2
    assert manifest.is_file()

    resolved_csv = store.get_vehicle_types_for_files("csv", ["old_run.csv", "old_run_unixtime.csv"], csv_dir)
    resolved_rerun = store.get_vehicle_types_for_files("rerun", ["old_run.rrd", "orphan_only.rrd"], csv_dir)
    resolved_raw = store.get_vehicle_types_for_files("raw", ["raw_old_run.data"], csv_dir)
    assert resolved_csv == {"old_run.csv": VEHICLE_IC, "old_run_unixtime.csv": VEHICLE_IC}
    assert resolved_rerun == {"old_run.rrd": VEHICLE_IC, "orphan_only.rrd": VEHICLE_IC}
    assert resolved_raw == {"raw_old_run.data": VEHICLE_IC}


def test_convert_file_persists_fingerprint_and_avoids_rescan_on_files_endpoint(
    isolated_app, mf26_v3_registry, monkeypatch
):
    client, raw_dir, csv_dir, rerun_dir, manifest = isolated_app
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")
    ev_frame = CarFrame()
    ev_frame.timestamp.seconds = 1_700_000_100
    ev_frame.ev.module2.voltage_g1 = 4.05

    class SyncThread:
        def __init__(self, target, args=(), kwargs=None, name=None, **_options):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.name = name

        def start(self):
            orig = threading.current_thread().name
            threading.current_thread().name = self.name
            try:
                self.target(*self.args, **self.kwargs)
            finally:
                threading.current_thread().name = orig

    monkeypatch.setattr(app_module.threading, "Thread", SyncThread)

    resp = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(_delimited_stream([ev_frame])), "ev_bench_run.binpb"),
            "input_format": "protobuf_delimited",
            "message_type": "MF26.v3.CarFrame",
            "debug": "true",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    task_name = resp.get_json()["name"]

    progress_resp = client.get(f"/progress?name={task_name}")
    assert progress_resp.status_code == 200
    progress_body = progress_resp.get_json()
    assert progress_body["exception"]["present"] is False, progress_body["exception"]
    assert progress_body["metrics"]["vehicle_type"] == VEHICLE_EV
    assert progress_body["metrics"]["classifications"][0]["run_id"] == "ev_bench_run"

    # Stored fingerprint must match post-utime mtime on disk.
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    csv_stat = (csv_dir / "ev_bench_run.csv").stat()
    assert manifest_data["runs"]["ev_bench_run"]["csv_mtime_ns"] == csv_stat.st_mtime_ns
    assert manifest_data["runs"]["ev_bench_run"]["csv_size"] == csv_stat.st_size

    # Spy on CSV scanning to prove /files never re-scans mapped files.
    import app.classification_store as store_module

    scan_calls = []

    def fail_if_called(path):
        scan_calls.append(path)
        raise AssertionError(f"Unexpected CSV rescan for {path}")

    monkeypatch.setattr(store_module, "classify_csv_file", fail_if_called)

    for file_type, expected_name in (
        ("csv", "ev_bench_run.csv"),
        ("rerun", "ev_bench_run.rrd"),
        ("raw", "raw_ev_bench_run.data"),
    ):
        r = client.get(f"/files?type={file_type}")
        assert r.status_code == 200
        names_to_types = {item["name"]: item["vehicle_type"] for item in r.get_json()}
        assert names_to_types[expected_name] == VEHICLE_EV
    assert scan_calls == []

    # Verify ?vehicle filtering query parameter.
    assert len(client.get("/files?type=csv&vehicle=ev").get_json()) == 2
    assert len(client.get("/files?type=csv&vehicle=ic").get_json()) == 0
    assert client.get("/files?type=csv&vehicle=bogus").status_code == 400


def test_store_rescans_only_when_csv_changes_on_disk(tmp_path):
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    manifest = tmp_path / "vehicle_classifications.json"
    store = ClassificationStore(manifest)

    csv_file = csv_dir / "mutable_run.csv"
    csv_file.write_text(
        "Timestamp,CANID,Sensor,Value,Unit\n1000,10,Latitude,39.7,\n",
        encoding="utf-8",
    )
    assert store.get_vehicle_types_for_files("csv", ["mutable_run.csv"], csv_dir) == {
        "mutable_run.csv": VEHICLE_UNKNOWN
    }

    # Overwrite with EV data and advance mtime to simulate out-of-band replacement.
    csv_file.write_text(
        "Timestamp,CANID,Sensor,Value,Unit\n1000,EV_01,ev.module1.voltage_g1,4.1,\n",
        encoding="utf-8",
    )
    st = csv_file.stat()
    os.utime(csv_file, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))

    assert store.get_vehicle_types_for_files("csv", ["mutable_run.csv"], csv_dir) == {
        "mutable_run.csv": VEHICLE_EV
    }


def test_multifile_batch_upload_and_manual_classify_endpoint(
    isolated_app, mf26_v3_registry, monkeypatch
):
    client, _, csv_dir, rerun_dir, manifest = isolated_app
    CarFrame = mf26_v3_registry.resolve("MF26.v3.CarFrame")

    ev_frame = CarFrame()
    ev_frame.timestamp.seconds = 1_700_000_200
    ev_frame.ev.module1.voltage_g1 = 3.9

    ic_frame = CarFrame()
    ic_frame.timestamp.seconds = 1_700_000_201
    ic_frame.ecu.engine.engine_speed_rpm = 7000.0

    unk_frame = CarFrame()
    unk_frame.timestamp.seconds = 1_700_000_202
    unk_frame.gps.latitude_deg = 39.75

    class SyncThread:
        def __init__(self, target, args=(), kwargs=None, name=None, **_options):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}
            self.name = name

        def start(self):
            orig = threading.current_thread().name
            threading.current_thread().name = self.name
            try:
                self.target(*self.args, **self.kwargs)
            finally:
                threading.current_thread().name = orig

    monkeypatch.setattr(app_module.threading, "Thread", SyncThread)

    resp = client.post(
        "/upload",
        data={
            "files": [
                (io.BytesIO(_delimited_stream([ev_frame])), "batch_ev.binpb"),
                (io.BytesIO(_delimited_stream([ic_frame])), "batch_ic.binpb"),
                (io.BytesIO(_delimited_stream([unk_frame])), "batch_unk.binpb"),
            ],
            "input_format": "protobuf_delimited",
            "message_type": "MF26.v3.CarFrame",
            "debug": "true",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    task_name = resp.get_json()["name"]
    progress = client.get(f"/progress?name={task_name}").get_json()
    assert progress["exception"]["present"] is False, progress["exception"]
    by_run = {
        entry["run_id"]: entry["vehicle_type"]
        for entry in progress["metrics"]["classifications"]
    }
    assert by_run == {
        "batch_ev": VEHICLE_EV,
        "batch_ic": VEHICLE_IC,
        "batch_unk": VEHICLE_UNKNOWN,
    }

    # Validation checks on /files/classify
    assert client.post("/files/classify", json={"run_id": "batch_unk", "vehicle_type": "other"}).status_code == 400
    assert client.post("/files/classify", json={"run_id": "../evil", "vehicle_type": "ev"}).status_code == 400
    assert client.post("/files/classify", json={"run_id": "does_not_exist", "vehicle_type": "ev"}).status_code == 404

    # Manually label batch_unk as EV
    classify_resp = client.post(
        "/files/classify",
        json={"run_id": "batch_unk", "vehicle_type": "ev"},
    )
    assert classify_resp.status_code == 200
    assert classify_resp.get_json() == {
        "run_id": "batch_unk",
        "vehicle_type": "ev",
        "source": "manual",
    }

    # All file types reflect manual override and survive fresh ClassificationStore load.
    for file_type, expected_name in (
        ("csv", "batch_unk.csv"),
        ("rerun", "batch_unk.rrd"),
        ("raw", "raw_batch_unk.data"),
    ):
        items = {f["name"]: f["vehicle_type"] for f in client.get(f"/files?type={file_type}").get_json()}
        assert items[expected_name] == VEHICLE_EV

    reloaded = ClassificationStore(manifest)
    assert reloaded.get_vehicle_types_for_files("rerun", ["batch_unk.rrd"], csv_dir) == {
        "batch_unk.rrd": VEHICLE_EV
    }


def test_canonical_run_id_handles_double_raw_prefix():
    assert canonical_run_id("raw", "raw_raw_test.data") == "raw_test"
    assert canonical_run_id("csv", "run1_unixtime.csv") == "run1"
    assert canonical_run_id("rerun", "run1.rrd") == "run1"


def test_sample_protobuf_generator_emits_ev_ic_and_unknown(tmp_path):
    if not GENERATOR_SCRIPT.is_file() or importlib.util.find_spec("grpc_tools") is None:
        pytest.skip("Sibling ProtobufFiles generator or grpcio-tools unavailable")

    cases = [
        ("sample_ev.binpb", ["--oneof", "ev", "--car-id", "EV_01"], VEHICLE_EV),
        ("sample_ic.binpb", ["--oneof", "ecu", "--car-id", "IC_01"], VEHICLE_IC),
        ("sample_unknown.binpb", ["--oneof", "gps", "--no-car-id"], VEHICLE_UNKNOWN),
    ]
    for filename, extra_args, expected in cases:
        out_pb = tmp_path / filename
        cmd = [
            sys.executable,
            str(GENERATOR_SCRIPT),
            str(SCHEMAS_V3_DIR),
            "MF26.v3.CarFrame",
            str(out_pb),
            "--count",
            "2",
            *extra_args,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert res.returncode == 0, res.stderr
        cfg = InputConfig(
            input_format="protobuf_delimited",
            schema=SCHEMAS_DIR,
            message_type="MF26.v3.CarFrame",
        )
        decode_to_csv(out_pb, tmp_path / f"{out_pb.stem}.csv", cfg)
        assert cfg.runtime_metrics["vehicle_classification"].vehicle_type == expected
