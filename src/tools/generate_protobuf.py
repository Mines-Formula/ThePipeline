"""Generate Python protobuf modules with a checked compiler version."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

from grpc_tools import protoc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("schema_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--include", action="append", default=[], type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()

    version = subprocess.run(
        [sys.executable, "-m", "grpc_tools.protoc", "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if args.expected_version and version != args.expected_version:
        parser.error(
            f"protoc version mismatch: expected {args.expected_version!r}, got {version!r}"
        )

    root = args.schema_root.resolve()
    protos = sorted(root.rglob("*.proto"))
    if not protos:
        parser.error(f"no .proto files found under {root}")
    args.output.mkdir(parents=True, exist_ok=True)
    grpc_include = (
        Path(importlib.util.find_spec("grpc_tools").origin).parent / "_proto"
    )
    command = ["protoc", f"-I{root}", f"-I{grpc_include}"]
    command.extend(f"-I{path.resolve()}" for path in args.include)
    command.append(f"--python_out={args.output.resolve()}")
    command.extend(str(proto.relative_to(root)) for proto in protos)
    result = protoc.main(command)
    if result:
        parser.error(f"protoc failed with exit status {result}")
    print(f"Generated {len(protos)} schema module(s) with {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
