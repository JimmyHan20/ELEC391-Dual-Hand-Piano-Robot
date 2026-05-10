from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set


HAND_LEFT = "left"
HAND_RIGHT = "right"

EVENT_PRESS = "PRESS"
EVENT_RELEASE = "RELEASE"


@dataclass
class HandEvent:
    hand: str
    event_type: str
    global_time: float
    segment_index: int
    notes: List[str]
    action_type: str
    center_mode: Optional[str]
    center_slot: Optional[int]
    center_note: Optional[str]
    spread: int
    spread_profile: Optional[str]
    center_angle_override: Optional[float]
    spread_angle_override: Optional[float]
    finger_ids: List[str]
    note_finger_map: Dict[str, str]
    source_path: Optional[str]
    mid_available: Optional[bool]
    target_position: Dict[str, Any]
    sync_required: bool = False
    pair_event_id: Optional[str] = None
    held_from_time: Optional[float] = None
    held_until_time: Optional[float] = None
    partner_changes_here: bool = False


@dataclass
class HoldWindow:
    hand: str
    start_time: float
    end_time: float
    segment_index: int
    notes: List[str]
    action_type: str
    target_position: Dict[str, Any]
    partner_changes_inside: bool = False


@dataclass
class GlobalTimeSlot:
    global_time: float
    left_events: List[HandEvent] = field(default_factory=list)
    right_events: List[HandEvent] = field(default_factory=list)
    shared_press: bool = False
    changed_hands: List[str] = field(default_factory=list)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _norm_note(value: Any) -> str:
    return str("" if value is None else value).strip().upper()


def _norm_notes(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        note = _norm_note(values)
        return [] if note in ("", "REST") else [note]

    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        note = _norm_note(value)
        if note in ("", "REST"):
            continue
        if note not in seen:
            seen.add(note)
            out.append(note)
    return out


def _norm_fingers(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        finger = str(values).strip()
        return [] if not finger else [finger]

    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        finger = str("" if value is None else value).strip()
        if not finger or finger in seen:
            continue
        seen.add(finger)
        out.append(finger)
    return out


def _norm_note_finger_map(mapping: Any) -> Dict[str, str]:
    if not isinstance(mapping, dict):
        return {}

    out: Dict[str, str] = {}
    for note, finger in mapping.items():
        norm_note = _norm_note(note)
        norm_finger = str("" if finger is None else finger).strip()
        if norm_note in ("", "REST") or not norm_finger:
            continue
        out[norm_note] = norm_finger
    return out


def _normalize_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    center_angle_override = segment.get("center_angle_override")
    spread_angle_override = segment.get("spread_angle_override")

    return {
        "notes": _norm_notes(segment.get("notes", [])),
        "duration": max(0.0, _safe_float(segment.get("duration", 0.0))),
        "center_mode": segment.get("center_mode"),
        "center_slot": segment.get("center_slot"),
        "center_note": _norm_note(segment.get("center_note")) or None,
        "spread": _safe_int(segment.get("spread", 0), 0),
        "spread_profile": segment.get("spread_profile"),
        "center_angle_override": None if center_angle_override is None else _safe_float(center_angle_override),
        "spread_angle_override": None if spread_angle_override is None else _safe_float(spread_angle_override),
        "finger_ids": _norm_fingers(segment.get("finger_ids", [])),
        "action_type": str(segment.get("action_type", "unknown")),
        "note_finger_map": _norm_note_finger_map(segment.get("note_finger_map", {})),
        "source_path": segment.get("source_path"),
        "mid_available": segment.get("mid_available"),
    }


def _make_target_position(segment: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "center_mode": segment["center_mode"],
        "center_slot": segment["center_slot"],
        "center_note": segment["center_note"],
        "spread": segment["spread"],
        "spread_profile": segment["spread_profile"],
        "center_angle_override": segment["center_angle_override"],
        "spread_angle_override": segment["spread_angle_override"],
    }


def _make_event(
    *,
    hand: str,
    event_type: str,
    global_time: float,
    segment_index: int,
    segment: Dict[str, Any],
    sync_required: bool = False,
    pair_event_id: Optional[str] = None,
    held_from_time: Optional[float] = None,
    held_until_time: Optional[float] = None,
    partner_changes_here: bool = False,
) -> HandEvent:
    return HandEvent(
        hand=hand,
        event_type=event_type,
        global_time=round(float(global_time), 6),
        segment_index=segment_index,
        notes=list(segment["notes"]),
        action_type=segment["action_type"],
        center_mode=segment["center_mode"],
        center_slot=segment["center_slot"],
        center_note=segment["center_note"],
        spread=segment["spread"],
        spread_profile=segment["spread_profile"],
        center_angle_override=segment["center_angle_override"],
        spread_angle_override=segment["spread_angle_override"],
        finger_ids=list(segment["finger_ids"]),
        note_finger_map=dict(segment["note_finger_map"]),
        source_path=segment["source_path"],
        mid_available=segment["mid_available"],
        target_position=_make_target_position(segment),
        sync_required=sync_required,
        pair_event_id=pair_event_id,
        held_from_time=held_from_time,
        held_until_time=held_until_time,
        partner_changes_here=partner_changes_here,
    )


def build_hand_events(planned_segments: Iterable[Dict[str, Any]], hand: str) -> Dict[str, List[Any]]:
    """
    Convert one hand's planned segment list into explicit PRESS/RELEASE events.

    HOLD is kept as an implicit state:
    if a segment contains notes, the hand is understood to be holding those notes
    from PRESS time until RELEASE time.
    """
    hand_name = str(hand).strip().lower()
    if hand_name not in {HAND_LEFT, HAND_RIGHT}:
        raise ValueError(f"hand must be '{HAND_LEFT}' or '{HAND_RIGHT}', got {hand!r}")

    events: List[HandEvent] = []
    hold_windows: List[HoldWindow] = []

    current_time = 0.0
    for segment_index, raw_segment in enumerate(planned_segments):
        segment = _normalize_segment(raw_segment)
        start_time = current_time
        end_time = start_time + segment["duration"]

        if segment["notes"]:
            press_event = _make_event(
                hand=hand_name,
                event_type=EVENT_PRESS,
                global_time=start_time,
                segment_index=segment_index,
                segment=segment,
                held_until_time=end_time,
            )
            release_event = _make_event(
                hand=hand_name,
                event_type=EVENT_RELEASE,
                global_time=end_time,
                segment_index=segment_index,
                segment=segment,
                held_from_time=start_time,
            )
            events.extend([press_event, release_event])
            hold_windows.append(
                HoldWindow(
                    hand=hand_name,
                    start_time=round(start_time, 6),
                    end_time=round(end_time, 6),
                    segment_index=segment_index,
                    notes=list(segment["notes"]),
                    action_type=segment["action_type"],
                    target_position=_make_target_position(segment),
                )
            )

        current_time = end_time

    events.sort(key=lambda ev: (ev.global_time, 0 if ev.event_type == EVENT_RELEASE else 1, ev.segment_index))
    return {
        "events": events,
        "hold_windows": hold_windows,
    }


def build_global_timeline(
    planned_left: Iterable[Dict[str, Any]],
    planned_right: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a state-machine-friendly structure from pre-planned left/right hand segments.

    Output shape:
    - left_events: explicit PRESS/RELEASE events
    - right_events: explicit PRESS/RELEASE events
    - left_holds/right_holds: implicit hold windows
    - global_slots: merged global event times with events from both hands
    """
    left_data = build_hand_events(planned_left, HAND_LEFT)
    right_data = build_hand_events(planned_right, HAND_RIGHT)

    all_times = {
        *(event.global_time for event in left_data["events"]),
        *(event.global_time for event in right_data["events"]),
    }
    sorted_times = sorted(all_times)

    left_by_time: Dict[float, List[HandEvent]] = {}
    right_by_time: Dict[float, List[HandEvent]] = {}

    for event in left_data["events"]:
        left_by_time.setdefault(event.global_time, []).append(event)
    for event in right_data["events"]:
        right_by_time.setdefault(event.global_time, []).append(event)

    global_slots: List[GlobalTimeSlot] = []
    shared_press_counter = 1

    for slot_time in sorted_times:
        left_events = left_by_time.get(slot_time, [])
        right_events = right_by_time.get(slot_time, [])

        left_has_press = any(event.event_type == EVENT_PRESS for event in left_events)
        right_has_press = any(event.event_type == EVENT_PRESS for event in right_events)
        shared_press = left_has_press and right_has_press

        if shared_press:
            pair_event_id = f"sync_press_{shared_press_counter}"
            shared_press_counter += 1
            for event in left_events + right_events:
                if event.event_type == EVENT_PRESS:
                    event.sync_required = True
                    event.pair_event_id = pair_event_id

        changed_hands: List[str] = []
        if left_events:
            changed_hands.append(HAND_LEFT)
        if right_events:
            changed_hands.append(HAND_RIGHT)

        global_slots.append(
            GlobalTimeSlot(
                global_time=slot_time,
                left_events=left_events,
                right_events=right_events,
                shared_press=shared_press,
                changed_hands=changed_hands,
            )
        )

    _mark_partner_changes(left_data["hold_windows"], global_slots, HAND_LEFT)
    _mark_partner_changes(right_data["hold_windows"], global_slots, HAND_RIGHT)
    _propagate_partner_change_flags(left_data["events"], global_slots, HAND_LEFT)
    _propagate_partner_change_flags(right_data["events"], global_slots, HAND_RIGHT)

    return {
        "left_events": [asdict(event) for event in left_data["events"]],
        "right_events": [asdict(event) for event in right_data["events"]],
        "left_holds": [asdict(window) for window in left_data["hold_windows"]],
        "right_holds": [asdict(window) for window in right_data["hold_windows"]],
        "global_slots": [asdict(slot) for slot in global_slots],
    }


def _mark_partner_changes(
    hold_windows: List[HoldWindow],
    global_slots: List[GlobalTimeSlot],
    hand: str,
) -> None:
    partner = HAND_RIGHT if hand == HAND_LEFT else HAND_LEFT

    for hold in hold_windows:
        hold.partner_changes_inside = any(
            hold.start_time < slot.global_time < hold.end_time and partner in slot.changed_hands
            for slot in global_slots
        )


def _propagate_partner_change_flags(
    events: List[HandEvent],
    global_slots: List[GlobalTimeSlot],
    hand: str,
) -> None:
    partner = HAND_RIGHT if hand == HAND_LEFT else HAND_LEFT

    slot_partner_changes = {
        slot.global_time: (partner in slot.changed_hands)
        for slot in global_slots
    }
    for event in events:
        event.partner_changes_here = slot_partner_changes.get(event.global_time, False)


if __name__ == "__main__":
    planned_right = [
        {
            "notes": ["C4", "G4"],
            "duration": 1.20,
            "center_mode": "between_white",
            "center_slot": 1,
            "center_note": None,
            "spread": 3,
            "spread_profile": "HC_8VE",
            "center_angle_override": 1092.5,
            "spread_angle_override": 120.0,
            "finger_ids": ["Lw", "Rw"],
            "action_type": "chord",
            "note_finger_map": {"C4": "Lw", "G4": "Rw"},
            "source_path": "B",
            "mid_available": False,
        },
        {
            "notes": ["C4", "A4"],
            "duration": 1.20,
            "center_mode": "between_white",
            "center_slot": 5,
            "center_note": None,
            "spread": 1,
            "spread_profile": "HC_6TH",
            "center_angle_override": 950.0,
            "spread_angle_override": 40.0,
            "finger_ids": ["Lw", "Rw"],
            "action_type": "move_hand_and_chord",
            "note_finger_map": {"C4": "Lw", "A4": "Rw"},
            "source_path": "B",
            "mid_available": False,
        },
        {
            "notes": ["F4"],
            "duration": 0.60,
            "center_mode": "white_key",
            "center_slot": 0,
            "center_note": "F4",
            "spread": 0,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 0.0,
            "finger_ids": ["M"],
            "action_type": "move_hand",
            "note_finger_map": {"F4": "M"},
            "source_path": "A",
            "mid_available": True,
        },
        {
            "notes": ["E4"],
            "duration": 1.20,
            "center_mode": "white_key",
            "center_slot": 0,
            "center_note": "F4",
            "spread": 0,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 0.0,
            "finger_ids": ["Lb"],
            "action_type": "finger_only",
            "note_finger_map": {"E4": "Lb"},
            "source_path": "A",
            "mid_available": True,
        },
        {
            "notes": [],
            "duration": 0.40,
            "center_mode": "white_key",
            "center_slot": 2,
            "center_note": "G4",
            "spread": 0,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 0.0,
            "finger_ids": [],
            "action_type": "rest_reposition",
            "note_finger_map": {},
            "source_path": "A",
            "mid_available": True,
        },
    ]

    planned_left = [
        {
            "notes": ["C3", "E3"],
            "duration": 1.20,
            "center_mode": "white_key",
            "center_slot": 4,
            "center_note": "E3",
            "spread": 1,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 40.0,
            "finger_ids": ["Lw", "M"],
            "action_type": "chord",
            "note_finger_map": {"C3": "Lw", "E3": "M"},
            "source_path": "A",
            "mid_available": True,
        },
        {
            "notes": ["F2", "A3"],
            "duration": 1.20,
            "center_mode": "white_key",
            "center_slot": 8,
            "center_note": "G3",
            "spread": 2,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 80.0,
            "finger_ids": ["Lw", "Rw"],
            "action_type": "move_hand_and_chord",
            "note_finger_map": {"F2": "Lw", "A3": "Rw"},
            "source_path": "A",
            "mid_available": True,
        },
        {
            "notes": ["G2", "G3"],
            "duration": 1.20,
            "center_mode": "between_white",
            "center_slot": 7,
            "center_note": None,
            "spread": 3,
            "spread_profile": "HC_8VE",
            "center_angle_override": 983.0,
            "spread_angle_override": 120.0,
            "finger_ids": ["Lw", "Rw"],
            "action_type": "move_hand_and_chord",
            "note_finger_map": {"G2": "Lw", "G3": "Rw"},
            "source_path": "B",
            "mid_available": False,
        },
        {
            "notes": [],
            "duration": 0.40,
            "center_mode": "white_key",
            "center_slot": 6,
            "center_note": "F3",
            "spread": 0,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 0.0,
            "finger_ids": [],
            "action_type": "rest_hold",
            "note_finger_map": {},
            "source_path": "A",
            "mid_available": True,
        },
        {
            "notes": ["C3"],
            "duration": 1.20,
            "center_mode": "white_key",
            "center_slot": 2,
            "center_note": "C3",
            "spread": 0,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 0.0,
            "finger_ids": ["M"],
            "action_type": "move_hand",
            "note_finger_map": {"C3": "M"},
            "source_path": "A",
            "mid_available": True,
        },
    ]

    timeline = build_global_timeline(planned_left, planned_right)

    import json
    print(json.dumps(timeline, indent=2))
