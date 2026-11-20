import rerun as rr  # NOTE: `rerun`, not `rerun-sdk`!
import pandas as pd
import numpy as np
import time
from pathlib import Path


def log_data(df: pd.DataFrame) -> None:
    sensors = df["Sensor"].dropna().unique()

    for sensor in sensors:
        label = str(sensor).replace("_", "\\ ")
        sensor_df = df[df["Sensor"] == sensor]

        times = sensor_df["time_s"].to_numpy()
        values = sensor_df["Value"].to_numpy()

        rr.send_columns(
            label,
            indexes=[rr.TimeColumn("time", duration=times)],
            columns=rr.Scalars.columns(scalars=values),
        )


def log_gps(df: pd.DataFrame) -> None:
    longitude_df = df[df["Sensor"] == "Longitude"]
    latitude_df = df[df["Sensor"] == "Latitude"]

    long_vals = longitude_df["Value"].to_numpy()
    lat_vals = latitude_df["Value"].to_numpy()
    times = longitude_df["time_s"].to_numpy()

    minimum_length = min(len(long_vals), len(lat_vals))

    long_vals = long_vals[:minimum_length]
    lat_vals = lat_vals[:minimum_length]
    times = times[:minimum_length]

    coordinates = np.column_stack((lat_vals, long_vals))

    for i in range(minimum_length):
        rr.set_time("time", duration=times[i])
        rr.log(
            "gps/position",
            rr.GeoPoints(
                lat_lon=[coordinates[i]],
                radii=rr.Radius.ui_points(5),
                colors=0xFF0000FF,
            ),
            rr.GeoLineStrings(
                lat_lon=[coordinates[: i + 1]],
                radii=rr.Radius.ui_points(1),
                colors=0x00FFFFAA,
            ),
        )


def has_gps(df: pd.DataFrame) -> bool:
    sensors = set(df["Sensor"].dropna().unique())
    return ("Longitude" in sensors) and ("Latitude" in sensors)


def convert(input_path: Path, output_dir: Path) -> None:
    rr.init(input_path.stem)

    df = pd.read_csv(input_path.resolve(), usecols=["Timestamp", "Sensor", "Value"])

    df = df[(df["Timestamp"] >= 90 * 1000) & (df["Timestamp"] <= 1000 * 1000)].copy()
    df["time_s"] = df["Timestamp"] / 1000.0

    rr.save(output_dir / Path(input_path.stem + ".rrd"))

    try:
        log_data(df)

        if has_gps(df):
            log_gps(df)
    finally:
        # Flush and close the .rrd file so nothing writes to it after we
        # return; otherwise a late flush overwrites the mtime the caller sets.
        rr.disconnect()
