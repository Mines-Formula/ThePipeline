from __future__ import annotations

import tempfile
import json
import time
import re
from typing import TYPE_CHECKING

from flask import Flask, jsonify, render_template, request
from pathlib import Path
from pathlib import PurePosixPath
from datetime import datetime
import os
import pandas as pd

from csv_to_influxdb import line_protocol, write_to_influxDB
from csv_to_rerun import csv_to_rerun
from telemetry_input import InputConfig, InputFormat, decode_to_csv
from telemetry_input.vehicle_classifier import (
    VALID_MANUAL_VEHICLE_TYPES,
    VALID_VEHICLE_TYPES,
    classify_csv_file,
)
from constants import *
from os import urandom
from .classification_store import (
    ClassificationStore,
    canonical_run_id,
    find_csv_for_run,
)
from .models import ConversionProgress, LimitedDict
import threading

from flask import send_from_directory

if TYPE_CHECKING:
    from typing import Any

    from werkzeug.datastructures.file_storage import FileStorage


DATA_FILENAME = "{}.data"
CSV_FILENAME = "{}.csv"
LINE_FILENAME = "{}.line"
DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
DEFAULT_MESSAGE_TYPE = "MF26.v3.CarFrame"

app = Flask(__name__)
app.config["tasks"] = LimitedDict(max_size=20)

CSV_DIR.mkdir(parents=True, exist_ok=True)
RERUN_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

_classification_stores: dict[Path, ClassificationStore] = {}
_classification_stores_lock = threading.Lock()


def _get_classification_store() -> ClassificationStore:
    manifest_path = Path(CLASSIFICATIONS_FILE)
    with _classification_stores_lock:
        store = _classification_stores.get(manifest_path)
        if store is None:
            store = ClassificationStore(manifest_path)
            _classification_stores[manifest_path] = store
        return store


_get_classification_store().seed_legacy_runs(RAW_DIR, CSV_DIR, RERUN_DIR)


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
                "metrics": progress.metrics,
            }
        ),
        200 if progress.finished else 202,
    )


def allowed_file(filename: str) -> bool:
    return filename.lower().endswith((".data", ".bin", ".pb", ".protobuf", ".binpb"))


def allowed_schema_file(filename: str) -> bool:
    return filename.lower().endswith(".proto")


@app.post("/upload")
def upload_data():
    telemetry_files = request.files.getlist("files")
    schema_files = request.files.getlist("schema_files")
    if not schema_files:
        uploaded_schema = request.files.get("schema_file")
        schema_files = [uploaded_schema] if uploaded_schema is not None else []
    if not telemetry_files:
        # Preserve compatibility with the previous frontend, which used each
        # filename as the multipart field name.
        telemetry_files = [
            file
            for key, file in request.files.items(multi=True)
            if key not in {"schema_file", "schema_files"}
        ]
    input_format = request.form.get("input_format", InputFormat.FORMULA_BINARY.value)

    if input_format == InputFormat.PROTOBUF_BINARY.value:
        return jsonify(
            {"error": "Single-message protobuf uploads are not supported; upload a full protobuf capture file."}
        ), 400

    if (
        input_format == InputFormat.PROTOBUF_DELIMITED.value
        and not any(file.filename for file in schema_files)
        and not request.form.get("schema")
        and not DEFAULT_SCHEMA_DIR.is_dir()
    ):
        return jsonify({"error": "The default MF26/v3 protobuf schema is unavailable."}), 500

    if not telemetry_files:
        return jsonify({"error": "No file uploaded"}), 400

    if not all(file.filename and allowed_file(file.filename) for file in telemetry_files):
        return jsonify({"error": "Invalid types uploaded."}), 400

    if any(
        not file.filename or not allowed_schema_file(file.filename)
        for file in schema_files
    ):
        return jsonify({"error": "Schema files must use the .proto extension."}), 400
    try:
        for file in schema_files:
            if file.filename:
                _safe_schema_path(file.filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    debug_enabled = request.form.get("debug") == "true"
    try:
        input_config = _input_config_from_form(
            request.form,
            schema_file=schema_files[0] if schema_files else None,
        )
        input_config.validate()
    except (ValueError, json.JSONDecodeError) as exc:
        return jsonify({"error": str(exc)}), 400

    # Read all file contents upfront to avoid closed file errors
    files_data = [(file.filename, file.read()) for file in telemetry_files]
    schema_data = [
        (file.filename, file.read()) for file in schema_files if file.filename
    ]
    if not schema_data:
        schema_data = None
    elif len(schema_data) == 1:
        schema_data = schema_data[0]

    conversion_thread = threading.Thread(
        target=convert_files,
        args=(files_data, schema_data),
        kwargs=dict(is_debug=debug_enabled, input_config=input_config),
        daemon=True,
        name=urandom(8).hex(),
    )

    app.config["tasks"][conversion_thread.name] = ConversionProgress(
        name=conversion_thread.name, progress="starting"
    )
    conversion_thread.start()

    return jsonify({"name": conversion_thread.name})


def convert_files(files, schema_data=None, **kwargs: Any):
    # The schema must remain available for the entire worker lifetime. It is
    # deliberately kept separate from telemetry files so it is never decoded
    # as a data capture.
    with tempfile.TemporaryDirectory() as schema_directory:
        if schema_data:
            schema_entries = (
                [schema_data]
                if isinstance(schema_data, tuple)
                else schema_data
            )
            raw_paths = [_safe_schema_path(filename) for filename, _ in schema_entries]
            relative_paths = _schema_relative_paths(schema_entries, raw_paths)
            schema_paths = []
            for (schema_filename, schema_content), raw_path in zip(
                schema_entries, relative_paths
            ):
                schema_path = Path(schema_directory) / raw_path
                schema_path.parent.mkdir(parents=True, exist_ok=True)
                schema_path.write_bytes(schema_content)
                schema_paths.append(schema_path)
            # An uploaded bundle is loaded as a directory, overriding the
            # bundled default while preserving repository-relative imports.
            kwargs["input_config"].schema = (
                Path(schema_directory)
                if len(schema_paths) > 1
                else schema_paths[0]
            )

        for index, (filename, content) in enumerate(files):
            # Create a temporary FileStorage-like object for conversion
            from io import BytesIO
            from werkzeug.datastructures import FileStorage

            file_like = FileStorage(
                stream=BytesIO(content), name=filename, filename=filename
            )

            convert_file(
                file_like,
                mark_finished=(index == len(files) - 1),
                **kwargs,
            )


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
            os.utime(path, (timestamp, timestamp))
        else:
            app.logger.error("Path does not exist when it should: %s", path)


def _safe_schema_path(filename: str) -> Path:
    """Convert a browser-provided relative filename into a safe local path."""
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("invalid schema file path")
    return Path(*path.parts)


def _schema_relative_paths(schema_entries, raw_paths: list[Path]) -> list[Path]:
    """Preserve schema import paths from browser directory uploads.

    Browser directory pickers usually add the selected directory as a wrapper.
    A user may also select the ``v3`` directory itself, even though MF26 source
    files import it as ``MF26/v3/...``. Infer that omitted import-root prefix
    from imports which name one of the uploaded files.
    """
    mf26_positions = [
        path.parts.index("MF26") for path in raw_paths if "MF26" in path.parts
    ]
    strip_prefix = (
        mf26_positions[0]
        if mf26_positions and len(set(mf26_positions)) == 1
        else 0
    )
    paths = [Path(*path.parts[strip_prefix:]) for path in raw_paths]
    uploaded = {path.as_posix() for path in paths}
    prefixes = set()
    for _, content in schema_entries:
        source = content.decode("utf-8", errors="ignore")
        for imported in re.findall(r'^\s*import(?:\s+(?:public|weak))?\s+"([^"]+)"', source, re.MULTILINE):
            for relative in uploaded:
                if imported.endswith("/" + relative):
                    prefixes.add(imported[: -len(relative)])

    # One consistent prefix identifies the directory that was omitted by the
    # picker. Never guess when schemas use more than one import root.
    if len(prefixes) == 1:
        prefix = Path(prefixes.pop())
        return [prefix / path for path in paths]
    return paths


def convert_file(
    file: FileStorage,
    is_debug: bool,
    input_config: InputConfig | None = None,
    *,
    mark_finished: bool = True,
) -> None:
    """
    Converts .data following this flow:
        .data (raw) -> .data (unknown) -> .csv (known) -> .line (known)

    Saves the intermediate .csv to CSV_PARENT_PATH

    :param: file The file to convert."""
    assert file.name

    base_name = Path(file.name).stem

    csv_filename = CSV_FILENAME.format(base_name)
    raw_data_filename = DATA_FILENAME.format("raw_" + base_name)
    line_filename = LINE_FILENAME.format(base_name)

    current_thread_name = threading.current_thread().name
    conversion_progress: ConversionProgress = app.config["tasks"][current_thread_name]

    with tempfile.TemporaryDirectory() as temporary_directory:
        parent_path = Path(temporary_directory)
        raw_data_path = RAW_DIR / raw_data_filename
        payload_path = raw_data_path
        csv_path = CSV_DIR / csv_filename
        line_path = parent_path / line_filename

        file.save(raw_data_path)
        conversion_progress.progress = "decoding input"
        decode_started = time.perf_counter()
        try:
            resolved_input_config = input_config or InputConfig(
                input_format=InputFormat.AUTO
            )
            record_count = decode_to_csv(
                raw_data_path.resolve(),
                csv_path.resolve(),
                resolved_input_config,
            )
        except Exception as exec:
            conversion_progress.exception = exec
            conversion_progress.finished = True
            return
        else:
            conversion_progress.metrics.update(
                {
                    "records_processed": record_count,
                    "records_skipped": resolved_input_config.runtime_metrics[
                        "records_skipped"
                    ],
                    "decode_seconds": round(time.perf_counter() - decode_started, 6),
                }
            )
            conversion_progress.progress = "csv_to_influxdb"

        # Protobuf frames carry Unix epoch timestamps; CAN logs carry
        # milliseconds since boot and need a reference time added.
        is_protobuf = resolved_input_config.input_format in {
            InputFormat.PROTOBUF_BINARY,
            InputFormat.PROTOBUF_DELIMITED,
        }

        try:
            line_protocol.convert_to_lineprotocol(
                str(csv_path.resolve()),
                str(line_path.resolve()),
                absolute_timestamps=is_protobuf,
            )
        except Exception as exec:
            conversion_progress.exception = exec
            conversion_progress.finished = True
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
            conversion_progress.finished = True
            return
        else:
            conversion_progress.progress = "creating rerun file"

        try:
            csv_to_rerun.convert(csv_path.resolve(), RERUN_DIR)
        except Exception as exec:
            conversion_progress.exception = exec
            conversion_progress.finished = True
            return

        conversion_progress.progress = "updating files timestamp"
        unix_time_csv_path = csv_path.with_name(
            csv_path.stem + "_unixtime" + csv_path.suffix
        )
        paths_to_update = (
            raw_data_path,
            csv_path,
            line_path,
            unix_time_csv_path,
            RERUN_DIR / f"{csv_path.stem}.rrd",
        )

        try:
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
        except Exception as exec:
            conversion_progress.exception = exec
            conversion_progress.finished = True
            return

        try:
            classification = resolved_input_config.runtime_metrics.get(
                "vehicle_classification"
            )
            if classification is None and csv_path.exists():
                classification = classify_csv_file(csv_path)
            if classification is not None:
                _get_classification_store().record_upload(
                    base_name, classification, csv_path
                )
                conversion_progress.metrics.setdefault("classifications", []).append(
                    {
                        "run_id": base_name,
                        "filename": file.name,
                        "vehicle_type": classification.vehicle_type,
                        "source": classification.source,
                        "reason": classification.reason,
                    }
                )
                conversion_progress.metrics["vehicle_type"] = (
                    classification.vehicle_type
                )
        except Exception as exec:
            conversion_progress.exception = exec
            conversion_progress.finished = True
            return

        if mark_finished:
            conversion_progress.progress = "done"
            conversion_progress.finished = True


def _input_config_from_form(form, schema_file=None) -> InputConfig:
    input_format = form.get("input_format", InputFormat.AUTO.value)
    is_protobuf = input_format in {
        InputFormat.PROTOBUF_BINARY.value,
        InputFormat.PROTOBUF_DELIMITED.value,
    }
    options = {
        "input_format": input_format,
        "schema": form.get("schema") or (
            "uploaded.proto"
            if schema_file
            else DEFAULT_SCHEMA_DIR
            if is_protobuf
            else None
        ),
        "generated_module": form.get("generated_module") or None,
        "message_type": form.get("message_type") or (
            DEFAULT_MESSAGE_TYPE if is_protobuf else None
        ),
        "length_prefix_encoding": "varint",
    }
    return InputConfig(**options)


@app.route("/files")
def list_files():
    TYPE_TO_DIR = {
        "csv": CSV_DIR,
        "rerun": RERUN_DIR,
        "raw": RAW_DIR,
    }

    type_ = request.args.get("type", "").casefold()

    try:
        directory = TYPE_TO_DIR[type_]
    except KeyError:
        return "Invalid type!", 400

    vehicle_filter = request.args.get("vehicle", "all").casefold()
    if vehicle_filter != "all" and vehicle_filter not in VALID_VEHICLE_TYPES:
        return "Invalid vehicle filter!", 400

    file_paths = [path for path in directory.iterdir() if path.is_file()]
    vehicle_types = _get_classification_store().get_vehicle_types_for_files(
        type_, [path.name for path in file_paths], CSV_DIR
    )

    files = []

    for path in file_paths:
        vehicle_type = vehicle_types[path.name]
        if vehicle_filter != "all" and vehicle_type != vehicle_filter:
            continue
        files.append(
            {
                "name": path.name,
                "run_id": canonical_run_id(type_, path.name),
                "timestamp": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "size": path.stat().st_size,
                "vehicle_type": vehicle_type,
            }
        )
    files.sort(key=lambda f: f["timestamp"], reverse=True)

    return jsonify(files)


def _is_safe_run_id(value: str) -> bool:
    return bool(value) and "/" not in value and "\\" not in value and ".." not in value


def _run_exists(run_id: str) -> bool:
    for file_type, directory in (
        ("csv", CSV_DIR),
        ("rerun", RERUN_DIR),
        ("raw", RAW_DIR),
    ):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and canonical_run_id(file_type, path.name) == run_id:
                return True
    return False


@app.post("/files/classify")
def classify_file_endpoint():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON request body is required."}), 400

    vehicle_type = str(payload.get("vehicle_type", "")).strip().lower()
    if vehicle_type not in VALID_MANUAL_VEHICLE_TYPES:
        return jsonify({"error": "vehicle_type must be 'ic' or 'ev'."}), 400

    raw_run_id = payload.get("run_id")
    raw_filename = payload.get("filename")
    if isinstance(raw_run_id, str) and raw_run_id.strip():
        candidate = raw_run_id.strip()
        if not _is_safe_run_id(candidate):
            return jsonify({"error": "Invalid run_id."}), 400
        run_id = candidate
    elif isinstance(raw_filename, str) and raw_filename.strip():
        filename = raw_filename.strip()
        if not _is_safe_run_id(filename):
            return jsonify({"error": "Invalid filename."}), 400
        file_type = str(payload.get("type", "")).strip().lower()
        run_id = canonical_run_id(file_type, filename)
    else:
        return jsonify({"error": "run_id or filename is required."}), 400

    if not _run_exists(run_id):
        return jsonify({"error": f"Run {run_id!r} was not found."}), 404

    csv_path = find_csv_for_run(CSV_DIR, run_id)
    _get_classification_store().set_manual(run_id, vehicle_type, csv_path)
    return jsonify(
        {
            "run_id": run_id,
            "vehicle_type": vehicle_type,
            "source": "manual",
        }
    )


@app.route("/files/download/<path:filename>")
def download_file(filename):
    type_ = request.args.get("type", "").casefold()
    type_to_dir = {
        "csv": CSV_DIR,
        "rerun": RERUN_DIR,
        "raw": RAW_DIR,
    }

    if type_:
        try:
            directory = type_to_dir[type_]
        except KeyError:
            return "Invalid type!", 400
    elif (RERUN_DIR / Path(filename)).exists():
        # Preserve compatibility with download links created before file types
        # were included in the URL.
        directory = RERUN_DIR
    else:
        directory = CSV_DIR

    return send_from_directory(directory, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
