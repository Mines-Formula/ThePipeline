from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_input.vehicle_classifier import (
    VALID_MANUAL_VEHICLE_TYPES,
    VEHICLE_IC,
    VEHICLE_UNKNOWN,
    VehicleClassificationResult,
    classify_csv_file,
)

logger = logging.getLogger(__name__)


def canonical_run_id(file_type: str, filename: str) -> str:
    """Normalize a raw, csv, or rerun filename to its shared run identifier."""
    stem = Path(filename).stem
    if file_type == "raw":
        return stem.removeprefix("raw_")
    if file_type == "csv" and stem.endswith("_unixtime"):
        return stem[: -len("_unixtime")]
    return stem


def find_csv_for_run(csv_dir: Path, run_id: str) -> Path | None:
    """Locate the primary CSV (or unixtime fallback) associated with a run."""
    primary = csv_dir / f"{run_id}.csv"
    if primary.is_file():
        return primary
    unixtime = csv_dir / f"{run_id}_unixtime.csv"
    if unixtime.is_file():
        return unixtime
    return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _csv_fingerprint(csv_path: Path | None) -> tuple[int | None, int | None]:
    if csv_path is None or not csv_path.is_file():
        return None, None
    stat = csv_path.stat()
    return stat.st_mtime_ns, stat.st_size


def _build_entry(
    *,
    vehicle_type: str,
    source: str,
    manual_override: bool,
    csv_mtime_ns: int | None,
    csv_size: int | None,
    matched_ev: list[str] | tuple[str, ...] = (),
    matched_ic: list[str] | tuple[str, ...] = (),
    car_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "vehicle_type": vehicle_type,
        "source": source,
        "manual_override": manual_override,
        "matched_ev": list(matched_ev),
        "matched_ic": list(matched_ic),
        "car_ids": list(car_ids),
        "csv_mtime_ns": csv_mtime_ns,
        "csv_size": csv_size,
        "classified_at": _utc_now_iso(),
    }


def _is_cache_hit(
    entry: dict[str, Any] | None,
    csv_path: Path | None,
    mtime_ns: int | None,
    size: int | None,
) -> bool:
    if entry is None:
        return False
    stored_fp = (entry.get("csv_mtime_ns"), entry.get("csv_size"))
    if entry.get("manual_override"):
        return csv_path is None or stored_fp == (mtime_ns, size)
    return stored_fp == (mtime_ns, size)


def _collect_existing_run_ids(
    raw_dir: Path, csv_dir: Path, rerun_dir: Path
) -> set[str]:
    run_ids: set[str] = set()
    for file_type, directory in (
        ("raw", raw_dir),
        ("csv", csv_dir),
        ("rerun", rerun_dir),
    ):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and not path.name.startswith("."):
                run_ids.add(canonical_run_id(file_type, path.name))
    return run_ids


class ClassificationStore:
    """Thread-safe JSON persistence for run-level IC / EV / Unknown labels."""

    def __init__(self, manifest_path: Path | str) -> None:
        self.path = Path(manifest_path)
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._cache_fingerprint: tuple[int, int] | None = None

    def seed_legacy_runs(
        self, raw_dir: Path, csv_dir: Path, rerun_dir: Path
    ) -> int:
        """Seed pre-existing files as IC on first upgrade when no manifest exists."""
        with self._lock:
            if self.path.exists():
                return 0
            run_ids = _collect_existing_run_ids(raw_dir, csv_dir, rerun_dir)
            if not run_ids:
                return 0
            runs: dict[str, Any] = {}
            for run_id in sorted(run_ids):
                csv_path = find_csv_for_run(csv_dir, run_id)
                mtime_ns, size = _csv_fingerprint(csv_path)
                runs[run_id] = _build_entry(
                    vehicle_type=VEHICLE_IC,
                    source="legacy_migration",
                    manual_override=False,
                    csv_mtime_ns=mtime_ns,
                    csv_size=size,
                )
            self._write_manifest_unlocked({"version": 1, "runs": runs})
            return len(runs)

    def record_upload(
        self,
        run_id: str,
        result: VehicleClassificationResult,
        csv_path: Path | None = None,
    ) -> dict[str, Any]:
        """Persist automatic classification and post-utime CSV fingerprint."""
        mtime_ns, size = _csv_fingerprint(csv_path)
        entry = _build_entry(
            vehicle_type=result.vehicle_type,
            source=result.source,
            manual_override=False,
            csv_mtime_ns=mtime_ns,
            csv_size=size,
            matched_ev=result.matched_ev,
            matched_ic=result.matched_ic,
            car_ids=result.car_ids,
        )
        with self._lock:
            data = self._read_manifest_unlocked()
            data["runs"][run_id] = entry
            self._write_manifest_unlocked(data)
        return dict(entry)

    def set_manual(
        self,
        run_id: str,
        vehicle_type: str,
        csv_path: Path | None = None,
    ) -> dict[str, Any]:
        """Persist an explicit user override ('ic' or 'ev') for a run."""
        if vehicle_type not in VALID_MANUAL_VEHICLE_TYPES:
            raise ValueError(f"invalid manual vehicle_type: {vehicle_type!r}")
        mtime_ns, size = _csv_fingerprint(csv_path)
        with self._lock:
            data = self._read_manifest_unlocked()
            existing = data["runs"].get(run_id, {})
            entry = _build_entry(
                vehicle_type=vehicle_type,
                source="manual",
                manual_override=True,
                csv_mtime_ns=mtime_ns,
                csv_size=size,
                matched_ev=existing.get("matched_ev", []),
                matched_ic=existing.get("matched_ic", []),
                car_ids=existing.get("car_ids", []),
            )
            data["runs"][run_id] = entry
            self._write_manifest_unlocked(data)
        return dict(entry)

    def get_vehicle_types_for_files(
        self,
        file_type: str,
        filenames: list[str],
        csv_dir: Path,
    ) -> dict[str, str]:
        """Resolve vehicle_type per filename with zero scans for cached runs."""
        if not filenames:
            return {}
        run_by_name = {
            name: canonical_run_id(file_type, name) for name in filenames
        }
        unique_runs = sorted(set(run_by_name.values()))

        with self._lock:
            data = self._read_manifest_unlocked()
            runs: dict[str, Any] = data["runs"]
            dirty = False
            resolved_by_run: dict[str, str] = {}

            for run_id in unique_runs:
                csv_path = find_csv_for_run(csv_dir, run_id)
                mtime_ns, size = _csv_fingerprint(csv_path)
                entry = runs.get(run_id)
                if _is_cache_hit(entry, csv_path, mtime_ns, size):
                    resolved_by_run[run_id] = entry.get(
                        "vehicle_type", VEHICLE_UNKNOWN
                    )
                    continue

                dirty = True
                if csv_path is not None:
                    result = classify_csv_file(csv_path)
                    runs[run_id] = _build_entry(
                        vehicle_type=result.vehicle_type,
                        source=result.source,
                        manual_override=False,
                        csv_mtime_ns=mtime_ns,
                        csv_size=size,
                        matched_ev=result.matched_ev,
                        matched_ic=result.matched_ic,
                        car_ids=result.car_ids,
                    )
                    resolved_by_run[run_id] = result.vehicle_type
                else:
                    runs[run_id] = _build_entry(
                        vehicle_type=VEHICLE_IC,
                        source="legacy_default",
                        manual_override=False,
                        csv_mtime_ns=None,
                        csv_size=None,
                    )
                    resolved_by_run[run_id] = VEHICLE_IC

            if dirty:
                self._write_manifest_unlocked(data)

        return {name: resolved_by_run[run_by_name[name]] for name in filenames}

    def _read_manifest_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            self._cache = {"version": 1, "runs": {}}
            self._cache_fingerprint = None
            return self._cache

        stat = self.path.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and self._cache_fingerprint == fingerprint:
            return self._cache

        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Resetting unreadable classification manifest: %s", exc)
            parsed = {}

        if not isinstance(parsed, dict) or not isinstance(parsed.get("runs"), dict):
            parsed = {"version": 1, "runs": {}}
        else:
            parsed.setdefault("version", 1)

        self._cache = parsed
        self._cache_fingerprint = fingerprint
        return parsed

    def _write_manifest_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.stem}_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

        stat = self.path.stat()
        self._cache = data
        self._cache_fingerprint = (stat.st_mtime_ns, stat.st_size)
