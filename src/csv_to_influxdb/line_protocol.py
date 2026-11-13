import pandas as pd
import os
from csv_to_influxdb import convert_unix_time

"""
@author Will Turchin
Script to take formula CSV files and convert them into line protocal files
to be used in the influx database
"""


def esc_measure(s: str) -> str:
    return s.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")


def esc_tag(s: str) -> str:
    return esc_measure(s)


def _escape_unique(column: pd.Series, escape) -> pd.Series:
    return column.map({value: escape(value) for value in column.unique()})


def convert_to_lineprotocol(FILE_NAME: str, FILE_OUTPUT: str):
    """
    Converts csv to line protocol

    :param FILE_NAME: Name of Input File
    :param FILE_OUTPUT: Name of Output File
    """

    # convert input CSV to Unix Time
    FILE_NAME_BASE, ending = os.path.splitext(FILE_NAME)
    FILE_NAME_UNIX = FILE_NAME_BASE + "_unixtime" + ending

    # Load the csv once and shift it in memory. This matches what
    # convert_unix_time.convert_to_unix + re-reading its output produced, minus
    # two extra full passes over the file.
    df = pd.read_csv(FILE_NAME, dtype={"Timestamp": "int64"})
    df["Timestamp"] += int(convert_unix_time.time_ref_from_frame(df))
    df = df.drop_duplicates()
    df.to_csv(FILE_NAME_UNIX, mode="w", index=False)

    with open(FILE_OUTPUT, "w", encoding="utf-8", newline="\n") as out:
        for chunk in pd.read_csv(
            FILE_NAME_UNIX,
            usecols=["Timestamp", "CANID", "Sensor", "Value"],
            dtype={
                "Timestamp": "string",
                "CANID": "string",
                "Sensor": "string",
                "Value": "string",
            },
            na_filter=False,
            chunksize=200000,
        ):
            chunk = chunk[
                (chunk["Timestamp"] != "")
                & (chunk["CANID"] != "")
                & (chunk["Sensor"] != "")
                & (chunk["Value"] != "")
            ]
            # A log has millions of rows but only a few dozen distinct sensors
            # and CAN IDs, so escape each distinct value once.
            lines = (
                _escape_unique(chunk["Sensor"], esc_measure)
                + ",tag1="
                + _escape_unique(chunk["CANID"], esc_tag)
                + " field1="
                + chunk["Value"]
                + " "
                + chunk["Timestamp"]
            )
            out.write("\n".join(lines) + "\n")
