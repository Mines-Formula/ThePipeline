"""Compare protobuf-only decoding with end-to-end CSV conversion."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from telemetry_input import InputConfig, decode_to_csv, iter_decoded_records
from telemetry_input.pipeline import build_registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--message-type", required=True)
    parser.add_argument(
        "--format",
        choices=("protobuf_binary", "protobuf_delimited"),
        default="protobuf_delimited",
    )
    args = parser.parse_args()

    config = InputConfig(
        input_format=args.format,
        schema=args.schema,
        message_type=args.message_type,
    )
    registry = build_registry(config)

    started = time.perf_counter()
    decoded_count = sum(
        1 for _ in iter_decoded_records(args.input, config, registry)
    )
    decode_seconds = time.perf_counter() - started

    with tempfile.TemporaryDirectory() as temporary_directory:
        output = Path(temporary_directory) / "benchmark.csv"
        started = time.perf_counter()
        output_count = decode_to_csv(args.input, output, config, registry)
        total_seconds = time.perf_counter() - started

    print(
        json.dumps(
            {
                "decoded_records": decoded_count,
                "output_records": output_count,
                "protobuf_decode_seconds": round(decode_seconds, 6),
                "csv_pipeline_seconds": round(total_seconds, 6),
                "decode_fraction": (
                    round(decode_seconds / total_seconds, 4)
                    if total_seconds
                    else None
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
