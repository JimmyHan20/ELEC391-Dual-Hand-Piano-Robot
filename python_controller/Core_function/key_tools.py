from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    from mido import MidiFile
except Exception:
    MidiFile = None


NOTE_NAMES_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F',
                    'F#', 'G', 'G#', 'A', 'A#', 'B']

FLAT_TO_SHARP = {
    'DB': 'C#',
    'EB': 'D#',
    'GB': 'F#',
    'AB': 'G#',
    'BB': 'A#',
}

KEY_TO_PC = {
    'C': 0, 'B#': 0,
    'C#': 1, 'DB': 1,
    'D': 2,
    'D#': 3, 'EB': 3,
    'E': 4, 'FB': 4,
    'F': 5, 'E#': 5,
    'F#': 6, 'GB': 6,
    'G': 7,
    'G#': 8, 'AB': 8,
    'A': 9,
    'A#': 10, 'BB': 10,
    'B': 11, 'CB': 11,
}

PC_TO_KEY_SHARP = {
    0: 'C',
    1: 'C#',
    2: 'D',
    3: 'D#',
    4: 'E',
    5: 'F',
    6: 'F#',
    7: 'G',
    8: 'G#',
    9: 'A',
    10: 'A#',
    11: 'B',
}

MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]

MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


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


def normalize_key_name(key_name: str) -> Optional[str]:
    if key_name is None:
        return None
    s = str(key_name).strip().upper()
    s = FLAT_TO_SHARP.get(s, s)
    if s in KEY_TO_PC:
        return PC_TO_KEY_SHARP[KEY_TO_PC[s]]
    return None


def key_label(tonic: str, mode: str) -> str:
    mode = str(mode).strip().lower()
    if mode not in ("major", "minor"):
        return tonic
    return f"{tonic} {mode}"


def semitone_delta(src_tonic: str, dst_tonic: str) -> int:
    src = normalize_key_name(src_tonic)
    dst = normalize_key_name(dst_tonic)
    if src is None or dst is None:
        raise ValueError(f"Bad key names: {src_tonic!r}, {dst_tonic!r}")

    delta = KEY_TO_PC[dst] - KEY_TO_PC[src]
    if delta > 6:
        delta -= 12
    elif delta < -6:
        delta += 12
    return delta


def transpose_note(note: str, semitones: int) -> str:
    nn = normalize_note(note)
    if nn == "REST":
        return "REST"
    return midi_to_note(note_to_midi(nn) + int(semitones))


def transpose_score(score: Dict[str, List[Tuple[List[str], float]]], semitones: int):
    out = {"right": [], "left": []}
    semitones = int(semitones)

    for hand in ("right", "left"):
        for notes, dur in score.get(hand, []):
            new_notes = [transpose_note(n, semitones) for n in notes]
            out[hand].append((new_notes, dur))

    return out


def _rotate_profile(profile: List[float], shift: int) -> List[float]:
    shift %= 12
    return profile[-shift:] + profile[:-shift]


def _score_histogram_against_profile(hist: List[float], profile: List[float]) -> float:
    return sum(h * p for h, p in zip(hist, profile))


def _score_events_iter(score):
    for hand in ("right", "left"):
        for item in score.get(hand, []):
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            notes_obj, dur = item[0], item[1]
            try:
                duration = max(0.01, float(dur))
            except Exception:
                duration = 0.01

            if isinstance(notes_obj, str):
                notes = [] if normalize_note(notes_obj) == "REST" else [normalize_note(notes_obj)]
            else:
                notes = []
                for x in (notes_obj or []):
                    nn = normalize_note(x)
                    if nn != "REST":
                        notes.append(nn)

            yield hand, notes, duration


def _pitch_class_histogram(score) -> List[float]:
    hist = [0.0] * 12

    for hand, notes, duration in _score_events_iter(score):
        if not notes:
            continue

        weight = duration
        # 稍微偏向右手旋律和左手 bass
        hand_boost = 1.15 if hand == "right" else 1.0

        for note in notes:
            pc = note_to_midi(note) % 12
            hist[pc] += weight * hand_boost

    s = sum(hist)
    if s <= 1e-9:
        return hist

    return [x / s for x in hist]


def detect_key_from_midi_meta(path: str):
    if MidiFile is None:
        return None

    try:
        mid = MidiFile(path)
    except Exception:
        return None

    for track in mid.tracks:
        for msg in track:
            if getattr(msg, "type", None) != "key_signature":
                continue

            raw = str(getattr(msg, "key", "")).strip()
            if not raw:
                continue

            # mido 常见形式: "C", "G", "F#", "Bb", "Am", "F#m"
            if raw.lower().endswith("m"):
                tonic_raw = raw[:-1]
                tonic = normalize_key_name(tonic_raw)
                if tonic is None:
                    continue
                return {
                    "tonic": tonic,
                    "mode": "minor",
                    "label": key_label(tonic, "minor"),
                    "source": "midi_meta",
                    "confidence": 1.0,
                }
            else:
                tonic = normalize_key_name(raw)
                if tonic is None:
                    continue
                return {
                    "tonic": tonic,
                    "mode": "major",
                    "label": key_label(tonic, "major"),
                    "source": "midi_meta",
                    "confidence": 1.0,
                }

    return None


def estimate_key_from_score(score):
    hist = _pitch_class_histogram(score)

    if sum(hist) <= 1e-9:
        return {
            "tonic": "C",
            "mode": "major",
            "label": "C major",
            "source": "estimated",
            "confidence": 0.0,
        }

    candidates = []

    for tonic_pc in range(12):
        major_score = _score_histogram_against_profile(hist, _rotate_profile(MAJOR_PROFILE, tonic_pc))
        minor_score = _score_histogram_against_profile(hist, _rotate_profile(MINOR_PROFILE, tonic_pc))

        candidates.append((major_score, tonic_pc, "major"))
        candidates.append((minor_score, tonic_pc, "minor"))

    candidates.sort(reverse=True, key=lambda x: x[0])

    best_score, best_pc, best_mode = candidates[0]
    second_score = candidates[1][0] if len(candidates) >= 2 else 0.0

    confidence = max(0.0, best_score - second_score)

    tonic = PC_TO_KEY_SHARP[best_pc]
    return {
        "tonic": tonic,
        "mode": best_mode,
        "label": key_label(tonic, best_mode),
        "source": "estimated",
        "confidence": round(confidence, 4),
    }


def detect_key(path: str, raw_score=None):
    meta = detect_key_from_midi_meta(path)
    if meta is not None:
        return meta

    if raw_score is not None:
        return estimate_key_from_score(raw_score)

    return {
        "tonic": "C",
        "mode": "major",
        "label": "C major",
        "source": "fallback",
        "confidence": 0.0,
    }


def _hand_quality(score_events, planner) -> Dict[str, float]:
    total_notes = 0
    supported_notes = 0
    total_events = 0
    fully_playable_events = 0

    for notes, _dur in score_events:
        notes = list(notes or [])
        if not notes:
            continue

        total_events += 1
        total_notes += len(notes)

        supported = []
        for n in notes:
            try:
                ok = planner._note_supported_now(n)
            except Exception:
                ok = False
            if ok:
                supported.append(n)

        supported_notes += len(supported)

        try:
            if supported and planner.can_play_notes_at_once(supported):
                fully_playable_events += 1
        except Exception:
            pass

    note_support_ratio = 1.0 if total_notes == 0 else (supported_notes / total_notes)
    event_playable_ratio = 1.0 if total_events == 0 else (fully_playable_events / total_events)

    quality_score = 0.65 * note_support_ratio + 0.35 * event_playable_ratio

    return {
        "total_notes": total_notes,
        "supported_notes": supported_notes,
        "total_events": total_events,
        "fully_playable_events": fully_playable_events,
        "note_support_ratio": round(note_support_ratio, 4),
        "event_playable_ratio": round(event_playable_ratio, 4),
        "quality_score": round(quality_score, 4),
    }


def evaluate_transposed_score_for_robot(raw_score, semitones: int, right_planner, left_planner):
    shifted = transpose_score(raw_score, semitones)

    right_q = _hand_quality(shifted.get("right", []), right_planner)
    left_q = _hand_quality(shifted.get("left", []), left_planner)

    total_notes = right_q["total_notes"] + left_q["total_notes"]
    supported_notes = right_q["supported_notes"] + left_q["supported_notes"]
    total_events = right_q["total_events"] + left_q["total_events"]
    playable_events = right_q["fully_playable_events"] + left_q["fully_playable_events"]

    note_support_ratio = 1.0 if total_notes == 0 else (supported_notes / total_notes)
    event_playable_ratio = 1.0 if total_events == 0 else (playable_events / total_events)
    quality_score = 0.65 * note_support_ratio + 0.35 * event_playable_ratio

    return {
        "shifted_score": shifted,
        "note_support_ratio": round(note_support_ratio, 4),
        "event_playable_ratio": round(event_playable_ratio, 4),
        "quality_score": round(quality_score, 4),
        "right": right_q,
        "left": left_q,
    }


def get_viable_target_keys(raw_score,
                           detected_key: Dict[str, str],
                           right_planner,
                           left_planner,
                           max_abs_shift: int = 5,
                           min_quality: float = 0.78):
    """
    只返回和原曲同 mode 的目标调：
    - major -> major
    - minor -> minor

    再按当前机器人/手型的可弹性过滤。
    """
    src_tonic = normalize_key_name(detected_key.get("tonic", "C")) or "C"
    mode = str(detected_key.get("mode", "major")).strip().lower()
    if mode not in ("major", "minor"):
        mode = "major"

    out = []

    for tonic in [PC_TO_KEY_SHARP[i] for i in range(12)]:
        shift = semitone_delta(src_tonic, tonic)
        if abs(shift) > int(max_abs_shift):
            continue

        quality = evaluate_transposed_score_for_robot(
            raw_score=raw_score,
            semitones=shift,
            right_planner=right_planner,
            left_planner=left_planner,
        )

        if quality["quality_score"] >= float(min_quality):
            out.append({
                "tonic": tonic,
                "mode": mode,
                "label": key_label(tonic, mode),
                "shift": shift,
                "quality_score": quality["quality_score"],
                "note_support_ratio": quality["note_support_ratio"],
                "event_playable_ratio": quality["event_playable_ratio"],
            })

    out.sort(key=lambda x: (-float(x["quality_score"]), abs(int(x["shift"])), x["tonic"]))

    # 保证原调一定在最前面
    out.sort(key=lambda x: (x["tonic"] != src_tonic, -float(x["quality_score"]), abs(int(x["shift"]))))

    return out