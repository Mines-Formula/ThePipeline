import pandas as pd

from can_to_csv.filter_gps import filter_gps


def test_filter_gps_removes_out_of_bounds_latitude(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1, 2, 3],
            "CANID": [1, 1, 1],
            "Sensor": ["Latitude", "Latitude", "Speed"],
            "Value": [10.0, 39.0, 55.0],
            "Unit": ["", "", "mph"],
        }
    ).to_csv(f, index=False)
    filter_gps(str(f))
    result = pd.read_csv(f)
    latitudes = result.loc[result["Sensor"] == "Latitude", "Value"]
    assert len(latitudes) == 1
    assert all(latitudes.between(32, 46))


def test_filter_gps_removes_out_of_bounds_longitude(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1, 2, 3],
            "CANID": [1, 1, 1],
            "Sensor": ["Longitude", "Longitude", "Speed"],
            "Value": [-150.0, -105.0, 55.0],
            "Unit": ["", "", "mph"],
        }
    ).to_csv(f, index=False)
    filter_gps(str(f))
    result = pd.read_csv(f)
    longitudes = result.loc[result["Sensor"] == "Longitude", "Value"]
    assert len(longitudes) == 1
    assert all(longitudes.between(-118, -73))


def test_filter_gps_preserves_non_gps_rows(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1, 2, 3],
            "CANID": [1, 1, 1],
            "Sensor": ["Speed", "RPM", "BrakeTemp"],
            "Value": [55.0, 3000.0, 120.0],
            "Unit": ["mph", "rpm", "C"],
        }
    ).to_csv(f, index=False)
    filter_gps(str(f))
    result = pd.read_csv(f)
    assert len(result) == 3


def test_filter_gps_preserves_in_bounds_gps(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1, 2],
            "CANID": [1, 1],
            "Sensor": ["Latitude", "Longitude"],
            "Value": [39.7, -105.2],
            "Unit": ["", ""],
        }
    ).to_csv(f, index=False)
    filter_gps(str(f))
    result = pd.read_csv(f)
    assert len(result) == 2


def test_filter_gps_overwrites_file_in_place(tmp_path):
    f = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "Timestamp": [1],
            "CANID": [1],
            "Sensor": ["Latitude"],
            "Value": [5.0],
            "Unit": [""],
        }
    ).to_csv(f, index=False)
    filter_gps(str(f))
    result = pd.read_csv(f)
    assert result.loc[result["Sensor"] == "Latitude"].empty
