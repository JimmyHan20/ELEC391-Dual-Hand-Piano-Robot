from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Dict, List, Optional, Tuple
from Core_function.music_theory_tools import choose_musical_subset

try:
    from Song.midi_loader import load_score_from_midi
except Exception:
    load_score_from_midi = None
from itertools import combinations

# ============================================================
# 1) Basic note utilities
# ============================================================

NOTE_NAMES_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#',
                    'G', 'G#', 'A', 'A#', 'B']

WHITE_PITCHES = {'C', 'D', 'E', 'F', 'G', 'A', 'B'}

FLAT_TO_SHARP = {
    'DB': 'C#',
    'EB': 'D#',
    'GB': 'F#',
    'AB': 'G#',
    'BB': 'A#',
}

def _pose_spread_to_output_level(spread_idx: int) -> int:
    return {
        0: 0,
        1: 2,   # 一个键
        2: 4,   # 两个键
    }.get(int(spread_idx), int(spread_idx))

RIGHT_LOW_NOTE_HW_OVERRIDE_PLAIN = {
    "C4": {"center_note": "F4", "spread": 2, "finger_id": "Lw"},
    "D4": {"center_note": "F4", "spread": 0, "finger_id": "Lw"},
    "E4": {"center_note": "G4", "spread": 0, "finger_id": "Lw"},
}

LEFT_HIGH_NOTE_HW_OVERRIDE_PLAIN = {
    "G3": {"center_note": "E3", "spread": 0, "finger_id": "Rw"},
    "A3": {"center_note": "E3", "spread": 2, "finger_id": "Rw"},
    "B3": {"center_note": "E3", "spread": 4, "finger_id": "Rw"},
}


def _apply_edge_note_hw_override_plain(planner: "HandPlanner", ev: dict) -> dict:
    center_min = normalize_note(planner.cfg.center_note_min) if planner.cfg.center_note_min else ""
    center_max = normalize_note(planner.cfg.center_note_max) if planner.cfg.center_note_max else ""

    note = normalize_note(ev.get("note", ""))
    out = dict(ev)

    # 右手：保持你原来的逻辑
    if center_min == "F4":
        override = RIGHT_LOW_NOTE_HW_OVERRIDE_PLAIN.get(note)
        if override is None:
            return ev

        out["center_note"] = override["center_note"]
        out["spread"] = int(override["spread"])
        out["finger_id"] = override["finger_id"]
        out["low_note_hw_override"] = True
        return out

    # 左手：新增 G3 / A3 / B3 特例
    if center_max == "E3":
        override = LEFT_HIGH_NOTE_HW_OVERRIDE_PLAIN.get(note)
        if override is None:
            return ev

        out["center_note"] = override["center_note"]
        out["spread"] = int(override["spread"])
        out["finger_id"] = override["finger_id"]
        out["left_high_note_hw_override"] = True
        return out

    return ev

def normalize_note(note: str) -> str:
    s = str(note).strip().upper()
    if s == "REST":
        return "REST"

    if len(s) < 2:
        raise ValueError(f"Bad note: {note!r}")

    if len(s) >= 3 and s[1] == 'B':
        pitch = s[:2]
        octave = s[2:]
        pitch = FLAT_TO_SHARP.get(pitch, pitch)
        return f"{pitch}{octave}"

    return s


def note_to_midi(note: str) -> int:
    n = normalize_note(note)

    if n == "REST":
        raise ValueError("REST has no MIDI number.")

    if len(n) >= 3 and n[1] == '#':
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

    if len(n) >= 3 and n[1] == '#':
        return n[:2]
    return n[:1]


def is_white_note(note: str) -> bool:
    return get_pitch_name(note) in WHITE_PITCHES


def is_black_note(note: str) -> bool:
    n = normalize_note(note)
    if n == "REST":
        return False
    return not is_white_note(n)


def build_white_key_range(note_min: str, note_max: str) -> List[str]:
    lo = note_to_midi(note_min)
    hi = note_to_midi(note_max)

    if lo > hi:
        raise ValueError("note_min must be <= note_max.")

    out = []
    for m in range(lo, hi + 1):
        n = midi_to_note(m)
        if is_white_note(n):
            out.append(n)
    return out


def associated_black_for_white(white_note: str) -> Optional[str]:
    """
    Return the black-key slot associated with a white key.

    Rule:
    - Prefer the sharp of this white key (white + 1 semitone) if that is black.
    - If no sharp exists (e.g. E, B), fall back to the flat below (white - 1 semitone)
      if that is black.

    Examples:
        A  -> A#
        G  -> G#
        F  -> F#
        E  -> D#
        B  -> A#
    """
    w = normalize_note(white_note)
    if not is_white_note(w):
        return None

    m = note_to_midi(w)

    up = midi_to_note(m + 1)
    if is_black_note(up):
        return up

    down = midi_to_note(m - 1)
    if is_black_note(down):
        return down

    return None


# ============================================================
# 2) Planner dataclasses
# ============================================================

@dataclass(frozen=True)
class Pose:
    """
    center_idx: current middle-finger center on WHITE-key index
    spread:
        0 -> outer white fingers at ±2 white keys
        1 -> outer white fingers at ±3 white keys
        2 -> outer white fingers at ±4 white keys
    """
    center_idx: int
    spread: int


@dataclass(frozen=True)
class State:
    pose: Pose
    last_finger_id: Optional[str]   # "Lw", "Lb", "M", "Rb", "Rw", or None


@dataclass(frozen=True)
class Candidate:
    pose: Pose
    finger_id: Optional[str]        # None only for REST reposition


@dataclass
class PlannerConfig:
    # Keyboard range
    note_min: str = "E3"
    note_max: str = "E5"

    # spread 0/1/2 => outer white offsets ±2/±3/±4
    spread_distances: Tuple[int, ...] = (2, 3, 4)

    # Start pose
    start_center_note: Optional[str] = None
    start_spread: int = 0

    # Cost weights
    cost_hand_move_per_key: float = 10.0
    cost_spread_change_per_level: float = 2.0
    cost_finger_change: float = 0.6

    # REST can be used for reposition
    allow_rest_reposition: bool = True
    rest_move_discount: float = 0.50

    # Unsupported note handling
    unsupported_note_as_rest: bool = False

    # Enable black-key slots on both sides
    enable_left_black_finger: bool = True
    enable_right_black_finger: bool = True

    first_note_action_type: str = "move_hand_and_finger"

    center_note_min: Optional[str] = None
    center_note_max: Optional[str] = None


# ============================================================
# 3) HandPlanner
# ============================================================

class HandPlanner:
    """
    Enabled model:
        Lw, Lb, M, Rb, Rw

    For each pose:
        M  -> center white
        Lw -> center - d  (white track)
        Rw -> center + d  (white track)

        Lb -> black slot associated with Lw
        Rb -> black slot associated with Rw

    This matches the mechanical idea that when motor2 changes spread:
        - white-key slots move outward together on the white track
        - black-key slots move outward together on the black track
    """

    VALID_FINGERS_NOW = ("Lw", "Lb", "M", "Rb", "Rw")

    def __init__(self, cfg: PlannerConfig):
        self.cfg = cfg

        self.white_keys: List[str] = build_white_key_range(cfg.note_min, cfg.note_max)
        if not self.white_keys:
            raise ValueError("White-key range is empty.")

        self.white_note_to_idx: Dict[str, int] = {
            normalize_note(n): i for i, n in enumerate(self.white_keys)
        }

        self.valid_poses: List[Pose] = self._build_valid_poses()

    # --------------------------------------------------------
    # Pose validity
    # --------------------------------------------------------

    def _pose_valid(self, center_idx: int, spread: int) -> bool:
        if spread < 0 or spread >= len(self.cfg.spread_distances):
            return False

        if center_idx < 0 or center_idx >= len(self.white_keys):
            return False

        # 先把 center_idx 对应回真正的中心音
        center_note = self.white_keys[center_idx]

        # 右手中指中心位限制：F4 ~ D6
        if self.cfg.center_note_min is not None:
            if note_to_midi(center_note) < note_to_midi(self.cfg.center_note_min):
                return False

        if self.cfg.center_note_max is not None:
            if note_to_midi(center_note) > note_to_midi(self.cfg.center_note_max):
                return False

        d = self.cfg.spread_distances[spread]
        left_idx = center_idx - d
        right_idx = center_idx + d

        return (
            0 <= left_idx < len(self.white_keys) and
            0 <= right_idx < len(self.white_keys)
        )



    def _build_valid_poses(self) -> List[Pose]:
        out = []
        for center_idx in range(len(self.white_keys)):
            for spread in range(len(self.cfg.spread_distances)):
                if self._pose_valid(center_idx, spread):
                    out.append(Pose(center_idx, spread))
        return out

    # --------------------------------------------------------
    # Per-pose reachable notes
    # --------------------------------------------------------
    def _pose_note_map(self, pose: Pose) -> List[Tuple[str, str]]:
        """
        Return reachable (note, finger_id) pairs for one pose.
        """
        d = self.cfg.spread_distances[pose.spread]
        center_idx = pose.center_idx
        left_idx = center_idx - d
        right_idx = center_idx + d

        out: List[Tuple[str, str]] = []

        left_white = self.white_keys[left_idx]
        center_white = self.white_keys[center_idx]
        right_white = self.white_keys[right_idx]

        # left white
        out.append((left_white, "Lw"))

        # left black follows the left white slot
        if self.cfg.enable_left_black_finger:
            lb_note = associated_black_for_white(left_white)
            if lb_note is not None:
                out.append((lb_note, "Lb"))

        # middle
        out.append((center_white, "M"))

        # right black follows the right white slot
        if self.cfg.enable_right_black_finger:
            rb_note = associated_black_for_white(right_white)
            if rb_note is not None:
                out.append((rb_note, "Rb"))

        # right white
        out.append((right_white, "Rw"))

        return out

    # --------------------------------------------------------
    # Note support
    # --------------------------------------------------------
    def _note_supported_now(self, note: str) -> bool:
        n = normalize_note(note)

        if n == "REST":
            return True

        for pose in self.valid_poses:
            for pose_note, _finger_id in self._pose_note_map(pose):
                if normalize_note(pose_note) == n:
                    return True

        return False

    # --------------------------------------------------------
    # Candidate generation
    # --------------------------------------------------------
    def _note_candidates(self, note: str) -> List[Candidate]:
        n = normalize_note(note)
        if n == "REST":
            raise ValueError("REST should not go into _note_candidates().")

        out: List[Candidate] = []

        for pose in self.valid_poses:
            for pose_note, finger_id in self._pose_note_map(pose):
                if normalize_note(pose_note) == n:
                    out.append(Candidate(
                        pose=pose,
                        finger_id=finger_id,
                    ))

        uniq: Dict[Tuple[int, int, str], Candidate] = {}
        for c in out:
            uniq[(c.pose.center_idx, c.pose.spread, c.finger_id or "REST")] = c

        return list(uniq.values())

    def _rest_candidates(self) -> List[Candidate]:
        if not self.cfg.allow_rest_reposition:
            return []
        return [Candidate(pose=p, finger_id=None) for p in self.valid_poses]
    
    def _direct_pose_solutions_for_notes(self, wanted: List[str]):
        """
        Old logic extracted:
        scan all valid poses and keep the ones that can cover all wanted notes.
        Return list of:
            {
                "pose": Pose,
                "mapping": {note: finger_id},
                "pair_priority": 99,
            }
        """
        out = []

        for pose in self.valid_poses:
            pose_pairs = self._pose_note_map(pose)

            mapping = {}
            for pose_note, finger_id in pose_pairs:
                nn = normalize_note(pose_note)
                if nn in wanted and nn not in mapping:
                    mapping[nn] = finger_id

            if len(mapping) != len(wanted):
                continue

            out.append({
                "pose": pose,
                "mapping": mapping,
                "pair_priority": 99,   # normal search, no special preference
            })

        return out

    def _center_candidates_for_note_finger(self, note: str, finger_id: str, spread: int) -> List[int]:
        """
        Reverse solve:
            given (note, finger_id, spread), what center_idx could make it happen?

        Returns a list because black-key mapping may have more than one white anchor.
        """
        n = normalize_note(note)
        d = self.cfg.spread_distances[spread]
        out = []

        # middle finger -> center white only
        if finger_id == "M":
            if n in self.white_note_to_idx:
                out.append(self.white_note_to_idx[n])
            return out

        # left white
        if finger_id == "Lw":
            if n in self.white_note_to_idx:
                target_idx = self.white_note_to_idx[n]
                out.append(target_idx + d)
            return out

        # right white
        if finger_id == "Rw":
            if n in self.white_note_to_idx:
                target_idx = self.white_note_to_idx[n]
                out.append(target_idx - d)
            return out

        # left black
        if finger_id == "Lb":
            if not is_black_note(n):
                return out
            for white_idx, white_note in enumerate(self.white_keys):
                assoc = associated_black_for_white(white_note)
                if assoc is not None and normalize_note(assoc) == n:
                    out.append(white_idx + d)
            return out

        # right black
        if finger_id == "Rb":
            if not is_black_note(n):
                return out
            for white_idx, white_note in enumerate(self.white_keys):
                assoc = associated_black_for_white(white_note)
                if assoc is not None and normalize_note(assoc) == n:
                    out.append(white_idx - d)
            return out

        return out

    def _inverse_two_note_solutions(self, wanted: List[str]):
        """
        Scheme B:
        only for 2-note events and only as fallback when direct scan failed.

        Try to assign:
            low note  -> left-ish finger
            high note -> right-ish finger
        then solve center_idx from equations.

        Returns same format as _direct_pose_solutions_for_notes().
        """
        wanted = _unique_sorted_notes(wanted)
        if len(wanted) != 2:
            return []

        low_note, high_note = wanted[0], wanted[1]

        # priority order:
        # 1) outer-to-outer
        # 2) outer-to-middle / middle-to-outer
        # 3) black variants
        pair_order = [
            ("Lw", "Rw"),
            ("Lw", "M"),
            ("M", "Rw"),
            ("Lb", "Rb"),
            ("Lb", "M"),
            ("M", "Rb"),
            ("Lw", "Rb"),
            ("Lb", "Rw"),
        ]

        out = []
        seen = set()

        for spread in range(len(self.cfg.spread_distances)):
            for pair_priority, (low_finger, high_finger) in enumerate(pair_order):
                low_centers = set(self._center_candidates_for_note_finger(low_note, low_finger, spread))
                high_centers = set(self._center_candidates_for_note_finger(high_note, high_finger, spread))

                common_centers = sorted(low_centers & high_centers)
                if not common_centers:
                    continue

                for center_idx in common_centers:
                    if not self._pose_valid(center_idx, spread):
                        continue

                    pose = Pose(center_idx, spread)

                    # Verify the exact finger->note relationship on this pose
                    finger_to_note = {}
                    for pose_note, fid in self._pose_note_map(pose):
                        finger_to_note[fid] = normalize_note(pose_note)

                    if finger_to_note.get(low_finger) != low_note:
                        continue
                    if finger_to_note.get(high_finger) != high_note:
                        continue

                    key = (pose.center_idx, pose.spread, low_finger, high_finger)
                    if key in seen:
                        continue
                    seen.add(key)

                    out.append({
                        "pose": pose,
                        "mapping": {
                            low_note: low_finger,
                            high_note: high_finger,
                        },
                        "pair_priority": pair_priority,
                    })

        return out

    def _solution_rank(self, pose: Pose, mapping: Dict[str, str], wanted: List[str], pair_priority: int = 99):
        """
        Shared ranking:
        lower is better.
        """
        center_midi = note_to_midi(self.white_keys[pose.center_idx])
        avg_target = sum(note_to_midi(n) for n in wanted) / len(wanted)

        return (
            pair_priority,  # special 2-note solved pairs first
            pose.spread,
            abs(center_midi - avg_target),
            abs(pose.center_idx - (len(self.white_keys) // 2)),
        )
    
    # --------------------------------------------------------
    # Chord / multi-note helpers
    # --------------------------------------------------------
    def find_pose_and_fingers_for_notes(self, notes: List[str]):
        """
        Try to find ONE pose that can cover all given notes simultaneously.

        Normal flow:
        1) direct scan over all valid poses
        2) if not found and this is a 2-note event -> Scheme B inverse solve
        """
        wanted = []
        seen = set()

        for n in notes:
            nn = normalize_note(n)
            if nn == "REST":
                continue
            if nn not in seen:
                wanted.append(nn)
                seen.add(nn)

        wanted = _unique_sorted_notes(wanted)

        if not wanted:
            return None, {}

        # 1) normal direct scan
        solutions = self._direct_pose_solutions_for_notes(wanted)

        # 2) Scheme B fallback for 2-note events
        if not solutions and len(wanted) == 2:
            solutions = self._inverse_two_note_solutions(wanted)

        if not solutions:
            return None, {}

        best = min(
            solutions,
            key=lambda s: self._solution_rank(
                s["pose"],
                s["mapping"],
                wanted,
                s.get("pair_priority", 99),
            )
        )

        return best["pose"], best["mapping"]

    def can_play_notes_at_once(self, notes: List[str]) -> bool:
        pose, _mapping = self.find_pose_and_fingers_for_notes(notes)
        return pose is not None

    def best_playable_subset(self, notes: List[str], hand_name: str = "right") -> List[str]:
        """
        From a note list, find the best subset that can be covered by ONE pose.

        Policy:
        - keep as many notes as possible
        - right hand prefers higher notes
        - left hand prefers lower notes
        - smaller span is slightly preferred
        """
        uniq = []
        seen = set()
        for n in notes:
            nn = normalize_note(n)
            if nn == "REST":
                continue
            if nn not in seen:
                uniq.append(nn)
                seen.add(nn)

        uniq.sort(key=note_to_midi)

        if not uniq:
            return []

        best_subset = []

        def subset_key(combo: Tuple[str, ...]):
            mids = [note_to_midi(x) for x in combo]
            span = (max(mids) - min(mids)) if len(mids) >= 2 else 0

            if hand_name.lower() == "right":
                return (len(combo), sum(mids), -span)
            else:
                # lower total midi => more bass-heavy => better for left hand
                return (len(combo), -sum(mids), -span)

        for size in range(len(uniq), 0, -1):
            playable = []
            for combo in combinations(uniq, size):
                if self.can_play_notes_at_once(list(combo)):
                    playable.append(combo)

            if playable:
                best = max(playable, key=subset_key)
                best_subset = list(best)
                break

        return best_subset

    # --------------------------------------------------------
    # Costs
    # --------------------------------------------------------
    def _transition_cost(self, prev_state: State, cand: Candidate, is_rest: bool) -> float:
        hand_move = abs(cand.pose.center_idx - prev_state.pose.center_idx)
        spread_change = abs(cand.pose.spread - prev_state.pose.spread)

        finger_change = 0.0
        if cand.finger_id is not None and prev_state.last_finger_id is not None:
            if cand.finger_id != prev_state.last_finger_id:
                finger_change = 1.0

        move_scale = self.cfg.rest_move_discount if is_rest else 1.0

        total = 0.0
        total += hand_move * self.cfg.cost_hand_move_per_key * move_scale
        total += spread_change * self.cfg.cost_spread_change_per_level * move_scale
        total += finger_change * self.cfg.cost_finger_change
        return total

    # --------------------------------------------------------
    # Action label
    # --------------------------------------------------------
    @staticmethod
    def _action_type(prev_state: State, cand: Candidate) -> str:
        hand_changed = (cand.pose.center_idx != prev_state.pose.center_idx)
        spread_changed = (cand.pose.spread != prev_state.pose.spread)
        finger_changed = (cand.finger_id != prev_state.last_finger_id)

        if cand.finger_id is None:
            if hand_changed or spread_changed:
                return "rest_reposition"
            return "rest_hold"

        if hand_changed and spread_changed:
            return "move_hand_and_finger"
        if hand_changed:
            return "move_hand"
        if spread_changed or finger_changed:
            return "finger_only"
        return "hold_shape"

    # --------------------------------------------------------
    # Start state
    # --------------------------------------------------------
    def _make_start_state(self, song: List[Tuple[str, float]]) -> State:
        if self.cfg.start_center_note is not None:
            n = normalize_note(self.cfg.start_center_note)
            if n not in self.white_note_to_idx:
                raise ValueError(f"start_center_note {n!r} is outside white-key range.")
            wanted_center_idx = self.white_note_to_idx[n]
        else:
            wanted_center_idx = None
            for note, _ in song:
                nn = normalize_note(note)
                if nn != "REST" and nn in self.white_note_to_idx:
                    wanted_center_idx = self.white_note_to_idx[nn]
                    break

            if wanted_center_idx is None:
                wanted_center_idx = len(self.white_keys) // 2

        wanted_spread = max(0, min(self.cfg.start_spread, len(self.cfg.spread_distances) - 1))

        if self._pose_valid(wanted_center_idx, wanted_spread):
            return State(Pose(wanted_center_idx, wanted_spread), None)

        best_pose = None
        best_dist = inf
        for p in self.valid_poses:
            dist = abs(p.center_idx - wanted_center_idx) + abs(p.spread - wanted_spread)
            if dist < best_dist:
                best_dist = dist
                best_pose = p

        if best_pose is None:
            raise RuntimeError("No valid starting pose found.")

        return State(best_pose, None)

    # --------------------------------------------------------
    # Main API
    # --------------------------------------------------------
    def plan_song(self, song: List[Tuple[str, float]]) -> List[dict]:
        """
        Input:
            song = [(note_name, duration_sec), ...]

        Output:
            planned_song = [
                {
                    "note": str,
                    "duration": float,
                    "center_note": str,
                    "spread": int,
                    "finger_id": str,     # "Lw", "Lb", "M", "Rb", "Rw", or "REST"
                    "action_type": str,
                },
                ...
            ]
        """
        if not song:
            return []

        # 1) Clean input
        clean_song: List[Tuple[str, float]] = []
        for note, dur in song:
            nn = normalize_note(note)

            try:
                dd = max(0.01, float(dur))
            except Exception:
                dd = 0.01

            if nn != "REST" and not self._note_supported_now(nn):
                if self.cfg.unsupported_note_as_rest:
                    nn = "REST"
                else:
                    raise ValueError(f"Unsupported note in current mode: {note!r}")

            clean_song.append((nn, dd))

        # 2) Start state
        start_state = self._make_start_state(clean_song)

        dp: Dict[State, float] = {start_state: 0.0}
        history: List[Dict[State, Tuple[Optional[State], dict]]] = []

        # 3) Dynamic programming
        for idx, (note, duration) in enumerate(clean_song):
            is_rest = (note == "REST")

            if is_rest:
                cands = self._rest_candidates()
                if not cands:
                    cands = [Candidate(pose=st.pose, finger_id=None) for st in dp.keys()]
            else:
                cands = self._note_candidates(note)

            if not cands:
                raise RuntimeError(f"No valid candidates at idx={idx}, note={note!r}")

            next_dp: Dict[State, float] = {}
            step_back: Dict[State, Tuple[Optional[State], dict]] = {}

            for prev_state, prev_cost in dp.items():
                for cand in cands:
                    new_state = State(
                        pose=cand.pose,
                        last_finger_id=cand.finger_id,
                    )

                    step_cost = self._transition_cost(prev_state, cand, is_rest=is_rest)
                    total_cost = prev_cost + step_cost

                    if new_state not in next_dp or total_cost < next_dp[new_state]:

                        if idx == 0:
                            action_type = self.cfg.first_note_action_type
                        else:
                            action_type = self._action_type(prev_state, cand)
                            
                        runtime_event = {
                            "note": note,
                            "duration": duration,
                            "center_note": self.white_keys[cand.pose.center_idx],
                            "spread": _pose_spread_to_output_level(cand.pose.spread),
                            "finger_id": cand.finger_id if cand.finger_id is not None else "REST",
                            "action_type": action_type,
                        }

                        runtime_event = _apply_edge_note_hw_override_plain(self, runtime_event)

                        next_dp[new_state] = total_cost
                        step_back[new_state] = (prev_state, runtime_event)

            dp = next_dp
            history.append(step_back)

        # 4) Choose best final state
        final_state = min(dp.keys(), key=lambda s: dp[s])

        # 5) Backtrack
        planned_rev: List[dict] = []
        cur = final_state

        for i in range(len(history) - 1, -1, -1):
            prev_state, event = history[i][cur]
            planned_rev.append(event)
            cur = prev_state

        return list(reversed(planned_rev))

    @staticmethod
    def pretty_print_plan(plan: List[dict]) -> None:
        for i, ev in enumerate(plan):
            print(
                f"[{i:02d}] "
                f"note={ev['note']:<4} dur={ev['duration']:.3f}  "
                f"center={ev['center_note']:<3} spread={ev['spread']}  "
                f"finger_id={ev['finger_id']:<4} action={ev['action_type']}"
            )
    # ============================================================
    # 4) Compatibility layer (range fix / chord sanitize)
    # ============================================================

@dataclass
class HandCompatibilityConfig:
    hand_name: str = "right"   # "right" or "left"
    try_local_octave_fix: bool = True
    max_octave_steps: int = 3
    merge_rests: bool = True


def _event_notes_to_list(notes_obj) -> List[str]:
    """
    Accept:
        ["C5", "E5", "G5"]
        "C5"
        []
        "REST"
    Return normalized note list, excluding REST.
    """
    if notes_obj is None:
        return []

    if isinstance(notes_obj, str):
        nn = normalize_note(notes_obj)
        if nn == "REST":
            return []
        return [nn]

    out = []
    for x in notes_obj:
        nn = normalize_note(x)
        if nn != "REST":
            out.append(nn)
    return out


def _unique_sorted_notes(notes: List[str]) -> List[str]:
    uniq = []
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


def _try_repair_note_by_octave(note: str,
                            planner: HandPlanner,
                            max_octave_steps: int = 3) -> Optional[str]:
    """
    Try moving only THIS note by +/- 12 semitones so it becomes
    supported by this hand planner.
    """
    nn = normalize_note(note)

    if nn == "REST":
        return None

    if planner._note_supported_now(nn):
        return nn

    base_midi = note_to_midi(nn)
    lo = note_to_midi(planner.cfg.note_min)
    hi = note_to_midi(planner.cfg.note_max)

    candidates = []

    for k in range(-max_octave_steps, max_octave_steps + 1):
        if k == 0:
            continue

        mm = base_midi + 12 * k
        if not (lo <= mm <= hi):
            continue

        cand = midi_to_note(mm)
        if planner._note_supported_now(cand):
            # prefer fewer octave moves, then smaller pitch displacement
            candidates.append((abs(k), abs(mm - base_midi), cand))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1], note_to_midi(x[2])))
    return candidates[0][2]

def _all_playable_subsets_for_hand(notes: List[str],
                                   planner: HandPlanner) -> List[List[str]]:
    notes = _unique_sorted_notes(notes)
    if not notes:
        return []

    playable: List[List[str]] = []

    for size in range(len(notes), 0, -1):
        level = []
        for combo in combinations(notes, size):
            combo_list = list(combo)
            if planner.can_play_notes_at_once(combo_list):
                level.append(combo_list)

        if level:
            playable.extend(level)
            break

    return playable

def sanitize_chord_for_hand(notes,
                            planner: HandPlanner,
                            compat_cfg: Optional[HandCompatibilityConfig] = None) -> List[str]:
    """
    Input:
        notes = ["C5", "E5", "G6"]

    Output example:
        ["C5", "E5", "G5"]
        or ["C5", "E5"]
        or []
    """
    compat_cfg = compat_cfg or HandCompatibilityConfig()

    raw_notes = _event_notes_to_list(notes)
    if not raw_notes:
        return []

    # 1) first pass: keep supported notes, try octave-fix unsupported notes
    repaired = []

    for n in raw_notes:
        if planner._note_supported_now(n):
            repaired.append(normalize_note(n))
            continue

        fixed = None
        if compat_cfg.try_local_octave_fix:
            fixed = _try_repair_note_by_octave(
                n,
                planner=planner,
                max_octave_steps=compat_cfg.max_octave_steps
            )

        if fixed is not None:
            repaired.append(fixed)

    repaired = _unique_sorted_notes(repaired)
    if not repaired:
        return []

    # 2) if whole repaired chord is playable by one pose, keep whole chord
    if planner.can_play_notes_at_once(repaired):
        return repaired

    # 3) otherwise choose the best mechanically-playable subset
    #    using musical ranking first
    playable_subsets = _all_playable_subsets_for_hand(
        repaired,
        planner=planner,
    )

    if playable_subsets:
        subset = choose_musical_subset(
            notes=repaired,
            playable_subsets=playable_subsets,
            hand_name=compat_cfg.hand_name,
            prev_subset=None,
        )
        if subset:
            return _unique_sorted_notes(subset)

    # fallback: old mechanical rule
    subset = planner.best_playable_subset(
        repaired,
        hand_name=compat_cfg.hand_name
    )

    return _unique_sorted_notes(subset)


def sanitize_hand_score(score_events,
                        planner: HandPlanner,
                        compat_cfg: Optional[HandCompatibilityConfig] = None):
    """
    Input:
        [
            (["C5", "E5", "G6"], 0.5),
            ([], 0.25),
            (["E5"], 0.4),
        ]

    Output:
        [
            (["C5", "E5", "G5"], 0.5),
            ([], 0.25),
            (["E5"], 0.4),
        ]
    """
    compat_cfg = compat_cfg or HandCompatibilityConfig()
    out = []

    for item in score_events:
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue

        notes_obj, dur = item[0], item[1]

        try:
            duration = max(0.01, float(dur))
        except Exception:
            duration = 0.01

        cleaned_notes = sanitize_chord_for_hand(
            notes_obj,
            planner=planner,
            compat_cfg=compat_cfg
        )

        if cleaned_notes:
            out.append((cleaned_notes, duration))
        else:
            out.append(([], duration))

    if compat_cfg.merge_rests:
        merged = []
        for notes, dur in out:
            if len(notes) == 0 and merged and len(merged[-1][0]) == 0:
                prev_notes, prev_dur = merged[-1]
                merged[-1] = (prev_notes, prev_dur + dur)
            else:
                merged.append((notes, dur))
        out = merged

    return out


def _finger_sort_key(fid: str) -> int:
    order = {"Lw": 0, "Lb": 1, "M": 2, "Rb": 3, "Rw": 4}
    return order.get(str(fid), 99)


def _ordered_unique_finger_ids(finger_ids: List[str]) -> List[str]:
    seen = set()
    out = []
    for fid in sorted(finger_ids, key=_finger_sort_key):
        if fid not in seen:
            out.append(fid)
            seen.add(fid)
    return out


def _notes_for_center_fallback(notes: List[str]) -> str:
    notes = _unique_sorted_notes(notes)
    if not notes:
        return ""
    mids = [note_to_midi(n) for n in notes]
    avg = sum(mids) / len(mids)
    best = min(notes, key=lambda n: (abs(note_to_midi(n) - avg), note_to_midi(n)))
    return best


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


def _event_action_type(prev_pose: Optional[Pose], pose: Pose, is_rest: bool, is_chord: bool) -> str:
    if is_rest:
        if prev_pose is None:
            return "rest_hold"
        if prev_pose.center_idx != pose.center_idx or prev_pose.spread != pose.spread:
            return "rest_reposition"
        return "rest_hold"

    if prev_pose is None:
        return "chord" if is_chord else "move_hand"

    hand_changed = prev_pose.center_idx != pose.center_idx
    spread_changed = prev_pose.spread != pose.spread

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



def sanitize_score_for_robot(score: dict,
                            right_planner: HandPlanner,
                            left_planner: HandPlanner):
    """
    score format:
    {
        "right": [
            (["C5", "E5", "G6"], 0.5),
            ...
        ],
        "left": [
            (["C2", "G2"], 0.5),
            ...
        ]
    }
    """
    right_cfg = HandCompatibilityConfig(
        hand_name="right",
        try_local_octave_fix=True,
        max_octave_steps=3,
        merge_rests=True,
    )

    left_cfg = HandCompatibilityConfig(
        hand_name="left",
        try_local_octave_fix=True,
        max_octave_steps=3,
        merge_rests=True,
    )

    return {
        "right": sanitize_hand_score(score.get("right", []), right_planner, right_cfg),
        "left": sanitize_hand_score(score.get("left", []), left_planner, left_cfg),
    }





if __name__ == "__main__":
    right_cfg = PlannerConfig(
        note_min="C4",
        note_max="F6",
        start_center_note=None,
        start_spread=0,
        center_note_min=None,
        center_note_max=None,
        enable_left_black_finger=True,
        enable_right_black_finger=True,
    )
    left_cfg = PlannerConfig(
        note_min="C2",
        note_max="B3",   # C4 归右手，避免重叠
        start_center_note="C3",
        start_spread=0,
        enable_left_black_finger=True,
        enable_right_black_finger=True,
    )

    right_planner = HandPlanner(right_cfg)
    left_planner = HandPlanner(left_cfg)

    raw_score = {
        "right": [
            (["C5", "E5", "G6"], 0.50),   # should prefer repairing to G5 if possible
            (["C5", "E5", "G5"], 0.50),
            (["F6", "A6"], 0.50),         # A6 may fold or be trimmed
            ([], 0.25),
        ],
        "left": [
            (["C2", "G2"], 0.50),
            (["B1", "D2", "G2"], 0.50),   # B1 may fold to B2
            (["C4", "E4"], 0.50),         # outside left-hand non-overlap range -> likely rest/trim
        ]
    }

    cleaned = sanitize_score_for_robot(
        raw_score,
        right_planner=right_planner,
        left_planner=left_planner,
    )

    print("RIGHT CLEANED:")
    for item in cleaned["right"]:
        print(item)

    print("\nLEFT CLEANED:")
    for item in cleaned["left"]:
        print(item)

# ============================================================
# 5) MIDI -> hand bridge
# ============================================================

def _chord_candidates_for_event(planner: HandPlanner, notes: List[str]):
    """
    Build all candidates that can cover the full note set with one pose.

    Flow:
    1) normal direct scan
    2) if not found and len(notes)==2 -> Scheme B inverse solve
    """
    wanted = _unique_sorted_notes(notes)
    if not wanted:
        return []

    raw_solutions = planner._direct_pose_solutions_for_notes(wanted)

    if not raw_solutions and len(wanted) == 2:
        raw_solutions = planner._inverse_two_note_solutions(wanted)

    out = []
    seen = set()

    for sol in raw_solutions:
        pose = sol["pose"]
        mapping = dict(sol["mapping"])
        finger_ids = _ordered_unique_finger_ids([mapping[n] for n in wanted])

        key = (pose.center_idx, pose.spread, tuple(finger_ids), tuple(sorted(mapping.items())))
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "pose": pose,
            "mapping": mapping,
            "finger_ids": finger_ids,
        })

    return out


def _finger_set_distance(prev_finger_ids, cur_finger_ids) -> int:
    prev_set = set(_ordered_unique_finger_ids(list(prev_finger_ids or [])))
    cur_set = set(_ordered_unique_finger_ids(list(cur_finger_ids or [])))
    return len(prev_set.symmetric_difference(cur_set))



def _event_static_pose_cost(planner: HandPlanner, pose: Pose, notes: List[str]) -> float:
    """
    Small tie-breaker so DP prefers:
    - smaller spread
    - center closer to chord center
    - center not too far from keyboard middle
    This should stay much smaller than real transition costs.
    """
    if not notes:
        return 0.01 * float(pose.spread)

    center_note = planner.white_keys[pose.center_idx]
    center_midi = note_to_midi(center_note)
    avg_target = sum(note_to_midi(n) for n in notes) / len(notes)

    return (
        0.05 * float(pose.spread)
        + 0.01 * abs(center_midi - avg_target)
        + 0.002 * abs(pose.center_idx - (len(planner.white_keys) // 2))
    )



def _transition_cost_for_event(planner: HandPlanner,
                               prev_pose: Pose,
                               prev_finger_ids,
                               pose: Pose,
                               finger_ids,
                               is_rest: bool) -> float:
    hand_move = abs(pose.center_idx - prev_pose.center_idx)
    spread_change = abs(pose.spread - prev_pose.spread)
    finger_change = _finger_set_distance(prev_finger_ids, finger_ids)

    move_scale = planner.cfg.rest_move_discount if is_rest else 1.0

    total = 0.0
    total += hand_move * planner.cfg.cost_hand_move_per_key * move_scale
    total += spread_change * planner.cfg.cost_spread_change_per_level * move_scale
    total += finger_change * planner.cfg.cost_finger_change
    return total



def _event_candidates_with_fallback(score_item,
                                    planner: HandPlanner,
                                    hand_name: str = "right"):
    notes, duration = _coerce_score_event(score_item)

    if not notes:
        cands = []
        for pose in planner.valid_poses:
            cands.append({
                "pose": pose,
                "mapping": {},
                "finger_ids": [],
                "notes": [],
                "duration": duration,
                "is_rest": True,
            })
        return cands

    candidates = _chord_candidates_for_event(planner, notes)

    if not candidates:
        subset = planner.best_playable_subset(notes, hand_name=hand_name)
        notes = _unique_sorted_notes(subset)
        if notes:
            candidates = _chord_candidates_for_event(planner, notes)

    if not candidates:
        # fully unplayable -> treat as rest so the planner can still continue
        cands = []
        for pose in planner.valid_poses:
            cands.append({
                "pose": pose,
                "mapping": {},
                "finger_ids": [],
                "notes": [],
                "duration": duration,
                "is_rest": True,
            })
        return cands

    out = []
    for cand in candidates:
        out.append({
            "pose": cand["pose"],
            "mapping": dict(cand["mapping"]),
            "finger_ids": _ordered_unique_finger_ids(list(cand["finger_ids"])),
            "notes": list(notes),
            "duration": duration,
            "is_rest": False,
        })
    return out



def plan_hand_score(score_events, planner: HandPlanner, hand_name: str = "right") -> List[dict]:
    """
    Convert one hand's score events:
        [([notes], dur), ...]
    into sequencer-ready events using dynamic programming across the FULL hand score.

    This is the missing bridge between:
        midi_loader -> sanitize_hand_score -> sequencer

    Compared with the earlier greedy version, this one can use REST events to
    reposition the hand for future chords and choose a lower-total-motion path
    across the whole sequence.
    """
    if not score_events:
        return []

    fake_song = []
    for item in score_events:
        notes, dur = _coerce_score_event(item)
        fake_song.append((notes[0], dur) if notes else ("REST", dur))

    start_state_obj = planner._make_start_state(fake_song if fake_song else [("REST", 0.1)])
    start_pose = start_state_obj.pose
    start_key = (start_pose.center_idx, start_pose.spread, tuple())

    prepared_steps = []
    for item in score_events:
        cands = _event_candidates_with_fallback(item, planner=planner, hand_name=hand_name)
        if not cands:
            # defensive fallback, should not happen because we create rest candidates
            cands = [{
                "pose": start_pose,
                "mapping": {},
                "finger_ids": [],
                "notes": [],
                "duration": 0.01,
                "is_rest": True,
            }]
        prepared_steps.append(cands)

    dp = {start_key: 0.0}
    backrefs = []

    for step_idx, cands in enumerate(prepared_steps):
        next_dp = {}
        step_back = {}

        for prev_key, prev_cost in dp.items():
            prev_pose = Pose(prev_key[0], prev_key[1])
            prev_finger_ids = list(prev_key[2])

            for cand in cands:
                pose = cand["pose"]
                finger_ids = tuple(_ordered_unique_finger_ids(cand["finger_ids"]))
                notes = list(cand["notes"])
                is_rest = bool(cand["is_rest"])

                state_key = (pose.center_idx, pose.spread, finger_ids)

                step_cost = _transition_cost_for_event(
                    planner=planner,
                    prev_pose=prev_pose,
                    prev_finger_ids=prev_finger_ids,
                    pose=pose,
                    finger_ids=finger_ids,
                    is_rest=is_rest,
                )
                step_cost += _event_static_pose_cost(planner, pose, notes)
                total_cost = prev_cost + step_cost

                if state_key not in next_dp or total_cost < next_dp[state_key]:
                    next_dp[state_key] = total_cost
                    step_back[state_key] = {
                        "prev_key": prev_key,
                        "pose": pose,
                        "notes": notes,
                        "duration": cand["duration"],
                        "finger_ids": list(finger_ids),
                        "mapping": dict(cand["mapping"]),
                        "is_rest": is_rest,
                        "step_idx": step_idx,
                    }

        dp = next_dp
        backrefs.append(step_back)

    def final_key_rank(key):
        pose = Pose(key[0], key[1])
        return (
            dp[key],
            pose.spread,
            abs(pose.center_idx - (len(planner.white_keys) // 2)),
            len(key[2]),
        )

    final_key = min(dp.keys(), key=final_key_rank)

    rev_events = []
    cur_key = final_key
    for step_idx in range(len(backrefs) - 1, -1, -1):
        record = backrefs[step_idx][cur_key]
        rev_events.append(record)
        cur_key = record["prev_key"]

    rev_events.reverse()

    planned = []
    prev_pose_for_action = start_pose

    for idx, rec in enumerate(rev_events):
        pose = rec["pose"]
        notes = _unique_sorted_notes(rec["notes"])
        duration = rec["duration"]
        finger_ids = _ordered_unique_finger_ids(rec["finger_ids"])
        is_rest = bool(rec["is_rest"])
        is_chord = len(notes) >= 2

        if idx == 0 and not is_rest:
            if is_chord:
                action_type = "chord"
            else:
                action_type = planner.cfg.first_note_action_type
        else:
            action_type = _event_action_type(
                prev_pose_for_action,
                pose,
                is_rest=is_rest,
                is_chord=is_chord,
            )

        print(
            f"[PLAN step {idx}] "
            f"notes={notes} "
            f"center_note={planner.white_keys[pose.center_idx]} "
            f"spread_internal={pose.spread} "
            f"spread_output={_pose_spread_to_output_level(pose.spread)} "
            f"finger_ids={finger_ids} "
            f"note_finger_map={dict(rec['mapping'])} "
            f"action_type={action_type}"
        )
        ev = {
            "notes": notes,
            "duration": duration,
            "center_note": planner.white_keys[pose.center_idx],
            "spread": _pose_spread_to_output_level(pose.spread),
            "finger_ids": finger_ids,
            "action_type": action_type,
            "note_finger_map": dict(rec["mapping"]),
        }

        # 如果是右手单音 C4/D4/E4，就强制改成 F4 + SF=0/2/4 + Lw
        if len(notes) == 1:
            one_note = normalize_note(notes[0])

            center_min = normalize_note(planner.cfg.center_note_min) if planner.cfg.center_note_min else ""
            center_max = normalize_note(planner.cfg.center_note_max) if planner.cfg.center_note_max else ""

            # 右手：保持原逻辑
            if center_min == "F4":
                override = RIGHT_LOW_NOTE_HW_OVERRIDE_PLAIN.get(one_note)
                if override is not None:
                    ev["center_note"] = override["center_note"]
                    ev["spread"] = int(override["spread"])
                    ev["finger_ids"] = [override["finger_id"]]
                    ev["note_finger_map"] = {one_note: override["finger_id"]}
                    ev["low_note_hw_override"] = True

            # 左手：新增 G3 / A3 / B3 特例
            elif center_max == "E3":
                override = LEFT_HIGH_NOTE_HW_OVERRIDE_PLAIN.get(one_note)
                if override is not None:
                    ev["center_note"] = override["center_note"]
                    ev["spread"] = int(override["spread"])
                    ev["finger_ids"] = [override["finger_id"]]
                    ev["note_finger_map"] = {one_note: override["finger_id"]}
                    ev["left_high_note_hw_override"] = True

        planned.append(ev)
        prev_pose_for_action = pose

    return planned


def plan_robot_score(score: dict,
                     right_planner: HandPlanner,
                     left_planner: HandPlanner):
    """
    score -> sequencer-ready dict for both hands
    """
    return {
        "right": plan_hand_score(score.get("right", []), right_planner, hand_name="right"),
        "left": plan_hand_score(score.get("left", []), left_planner, hand_name="left"),
    }


def load_and_plan_midi_for_robot(path: str,
                                 right_planner: HandPlanner,
                                 left_planner: HandPlanner,
                                 default_tempo: int = 500000,
                                 min_duration: float = 0.08,
                                 rest_merge: bool = True,
                                 split_note: str = "C4",
                                 verbose: bool = False):
    """
    One-stop bridge:
        MIDI -> score -> sanitized score -> sequencer-ready plans
    """
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

    clean_score = sanitize_score_for_robot(
        raw_score,
        right_planner=right_planner,
        left_planner=left_planner,
    )

    planned = plan_robot_score(
        clean_score,
        right_planner=right_planner,
        left_planner=left_planner,
    )

    return {
        "raw_score": raw_score,
        "clean_score": clean_score,
        "planned": planned,
    }
