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


def make_known(unknown_file_name: str, output_file_name: str):
    """
    Takes input file of unknown data and decodes it writing into a csv. Uses MF13Beta.dbc file.

    :param unknown_file_name: Name of the file with unknown/raw data
    :param output_file_name: Name of the csv file decoded data will be written to
    """
    # === LOAD DBC ===
    db = cantools.database.load_file(DBC_DIR / "MF13Beta.dbc")

    # === DEFINE HEADERS AND FILE PATHS ===
    fields = ["Timestamp", "CANID", "Sensor", "Value", "Unit"]
    data_file = unknown_file_name
    output_file = output_file_name
    file_name_base, ending = os.path.splitext(data_file)
    log_file = f"{file_name_base}.log"

    # === READS RAW DATA ===
    with open(data_file, "r") as unknown:
        header = unknown.readline()
        header = unknown.readline()
        data = []
        # print(header)  # This is just to get the header out of the way

        # === ADDS DATA TO LIST, FORMATTED [timestamp,canID,dataBytes]
        for line in unknown:
            dataLst = []
            lineLst = line.split(",")
            timestamp = int(lineLst[0])
            dataLst.append(timestamp)
            canID = int(lineLst[1])
            dataLst.append(canID)
            dataDecimal = lineLst[2:]
            for i in range(len(dataDecimal)):
                dataDecimal[i] = int(dataDecimal[i].strip())
            dataBytes = bytes(dataDecimal)
            dataLst.append(dataBytes)
            data.append(dataLst)

    failed_lines = 0
    skipped_ids = []  # List of CAN IDs that are found in data_file but not in the dbc
    failed_lines_raw = []
    # === DECODES dataBytes TO BE WRITTEN INTO CSV ===
    writable_lines = []
    for dataset in data:
        write_this = []
        values = []
        units = []
        sensors = []
        try:
            decoded = db.decode_message(dataset[1], dataset[2])
            message = db.get_message_by_frame_id(dataset[1])
            write_this.append(dataset[0])  # timestamp
            write_this.append(dataset[1])  # CAN ID
            for signal in message.signals:
                sensor = signal.name
                sensors.append(sensor)
                value = decoded.get(sensor)
                values.append(value)
                unit = signal.unit
                if unit == None:
                    unit = (
                        ""  # For sensors with undefined units in dbc, adds empty string
                    )
                units.append(unit)
            write_this.append(sensors)  # write_this[2]
            write_this.append(values)  # write_this[3]
            write_this.append(units)  # write_this[4]
            writable_lines.append(write_this)

        except Exception as e:
            failed_lines_raw.append(dataset)
            if dataset[1] not in skipped_ids:
                skipped_ids.append(dataset[1])
            failed_lines += 1
            continue

    # === CHECK FOR TIME VALUES ===
    #    no_time = True
    #    for line in writable_lines:
    #        for i in range(len(line[2])):
    #            if line[2][i] == "Time" and line[3][i] != None:
    #                no_time = False
    #    if no_time:
    #        raise ValueError("Time sensor with no value")

    # === WRITES FAILED LINES TO LOG FILE ===
    for i in range(len(failed_lines_raw)):
        for j in range(len(failed_lines_raw[i])):
            failed_lines_raw[i][j] = str(failed_lines_raw[i][j])
    with open(log_file, "w") as log:
        log.write("Timestamp,CANID,DataBytes\n")
        for failure in failed_lines_raw:
            log.write(f'{",".join(failure)}\n')

    # === WRITES DECODED DATA TO OUTPUT FILE ===
    with open(output_file, "w") as file:
        file.write(f'{",".join(fields)}\n')
        for line in writable_lines:
            time = line[0]
            canbus_id = line[1]
            for i in range(len(line[2])):
                sense = line[2][i]
                val = line[3][i]
                unt = line[4][i]
                data_entry = f"{time},{canbus_id},{sense},{val},{unt}\n"
                file.write(data_entry)

    # === FILTER OUT 'COLD-START' GPS DATA ===
    filter_gps(output_file)

    print(f"DATA DECODED INTO FILE: {output_file}")
    print(f"LINES SKIPPED: {failed_lines}")
    print(f"SKIPPED IDS: {skipped_ids}")


# unknown_file = 'EnduranceDayData (2).data'
# output_file = 'decoded_can_data_2.csv'
# make_known(unknown_file, output_file)
