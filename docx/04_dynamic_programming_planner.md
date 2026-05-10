# Dynamic Programming Hand Planner

## Overview

This document explains the dynamic programming hand planner used in the dual-hand robotic piano system.

After the MIDI file has been converted into left-hand and right-hand score sequences, and after the score has been adapted to the robot's playable range, each robotic hand still needs a detailed motion plan.

For each note or chord, the robot must decide:

- where the hand center should move
- what spread level should be used
- which finger or solenoid should press each note
- whether the hand should move, hold, or reposition
- how to reduce unnecessary movement across the whole song

A simple greedy planner would only choose the best hand pose for the current note. That may work for one note, but it can create bad movement for the next notes.

The dynamic programming planner solves this by planning across the full hand sequence and choosing the lowest-cost motion path.

---

## Where This Planner Fits in the System

The planner is used after MIDI processing and playable-score adaptation.

```text
MIDI / Song Input
        ↓
Left-Hand Score + Right-Hand Score
        ↓
Playable Score Adaptation
        ↓
Dynamic Programming Hand Planner
        ↓
Planned Hand Events
        ↓
Global Timeline Builder
        ↓
Dual-Hand Conductor
        ↓
STM32 Hardware Execution
```

The DP planner is responsible for converting a cleaned musical score into physically executable robotic hand events.

---

## Important Distinction: Quality Score vs DP Cost

There are two different scoring systems in this project:

1. **Playable transposition quality score**
2. **Dynamic programming motion cost**

They are not the same thing.

---

## 1. Playable Transposition Quality Score

The playable transposition quality score is used before final hand motion planning.

It answers this question:

```text
If the song is transposed to this target key, is it generally easier for the robot to play?
```

The formula is:

```text
quality_score = 0.65 × note_support_ratio + 0.35 × event_playable_ratio
```

where:

| Term | Meaning |
|---|---|
| `note_support_ratio` | Ratio of individual notes that are supported by the robot |
| `event_playable_ratio` | Ratio of note events or chords that can be fully played |
| `0.65` | Weight for individual note support |
| `0.35` | Weight for full event/chord playability |

This score is mainly used for evaluating candidate transpositions.

For example, if a song is originally in a difficult key, the UI can suggest a more playable target key. The score helps decide whether the transposed version is suitable for the robot.

Example:

```text
note_support_ratio = 0.90
event_playable_ratio = 0.70

quality_score = 0.65 × 0.90 + 0.35 × 0.70
              = 0.585 + 0.245
              = 0.830
```

A higher `quality_score` means the transposed score is more suitable for the robot.

This is not the dynamic programming cost.

It does not choose the exact hand center, spread, or finger assignment for every event.

It only evaluates whether a score version is generally playable.

---

## 2. Dynamic Programming Motion Cost

The dynamic programming motion cost is used after the score has already been selected and cleaned.

It answers this question:

```text
Given this score, what sequence of hand poses produces the lowest total robot movement cost?
```

The DP planner considers the full sequence of notes and chooses the best path of hand states.

A simplified transition cost is:

```text
transition_cost =
    hand movement cost
  + spread change cost
  + finger change cost
```

The accumulated DP cost is:

```text
total_cost[i, current_state] =
    best previous total cost
  + transition cost from previous_state to current_state
  + static pose cost
```

The planner selects the path with the lowest total accumulated cost.

---

## Input

The input is one hand's cleaned score.

Example:

```python
[
    (["C5"], 0.50),
    (["E5", "G5"], 0.50),
    ([], 0.25),
    (["A5"], 0.50),
]
```

Each event contains:

| Item | Meaning |
|---|---|
| `notes` | Notes to play |
| `duration` | Duration in seconds |
| `[]` | Rest event |

The same planning logic can be applied to either the right hand or the left hand.

---

## Output

The output is a list of planned hand events.

Example:

```python
[
    {
        "notes": ["C5", "E5"],
        "duration": 0.50,
        "center_note": "G5",
        "spread": 2,
        "finger_ids": ["Lw", "M"],
        "action_type": "move_hand",
        "note_finger_map": {
            "C5": "Lw",
            "E5": "M"
        }
    }
]
```

Each planned event contains:

| Field | Meaning |
|---|---|
| `notes` | Notes to play |
| `duration` | Event duration |
| `center_note` | Target center note for the hand |
| `spread` | Spread level sent to the spread mechanism |
| `finger_ids` | Fingers used for pressing |
| `action_type` | Type of motion/action |
| `note_finger_map` | Mapping from notes to fingers |

This output can be used by the global timeline builder and dual-hand conductor.

---

## Hand Pose Model

A basic hand pose is represented as:

```python
Pose(center_idx, spread)
```

where:

| Term | Meaning |
|---|---|
| `center_idx` | Center position on the white-key index |
| `spread` | Spread level of the hand |

Conceptually:

```text
center_idx → where the middle/center position of the hand is
spread     → how far the outer fingers can reach from the center
```

For the standard planner, the spread model is:

```text
spread = 0 → outer white fingers at ±2 white keys
spread = 1 → outer white fingers at ±3 white keys
spread = 2 → outer white fingers at ±4 white keys
```

In the hardware output, these internal spread levels can be mapped to real STM32 spread command levels.

---

## Finger Model

The planner uses five finger IDs:

```text
Lw, Lb, M, Rb, Rw
```

Their meaning is:

| Finger ID | Meaning |
|---|---|
| `Lw` | Left white-key finger |
| `Lb` | Left black-key finger |
| `M` | Middle / center finger |
| `Rb` | Right black-key finger |
| `Rw` | Right white-key finger |

This model matches the mechanical idea that the robot has separate reachable positions for white keys and black keys.

For each pose:

```text
M  → center white key
Lw → left white-key position
Rw → right white-key position
Lb → black-key slot associated with the left white-key position
Rb → black-key slot associated with the right white-key position
```

---

## Candidate Generation

For each score event, the planner generates all possible candidates.

A candidate describes one possible way to play the current note or chord.

For a single note, a candidate includes:

- one valid pose
- one finger assignment

For a chord, a candidate includes:

- one valid pose
- multiple finger assignments
- a note-to-finger map

Example candidate:

```python
{
    "pose": Pose(center_idx=5, spread=1),
    "mapping": {
        "C5": "Lw",
        "E5": "M"
    },
    "finger_ids": ["Lw", "M"],
    "notes": ["C5", "E5"],
    "duration": 0.50,
    "is_rest": False
}
```

If the full chord cannot be played, the planner may use a playable subset.

If no note can be played, the event can fall back to a rest so the planner can continue.

---

## Rest Candidates

A rest event is represented as:

```python
([], duration)
```

Rests are important because the robot can move during silent time.

For a rest, the planner can generate candidate poses without pressing any finger.

This allows the robot to reposition before future notes.

Example:

```text
C5 note → rest → A5 note
```

During the rest, the hand can move closer to A5.

This is why rest handling is important in the DP planner.

---

## Dynamic Programming State

A DP state represents the current hand configuration.

A standard state includes:

```text
center position
spread level
current finger set
```

A state key may look like:

```python
(center_idx, spread, ("Lw", "M"))
```

The planner evaluates transitions between states across the full score.

The goal is to find the sequence of states with the lowest accumulated cost.

---

## Standard DP Cost Terms

The standard hand planner uses these main cost weights:

```text
cost_hand_move_per_key = 10.0
cost_spread_change_per_level = 2.0
cost_finger_change = 0.6
rest_move_discount = 0.50
```

These values express the relative difficulty of different robot actions.

---

## Hand Movement Cost

Hand movement cost penalizes moving the hand center.

Conceptually:

```text
hand_move_cost =
    abs(current_center_idx - previous_center_idx)
    × cost_hand_move_per_key
```

With:

```text
cost_hand_move_per_key = 10.0
```

Moving the entire hand is expensive because it takes time and is more likely to affect timing accuracy.

Example:

```text
previous center_idx = 4
current center_idx  = 7

hand movement = abs(7 - 4) = 3 white-key steps
hand_move_cost = 3 × 10.0 = 30.0
```

This is a large cost, so the planner avoids unnecessary large hand jumps.

---

## Spread Change Cost

Spread change cost penalizes changing the spread mechanism.

Conceptually:

```text
spread_change_cost =
    abs(current_spread - previous_spread)
    × cost_spread_change_per_level
```

With:

```text
cost_spread_change_per_level = 2.0
```

Example:

```text
previous spread = 0
current spread  = 2

spread change = abs(2 - 0) = 2 levels
spread_change_cost = 2 × 2.0 = 4.0
```

Changing spread is cheaper than moving the whole hand, but still not free.

---

## Finger Change Cost

Finger change cost penalizes switching the active finger set.

Conceptually:

```text
finger_change_cost =
    finger_set_difference × cost_finger_change
```

With:

```text
cost_finger_change = 0.6
```

Example:

```text
previous fingers = ["Lw"]
current fingers  = ["Lw", "M"]

finger set difference = 1
finger_change_cost = 1 × 0.6 = 0.6
```

Finger changes are relatively cheap compared with moving the whole hand.

This makes sense because activating a different solenoid is easier than moving the center mechanism.

---

## Rest Move Discount

When the current event is a rest, movement is discounted.

With:

```text
rest_move_discount = 0.50
```

Conceptually:

```text
if current_event_is_rest:
    movement_cost = movement_cost × rest_move_discount
```

Example:

```text
normal hand movement cost = 20.0
during rest = 20.0 × 0.50 = 10.0
```

This encourages the planner to move during silence.

That is useful because moving during a rest does not interrupt the sound.

---

## Standard Transition Cost Formula

The standard planner transition cost can be summarized as:

```text
transition_cost =
    hand_move × cost_hand_move_per_key × move_scale
  + spread_change × cost_spread_change_per_level × move_scale
  + finger_change × cost_finger_change
```

where:

```text
move_scale = rest_move_discount if the current event is a rest
move_scale = 1.0 otherwise
```

This means:

- moving during a note is expensive
- moving during a rest is cheaper
- spread changes are allowed but penalized
- finger changes are allowed with a small penalty

---

## Static Pose Cost

The planner can also use a small static pose cost as a tie-breaker.

This cost is much smaller than transition cost.

It helps the planner prefer:

- smaller spread
- center closer to the target notes
- center not too far from the keyboard middle

A simplified form is:

```text
static_pose_cost =
    small spread penalty
  + center-to-target penalty
  + center-to-middle penalty
```

This is not the main cost.

It is only used to break ties between otherwise similar candidates.

---

## Example DP Cost Comparison

Suppose the planner compares two possible paths.

### Path A

```text
Move hand center by 2 white-key steps
No spread change
One finger change
```

Cost:

```text
hand movement: 2 × 10.0 = 20.0
spread change: 0 × 2.0  = 0.0
finger change: 1 × 0.6  = 0.6

total = 20.6
```

### Path B

```text
No hand movement
One spread change
Two finger changes
```

Cost:

```text
hand movement: 0 × 10.0 = 0.0
spread change: 1 × 2.0 = 2.0
finger change: 2 × 0.6 = 1.2

total = 3.2
```

The planner prefers Path B because it has a lower total cost.

This matches the real robot: changing spread and fingers is usually better than moving the entire hand a large distance.

---

## Example With Rest Discount

Suppose the robot needs to move 3 white-key steps.

If it moves during a note:

```text
hand_move_cost = 3 × 10.0 = 30.0
```

If it moves during a rest:

```text
hand_move_cost = 3 × 10.0 × 0.50 = 15.0
```

The planner prefers using the rest to prepare for the next note.

This is one of the main advantages of planning across the full sequence.

---

## Why Dynamic Programming Is Better Than Greedy Planning

A greedy planner only asks:

```text
What is the best pose for the current event?
```

The DP planner asks:

```text
What sequence of poses gives the lowest total cost across the full score?
```

Example:

```text
Event 1: C5
Event 2: D5
Event 3: A5
```

A greedy planner may choose the best pose for C5, then later discover that A5 requires a large jump.

A DP planner can choose a slightly less ideal pose for C5 if it makes D5 and A5 much easier to reach later.

This produces smoother and more realistic robotic motion.

---

## DP Planning Flow

The standard planning flow is:

```text
Cleaned hand score
        ↓
Generate candidates for each event
        ↓
Initialize start pose
        ↓
Run dynamic programming across all events
        ↓
Store best previous state for each current state
        ↓
Select lowest-cost final state
        ↓
Backtrack the best path
        ↓
Generate planned hand events
```

---

## Backtracking

After filling the DP table, the planner selects the final state with the lowest total cost.

Then it walks backward through stored backreferences.

This reconstructs the best path from the end of the song back to the beginning.

The final output is reversed back into normal time order.

---

## Action Types

The planner labels each output event with an action type.

Examples:

| Action Type | Meaning |
|---|---|
| `move_hand` | Hand center changes |
| `move_hand_and_finger` | Hand center changes and finger action changes |
| `move_hand_and_chord` | Hand moves and multiple notes are pressed |
| `chord` | Multiple notes are pressed without major movement |
| `chord_spread_change` | Chord is played with a spread change |
| `finger_only` | Only finger/solenoid action changes |
| `hold_shape` | Hand keeps the same shape |
| `rest_reposition` | Hand moves during rest |
| `rest_hold` | Rest event without movement |

These action types help with debugging, UI visualization, and conductor execution.

---

## Dual-Path Planner Extension

The project also includes a dual-path planner.

The dual-path planner extends the basic DP planner by allowing two geometry modes:

| Path | Meaning |
|---|---|
| Path A | Standard white-key-centered hand pose |
| Path B | Half-center pose between two white keys |

Path A is the normal mode.

Path B is used for difficult intervals that are hard to reach using the standard white-key-centered model.

---

## Path A: White-Key Center

Path A uses the normal hand model.

The center is placed on a white key.

Example:

```text
center = G4
spread = 2
```

This is stable and preferred when it works.

---

## Path B: Half-Center Profile

Path B allows the center to be placed between two white keys.

This is useful for special intervals.

Example profiles:

```text
HC_6TH
HC_8VE
```

In this mode, the center is represented using a doubled white-key grid:

```text
white key index i          → slot 2*i
gap between i and i+1      → slot 2*i+1
```

This allows the planner to represent positions between white keys.

---

## Dual-Path Cost Terms

The dual-path planner uses additional cost terms:

```text
cost_hand_move_per_white = 10.0
cost_small_motor_angle_per_deg = 0.03
cost_profile_switch = 0.5
cost_finger_change = 0.6
rest_move_discount = 0.50
```

It also includes path preference terms:

```text
path_a_bonus = 0.20
path_b_penalty = 0.20
```

This means:

- Path A receives a small bonus.
- Path B receives a small penalty.
- Path B is still selected if it produces a better solution.

This prevents the system from overusing special half-center poses when a normal pose is already good enough.

---

## Dual-Path Transition Cost

The dual-path transition cost can be summarized as:

```text
transition_cost =
    hand_move_white × cost_hand_move_per_white × move_scale
  + spread_switch_cost × move_scale
  + finger_change × cost_finger_change
  + path preference term
```

where:

```text
move_scale = rest_move_discount if current event is a rest
move_scale = 1.0 otherwise
```

The path preference term is:

```text
Path A: subtract path_a_bonus
Path B: add path_b_penalty
```

So Path A is slightly preferred when both options are similar.

---

## Spread Switch Cost in Dual-Path Planning

The dual-path planner handles spread changes more carefully.

If both the previous and current candidates have known spread angles, the spread switch cost is:

```text
spread_switch_cost =
    abs(current_spread_angle - previous_spread_angle)
    × cost_small_motor_angle_per_deg
```

With:

```text
cost_small_motor_angle_per_deg = 0.03
```

If spread angles are not available but the spread profile changes, the planner uses:

```text
cost_profile_switch = 0.5
```

This models the physical cost of changing the spread motor profile.

---

## Example Dual-Path Cost

Suppose the robot compares:

### Candidate A

```text
Path A
small hand movement
same spread profile
same finger set
```

Cost:

```text
hand movement = 1 × 10.0 = 10.0
spread switch = 0.0
finger change = 0.0
path bonus = -0.20

total = 9.80
```

### Candidate B

```text
Path B
no hand movement
profile switch
one finger change
```

Cost:

```text
hand movement = 0 × 10.0 = 0.0
profile switch = 0.5
finger change = 1 × 0.6 = 0.6
path penalty = +0.20

total = 1.30
```

Even though Path B has a penalty, it is still better here because it avoids a large hand movement.

This shows why Path B is not always avoided. It is only discouraged when Path A is equally good.

---

## Hardware-Specific Overrides

The planner also includes hardware-specific overrides for edge notes.

Some edge notes may be theoretically playable, but the real mechanism may need a special center note, spread level, or finger assignment.

For example, notes near the low end of the right hand or high end of the left hand may require forced configurations.

These overrides improve real hardware reliability.

They help bridge the gap between mathematical reachability and actual mechanical behavior.

---

## Relationship to Playable Score Adaptation

Playable-score adaptation and DP planning are connected but different.

| Stage | Question |
|---|---|
| Playable score adaptation | Which notes/chords should remain so the score is playable? |
| Dynamic programming planner | Given those notes/chords, how should the robot move to play them? |

The adaptation stage may remove, shift, or simplify notes.

The DP planner chooses the best hand poses for the resulting notes.

---

## Relationship to Transposition Quality Score

The transposition quality score and DP motion cost are also different.

| Item | Used For | Main Question | Higher or Lower Is Better |
|---|---|---|---|
| `quality_score` | Target key selection / transposition evaluation | Is this score generally playable? | Higher is better |
| `DP cost` | Hand motion planning | What pose sequence is easiest to execute? | Lower is better |

The `0.65` value belongs to `quality_score`.

It does not mean hand movement cost.

The DP planner uses motion-related values such as:

```text
10.0 for hand movement
2.0 for spread change
0.6 for finger change
0.50 rest discount
```

This distinction is important when explaining the project.

---

## Example Full Planning Scenario

Input hand score:

```python
[
    (["C5"], 0.50),
    (["E5", "G5"], 0.50),
    ([], 0.25),
    (["A5"], 0.50),
]
```

Possible planned output:

```python
[
    {
        "notes": ["C5"],
        "duration": 0.50,
        "center_note": "G5",
        "spread": 2,
        "finger_ids": ["Lw"],
        "action_type": "move_hand",
        "note_finger_map": {
            "C5": "Lw"
        }
    },
    {
        "notes": ["E5", "G5"],
        "duration": 0.50,
        "center_note": "G5",
        "spread": 2,
        "finger_ids": ["M", "Rw"],
        "action_type": "finger_only",
        "note_finger_map": {
            "E5": "M",
            "G5": "Rw"
        }
    },
    {
        "notes": [],
        "duration": 0.25,
        "center_note": "A5",
        "spread": 1,
        "finger_ids": [],
        "action_type": "rest_reposition",
        "note_finger_map": {}
    },
    {
        "notes": ["A5"],
        "duration": 0.50,
        "center_note": "A5",
        "spread": 1,
        "finger_ids": ["M"],
        "action_type": "move_hand",
        "note_finger_map": {
            "A5": "M"
        }
    }
]
```

In this example, the rest event is used to reposition the hand before A5.

That is exactly the kind of behavior DP planning is designed to find.

---

## Why This Planner Matters

The dynamic programming planner makes the robot more reliable because it:

- reduces unnecessary hand movement
- avoids unstable large jumps
- uses rest time for preparation
- chooses finger assignments systematically
- supports chords
- supports hardware-specific constraints
- provides a complete planned event sequence for the conductor

Without this planner, the robot would behave more like a simple note-by-note machine.

With this planner, the system behaves more like a real robotic performance pipeline.

---

## Summary

The dynamic programming planner solves this problem:

```text
Given a sequence of notes, choose the best sequence of hand poses and finger assignments.
```

It is different from the transposition quality score.

```text
quality_score:
    chooses or evaluates the playable version of the score

DP cost:
    chooses the physical motion path for one robotic hand
```

The main DP idea is:

```text
Generate all possible candidates for each event
        ↓
Calculate transition costs between candidates
        ↓
Accumulate total cost across the sequence
        ↓
Backtrack the lowest-cost path
        ↓
Output executable planned hand events
```

This is one of the core technical components of the dual-hand robotic piano system.