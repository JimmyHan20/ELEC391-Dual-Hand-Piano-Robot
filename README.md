# Dual-Hand MIDI-to-Motion Robotic Piano Player

## Overview

This project is a dual-hand robotic piano-playing system developed for ELEC 391.

The system converts MIDI music into coordinated physical piano-playing actions. It first loads a song from a MIDI file, built-in score, or custom input, then separates the music into left-hand and right-hand score sequences. Each hand is planned using dynamic programming to select reachable hand poses, spread levels, and finger assignments. If the original music exceeds the robot's physical range or mechanical capability, the system applies playable-score adaptation and music-aware note reduction to preserve important musical information while keeping the motion executable.

After planning, the system builds a global timeline and uses a dual-hand conductor to coordinate both robotic hands. The conductor sends commands to two STM32-controlled hands through UART, allowing the robot to prepare, move, press, release, and synchronize both hands during playback.

This project demonstrates an end-to-end robotics pipeline from music input to real hardware execution.

---

## Core Idea

The correct system logic is:

```text
MIDI / Song Input
        ↓
Left-Hand Score + Right-Hand Score
        ↓
Playable Score Adaptation
        ↓
Dual-Hand Dynamic Programming Planning
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
UART Commands
        ↓
Left STM32 + Right STM32
        ↓
Motor + Spread Mechanism + Solenoid Actuation
        ↓
Physical Piano Playback