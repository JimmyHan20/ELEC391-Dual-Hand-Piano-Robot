# Hardware Setup

## Overview

This document explains the hardware setup for the dual-hand robotic piano player.

The system uses a Python controller running on a PC and two STM32-based embedded controllers. Each STM32 controls one robotic hand.

The high-level software handles music processing, planning, and synchronization. The embedded firmware handles motor control, spread control, encoder feedback, and solenoid actuation.

```text
PC / Python Controller
        ↓
Left UART + Right UART
        ↓
Left STM32 + Right STM32
        ↓
Motors + Spread Mechanism + Solenoids
        ↓
Physical Piano Playback
```

---

## System-Level Hardware Architecture

The hardware system can be divided into five parts:

1. PC running the Python controller
2. Left-hand STM32 controller
3. Right-hand STM32 controller
4. Motor and spread mechanisms
5. Solenoid key-pressing mechanisms

Conceptual architecture:

```text
                    ┌─────────────────────────┐
                    │   Python Controller      │
                    │   MIDI / DP / Conductor  │
                    └───────────┬─────────────┘
                                │
                 ┌──────────────┴──────────────┐
                 │                             │
            UART Left                     UART Right
                 │                             │
        ┌────────▼────────┐           ┌────────▼────────┐
        │   Left STM32     │           │   Right STM32    │
        └───────┬─────────┘           └───────┬─────────┘
                │                             │
     ┌──────────┼──────────┐       ┌──────────┼──────────┐
     │          │          │       │          │          │
 Center     Spread     Solenoids Center    Spread     Solenoids
 Motor      Motor                Motor     Motor
```

---

## Main Hardware Components

The system uses:

- PC or laptop
- Two STM32 controller boards
- USB/UART connection for each STM32 board
- Center movement motor for each hand
- Spread mechanism motor for each hand
- Encoder feedback for position measurement
- Solenoids for key pressing
- Solenoid driver circuit
- Motor driver / H-bridge
- External power supply
- Mechanical hand structure
- Piano keyboard or test keybed

---

## PC Side

The PC runs the Python controller and UI.

The Python side is responsible for:

- loading MIDI files
- building left/right score sequences
- running playable-score adaptation
- running dynamic programming planning
- coordinating left and right hands
- sending UART commands
- monitoring serial feedback
- displaying UI state and debugging logs

The PC connects to the STM32 boards through USB serial or UART adapters.

---

## STM32 Side

Each STM32 board controls one hand.

The STM32 firmware is responsible for:

- receiving UART commands
- parsing `SP`, `SF`, `SL`, and `RE`
- generating PWM for motor control
- reading encoder feedback
- controlling motor direction
- controlling spread mechanism
- driving solenoids
- reporting telemetry if enabled
- handling homing/reset behavior

Recommended logical separation:

```text
Left STM32  → left robotic hand
Right STM32 → right robotic hand
```

---

## Serial Connection

The UI supports two serial ports:

```text
Left Port
Right Port
```

Default values:

```text
Left Port:  COM5
Right Port: COM6
Baud Rate: 115200
```

These port names depend on the computer and operating system.

Before running the full system, verify that:

- both STM32 boards appear as serial devices
- left and right ports are different
- baud rate matches the firmware
- both readers connect successfully in the UI

---

## Power Setup

The system should separate logic power and actuator power when possible.

Typical power categories:

| Power Type | Used For |
|---|---|
| USB / logic power | STM32 board and serial communication |
| Motor power | Center motor and spread motor |
| Solenoid power | Solenoid key pressing |
| Common ground | Shared reference between control and driver circuits |

Important:

```text
The STM32 ground and external power supply ground should share a common reference.
```

Without common ground, control signals may not behave correctly.

---

## Center Movement Motor

Each hand has a center movement mechanism.

The center motor moves the hand along the keyboard range.

The Python controller sends:

```text
SP=<angle>!
```

The STM32 firmware moves the center motor to the requested angle.

The target angle may come from:

- note-to-angle mapping
- direct angle override from the planner

Example:

```text
SP=950.00!
```

The STM32 should use motor control and encoder feedback to reach the target angle.

---

## Spread Mechanism

Each hand has a spread mechanism that changes how far the fingers open.

The Python controller sends:

```text
SF=<level>!
```

Example:

```text
SF=2!
```

The spread level corresponds to a calibrated spread angle.

Typical mapping:

```text
SF=0 → 0°
SF=1 → 40°
SF=2 → 80°
SF=3 → 120°
SF=4 → 160°
```

This mapping must be consistent between the Python planner and STM32 firmware.

---

## Solenoid Key Pressing

Solenoids are used to press piano keys.

The Python controller sends:

```text
SL=<digits>!
```

Example:

```text
SL=13!
```

This presses solenoids 1 and 3.

To release all solenoids:

```text
SL=0!
```

The solenoid driver circuit should be able to provide enough current for the solenoids.

Do not power solenoids directly from STM32 GPIO pins.

The STM32 GPIO should control a driver circuit, such as a transistor, MOSFET, driver array, or shift-register-based driver circuit.

---

## Finger-to-Solenoid Layout

The software uses five finger IDs:

```text
Lw, Lb, M, Rb, Rw
```

The conductor maps them to solenoid numbers:

| Finger ID | Solenoid |
|---|---|
| `Lw` | 1 |
| `Lb` | 2 |
| `M` | 3 |
| `Rb` | 4 |
| `Rw` | 5 |

This means:

```text
SL=1!  → press Lw
SL=2!  → press Lb
SL=3!  → press M
SL=4!  → press Rb
SL=5!  → press Rw
SL=15! → press Lw and Rw
```

---

## Encoder Feedback

Encoder feedback is used to estimate actual motor position.

The conductor can compare actual position against target position.

Typical readiness check:

```text
abs(actual_center - target_center) <= tolerance
```

Example tolerance:

```text
CENTER_TOL_DEG = 5.0
```

For the spread mechanism:

```text
SPREAD_TOL_DEG = 12.0
```

If the actual position is close enough to the target, the hand is considered ready to press.

---

## Homing / Reset

Homing is used to bring the robot to a known reference position.

The Python controller can send:

```text
RE=1!
```

The STM32 firmware should run the homing or reset routine.

Recommended reset sequence:

```text
SL=0!
RE=1!
```

This first releases all solenoids, then starts homing.

Homing should be performed:

- before a demo
- after mechanical adjustment
- after unexpected movement
- after emergency stop
- when the current position is unknown

---

## Recommended Startup Procedure

Use this order before a full playback test:

1. Check mechanical parts are clear.
2. Power the STM32 boards.
3. Power external motor and solenoid supplies.
4. Connect left and right STM32 boards to the PC.
5. Open the Python UI.
6. Select the correct left and right serial ports.
7. Click connect.
8. Send or trigger reset/homing.
9. Test center movement with a safe angle.
10. Test spread movement with a low spread level.
11. Test one solenoid press and release.
12. Load a short song.
13. Check the planned event table.
14. Start playback at a safe speed.

---

## Recommended Test Sequence

Before running a full song, test each subsystem separately.

### 1. Serial Test

Send:

```text
SL=0!
```

Expected result:

```text
All solenoids are released.
```

---

### 2. Center Motor Test

Send:

```text
SP=<safe_angle>!
```

Expected result:

```text
The hand center moves to the target angle.
```

---

### 3. Spread Test

Send:

```text
SF=0!
SF=1!
SF=2!
```

Expected result:

```text
The spread mechanism moves through calibrated levels.
```

---

### 4. Solenoid Test

Send:

```text
SL=3!
SL=0!
```

Expected result:

```text
Middle solenoid presses and releases.
```

---

### 5. Single-Hand Test

Run a short sequence with one hand only.

Expected result:

```text
The selected hand moves, presses, and releases correctly.
```

---

### 6. Dual-Hand Test

Run a short song with both hands.

Expected result:

```text
Both hands prepare, move, press, and release according to the planned sequence.
```

---

## Safety Notes

Because this project controls motors and solenoids, safety is important.

Recommended safety practices:

- Keep hands clear of moving parts during testing.
- Start with low-speed or short-motion tests.
- Always test `SL=0!` release behavior.
- Use an external power supply appropriate for the motors and solenoids.
- Do not power solenoids directly from STM32 GPIO pins.
- Check wiring before powering actuators.
- Use homing before full playback if the position is uncertain.
- Stop immediately if the robot makes unexpected movements.
- Avoid testing full-speed songs before single-note tests pass.

---

## Mechanical Alignment

The software assumes that note-to-angle mappings are calibrated.

For example:

```text
C5 → target angle
D5 → target angle
E5 → target angle
```

If the physical mechanism shifts, the mapping must be recalibrated.

Symptoms of bad calibration:

- hand moves to the wrong key
- solenoid presses between keys
- black-key and white-key alignment is off
- Path B half-center poses do not land correctly
- spread profile does not match the expected interval

---

## Calibration Checklist

Before final demonstration, check:

- left-hand center mapping
- right-hand center mapping
- spread level angles
- solenoid order
- solenoid strength
- homing position
- encoder direction
- motor direction
- serial port assignment
- common ground
- external power stability

---

## Common Problems and Fixes

### Problem: UI connects to only one hand

Possible causes:

- wrong COM port
- both hands assigned to same port
- one STM32 board not powered
- one USB cable not detected
- firmware not running

Fix:

```text
Refresh ports.
Select different left and right ports.
Reconnect both boards.
```

---

### Problem: Motor moves in the wrong direction

Possible causes:

- motor wiring reversed
- encoder sign reversed
- firmware direction logic incorrect
- calibration mismatch

Fix:

```text
Check motor wiring.
Check firmware direction setting.
Check encoder sign.
Test with small safe movements.
```

---

### Problem: Solenoid does not press

Possible causes:

- external solenoid power missing
- driver circuit issue
- wrong solenoid mapping
- insufficient current
- command not parsed correctly

Fix:

```text
Check power supply.
Check driver circuit.
Send a manual SL command.
Verify solenoid number mapping.
```

---

### Problem: Hand reaches wrong key

Possible causes:

- note-to-angle mapping incorrect
- homing position shifted
- mechanical slip
- wrong hand selected
- left/right port swapped

Fix:

```text
Run homing.
Check left/right serial assignment.
Verify note-to-angle table.
Recalibrate key positions.
```

---

### Problem: Playback timing is late

Possible causes:

- motor movement takes longer than planned
- spread movement takes too long
- conductor waits for readiness
- song tempo is too fast
- planned movements are too large

Fix:

```text
Reduce tempo.
Use shorter demo songs.
Check planner output.
Use rests for repositioning.
Tune movement timing.
```

---

### Problem: Two hands interfere physically

Possible causes:

- planned hands overlap near boundary region
- cross-hand constraint not strict enough
- mechanical range too close
- spread too large near the other hand

Fix:

```text
Use safer test songs.
Check planned table.
Add or tune cross-hand constraints.
Reduce risky high-left / low-right combinations.
```

---

## Full Demo Preparation Checklist

Before recording or presenting the project:

```text
[ ] GitHub code cleaned
[ ] README completed
[ ] API keys removed
[ ] STM32 firmware flashed
[ ] Left STM32 connected
[ ] Right STM32 connected
[ ] Serial ports selected correctly
[ ] Homing tested
[ ] Solenoids tested
[ ] Center motors tested
[ ] Spread mechanism tested
[ ] Short song tested
[ ] UI table checked
[ ] Demo video recorded
[ ] Emergency stop / release behavior confirmed
```

---

## Hardware-Software Boundary

The project is designed with a clear boundary:

### Python Controller

```text
Music processing
Motion planning
Dual-hand coordination
UART command generation
UI visualization
```

### STM32 Firmware

```text
UART parsing
PWM motor control
Encoder feedback
Spread control
Solenoid actuation
Homing/reset
```

This separation makes the system easier to debug.

If the robot presses the wrong note, check the Python planning and note-to-angle mapping.

If the robot fails to move correctly, check STM32 motor control, encoders, and power.

---

## Summary

The hardware setup consists of:

```text
PC Python controller
    ↓
Two UART connections
    ↓
Two STM32 boards
    ↓
Center motors + spread motors + solenoids
    ↓
Dual-hand robotic piano playback
```

The system works only when software planning, UART communication, embedded firmware, power electronics, and mechanical calibration are all aligned.

This hardware-software integration is the core engineering challenge of the project.