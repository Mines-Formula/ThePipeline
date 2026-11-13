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

from csv_to_influxdb import line_protocol, write_to_influxDB
from csv_to_rerun import csv_to_rerun
from telemetry_input import InputConfig, InputFormat, decode_to_csv
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
DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
DEFAULT_MESSAGE_TYPE = "MF26.v3.CarFrame"

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
                "metrics": progress.metrics,
            }
        ),
        200 if progress.finished else 202,
    )


def allowed_file(filename: str) -> bool:
    return filename.lower().endswith((".data", ".bin", ".pb", ".protobuf", ".binpd"))


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

        for filename, content in files:
            # Create a temporary FileStorage-like object for conversion
            from io import BytesIO
            from werkzeug.datastructures import FileStorage

            file_like = FileStorage(
                stream=BytesIO(content), name=filename, filename=filename
            )

            convert_file(file_like, **kwargs)


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

        try:
            line_protocol.convert_to_lineprotocol(
                str(csv_path.resolve()),
                str(line_path.resolve()),
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
        else:
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
                    "size": path.stat().st_size,
                }
            )
    files.sort(key=lambda f: f["timestamp"], reverse=True)

    return jsonify(files)


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
