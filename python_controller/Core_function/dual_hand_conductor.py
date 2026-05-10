import threading
import time
from dataclasses import dataclass
from itertools import zip_longest

from Core_function.note import note_to_angle

# Program structure:
# 1. `HandTransport` wraps the STM command format for one logical hand.
# 2. `DualHandConductor` normalizes planner output and runs the dual-hand FSM.
# 3. Each global beat flows through: prepare -> move -> wait -> execute -> release.
# 4. If beat-time readiness is late, the lateness accumulates and shifts the rest
#    of the song later.

CENTER_TOL_DEG = 5.0
CENTER_WAIT_S = 5.0
SPREAD_SETTLE_S = 0.08
SPREAD_WAIT_S = 2.0
SPREAD_TOL_DEG = 12.0
MIN_RELEASE_GAP_S = 0.03
HOME_WAIT_S = 4.5

FINGER_TO_SOLENOID = {
    "Lw": 1,
    "Lb": 2,
    "M": 3,
    "Rb": 4,
    "Rw": 5,
}

SOLENOID_ORDER = ("Lw", "Lb", "M", "Rb", "Rw")
HANDS = ("right", "left")


def _is_close(a, b, tol=1e-6):
    """Compare two numeric values with tolerance.

    Input:
    - `a`: first numeric-like value, or `None`
    - `b`: second numeric-like value, or `None`
    - `tol`: absolute tolerance

    Output:
    - `True` when both values exist and differ by at most `tol`, else `False`
    """
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


@dataclass
class HandTransport:
    name: str
    reader: object
    command_prefix: str = ""

    def _write(self, cmd: str):
        """Send one raw command string to the underlying STM writer.

        Input:
        - `cmd`: command body such as `SP=950.00!`

        Output:
        - none; forwards the command to `reader.write(...)`
        """
        self.reader.write(f"{self.command_prefix}{cmd}")

    def send_center_note(self, center_note: str) -> float:
        """Move the large motor using a note name.

        Input:
        - `center_note`: note string accepted by `note_to_angle`

        Output:
        - the target center angle sent to STM as `float`
        """
        angle = note_to_angle(center_note, self.name)
        self._write(f"SP={angle:.2f}!")
        return angle

    def send_center_angle(self, angle: float) -> float:
        """Move the large motor using an already computed angle.

        Input:
        - `angle`: center target angle in degrees

        Output:
        - the normalized float angle that was sent
        """
        angle = float(angle)
        self._write(f"SP={angle:.2f}!")
        return angle

    def send_spread(self, spread: int):
        """Command the spread motor level.

        Input:
        - `spread`: integer spread level

        Output:
        - none; sends `SF=<spread>!`
        """
        self._write(f"SF={int(spread)}!")

    def press_fingers(self, finger_ids) -> str:
        """Press one or more fingers using the sequencer-style solenoid encoding.

        Input:
        - `finger_ids`: iterable of planner finger ids, e.g. `["Lw", "Rw"]`

        Output:
        - concatenated solenoid digits string that was sent, e.g. `"15"`
        """
        chosen = set(str(x).strip() for x in (finger_ids or []) if str(x).strip())
        digits = []
        for fid in SOLENOID_ORDER:
            if fid in chosen:
                digits.append(str(FINGER_TO_SOLENOID[fid]))
        joined = "".join(digits)
        if not joined:
            raise ValueError(f"[{self.name}] No valid finger_ids to press.")
        self._write(f"SL={joined}!")
        return joined

    def release_all(self):
        """Release all currently pressed fingers for this hand.

        Input:
        - none

        Output:
        - none; sends `SL=0!`
        """
        self._write("SL=0!")

    def home(self):
        """Send the homing command for this hand.

        Input:
        - none

        Output:
        - none; sends `RE=1!`
        """
        self._write("RE=1!")


class DualHandConductor(threading.Thread):
    """
    Central dual-hand FSM.

    Assumptions:
    - `planned_right[i]` and `planned_left[i]` represent the same global beat.
    - If one side is shorter, it is padded with REST events.
    - Global beat duration is the max of both hand durations for that index.
    - If one hand is not ready when the beat arrives, the beat is delayed and the
      delay accumulates into all subsequent beats.
    """

    def __init__(
        self,
        planned_right=None,
        planned_left=None,
        reader=None,
        out_q=None,
        stop_evt=None,
        pause_evt=None,
        get_actual_fn=None,
        get_actual_spread_fn=None,
        hand_transports=None,
        command_prefixes=None,
    ):
        """Construct the dual-hand conductor thread.

        Input:
        - `planned_right`: planner output list for the right hand
        - `planned_left`: planner output list for the left hand
        - `reader`: shared serial-like writer used when `hand_transports` is not supplied
        - `out_q`: optional queue for UI/status events
        - `stop_evt`: optional stop event
        - `pause_evt`: optional pause event
        - `get_actual_fn`: center-feedback callable or `{hand: callable}` mapping
        - `get_actual_spread_fn`: spread-feedback callable or `{hand: callable}` mapping
        - `hand_transports`: optional `{hand: HandTransport}` mapping
        - `command_prefixes`: optional per-hand command prefixes

        Output:
        - initialized `DualHandConductor` instance
        """
        super().__init__(daemon=True)

        self.planned_right = self._normalize_hand_song(planned_right or [])
        self.planned_left = self._normalize_hand_song(planned_left or [])

        self.reader = reader
        self.out_q = out_q
        self.stop_evt = stop_evt if stop_evt is not None else threading.Event()
        self.pause_evt = pause_evt if pause_evt is not None else threading.Event()

        self._pause_notified = False
        self._pause_release_sent = False

        command_prefixes = command_prefixes or {}
        if hand_transports is None:
            if reader is None:
                raise ValueError("reader is required when hand_transports is not provided.")
            hand_transports = {
                hand: HandTransport(hand, reader, command_prefix=command_prefixes.get(hand, ""))
                for hand in HANDS
            }
        self.hand_transports = hand_transports

        self.get_actual_center = {
            hand: self._resolve_hand_callable(get_actual_fn, hand)
            for hand in HANDS
        }
        self.get_actual_spread = {
            hand: self._resolve_hand_callable(get_actual_spread_fn, hand)
            for hand in HANDS
        }

        self._last_center_target_angle = {hand: None for hand in HANDS}
        self._last_spread = {hand: None for hand in HANDS}
        self._pressed_fingers = {hand: [] for hand in HANDS}
        self._log_lock = threading.Lock()

    # --------------------------------------------------------
    # Input normalization
    # --------------------------------------------------------
    def _normalize_hand_song(self, seq):
        """Normalize one hand's event list into conductor-ready dicts.

        Input:
        - `seq`: iterable of planner event dicts

        Output:
        - list of normalized event dicts
        """
        return [self._normalize_event(ev) for ev in (seq or [])]

    def _normalize_event(self, ev):
        """Normalize a single planner event.

        Input:
        - `ev`: planner event dict for one hand

        Output:
        - normalized event dict with stable field types
        """
        if not isinstance(ev, dict):
            raise TypeError(f"Event must be dict, got {type(ev)!r}")

        duration = self._safe_duration(ev.get("duration", 0.1))
        notes = self._norm_notes(ev.get("notes", []))
        center_note = self._norm_note(ev.get("center_note", ""))
        spread = self._safe_int(ev.get("spread", 0), default=0)
        finger_ids = self._norm_fingers(ev.get("finger_ids", []))
        action_type = str(ev.get("action_type", "hold_shape"))
        note_finger_map = self._norm_note_finger_map(ev.get("note_finger_map", {}))

        center_angle_override = ev.get("center_angle_override", None)
        spread_angle_override = ev.get("spread_angle_override", None)
        try:
            center_angle_override = None if center_angle_override is None else float(center_angle_override)
        except Exception:
            center_angle_override = None
        try:
            spread_angle_override = None if spread_angle_override is None else float(spread_angle_override)
        except Exception:
            spread_angle_override = None

        return {
            "notes": notes,
            "duration": duration,
            "center_mode": ev.get("center_mode"),
            "center_slot": ev.get("center_slot"),
            "center_note": center_note,
            "spread": spread,
            "spread_profile": ev.get("spread_profile"),
            "center_angle_override": center_angle_override,
            "spread_angle_override": spread_angle_override,
            "finger_ids": finger_ids,
            "action_type": action_type,
            "note_finger_map": note_finger_map,
            "source_path": ev.get("source_path"),
            "mid_available": bool(ev.get("mid_available", False)),
        }

    def _rest_event(self):
        """Build a synthetic rest event for padding uneven hand lengths.

        Input:
        - none

        Output:
        - normalized rest event dict
        """
        return self._normalize_event({
            "notes": [],
            "duration": 0.0,
            "center_mode": None,
            "center_slot": None,
            "center_note": "",
            "spread": 0,
            "spread_profile": None,
            "center_angle_override": None,
            "spread_angle_override": 0.0,
            "finger_ids": [],
            "action_type": "rest_hold",
            "note_finger_map": {},
            "source_path": None,
            "mid_available": False,
        })

    # --------------------------------------------------------
    # Utility helpers
    # --------------------------------------------------------
    def _resolve_hand_callable(self, obj, hand):
        """Resolve a per-hand feedback callback.

        Input:
        - `obj`: callable, `{hand: callable}` mapping, or `None`
        - `hand`: `"right"` or `"left"`

        Output:
        - callable returning the requested feedback value, or a `None`-returning fallback
        """
        if isinstance(obj, dict):
            fn = obj.get(hand)
            return fn if callable(fn) else (lambda: None)
        if callable(obj):
            return obj
        return lambda: None

    def _safe_duration(self, duration) -> float:
        """Convert a duration-like value to a non-negative float.

        Input:
        - `duration`: any numeric-like object

        Output:
        - non-negative float duration
        """
        try:
            value = float(duration)
        except Exception:
            value = 0.1
        return max(0.0, value)

    def _safe_int(self, x, default=0) -> int:
        """Convert a value to int with a fallback default.

        Input:
        - `x`: any int-like value
        - `default`: fallback integer if conversion fails

        Output:
        - integer result
        """
        try:
            return int(x)
        except Exception:
            return int(default)

    def _norm_note(self, x) -> str:
        """Normalize one note token.

        Input:
        - `x`: note-like object or `None`

        Output:
        - uppercase note string, or empty string for missing/rest values
        """
        if x is None:
            return ""
        s = str(x).strip().upper()
        if not s or s == "REST":
            return ""
        return s

    def _norm_notes(self, xs):
        """Normalize a list of note tokens.

        Input:
        - `xs`: iterable of note-like values

        Output:
        - list of normalized non-empty note strings
        """
        out = []
        for x in xs or []:
            s = self._norm_note(x)
            if s:
                out.append(s)
        return out

    def _norm_finger(self, x) -> str:
        """Normalize one finger token.

        Input:
        - `x`: finger-like object

        Output:
        - stripped finger id string, or `"REST"` when empty
        """
        s = str(x).strip()
        return s if s else "REST"

    def _norm_fingers(self, xs):
        """Normalize a list of finger ids.

        Input:
        - `xs`: iterable of finger-like values

        Output:
        - list of normalized finger ids excluding `"REST"`
        """
        out = []
        for x in xs or []:
            s = self._norm_finger(x)
            if s != "REST":
                out.append(s)
        return out

    def _norm_note_finger_map(self, mp):
        """Normalize a note-to-finger mapping.

        Input:
        - `mp`: mapping from note-like keys to finger-like values

        Output:
        - cleaned dict containing only valid note/finger entries
        """
        out = {}
        for note, finger in (mp or {}).items():
            n = self._norm_note(note)
            f = self._norm_finger(finger)
            if n and f != "REST":
                out[n] = f
        return out

    def _elapsed(self, song_t0: float, paused_accum: float) -> float:
        """Compute song-time elapsed excluding paused time.

        Input:
        - `song_t0`: wall-clock start time
        - `paused_accum`: total paused duration already accumulated

        Output:
        - non-negative elapsed song time in seconds
        """
        return max(0.0, time.perf_counter() - song_t0 - paused_accum)

    def _put(self, tag, payload):
        """Emit one event to the output queue when a queue exists.

        Input:
        - `tag`: queue event name
        - `payload`: queue event payload

        Output:
        - none
        """
        if self.out_q is not None:
            self.out_q.put((tag, payload))

    def _log_bool_text(self, value):
        """Format readiness values consistently for conductor logs."""
        if value is None:
            return "N/A"
        return "ready" if bool(value) else "waiting"

    def _init_beat_log_context(self, nominal_beat_t: float, starting_items: dict, current_active: dict, accumulated_wait_s: float):
        """Create one mutable per-beat log snapshot shared by all log emitters."""
        hand_states = {}
        readiness = {}
        for hand in HANDS:
            item = starting_items.get(hand)
            active = current_active.get(hand)
            if item is not None:
                hand_states[hand] = "get_event"
                readiness[hand] = None
            elif active is not None:
                hand_states[hand] = "execute"
                readiness[hand] = None
            else:
                hand_states[hand] = "get_event"
                readiness[hand] = None
        return {
            "nominal_beat_t": float(nominal_beat_t),
            "starting_items": starting_items,
            "current_active": current_active,
            "accumulated_wait_s": float(accumulated_wait_s),
            "hand_states": hand_states,
            "readiness": readiness,
        }

    def _default_hand_state(self, item, active):
        """Infer one hand's baseline state from the current beat context."""
        if item is not None:
            return "get_event"
        if active is not None:
            return "execute"
        return "get_event"

    def _event_action_text(self, item, active):
        """Summarize one hand's current event type and next action for logs."""
        if item is not None:
            event = item.get("event", {})
            event_type = str(event.get("action_type", "rest_hold" if item.get("is_rest") else "hold_shape"))
            action = "start_rest" if item.get("is_rest") else "prepare_press"
            return f"{event_type}->{action}"
        if active is not None:
            event = active.get("event", {})
            event_type = str(event.get("action_type", "rest_hold" if active.get("is_rest") else "hold_shape"))
            action = "hold_rest" if active.get("is_rest") else "release"
            return f"{event_type}->{action}"
        return "idle->wait"

    def _log_conductor_status(self, log_context: dict, hand_states=None, readiness=None):
        """Emit one conductor status line with the shared beat-centric schema."""
        hand_states = hand_states or {}
        readiness = readiness or {}

        with self._log_lock:
            log_context["hand_states"].update(hand_states)
            log_context["readiness"].update(readiness)

            event_parts = []
            state_parts = []
            readiness_parts = []
            for hand in HANDS:
                item = log_context["starting_items"].get(hand)
                active = log_context["current_active"].get(hand)
                default_state = self._default_hand_state(item, active)
                event_parts.append(f"{hand}={self._event_action_text(item, active)}")
                state_parts.append(f"{hand}={log_context['hand_states'].get(hand, default_state)}")
                readiness_parts.append(f"{hand}={self._log_bool_text(log_context['readiness'].get(hand))}")

            self._put(
                "status",
                " ".join([
                    f"[DHC] global_beat={log_context['nominal_beat_t']:.3f}",
                    f"events=({' , '.join(event_parts)})".replace(" , ", ", "),
                    f"states=({' , '.join(state_parts)})".replace(" , ", ", "),
                    f"accumulated_wait={log_context['accumulated_wait_s']:.3f}",
                    f"readiness=({' , '.join(readiness_parts)})".replace(" , ", ", "),
                ]),
            )

    def _event_note_text(self, ev) -> str:
        """Format an event's note list for logging."""
        notes = self._norm_notes((ev or {}).get("notes", []))
        return "/".join(notes) if notes else "REST"

    def _log_beat_snapshot(self, log_context: dict, prep_tasks: dict):
        """Emit one compact per-beat summary line using the shared log schema."""
        hand_states = {}
        readiness = {}
        for hand in HANDS:
            item = log_context["starting_items"].get(hand)
            active = log_context["current_active"].get(hand)
            task = prep_tasks.get(hand)
            if item is not None and item.get("is_rest"):
                hand_states[hand] = "get_event"
                readiness[hand] = None
            elif item is not None:
                if task is not None:
                    hand_states[hand] = log_context["hand_states"].get(hand, "prep")
                else:
                    hand_states[hand] = "get_event"
                readiness[hand] = None
            elif active is not None:
                hand_states[hand] = "execute"
                readiness[hand] = None
            else:
                hand_states[hand] = "get_event"
                readiness[hand] = None

        self._log_conductor_status(log_context, hand_states=hand_states, readiness=readiness)

    def _handle_pause(self, song_t0: float, paused_accum: float):
        """Apply pause behavior and return updated paused time.

        Input:
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - updated `paused_accum`, or `None` if stop was requested during pause
        """
        if not self.pause_evt.is_set():
            self._pause_notified = False
            self._pause_release_sent = False
            return paused_accum

        if not self._pause_notified:
            self._put("seq_paused", {"elapsed": self._elapsed(song_t0, paused_accum)})
            self._pause_notified = True

        if not self._pause_release_sent:
            for hand in HANDS:
                try:
                    self.hand_transports[hand].release_all()
                except Exception:
                    pass
            self._pause_release_sent = True

        pause_t0 = time.perf_counter()
        while self.pause_evt.is_set():
            if self.stop_evt.is_set():
                return None
            time.sleep(0.01)
        paused_for = time.perf_counter() - pause_t0
        paused_accum += paused_for
        self._put("seq_resumed", {"paused_for": paused_for, "elapsed": self._elapsed(song_t0, paused_accum)})
        self._pause_notified = False
        self._pause_release_sent = False
        return paused_accum

    def _sleep_for(self, seconds: float, song_t0: float, paused_accum: float):
        """Sleep cooperatively while honoring stop and pause events.

        Input:
        - `seconds`: target sleep duration
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - `(ok, paused_accum)` where `ok` is `False` if sleep was interrupted by stop
        """
        remain = max(0.0, float(seconds))
        while remain > 0.0:
            if self.stop_evt.is_set():
                return False, paused_accum
            updated = self._handle_pause(song_t0, paused_accum)
            if updated is None:
                return False, paused_accum
            paused_accum = updated
            chunk = min(0.01, remain)
            t0 = time.perf_counter()
            time.sleep(chunk)
            remain -= (time.perf_counter() - t0)
        return True, paused_accum

    def _wait_until_center(self, hand: str, target_angle: float, song_t0: float, paused_accum: float):
        """Wait until a hand's center motor reaches its target.

        Input:
        - `hand`: `"right"` or `"left"`
        - `target_angle`: desired center angle
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - `(ok, actual, paused_accum)` where `ok` reports success and `actual` is the last feedback angle
        """
        get_actual = self.get_actual_center[hand]
        if get_actual() is None:
            ok, paused_accum = self._sleep_for(0.12, song_t0, paused_accum)
            return ok, None, paused_accum

        t0 = time.perf_counter()
        actual = None
        while not self.stop_evt.is_set():
            updated = self._handle_pause(song_t0, paused_accum)
            if updated is None:
                return False, actual, paused_accum
            paused_accum = updated

            try:
                actual = get_actual()
            except Exception:
                actual = None

            if actual is not None and abs(float(actual) - float(target_angle)) <= CENTER_TOL_DEG:
                return True, actual, paused_accum

            if time.perf_counter() - t0 >= CENTER_WAIT_S:
                return False, actual, paused_accum
            time.sleep(0.01)

        return False, actual, paused_accum

    def _wait_until_spread_ready(self, hand: str, target_angle: float, song_t0: float, paused_accum: float):
        """Wait until a hand's spread motor is ready.

        Input:
        - `hand`: `"right"` or `"left"`
        - `target_angle`: desired spread angle, or `None` for open-loop settle only
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - `(ok, actual, paused_accum)` where `ok` reports success and `actual` is the last feedback angle
        """
        if target_angle is None:
            ok, paused_accum = self._sleep_for(SPREAD_SETTLE_S, song_t0, paused_accum)
            return ok, None, paused_accum

        get_actual = self.get_actual_spread[hand]
        if get_actual() is None:
            ok, paused_accum = self._sleep_for(SPREAD_SETTLE_S, song_t0, paused_accum)
            return ok, None, paused_accum

        t0 = time.perf_counter()
        actual = None
        while not self.stop_evt.is_set():
            updated = self._handle_pause(song_t0, paused_accum)
            if updated is None:
                return False, actual, paused_accum
            paused_accum = updated

            try:
                actual = get_actual()
            except Exception:
                actual = None

            if actual is not None and abs(float(actual) - float(target_angle)) <= SPREAD_TOL_DEG:
                return True, actual, paused_accum

            if time.perf_counter() - t0 >= SPREAD_WAIT_S:
                return False, actual, paused_accum
            time.sleep(0.01)

        return False, actual, paused_accum

    def _target_center_angle(self, hand: str, ev):
        """Resolve the effective center angle for one event.

        Input:
        - `ev`: normalized event dict

        Output:
        - target center angle as `float`, or `None` if no center move is defined
        """
        override = ev.get("center_angle_override")
        if override is not None:
            return float(override)
        center_note = self._norm_note(ev.get("center_note", ""))
        if center_note:
            return note_to_angle(center_note, hand)
        return None

    def _target_spread_angle(self, ev):
        """Resolve the effective spread angle for one event.

        Input:
        - `ev`: normalized event dict

        Output:
        - target spread angle as `float`, or `None` if only spread level is available
        """
        override = ev.get("spread_angle_override")
        if override is not None:
            return float(override)
        return None

    def _event_needs_move(self, hand: str, ev):
        """Check whether the hand must reposition before execution.

        Input:
        - `hand`: `"right"` or `"left"`
        - `ev`: normalized event dict

        Output:
        - `True` when center or spread changed and the event is not a pure `rest_hold`
        """
        action_type = str(ev.get("action_type", "hold_shape"))
        if action_type == "rest_hold":
            return False

        target_center = self._target_center_angle(hand, ev)
        target_spread = int(ev.get("spread", 0))
        return (
            (target_center is not None and not _is_close(target_center, self._last_center_target_angle[hand]))
            or self._last_spread[hand] != target_spread
        )

    def _is_press_event(self, ev):
        """Check whether an event should issue a finger press.

        Input:
        - `ev`: normalized event dict

        Output:
        - `True` when the event contains both notes and finger ids
        """
        return bool(self._norm_notes(ev.get("notes", [])) and self._norm_fingers(ev.get("finger_ids", [])))

    def _prepare_hand(self, hand: str, ev, global_idx: int, total: int, song_t0: float, paused_accum: float):
        """Prepare one hand for the current global beat.

        Input:
        - `hand`: `"right"` or `"left"`
        - `ev`: normalized event dict for that hand
        - `global_idx`: current global beat index
        - `total`: total number of global beats
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - dict describing the prepared hand state for the execute phase
        """
        notes = self._norm_notes(ev.get("notes", []))
        finger_ids = self._norm_fingers(ev.get("finger_ids", []))
        center_note = self._norm_note(ev.get("center_note", ""))
        spread = self._safe_int(ev.get("spread", 0), default=0)
        action_type = str(ev.get("action_type", "hold_shape"))
        note_finger_map = self._norm_note_finger_map(ev.get("note_finger_map", {}))
        primary_note = notes[0] if notes else "REST"
        primary_finger = finger_ids[0] if finger_ids else "REST"

        self._put("seq_note", {
            "idx": global_idx,
            "total": total,
            "hand": hand,
            "notes": notes,
            "note": primary_note,
            "center_note": center_note,
            "spread": spread,
            "finger_ids": finger_ids,
            "finger_id": primary_finger,
            "note_finger_map": note_finger_map,
            "action_type": action_type,
        })

        target_center_angle = self._target_center_angle(hand, ev)
        target_spread_angle = self._target_spread_angle(ev)
        need_move = self._event_needs_move(hand, ev)
        transport = self.hand_transports[hand]

        actual_after_move = None
        actual_spread = None
        move_started_at = time.perf_counter()

        if need_move and target_center_angle is not None and not _is_close(target_center_angle, self._last_center_target_angle[hand]):
            if ev.get("center_angle_override") is not None:
                transport.send_center_angle(target_center_angle)
            else:
                transport.send_center_note(center_note)
            ok, actual_after_move, paused_accum = self._wait_until_center(
                hand, target_center_angle, song_t0, paused_accum
            )
            move_time = time.perf_counter() - move_started_at
            self._put("seq_move_time", {
                "idx": global_idx,
                "hand": hand,
                "note": primary_note,
                "notes": notes,
                "center_note": center_note,
                "target_angle": target_center_angle,
                "actual_angle": actual_after_move,
                "reached": ok,
                "move_time": move_time,
            })
            if not ok:
                raise RuntimeError(f"[{hand}] Center timeout")
            self._last_center_target_angle[hand] = target_center_angle

        if need_move and self._last_spread[hand] != spread:
            self._put("status", (
                f"[SPREAD DEBUG] hand={hand} notes={notes or ['REST']} center={center_note or '-'} "
                f"spread={spread} target_spread_angle={target_spread_angle} last_spread={self._last_spread[hand]} "
                f"source_path={ev.get('source_path')} spread_profile={ev.get('spread_profile')}"
            ))
            transport.send_spread(spread)
            ok, actual_spread, paused_accum = self._wait_until_spread_ready(
                hand, target_spread_angle, song_t0, paused_accum
            )
            if not ok:
                raise RuntimeError(
                    f"[{hand}] Spread timeout on level {spread} "
                    f"notes={notes or ['REST']} center={center_note or '-'} "
                    f"target_angle={target_spread_angle} actual_angle={actual_spread} "
                    f"source_path={ev.get('source_path')} spread_profile={ev.get('spread_profile')}"
                )
            self._last_spread[hand] = spread

        return {
            "hand": hand,
            "event": ev,
            "need_move": need_move,
            "is_press": self._is_press_event(ev),
            "notes": notes,
            "finger_ids": finger_ids,
            "primary_note": primary_note,
            "primary_finger": primary_finger,
            "duration": self._safe_duration(ev.get("duration", 0.0)),
            "paused_accum": paused_accum,
            "actual_center": actual_after_move,
            "actual_spread": actual_spread,
        }

    def _execute_press(self, prepared, global_idx: int, song_t0: float, paused_accum: float):
        """Execute the press phase for one prepared hand.

        Input:
        - `prepared`: hand-preparation dict returned by `_prepare_hand`
        - `global_idx`: current global beat index
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - none; sends the press command and emits `seq_press`
        """
        hand = prepared["hand"]
        notes = prepared["notes"]
        finger_ids = prepared["finger_ids"]
        primary_note = prepared["primary_note"]
        primary_finger = prepared["primary_finger"]
        transport = self.hand_transports[hand]
        digits = transport.press_fingers(finger_ids)
        self._pressed_fingers[hand] = list(finger_ids)
        self._put("seq_press", {
            "idx": global_idx,
            "hand": hand,
            "note": primary_note,
            "notes": notes,
            "finger_id": primary_finger,
            "finger_ids": finger_ids,
            "solenoid": digits,
            "elapsed": self._elapsed(song_t0, paused_accum),
        })

    def _execute_release(self, hand: str, prepared, global_idx: int, song_t0: float, paused_accum: float):
        """Execute the release phase for one hand.

        Input:
        - `hand`: `"right"` or `"left"`
        - `prepared`: hand-preparation dict returned by `_prepare_hand`
        - `global_idx`: current global beat index
        - `song_t0`: wall-clock start time
        - `paused_accum`: paused time accumulated so far

        Output:
        - none; sends the release command and emits `seq_release`
        """
        notes = prepared["notes"]
        finger_ids = prepared["finger_ids"]
        primary_note = prepared["primary_note"]
        primary_finger = prepared["primary_finger"]
        self.hand_transports[hand].release_all()
        self._pressed_fingers[hand] = []
        self._put("seq_release", {
            "idx": global_idx,
            "hand": hand,
            "note": primary_note,
            "notes": notes,
            "finger_id": primary_finger,
            "finger_ids": finger_ids,
            "elapsed": self._elapsed(song_t0, paused_accum),
        })

    def _home_all(self):
        """Send the home command to both logical hands.

        Input:
        - none

        Output:
        - none
        """
        for hand in HANDS:
            try:
                self.hand_transports[hand].home()
            except Exception:
                pass

    def _build_hand_timeline(self, planned_seq, hand: str):
        """Build one hand's independent time-aligned event timeline."""
        timeline = []
        start_time = 0.0
        total = len(planned_seq or [])
        for idx, ev in enumerate(planned_seq or []):
            duration = self._safe_duration(ev.get("duration", 0.0))
            end_time = start_time + duration
            timeline.append({
                "hand": hand,
                "idx": idx,
                "total": total,
                "event": ev,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "is_rest": not self._is_press_event(ev),
            })
            start_time = end_time
        return timeline

    def _sleep_until_song_time(self, target_song_time: float, song_t0: float, paused_accum: float):
        """Wait until a hand-local target song time while honoring pause/stop."""
        while not self.stop_evt.is_set():
            if self.pause_evt.is_set():
                pause_t0 = time.perf_counter()
                while self.pause_evt.is_set():
                    if self.stop_evt.is_set():
                        return False, paused_accum
                    time.sleep(0.01)
                paused_accum += (time.perf_counter() - pause_t0)
                continue

            remain = float(target_song_time) - self._elapsed(song_t0, paused_accum)
            if remain <= 0.0:
                return True, paused_accum

            time.sleep(min(0.01, remain))

        return False, paused_accum

    def _sleep_for_local(self, seconds: float, song_t0: float, paused_accum: float):
        """Hand-local sleep that does not emit global pause/resume events."""
        target_song_time = self._elapsed(song_t0, paused_accum) + max(0.0, float(seconds))
        return self._sleep_until_song_time(target_song_time, song_t0, paused_accum)

    def _wait_until_center_local(self, hand: str, target_angle: float, song_t0: float, paused_accum: float):
        """Per-hand center wait used by independent hand FSM threads."""
        get_actual = self.get_actual_center[hand]
        if get_actual() is None:
            ok, paused_accum = self._sleep_for_local(0.12, song_t0, paused_accum)
            return ok, None, paused_accum

        t0 = time.perf_counter()
        actual = None
        while not self.stop_evt.is_set():
            if self.pause_evt.is_set():
                pause_t0 = time.perf_counter()
                while self.pause_evt.is_set():
                    if self.stop_evt.is_set():
                        return False, actual, paused_accum
                    time.sleep(0.01)
                paused_accum += (time.perf_counter() - pause_t0)
                continue

            try:
                actual = get_actual()
            except Exception:
                actual = None

            if actual is not None and abs(float(actual) - float(target_angle)) <= CENTER_TOL_DEG:
                return True, actual, paused_accum

            if time.perf_counter() - t0 >= CENTER_WAIT_S:
                return False, actual, paused_accum

            time.sleep(0.01)

        return False, actual, paused_accum

    def _wait_until_spread_ready_local(self, hand: str, target_angle: float, song_t0: float, paused_accum: float):
        """Per-hand spread wait used by independent hand FSM threads."""
        if target_angle is None:
            ok, paused_accum = self._sleep_for_local(SPREAD_SETTLE_S, song_t0, paused_accum)
            return ok, None, paused_accum

        get_actual = self.get_actual_spread[hand]
        if get_actual() is None:
            ok, paused_accum = self._sleep_for_local(SPREAD_SETTLE_S, song_t0, paused_accum)
            return ok, None, paused_accum

        t0 = time.perf_counter()
        actual = None
        while not self.stop_evt.is_set():
            if self.pause_evt.is_set():
                pause_t0 = time.perf_counter()
                while self.pause_evt.is_set():
                    if self.stop_evt.is_set():
                        return False, actual, paused_accum
                    time.sleep(0.01)
                paused_accum += (time.perf_counter() - pause_t0)
                continue

            try:
                actual = get_actual()
            except Exception:
                actual = None

            if actual is not None and abs(float(actual) - float(target_angle)) <= SPREAD_TOL_DEG:
                return True, actual, paused_accum

            if time.perf_counter() - t0 >= SPREAD_WAIT_S:
                return False, actual, paused_accum

            time.sleep(0.01)

        return False, actual, paused_accum

    def _prepare_hand_local(
        self,
        hand: str,
        ev,
        global_idx: int,
        total: int,
        song_t0: float,
        paused_accum: float,
        log_context=None,
    ):
        """Hand-local prepare step that can run concurrently with the other hand."""
        notes = self._norm_notes(ev.get("notes", []))
        finger_ids = self._norm_fingers(ev.get("finger_ids", []))
        center_note = self._norm_note(ev.get("center_note", ""))
        spread = self._safe_int(ev.get("spread", 0), default=0)
        action_type = str(ev.get("action_type", "hold_shape"))
        note_finger_map = self._norm_note_finger_map(ev.get("note_finger_map", {}))
        primary_note = notes[0] if notes else "REST"
        primary_finger = finger_ids[0] if finger_ids else "REST"

        self._put("seq_note", {
            "idx": global_idx,
            "total": total,
            "hand": hand,
            "notes": notes,
            "note": primary_note,
            "center_note": center_note,
            "spread": spread,
            "finger_ids": finger_ids,
            "finger_id": primary_finger,
            "note_finger_map": note_finger_map,
            "action_type": action_type,
        })

        target_center_angle = self._target_center_angle(hand, ev)
        target_spread_angle = self._target_spread_angle(ev)
        need_move = self._event_needs_move(hand, ev)
        transport = self.hand_transports[hand]

        actual_after_move = None
        actual_spread = None
        move_started_at = time.perf_counter()

        if need_move and log_context is not None:
            self._log_conductor_status(log_context, hand_states={hand: "move"}, readiness={hand: None})

        if need_move and target_center_angle is not None and not _is_close(target_center_angle, self._last_center_target_angle[hand]):
            if ev.get("center_angle_override") is not None:
                transport.send_center_angle(target_center_angle)
            else:
                transport.send_center_note(center_note)
            ok, actual_after_move, paused_accum = self._wait_until_center_local(
                hand, target_center_angle, song_t0, paused_accum
            )
            move_time = time.perf_counter() - move_started_at
            self._put("seq_move_time", {
                "idx": global_idx,
                "hand": hand,
                "note": primary_note,
                "notes": notes,
                "center_note": center_note,
                "target_angle": target_center_angle,
                "actual_angle": actual_after_move,
                "reached": ok,
                "move_time": move_time,
            })
            if not ok:
                raise RuntimeError(f"[{hand}] Center timeout")
            self._last_center_target_angle[hand] = target_center_angle

        if need_move and self._last_spread[hand] != spread:
            self._put("status", (
                f"[SPREAD DEBUG] hand={hand} notes={notes or ['REST']} center={center_note or '-'} "
                f"spread={spread} target_spread_angle={target_spread_angle} last_spread={self._last_spread[hand]} "
                f"source_path={ev.get('source_path')} spread_profile={ev.get('spread_profile')}"
            ))
            transport.send_spread(spread)
            ok, actual_spread, paused_accum = self._wait_until_spread_ready_local(
                hand, target_spread_angle, song_t0, paused_accum
            )
            if not ok:
                raise RuntimeError(
                    f"[{hand}] Spread timeout on level {spread} "
                    f"notes={notes or ['REST']} center={center_note or '-'} "
                    f"target_angle={target_spread_angle} actual_angle={actual_spread} "
                    f"source_path={ev.get('source_path')} spread_profile={ev.get('spread_profile')}"
                )
            self._last_spread[hand] = spread

        return {
            "hand": hand,
            "event": ev,
            "need_move": need_move,
            "is_press": self._is_press_event(ev),
            "notes": notes,
            "finger_ids": finger_ids,
            "primary_note": primary_note,
            "primary_finger": primary_finger,
            "duration": self._safe_duration(ev.get("duration", 0.0)),
            "paused_accum": paused_accum,
            "actual_center": actual_after_move,
            "actual_spread": actual_spread,
        }

    def _find_next_prepare_index(self, timeline, current_idx: int, hand: str):
        """Find the next future event worth preparing during a rest window."""
        for next_idx in range(current_idx + 1, len(timeline)):
            next_ev = timeline[next_idx]["event"]
            if self._is_press_event(next_ev) or self._event_needs_move(hand, next_ev):
                return next_idx
        return None

    def _spawn_prepare_task(
        self,
        hand: str,
        timeline,
        idx: int,
        song_t0: float,
        prep_tasks: dict,
        log_context: dict,
    ):
        """Start or reuse one in-flight prepare task for a future hand event."""
        if idx is None:
            return

        existing = prep_tasks.get(hand)
        if existing is not None and existing.get("idx") == idx:
            return

        box = {}

        def _worker():
            try:
                item = timeline[idx]
                self._log_conductor_status(log_context, hand_states={hand: "prep"}, readiness={hand: None})
                prepared = self._prepare_hand_local(
                    hand=hand,
                    ev=item["event"],
                    global_idx=idx,
                    total=len(timeline),
                    song_t0=song_t0,
                    paused_accum=0.0,
                    log_context=log_context,
                )
                box["prepared"] = prepared
            except Exception as e:
                box["error"] = e

        th = threading.Thread(target=_worker, daemon=True)
        prep_tasks[hand] = {
            "idx": idx,
            "thread": th,
            "box": box,
        }
        th.start()

    def _wait_prepare_task(
        self,
        hand: str,
        idx: int,
        prep_tasks: dict,
        song_t0: float,
        paused_accum: float,
        log_context: dict,
    ):
        """Wait until a hand's prepare task finishes and return its prepared payload."""
        task = prep_tasks.get(hand)
        if task is None or task.get("idx") != idx:
            raise RuntimeError(f"[{hand}] Missing prepare task for event {idx}")

        th = task["thread"]
        box = task["box"]
        while th.is_alive():
            if self.stop_evt.is_set():
                return None, paused_accum, "stopped"
            updated = self._handle_pause(song_t0, paused_accum)
            if updated is None:
                return None, paused_accum, "stopped"
            paused_accum = updated
            time.sleep(0.01)

        if box.get("error") is not None:
            raise box["error"]

        prepared = box.get("prepared")
        if prepared is None:
            raise RuntimeError(f"[{hand}] Prepare task returned no result for event {idx}")
        self._log_conductor_status(log_context, hand_states={hand: "wait"}, readiness={hand: True})
        return prepared, paused_accum, None

    def _run_hand_fsm(self, hand: str, timeline, song_t0: float, result_box: dict):
        """Run one hand's independent FSM over its own timeline."""
        paused_accum = 0.0
        prepared_cache = {}

        try:
            for item in timeline:
                idx = int(item["idx"])
                ev = item["event"]

                ok, paused_accum = self._sleep_until_song_time(item["start_time"], song_t0, paused_accum)
                if not ok:
                    result_box[hand] = {"stopped": True, "paused_accum": paused_accum}
                    return

                prepared = prepared_cache.pop(idx, None)
                if prepared is None:
                    prepared = self._prepare_hand_local(
                        hand=hand,
                        ev=ev,
                        global_idx=idx,
                        total=len(timeline),
                        song_t0=song_t0,
                        paused_accum=paused_accum,
                    )
                    paused_accum = prepared["paused_accum"]

                if prepared["is_press"]:
                    self._execute_press(prepared, idx, song_t0, paused_accum)

                    ok, paused_accum = self._sleep_until_song_time(item["end_time"], song_t0, paused_accum)
                    if not ok:
                        result_box[hand] = {"stopped": True, "paused_accum": paused_accum}
                        return

                    self._execute_release(hand, prepared, idx, song_t0, paused_accum)
                    ok, paused_accum = self._sleep_for_local(MIN_RELEASE_GAP_S, song_t0, paused_accum)
                    if not ok:
                        result_box[hand] = {"stopped": True, "paused_accum": paused_accum}
                        return
                    continue

                self._put("seq_rest_start", {
                    "idx": idx,
                    "hand": hand,
                    "elapsed": self._elapsed(song_t0, paused_accum),
                    "duration": item["duration"],
                })

                next_idx = self._find_next_prepare_index(timeline, idx, hand)
                if next_idx is not None and next_idx not in prepared_cache:
                    next_prepared = self._prepare_hand_local(
                        hand=hand,
                        ev=timeline[next_idx]["event"],
                        global_idx=next_idx,
                        total=len(timeline),
                        song_t0=song_t0,
                        paused_accum=paused_accum,
                    )
                    paused_accum = next_prepared["paused_accum"]
                    prepared_cache[next_idx] = next_prepared

                ok, paused_accum = self._sleep_until_song_time(item["end_time"], song_t0, paused_accum)
                if not ok:
                    result_box[hand] = {"stopped": True, "paused_accum": paused_accum}
                    return

                self._put("seq_rest_end", {
                    "idx": idx,
                    "hand": hand,
                    "elapsed": self._elapsed(song_t0, paused_accum),
                    "duration": item["duration"],
                })

            result_box[hand] = {
                "stopped": False,
                "paused_accum": paused_accum,
                "elapsed": self._elapsed(song_t0, paused_accum),
            }
        except Exception as e:
            result_box[hand] = {"error": e, "paused_accum": paused_accum}

    # --------------------------------------------------------
    # Main run
    # --------------------------------------------------------
    def run(self):
        """Run the full dual-hand playback FSM.

        Input:
        - none; uses instance state initialized in `__init__`

        Output:
        - none; sends STM commands and emits queue events until completion or stop
        """
        left_timeline = self._build_hand_timeline(self.planned_left, "left")
        right_timeline = self._build_hand_timeline(self.planned_right, "right")
        timelines = {
            "left": left_timeline,
            "right": right_timeline,
        }
        total = {
            "left": len(left_timeline),
            "right": len(right_timeline),
        }

        self._put("seq_started", {
            "total": total,
            "song": {
                "right": self.planned_right,
                "left": self.planned_left,
            },
        })

        try:
            self._put("seq_home_start", None)
            self._put(
                "status",
                "[DHC] global_beat=HOME events=(right=home->home, left=home->home) "
                "states=(right=move, left=move) accumulated_wait=0.000 "
                "readiness=(right=N/A, left=N/A)",
            )
            self._home_all()
        except Exception as e:
            self._put("error", f"[DualHandConductor] Failed to home: {e}")
            self._put("seq_stop", {"elapsed": 0.0})
            return

        home_t0 = time.perf_counter()
        while not self.stop_evt.is_set():
            if time.perf_counter() - home_t0 >= HOME_WAIT_S:
                break
            time.sleep(0.01)

        if self.stop_evt.is_set():
            self._put("seq_stop", {"elapsed": 0.0})
            return

        home_elapsed = max(0.0, time.perf_counter() - home_t0)
        self._put("seq_home_done", {"elapsed": home_elapsed})

        song_t0 = time.perf_counter()
        paused_accum = 0.0
        accumulated_late_s = 0.0
        current_active = {hand: None for hand in HANDS}
        prep_tasks = {}

        beat_times = set([0.0])
        for hand in HANDS:
            for item in timelines[hand]:
                beat_times.add(float(item["start_time"]))
            if timelines[hand]:
                beat_times.add(float(timelines[hand][-1]["end_time"]))
        beat_times = sorted(beat_times)

        start_lookup = {
            hand: {float(item["start_time"]): item for item in timelines[hand]}
            for hand in HANDS
        }

        for nominal_beat_t in beat_times:
            if self.stop_evt.is_set():
                self._put("seq_stop", {"elapsed": self._elapsed(song_t0, paused_accum)})
                return

            target_time = float(nominal_beat_t) + accumulated_late_s

            starting_items = {
                hand: start_lookup[hand].get(float(nominal_beat_t))
                for hand in HANDS
            }
            beat_log_context = self._init_beat_log_context(
                nominal_beat_t=nominal_beat_t,
                starting_items=starting_items,
                current_active=current_active,
                accumulated_wait_s=accumulated_late_s,
            )

            self._log_beat_snapshot(beat_log_context, prep_tasks=prep_tasks)

            if self._elapsed(song_t0, paused_accum) < target_time:
                ok, paused_accum = self._sleep_for(target_time - self._elapsed(song_t0, paused_accum), song_t0, paused_accum)
                if not ok:
                    self._put("seq_stop", {"elapsed": self._elapsed(song_t0, paused_accum)})
                    return

            # Global beat reached: close any prior hand event whose nominal end is now.
            released_hands_this_beat = set()
            effective_beat_time = self._elapsed(song_t0, paused_accum)
            for hand in HANDS:
                active = current_active[hand]
                if active is not None and _is_close(active["end_time"], nominal_beat_t):
                    if active["is_rest"]:
                        self._log_conductor_status(
                            beat_log_context,
                            hand_states={hand: "execute"},
                        )
                        self._put("seq_rest_end", {
                            "idx": active["idx"],
                            "hand": hand,
                            "elapsed": effective_beat_time,
                            "duration": active["duration"],
                        })
                    else:
                        prepared = active.get("prepared")
                        if prepared is not None:
                            self._log_conductor_status(
                                beat_log_context,
                                hand_states={hand: "execute"},
                            )
                            self._execute_release(hand, prepared, int(active["idx"]), song_t0, paused_accum)
                            released_hands_this_beat.add(hand)
                    current_active[hand] = None

            # Only now is it safe to start preparing a press on this beat.
            # This prevents the same hand from moving before its previous press is released.
            for hand in HANDS:
                item = starting_items[hand]
                if item is not None and not item["is_rest"]:
                    self._spawn_prepare_task(
                        hand,
                        timelines[hand],
                        int(item["idx"]),
                        song_t0,
                        prep_tasks,
                        beat_log_context,
                    )

            waiting_on_hands = []
            for hand in HANDS:
                item = starting_items[hand]
                if item is None or item["is_rest"]:
                    continue
                task = prep_tasks.get(hand)
                if task is None or task["thread"].is_alive():
                    waiting_on_hands.append(hand)

            if waiting_on_hands:
                self._log_conductor_status(beat_log_context)

            required_hands = []
            prepared_now = {}
            ready_hands = []
            try:
                for hand in HANDS:
                    item = starting_items[hand]
                    if item is None or item["is_rest"]:
                        continue
                    prepared, paused_accum, status = self._wait_prepare_task(
                        hand=hand,
                        idx=int(item["idx"]),
                        prep_tasks=prep_tasks,
                        song_t0=song_t0,
                        paused_accum=paused_accum,
                        log_context=beat_log_context,
                    )
                    if status == "stopped":
                        self._put("seq_stop", {"elapsed": self._elapsed(song_t0, paused_accum)})
                        return
                    prepared_now[hand] = prepared
                    required_hands.append(hand)
                    ready_hands.append(hand)
            except Exception as e:
                self._put("error", f"[DualHandConductor] {e}")
                self._put("seq_stop", {"elapsed": self._elapsed(song_t0, paused_accum)})
                return

            late_now = max(0.0, self._elapsed(song_t0, paused_accum) - target_time)
            if late_now > 0.0:
                accumulated_late_s += late_now
                beat_log_context["accumulated_wait_s"] = accumulated_late_s
                self._log_conductor_status(
                    beat_log_context,
                    hand_states={hand: "wait" for hand in required_hands},
                    readiness={hand: True for hand in required_hands},
                )
            else:
                self._log_conductor_status(
                    beat_log_context,
                    hand_states={hand: "wait" for hand in required_hands},
                    readiness={hand: True for hand in required_hands},
                )

            effective_beat_time = self._elapsed(song_t0, paused_accum)
            self._put("seq_wait", {
                "idx": None,
                "elapsed": effective_beat_time,
                "late_by": late_now,
                "accumulated_late_s": accumulated_late_s,
            })

            reattack_hands = [
                hand for hand in HANDS
                if hand in released_hands_this_beat
                and starting_items.get(hand) is not None
                and not starting_items[hand]["is_rest"]
            ]
            if reattack_hands:
                ok, paused_accum = self._sleep_for(MIN_RELEASE_GAP_S, song_t0, paused_accum)
                if not ok:
                    self._put("seq_stop", {"elapsed": self._elapsed(song_t0, paused_accum)})
                    return

            # Start events on this beat after ready-gating resolves.
            for hand in HANDS:
                item = starting_items[hand]
                if item is None:
                    continue

                if item["is_rest"]:
                    current_active[hand] = dict(item)
                    self._log_conductor_status(
                        beat_log_context,
                        hand_states={hand: "execute"},
                    )
                    self._put("seq_rest_start", {
                        "idx": item["idx"],
                        "hand": hand,
                        "elapsed": effective_beat_time,
                        "duration": item["duration"],
                    })

                    # Only prepare a future note after this hand has actually entered
                    # the rest/execute phase for the current beat. This avoids moving
                    # while the previous press is still physically down.
                    next_idx = self._find_next_prepare_index(timelines[hand], int(item["idx"]), hand)
                    if next_idx is not None:
                        self._spawn_prepare_task(
                            hand,
                            timelines[hand],
                            next_idx,
                            song_t0,
                            prep_tasks,
                            beat_log_context,
                        )
                    continue

                prepared = prepared_now[hand]
                self._log_conductor_status(
                    beat_log_context,
                    hand_states={hand: "execute"},
                    readiness={hand: None},
                )
                self._execute_press(prepared, int(item["idx"]), song_t0, paused_accum)
                current_active[hand] = dict(item)
                current_active[hand]["prepared"] = prepared

        final_elapsed = self._elapsed(song_t0, paused_accum)
        self._put("seq_time", final_elapsed)
        self._put("seq_done", {"elapsed": final_elapsed, "accumulated_late_s": accumulated_late_s})
