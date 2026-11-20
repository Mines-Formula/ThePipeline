from datetime import datetime

import pandas as pd

from csv_to_influxdb.convert_unix_time import (
    _parse_time_value,
    build_time_ref,
    convert_to_unix,
)


def test_parse_normal_time():
    assert _parse_time_value(123456000) == (12, 34, 56)


def test_parse_short_time_pads():
    assert _parse_time_value(1000) == (0, 0, 1)


def test_parse_midnight():
    assert _parse_time_value(0) == (0, 0, 0)


def test_parse_end_of_day():
    assert _parse_time_value(235959000) == (23, 59, 59)


def test_build_time_ref_falls_back_to_now_when_no_date_or_time(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1],
            "CANID": ["1"],
            "Sensor": ["Speed"],
            "Value": ["55"],
            "Unit": ["mph"],
        }
    ).to_csv(f, index=False)
    before = datetime.now().timestamp() * 1000
    result = build_time_ref(str(f))
    after = datetime.now().timestamp() * 1000
    assert before <= result <= after


def test_build_time_ref_returns_correct_unix_ms(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [0, 0],
            "CANID": ["1", "1"],
            "Sensor": ["Date", "Time"],
            "Value": ["240525", "120000000"],
            "Unit": ["", ""],
        }
    ).to_csv(f, index=False)
    result = build_time_ref(str(f))
    expected = datetime(2025, 5, 24, 12, 0, 0).timestamp() * 1000
    assert abs(result - expected) < 1000


def test_build_time_ref_pads_short_date(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [0, 0],
            "CANID": ["1", "1"],
            "Sensor": ["Date", "Time"],
            "Value": ["60525", "0"],
            "Unit": ["", ""],
        }
    ).to_csv(f, index=False)
    result = build_time_ref(str(f))
    expected = datetime(2025, 5, 6, 0, 0, 0).timestamp() * 1000
    assert abs(result - expected) < 1000


def test_convert_to_unix_shifts_timestamps(tmp_path):
    f_in = tmp_path / "data.csv"
    f_out = tmp_path / "out.csv"
    pd.DataFrame(
        {
            "Timestamp": [500, 0, 0],
            "CANID": ["1", "1", "1"],
            "Sensor": ["Speed", "Date", "Time"],
            "Value": ["55", "240525", "120000000"],
            "Unit": ["mph", "", ""],
        }
    ).to_csv(f_in, index=False)
    convert_to_unix(str(f_in), str(f_out))
    result = pd.read_csv(f_out)
    expected_ref = int(datetime(2025, 5, 24, 12, 0, 0).timestamp() * 1000)
    speed_ts = result.loc[result["Sensor"] == "Speed", "Timestamp"].iloc[0]
    assert speed_ts == expected_ref + 500


def test_convert_to_unix_produces_output_file(tmp_path):
    f_in = tmp_path / "data.csv"
    f_out = tmp_path / "out.csv"
    pd.DataFrame(
        {
            "Timestamp": [1000],
            "CANID": ["1"],
            "Sensor": ["RPM"],
            "Value": ["3000"],
            "Unit": ["rpm"],
        }
    ).to_csv(f_in, index=False)
    convert_to_unix(str(f_in), str(f_out))
    assert f_out.exists()
    result = pd.read_csv(f_out)
    assert list(result.columns) == ["Timestamp", "CANID", "Sensor", "Value", "Unit"]
