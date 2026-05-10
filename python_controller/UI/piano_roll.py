import tkinter as tk

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
FLAT_MAP = {
    "DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#",
}


def midi_to_name(note_num: int) -> str:
    pitch = NOTE_NAMES[note_num % 12]
    octave = (note_num // 12) - 1
    return f"{pitch}{octave}"


def normalize_note_name(note_name: str) -> str:
    s = str(note_name).strip().upper()
    if not s:
        return ""
    if s == "REST":
        return "REST"

    if len(s) >= 3 and s[1] == "B":
        pitch = s[:2]
        octave = s[2:]
        pitch = FLAT_MAP.get(pitch, pitch)
        return f"{pitch}{octave}"
    return s


def name_to_midi(note_name: str) -> int:
    s = normalize_note_name(note_name)
    if len(s) < 2:
        raise ValueError(f"Bad note: {note_name}")

    if s[1] == "#":
        pitch = s[:2]
        octave = int(s[2:])
    else:
        pitch = s[:1]
        octave = int(s[1:])

    return NOTE_NAMES.index(pitch) + (octave + 1) * 12


def is_black(note_name: str) -> bool:
    return "#" in normalize_note_name(note_name)


class PianoRollView(tk.Frame):
    BG = "#04070b"
    GRID = "#122033"
    GRID_SOFT = "#0b1520"
    GRID_TIME = "#183047"

    KEY_WHITE = "#f3f3f5"
    KEY_BLACK = "#0f1115"
    KEY_BORDER = "#28313a"
    KEY_ACTIVE = "#72f2df"
    KEY_ACTIVE_SOFT = "#b6fff4"
    KEY_ARM = "#6aa8ff"
    KEYBED = "#d8dde5"
    KEY_SHADOW = "#a8b2bf"
    BLACK_KEY_GLOSS = "#2a313a"

    TEXT = "#dce7f7"
    SUBTEXT = "#8ea0b8"
    WARN = "#ffb86b"

    PLAYHEAD = "#ffffff"
    PLAYHEAD_GLOW = "#96fff1"

    RIGHT_NOTE = "#63e7d8"
    RIGHT_NOTE_EDGE = "#b9fff7"
    LEFT_NOTE = "#77a9ff"
    LEFT_NOTE_EDGE = "#d3e3ff"

    LEFT_KEY_ACTIVE = "#8fb2ff"
    LEFT_KEY_ACTIVE_SOFT = "#dce8ff"
    LEFT_ARM_SOFT = "#b8ccff"

    RIGHT_KEY_ACTIVE = "#72f2df"
    RIGHT_KEY_ACTIVE_SOFT = "#ccfff8"
    RIGHT_ARM_SOFT = "#bafcf2"

    def __init__(self, master, start_note="A0", end_note="C8", **kwargs):
        bg = kwargs.pop("bg", self.BG)
        super().__init__(master, bg=bg, **kwargs)

        self.start_note = start_note
        self.end_note = end_note

        start_midi = name_to_midi(start_note)
        end_midi = name_to_midi(end_note)
        if end_midi < start_midi:
            raise ValueError("end_note must be >= start_note")

        self.total_keys = end_midi - start_midi + 1
        self.key_notes = [midi_to_name(start_midi + i) for i in range(self.total_keys)]
        self.white_notes = [note for note in self.key_notes if not is_black(note)]
        self.white_index_map = {note: idx for idx, note in enumerate(self.white_notes)}

        self.song_blocks = []
        self.song_total_duration = 1.0
        self.song_name = ""
        self.status_text = "Stopped"

        self.play_time = 0.0
        self.current_note = None   # 这个先保留，给标题/兼容用

        self.pressed_left_note = None
        self.pressed_right_note = None

        self.arm_left_note = None
        self.arm_right_note = None

        self.current_move_time = None
        self.current_move_reached = None

        self.padding_x = 20
        self.header_h = 44
        self.hit_h = 6
        self.keyboard_h = 190
        self.note_margin_white = 4
        self.note_margin_black = 1

        self.top_time_pad = 24
        self.bottom_time_pad = 20
        self.px_per_second = 100.0

        self.follow_playhead = False  # 想自动跟随就改成 True

        self._redraw_pending = False
        self._scroll_to_start_on_rebuild = False
        self._content_h = 1000

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header_canvas = tk.Canvas(
            self,
            bg=self.BG,
            highlightthickness=0,
            height=self.header_h,
        )
        self.header_canvas.grid(row=0, column=0, sticky="ew")

        self.roll_canvas = tk.Canvas(
            self,
            bg=self.BG,
            highlightthickness=0,
            yscrollincrement=24,
        )
        self.roll_canvas.grid(row=1, column=0, sticky="nsew")

        self.v_scroll = tk.Scrollbar(
            self,
            orient="vertical",
            command=self.roll_canvas.yview,
        )
        self.v_scroll.grid(row=1, column=1, sticky="ns")
        self.roll_canvas.configure(yscrollcommand=self.v_scroll.set)

        self.hit_canvas = tk.Canvas(
            self,
            bg=self.BG,
            highlightthickness=0,
            height=self.hit_h,
        )
        self.hit_canvas.grid(row=2, column=0, sticky="ew")

        self.keyboard_canvas = tk.Canvas(
            self,
            bg=self.BG,
            highlightthickness=0,
            height=self.keyboard_h,
        )
        self.keyboard_canvas.grid(row=3, column=0, sticky="ew")

        self._playhead_glow_id = None
        self._playhead_id = None

        self.bind("<Configure>", self._on_configure)
        self.roll_canvas.bind("<Configure>", self._on_configure)
        self.keyboard_canvas.bind("<Configure>", self._on_configure)
        self.header_canvas.bind("<Configure>", self._on_configure)

        # 滚轮：直接滚时间轴
        for widget in (self.roll_canvas, self.keyboard_canvas, self.hit_canvas, self.header_canvas):
            widget.bind("<MouseWheel>", self._on_mousewheel_windows)
            widget.bind("<Button-4>", self._on_mousewheel_linux_up)
            widget.bind("<Button-5>", self._on_mousewheel_linux_down)

        self._request_redraw()

    def set_pressed_notes(self, left_note=None, right_note=None):
        self.pressed_left_note = self._clean_note(left_note)
        self.pressed_right_note = self._clean_note(right_note)
        self._draw_keyboard()

    def set_arm_notes(self, left_note=None, right_note=None):
        self.arm_left_note = self._clean_note(left_note)
        self.arm_right_note = self._clean_note(right_note)
        self._draw_keyboard()
    # =========================================================
    # Public API
    # =========================================================
    def set_song(self, song_name, song):
        self.song_name = str(song_name) if song_name else ""
        self.song_blocks, self.song_total_duration = self._normalize_song_input(song)

        self.play_time = 0.0
        self.current_note = None
        self.pressed_left_note = None
        self.pressed_right_note = None
        self.arm_left_note = None
        self.arm_right_note = None
        self.current_move_time = None
        self.current_move_reached = None

        self._scroll_to_start_on_rebuild = True
        self._request_redraw()

    def set_play_time(self, seconds: float):
        try:
            self.play_time = max(0.0, float(seconds))
        except Exception:
            self.play_time = 0.0

        self._update_header()
        self._update_playhead()

    def set_current_note(self, note):
        self.current_note = self._clean_note(note)
        self._draw_keyboard()

    def set_pressed_note(self, note):
        # 兼容旧调用：默认当成右手
        self.set_pressed_notes(None, note)

    def set_arm_note(self, note):
        # 兼容旧调用：默认当成右手
        self.set_arm_notes(None, note)

    def set_status(self, status_text: str):
        self.status_text = str(status_text)
        self._update_header()

    def set_move_info(self, move_time=None, reached=None):
        self.current_move_time = move_time
        self.current_move_reached = reached
        self._update_header()

    def set_note_move_time(self, idx: int, move_time: float):
        return

    def set_note_actual_press(self, idx: int, elapsed: float):
        return

    def set_note_actual_release(self, idx: int, elapsed: float):
        return

    def reset_view(self):
        self.play_time = 0.0
        self.current_note = None
        self.pressed_left_note = None
        self.pressed_right_note = None
        self.arm_left_note = None
        self.arm_right_note = None
        self.current_move_time = None
        self.current_move_reached = None
        self.status_text = "Stopped"

        self._update_header()
        self._draw_keyboard()
        self._update_playhead()

        # 回到歌曲开头（底部）
        self.after_idle(lambda: self.roll_canvas.yview_moveto(1.0))

    # =========================================================
    # Event / redraw
    # =========================================================
    def _on_configure(self, _event=None):
        self._request_redraw()

    def _request_redraw(self):
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after_idle(self._do_redraw)

    def _do_redraw(self):
        self._redraw_pending = False

        old_y = self.roll_canvas.yview()[0] if self.roll_canvas.yview() else 1.0

        self._update_header()
        self._draw_roll_scene()
        self._draw_hit_line()
        self._draw_keyboard()

        if self._scroll_to_start_on_rebuild:
            self.roll_canvas.yview_moveto(1.0)
            self._scroll_to_start_on_rebuild = False
        else:
            try:
                self.roll_canvas.yview_moveto(old_y)
            except Exception:
                pass

        self._update_playhead()

    # =========================================================
    # Input normalization
    # =========================================================
    def _clean_note(self, note):
        if not note:
            return None
        n = normalize_note_name(note)
        if n in ("", "REST"):
            return None
        return n

    def _normalize_song_input(self, song):
        blocks = []
        total_duration = 0.0

        # full dual-hand score dict
        if isinstance(song, dict):
            for hand in ("left", "right"):
                t = 0.0
                for item in song.get(hand, []):
                    notes, dur = self._coerce_event(item)
                    d = max(0.01, dur)
                    for note in notes:
                        if note in self.key_notes:
                            blocks.append({
                                "hand": hand,
                                "note": note,
                                "start": t,
                                "end": t + d,
                            })
                    t += d
                total_duration = max(total_duration, t)

            return blocks, max(0.001, total_duration)

        # old one-line format fallback
        t = 0.0
        for item in (song or []):
            notes, dur = self._coerce_event(item)
            d = max(0.01, dur)
            for note in notes:
                if note in self.key_notes:
                    blocks.append({
                        "hand": "right",
                        "note": note,
                        "start": t,
                        "end": t + d,
                    })
            t += d

        return blocks, max(0.001, t)

    def _coerce_event(self, item):
        # planner event: {"notes": [...], "duration": ...}
        if isinstance(item, dict):
            notes = self._coerce_notes(item.get("notes", []))
            try:
                duration = max(0.01, float(item.get("duration", 0.01)))
            except Exception:
                duration = 0.01
            return notes, duration

        # raw score event: ([notes], dur) or ("C4", dur)
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            return [], 0.01

        notes_obj, dur = item[0], item[1]

        try:
            duration = max(0.01, float(dur))
        except Exception:
            duration = 0.01

        notes = self._coerce_notes(notes_obj)
        return notes, duration

    def _coerce_notes(self, notes_obj):
        out = []

        if isinstance(notes_obj, str):
            s = normalize_note_name(notes_obj)
            if s in ("", "REST"):
                return []
            parts = [normalize_note_name(x) for x in s.split("/") if normalize_note_name(x) not in ("", "REST")]
            for n in parts:
                if n not in out:
                    out.append(n)
            return out

        for x in (notes_obj or []):
            n = normalize_note_name(x)
            if n in ("", "REST"):
                continue
            if n not in out:
                out.append(n)

        return out

    # =========================================================
    # Mouse wheel
    # =========================================================
    def _on_mousewheel_windows(self, event):
        if event.delta == 0:
            return
        step = -1 * int(event.delta / 120)
        self.roll_canvas.yview_scroll(step * 3, "units")

    def _on_mousewheel_linux_up(self, _event):
        self.roll_canvas.yview_scroll(-3, "units")

    def _on_mousewheel_linux_down(self, _event):
        self.roll_canvas.yview_scroll(3, "units")

    # =========================================================
    # Geometry helpers
    # =========================================================
    def _content_width(self):
        return max(500, self.roll_canvas.winfo_width())

    def _white_key_w(self):
        white_count = max(1, len(self.white_notes))
        return (self._content_width() - 2 * self.padding_x) / white_count

    def _black_key_w(self):
        return self._white_key_w() * 0.64

    def _x_for_note(self, note_name: str):
        note_name = normalize_note_name(note_name)
        if note_name not in self.key_notes:
            return None

        white_w = self._white_key_w()

        if not is_black(note_name):
            idx = self.white_index_map[note_name]
            x0 = self.padding_x + idx * white_w
            return x0, x0 + white_w

        midi_num = name_to_midi(note_name)
        prev_note = midi_to_name(midi_num - 1)
        next_note = midi_to_name(midi_num + 1)

        if prev_note not in self.white_index_map or next_note not in self.white_index_map:
            return None

        prev_right = self.padding_x + (self.white_index_map[prev_note] + 1) * white_w
        next_left = self.padding_x + self.white_index_map[next_note] * white_w
        center = (prev_right + next_left) / 2
        half_w = self._black_key_w() / 2
        return center - half_w, center + half_w

    def _visible_roll_h(self):
        return max(220, self.roll_canvas.winfo_height())

    def _time_zero_y(self):
        return self._content_h - self.bottom_time_pad

    def _y_for_time(self, t: float):
        return self._time_zero_y() - float(t) * self.px_per_second

    def _format_time(self, t: float) -> str:
        t = max(0.0, float(t))
        m = int(t // 60)
        s = t - 60 * m
        return f"{m:02d}:{s:05.2f}"

    # =========================================================
    # Draw header / hit / keyboard / roll
    # =========================================================
    def _update_header(self):
        c = self.header_canvas
        c.delete("all")

        w = max(300, c.winfo_width())
        h = max(36, c.winfo_height())

        c.create_rectangle(0, 0, w, h, fill=self.BG, outline="")

        left_text = self.song_name if self.song_name else "No Song Loaded"
        time_text = f"{self._format_time(self.play_time)} / {self._format_time(self.song_total_duration)}"

        c.create_text(
            14, 14,
            text=left_text,
            anchor="nw",
            fill=self.TEXT,
            font=("Segoe UI", 14, "bold")
        )

        c.create_text(
            w - 14, 14,
            text=self.status_text,
            anchor="ne",
            fill=self.SUBTEXT,
            font=("Segoe UI", 11, "bold")
        )

        c.create_text(
            14, 34,
            text=time_text,
            anchor="nw",
            fill=self.SUBTEXT,
            font=("Consolas", 10)
        )

        if self.current_move_time is not None:
            reached_txt = "OK" if self.current_move_reached else "WAIT/TIMEOUT"
            reached_color = "#89f0dd" if self.current_move_reached else self.WARN

            c.create_text(
                150, 34,
                text=f"Move: {self.current_move_time:.3f}s",
                anchor="nw",
                fill=self.SUBTEXT,
                font=("Consolas", 10, "bold")
            )
            c.create_text(
                290, 34,
                text=f"[{reached_txt}]",
                anchor="nw",
                fill=reached_color,
                font=("Consolas", 10, "bold")
            )

    def _draw_hit_line(self):
        c = self.hit_canvas
        c.delete("all")

        w = max(300, c.winfo_width())
        h = max(4, c.winfo_height())

        c.create_rectangle(0, 0, w, h, fill=self.BG, outline="")
        # 只保留一个很淡的分隔线，不再画发光线
        c.create_line(
            self.padding_x, h - 1,
            w - self.padding_x, h - 1,
            fill="#163041",
            width=1
        )

    def _draw_roll_scene(self):
        c = self.roll_canvas
        c.delete("all")

        w = self._content_width()
        visible_h = self._visible_roll_h()

        self._content_h = max(
            visible_h,
            int(self.song_total_duration * self.px_per_second) + self.top_time_pad + self.bottom_time_pad
        )

        c.configure(scrollregion=(0, 0, w, self._content_h))
        c.create_rectangle(0, 0, w, self._content_h, fill=self.BG, outline="")

        self._draw_roll_background()
        self._draw_roll_notes()

        self._playhead_glow_id = c.create_line(
            self.padding_x, 0,
            w - self.padding_x, 0,
            fill=self.PLAYHEAD_GLOW,
            width=4
        )
        self._playhead_id = c.create_line(
            self.padding_x, 0,
            w - self.padding_x, 0,
            fill=self.PLAYHEAD,
            width=2
        )

    def _draw_roll_background(self):
        c = self.roll_canvas
        w = self._content_width()

        # 每个键一条对应轨道：白键淡一点，黑键深一点
        for note in self.key_notes:
            pos = self._x_for_note(note)
            if pos is None:
                continue

            x0, x1 = pos

            if is_black(note):
                fill = "#081622"
                edge = "#10283b"
            else:
                fill = "#050d16"
                edge = "#0b1d2c"

            c.create_rectangle(
                x0, 0, x1, self._content_h,
                fill=fill,
                outline=edge,
                width=1
            )

        # 白键主分隔线
        white_w = self._white_key_w()
        for i in range(len(self.white_notes) + 1):
            x = self.padding_x + i * white_w
            c.create_line(x, 0, x, self._content_h, fill="#17314a", width=1)

        # 时间横线
        sec = 0
        while sec <= self.song_total_duration + 0.001:
            y = self._y_for_time(sec)
            c.create_line(
                self.padding_x, y,
                w - self.padding_x, y,
                fill=self.GRID_TIME,
                width=1
            )
            c.create_text(
                6, y,
                text=f"{int(sec)}s",
                anchor="w",
                fill=self.SUBTEXT,
                font=("Consolas", 9)
            )
            sec += 1

    def _draw_roll_notes(self):
        c = self.roll_canvas

        for block in self.song_blocks:
            note = block["note"]
            pos = self._x_for_note(note)
            if pos is None:
                continue

            x0, x1 = pos
            y0 = self._y_for_time(block["end"])
            y1 = self._y_for_time(block["start"])

            if y1 - y0 < 4:
                y0 = y1 - 4

            if is_black(note):
                margin_x = 2
                fill = "#5ad7cb" if block["hand"] == "right" else "#6f9df0"
                outline = "#c8fff9" if block["hand"] == "right" else "#dbe7ff"
            else:
                margin_x = 4
                fill = "#69dfd4" if block["hand"] == "right" else "#7eabf7"
                outline = "#d8fffb" if block["hand"] == "right" else "#e2ecff"

            c.create_rectangle(
                x0 + margin_x, y0,
                x1 - margin_x, y1,
                fill=fill,
                outline=outline,
                width=1
            )

    def _draw_keyboard(self):
        c = self.keyboard_canvas
        c.delete("all")

        w = max(500, c.winfo_width())
        h = max(120, c.winfo_height())
        top = 0
        bottom = h

        c.create_rectangle(0, 0, w, h, fill=self.BG, outline="")
        c.create_rectangle(self.padding_x, top, w - self.padding_x, bottom, fill=self.KEYBED, outline="")
        c.create_rectangle(self.padding_x, bottom - 12, w - self.padding_x, bottom, fill=self.KEY_SHADOW, outline="")

        white_h = h
        black_h = int(h * 0.60)

        # white keys
        for note in self.key_notes:
            if is_black(note):
                continue

            pos = self._x_for_note(note)
            if pos is None:
                continue
            x0, x1 = pos

            fill = self.KEY_WHITE
            outline = self.KEY_BORDER
            width = 1

            left_pressed = (note == self.pressed_left_note)
            right_pressed = (note == self.pressed_right_note)
            is_current = (note == self.current_note)

            left_arm = (note == self.arm_left_note)
            right_arm = (note == self.arm_right_note)

            if left_pressed and right_pressed:
                fill = "#cfe8ff"
            elif left_pressed:
                fill = self.LEFT_KEY_ACTIVE
            elif right_pressed:
                fill = self.RIGHT_KEY_ACTIVE
            elif is_current:
                fill = self.KEY_ACTIVE_SOFT

            # arm 高光：保持淡淡的 center glow
            if left_arm and right_arm:
                outline = "#d8f3ff"
                width = 3
            elif left_arm:
                outline = self.LEFT_KEY_ACTIVE
                width = 3
            elif right_arm:
                outline = self.RIGHT_KEY_ACTIVE
                width = 3

            c.create_rectangle(x0, top, x1, white_h, fill=fill, outline=outline, width=width)
            c.create_line(x0, top, x1, top, fill="#ffffff", width=2)
            c.create_line(x0, white_h - 1, x1, white_h - 1, fill="#bdc6d2", width=1)

            # center 位置的淡高光，持续显示
            if left_arm:
                self.keyboard_canvas.create_rectangle(
                    x0 + 2, top + 2, x1 - 2, top + 12,
                    fill=self.LEFT_ARM_SOFT,
                    outline=""
                )

            if right_arm:
                self.keyboard_canvas.create_rectangle(
                    x0 + 2, top + 12, x1 - 2, top + 22,
                    fill=self.RIGHT_ARM_SOFT,
                    outline=""
                )

            # 只给白键标字
            if len(note) == 2:
                c.create_text(
                    (x0 + x1) / 2,
                    white_h - 18,
                    text=note,
                    fill="#444",
                    font=("Segoe UI", 9)
                )

                # black keys
        for note in self.key_notes:
            if not is_black(note):
                continue

            pos = self._x_for_note(note)
            if pos is None:
                continue
            x0, x1 = pos

            fill = self.KEY_BLACK
            outline = "#000000"
            width = 1

            left_pressed = (note == self.pressed_left_note)
            right_pressed = (note == self.pressed_right_note)
            is_current = (note == self.current_note)

            left_arm = (note == self.arm_left_note)
            right_arm = (note == self.arm_right_note)

            if left_pressed and right_pressed:
                fill = "#b8d4ff"
                outline = "#dce8ff"
            elif left_pressed:
                fill = self.LEFT_KEY_ACTIVE
                outline = self.LEFT_KEY_ACTIVE
            elif right_pressed:
                fill = self.RIGHT_KEY_ACTIVE
                outline = self.RIGHT_KEY_ACTIVE
            elif is_current:
                fill = "#3bcaba"
                outline = "#3bcaba"

            if left_arm and right_arm:
                outline = "#d8f3ff"
                width = 3
            elif left_arm:
                outline = self.LEFT_KEY_ACTIVE
                width = 3
            elif right_arm:
                outline = self.RIGHT_KEY_ACTIVE
                width = 3

            c.create_rectangle(
                x0, top,
                x1, top + black_h,
                fill=fill,
                outline=outline,
                width=width
            )

            # center 的淡淡高光
            if left_arm:
                c.create_rectangle(
                    x0 + 2, top + 2, x1 - 2, top + 7,
                    fill=self.LEFT_ARM_SOFT,
                    outline=""
                )

            if right_arm:
                c.create_rectangle(
                    x0 + 2, top + 7, x1 - 2, top + 12,
                    fill=self.RIGHT_ARM_SOFT,
                    outline=""
                )

            # 顶部 gloss / 按下高光
            if left_pressed or right_pressed or is_current:
                gloss = "#cffff9"
                if left_pressed and not right_pressed:
                    gloss = "#e7efff"
            else:
                gloss = self.BLACK_KEY_GLOSS

            c.create_rectangle(
                x0 + 2, top + 2, x1 - 2, top + 10,
                fill=gloss,
                outline=""
            )

    # =========================================================
    # Playhead
    # =========================================================
    def _update_playhead(self):
        if self._playhead_id is None or self._playhead_glow_id is None:
            return

        y = self._y_for_time(self.play_time)
        x0 = self.padding_x
        x1 = self._content_width() - self.padding_x

        self.roll_canvas.coords(self._playhead_glow_id, x0, y, x1, y)
        self.roll_canvas.coords(self._playhead_id, x0, y, x1, y)

        if self.follow_playhead:
            self._ensure_y_visible(y)

    def _ensure_y_visible(self, y):
        c = self.roll_canvas
        visible_top = c.canvasy(0)
        visible_bottom = c.canvasy(c.winfo_height())
        view_h = max(1.0, visible_bottom - visible_top)
        total_scroll_h = max(1.0, self._content_h - view_h)

        pad = 80

        target_top = None
        if y < visible_top + pad:
            target_top = max(0.0, y - pad)
        elif y > visible_bottom - pad:
            target_top = min(total_scroll_h, y - view_h + pad)

        if target_top is not None:
            c.yview_moveto(target_top / total_scroll_h if total_scroll_h > 1e-9 else 0.0)