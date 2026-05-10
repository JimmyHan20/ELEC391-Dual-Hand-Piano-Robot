# Playable Score Adaptation

## Overview

This document explains how the system adapts a musical score so that it becomes playable by the dual-hand piano robot.

A MIDI file may contain notes and chords that are valid for a human pianist but impossible for the robot. The robot has limited hand range, limited spread levels, fixed finger positions, motor movement limits, and possible cross-hand interference.

Therefore, before the dynamic programming planner generates final hand motions, the raw score must be cleaned and adapted.

The goal is:

```text
Keep the music meaningful while making it physically executable.
```

---

## Why Adaptation Is Needed

A real piano score may include:

- notes outside the robot's playable range
- chords that are too wide for one robotic hand
- too many notes in one chord
- difficult black-key and white-key combinations
- very short durations
- notes that require impossible hand jumps
- left-hand and right-hand overlap risks
- MIDI artifacts or messy note groupings

The robot cannot simply execute every note from the original MIDI file.

Playable-score adaptation acts as a bridge between ideal music and physical hardware.

---

## Input

The input is a left/right score dictionary:

```python
{
    "right": [
        (["C5", "E5", "G5"], 0.50),
        (["A5"], 0.25),
    ],
    "left": [
        (["C3", "G3"], 0.50),
        ([], 0.25),
    ]
}
```

At this stage, the score has already been extracted from MIDI or built-in song data.

However, it may not be physically playable yet.

---

## Output

The output is a cleaned score with the same structure:

```python
{
    "right": [
        (["C5", "E5"], 0.50),
        (["A5"], 0.25),
    ],
    "left": [
        (["C3"], 0.50),
        ([], 0.25),
    ]
}
```

The cleaned score is then passed into the dynamic programming planner.

---

## Robot Range Limits

Each robotic hand has a limited playable range.

In the UI configuration, the right and left planners use different ranges.

Example concept:

```text
Right hand: higher keyboard region
Left hand: lower keyboard region
```

The planner configuration defines:

- note range
- center-note range
- spread distances
- black-finger availability
- start position
- cost weights

If a note cannot be supported by a hand, the system must either repair it, simplify it, or remove it.

---

## Octave Repair

Some notes may be outside the robot's playable range but still musically usable if shifted by octaves.

Example:

```text
Original note: C7
Playable shifted note: C6
```

or:

```text
Original note: A1
Playable shifted note: A2
```

This preserves the pitch class while moving the note into the robot's mechanical range.

Octave repair is useful because the robot's physical range is smaller than a full piano keyboard.

---

## Chord Reduction

A human pianist can often play more complex chords than this robot.

For example, a MIDI chord may contain:

```python
["C4", "E4", "G4", "B4"]
```

But the robot hand may only be able to play two or three of those notes in a valid pose.

In that case, the system chooses a playable subset.

Example:

```python
["C4", "E4", "G4", "B4"]
```

may become:

```python
["C4", "E4", "B4"]
```

or:

```python
["C4", "G4"]
```

depending on what is mechanically reachable.

---

## Music-Aware Note Selection

When a chord must be reduced, the system should not randomly delete notes.

The goal is to preserve the most musically important notes.

Important notes may include:

- melody notes
- bass anchor notes
- root notes
- thirds
- sevenths
- notes that strongly define the harmony
- structurally important chord tones

This makes the simplified version sound closer to the original music.

The output may not contain every original note, but it should still preserve the musical identity of the piece.

---

## Duration Cleanup

The system also cleans timing information.

Very short durations may be difficult for real hardware to execute because motors and solenoids need physical time to move and settle.

The adaptation stage may:

- clamp very short durations
- merge consecutive rests
- remove invalid events
- keep the score format consistent

This helps prevent unrealistic execution timing.

---

## Rest Handling

Rest events are represented as:

```python
([], duration)
```

Rests are important because they give the hand planner opportunities to reposition.

For example:

```python
(["C4"], 0.5),
([], 0.3),
(["G4"], 0.5)
```

The 0.3-second rest may allow the robot to move from the C4 region to the G4 region before the next note.

Therefore, rests are not useless. They are useful planning windows.

---

## Hardware-Specific Edge Cases

Some notes near the edge of the robot's range may require special handling.

For example, a note might technically be within the musical range but difficult to reach with the standard hand center and spread configuration.

The code includes hardware-specific overrides for certain edge notes.

These overrides can force:

- a safer center note
- a specific spread level
- a specific finger assignment

This is useful when the ideal mathematical model does not perfectly match the real mechanical behavior of the robot.

---

## Relationship to Dynamic Programming

Playable-score adaptation does not fully decide the robot motion.

Instead, it prepares a better input for the dynamic programming planner.

The relationship is:

```text
Raw score
    ↓
Playable-score adaptation
    ↓
Cleaned score
    ↓
Dynamic programming planner
    ↓
Planned hand events
```

The adaptation stage handles coarse filtering and musical simplification.

The dynamic programming planner handles detailed pose selection and finger assignment.

---

## Example

Raw score:

```python
{
    "right": [
        (["C5", "E5", "G5", "B5"], 0.50),
        (["C7"], 0.25),
    ],
    "left": [
        (["A1", "E2"], 0.50),
    ]
}
```

Possible cleaned score:

```python
{
    "right": [
        (["C5", "E5", "B5"], 0.50),
        (["C6"], 0.25),
    ],
    "left": [
        (["A2", "E2"], 0.50),
    ]
}
```

The exact output depends on robot range, hand geometry, and playable subset selection.

---

## Design Goal

The adaptation stage follows a practical robotics principle:

```text
A simplified playable version is better than an exact version that the robot cannot execute.
```

This is especially important for a physical robot because impossible commands can cause:

- missed notes
- timing delays
- unstable movement
- mechanical collisions
- failed demonstrations

Playable adaptation makes the final playback more reliable.

---

## Summary

Playable-score adaptation converts the raw score into a mechanically realistic score.

It handles:

- range limits
- octave repair
- unsupported notes
- chord simplification
- music-aware note preservation
- duration cleanup
- rest handling
- hardware-specific edge cases

The result is a cleaned score that is ready for dynamic programming hand planning.