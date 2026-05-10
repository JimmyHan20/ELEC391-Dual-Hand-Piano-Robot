# Global Timeline Builder

## Overview

This document explains the global timeline builder used in the robotic piano system.

After the left and right hands have been planned, each hand has its own sequence of planned segments.

However, the UI and conductor need a clearer time-based representation:

- when a note starts
- when a note ends
- which hand changes at each time
- whether both hands press at the same time
- whether one hand is holding while the other hand changes

The global timeline builder converts planned hand segments into explicit time events.

---

## Position in the Pipeline

The global timeline builder sits after planning and before execution.

```text
Planned Left Events + Planned Right Events
        ↓
Global Timeline Builder
        ↓
PRESS / RELEASE / HOLD / global time slots
        ↓
Dual-Hand Conductor / UI Debugging
```

The planner decides what each hand should do.

The global timeline builder organizes those actions by time.

---

## Input

The input is two planned event lists:

```python
planned_left = [
    {
        "notes": ["C3"],
        "duration": 0.5,
        "center_note": "C3",
        "spread": 0,
        "finger_ids": ["M"],
        "action_type": "move_hand"
    }
]

planned_right = [
    {
        "notes": ["E5"],
        "duration": 0.5,
        "center_note": "E5",
        "spread": 0,
        "finger_ids": ["M"],
        "action_type": "move_hand"
    }
]
```

Each planned segment represents one time interval for one hand.

---

## Output

The output is a dictionary containing:

```python
{
    "left_events": [...],
    "right_events": [...],
    "left_holds": [...],
    "right_holds": [...],
    "global_slots": [...]
}
```

Meaning:

| Output Field | Meaning |
|---|---|
| `left_events` | Explicit left-hand PRESS and RELEASE events |
| `right_events` | Explicit right-hand PRESS and RELEASE events |
| `left_holds` | Left-hand hold windows |
| `right_holds` | Right-hand hold windows |
| `global_slots` | Merged event times from both hands |

This structure is useful for UI tables, debugging, and synchronization analysis.

---

## PRESS and RELEASE Events

A planned segment with notes is converted into two explicit events:

1. `PRESS` at the segment start time
2. `RELEASE` at the segment end time

Example input segment:

```python
{
    "notes": ["C5"],
    "duration": 0.5,
    "center_note": "G5",
    "spread": 2,
    "finger_ids": ["Lw"]
}
```

becomes:

```text
Time 0.00s: PRESS C5
Time 0.50s: RELEASE C5
```

This makes the note lifecycle explicit.

---

## HOLD Windows

The system does not create a separate `HOLD` event for every moment.

Instead, hold is stored as an implicit window.

Example:

```text
PRESS at 0.00s
RELEASE at 0.50s
```

means:

```text
HOLD from 0.00s to 0.50s
```

The hold window records:

- hand
- start time
- end time
- segment index
- notes
- action type
- target position

This is useful for checking whether one hand is holding while the other hand changes.

---

## Rest Segments

If a planned segment has no notes:

```python
{
    "notes": [],
    "duration": 0.25,
    "action_type": "rest_hold"
}
```

then it does not generate a PRESS or RELEASE event.

However, it still contributes to the hand's internal current time.

This means rest duration affects the timing of later notes.

---

## Global Time Slots

After building left-hand and right-hand events, the system merges all event times into global slots.

Example:

```text
Left events:
    0.00s PRESS
    0.50s RELEASE

Right events:
    0.00s PRESS
    0.25s RELEASE
```

Global slots:

```text
0.00s
0.25s
0.50s
```

Each global slot contains:

- left-hand events at that time
- right-hand events at that time
- whether both hands press together
- which hands changed at that time

---

## Global Slot Structure

A global slot contains:

```python
{
    "global_time": 0.0,
    "left_events": [...],
    "right_events": [...],
    "shared_press": True,
    "changed_hands": ["left", "right"]
}
```

Important fields:

| Field | Meaning |
|---|---|
| `global_time` | Event time in seconds |
| `left_events` | Left-hand events at this time |
| `right_events` | Right-hand events at this time |
| `shared_press` | Whether both hands press at this time |
| `changed_hands` | Which hands have events at this time |

---

## Shared Press Detection

If both hands have a `PRESS` event at the same global time, the slot is marked as a shared press.

Example:

```text
Time 1.20s:
    Left hand: PRESS C3
    Right hand: PRESS E5
```

Then:

```python
shared_press = True
```

The system also assigns a shared pair ID, such as:

```text
sync_press_1
```

This makes it easier to identify events that should happen together.

---

## Synchronization Fields

When a shared press is detected, both press events can be marked with:

```python
sync_required = True
pair_event_id = "sync_press_1"
```

This is useful for debugging and visualization.

It shows that the two hand events are musically linked and should be treated as synchronized actions.

---

## Partner Change Detection

The global timeline also checks whether one hand changes while the other hand is holding.

Example:

```text
Left hand holds C3 from 0.00s to 1.00s.
Right hand presses E5 at 0.50s.
```

In this case, the left hand's hold window has a partner change inside it.

This is useful because the robot may need to know that one hand remains physically active while the other hand moves or presses.

---

## Why Partner Change Matters

Partner change information helps with debugging dual-hand behavior.

It can answer questions such as:

- Was the left hand holding while the right hand moved?
- Did one hand change during another hand's sustained note?
- Was a collision risk possible during a hold?
- Did the UI correctly display active hands?

This is especially useful for a real physical robot where both hands share space.

---

## Event Sorting

Events are sorted by:

```text
global time
event type order
segment index
```

Release events can be ordered before press events at the same time.

This helps avoid cases where the same hand starts a new note before releasing the previous one.

---

## Example

Input:

```python
planned_left = [
    {
        "notes": ["C3"],
        "duration": 1.0,
        "center_note": "C3",
        "spread": 0,
        "finger_ids": ["M"],
        "action_type": "move_hand"
    }
]

planned_right = [
    {
        "notes": ["E5"],
        "duration": 0.5,
        "center_note": "E5",
        "spread": 0,
        "finger_ids": ["M"],
        "action_type": "move_hand"
    },
    {
        "notes": ["G5"],
        "duration": 0.5,
        "center_note": "G5",
        "spread": 0,
        "finger_ids": ["M"],
        "action_type": "move_hand"
    }
]
```

Output concept:

```text
Global Slot 0.00s:
    Left: PRESS C3
    Right: PRESS E5
    shared_press = True
    changed_hands = ["left", "right"]

Global Slot 0.50s:
    Right: RELEASE E5
    Right: PRESS G5
    shared_press = False
    changed_hands = ["right"]

Global Slot 1.00s:
    Left: RELEASE C3
    Right: RELEASE G5
    shared_press = False
    changed_hands = ["left", "right"]
```

The left hold window:

```text
Left holds C3 from 0.00s to 1.00s
partner_changes_inside = True
```

because the right hand changes at 0.50s.

---

## Use in the UI

The global timeline is useful for UI debugging.

It can support:

- planner tables
- left/right event visualization
- shared press highlighting
- hold window analysis
- active hand display
- debugging of press/release timing

This helps demonstrate that the robot has a real planning pipeline, not just simple serial commands.

---

## Use in Execution

The conductor can use planned events directly, but the global timeline is still valuable because it explains the timing relationship between both hands.

It makes the planned score easier to inspect.

For example, if a note sounds late, the timeline helps identify whether:

- the planned event time was late
- the hand had to move too far
- one hand was holding while the other changed
- a shared press was expected but not achieved

---

## Relationship to Previous and Next Stages

Previous stage:

```text
Dynamic programming + dual-path planner
```

produces:

```text
planned_left
planned_right
```

Global timeline builder converts them into:

```text
PRESS / RELEASE / HOLD / global_slots
```

Next stage:

```text
Dual-hand conductor
```

uses planned timing and hand events to execute physical playback.

---

## Summary

The global timeline builder turns hand-level planned segments into system-level timing events.

It creates:

- explicit `PRESS` events
- explicit `RELEASE` events
- implicit `HOLD` windows
- merged `global_slots`
- shared press flags
- partner-change information

This makes dual-hand robotic piano playback easier to debug, visualize, and synchronize.