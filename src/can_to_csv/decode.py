import cantools
import pandas as pd
import os

from pathlib import Path

from constants import *

from .filter_gps import filter_gps

"""
@author Magnus Van Zyl
Script to convert raw canbus data into readable data in a csv file in the format 'Timestamp,CANID,SENSOR,Value,Unit'.
"""


FIELDS = ["Timestamp", "CANID", "Sensor", "Value", "Unit"]

# Rows are buffered and flushed in batches; one write() per row dominated
# runtime on full endurance logs.
_WRITE_BATCH_ROWS = 50_000


def make_known(unknown_file_name: str, output_file_name: str):
    """
    Takes input file of unknown data and decodes it writing into a csv. Uses MF13Beta.dbc file.

    :param unknown_file_name: Name of the file with unknown/raw data
    :param output_file_name: Name of the csv file decoded data will be written to
    """
    file_name_base, ending = os.path.splitext(unknown_file_name)
    log_file = f"{file_name_base}.log"

    # === READS RAW DATA, FORMATTED (timestamp, canID, dataBytes) ===
    with open(unknown_file_name, "r") as unknown:
        unknown.readline()  # The first two lines are headers
        unknown.readline()
        frames = []
        for line in unknown:
            lineLst = line.split(",")
            frames.append(
                (
                    int(lineLst[0]),
                    int(lineLst[1]),
                    bytes(int(item) for item in lineLst[2:]),
                )
            )

    decode_frames(frames, output_file_name, log_file)


def decode_frames(frames, output_file: str, log_file: str | None = None):
    """
    Decodes (timestamp, can_id, payload) frames with the DBC and writes the
    'Timestamp,CANID,Sensor,Value,Unit' csv, then filters cold-start GPS rows.

    :param frames: Iterable of (timestamp: int, can_id: int, payload: bytes)
    :param output_file: Name of the csv file decoded data will be written to
    :param log_file: Optional file to record frames that failed to decode
    """
    # === LOAD DBC ===
    db = cantools.database.load_file(DBC_DIR / "MF13Beta.dbc")
    decode_message = db.decode_message

    # Signal names/units only depend on the CAN ID, so look them up once per
    # ID instead of once per frame. Units undefined in the dbc become "".
    signal_layouts = {}

    failed_lines = 0
    skipped_ids = []  # List of CAN IDs that are found in data_file but not in the dbc
    seen_skipped_ids = set()
    failed_lines_raw = []

    with open(output_file, "w") as file:
        file.write(f'{",".join(FIELDS)}\n')
        rows = []
        # === DECODES dataBytes AND WRITES THEM INTO CSV ===
        for timestamp, can_id, payload in frames:
            try:
                decoded = decode_message(can_id, payload)
                layout = signal_layouts.get(can_id)
                if layout is None:
                    message = db.get_message_by_frame_id(can_id)
                    layout = [
                        (signal.name, "" if signal.unit is None else signal.unit)
                        for signal in message.signals
                    ]
                    signal_layouts[can_id] = layout
            except Exception:
                failed_lines_raw.append((timestamp, can_id, payload))
                if can_id not in seen_skipped_ids:
                    seen_skipped_ids.add(can_id)
                    skipped_ids.append(can_id)
                failed_lines += 1
                continue

            prefix = f"{timestamp},{can_id},"
            for sensor, unit in layout:
                rows.append(f"{prefix}{sensor},{decoded.get(sensor)},{unit}\n")
            if len(rows) >= _WRITE_BATCH_ROWS:
                file.write("".join(rows))
                rows.clear()
        file.write("".join(rows))

    # === WRITES FAILED LINES TO LOG FILE ===
    if log_file is not None:
        with open(log_file, "w") as log:
            log.write("Timestamp,CANID,DataBytes\n")
            log.write(
                "".join(
                    f"{timestamp},{can_id},{payload}\n"
                    for timestamp, can_id, payload in failed_lines_raw
                )
            )

    # === FILTER OUT 'COLD-START' GPS DATA ===
    filter_gps(output_file)

    print(f"DATA DECODED INTO FILE: {output_file}")
    print(f"LINES SKIPPED: {failed_lines}")
    print(f"SKIPPED IDS: {skipped_ids}")


# unknown_file = 'EnduranceDayData (2).data'
# output_file = 'decoded_can_data_2.csv'
# make_known(unknown_file, output_file)
