"""Regression tests for the fast paths in can_to_csv.decode and line_protocol.

Each test checks the optimized code against the behaviour of the code it
replaced, so the outputs users download stay byte-for-byte the same.
"""

import pandas as pd
import pytest

from can_to_csv import decode
from csv_to_influxdb import convert_unix_time, line_protocol
from telemetry_input import InputConfig, InputFormat, decode_to_csv

# Minimal stand-in for MF13Beta.dbc (the real file lives in a submodule that
# CI does not check out).
TEST_DBC = """VERSION ""

BS_:

BU_: ECU

BO_ 1600 EngineData: 8 ECU
 SG_ EngineSpeed : 7|16@0+ (1,0) [0|65535] "rpm" ECU
 SG_ ThrottlePosition : 23|16@0+ (0.1,0) [0|100] "%" ECU

BO_ 1608 GPS: 8 ECU
 SG_ Latitude : 7|32@0- (0.0001,0) [-90|90] "" ECU
 SG_ Longitude : 39|32@0- (0.0001,0) [-180|180] "" ECU
"""


@pytest.fixture
def test_dbc(tmp_path, monkeypatch):
    dbc_dir = tmp_path / "dbc"
    dbc_dir.mkdir()
    (dbc_dir / "MF13Beta.dbc").write_text(TEST_DBC)
    monkeypatch.setattr(decode, "DBC_DIR", dbc_dir)
    return dbc_dir


def _frames():
    lat = int(39.75 / 0.0001).to_bytes(4, "big", signed=True)
    lon = int(-105.22 / 0.0001).to_bytes(4, "big", signed=True)
    cold_lat = (0).to_bytes(4, "big", signed=True)
    return [
        (10, 1600, bytes([0x0B, 0xB8, 0x01, 0xF4, 0, 0, 0, 0])),
        (11, 1608, lat + lon),
        (12, 1608, cold_lat + lon),  # cold-start latitude, filtered out
        (13, 2047, bytes([1, 2, 3])),  # not in the DBC
        (14, 1600, bytes([1, 2])),  # too short to decode
        (15, 2047, b""),
        (16, 1600, bytes([0x0F, 0xA0, 0x02, 0x00, 0, 0, 0, 0])),
    ]


def _write_raw_text(path, frames):
    with open(path, "w") as raw:
        raw.write("header\nTimestamp,CANID,DataBytes\n")
        for timestamp, can_id, payload in frames:
            suffix = "".join(f",{byte}" for byte in payload)
            raw.write(f"{timestamp},{can_id}{suffix}\n")


def test_decode_frames_matches_make_known(tmp_path, test_dbc):
    raw = tmp_path / "raw.data"
    _write_raw_text(raw, _frames())
    from_text = tmp_path / "from_text.csv"
    decode.make_known(str(raw), str(from_text))

    from_frames = tmp_path / "from_frames.csv"
    from_frames_log = tmp_path / "from_frames.log"
    decode.decode_frames(_frames(), str(from_frames), str(from_frames_log))

    assert from_frames.read_bytes() == from_text.read_bytes()
    assert from_frames_log.read_bytes() == (tmp_path / "raw.log").read_bytes()

    result = pd.read_csv(from_frames)
    assert list(result.columns) == ["Timestamp", "CANID", "Sensor", "Value", "Unit"]
    assert set(result["Timestamp"]) == {10, 11, 12, 16}
    # The cold-start latitude at t=12 is filtered; its longitude is kept.
    assert result.loc[result["Sensor"] == "Latitude", "Value"].tolist() == [39.75]
    assert result.loc[result["Sensor"] == "Longitude", "Timestamp"].tolist() == [11, 12]


def test_make_known_log_lists_failed_frames(tmp_path, test_dbc):
    raw = tmp_path / "raw.data"
    _write_raw_text(raw, _frames())
    decode.make_known(str(raw), str(tmp_path / "out.csv"))
    log_lines = (tmp_path / "raw.log").read_text().splitlines()
    assert log_lines[0] == "Timestamp,CANID,DataBytes"
    assert [line.split(",")[0] for line in log_lines[1:]] == ["13", "14", "15"]


def test_formula_binary_pipeline_matches_text_round_trip(tmp_path, test_dbc):
    binary = bytearray(bytes([127 + 2]) + b"hi")
    for timestamp, can_id, payload in _frames():
        binary += (
            bytes([len(payload)])
            + timestamp.to_bytes(4, "big")
            + can_id.to_bytes(4, "big")
            + payload
        )
    source = tmp_path / "log.data"
    source.write_bytes(bytes(binary))

    out = tmp_path / "pipeline.csv"
    count = decode_to_csv(source, out, InputConfig(input_format=InputFormat.AUTO))
    assert count == len(_frames())

    raw = tmp_path / "raw.data"
    _write_raw_text(raw, _frames())
    expected = tmp_path / "expected.csv"
    decode.make_known(str(raw), str(expected))
    assert out.read_bytes() == expected.read_bytes()


def _legacy_convert_to_lineprotocol(file_name, file_output):
    """The pre-optimization implementation, kept here as the reference."""
    import os

    base, ending = os.path.splitext(file_name)
    unix = base + "_unixtime" + ending
    convert_unix_time.convert_to_unix(file_name, unix)
    with open(file_output, "w", encoding="utf-8", newline="\n") as out:
        df = pd.read_csv(unix).drop_duplicates()
        df.to_csv(unix, mode="w", index=False)
        for chunk in pd.read_csv(
            unix,
            usecols=["Timestamp", "CANID", "Sensor", "Value"],
            dtype=str,
            na_filter=False,
            chunksize=200000,
        ):
            chunk = chunk[
                (chunk["Timestamp"] != "")
                & (chunk["CANID"] != "")
                & (chunk["Sensor"] != "")
                & (chunk["Value"] != "")
            ]
            lines = (
                chunk["Sensor"].map(line_protocol.esc_measure)
                + ",tag1="
                + chunk["CANID"].map(line_protocol.esc_tag)
                + " field1="
                + chunk["Value"]
                + " "
                + chunk["Timestamp"]
            )
            out.write("\n".join(lines) + "\n")


def test_line_protocol_matches_legacy_implementation(tmp_path):
    rows = pd.DataFrame(
        {
            "Timestamp": [0, 0, 5, 5, 9, 9, 12, 12],
            "CANID": [1600, 1600, 1608, 1608, 1609, 1609, 1600, 1600],
            "Sensor": [
                "EngineSpeed",
                "EngineSpeed",  # exact duplicate row, dropped
                "Date",
                "Time",
                "wheel speed",
                "a,b=c",
                "ThrottlePosition",
                "Missing",
            ],
            "Value": ["3000", "3000", "120526", "143015000", "12", "1.5", "50.1", ""],
            "Unit": ["rpm", "rpm", "", "", "mph", "", "%", ""],
        }
    )
    for name in ("new", "old"):
        rows.to_csv(tmp_path / f"{name}.csv", index=False)

    line_protocol.convert_to_lineprotocol(
        str(tmp_path / "new.csv"), str(tmp_path / "new.line")
    )
    _legacy_convert_to_lineprotocol(
        str(tmp_path / "old.csv"), str(tmp_path / "old.line")
    )

    assert (tmp_path / "new.line").read_bytes() == (tmp_path / "old.line").read_bytes()
    assert (tmp_path / "new_unixtime.csv").read_bytes() == (
        tmp_path / "old_unixtime.csv"
    ).read_bytes()
    assert r"wheel\ speed,tag1=1609" in (tmp_path / "new.line").read_text()


def test_build_time_ref_matches_full_frame(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1, 2],
            "CANID": [1, 1],
            "Sensor": ["Date", "Time"],
            "Value": [60525, 143015000],
            "Unit": ["", ""],
        }
    ).to_csv(f, index=False)
    assert convert_unix_time.build_time_ref(str(f)) == (
        convert_unix_time.time_ref_from_frame(pd.read_csv(f))
    )
