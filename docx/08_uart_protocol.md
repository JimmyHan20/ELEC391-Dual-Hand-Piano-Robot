# UART Protocol

## Overview

This document explains the UART communication protocol between the Python controller and the STM32 firmware.

The Python side performs high-level planning and synchronization. The STM32 side performs low-level hardware control.

The UART protocol connects these two layers.

```text
Python Controller
        ↓ UART text commands
STM32 Firmware
        ↓
Motor / Spread / Solenoid Execution
```

The protocol uses short text commands ending with `!`.

---

## Communication Role

The Python controller is responsible for:

- loading and planning music
- deciding hand center targets
- deciding spread levels
- deciding which fingers should press
- coordinating left and right hands
- sending commands to STM32

The STM32 firmware is responsible for:

- receiving UART commands
- parsing command strings
- controlling motors
- controlling spread mechanism
- driving solenoids
- reporting feedback or telemetry if available

---

## Two-Hand UART Structure

The project uses two logical hands:

```text
left hand
right hand
```

Each hand has its own STM32 connection.

Conceptually:

```text
Python Controller
    ├── UART Left  → Left STM32  → Left Robotic Hand
    └── UART Right → Right STM32 → Right Robotic Hand
```

In the UI, the default ports are:

```text
Left Port:  COM5
Right Port: COM6
Baud Rate: 115200
```

These values can be changed in the UI depending on the actual computer and connected boards.

---

## Command Format

Every command follows this general format:

```text
COMMAND=VALUE!
```

The `!` character marks the end of one command.

Example:

```text
SP=1200.00!
```

This means:

```text
Command: SP
Value:   1200.00
End:     !
```

Using a clear terminator allows the STM32 firmware to know when one full command has been received.

---

## Main Commands

The main commands are:

| Command | Meaning |
|---|---|
| `SP=<angle>!` | Set center motor position |
| `SF=<level>!` | Set spread mechanism level |
| `SL=<digits>!` | Press selected solenoids |
| `SL=0!` | Release all solenoids |
| `RE=1!` | Start homing / reset |

---

## `SP` Command: Set Center Position

The `SP` command moves the main hand center motor to a target angle.

Format:

```text
SP=<angle>!
```

Example:

```text
SP=950.00!
```

Meaning:

```text
Move the center motor to 950.00 degrees.
```

The target angle can come from:

1. A note-to-angle mapping, such as `note_to_angle("G5", "right")`
2. A direct angle override from the dual-path planner

---

## Normal Center Note Movement

For a normal white-key-centered pose, the planner outputs:

```python
center_note = "G5"
```

The conductor converts it into an angle:

```python
angle = note_to_angle(center_note, hand)
```

Then sends:

```text
SP=<angle>!
```

Example:

```text
center_note = G5
angle = 780.00

SP=780.00!
```

---

## Center Angle Override

For special dual-path poses, especially Path B half-center profiles, the center may not be exactly on a normal key.

In that case, the planner may output:

```python
center_angle_override = 950.00
```

Then the conductor sends the override directly:

```text
SP=950.00!
```

This is useful when the robot needs a center position between two white keys.

---

## `SF` Command: Set Spread Level

The `SF` command controls the spread mechanism.

Format:

```text
SF=<level>!
```

Example:

```text
SF=2!
```

Meaning:

```text
Set the spread mechanism to level 2.
```

The spread level controls how far the outer fingers open from the center.

---

## Spread Level Meaning

The hardware spread levels are:

```text
SF=0 → 0°
SF=1 → 40°
SF=2 → 80°
SF=3 → 120°
SF=4 → 160°
```

These levels must stay consistent between the Python planner and STM32 firmware.

If Python sends `SF=2`, the STM32 firmware should move the spread mechanism to the corresponding calibrated target angle.

---

## Path A Spread Mapping

For standard Path A planning, the internal spread index may be mapped to a hardware spread level.

Conceptually:

```text
internal spread 0 → SF=0
internal spread 1 → SF=2
internal spread 2 → SF=4
```

This mapping allows the planner to use a simplified hand model while still sending valid hardware commands.

---

## Path B Spread Profiles

For Path B half-center profiles, the spread level may come from a special profile.

Example:

```text
HC_6TH → SF=1
HC_8VE → SF=3
```

These profiles are used for difficult intervals where the center is between two white keys.

---

## `SL` Command: Solenoid Press

The `SL` command controls the solenoids used to press piano keys.

Format:

```text
SL=<digits>!
```

Example:

```text
SL=13!
```

Meaning:

```text
Press solenoid 1 and solenoid 3.
```

The digits correspond to the planner's finger IDs.

---

## Finger-to-Solenoid Mapping

The planner uses five finger IDs:

```text
Lw, Lb, M, Rb, Rw
```

The conductor maps them to solenoid numbers:

| Finger ID | Solenoid Number |
|---|---|
| `Lw` | 1 |
| `Lb` | 2 |
| `M` | 3 |
| `Rb` | 4 |
| `Rw` | 5 |

Example:

```python
finger_ids = ["Lw", "M"]
```

becomes:

```text
SL=13!
```

Example:

```python
finger_ids = ["Lw", "Rw"]
```

becomes:

```text
SL=15!
```

---

## `SL=0!`: Release All Solenoids

To release all solenoids, the Python controller sends:

```text
SL=0!
```

This command should turn off all active solenoid outputs for that hand.

It is used:

- after a note duration ends
- when playback stops
- during emergency release
- before reset or homing

---

## `RE` Command: Homing / Reset

The `RE` command starts homing or reset behavior.

Format:

```text
RE=1!
```

Meaning:

```text
Start the reset or homing routine.
```

This is used to bring the robot hand back to a known reference position.

Homing is important because motor angle control needs a reliable reference.

---

## Example: Single Note Execution

Suppose the planner outputs:

```python
{
    "notes": ["C5"],
    "center_note": "G5",
    "spread": 2,
    "finger_ids": ["Lw"],
    "duration": 0.50
}
```

The conductor may send:

```text
SP=780.00!
SF=2!
SL=1!
SL=0!
```

Meaning:

1. Move the center motor.
2. Set the spread mechanism.
3. Press the selected finger.
4. Release the solenoid.

---

## Example: Chord Execution

Suppose the planner outputs:

```python
{
    "notes": ["C5", "E5"],
    "center_note": "G5",
    "spread": 2,
    "finger_ids": ["Lw", "M"],
    "duration": 0.50
}
```

The conductor may send:

```text
SP=780.00!
SF=2!
SL=13!
SL=0!
```

Meaning:

```text
Move to target position.
Set spread level.
Press solenoids 1 and 3.
Release all solenoids.
```

---

## Example: Path B Half-Center Execution

Suppose the planner outputs:

```python
{
    "notes": ["C4", "A4"],
    "center_mode": "between_white",
    "center_angle_override": 950.00,
    "spread": 1,
    "spread_profile": "HC_6TH",
    "finger_ids": ["Lw", "Rw"],
    "duration": 1.00
}
```

The conductor may send:

```text
SP=950.00!
SF=1!
SL=15!
SL=0!
```

Meaning:

1. Move the center to a special between-key position.
2. Set the spread profile.
3. Press outer white-key fingers.
4. Release all solenoids.

---

## Command Timing

The conductor generally follows this order:

```text
1. Send center movement command if needed.
2. Send spread command if needed.
3. Wait for movement / spread readiness.
4. Send solenoid press command.
5. Hold for note duration.
6. Send release command.
```

Conceptually:

```text
SP → SF → wait → SL press → hold → SL release
```

This order prevents the solenoid from pressing before the hand has reached the target position.

---

## Avoiding Redundant Commands

The conductor keeps track of the last target center and spread.

If the next event uses the same center and spread, it does not need to send the same movement commands again.

Example:

```text
Event 1: center=G5, spread=2
Event 2: center=G5, spread=2
```

For Event 2, the conductor may only send a solenoid command:

```text
SL=<digits>!
```

This reduces unnecessary motor movement and makes playback smoother.

---

## Feedback and Readiness

If telemetry feedback is available, the conductor can compare:

```text
actual center angle vs target center angle
actual spread angle vs target spread angle
```

Typical tolerance values:

```text
CENTER_TOL_DEG = 5.0
SPREAD_TOL_DEG = 12.0
```

If the actual value is close enough to the target value, the hand is considered ready.

If feedback is not available, the system can fall back to short timing delays.

---

## Pause and Stop Behavior

During pause:

```text
The conductor stops advancing musical time.
No new press command should be sent.
Active timing is preserved.
```

During stop:

```text
The conductor stops playback.
The system sends SL=0! to release solenoids.
The UI returns to the stopped state.
```

For safety, release commands should be sent whenever playback is stopped.

---

## Reset Behavior

During reset:

```text
SL=0!
RE=1!
```

This first releases all solenoids, then starts homing.

Reset should be used before testing if the robot position is unknown.

---

## Debugging Commands Manually

For basic testing, commands can be sent manually through the UART monitor.

Recommended test order:

```text
SL=0!
SP=<safe_angle>!
SF=0!
SL=3!
SL=0!
RE=1!
```

This checks:

1. Solenoid release
2. Center motor movement
3. Spread mechanism
4. Single solenoid press
5. Release behavior
6. Homing behavior

---

## Common Problems

### No response from STM32

Possible causes:

- wrong COM port
- wrong baud rate
- STM32 not powered
- firmware not flashed
- UART wiring issue
- command missing `!`

---

### Motor moves but solenoid does not press

Possible causes:

- solenoid power not connected
- solenoid driver issue
- wrong solenoid mapping
- `SL` command not parsed correctly
- external power supply insufficient

---

### Solenoid presses before hand reaches target

Possible causes:

- readiness feedback unavailable
- center tolerance too loose
- wait time too short
- motor movement is slower than expected
- conductor timing needs adjustment

---

### Spread mechanism does not match expected pose

Possible causes:

- Python spread level mapping does not match STM32 firmware
- spread calibration changed
- motor direction reversed
- encoder feedback not calibrated
- mechanical linkage slipping

---

## Protocol Summary

| Command | Example | Meaning |
|---|---|---|
| `SP=<angle>!` | `SP=950.00!` | Move center motor |
| `SF=<level>!` | `SF=2!` | Set spread level |
| `SL=<digits>!` | `SL=13!` | Press solenoids 1 and 3 |
| `SL=0!` | `SL=0!` | Release all solenoids |
| `RE=1!` | `RE=1!` | Start homing/reset |

---

## Summary

The UART protocol is the bridge between the Python planning system and the STM32 hardware controller.

The Python side decides:

```text
where to move
which spread to use
which fingers to press
when to release
```

The STM32 side executes:

```text
motor control
spread control
solenoid actuation
homing/reset
```

This separation allows the system to combine high-level robotic planning with low-level embedded control.