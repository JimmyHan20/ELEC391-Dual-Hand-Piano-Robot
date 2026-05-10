# Dual-Path Planning and Cross-Hand Constraints

## Overview

This document explains the dual-path planning layer and the cross-hand constraint handling used in the robotic piano system.

After the standard dynamic programming planner creates possible hand-motion paths, the system further improves planning by supporting two different hand geometry modes:

1. **Path A: White-key-centered pose**
2. **Path B: Half-center pose between white keys**

This allows the robot to play note combinations that may be difficult or impossible with only a standard white-key center model.

In addition, because the system has two robotic hands, the planner must consider possible physical conflicts between the left hand and the right hand. The cross-hand constraint logic checks whether one hand's plan may interfere with the other and re-plans affected sections when needed.

---

## Why Dual-Path Planning Is Needed

A simple hand planner assumes that the hand center is always aligned with a white key.

This works for many notes and chords, but not all of them.

Some intervals are difficult because:

- the notes are too far apart for a standard spread
- the best mechanical center is between two white keys
- the hand needs a special spread profile
- the left and right fingers need to land on two distant white keys

For these cases, Path B provides extra flexibility.

The purpose is:

```text
Use the normal stable hand model when possible.
Use special half-center profiles only when they help.
```

---

## Path A: White-Key Center

Path A is the standard planning mode.

In Path A, the hand center is placed directly on a white key.

Example:

```text
center_note = G4
center_mode = white_key
spread = 2
```

The planner uses the regular hand pose model:

```python
Pose(center_idx, spread)
```

where:

| Field | Meaning |
|---|---|
| `center_idx` | White-key index of the hand center |
| `spread` | Internal spread level |

Path A is stable and preferred when it can play the event.

---

## Path A Spread Mapping

The internal Path A spread index is mapped to real hardware spread command levels.

Conceptually:

```text
internal spread 0 → hardware spread level 0
internal spread 1 → hardware spread level 2
internal spread 2 → hardware spread level 4
```

The hardware spread levels correspond to real spread motor target angles:

```text
SF=0 → 0°
SF=1 → 40°
SF=2 → 80°
SF=3 → 120°
SF=4 → 160°
```

This mapping is important because the software planner uses abstract hand geometry, while the STM32 firmware needs real hardware command levels.

---

## Path B: Half-Center Profile

Path B is the special planning mode.

In Path B, the hand center can be placed between two white keys instead of exactly on a white key.

This is represented using a doubled white-key slot grid:

```text
white key index i      → center_slot = 2*i
gap between i and i+1  → center_slot = 2*i + 1
```

Example:

```text
center_slot = 9
```

means the center is between two neighboring white keys.

Path B is useful when the robot needs to play a wider interval using the two outer white-key fingers.

---

## Half-Center Profiles

The system defines special half-center profiles.

Examples:

```text
HC_6TH
HC_8VE
```

A half-center profile describes:

| Field | Meaning |
|---|---|
| `name` | Profile name, such as `HC_6TH` |
| `white_span_steps` | Distance between the two white notes |
| `spread_level` | Hardware spread level to send |
| `small_motor_angle` | Expected spread motor angle |
| `priority` | Tie-break priority |

Example concept:

```text
HC_6TH:
    white_span_steps = 5
    spread_level = 1
    small_motor_angle = 40°

HC_8VE:
    white_span_steps = 7
    spread_level = 3
    small_motor_angle = 120°
```

These profiles allow the robot to handle intervals such as sixths or octaves more reliably.

---

## Path A vs Path B

| Feature | Path A | Path B |
|---|---|---|
| Center location | On a white key | Between two white keys |
| Main use | Normal notes and chords | Difficult wide intervals |
| Stability | More stable | Special-case geometry |
| Preferred by default | Yes | No, only when useful |
| Example source path | `A` | `B` |

The planner gives Path A a small preference because it is mechanically simpler.

Path B is selected only when it produces a better or more playable solution.

---

## Dual-Path Candidate Output

A planned event can include both legacy fields and dual-path fields.

Example:

```python
{
    "notes": ["C4", "A4"],
    "duration": 1.0,
    "center_mode": "between_white",
    "center_slot": 5,
    "center_note": None,
    "spread": 1,
    "spread_profile": "HC_6TH",
    "center_angle_override": 950.0,
    "spread_angle_override": 40.0,
    "finger_ids": ["Lw", "Rw"],
    "note_finger_map": {
        "C4": "Lw",
        "A4": "Rw"
    },
    "source_path": "B",
    "action_type": "move_hand_and_chord"
}
```

Important fields:

| Field | Meaning |
|---|---|
| `center_mode` | `white_key` or `between_white` |
| `center_slot` | Doubled-grid center position |
| `center_note` | White-key center note for Path A |
| `spread_profile` | Special Path B profile name |
| `center_angle_override` | Direct motor angle for non-standard center |
| `spread_angle_override` | Direct spread angle for special profile |
| `source_path` | `A` or `B` |

---

## Dual-Path Cost Model

The dual-path planner compares candidate paths using a cost function.

Main cost terms:

```text
cost_hand_move_per_white = 10.0
cost_small_motor_angle_per_deg = 0.03
cost_profile_switch = 0.5
cost_finger_change = 0.6
rest_move_discount = 0.50
```

Path preference terms:

```text
path_a_bonus = 0.20
path_b_penalty = 0.20
```

This means:

- Path A gets a small bonus.
- Path B gets a small penalty.
- Path B is still selected if it avoids large movement or makes the chord playable.

---

## Dual-Path Transition Cost

A simplified dual-path transition cost is:

```text
transition_cost =
    hand movement cost
  + spread/profile switch cost
  + finger change cost
  + path preference term
```

If the current event is a rest, movement is discounted:

```text
movement_cost = movement_cost × rest_move_discount
```

This encourages the robot to move during silent time.

---

## Why Path B Is Penalized

Path B is useful, but it is more special than Path A.

The system does not want to overuse Path B because:

- half-center positions may be less mechanically stable
- special spread profiles may require more careful calibration
- Path A is easier to interpret and debug
- Path A is usually safer when both paths work

Therefore, Path B has a small penalty.

However, if Path B makes a difficult interval playable or reduces a large hand movement, the planner can still choose it.

---

## Cross-Hand Constraint Problem

The two robotic hands are not fully independent.

Even if the right hand and left hand each have valid plans separately, the combined plan may cause physical conflicts.

Example problem:

```text
Right hand moves low near C4.
Left hand moves high near A3 or B3.
```

These two regions may be too close mechanically, depending on the hand shapes and spread levels.

So the system needs cross-hand constraint handling.

---

## Cross-Hand Constraint Strategy

The system follows this general process:

```text
Initial right-hand dual-path plan
Initial left-hand dual-path plan
        ↓
Build timelines for both hands
        ↓
Detect dangerous overlapping windows
        ↓
Collect forbidden step indices
        ↓
Re-plan one hand with forbidden candidates removed
        ↓
Re-plan the other hand based on the updated plan
        ↓
Repeat until stable or max iteration reached
```

This is an iterative re-planning process.

The goal is to reduce dangerous cross-hand combinations without redesigning the entire planner.

---

## Forbidden Step Indices

When the planner detects that a hand overlaps in time with a dangerous configuration from the other hand, it marks that step index as forbidden.

Example:

```text
left_forbidden_steps = {3, 4}
right_forbidden_steps = {2}
```

Then during re-planning, the planner blocks dangerous candidates only at those step indices.

This is more flexible than banning an entire hand pose globally.

---

## Re-Planning Loop

The cross-hand re-planning loop works like this:

```text
1. Plan right hand without cross-hand constraints.
2. Plan left hand without cross-hand constraints.
3. Detect left-hand steps that conflict with dangerous right-hand events.
4. Re-plan left hand with those dangerous candidates forbidden.
5. Detect right-hand steps that conflict with dangerous left-hand events.
6. Re-plan right hand with those dangerous candidates forbidden.
7. Compare the new plans with the previous plans.
8. Stop if the result converges.
9. Stop if a repeated pattern is detected.
10. Stop after the maximum number of iterations.
```

This helps prevent infinite re-planning loops.

---

## Plan Signature

To detect convergence or oscillation, the system can compare plan signatures.

A plan signature summarizes the important fields of each event:

- notes
- center mode
- center slot
- center note
- spread
- spread profile
- angle overrides
- finger IDs
- action type
- source path

If the signature no longer changes, the planner has converged.

If a previous signature appears again, the planner may be oscillating, so it stops.

---

## Safe Rest Fallback

If a conflict cannot be solved cleanly, the system can force one event into a safe rest-hold state.

This means:

```python
{
    "notes": [],
    "finger_ids": [],
    "note_finger_map": {},
    "action_type": "rest_hold",
    "cross_hand_limit_blocked": True
}
```

This is a safety-oriented fallback.

It is better to skip one risky note than to allow a possible mechanical collision.

---

## Example Cross-Hand Situation

Suppose the right hand is holding a low position near C4:

```text
Right hand:
    notes = ["C4"]
    center_note = "F4"
    spread = 2
```

At the same time, the left hand tries to open high near A3 or B3:

```text
Left hand:
    notes = ["B3"]
    center_note = "E3"
    spread = 4
```

If these two motions overlap in time, the system may decide that this is risky.

It can then:

- re-plan the left hand with a safer candidate
- re-plan the right hand with a safer candidate
- or force one side into `rest_hold` if needed

---

## Output

The final output remains the same structure as the planned hand events:

```python
{
    "right": planned_right,
    "left": planned_left
}
```

Each event may contain:

```text
source_path = "A"
```

or:

```text
source_path = "B"
```

This makes it possible to debug whether a note was played using standard white-key center planning or special half-center planning.

---

## Relationship to the Next Stage

After dual-path and cross-hand planning, the system has two final planned event lists:

```text
planned_right
planned_left
```

These are then passed into the global timeline builder.

```text
planned right/left events
        ↓
global timeline builder
        ↓
PRESS / RELEASE / HOLD / shared press slots
```

The dual-path planner decides how each hand should move.

The global timeline decides when each planned action happens.

---

## Summary

Dual-path planning improves the robot's reach and flexibility.

Cross-hand constraint handling improves safety when both hands operate together.

The main idea is:

```text
Use Path A when normal white-key center planning works.
Use Path B when special half-center geometry is needed.
Check both hands together to reduce physical conflicts.
Force safe rest-hold if a risky conflict cannot be resolved.
```

This makes the system more realistic as a dual-hand robotic piano player.