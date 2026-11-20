from __future__ import annotations

import hashlib
import importlib
import importlib.util
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType
from typing import Iterable

from google.protobuf import descriptor_pb2, descriptor_pool, message, message_factory

_DESCRIPTOR_CACHE: dict[str, bytes] = {}


class SchemaResolutionError(ValueError):
    """A protobuf schema could not be loaded or a message could not be resolved."""


class SchemaRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, type[message.Message]] = {}
        self._loaded_hashes: set[str] = set()

    def load_generated_module(
        self, module: str | ModuleType
    ) -> "SchemaRegistry":
        loaded = importlib.import_module(module) if isinstance(module, str) else module
        descriptor = getattr(loaded, "DESCRIPTOR", None)
        if descriptor is None:
            raise SchemaResolutionError(
                f"generated module {loaded.__name__!r} has no DESCRIPTOR"
            )
        self._register_file_descriptor(descriptor)
        return self

    def load_proto_file(
        self, path: str | Path, include_paths: Iterable[str | Path] = ()
    ) -> "SchemaRegistry":
        proto = Path(path).resolve()
        if not proto.is_file():
            raise SchemaResolutionError(f"schema file does not exist: {proto}")
        roots = self._include_roots(proto.parent, include_paths)
        self._compile_and_load([proto], roots)
        return self

    def load_proto_directory(
        self, path: str | Path, include_paths: Iterable[str | Path] = ()
    ) -> "SchemaRegistry":
        directory = Path(path).resolve()
        if not directory.is_dir():
            raise SchemaResolutionError(f"schema directory does not exist: {directory}")
        protos = sorted(self._iter_proto_files(directory))
        if not protos:
            raise SchemaResolutionError(f"schema directory contains no .proto files: {directory}")
        roots = self._include_roots(directory, include_paths)
        self._compile_and_load(protos, roots)
        return self

    @staticmethod
    def _iter_proto_files(directory: Path) -> Iterable[Path]:
        """Yield schema sources while ignoring bundled development artifacts.

        A browser directory upload can include a local virtual environment or
        build checkout. Those trees often contain copies of Google's standard
        .proto files, which conflict with grpcio-tools' built-in include tree.
        Hidden directories and common dependency/build directories are not
        part of the selected schema bundle and must not be compiled.
        """
        ignored_directories = {
            "__pycache__",
            ".git",
            ".venv",
            "build",
            "dist",
            "env",
            "node_modules",
            "venv",
        }
        for candidate in directory.rglob("*.proto"):
            relative_parts = candidate.relative_to(directory).parts
            if any(
                part.startswith(".") or part in ignored_directories
                for part in relative_parts[:-1]
            ):
                continue
            yield candidate

    def resolve(self, message_type: str) -> type[message.Message]:
        name = message_type.lstrip(".")
        found = self._classes.get(name)
        if found is not None:
            return found
        suffix_matches = [
            cls for full_name, cls in self._classes.items()
            if full_name == name or full_name.endswith("." + name)
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            raise SchemaResolutionError(
                f"message type {message_type!r} is ambiguous; use its package-qualified name"
            )
        available = ", ".join(sorted(self._classes)) or "<none>"
        raise SchemaResolutionError(
            f"unknown protobuf message type {message_type!r}; available: {available}"
        )

    @staticmethod
    def _include_roots(
        default: Path, include_paths: Iterable[str | Path]
    ) -> list[Path]:
        roots = [default.resolve()]
        roots.extend(Path(item).resolve() for item in include_paths)
        return list(dict.fromkeys(roots))

    def _compile_and_load(self, protos: list[Path], roots: list[Path]) -> None:
        digest = hashlib.sha256()
        for proto in protos:
            digest.update(str(proto).encode())
            digest.update(proto.read_bytes())
        for root in roots:
            digest.update(str(root).encode())
        for distribution in ("protobuf", "grpcio-tools"):
            try:
                digest.update(version(distribution).encode())
            except PackageNotFoundError:
                digest.update(b"<not-installed>")
        cache_key = digest.hexdigest()
        if cache_key in self._loaded_hashes:
            return

        descriptor_set = descriptor_pb2.FileDescriptorSet()
        cached = _DESCRIPTOR_CACHE.get(cache_key)
        if cached is not None:
            descriptor_set.ParseFromString(cached)
        else:
            try:
                grpc_include = (
                    Path(importlib.util.find_spec("grpc_tools").origin).parent
                    / "_proto"
                )
            except (ImportError, AttributeError, TypeError) as exc:
                raise SchemaResolutionError(
                    "loading .proto source requires grpcio-tools; generated modules "
                    "can be loaded without it"
                ) from exc

            all_roots = [*roots, grpc_include]
            arguments = []
            arguments.extend(f"-I{root}" for root in all_roots)
            with tempfile.TemporaryDirectory() as temporary_directory:
                descriptor_path = Path(temporary_directory) / "schema.pb"
                arguments.extend(
                    [
                        f"--descriptor_set_out={descriptor_path}",
                        "--include_imports",
                        "--include_source_info",
                    ]
                )
                for proto in protos:
                    relative = self._relative_proto(proto, roots)
                    arguments.append(relative.as_posix())
                result = subprocess.run(
                    [sys.executable, "-m", "grpc_tools.protoc", *arguments],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    diagnostic = (result.stderr or result.stdout).strip()
                    detail = f": {diagnostic}" if diagnostic else ""
                    raise SchemaResolutionError(
                        f"protoc failed with exit status {result.returncode} while loading "
                        f"{', '.join(str(item) for item in protos)}{detail}"
                    )
                serialized = descriptor_path.read_bytes()
                descriptor_set.ParseFromString(serialized)
                _DESCRIPTOR_CACHE[cache_key] = serialized

        pool = descriptor_pool.DescriptorPool()
        remaining = list(descriptor_set.file)
        while remaining:
            deferred = []
            for file_proto in remaining:
                try:
                    pool.Add(file_proto)
                except TypeError:
                    deferred.append(file_proto)
            if len(deferred) == len(remaining):
                missing = ", ".join(item.name for item in deferred)
                raise SchemaResolutionError(
                    f"could not resolve protobuf imports while loading: {missing}"
                )
            remaining = deferred

        for file_proto in descriptor_set.file:
            file_descriptor = pool.FindFileByName(file_proto.name)
            self._register_file_descriptor(file_descriptor)
        self._loaded_hashes.add(cache_key)

    @staticmethod
    def _relative_proto(proto: Path, roots: list[Path]) -> Path:
        for root in roots:
            try:
                return proto.relative_to(root)
            except ValueError:
                continue
        raise SchemaResolutionError(
            f"schema {proto} is not below a configured include path"
        )

    def _register_file_descriptor(self, file_descriptor) -> None:
        for descriptor in file_descriptor.message_types_by_name.values():
            self._register_descriptor(descriptor)

    def _register_descriptor(self, descriptor) -> None:
        cls = message_factory.GetMessageClass(descriptor)
        self._classes[descriptor.full_name] = cls
        for nested in descriptor.nested_types:
            if not nested.GetOptions().map_entry:
                self._register_descriptor(nested)
