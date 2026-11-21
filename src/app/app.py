from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING

from flask import Flask, jsonify, render_template, request
from pathlib import Path
from datetime import datetime
import os
import pandas as pd

from known_to_influxdb import line_protocol, write_to_influxDB
from unknown_to_known import decode
from csv_to_rerun import csv_to_rerun
from raw_to_unknown import deserializer
from constants import *
from os import urandom
from .models import ConversionProgress, LimitedDict
import threading

from flask import send_from_directory

if TYPE_CHECKING:
    from typing import Any

    from werkzeug.datastructures.file_storage import FileStorage


DATA_FILENAME = "{}.data"
CSV_FILENAME = "{}.csv"
LINE_FILENAME = "{}.line"

app = Flask(__name__)
app.config["tasks"] = LimitedDict(max_size=20)

CSV_DIR.mkdir(parents=True, exist_ok=True)
RERUN_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/")
def home():
    return render_template("index.html")


@app.get("/progress")
def get_progress():
    thread_name = request.args.get("name")

    if thread_name is None:
        return jsonify({"message": "No name parameter provided!"}), 400

    progress: ConversionProgress | None = app.config["tasks"].get(thread_name)
    if progress is None:
        return jsonify({"message": "Unknown task name."}), 404

    exception_present = progress.exception is not None

    return (
        jsonify(
            {
                "progress": progress.progress,
                "exception": {
                    "present": exception_present,
                    "type": str(progress.exception),
                },
            }
        ),
        200 if progress.finished else 202,
    )


def allowed_file(filename: str) -> bool:
    return filename.lower().endswith(".data")


@app.post("/upload")
def upload_data():
    if len(request.files) == 0:
        return jsonify({"error": "No file uploaded"}), 400

    if not any(
        file.filename and allowed_file(file.filename) for file in request.files.values()
    ):
        return jsonify({"error": "Invalid types uploaded."}), 400

    debug_enabled = request.form.get("debug") == "true"

    # Read all file contents upfront to avoid closed file errors
    files_data = [(file.filename, file.read()) for file in request.files.values()]

    conversion_thread = threading.Thread(
        target=convert_files,
        args=(files_data,),
        kwargs=dict(is_debug=debug_enabled),
        daemon=True,
        name=urandom(8).hex(),
    )

    app.config["tasks"][conversion_thread.name] = ConversionProgress(
        name=conversion_thread.name, progress="starting"
    )
    conversion_thread.start()

    return jsonify({"name": conversion_thread.name})


def convert_files(files, **kwargs: Any):
    current_thread_name = threading.current_thread().name
    try:
        for filename, content in files:
            from io import BytesIO
            from werkzeug.datastructures import FileStorage

            file_like = FileStorage(
                stream=BytesIO(content), name=filename, filename=filename
            )

            convert_file(file_like, **kwargs)
    except Exception as e:
        app.logger.exception(
            f"Thread {current_thread_name}: Unhandled exception during file conversion: {e}"
        )
        if current_thread_name in app.config["tasks"]:
            conversion_progress: ConversionProgress = app.config["tasks"][current_thread_name]
            conversion_progress.exception = e


def extract_first_timestamp(csv_path: Path) -> float | None:
    """Return the first timestamp found in the CSV as seconds since epoch."""
    try:
        df = pd.read_csv(csv_path, usecols=["Timestamp"], nrows=1, dtype=str)

        if df.empty:
            return None

        timestamp_raw = df.loc[0, "Timestamp"]

        if pd.isna(timestamp_raw):
            return None

        timestamp_numeric = pd.to_numeric(timestamp_raw, errors="coerce")

        if pd.isna(timestamp_numeric):
            return None

        timestamp_int = int(timestamp_numeric)

        return timestamp_int / 1000 if timestamp_int > 10**12 else float(timestamp_int)
    except (FileNotFoundError, ValueError, KeyError, pd.errors.EmptyDataError):
        return None


def update_file_timestamps(paths: tuple[Path, ...], timestamp: float) -> None:
    """Set mtime/atime on all existing paths in the tuple."""
    for path in paths:
        if path.exists():
            try:
                os.utime(path, (timestamp, timestamp))
            except Exception as e:
                app.logger.error("Failed to update timestamp for %s: %s", path, e)
        else:
            app.logger.error("Path does not exist when it should: %s", path)


def convert_file(file: FileStorage, is_debug: bool) -> None:
    """
    Converts .data following this flow:
        .data (raw) -> .data (unknown) -> .csv (known) -> .line (known)

    Saves the intermediate .csv to CSV_PARENT_PATH

    :param: file The file to convert."""
    assert file.name

    base_name = Path(file.name).stem

    csv_filename = CSV_FILENAME.format(base_name)
    raw_data_filename = DATA_FILENAME.format("raw_" + base_name)
    unknown_data_filename = DATA_FILENAME.format("unknown_" + base_name)
    line_filename = LINE_FILENAME.format(base_name)

    current_thread_name = threading.current_thread().name
    conversion_progress: ConversionProgress = app.config["tasks"][current_thread_name]

    with tempfile.TemporaryDirectory() as temporary_directory:
        parent_path = Path(temporary_directory)
        unknown_data_path = parent_path / unknown_data_filename

        raw_data_path = RAW_DIR / raw_data_filename
        payload_path = raw_data_path
        csv_path = CSV_DIR / csv_filename
        line_path = parent_path / line_filename

        file.save(raw_data_path)
        conversion_progress.progress = "deserialize"

        try:
            deserializer.deserialize(
                str(raw_data_path.resolve()), str(unknown_data_path.resolve())
            )
        except Exception as _:
            pass
        else:
            payload_path = unknown_data_path
            conversion_progress.progress = "making known"

        try:
            decode.make_known(str(payload_path.resolve()), str(csv_path.resolve()))
        except Exception as exec:
            conversion_progress.exception = exec
            return
        else:
            conversion_progress.progress = "line protocol "

        try:
            line_protocol.convert_to_lineprotocol(
                str(csv_path.resolve()),
                str(line_path.resolve()),
            )
        except Exception as exec:
            app.logger.error(
                f"Thread {current_thread_name}: Failed to convert to line protocol: {exec}"
            )
            conversion_progress.exception = exec
            return
        else:
            conversion_progress.progress = "uploading to influx"

        try:
            if is_debug:
                app.logger.info(
                    f"Thread {current_thread_name}: Skipping InfluxDB upload (Debug Mode)."
                )
            else:
                write_to_influxDB.write_to_influxDB(str(line_path.resolve()))

        except Exception as exec:
            conversion_progress.exception = exec
            return
        else:
            conversion_progress.progress = "creating rerun file"

        try:
            csv_to_rerun.convert(csv_path.resolve(), RERUN_DIR)
        except Exception as exec:
            conversion_progress.exception = exec
            return

        conversion_progress.progress = "updating files timestamp"
        unix_time_csv_path = csv_path.with_name(
            csv_path.stem + "_unixtime" + csv_path.suffix
        )
        paths_to_update = (
            raw_data_path,
            unknown_data_path,
            csv_path,
            line_path,
            unix_time_csv_path,
            RERUN_DIR / f"{csv_path.stem}.rrd",
        )

        if unix_time_csv_path.exists():
            timestamp = extract_first_timestamp(unix_time_csv_path)
            app.logger.info("Extracted timestamp: %s", timestamp)
        else:
            timestamp = None
            app.logger.error("Unix time CSV missing: %s", unix_time_csv_path)

        if timestamp is not None:
            app.logger.info("Updating timestamps...")
            update_file_timestamps(paths_to_update, timestamp)
        else:
            app.logger.error(
                "Failed to find valid timestamp for: %s",
                unix_time_csv_path,
            )

        conversion_progress.progress = "done"
        conversion_progress.finished = True


@app.route("/files")
def list_files():
    TYPE_TO_DIR = {
        "csv": CSV_DIR,
        "rerun": RERUN_DIR,
        "raw": RAW_DIR,
    }

    type_ = request.args.get("type", "").casefold()

    try:
        dir = TYPE_TO_DIR[type_]
    except KeyError:
        return "Invalid type!", 400

    files = []

    for path in dir.iterdir():
        if path.is_file():
            files.append(
                {
                    "name": path.name,
                    "timestamp": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )
    files.sort(key=lambda f: f["timestamp"], reverse=True)

    return jsonify(files)


@app.route("/files/download/<path:filename>")
def download_file(filename):
    if (RERUN_DIR / Path(filename)).exists():
        dir = RERUN_DIR
    else:
        dir = CSV_DIR

    return send_from_directory(dir, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
