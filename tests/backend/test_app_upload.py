from __future__ import annotations

import io
import pytest


@pytest.fixture
def upload_app(monkeypatch):
    import app.app as app_module

    captured = []

    def fake_convert_files(files, schema_data=None, **kwargs):
        captured.append((files, schema_data, kwargs))

    class ImmediateThread:
        name = "test-upload-thread"

        def __init__(self, target, args=(), kwargs=None, **_options):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(app_module, "convert_files", fake_convert_files)
    monkeypatch.setattr(app_module.threading, "Thread", ImmediateThread)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client(), captured


def test_upload_accepts_can_binary(upload_app):
    client, captured = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"can bytes"), "capture.data"),
            "input_format": "formula_binary",
            "debug": "true",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    files, schema, kwargs = captured[-1]
    assert files == [("capture.data", b"can bytes")]
    assert schema is None
    assert kwargs["input_config"].input_format.value == "formula_binary"


def test_upload_accepts_protobuf_capture_and_schema(upload_app):
    client, captured = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"protobuf bytes"), "capture.pb"),
            "schema_file": (io.BytesIO(b'syntax = "proto3"; message Test {}'), "test.proto"),
            "input_format": "protobuf_delimited",
            "message_type": "telemetry.Test",
            "debug": "true",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    files, schema, kwargs = captured[-1]
    assert files == [("capture.pb", b"protobuf bytes")]
    assert schema == ("test.proto", b'syntax = "proto3"; message Test {}')
    config = kwargs["input_config"]
    assert config.input_format.value == "protobuf_delimited"
    assert config.message_type == "telemetry.Test"
    assert config.schema is not None
    assert config.length_prefix_encoding.value == "varint"


def test_upload_accepts_binpd_protobuf_capture(upload_app):
    client, captured = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"protobuf bytes"), "capture.binpd"),
            "input_format": "protobuf_delimited",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    files, _, _ = captured[-1]
    assert files == [("capture.binpd", b"protobuf bytes")]


def test_protobuf_upload_defaults_to_bundled_mf26_v3_schema(upload_app):
    import app.app as app_module

    client, captured = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"protobuf bytes"), "capture.pb"),
            "input_format": "protobuf_delimited",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    _, schema, kwargs = captured[-1]
    assert schema is None
    assert kwargs["input_config"].schema == app_module.DEFAULT_SCHEMA_DIR
    assert kwargs["input_config"].message_type == "MF26.v3.CarFrame"


def test_upload_accepts_imported_schema_bundle(upload_app):
    client, captured = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"protobuf bytes"), "capture.pb"),
            "schema_files": [
                (io.BytesIO(b'import "MF26/v3/daq.proto";'), "MF26/v3/vehicle.proto"),
                (io.BytesIO(b'message DAQData {}'), "MF26/v3/daq.proto"),
            ],
            "input_format": "protobuf_delimited",
            "message_type": "MF26.v3.CarFrame",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    _, schema, kwargs = captured[-1]
    assert schema == [
        ("MF26/v3/vehicle.proto", b'import "MF26/v3/daq.proto";'),
        ("MF26/v3/daq.proto", b"message DAQData {}"),
    ]


def test_convert_files_strips_folder_wrapper_with_ignored_schema_paths(monkeypatch):
    import app.app as app_module

    captured = []

    def fake_convert_file(file, **kwargs):
        schema_root = kwargs["input_config"].schema
        captured.append(
            sorted(
                path.relative_to(schema_root).as_posix()
                for path in schema_root.rglob("*.proto")
            )
        )

    monkeypatch.setattr(app_module, "convert_file", fake_convert_file)
    config = app_module.InputConfig(
        input_format="protobuf_delimited",
        schema="placeholder.proto",
        message_type="MF26.v3.CarFrame",
    )

    app_module.convert_files(
        [("capture.pb", b"capture")],
        [
            ("ProtobufFiles/MF26/v3/vehicle.proto", b"vehicle"),
            ("ProtobufFiles/.venv/vendor.proto", b"vendor"),
        ],
        is_debug=True,
        input_config=config,
    )

    assert captured == [[".venv/vendor.proto", "MF26/v3/vehicle.proto"]]


def test_schema_paths_restore_a_missing_mf26_import_root():
    import app.app as app_module

    entries = [
        ("v3/vehicle.proto", b'import "MF26/v3/daq.proto";'),
        ("v3/daq.proto", b"message DAQData {}"),
    ]
    paths = [app_module._safe_schema_path(filename) for filename, _ in entries]

    assert [
        path.as_posix()
        for path in app_module._schema_relative_paths(entries, paths)
    ] == ["MF26/v3/vehicle.proto", "MF26/v3/daq.proto"]


def test_upload_rejects_invalid_schema_extension(upload_app):
    client, _ = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"protobuf bytes"), "capture.pb"),
            "schema_file": (io.BytesIO(b"not a schema"), "schema.txt"),
            "input_format": "protobuf_delimited",
            "message_type": "telemetry.Test",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "proto extension" in response.json["error"]


def test_upload_rejects_single_message_protobuf(upload_app):
    client, _ = upload_app

    response = client.post(
        "/upload",
        data={
            "files": (io.BytesIO(b"protobuf bytes"), "capture.pb"),
            "schema_file": (io.BytesIO(b"syntax = \"proto3\"; message Test {}"), "test.proto"),
            "input_format": "protobuf_binary",
            "message_type": "telemetry.Test",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "full protobuf capture" in response.json["error"]
