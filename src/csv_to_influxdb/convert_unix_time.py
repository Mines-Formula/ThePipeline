import pandas as pd
from datetime import datetime

"""
@author Will Turchin
Script to convert formula .csv Timestamp's into unix time using the "Time" sensor value
However, the "Time" values from the 2024 Comp data were so noisy that they could not be converted correctly into
usable Unix times, so this code is not currently being used 
"""


def _parse_time_value(time_value: int) -> tuple[int, int, int]:
    """
    Logged GPS time values arrive as HHMMSS.sss with the decimal removed.
    Pad from the left so short values like 1000 become 00:00:01.000.
    """
    raw_time = str(int(time_value)).zfill(9)
    clock_time = raw_time[:-3]

    hour = int(clock_time[0:2])
    minute = int(clock_time[2:4])
    second = int(clock_time[4:6])
    return hour, minute, second


def build_time_ref(file) -> float:
    # Read only needed cols for efficiency
    df = pd.read_csv(file)
    DateRow = df[(df["Sensor"] == "Date")]  # DDMMYY
    TimeRow = df[(df["Sensor"] == "Time")]  # HHMMSS.sss

    if DateRow.size == 0 or TimeRow.size == 0:
        return datetime.now().timestamp() * 1000

    Date: str = str(int(DateRow["Value"].iloc[-1]))
    Time: int = int(TimeRow["Value"].iloc[-1])

    # Edge case, if given 06/05/25 date value is "60525" convert to -> "060525"
    while ( len(Date) < 6 ):  
        Date = "0" + Date

    day: int = int(Date[0:2])
    month: int = int(Date[2:4])
    year: int = int(Date[4:6]) + 2000

    hour, minute, second = _parse_time_value(Time)

    # Edge case, if the data was read with the wrong endianness, swap and parse again
    if (hour > 23 or minute > 59 or second > 59):  
        n = int(Time).to_bytes(4, byteorder="little")
        Time = int.from_bytes(n, byteorder="big")
        print(f"Exception detected: time was read as big endian, switching to little endian")
        hour, minute, second = _parse_time_value(Time)

    ms: int = 0  # UNIX time doesn't need ms
    dt = datetime(year, month, day, hour, minute, second, 0)
    Unix_ms: float = dt.timestamp() * 1000
    return Unix_ms


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
