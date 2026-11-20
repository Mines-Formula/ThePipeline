import pandas as pd

from csv_to_rerun.csv_to_rerun import has_gps


def test_has_gps_true():
    df = pd.DataFrame({"Sensor": ["Latitude", "Longitude", "Speed"]})
    assert has_gps(df) is True


def test_has_gps_missing_longitude():
    df = pd.DataFrame({"Sensor": ["Latitude", "Speed"]})
    assert has_gps(df) is False


def test_has_gps_missing_latitude():
    df = pd.DataFrame({"Sensor": ["Longitude", "Speed"]})
    assert has_gps(df) is False


def test_has_gps_no_gps_sensors():
    df = pd.DataFrame({"Sensor": ["Speed", "RPM", "BrakeTemp"]})
    assert has_gps(df) is False


def test_has_gps_empty_dataframe():
    df = pd.DataFrame({"Sensor": []})
    assert has_gps(df) is False


def test_has_gps_ignores_nan_sensors():
    df = pd.DataFrame({"Sensor": ["Latitude", "Longitude", None]})
    assert has_gps(df) is True
