# MIDI to Left/Right Hand Score

## Overview

This document explains how the system converts a MIDI file into left-hand and right-hand score sequences.

A MIDI file is not directly usable by the robot. MIDI data contains tracks, note-on events, note-off events, timing ticks, tempo changes, channels, and instrument information. The robot needs a much simpler representation:

```python
{
    "right": [
        (["C5", "E5"], 0.50),
        (["G5"], 0.25),
    ],
    "left": [
        (["C3"], 0.50),
        ([], 0.25),
    ]
}
```

Each score event contains:

- a list of notes
- a duration in seconds

An empty note list `[]` represents a rest.

The purpose of this stage is to transform MIDI data into clean left-hand and right-hand musical sequences before any robotic motion planning happens.

---

## Input

The input is a `.mid` or `.midi` file.

Example:

```text
Song/Song_list/twinkle-twinkle-little-star.mid
```

The MIDI file may contain:

- one track
- multiple tracks
- piano tracks
- non-piano tracks
- drum tracks
- tempo changes
- simultaneous notes
- note durations in MIDI ticks instead of seconds

The loader must analyze this information and produce a robot-friendly score.

---

## Output Format

The output format is a dictionary with two keys:

```python
{
    "right": right_hand_score,
    "left": left_hand_score,
}
```

Each hand score is a list of events:

```python
[
    (["C4"], 0.50),
    (["E4", "G4"], 0.75),
    ([], 0.25),
]
```

Each event has:

| Field | Meaning |
|---|---|
| `notes` | List of note names such as `C4`, `F#5`, or `A3` |
| `duration` | Event duration in seconds |
| `[]` | Rest event |

This format becomes the input to the playable-score adaptation and hand planning stages.

---

## MIDI Track Analysis

The MIDI loader first analyzes tracks to decide which tracks are useful.

For each track, the loader checks information such as:

- number of notes
- average pitch
- pitch range
- whether the track is likely a drum track
- whether the track explicitly uses a piano program
- whether the track looks piano-like even without explicit piano metadata

This helps the system avoid choosing drum tracks or irrelevant tracks.

---

## Track Selection Strategy

The loader follows this general strategy:

1. Prefer explicit piano tracks.
2. Otherwise, prefer piano-like tracks.
3. Otherwise, fall back to non-drum tracks.
4. If two useful tracks are available, assign them to right and left hands.
5. If only one useful track is available, split it by pitch.

---

## Dual-Track Mode

If the MIDI file contains two useful tracks, the loader assigns one track to the right hand and one track to the left hand.

The track with higher average pitch is usually treated as the right-hand track.

The track with lower average pitch is usually treated as the left-hand track.

Example:

```text
Track A average pitch: high  → right hand
Track B average pitch: low   → left hand
```

The result is:

```python
{
    "right": right_track_song,
    "left": left_track_song,
}
```

This works well for MIDI files that already separate melody and bass/accompaniment into different tracks.

---

## Single-Track Mode

Some MIDI files only have one useful musical track.

In that case, the system splits the notes by a pitch threshold.

The default split point is:

```text
C4
```

The rule is:

```text
notes below C4       → left hand
notes at/above C4    → right hand
```

Example:

```python
["C3", "G3", "E5"]
```

becomes:

```python
left  = ["C3", "G3"]
right = ["E5"]
```

This is a practical fallback when the MIDI file does not already provide separate left-hand and right-hand tracks.

---

## Tempo Handling

MIDI timing is stored in ticks, not seconds.

The loader converts ticks into seconds using tempo information from the MIDI file.

This is important because the robot planner and conductor need real-time durations.

Example conversion target:

```python
(start_tick, end_tick, note_name)
```

becomes:

```python
(["C4"], 0.50)
```

If the MIDI file contains tempo changes, the loader builds tempo segments and converts note timing based on the correct tempo region.

---

## Grouping Simultaneous Notes

If multiple notes start at the same MIDI tick, they are grouped into a chord.

Example MIDI events:

```text
C4 starts at tick 480
E4 starts at tick 480
G4 starts at tick 480
```

become:

```python
(["C4", "E4", "G4"], duration)
```

The chord duration is usually based on the longest note in that same-start group.

This grouping is important because the hand planner needs to know which notes should be pressed together.

---

## Rest Generation

If there is a gap between two note groups, the loader inserts a rest.

Example:

```text
Note group 1 ends at 1.00s
Note group 2 starts at 1.25s
```

The loader inserts:

```python
([], 0.25)
```

This rest is useful for planning because the robot can use rest time to reposition the hand before the next note.

---

## Duration Cleanup

The loader applies basic cleanup to make the output more stable.

Typical cleanup includes:

- minimum duration filtering
- rest merging
- optional maximum song length
- conversion from raw MIDI ticks to seconds

For the UI demo, the song length may be limited so very long MIDI files do not overload the planning or visualization system.

---

## Example Output

Example MIDI-to-score output:

```python
{
    "right": [
        (["C5"], 0.50),
        (["D5"], 0.50),
        (["E5"], 0.50),
        ([], 0.25),
        (["G5", "B5"], 0.75),
    ],
    "left": [
        (["C3"], 1.00),
        ([], 0.25),
        (["G2", "D3"], 0.75),
    ]
}
```

This output is not yet guaranteed to be playable by the robot.

It still needs to pass through playable-score adaptation and dynamic programming hand planning.

---

## Why This Stage Matters

The robot cannot directly understand MIDI.

MIDI data is designed for digital music playback, while the robot needs:

- note names
- durations in seconds
- left-hand and right-hand separation
- chord grouping
- rest events
- clean timing

This stage converts symbolic music data into a structured score format that the robotic planning system can process.

---

## Summary

The MIDI-to-score stage performs this transformation:

```text
MIDI file
    ↓
track analysis
    ↓
dual-track selection or single-track pitch split
    ↓
note event extraction
    ↓
tick-to-second conversion
    ↓
chord grouping and rest generation
    ↓
left-hand and right-hand score
```

The result is a clean musical representation prepared for robot-specific adaptation and planning.