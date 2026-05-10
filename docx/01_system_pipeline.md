# System Pipeline

## Overview

This project is a dual-hand robotic piano-playing system that converts music input into coordinated physical piano-playing actions.

The system is not only a simple UART control program. It includes a complete music-to-robot pipeline:

1. Load music from MIDI files or built-in song data.
2. Convert the music into left-hand and right-hand score sequences.
3. Adapt the score based on the robot's physical and musical limitations.
4. Plan both robotic hands using dynamic programming.
5. Apply dual-path planning and cross-hand constraint handling.
6. Build a global timeline for press, release, and hold events.
7. Use a dual-hand conductor to coordinate both hands during real playback.
8. Send UART commands to STM32 firmware.
9. Execute motor, spread, and solenoid actions on the physical robot.

The main goal of this project is to bridge the gap between ideal piano music and real robotic hardware constraints.

---

## Full Pipeline

```text
MIDI File / Built-in Song / Custom Input
        ↓
Song Loader / MIDI Loader
        ↓
Left-Hand Score + Right-Hand Score
        ↓
Playable Score Adaptation
        ↓
Dual-Hand Dynamic Programming Planner
        ↓
Dual-Path Pose Selection
        ↓
Cross-Hand Constraint Re-planning
        ↓
Planned Left Events + Planned Right Events
        ↓
Global Timeline Builder
        ↓
Dual-Hand Conductor
        ↓
UART Command Dispatch
        ↓
Left STM32 Firmware + Right STM32 Firmware
        ↓
Motor + Spread Mechanism + Solenoid Control
        ↓
Physical Piano Playback