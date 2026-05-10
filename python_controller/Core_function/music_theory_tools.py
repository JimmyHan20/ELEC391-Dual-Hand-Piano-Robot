from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

NOTE_NAMES_SHARP = [
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B",
]
FLAT_TO_SHARP = {
    "DB": "C#",
    "EB": "D#",
    "GB": "F#",
    "AB": "G#",
    "BB": "A#",
}


# ============================================================
# Basic helpers
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


def pitch_class(note: str) -> int:
    return note_to_midi(note) % 12


def unique_sorted_notes(notes: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for n in notes:
        nn = normalize_note(n)
        if nn == "REST" or nn in seen:
            continue
        seen.add(nn)
        out.append(nn)
    out.sort(key=note_to_midi)
    return out


# ============================================================
# Chord-role analysis
# ============================================================

def _role_priority_key(role_name: str) -> int:
    order = {
        "root": 0,
        "third": 1,
        "seventh": 2,
        "fifth": 3,
        "sixth_or_thirteenth": 4,
        "second_or_ninth": 5,
        "fourth_or_eleventh": 6,
        "other": 7,
    }
    return order.get(role_name, 99)


def _classify_interval(interval: int) -> str:
    interval %= 12
    if interval == 0:
        return "root"
    if interval in (3, 4):
        return "third"
    if interval == 7:
        return "fifth"
    if interval in (10, 11):
        return "seventh"
    if interval in (8, 9):
        return "sixth_or_thirteenth"
    if interval in (1, 2):
        return "second_or_ninth"
    if interval in (5, 6):
        return "fourth_or_eleventh"
    return "other"


def _root_fit_score(root_pc: int, notes: Sequence[str]) -> Tuple[int, int, int, int]:
    pcs = {pitch_class(n) for n in notes}
    has_third = int(((root_pc + 3) % 12 in pcs) or ((root_pc + 4) % 12 in pcs))
    has_fifth = int((root_pc + 7) % 12 in pcs)
    has_seventh = int(((root_pc + 10) % 12 in pcs) or ((root_pc + 11) % 12 in pcs))
    extension_count = sum(
        1 for interval in (1, 2, 5, 6, 8, 9)
        if (root_pc + interval) % 12 in pcs
    )
    return (has_third, has_seventh, has_fifth, extension_count)


def analyze_chord_roles(notes: Sequence[str]) -> Dict[str, object]:
    """
    Lightweight theory analysis for one simultaneous event.

    The goal is not full Roman-numeral harmony. We only need enough information
    to decide which notes are structurally important when the robot must delete
    something.

    Output includes:
    - inferred root pitch class
    - role classification for each original note
    - highest / lowest note
    - presence flags for root / 3rd / 5th / 7th
    """
    uniq = unique_sorted_notes(notes)
    if not uniq:
        return {
            "root_pc": None,
            "highest_note": None,
            "lowest_note": None,
            "roles_by_note": {},
            "has_root": False,
            "has_third": False,
            "has_fifth": False,
            "has_seventh": False,
        }

    best_root_pc = None
    best_key = None
    for n in uniq:
        root_pc = pitch_class(n)
        fit = _root_fit_score(root_pc, uniq)
        # Prefer candidate roots that are actually present in the bass area.
        bass_bias = -abs(note_to_midi(n) - note_to_midi(uniq[0]))
        key = (*fit, bass_bias, -root_pc)
        if best_key is None or key > best_key:
            best_key = key
            best_root_pc = root_pc

    roles_by_note: Dict[str, str] = {}
    has_root = has_third = has_fifth = has_seventh = False
    for n in uniq:
        role = _classify_interval((pitch_class(n) - best_root_pc) % 12)
        roles_by_note[n] = role
        if role == "root":
            has_root = True
        elif role == "third":
            has_third = True
        elif role == "fifth":
            has_fifth = True
        elif role == "seventh":
            has_seventh = True

    return {
        "root_pc": best_root_pc,
        "highest_note": uniq[-1],
        "lowest_note": uniq[0],
        "roles_by_note": roles_by_note,
        "has_root": has_root,
        "has_third": has_third,
        "has_fifth": has_fifth,
        "has_seventh": has_seventh,
    }


# ============================================================
# Musical-retention scoring
# ============================================================

def prefer_melody_bass_retention(
    original_notes: Sequence[str],
    subset: Sequence[str],
    hand_name: str,
) -> float:
    original = unique_sorted_notes(original_notes)
    kept = set(unique_sorted_notes(subset))
    if not original or not kept:
        return 0.0

    score = 0.0
    highest = original[-1]
    lowest = original[0]

    # Highest note matters a lot because it often behaves like melody / top voice.
    if highest in kept:
        score += 80.0 if str(hand_name).lower() == "right" else 40.0

    # Lowest note matters a lot because it often behaves like bass / harmonic anchor.
    if lowest in kept:
        score += 80.0 if str(hand_name).lower() == "left" else 40.0

    return score


def _common_tone_bonus(prev_subset: Optional[Sequence[str]], subset: Sequence[str]) -> float:
    if not prev_subset:
        return 0.0
    prev_set = {normalize_note(n) for n in prev_subset if normalize_note(n) != "REST"}
    cur_set = {normalize_note(n) for n in subset if normalize_note(n) != "REST"}
    return 12.0 * len(prev_set & cur_set)


def score_subset_by_theory(
    original_notes: Sequence[str],
    subset: Sequence[str],
    hand_name: str,
    prev_subset: Optional[Sequence[str]] = None,
) -> float:
    """
    Score one mechanically playable subset.

    Design principles:
    1) Delete as few notes as possible.
    2) Preserve melody / bass anchors.
    3) Preserve harmony-defining notes first (3rd / 7th), then root, then 5th.
    4) Prefer removing duplicates / color tones before deleting the chord core.
    5) Slightly reward common tones with the previous kept subset.
    """
    original = unique_sorted_notes(original_notes)
    kept = unique_sorted_notes(subset)
    kept_set = set(kept)
    if not kept:
        return float("-inf")

    analysis = analyze_chord_roles(original)
    roles_by_note = analysis["roles_by_note"]

    score = 0.0

    # Keep-note-count pressure stays strong so the system does not over-delete.
    score += 1000.0 * len(kept)

    # Melody / bass anchors.
    score += prefer_melody_bass_retention(original, kept, hand_name)

    # Core harmony retention.
    for note in kept:
        role = roles_by_note.get(note, "other")
        if role == "root":
            score += 45.0
        elif role == "third":
            score += 65.0
        elif role == "seventh":
            score += 55.0
        elif role == "fifth":
            score += 20.0
        elif role == "sixth_or_thirteenth":
            score += 8.0
        elif role == "second_or_ninth":
            score += 6.0
        elif role == "fourth_or_eleventh":
            score += 5.0
        else:
            score += 2.0

    # Penalties for losing structurally important roles.
    missing_roles = []
    for note in original:
        if note in kept_set:
            continue
        role = roles_by_note.get(note, "other")
        missing_roles.append(role)
        if role == "third":
            score -= 55.0
        elif role == "seventh":
            score -= 42.0
        elif role == "root":
            score -= 35.0
        elif role == "fifth":
            score -= 10.0
        elif role in ("sixth_or_thirteenth", "second_or_ninth", "fourth_or_eleventh"):
            score -= 4.0
        else:
            score -= 2.0

    # Special-case penalties for musically weak reductions.
    kept_roles = {roles_by_note.get(n, "other") for n in kept}
    if "root" in kept_roles and "fifth" in kept_roles and "third" not in kept_roles and "seventh" not in kept_roles:
        score -= 35.0
    if str(hand_name).lower() == "left" and "root" not in kept_roles and len(original) >= 2:
        score -= 30.0
    if str(hand_name).lower() == "right" and original[-1] not in kept_set and len(original) >= 2:
        score -= 25.0

    # Slight reward for closer spacing after reduction.
    mids = [note_to_midi(n) for n in kept]
    span = (max(mids) - min(mids)) if len(mids) >= 2 else 0
    score -= 0.3 * span

    # Smoothness between neighboring events.
    score += _common_tone_bonus(prev_subset, kept)

    # Stable tie-break: prefer retaining higher notes on right hand, lower notes on left.
    if str(hand_name).lower() == "right":
        score += 0.05 * sum(note_to_midi(n) for n in kept)
    else:
        score -= 0.05 * sum(note_to_midi(n) for n in kept)

    return score


# ============================================================
# Subset chooser (public API)
# ============================================================

def choose_musical_subset(
    notes: Sequence[str],
    playable_subsets: Iterable[Sequence[str]],
    hand_name: str,
    prev_subset: Optional[Sequence[str]] = None,
) -> List[str]:
    """
    Choose one subset from already mechanically playable candidates.

    Important boundary:
    - This function does NOT decide whether a subset is playable.
    - It only ranks subsets that the planner has already confirmed are playable.
    """
    original = unique_sorted_notes(notes)
    playable = [unique_sorted_notes(x) for x in playable_subsets if unique_sorted_notes(x)]
    if not playable:
        return []

    max_size = max(len(x) for x in playable)
    playable = [x for x in playable if len(x) == max_size]

    def key(subset: Sequence[str]):
        theory_score = score_subset_by_theory(
            original_notes=original,
            subset=subset,
            hand_name=hand_name,
            prev_subset=prev_subset,
        )
        role_rank = tuple(
            _role_priority_key(analyze_chord_roles(original)["roles_by_note"].get(n, "other"))
            for n in subset
        )
        return (
            theory_score,
            -len(subset),
            tuple(note_to_midi(n) for n in subset),
            tuple(-x for x in role_rank),
        )

    return list(max(playable, key=key))
