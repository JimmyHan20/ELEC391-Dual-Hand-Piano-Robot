from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from Core_function.music_theory_tools import choose_musical_subset

try:
    from Song.midi_loader import load_score_from_midi
except Exception:
    load_score_from_midi = None

try:
    from Core_function.hand_planner import HandPlanner, Pose
except Exception as exc:  # pragma: no cover
    raise ImportError(
        "hand_planner_dual_path.py requires Core_function.hand_planner.HandPlanner"
    ) from exc


NOTE_NAMES_SHARP = [
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B",
]
WHITE_PITCHES = {"C", "D", "E", "F", "G", "A", "B"}
FLAT_TO_SHARP = {
    "DB": "C#",
    "EB": "D#",
    "GB": "F#",
    "AB": "G#",
    "BB": "A#",
}

# Hardware spread command levels and their actual target angles.
# This must stay aligned with the STM / hand worker side.
HW_SPREAD_LEVEL_TO_ANGLE = {
    0: 0.0,
    1: 40.0,   # 6th profile
    2: 80.0,   # one-key white-center profile
    3: 120.0,  # octave profile
    4: 160.0,  # two-key white-center profile
}


def _path_a_pose_spread_to_hw_level(spread_idx: int) -> int:
    """Map internal white-center pose spreads to real hardware command levels."""
    return {
        0: 0,
        1: 2,
        2: 4,
    }.get(int(spread_idx), int(spread_idx))


def _hw_spread_angle_from_level(level: Optional[int]) -> Optional[float]:
    if level is None:
        return None
    try:
        return float(HW_SPREAD_LEVEL_TO_ANGLE[int(level)])
    except Exception:
        return None


# ============================================================
# Basic note helpers (kept local so this file is self-contained)
# ============================================================

def normalize_note(note: str) -> str:
    s = str(note).strip().upper()
    if s == "REST":
        return "REST"

    if len(s) < 2:
        raise ValueError(f"Bad note: {note!r}")

    if len(s) >= 3 and s[1] == "B":
        pitch = s[:2]
        octave = s[2:]
        pitch = FLAT_TO_SHARP.get(pitch, pitch)
        return f"{pitch}{octave}"

    return s


def note_to_midi(note: str) -> int:
    n = normalize_note(note)
    if n == "REST":
        raise ValueError("REST has no MIDI number.")

    if len(n) >= 3 and n[1] == "#":
        pitch = n[:2]
        octave = int(n[2:])
    else:
        pitch = n[:1]
        octave = int(n[1:])

    if pitch not in NOTE_NAMES_SHARP:
        raise ValueError(f"Unsupported pitch: {note!r}")

    return NOTE_NAMES_SHARP.index(pitch) + (octave + 1) * 12


def midi_to_note(midi_num: int) -> str:
    pitch = NOTE_NAMES_SHARP[midi_num % 12]
    octave = (midi_num // 12) - 1
    return f"{pitch}{octave}"


def get_pitch_name(note: str) -> str:
    n = normalize_note(note)
    if n == "REST":
        return "REST"
    if len(n) >= 3 and n[1] == "#":
        return n[:2]
    return n[:1]


def is_white_note(note: str) -> bool:
    return get_pitch_name(note) in WHITE_PITCHES


def _event_notes_to_list(notes_obj) -> List[str]:
    if notes_obj is None:
        return []

    if isinstance(notes_obj, str):
        nn = normalize_note(notes_obj)
        return [] if nn == "REST" else [nn]

    out = []
    for x in notes_obj:
        nn = normalize_note(x)
        if nn != "REST":
            out.append(nn)
    return out


def _unique_sorted_notes(notes: Sequence[str]) -> List[str]:
    uniq: List[str] = []
    seen = set()
    for n in notes:
        nn = normalize_note(n)
        if nn == "REST":
            continue
        if nn not in seen:
            uniq.append(nn)
            seen.add(nn)
    uniq.sort(key=note_to_midi)
    return uniq


def _coerce_score_event(item) -> Tuple[List[str], float]:
    if not isinstance(item, (tuple, list)) or len(item) < 2:
        return [], 0.01

    notes_obj, dur = item[0], item[1]
    notes = _event_notes_to_list(notes_obj)
    try:
        duration = max(0.01, float(dur))
    except Exception:
        duration = 0.01
    return _unique_sorted_notes(notes), duration


def _finger_sort_key(fid: str) -> int:
    order = {"Lw": 0, "Lb": 1, "M": 2, "Rb": 3, "Rw": 4}
    return order.get(str(fid), 99)


def _ordered_unique_finger_ids(finger_ids: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for fid in sorted(list(finger_ids), key=_finger_sort_key):
        if fid not in seen:
            out.append(fid)
            seen.add(fid)
    return out


# ============================================================
# Dual-path profile / event dataclasses
# ============================================================

@dataclass(frozen=True)
class HalfCenterProfile:
    """
    Path B profile: center is BETWEEN two white keys.

    white_span_steps:
        distance in white-key steps between the two white notes.
        Examples:
            C -> F : 3  (4th)
            C -> A : 5  (6th)
            C -> C : 7  (8ve)

    spread_level:
        real hardware SF level to send for this profile.

    small_motor_angle:
        actual target angle expected from that hardware level.
    """
    name: str
    white_span_steps: int
    spread_level: int
    small_motor_angle: float
    priority: int = 0


@dataclass(frozen=True)
class GeometryState:
    """
    Unified DP state for Path A + Path B.

    center_slot:
        doubled white grid.
        white key idx i -> slot 2*i
        gap between idx i and i+1 -> slot 2*i+1
    """
    mode: str                    # "white_key" or "between_white"
    center_slot: int
    spread_key: str              # e.g. "A:2" or "B:HC_8VE"
    finger_ids: Tuple[str, ...]


@dataclass
class GeometryCandidate:
    mode: str                    # "white_key" or "between_white"
    center_slot: int
    spread_key: str
    finger_ids: List[str]
    mapping: Dict[str, str]
    notes: List[str]
    duration: float
    is_rest: bool
    center_note: Optional[str] = None
    spread_level: Optional[int] = None
    spread_profile: Optional[str] = None
    center_angle_override: Optional[float] = None
    spread_angle_override: Optional[float] = None
    pair_priority: int = 99
    source_path: str = "A"


@dataclass
class DualPathPlannerConfig:
    # Path A spread-angle mapping (legacy / white-center profiles)
    path_a_spread_angle_map: Dict[int, float]

    # Path B profiles (half-center special shapes)
    path_b_profiles: Dict[int, HalfCenterProfile]

    # If both paths are valid, Path A gets a small bonus by default.
    path_a_bonus: float = 0.20
    path_b_penalty: float = 0.20

    # Cost model
    cost_hand_move_per_white: float = 10.0
    cost_small_motor_angle_per_deg: float = 0.03
    cost_profile_switch: float = 0.5
    cost_finger_change: float = 0.6
    rest_move_discount: float = 0.50

    # Path B v1 only supports 2-note white-white chords using Lw + Rw.
    allow_path_b_two_white_only: bool = True


def default_dual_path_config() -> DualPathPlannerConfig:
    return DualPathPlannerConfig(
        path_a_spread_angle_map=dict(HW_SPREAD_LEVEL_TO_ANGLE),
        path_b_profiles={
            5: HalfCenterProfile(
                "HC_6TH",
                white_span_steps=5,
                spread_level=1,
                small_motor_angle=HW_SPREAD_LEVEL_TO_ANGLE[1],
                priority=1,
            ),
            7: HalfCenterProfile(
                "HC_8VE",
                white_span_steps=7,
                spread_level=3,
                small_motor_angle=HW_SPREAD_LEVEL_TO_ANGLE[3],
                priority=2,
            ),
        },
        path_a_bonus=0.20,
        path_b_penalty=0.20,
    )


# ============================================================
# Geometry conversion helpers
# ============================================================

def white_idx_to_slot(white_idx: int) -> int:
    return 2 * int(white_idx)


def slot_to_white_idx_if_exact(center_slot: int) -> Optional[int]:
    return center_slot // 2 if (center_slot % 2 == 0) else None


def center_note_from_slot(planner: HandPlanner, center_slot: int) -> Optional[str]:
    white_idx = slot_to_white_idx_if_exact(center_slot)
    if white_idx is None:
        return None
    if 0 <= white_idx < len(planner.white_keys):
        return planner.white_keys[white_idx]
    return None


def center_slot_to_angle(center_slot: int, planner: HandPlanner, note_to_angle_fn) -> Optional[float]:
    """
    Convert a center slot to a big-motor angle.
    - even slot -> exact white key
    - odd slot  -> average of neighboring white-key angles
    """
    if note_to_angle_fn is None:
        return None

    # exact white key
    center_note = center_note_from_slot(planner, center_slot)
    if center_note is not None:
        try:
            return float(note_to_angle_fn(center_note))
        except Exception:
            return None

    # between two white keys
    left_idx = center_slot // 2
    right_idx = left_idx + 1
    if left_idx < 0 or right_idx >= len(planner.white_keys):
        return None

    left_note = planner.white_keys[left_idx]
    right_note = planner.white_keys[right_idx]
    try:
        a0 = float(note_to_angle_fn(left_note))
        a1 = float(note_to_angle_fn(right_note))
    except Exception:
        return None

    return 0.5 * (a0 + a1)


# ============================================================
# Candidate generation
# ============================================================

def _path_a_candidates(planner: HandPlanner, notes: List[str], duration: float, cfg: DualPathPlannerConfig) -> List[GeometryCandidate]:
    wanted = _unique_sorted_notes(notes)
    if not wanted:
        return []

    out: List[GeometryCandidate] = []
    seen = set()

    direct_solver = getattr(planner, "_direct_pose_solutions_for_notes", None)
    if callable(direct_solver):
        raw = direct_solver(wanted)
    else:
        raw = []
        for pose in planner.valid_poses:
            mapping = {}
            for pose_note, finger_id in planner._pose_note_map(pose):
                nn = normalize_note(pose_note)
                if nn in wanted and nn not in mapping:
                    mapping[nn] = finger_id
            if len(mapping) == len(wanted):
                raw.append({"pose": pose, "mapping": mapping, "pair_priority": 99})

    for sol in raw:
        pose = sol["pose"]
        mapping = dict(sol["mapping"])
        finger_ids = _ordered_unique_finger_ids(mapping[n] for n in wanted)
        key = (pose.center_idx, pose.spread, tuple(finger_ids), tuple(sorted(mapping.items())))
        if key in seen:
            continue
        seen.add(key)

        hw_spread_level = _path_a_pose_spread_to_hw_level(pose.spread)
        spread_angle = cfg.path_a_spread_angle_map.get(hw_spread_level)
        out.append(
            GeometryCandidate(
                mode="white_key",
                center_slot=white_idx_to_slot(pose.center_idx),
                spread_key=f"A:{pose.spread}",
                finger_ids=finger_ids,
                mapping=mapping,
                notes=list(wanted),
                duration=duration,
                is_rest=False,
                center_note=planner.white_keys[pose.center_idx],
                spread_level=hw_spread_level,
                spread_profile=None,
                center_angle_override=None,
                spread_angle_override=spread_angle,
                pair_priority=sol.get("pair_priority", 99),
                source_path="A",
            )
        )

    return out


def _path_b_two_note_candidates(
    planner: HandPlanner,
    notes: List[str],
    duration: float,
    cfg: DualPathPlannerConfig,
    note_to_angle_fn=None,
) -> List[GeometryCandidate]:
    wanted = _unique_sorted_notes(notes)
    if len(wanted) != 2:
        return []

    low_note, high_note = wanted

    if cfg.allow_path_b_two_white_only:
        if not is_white_note(low_note) or not is_white_note(high_note):
            return []

    if low_note not in planner.white_note_to_idx or high_note not in planner.white_note_to_idx:
        return []

    low_idx = planner.white_note_to_idx[low_note]
    high_idx = planner.white_note_to_idx[high_note]
    if high_idx <= low_idx:
        return []

    white_span_steps = high_idx - low_idx
    profile = cfg.path_b_profiles.get(white_span_steps)
    if profile is None:
        return []

    # odd span -> midpoint lies BETWEEN two white keys.
    if white_span_steps % 2 == 0:
        return []

    center_slot = low_idx + high_idx  # midpoint on doubled white grid
    center_angle = center_slot_to_angle(center_slot, planner, note_to_angle_fn)

    return [
        GeometryCandidate(
            mode="between_white",
            center_slot=center_slot,
            spread_key=f"B:{profile.name}",
            finger_ids=["Lw", "Rw"],
            mapping={low_note: "Lw", high_note: "Rw"},
            notes=list(wanted),
            duration=duration,
            is_rest=False,
            center_note=None,
            spread_level=int(profile.spread_level),
            spread_profile=profile.name,
            center_angle_override=center_angle,
            spread_angle_override=float(profile.small_motor_angle),
            pair_priority=profile.priority,
            source_path="B",
        )
    ]


def _rest_candidates(planner: HandPlanner, duration: float, cfg: DualPathPlannerConfig) -> List[GeometryCandidate]:
    out: List[GeometryCandidate] = []
    for pose in planner.valid_poses:
        hw_spread_level = _path_a_pose_spread_to_hw_level(pose.spread)
        spread_angle = cfg.path_a_spread_angle_map.get(hw_spread_level)
        out.append(
            GeometryCandidate(
                mode="white_key",
                center_slot=white_idx_to_slot(pose.center_idx),
                spread_key=f"A:{pose.spread}",
                finger_ids=[],
                mapping={},
                notes=[],
                duration=duration,
                is_rest=True,
                center_note=planner.white_keys[pose.center_idx],
                spread_level=hw_spread_level,
                spread_profile=None,
                center_angle_override=None,
                spread_angle_override=spread_angle,
                source_path="A",
            )
        )
    return out


def _best_playable_subset_dual_path(
    notes: List[str],
    planner: HandPlanner,
    cfg: DualPathPlannerConfig,
    duration: float,
    hand_name: str,
    note_to_angle_fn=None,
    forbidden_candidate_fn=None,
) -> List[str]:
    uniq = _unique_sorted_notes(notes)
    if not uniq:
        return []

    from itertools import combinations

    playable_subsets: List[List[str]] = []

    for size in range(len(uniq), 0, -1):
        level: List[List[str]] = []
        for combo in combinations(uniq, size):
            combo_list = list(combo)
            a = _path_a_candidates(planner, combo_list, duration, cfg)
            b = _path_b_two_note_candidates(
                planner,
                combo_list,
                duration,
                cfg,
                note_to_angle_fn=note_to_angle_fn,
            )

            all_cands = list(a) + list(b)
            if forbidden_candidate_fn is not None:
                all_cands = [c for c in all_cands if not forbidden_candidate_fn(c)]

            if all_cands:
                level.append(combo_list)

        if level:
            playable_subsets.extend(level)
            break

    if playable_subsets:
        subset = choose_musical_subset(
            notes=uniq,
            playable_subsets=playable_subsets,
            hand_name=hand_name,
            prev_subset=None,
        )
        if subset:
            return _unique_sorted_notes(subset)

    # fallback: old mechanical preference
    def subset_key(combo: Sequence[str]):
        mids = [note_to_midi(x) for x in combo]
        span = (max(mids) - min(mids)) if len(mids) >= 2 else 0
        if hand_name.lower() == "right":
            return (len(combo), sum(mids), -span)
        return (len(combo), -sum(mids), -span)

    if playable_subsets:
        return list(max(playable_subsets, key=subset_key))

    return []


def _event_candidates_dual_path(
    score_item,
    planner: HandPlanner,
    cfg: DualPathPlannerConfig,
    hand_name: str = "right",
    note_to_angle_fn=None,
    forbidden_candidate_fn=None,
) -> List[GeometryCandidate]:
    notes, duration = _coerce_score_event(score_item)

    def _filter(cands):
        if forbidden_candidate_fn is None:
            return cands
        return [c for c in cands if not forbidden_candidate_fn(c)]

    if not notes:
        rest_cands = _rest_candidates(planner, duration, cfg)
        filtered = _filter(rest_cands)
        return filtered if filtered else rest_cands

    cands = _path_a_candidates(planner, notes, duration, cfg)
    cands = _filter(cands)
    if not cands:
        cands = _path_b_two_note_candidates(
            planner, notes, duration, cfg, note_to_angle_fn=note_to_angle_fn
        )
        cands = _filter(cands)

    if not cands:
        subset = _best_playable_subset_dual_path(
            notes,
            planner=planner,
            cfg=cfg,
            duration=duration,
            hand_name=hand_name,
            note_to_angle_fn=note_to_angle_fn,
            forbidden_candidate_fn=forbidden_candidate_fn,
        )
        if subset:
            cands = _path_a_candidates(planner, subset, duration, cfg)
            cands = _filter(cands)
            if not cands:
                cands = _path_b_two_note_candidates(
                    planner, subset, duration, cfg, note_to_angle_fn=note_to_angle_fn
                )
                cands = _filter(cands)

    if cands:
        return cands

    # 最后才 fallback 成 rest
    return _rest_candidates(planner, duration, cfg)


# ============================================================
# DP / action selection
# ============================================================

def _finger_set_distance(prev_finger_ids: Iterable[str], cur_finger_ids: Iterable[str]) -> int:
    prev_set = set(_ordered_unique_finger_ids(prev_finger_ids or []))
    cur_set = set(_ordered_unique_finger_ids(cur_finger_ids or []))
    return len(prev_set.symmetric_difference(cur_set))


def _spread_switch_cost(prev: GeometryCandidate, cur: GeometryCandidate, cfg: DualPathPlannerConfig) -> float:
    if prev.is_rest and cur.is_rest and prev.spread_key == cur.spread_key:
        return 0.0

    prev_angle = prev.spread_angle_override
    cur_angle = cur.spread_angle_override
    if prev_angle is not None and cur_angle is not None:
        return abs(cur_angle - prev_angle) * cfg.cost_small_motor_angle_per_deg

    if prev.spread_key == cur.spread_key:
        return 0.0

    return cfg.cost_profile_switch


def _candidate_transition_cost(
    prev: GeometryCandidate,
    cur: GeometryCandidate,
    cfg: DualPathPlannerConfig,
) -> float:
    move_scale = cfg.rest_move_discount if cur.is_rest else 1.0

    hand_move_white = abs(cur.center_slot - prev.center_slot) / 2.0
    finger_change = _finger_set_distance(prev.finger_ids, cur.finger_ids)
    spread_change = _spread_switch_cost(prev, cur, cfg)

    total = 0.0
    total += hand_move_white * cfg.cost_hand_move_per_white * move_scale
    total += spread_change * move_scale
    total += finger_change * cfg.cost_finger_change

    if cur.source_path == "A":
        total -= cfg.path_a_bonus
    else:
        total += cfg.path_b_penalty

    return total


def _event_static_cost(cand: GeometryCandidate, planner: HandPlanner) -> float:
    if not cand.notes:
        return 0.01

    avg_target = sum(note_to_midi(n) for n in cand.notes) / len(cand.notes)

    if cand.center_note is not None:
        center_midi = note_to_midi(cand.center_note)
    else:
        # for half-center, approximate with average of surrounding white keys
        left_idx = cand.center_slot // 2
        right_idx = left_idx + 1
        if 0 <= left_idx < len(planner.white_keys) and 0 <= right_idx < len(planner.white_keys):
            center_midi = 0.5 * (
                note_to_midi(planner.white_keys[left_idx]) + note_to_midi(planner.white_keys[right_idx])
            )
        else:
            center_midi = avg_target

    return 0.01 * abs(center_midi - avg_target)


def _action_type(prev: GeometryCandidate, cur: GeometryCandidate) -> str:
    if cur.is_rest:
        if prev.center_slot != cur.center_slot or prev.spread_key != cur.spread_key:
            return "rest_reposition"
        return "rest_hold"

    is_chord = len(cur.notes) >= 2
    hand_changed = (prev.center_slot != cur.center_slot) or (prev.mode != cur.mode)
    spread_changed = prev.spread_key != cur.spread_key

    if is_chord:
        if hand_changed and spread_changed:
            return "move_hand_and_chord"
        if hand_changed:
            return "move_hand"
        if spread_changed:
            return "chord_spread_change"
        return "chord"

    if hand_changed and spread_changed:
        return "move_hand_and_finger"
    if hand_changed:
        return "move_hand"
    if spread_changed:
        return "finger_only"
    return "hold_shape"


def _make_start_candidate(
    planner: HandPlanner,
    score_events,
    cfg: DualPathPlannerConfig,
) -> GeometryCandidate:
    fake_song = []
    for item in score_events:
        notes, dur = _coerce_score_event(item)
        fake_song.append((notes[0], dur) if notes else ("REST", dur))

    start_state_obj = planner._make_start_state(fake_song if fake_song else [("REST", 0.1)])
    start_pose = start_state_obj.pose
    hw_spread_level = _path_a_pose_spread_to_hw_level(start_pose.spread)
    spread_angle = cfg.path_a_spread_angle_map.get(hw_spread_level)

    return GeometryCandidate(
        mode="white_key",
        center_slot=white_idx_to_slot(start_pose.center_idx),
        spread_key=f"A:{start_pose.spread}",
        finger_ids=[],
        mapping={},
        notes=[],
        duration=0.0,
        is_rest=True,
        center_note=planner.white_keys[start_pose.center_idx],
        spread_level=hw_spread_level,
        spread_profile=None,
        center_angle_override=None,
        spread_angle_override=spread_angle,
        source_path="A",
    )


def plan_hand_score_dual_path(
    score_events,
    planner: HandPlanner,
    hand_name: str = "right",
    dual_cfg: Optional[DualPathPlannerConfig] = None,
    note_to_angle_fn=None,
    forbidden_step_predicate=None,
) -> List[dict]:
    """
    New planner with two paths:

    Path A:
        center on a white key (legacy model, fully compatible with existing logic)

    Path B:
        center between two white keys, used only for selected 2-note spans
        with dedicated small-motor profile angles.

    Output keeps legacy fields for Path A and adds new fields for Path B:
        center_mode, center_slot, spread_profile,
        center_angle_override, spread_angle_override
    """
    dual_cfg = dual_cfg or default_dual_path_config()
    if not score_events:
        return []

    prepared_steps: List[List[GeometryCandidate]] = []
    for step_idx, item in enumerate(score_events):
        def _forbidden_here(cand, idx=step_idx):
            if forbidden_step_predicate is None:
                return False
            return bool(forbidden_step_predicate(idx, cand))

        prepared_steps.append(
            _event_candidates_dual_path(
                item,
                planner=planner,
                cfg=dual_cfg,
                hand_name=hand_name,
                note_to_angle_fn=note_to_angle_fn,
                forbidden_candidate_fn=_forbidden_here,
            )
        )

    start_cand = _make_start_candidate(planner, score_events, dual_cfg)
    start_state = GeometryState(
        mode=start_cand.mode,
        center_slot=start_cand.center_slot,
        spread_key=start_cand.spread_key,
        finger_ids=tuple(start_cand.finger_ids),
    )

    state_payload: Dict[GeometryState, GeometryCandidate] = {start_state: start_cand}
    dp: Dict[GeometryState, float] = {start_state: 0.0}
    history: List[Dict[GeometryState, Tuple[GeometryState, GeometryCandidate]]] = []

    for cands in prepared_steps:
        next_dp: Dict[GeometryState, float] = {}
        next_payload: Dict[GeometryState, GeometryCandidate] = {}
        step_back: Dict[GeometryState, Tuple[GeometryState, GeometryCandidate]] = {}

        for prev_state, prev_cost in dp.items():
            prev_cand = state_payload[prev_state]

            for cand in cands:
                cur_state = GeometryState(
                    mode=cand.mode,
                    center_slot=cand.center_slot,
                    spread_key=cand.spread_key,
                    finger_ids=tuple(_ordered_unique_finger_ids(cand.finger_ids)),
                )

                step_cost = _candidate_transition_cost(prev_cand, cand, dual_cfg)
                step_cost += _event_static_cost(cand, planner)
                total_cost = prev_cost + step_cost

                if cur_state not in next_dp or total_cost < next_dp[cur_state]:
                    next_dp[cur_state] = total_cost
                    next_payload[cur_state] = cand
                    step_back[cur_state] = (prev_state, cand)

        dp = next_dp
        state_payload = next_payload
        history.append(step_back)

    def final_key_rank(state: GeometryState):
        return (
            dp[state],
            0 if state.mode == "white_key" else 1,
            abs(state.center_slot - len(planner.white_keys)),
            len(state.finger_ids),
        )

    final_state = min(dp.keys(), key=final_key_rank)

    rev_events: List[GeometryCandidate] = []
    cur_state = final_state
    for step_idx in range(len(history) - 1, -1, -1):
        prev_state, cand = history[step_idx][cur_state]
        rev_events.append(cand)
        cur_state = prev_state
    rev_events.reverse()

    planned = []
    prev = start_cand
    for idx, cand in enumerate(rev_events):
        if idx == 0 and not cand.is_rest:
            action_type = "chord" if len(cand.notes) >= 2 else planner.cfg.first_note_action_type
        else:
            action_type = _action_type(prev, cand)

        planned.append({
            "notes": list(cand.notes),
            "duration": cand.duration,
            "center_mode": cand.mode,
            "center_slot": cand.center_slot,
            "center_note": cand.center_note,
            "spread": cand.spread_level,
            "spread_profile": cand.spread_profile,
            "center_angle_override": cand.center_angle_override,
            "spread_angle_override": cand.spread_angle_override,
            "finger_ids": list(cand.finger_ids),
            "action_type": action_type,
            "note_finger_map": dict(cand.mapping),
            "source_path": cand.source_path,
            "mid_available": (cand.mode == "white_key"),
        })
        prev = cand

    return planned

def _build_planned_timeline(planned_seq: List[dict]):
    out = []
    t = 0.0
    for idx, ev in enumerate(planned_seq or []):
        try:
            d = max(0.01, float(ev.get("duration", 0.01)))
        except Exception:
            d = 0.01

        out.append({
            "idx": idx,
            "start": t,
            "end": t + d,
            "event": ev,
        })
        t += d
    return out


def _right_cross_hand_lock_active(ev: dict) -> bool:
    center_note = normalize_note(ev.get("center_note", "")) if ev.get("center_note") else ""

    try:
        spread = int(ev.get("spread", 0))
    except Exception:
        spread = 0

    # 右手低边界危险区：
    # F4 + SF2 / SF4
    if center_note == "F4" and spread in (2, 4):
        return True

    # G4 + SF4
    if center_note == "G4" and spread == 4:
        return True

    return False


def _left_forbidden_a3_b3_open(ev: dict) -> bool:
    center_note = normalize_note(ev.get("center_note", "")) if ev.get("center_note") else ""

    try:
        spread = int(ev.get("spread", 0))
    except Exception:
        spread = 0

    # 左手危险区：
    # F3 为中心：已经太靠右
    if center_note == "F3":
        return True

    # E3 + SF2 / SF4：会推进到 A3 / B3
    if center_note == "E3" and spread in (2, 4):
        return True

    # D3 + SF4：也会推进到 A3
    if center_note == "D3" and spread == 4:
        return True

    return False
def _cand_right_danger(cand: GeometryCandidate) -> bool:
    center_note = normalize_note(cand.center_note) if cand.center_note else ""

    try:
        spread = int(cand.spread_level or 0)
    except Exception:
        spread = 0

    # 右手危险区：
    # F4 + SF2 / SF4
    if center_note == "F4" and spread in (2, 4):
        return True

    # G4 + SF4
    if center_note == "G4" and spread == 4:
        return True

    return False


def _cand_left_danger(cand: GeometryCandidate) -> bool:
    center_note = normalize_note(cand.center_note) if cand.center_note else ""

    try:
        spread = int(cand.spread_level or 0)
    except Exception:
        spread = 0

    # 左手危险区：
    # F3 为中心：太靠右
    if center_note == "F3":
        return True

    # E3 + SF2 / SF4：会打到 A3 / B3
    if center_note == "E3" and spread in (2, 4):
        return True

    # D3 + SF4：也会打到 A3
    if center_note == "D3" and spread == 4:
        return True

    return False

def _force_safe_rest(ev: dict, blocked_hand: str) -> None:
    ev["notes"] = []
    ev["finger_ids"] = []
    ev["note_finger_map"] = {}
    ev["center_note"] = ev.get("center_note", None)
    ev["action_type"] = "rest_hold"
    ev["cross_hand_limit_blocked"] = True
    ev["cross_hand_limit_blocked_hand"] = blocked_hand


def _apply_right_c4_left_limit(planned_right: List[dict], planned_left: List[dict]) -> None:
    right_timeline = _build_planned_timeline(planned_right)
    left_timeline = _build_planned_timeline(planned_left)

    for r in right_timeline:
        r_ev = r["event"]
        if not _right_cross_hand_lock_active(r_ev):
            continue

        for l in left_timeline:
            l_ev = l["event"]
            if not _left_forbidden_a3_b3_open(l_ev):
                continue

            # 没有时间重叠
            if l["end"] <= r["start"] or l["start"] >= r["end"]:
                continue

            # 谁后到，谁让开
            # 同时到的话，默认左手让开
            if l["start"] >= r["start"]:
                _force_safe_rest(l_ev, "left")
            else:
                _force_safe_rest(r_ev, "right")
            break

def _time_overlap(a: dict, b: dict) -> bool:
    return not (a["end"] <= b["start"] or a["start"] >= b["end"])


def _collect_forbidden_step_indices(
    planned_self: List[dict],
    planned_other: List[dict],
    other_event_danger_fn,
) -> set:
    self_timeline = _build_planned_timeline(planned_self)
    other_timeline = _build_planned_timeline(planned_other)

    danger_windows = [x for x in other_timeline if other_event_danger_fn(x["event"])]

    out = set()
    for s in self_timeline:
        for d in danger_windows:
            if _time_overlap(s, d):
                out.add(int(s["idx"]))
                break
    return out

def _event_signature(ev: dict):
    return (
        tuple(ev.get("notes", [])),
        ev.get("center_mode"),
        ev.get("center_slot"),
        ev.get("center_note"),
        ev.get("spread"),
        ev.get("spread_profile"),
        ev.get("center_angle_override"),
        ev.get("spread_angle_override"),
        tuple(ev.get("finger_ids", [])),
        tuple(sorted((ev.get("note_finger_map", {}) or {}).items())),
        ev.get("action_type"),
        ev.get("source_path"),
    )


def _plan_signature(plan: List[dict]):
    return tuple(_event_signature(ev) for ev in (plan or []))

def plan_robot_score_dual_path(
    score: dict,
    right_planner: HandPlanner,
    left_planner: HandPlanner,
    dual_cfg: Optional[DualPathPlannerConfig] = None,
    note_to_angle_fn=None,
    max_cross_iters: int = 6,
):
    dual_cfg = dual_cfg or default_dual_path_config()

    # 初始：不加跨手约束，各自先跑一次
    planned_right = plan_hand_score_dual_path(
        score.get("right", []),
        right_planner,
        hand_name="right",
        dual_cfg=dual_cfg,
        note_to_angle_fn=note_to_angle_fn,
    )

    planned_left = plan_hand_score_dual_path(
        score.get("left", []),
        left_planner,
        hand_name="left",
        dual_cfg=dual_cfg,
        note_to_angle_fn=note_to_angle_fn,
    )

    seen_signatures = set()

    for _ in range(max_cross_iters):
        left_forbidden_steps = _collect_forbidden_step_indices(
            planned_self=planned_left,
            planned_other=planned_right,
            other_event_danger_fn=_right_cross_hand_lock_active,
        )

        next_left = plan_hand_score_dual_path(
            score.get("left", []),
            left_planner,
            hand_name="left",
            dual_cfg=dual_cfg,
            note_to_angle_fn=note_to_angle_fn,
            forbidden_step_predicate=lambda idx, cand, blocked=left_forbidden_steps: (
                idx in blocked and _cand_left_danger(cand)
            ),
        )

        right_forbidden_steps = _collect_forbidden_step_indices(
            planned_self=planned_right,
            planned_other=next_left,
            other_event_danger_fn=_left_forbidden_a3_b3_open,
        )

        next_right = plan_hand_score_dual_path(
            score.get("right", []),
            right_planner,
            hand_name="right",
            dual_cfg=dual_cfg,
            note_to_angle_fn=note_to_angle_fn,
            forbidden_step_predicate=lambda idx, cand, blocked=right_forbidden_steps: (
                idx in blocked and _cand_right_danger(cand)
            ),
        )

        sig = (
            tuple(sorted(left_forbidden_steps)),
            tuple(sorted(right_forbidden_steps)),
            _plan_signature(next_left),
            _plan_signature(next_right),
        )

        # 收敛：结果不再变化
        if (
            _plan_signature(next_left) == _plan_signature(planned_left)
            and _plan_signature(next_right) == _plan_signature(planned_right)
        ):
            planned_left = next_left
            planned_right = next_right
            break

        # 防止来回震荡
        if sig in seen_signatures:
            planned_left = next_left
            planned_right = next_right
            break

        seen_signatures.add(sig)
        planned_left = next_left
        planned_right = next_right

    # 最后保留一个轻量兜底
    _apply_right_c4_left_limit(planned_right, planned_left)

    return {
        "right": planned_right,
        "left": planned_left,
    }


def load_and_plan_midi_dual_path(
    path: str,
    right_planner: HandPlanner,
    left_planner: HandPlanner,
    dual_cfg: Optional[DualPathPlannerConfig] = None,
    note_to_angle_fn=None,
    default_tempo: int = 500000,
    min_duration: float = 0.08,
    rest_merge: bool = True,
    split_note: str = "C4",
    verbose: bool = False,
):
    if load_score_from_midi is None:
        raise ImportError("midi_loader.load_score_from_midi is not available")

    raw_score = load_score_from_midi(
        path,
        default_tempo=default_tempo,
        min_duration=min_duration,
        rest_merge=rest_merge,
        split_note=split_note,
        verbose=verbose,
    )

    planned = plan_robot_score_dual_path(
        raw_score,
        right_planner=right_planner,
        left_planner=left_planner,
        dual_cfg=dual_cfg,
        note_to_angle_fn=note_to_angle_fn,
    )

    return {
        "raw_score": raw_score,
        "planned": planned,
    }


def pretty_print_dual_path_plan(plan: List[dict], title: str = "PLAN") -> None:
    print(f"\n{title}")
    print("-" * len(title))
    for i, ev in enumerate(plan):
        print(
            f"[{i:02d}] notes={ev['notes']} dur={ev['duration']:.3f}s "
            f"mode={ev['center_mode']} center_slot={ev['center_slot']} "
            f"center_note={ev['center_note']} spread={ev['spread']} "
            f"profile={ev['spread_profile']} fingers={ev['finger_ids']} "
            f"path={ev['source_path']} action={ev['action_type']}"
        )


if __name__ == "__main__":  # simple local demo
    try:
        from Core_function.note import note_to_angle as _note_to_angle
    except Exception:
        _note_to_angle = None

    from Core_function.hand_planner import PlannerConfig

    right_cfg = PlannerConfig(
        note_min="C4",
        note_max="F6",
        start_center_note="C5",
        start_spread=0,
        center_note_min="F4",
        center_note_max="D6",
        enable_left_black_finger=True,
        enable_right_black_finger=True,
    )
    left_cfg = PlannerConfig(
        note_min="C2",
        note_max="B3",
        start_center_note="C3",
        start_spread=0,
        enable_left_black_finger=True,
        enable_right_black_finger=True,
    )

    right_planner = HandPlanner(right_cfg)
    left_planner = HandPlanner(left_cfg)

    raw_score = {
        "right": [
            (["C4", "G4"], 1.0),
            (["C4", "A4"], 1.0),
            (["F4"], 1.0),
        ],
        "left": [
            (["C3", "E3"], 1.0),
            (["F2", "A3"], 1.0),
        ],
    }

    planned = plan_robot_score_dual_path(
        raw_score,
        right_planner,
        left_planner,
        dual_cfg=default_dual_path_config(),
        note_to_angle_fn=_note_to_angle,
    )

    pretty_print_dual_path_plan(planned["right"], "RIGHT DUAL PATH PLAN")
    pretty_print_dual_path_plan(planned["left"], "LEFT DUAL PATH PLAN")
