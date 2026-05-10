# midi_loader.py
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from statistics import mean
from mido import MidiFile, merge_tracks, tick2second

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F',
              'F#', 'G', 'G#', 'A', 'A#', 'B']

# GM program families
PIANO_PROGRAMS = set(range(0, 8))       # Acoustic/EP/etc. piano
GUITAR_PROGRAMS = set(range(24, 32))    # Guitar family
BASS_PROGRAMS = set(range(32, 40))      # Bass family
STRINGS_PROGRAMS = set(range(40, 52))   # Strings/ensemble family
DRUM_CHANNEL = 9                        # MIDI channel 10 -> zero-based 9


def midi_note_to_name(note_num: int) -> str:
    name = NOTE_NAMES[note_num % 12]
    octave = (note_num // 12) - 1
    return f"{name}{octave}"


def note_name_to_midi_num(name: str) -> int:
    if len(name) < 2:
        raise ValueError(f"Bad note name: {name}")

    if name[1] == '#':
        pitch = name[:2]
        octave = int(name[2:])
    else:
        pitch = name[:1]
        octave = int(name[1:])

    return NOTE_NAMES.index(pitch) + (octave + 1) * 12


def _merge_rest(song, rest_dur: float):
    if rest_dur <= 0:
        return

    if song and len(song[-1][0]) == 0:
        prev_notes, prev_dur = song[-1]
        song[-1] = (prev_notes, prev_dur + rest_dur)
    else:
        song.append(([], rest_dur))


def _append_group(song, notes, duration: float):
    notes = list(notes)
    if len(notes) == 0:
        _merge_rest(song, duration)
    else:
        song.append((notes, duration))


def _build_tempo_segments(mid: MidiFile, default_tempo: int = 500000):
    merged = merge_tracks(mid.tracks)

    abs_tick = 0
    tempo_changes = [(0, default_tempo)]

    for msg in merged:
        abs_tick += msg.time
        if msg.type == 'set_tempo':
            if tempo_changes and tempo_changes[-1][0] == abs_tick:
                tempo_changes[-1] = (abs_tick, msg.tempo)
            else:
                tempo_changes.append((abs_tick, msg.tempo))

    segments = []
    current_sec = 0.0

    for i, (tick_pos, tempo) in enumerate(tempo_changes):
        if i == 0:
            segments.append({
                "start_tick": tick_pos,
                "start_sec": 0.0,
                "tempo": tempo,
            })
            continue

        prev_tick, prev_tempo = tempo_changes[i - 1]
        current_sec += tick2second(
            tick_pos - prev_tick,
            mid.ticks_per_beat,
            prev_tempo
        )

        segments.append({
            "start_tick": tick_pos,
            "start_sec": current_sec,
            "tempo": tempo,
        })

    return segments


def _ticks_to_seconds(abs_tick: int, ticks_per_beat: int, tempo_segments) -> float:
    starts = [seg["start_tick"] for seg in tempo_segments]
    idx = bisect_right(starts, abs_tick) - 1
    if idx < 0:
        idx = 0

    seg = tempo_segments[idx]
    delta_tick = abs_tick - seg["start_tick"]

    return seg["start_sec"] + tick2second(delta_tick, ticks_per_beat, seg["tempo"])


def analyze_track(track) -> dict:
    note_count = 0
    notes = []
    program_by_channel = {}
    piano_program_hits = 0
    drum_note_count = 0

    for msg in track:
        if msg.type == 'program_change':
            ch = getattr(msg, 'channel', None)
            program = getattr(msg, 'program', None)
            if ch is not None and program is not None:
                program_by_channel[ch] = program

        elif msg.type == 'note_on' and getattr(msg, 'velocity', 0) > 0:
            note_count += 1
            note_num = int(msg.note)
            notes.append(note_num)

            ch = getattr(msg, 'channel', None)
            if ch == 9:
                drum_note_count += 1
            elif ch in program_by_channel and program_by_channel[ch] in range(0, 8):
                piano_program_hits += 1

    avg_pitch = mean(notes) if notes else None
    min_pitch = min(notes) if notes else None
    max_pitch = max(notes) if notes else None
    pitch_range = (max_pitch - min_pitch) if notes else 0

    likely_drum = (note_count > 0 and drum_note_count == note_count)
    explicit_piano = (piano_program_hits > 0) and (not likely_drum)

    # 没写 piano program 时的弱判断
    piano_like = (
        note_count >= 8 and
        pitch_range >= 12 and
        not likely_drum
    )

    return {
        "note_count": note_count,
        "avg_pitch": avg_pitch,
        "pitch_range": pitch_range,
        "likely_drum": likely_drum,
        "explicit_piano": explicit_piano,
        "piano_like": piano_like,
        "program_by_channel": dict(program_by_channel),
    }


def _extract_note_events_from_track(mid: MidiFile, track_index: int):
    """
    Parse one MIDI track only.

    Return:
        events = [
            (start_tick, end_tick, note_name),
            ...
        ]
    """
    if track_index < 0 or track_index >= len(mid.tracks):
        raise IndexError(
            f"track_index={track_index} out of range. "
            f"This MIDI has {len(mid.tracks)} tracks."
        )

    track = mid.tracks[track_index]

    abs_tick = 0
    active_notes = defaultdict(deque)
    events = []

    for msg in track:
        abs_tick += msg.time

        if msg.type == 'note_on' and msg.velocity > 0:
            active_notes[msg.note].append(abs_tick)

        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            if active_notes[msg.note]:
                start_tick = active_notes[msg.note].popleft()
                end_tick = abs_tick
                if end_tick > start_tick:
                    note_name = midi_note_to_name(msg.note)
                    events.append((start_tick, end_tick, note_name))

    events.sort(key=lambda x: (x[0], note_name_to_midi_num(x[2])))
    return events


def _group_track_events_to_song(
    events,
    ticks_per_beat: int,
    tempo_segments,
    min_duration: float = 0.08,
    rest_merge: bool = True,
    max_song_seconds: float | None = None,
):
    """
    Convert:
        [(start_tick, end_tick, note_name), ...]

    To:
        [
            (["C4", "E4", "G4"], 0.5),
            ([], 0.25),
            ...
        ]

    Rule:
    - same start_tick => same chord/group
    - duration of group = longest note in that same-start group
    """
    song = []
    prev_end_sec = 0.0

    i = 0
    n = len(events)

    while i < n:
        start_tick = events[i][0]
        same_start = [events[i]]

        j = i + 1
        while j < n and events[j][0] == start_tick:
            same_start.append(events[j])
            j += 1

        start_sec = _ticks_to_seconds(start_tick, ticks_per_beat, tempo_segments)

        # 已经超过时间上限，直接停止
        if max_song_seconds is not None and start_sec >= max_song_seconds:
            break

        end_tick = max(ev[1] for ev in same_start)
        end_sec = _ticks_to_seconds(end_tick, ticks_per_beat, tempo_segments)

        # 如果这个事件跨过了上限，就截断
        if max_song_seconds is not None and end_sec > max_song_seconds:
            end_sec = max_song_seconds

        duration = end_sec - start_sec

        if duration >= min_duration:
            gap = start_sec - prev_end_sec

            if gap >= min_duration:
                if rest_merge:
                    _merge_rest(song, gap)
                else:
                    song.append(([], gap))

            notes = sorted(
                [ev[2] for ev in same_start],
                key=note_name_to_midi_num
            )

            unique_notes = []
            seen = set()
            for note in notes:
                if note not in seen:
                    unique_notes.append(note)
                    seen.add(note)

            song.append((unique_notes, duration))
            prev_end_sec = max(prev_end_sec, end_sec)

        i = j

    return song


def _build_song_from_track(
    mid: MidiFile,
    track_index: int,
    tempo_segments,
    min_duration: float = 0.08,
    rest_merge: bool = True,
    max_song_seconds: float | None = None,
):
    events = _extract_note_events_from_track(mid, track_index)
    return _group_track_events_to_song(
        events=events,
        ticks_per_beat=mid.ticks_per_beat,
        tempo_segments=tempo_segments,
        min_duration=min_duration,
        rest_merge=rest_merge,
        max_song_seconds=max_song_seconds,
    )

def _split_single_song_by_pitch(song, split_note: str = "C4"):
    """
    Split one piano song into left/right by pitch threshold.

    Rule:
      midi < split_note  -> left
      midi >= split_note -> right
    """
    split_midi = note_name_to_midi_num(split_note)

    left_song = []
    right_song = []

    for notes, dur in song:
        if not notes:
            _append_group(left_song, [], dur)
            _append_group(right_song, [], dur)
            continue

        left_notes = []
        right_notes = []

        for note in notes:
            if note_name_to_midi_num(note) < split_midi:
                left_notes.append(note)
            else:
                right_notes.append(note)

        _append_group(left_song, left_notes, dur)
        _append_group(right_song, right_notes, dur)

    return right_song, left_song


def choose_basic_tracks(mid: MidiFile):
    analysis = []
    for idx, track in enumerate(mid.tracks):
        info = analyze_track(track)
        analysis.append((idx, info))

    explicit_piano_candidates = [
        (idx, info) for idx, info in analysis
        if info["note_count"] > 0 and info["explicit_piano"]
    ]

    piano_like_candidates = [
        (idx, info) for idx, info in analysis
        if info["note_count"] > 0 and info["piano_like"]
    ]

    fallback_candidates = [
        (idx, info) for idx, info in analysis
        if info["note_count"] > 0 and not info["likely_drum"]
    ]

    if explicit_piano_candidates:
        candidates = explicit_piano_candidates
        candidate_stage = "explicit_piano"
    elif piano_like_candidates:
        candidates = piano_like_candidates
        candidate_stage = "piano_like"
    elif fallback_candidates:
        candidates = fallback_candidates
        candidate_stage = "fallback_non_drum"
    else:
        return {
            "mode": "empty",
            "right_track": None,
            "left_track": None,
            "single_track": None,
            "analysis": analysis,
            "candidate_stage": "empty",
        }

    if len(candidates) == 1:
        return {
            "mode": "single_track_split",
            "right_track": None,
            "left_track": None,
            "single_track": candidates[0][0],
            "analysis": analysis,
            "candidate_stage": candidate_stage,
        }

    candidate_ids = [idx for idx, _ in candidates]

    # 优先保留 1 和 2
    if 1 in candidate_ids and 2 in candidate_ids:
        info1 = dict(candidates)[1]
        info2 = dict(candidates)[2]

        if (info1["avg_pitch"] or -999) >= (info2["avg_pitch"] or -999):
            right_track, left_track = 1, 2
        else:
            right_track, left_track = 2, 1

        return {
            "mode": "dual_track",
            "right_track": right_track,
            "left_track": left_track,
            "single_track": None,
            "analysis": analysis,
            "candidate_stage": candidate_stage,
        }

    # 否则按优先级排序：
    # 1) 更高阶段分数
    # 2) note_count 更多
    # 3) pitch_range 更宽
    # 4) 轨号更小
    def rank_key(item):
        idx, info = item
        return (
            -int(info["explicit_piano"]),
            -int(info["piano_like"]),
            -info["note_count"],
            -info["pitch_range"],
            idx
        )

    ranked = sorted(candidates, key=rank_key)[:2]
    (idx_a, info_a), (idx_b, info_b) = ranked

    # 平均音高更高的更像右手
    if (info_a["avg_pitch"] or -999) >= (info_b["avg_pitch"] or -999):
        right_track, left_track = idx_a, idx_b
    else:
        right_track, left_track = idx_b, idx_a

    return {
        "mode": "dual_track",
        "right_track": right_track,
        "left_track": left_track,
        "single_track": None,
        "analysis": analysis,
        "candidate_stage": candidate_stage,
    }


def load_track_song_from_midi(
    path: str,
    track_index: int,
    default_tempo: int = 500000,
    min_duration: float = 0.08,
    rest_merge: bool = True,
    max_song_seconds: float | None = None,
):  
    mid = MidiFile(path)
    tempo_segments = _build_tempo_segments(mid, default_tempo=default_tempo)
    return _build_song_from_track(
        mid=mid,
        track_index=track_index,
        tempo_segments=tempo_segments,
        min_duration=min_duration,
        rest_merge=rest_merge,
        max_song_seconds=max_song_seconds,
    )


def load_score_from_midi(
    path: str,
    default_tempo: int = 500000,
    min_duration: float = 0.08,
    rest_merge: bool = True,
    split_note: str = "C4",
    verbose: bool = False,
    max_song_seconds: float | None = 90.0,
):
    """
    Main API.

    Return:
        {
            "right": [...],
            "left": [...],
        }

    Strategy:
    1) Prefer explicit piano tracks
    2) Otherwise prefer piano-like tracks
    3) Otherwise fallback to non-drum tracks
    4) If only one valid track remains, split by pitch
    """
    mid = MidiFile(path)
    tempo_segments = _build_tempo_segments(mid, default_tempo=default_tempo)

    choice = choose_basic_tracks(mid)

    if verbose:
        print(f"[MIDI] total tracks = {len(mid.tracks)}")
        for idx, info in choice["analysis"]:
            print(
                f"[MIDI] Track {idx}: "
                f"notes={info['note_count']}, "
                f"avg_pitch={info['avg_pitch']}, "
                f"range={info['pitch_range']}, "
                f"likely_drum={info['likely_drum']}, "
                f"explicit_piano={info['explicit_piano']}, "
                f"piano_like={info['piano_like']}, "
                f"programs={info['program_by_channel']}"
            )
        print(f"[MIDI] candidate stage = {choice['candidate_stage']}")
        print(f"[MIDI] chosen mode = {choice['mode']}")
        print(f"[MIDI] chosen right_track = {choice['right_track']}")
        print(f"[MIDI] chosen left_track  = {choice['left_track']}")
        print(f"[MIDI] chosen single_track = {choice['single_track']}")

    if choice["mode"] == "empty":
        return {
            "right": [],
            "left": [],
        }

    if choice["mode"] == "dual_track":
        right_song = _build_song_from_track(
            mid=mid,
            track_index=choice["right_track"],
            tempo_segments=tempo_segments,
            min_duration=min_duration,
            rest_merge=rest_merge,
            max_song_seconds=max_song_seconds,
        )
        left_song = _build_song_from_track(
            mid=mid,
            track_index=choice["left_track"],
            tempo_segments=tempo_segments,
            min_duration=min_duration,
            rest_merge=rest_merge,
            max_song_seconds=max_song_seconds,
        )
        return {
            "right": right_song,
            "left": left_song,
        }

    single_song = _build_song_from_track(
        mid=mid,
        track_index=choice["single_track"],
        tempo_segments=tempo_segments,
        min_duration=min_duration,
        rest_merge=rest_merge,
        max_song_seconds=max_song_seconds,
    )

    right_song, left_song = _split_single_song_by_pitch(
        single_song,
        split_note=split_note
    )

    return {
        "right": right_song,
        "left": left_song,
    }



if __name__ == "__main__":
    midi_path = r"E:\ELEC 391\code\test1\test1\Python\Song\Song_list\twinkle-twinkle-little-star.mid"

    score = load_score_from_midi(
        midi_path,
        min_duration=0.08,
        split_note="C4",
        verbose=True,
    )

    print("\nRIGHT:")
    for notes, dur in score["right"]:
        print((notes, round(dur, 3)))

    print("\nLEFT:")
    for notes, dur in score["left"]:
        print((notes, round(dur, 3)))