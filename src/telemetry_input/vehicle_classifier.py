from __future__ import annotations

import csv
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.protobuf import descriptor
from google.protobuf import message as pb_message

from .models import DecodedRecord

VEHICLE_EV = "ev"
VEHICLE_IC = "ic"
VEHICLE_UNKNOWN = "unknown"

VALID_VEHICLE_TYPES = frozenset({VEHICLE_EV, VEHICLE_IC, VEHICLE_UNKNOWN})
VALID_MANUAL_VEHICLE_TYPES = frozenset({VEHICLE_EV, VEHICLE_IC})

# Explicit EV keywords requested from MF26/v3/ev.proto (EVStatus).
EV_CORE_FIELDS = frozenset(
    {
        "module1",
        "module2",
        "module3",
        "module4",
        "module5",
        "pre_charge_status",
    }
)

# All populated non-timestamp fields on MF26.v3.EVStatus count as EV signals.
EV_STATUS_FIELDS = EV_CORE_FIELDS | frozenset(
    {
        "ev_pwm_control",
        "bms_battery_pack",
        "m173_modulation_and_flux_info",
        "m172_torque_and_timer_info",
        "m194_read_write_param_response",
        "m193_read_write_param_command",
        "m171_fault_codes",
        "m170_internal_states",
        "m169_internal_voltages",
        "m168_flux_id_iq_info",
        "m167_voltage_info",
        "m166_current_info",
        "m165_motor_position_info",
        "m164_digital_input_status",
        "m163_analog_input_voltages",
        "m162_temperature_set_3",
        "m161_temperature_set_2",
        "m160_temperature_set_1",
        "m174_firmware_info",
        "m187_u2c_command_txd",
        "m188_u2c_message_rxd",
        "bms_current_limit",
        "m176_fast_info",
        "m175_diag_data_message",
        "vector_independent_sig_msg",
        "bms_heartbeat",
        "bms_error_state",
    }
)

# EV-specific fields in MF26.v3.VCUCommand (control.proto).
EV_VCU_COMMAND_FIELDS = frozenset(
    {
        "torque_request_nm",
        "derate_battery_temp",
        "derate_battery_charge",
        "derate_inverter_temp",
        "derate_inverter_current",
        "inv_enable_request",
        "inv_discharge_request",
        "vcu_error_state",
        "dereate_battery_current",
    }
)

# Strictly IC fields from MF26/v3/ecu.proto (ECUData).
IC_ECU_UNIQUE_LEAVES = frozenset(
    {
        "engine",
        "fuel",
        "ignition",
        "cut_state",
        "exhaust_load",
        "engine_status",
        "flex_fuel",
        "dual_fuel",
        "brake_steering_pressures",
        "mass_flow_shaft_data",
        "ecu_acceleration",
        "temp_voltage_data",
        "engine_speed_rpm",
        "inlet_manifold_pressure_kpa",
        "inlet_air_temp_c",
        "fuel_volume",
        "fuel_mixture_aim",
        "fuel_pressure_kpa",
        "injector_primary_duty_cycle",
        "engine_efficiency",
        "engine_load",
        "ignition_timing",
        "fuel_timing_primary",
        "ign_cut_count",
        "fuel_cut_count",
        "ign_cut_avg",
        "fuel_cut_avg",
        "inj_pulse_width_1",
        "ign_timing_state",
        "ign_cut_state",
        "oil_pressure_kpa",
        "exhaust_temp_c",
        "engine_load_avg",
        "engine_speed_limit_rpm",
        "oil_temp_c",
        "fuel_temp_c",
        "airbox_temp_c",
        "ecu_voltage_v",
        "fuel_used",
        "engine_run_time_s",
        "engine_uptime_s",
        "warn_engine_speed",
        "warn_oil_temp",
        "warn_oil_pressure",
        "warn_crankcase",
        "warn_fuel_pressure",
        "warn_knock",
        "engine_state",
        "fuel_pump_state",
        "anti_lag_state",
        "knock_state",
        "engine_over_run_state",
        "fuel_state",
        "fuel_composition",
        "flex_ignition",
        "inlet_mass_flow",
        "airbox_mass_flow",
        "fuel_flow",
    }
)

# Strictly IC channels in MF26/v3/pdm.proto (excludes shared fan/water_pump/rdaq/bspd).
IC_PDM_FIELDS = frozenset(
    {
        "shift_down_1_a",
        "shift_down_2_a",
        "shift_up_1_a",
        "shift_up_2_a",
        "fuel_pump_a",
        "injector_a",
        "ecu_a",
        "flex_fuel_a",
        "lambda_a",
        "coil_a",
        "shift_down_1_v",
        "shift_down_2_v",
        "shift_up_1_v",
        "shift_up_2_v",
        "fuel_pump_v",
        "injector_v",
        "ecu_v",
        "flex_fuel_v",
        "lambda_v",
        "coil_v",
    }
)

# Exact legacy DBC/CAN sensor names unique to IC (never match bare "rpm" substring).
IC_DBC_EXACT_SENSORS = frozenset(
    {"rpm", "afr", "lambda", "map", "maf", "egt", "tps", "knock"}
)

EV_KEYWORD_BOUNDARY_RE = re.compile(
    r"(?<![a-z0-9])(module[1-5]|pre_charge_status)(?![a-z0-9])",
    re.IGNORECASE,
)

IC_DBC_BOUNDARY_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"engine_?(?:speed|rpm)|fuel_?(?:pressure|temp|pump)|injector|ignition|"
    r"oil_?(?:pressure|temp)|exhaust_?temp|manifold_?pressure|airbox|"
    r"lambda|afr|egt|knock|anti_lag|flex_fuel|dual_fuel"
    r")(?![a-z0-9])",
    re.IGNORECASE,
)

CAR_ID_EV_RE = re.compile(r"^ev(?:[^a-z]|$)", re.IGNORECASE)
CAR_ID_IC_RE = re.compile(r"^ic(?:[^a-z]|$)", re.IGNORECASE)

_SHARED_CONTAINERS = frozenset({"DAQData", "GPSData", "SteeringWheel"})
_WRAPPER_CONTAINERS = frozenset({"CarFrame", "VehicleState"})


@dataclass(frozen=True, slots=True)
class VehicleClassificationResult:
    """Result of classifying a telemetry capture or CSV into EV, IC, or Unknown."""

    vehicle_type: str
    source: str
    matched_ev: tuple[str, ...] = ()
    matched_ic: tuple[str, ...] = ()
    car_ids: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        """Human-readable explanation when a file requires manual labeling."""
        if self.source == "conflict":
            return "Conflicting EV and IC signals detected"
        if self.source == "unclassified":
            return "No EV or IC signals detected"
        return self.source


def classify_car_id(car_id: str | None) -> str | None:
    """Classify a car_id string such as 'EV_01' or 'IC_01', or return None."""
    if not isinstance(car_id, str):
        return None
    cleaned = car_id.strip()
    if not cleaned:
        return None
    if CAR_ID_EV_RE.match(cleaned):
        return VEHICLE_EV
    if CAR_ID_IC_RE.match(cleaned):
        return VEHICLE_IC
    return None


def _is_timestamp_segments(segments: list[str]) -> bool:
    if not segments:
        return True
    if segments == ["timestamp"] or segments[-1] == "timestamp":
        return True
    return len(segments) >= 2 and segments[-2] == "timestamp" and segments[-1] in {
        "seconds",
        "nanos",
    }


class StreamVehicleClassifier:
    """Stateful classifier that inspects decoded records or CSV rows incrementally."""

    def __init__(self) -> None:
        self._ev_hits: set[str] = set()
        self._ic_hits: set[str] = set()
        self._ev_car_ids: set[str] = set()
        self._ic_car_ids: set[str] = set()
        self._seen_sensors: set[str] = set()
        self._seen_can_ids: set[str] = set()

    def has_conflict(self) -> bool:
        """Return True if mutually contradictory EV and IC signals were seen."""
        return bool(
            (self._ev_hits and self._ic_hits)
            or (self._ev_car_ids and self._ic_car_ids)
        )

    def observe_record(self, record: DecodedRecord) -> None:
        """Inspect one decoded protobuf or CAN record without altering the stream."""
        typed = record.metadata.get("typed_message")
        if isinstance(typed, pb_message.Message):
            self._inspect_protobuf_message(typed)
        elif isinstance(record.value, Mapping):
            self._inspect_mapping(record.message_type, record.value)

        normalized = record.normalized
        if normalized is not None:
            if normalized.sensor:
                self.observe_sensor_row(normalized.sensor, normalized.can_id)
            elif isinstance(normalized.can_id, str):
                self._observe_car_id(normalized.can_id)

    def observe_sensor_row(self, sensor: Any, can_id: Any = None) -> None:
        """Inspect a CSV Sensor and CANID pair with per-name deduplication."""
        if can_id is not None:
            can_str = str(can_id).strip()
            if can_str and can_str not in self._seen_can_ids:
                self._seen_can_ids.add(can_str)
                self._observe_car_id(can_str)
        if sensor:
            sensor_str = str(sensor).strip()
            if sensor_str and sensor_str not in self._seen_sensors:
                self._seen_sensors.add(sensor_str)
                self._observe_sensor_name(sensor_str)

    def finalize(self, source_prefix: str = "protobuf") -> VehicleClassificationResult:
        """Resolve accumulated primary and secondary signals into a classification."""
        matched_ev = tuple(sorted(self._ev_hits))
        matched_ic = tuple(sorted(self._ic_hits))
        car_ids = tuple(sorted(self._ev_car_ids | self._ic_car_ids))

        if matched_ev and matched_ic:
            return VehicleClassificationResult(
                VEHICLE_UNKNOWN, "conflict", matched_ev, matched_ic, car_ids
            )
        if matched_ev:
            return VehicleClassificationResult(
                VEHICLE_EV, f"{source_prefix}_ev_signals", matched_ev, (), car_ids
            )
        if matched_ic:
            return VehicleClassificationResult(
                VEHICLE_IC, f"{source_prefix}_ic_signals", (), matched_ic, car_ids
            )
        if self._ev_car_ids and self._ic_car_ids:
            return VehicleClassificationResult(
                VEHICLE_UNKNOWN, "conflict", (), (), car_ids
            )
        if self._ev_car_ids:
            return VehicleClassificationResult(
                VEHICLE_EV, "car_id", (), (), car_ids
            )
        if self._ic_car_ids:
            return VehicleClassificationResult(
                VEHICLE_IC, "car_id", (), (), car_ids
            )
        return VehicleClassificationResult(
            VEHICLE_UNKNOWN, "unclassified", (), (), car_ids
        )

    def _observe_car_id(self, value: Any) -> None:
        if not isinstance(value, str):
            return
        cleaned = value.strip()
        vehicle = classify_car_id(cleaned)
        if vehicle == VEHICLE_EV:
            self._ev_car_ids.add(cleaned)
        elif vehicle == VEHICLE_IC:
            self._ic_car_ids.add(cleaned)

    def _inspect_protobuf_message(self, msg: pb_message.Message) -> None:
        msg_name = msg.DESCRIPTOR.name
        if msg_name in _WRAPPER_CONTAINERS:
            self._inspect_wrapper_proto(msg)
        elif msg_name == "EVStatus":
            self._collect_ev_status_proto(msg)
        elif msg_name == "VCUCommand":
            self._collect_non_timestamp_proto(msg, self._ev_hits)
        elif msg_name == "ECUData":
            self._collect_non_timestamp_proto(msg, self._ic_hits)
        elif msg_name == "PDMData":
            self._collect_pdm_proto(msg)
        elif msg_name not in _SHARED_CONTAINERS:
            self._inspect_custom_proto(msg)

    def _inspect_wrapper_proto(self, msg: pb_message.Message) -> None:
        for field, field_value in msg.ListFields():
            name = field.name
            if name == "car_id":
                self._observe_car_id(field_value)
            elif name == "ev":
                self._collect_ev_status_proto(field_value)
            elif name == "ecu":
                self._collect_non_timestamp_proto(field_value, self._ic_hits)
            elif name == "pdm":
                self._collect_pdm_proto(field_value)
            elif name == "vehicle_state":
                self._inspect_wrapper_proto(field_value)

    def _collect_ev_status_proto(self, ev_msg: pb_message.Message) -> None:
        # ListFields() detects populated submessages such as pre_charge_status
        # even when all nested scalar fields hold proto3 default values (False).
        self._collect_non_timestamp_proto(ev_msg, self._ev_hits)

    @staticmethod
    def _collect_non_timestamp_proto(
        msg: pb_message.Message, target: set[str]
    ) -> None:
        for field, _ in msg.ListFields():
            if field.name != "timestamp":
                target.add(field.name.lower())

    def _collect_pdm_proto(self, pdm_msg: pb_message.Message) -> None:
        for field, field_value in pdm_msg.ListFields():
            if field.name in {"currents", "voltages"}:
                for sub_field, _ in field_value.ListFields():
                    sub_name = sub_field.name.lower()
                    if sub_name in IC_PDM_FIELDS:
                        self._ic_hits.add(sub_name)

    def _inspect_custom_proto(self, msg: pb_message.Message, depth: int = 0) -> None:
        if depth > 16:
            return
        for field, field_value in msg.ListFields():
            fname = field.name.lower()
            if fname == "car_id":
                self._observe_car_id(field_value)
                continue
            if fname == "timestamp":
                continue
            self._observe_sensor_name(fname)
            if field.type == descriptor.FieldDescriptor.TYPE_MESSAGE:
                self._recurse_custom_proto(field, field_value, depth + 1)

    def _recurse_custom_proto(
        self, field: descriptor.FieldDescriptor, value: Any, depth: int
    ) -> None:
        if field.is_repeated:
            is_map = (
                field.message_type is not None
                and field.message_type.GetOptions().map_entry
            )
            items = value.values() if is_map else value
            for item in items:
                if isinstance(item, pb_message.Message):
                    self._inspect_custom_proto(item, depth)
        elif isinstance(value, pb_message.Message):
            self._inspect_custom_proto(value, depth)

    def _inspect_mapping(
        self, message_type: str, mapping: Mapping[str, Any]
    ) -> None:
        msg_name = message_type.rsplit(".", 1)[-1] if message_type else ""
        if msg_name in _WRAPPER_CONTAINERS:
            self._inspect_wrapper_mapping(mapping)
        elif msg_name in {"EVStatus", "VCUCommand"}:
            self._collect_non_timestamp_mapping(mapping, self._ev_hits)
        elif msg_name == "ECUData":
            self._collect_non_timestamp_mapping(mapping, self._ic_hits)
        elif msg_name == "PDMData":
            self._collect_pdm_mapping(mapping)
        elif msg_name not in _SHARED_CONTAINERS:
            self._inspect_custom_mapping(mapping)

    def _inspect_wrapper_mapping(self, mapping: Mapping[str, Any]) -> None:
        self._observe_car_id(mapping.get("car_id"))
        if isinstance(mapping.get("ev"), Mapping):
            self._collect_non_timestamp_mapping(mapping["ev"], self._ev_hits)
        if isinstance(mapping.get("ecu"), Mapping):
            self._collect_non_timestamp_mapping(mapping["ecu"], self._ic_hits)
        if isinstance(mapping.get("pdm"), Mapping):
            self._collect_pdm_mapping(mapping["pdm"])
        if isinstance(mapping.get("vehicle_state"), Mapping):
            self._inspect_wrapper_mapping(mapping["vehicle_state"])

    @staticmethod
    def _collect_non_timestamp_mapping(
        mapping: Mapping[str, Any], target: set[str]
    ) -> None:
        for key in mapping:
            if key not in {"timestamp", "_oneofs"}:
                target.add(str(key).lower())

    def _collect_pdm_mapping(self, mapping: Mapping[str, Any]) -> None:
        for group_key in ("currents", "voltages"):
            group = mapping.get(group_key)
            if isinstance(group, Mapping):
                for key in group:
                    if str(key).lower() in IC_PDM_FIELDS:
                        self._ic_hits.add(str(key).lower())

    def _inspect_custom_mapping(
        self, mapping: Mapping[str, Any], depth: int = 0
    ) -> None:
        if depth > 16:
            return
        for key, child in mapping.items():
            if key in {"timestamp", "_oneofs"}:
                continue
            if key == "car_id":
                self._observe_car_id(child)
                continue
            self._observe_sensor_name(str(key))
            if isinstance(child, Mapping):
                self._inspect_custom_mapping(child, depth + 1)

    def _observe_sensor_name(self, sensor: str) -> None:
        segments = [
            re.sub(r"\[\d+\]$", "", part).lower()
            for part in sensor.split(".")
            if part
        ]
        if _is_timestamp_segments(segments):
            return
        if self._match_explicit_subtree(segments):
            return
        if self._match_ev_sensor(sensor, segments):
            return
        self._match_ic_sensor(sensor, segments)

    def _match_explicit_subtree(self, segments: list[str]) -> bool:
        if segments[0] == "ev":
            self._ev_hits.add(segments[1] if len(segments) > 1 else "ev")
            return True
        if len(segments) >= 2 and segments[:2] == ["vehicle_state", "ev"]:
            self._ev_hits.add(segments[2] if len(segments) > 2 else "ev")
            return True
        if segments[0] == "ecu":
            self._ic_hits.add(segments[1] if len(segments) > 1 else "ecu")
            return True
        if len(segments) >= 2 and segments[:2] == ["vehicle_state", "ecu"]:
            self._ic_hits.add(segments[2] if len(segments) > 2 else "ecu")
            return True
        return False

    def _match_ev_sensor(self, sensor: str, segments: list[str]) -> bool:
        for seg in segments:
            if seg in EV_STATUS_FIELDS or seg in EV_VCU_COMMAND_FIELDS:
                self._ev_hits.add(seg)
                return True
        match = EV_KEYWORD_BOUNDARY_RE.search(sensor)
        if match:
            self._ev_hits.add(match.group(1).lower())
            return True
        return False

    def _match_ic_sensor(self, sensor: str, segments: list[str]) -> bool:
        for seg in segments:
            if seg in IC_ECU_UNIQUE_LEAVES or seg in IC_PDM_FIELDS:
                self._ic_hits.add(seg)
                return True
        lowered = sensor.lower()
        if lowered in IC_DBC_EXACT_SENSORS:
            self._ic_hits.add(lowered)
            return True
        match = IC_DBC_BOUNDARY_RE.search(sensor)
        if match:
            self._ic_hits.add(match.group(0).lower())
            return True
        return False


def _resolve_csv_columns(header: list[str]) -> tuple[int, int]:
    lowered = [col.strip().lower() for col in header]
    sensor_idx = (
        lowered.index("sensor") if "sensor" in lowered else (2 if len(header) > 2 else -1)
    )
    can_id_idx = -1
    for candidate in ("canid", "can_id"):
        if candidate in lowered:
            can_id_idx = lowered.index(candidate)
            break
    if can_id_idx == -1 and len(header) > 1:
        can_id_idx = 1
    return sensor_idx, can_id_idx


def classify_csv_file(csv_path: str | Path) -> VehicleClassificationResult:
    """Stream-scan a decoded pipeline CSV once to classify it as EV, IC, or Unknown."""
    path = Path(csv_path)
    classifier = StreamVehicleClassifier()
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return classifier.finalize(source_prefix="csv")

        sensor_idx, can_id_idx = _resolve_csv_columns(header)
        for row in reader:
            sensor = row[sensor_idx] if 0 <= sensor_idx < len(row) else ""
            can_id = row[can_id_idx] if 0 <= can_id_idx < len(row) else ""
            classifier.observe_sensor_row(sensor, can_id)
            if classifier.has_conflict():
                break

    return classifier.finalize(source_prefix="csv")
