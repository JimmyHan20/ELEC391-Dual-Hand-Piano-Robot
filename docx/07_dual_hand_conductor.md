# Dual-Hand Conductor

## Overview

This document explains the dual-hand conductor used in the robotic piano system.

The dual-hand conductor is the real-time execution layer. It receives the planned left-hand and right-hand event sequences and sends commands to the STM32-controlled robotic hands.

The conductor does not decide the musical notes or the optimal hand poses. Those decisions are already made by the earlier planning stages.

Instead, the conductor answers this question:

```text
Given the planned events, how do we coordinate both physical hands in real time?
```

---

## Position in the Pipeline

The conductor is near the end of the system pipeline.

```text
MIDI / Song Input
        ↓
Playable Score Adaptation
        ↓
Dynamic Programming + Dual-Path Planner
        ↓
Global Timeline / Planned Hand Events
        ↓
Dual-Hand Conductor
        ↓
UART Commands
        ↓
STM32 Firmware
        ↓
Physical Piano Playback
```

The planner creates the event list.

The conductor executes the event list.

---

## Main Responsibilities

The dual-hand conductor is responsible for:

- normalizing planned events
- coordinating left-hand and right-hand timing
- sending center movement commands
- sending spread commands
- waiting for motor readiness
- sending solenoid press commands
- sending solenoid release commands
- handling pause and stop
- handling homing/reset
- emitting UI/debug status messages
- delaying later events if one hand is not ready on time

---

## HandTransport

The `HandTransport` layer wraps the STM32 command format for one logical hand.

Each hand has a transport object:

```python
HandTransport("left", reader_left)
HandTransport("right", reader_right)
```

The transport converts high-level hand actions into UART command strings.

---

## UART Commands

The main STM32 commands are:

```text
SP=<angle>!
```

Move the center motor to a target angle.

```text
SF=<level>!
```

Set the spread mechanism level.

```text
SL=<digits>!
```

Press one or more solenoids.

```text
SL=0!
```

Release all solenoids.

```text
RE=1!
```

Start homing/reset.

---

## Finger-to-Solenoid Mapping

The planner uses finger IDs:

```text
Lw, Lb, M, Rb, Rw
```

The conductor maps them to solenoid numbers:

```text
Lw → 1
Lb → 2
M  → 3
Rb → 4
Rw → 5
```

Example:

```python
finger_ids = ["Lw", "M"]
```

becomes:

```text
SL=13!
```

This presses solenoids 1 and 3.

---

## Input Event Format

The conductor receives planned events from the planner.

Example:

```python
{
    "notes": ["C5", "E5"],
    "duration": 0.50,
    "center_mode": "white_key",
    "center_slot": 8,
    "center_note": "G5",
    "spread": 2,
    "spread_profile": None,
    "center_angle_override": None,
    "spread_angle_override": 80.0,
    "finger_ids": ["Lw", "M"],
    "action_type": "move_hand_and_chord",
    "note_finger_map": {
        "C5": "Lw",
        "E5": "M"
    },
    "source_path": "A"
}
```

The conductor normalizes fields such as:

- notes
- duration
- center note
- spread
- finger IDs
- angle overrides
- note-to-finger map

This makes execution more stable even if some fields are missing or formatted differently.

---

## Execution Stages

Each active note event goes through this general sequence:

```text
prepare → move → wait → execute → release
```

Meaning:

| Stage | Meaning |
|---|---|
| `prepare` | Read the event and calculate required target position |
| `move` | Send center/spread commands if needed |
| `wait` | Wait until the hand reaches the target position |
| `execute` | Press the selected solenoids |
| `release` | Release the solenoids after the note duration |

This is the bridge between ideal planned timing and real hardware movement.

---

## Prepare Stage

During the prepare stage, the conductor reads the event and extracts:

- notes
- finger IDs
- center note
- spread level
- action type
- note-to-finger mapping
- target center angle
- target spread angle

It also emits UI/debug information such as:

```text
current hand
current notes
center note
spread
finger IDs
action type
```

This allows the UI to display what the robot is preparing to do.

---

## Move Stage

If the hand needs to move, the conductor sends movement commands.

For a normal white-key center:

```text
SP=<angle from note_to_angle(center_note)>!
```

For a Path B half-center pose:

```text
SP=<center_angle_override>!
```

Then it sends spread if needed:

```text
SF=<spread>!
```

The conductor avoids sending unnecessary repeated movement commands if the target center and spread are already the same as the previous event.

This reduces redundant hardware movement.

---

## Wait Stage

After sending movement commands, the conductor waits for the hand to become ready.

It can use feedback functions such as:

```python
get_actual_center()
get_actual_spread()
```

The system compares actual position with target position using tolerances.

Typical readiness parameters include:

```text
CENTER_TOL_DEG = 5.0
SPREAD_TOL_DEG = 12.0
CENTER_WAIT_S = 5.0
SPREAD_WAIT_S = 2.0
```

If feedback is not available, the conductor can use a short timing fallback.

If the hand does not reach the target within the wait limit, the conductor can raise an error or stop playback.

---

## Execute Stage

When the hand is ready and the musical time arrives, the conductor presses the required fingers.

Example:

```python
finger_ids = ["Lw", "Rw"]
```

becomes:

```text
SL=15!
```

The conductor then emits a `seq_press` event to the UI/debug queue.

This lets the UI update the currently pressed notes.

---

## Release Stage

After the note duration, the conductor releases the hand:

```text
SL=0!
```

It also emits a `seq_release` event to the UI/debug queue.

A small minimum release gap can be used so that consecutive solenoid actions do not overlap too aggressively.

Example:

```text
MIN_RELEASE_GAP_S = 0.03
```

---

## Dual-Hand Synchronization

The conductor coordinates both hands so that they follow the planned musical timing.

For each global beat, it considers:

- what the left hand should do
- what the right hand should do
- whether each hand needs to move
- whether each hand is ready
- whether notes should be pressed together

The conceptual global beat flow is:

```text
get left/right event
        ↓
prepare required hands
        ↓
move hands if needed
        ↓
wait for readiness
        ↓
execute press
        ↓
release when duration ends
```

---

## Handling Late Readiness

Real motors may not always reach the target exactly on time.

If one hand is late, the conductor delays the beat instead of blindly pressing early or skipping the event.

Conceptually:

```text
if hand is not ready at target beat time:
    wait until hand is ready
    accumulated_late_s += delay
    shift following beats later
```

This makes playback more physically reliable.

The trade-off is that the song may become slightly slower, but the robot avoids unsafe or incomplete actions.

---

## Pause and Stop

The conductor supports pause and stop behavior.

During pause:

- timing accumulation is suspended
- playback does not continue advancing
- pause duration is tracked
- elapsed song time excludes paused time

During stop:

- conductor exits execution
- active solenoids should be released
- playback state returns to stopped

This is important for safe hardware testing.

---

## Homing / Reset

The conductor can send homing commands to both hands:

```text
RE=1!
```

This is used to reset the physical hand position before or after playback.

Homing is important because a real robot must start from a known position.

---

## UI and Debug Events

The conductor emits status events to the UI queue.

Examples include:

- `seq_note`
- `seq_move_time`
- `seq_press`
- `seq_release`
- `seq_rest_start`
- `seq_rest_end`
- `status`
- `error`
- `seq_stop`

These events allow the UI to show:

- current note
- current hand
- planned center/spread
- actual movement time
- whether the hand reached its target
- live logs
- playback state

This is useful for debugging real hardware behavior.

---

## Example Execution

Suppose the planned event is:

```python
{
    "notes": ["C5", "E5"],
    "duration": 0.50,
    "center_note": "G5",
    "spread": 2,
    "finger_ids": ["Lw", "M"]
}
```

Execution sequence:

```text
1. Calculate target angle for center_note G5.
2. Send SP=<target_angle>!
3. Send SF=2!
4. Wait until center and spread are ready.
5. Send SL=13!
6. Hold for 0.50 seconds.
7. Send SL=0!
```

This converts the planned event into physical robot actions.

---

## Example Dual-Hand Beat

Suppose at time 2.00s:

```text
Left hand:  C3
Right hand: E5
```

The conductor prepares both hands.

When both hands are ready and the target time is reached:

```text
Left hand sends SL=<left fingers>!
Right hand sends SL=<right fingers>!
```

Then both release after their planned durations.

This is how the robot produces coordinated two-hand playback.

---

## Why the Conductor Is Needed

Without the conductor, the system would only have planned events.

The conductor is needed because real hardware has timing uncertainty:

- motors take time to move
- spread mechanism takes time to settle
- solenoids need press/release timing
- serial communication has delay
- one hand may finish preparing before the other
- pause/stop safety must be handled

The conductor manages these real-world execution details.

---

## Relationship to STM32 Firmware

The Python conductor handles:

- high-level timing
- event sequencing
- dual-hand synchronization
- command dispatch

The STM32 firmware handles:

- UART command parsing
- PWM motor control
- encoder feedback
- spread motor control
- solenoid actuation

This separation keeps the Python side flexible and the STM32 side focused on low-level real-time control.

---

## Summary

The dual-hand conductor is the execution brain of the system.

It takes planned left/right hand events and turns them into synchronized physical actions.

Core idea:

```text
planned hand event
        ↓
prepare
        ↓
move center/spread
        ↓
wait until ready
        ↓
press solenoids
        ↓
release solenoids
        ↓
continue to next event
```

This layer is what turns the project from a motion planner into a real robotic piano-playing system.