import pandas as pd
from datetime import datetime

"""
@author Will Turchin
Script to convert formula .csv Timestamp's into unix time using the "Time" sensor value
"""


def parse_date(date_val) -> tuple[int, int, int] | None:
    """
    Parses DDMMYY (e.g. 70820 or 61126) into (year, month, day).
    Returns None if invalid.
    """
    try:
        date_int = int(float(date_val))
        if date_int <= 0:
            return None
        date_str = f"{date_int:06d}"
        day = int(date_str[0:2])
        month = int(date_str[2:4])
        year = int(date_str[4:6]) + 2000
        if 1 <= month <= 12 and 1 <= day <= 31:
            return year, month, day
        return None
    except (ValueError, TypeError, OverflowError):
        return None


def parse_time(time_val) -> tuple[int, int, int, int] | None:
    """
    Parses time value (HHMMSS or HHMMSSsss) into (hour, minute, second, microsecond).
    Handles both big-endian 32-bit values (as decoded in reverse byte order by DBC)
    and un-swapped values.
    Returns None if invalid.
    """
    try:
        raw_int = int(float(time_val))
        if raw_int < 0:
            return None
    except (ValueError, TypeError, OverflowError):
        return None

    def try_decode(num: int) -> tuple[int, int, int, int] | None:
        if num < 0:
            return None
        # Could be HHMMSS (up to 6 digits)
        if num <= 235959:
            s = f"{num:06d}"
            h, m, sec = int(s[0:2]), int(s[2:4]), int(s[4:6])
            if 0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59:
                return h, m, sec, 0
        # Could be HHMMSSsss (up to 9 digits, with milliseconds)
        if num <= 235959999:
            s = f"{num:09d}"
            h, m, sec, ms = int(s[0:2]), int(s[2:4]), int(s[4:6]), int(s[6:9])
            if 0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59:
                return h, m, sec, ms * 1000
        return None

    # First, try byte-swapped (since cantools decodes GPS big-endian CAN data as little-endian)
    try:
        swapped = int.from_bytes(raw_int.to_bytes(4, byteorder="little"), byteorder="big")
        res_swapped = try_decode(swapped)
    except (OverflowError, ValueError):
        res_swapped = None

    if res_swapped is not None:
        return res_swapped

    # Next, try raw un-swapped
    return try_decode(raw_int)


def build_time_ref(file) -> float:
    """
    Extracts Date and Time from CSV and returns epoch timestamp in milliseconds.
    Falls back gracefully to current time if no valid Date and Time are found.
    """
    try:
        # Read only needed cols for efficiency
        df = pd.read_csv(file, usecols=["Sensor", "Value"])
        DateRow = df[df["Sensor"] == "Date"]
        TimeRow = df[df["Sensor"] == "Time"]

        if DateRow.empty or TimeRow.empty:
            return datetime.now().timestamp() * 1000

        d_parsed = None
        for d_val in reversed(DateRow["Value"].dropna().tolist()):
            d_parsed = parse_date(d_val)
            if d_parsed is not None:
                break

        t_parsed = None
        for t_val in reversed(TimeRow["Value"].dropna().tolist()):
            t_parsed = parse_time(t_val)
            if t_parsed is not None:
                break

        if d_parsed is not None and t_parsed is not None:
            try:
                year, month, day = d_parsed
                hour, minute, second, us = t_parsed
                dt = datetime(year, month, day, hour, minute, second, us)
                return dt.timestamp() * 1000
            except ValueError:
                pass
    except Exception:
        pass

    return datetime.now().timestamp() * 1000


def convert_to_unix(FILE_NAME: str, FILE_OUTPUT: str):
    """
    Takes input csv and converts timestamps into UNIX time format

    :param FILE_NAME: Name of input file
    :param FILE_OUTPUT: Name of output file
    """

    # Build the reference mapping once (optimization to make code run faster)
    time_ref: float = build_time_ref(FILE_NAME)
    header_written = False
    for chunk in pd.read_csv(
        FILE_NAME,
        dtype={
            "Timestamp": "int",
            "CANID": "string",
            "Sensor": "string",
            "Value": "string",
            "Unit": "string",
        },
        na_filter=False,
        chunksize=200000,
    ):
        chunk["Timestamp"] += int(time_ref)
        # write once, then append
        if not header_written:
            chunk.head(0).to_csv(FILE_OUTPUT, index=False)
            header_written = True

        chunk.to_csv(FILE_OUTPUT, mode="a", index=False, header=False)
