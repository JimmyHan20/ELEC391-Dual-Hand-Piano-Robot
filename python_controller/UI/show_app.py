import os
import sys
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog



CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    
    
from Song.midi_loader import load_score_from_midi
from Core_function.key_tools import (
    detect_key,
    get_viable_target_keys,
    transpose_score,
    semitone_delta,
)

from Communication.uart import SerialReader, VOFAListener, list_serial_ports
from Core_function.note import angle_to_note, note_to_angle
from Core_function.global_event_planner import build_global_timeline
from Core_function.hand_planner import (
    HandPlanner,
    PlannerConfig,
    sanitize_score_for_robot,
)
from Core_function.dual_hand_conductor import DualHandConductor, HandTransport
from Core_function.hand_planner_dual_path import (
    plan_robot_score_dual_path,
    default_dual_path_config,
)
from Song.song import SONGS
from UI.piano_roll import PianoRollView

import random


UI_POLL_MS = 10
DEFAULT_PORT_LEFT = "COM5"
DEFAULT_PORT_RIGHT = "COM6"
DEFAULT_BAUD = 115200


class ShowApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Automatic Piano Player - Dual Hand UI")
        self.geometry("1280x760")
        self.minsize(1080, 640)
        self.configure(bg="#0b0f14")

        # ---------------------------
        # General queues / events
        # ---------------------------
        self.q = queue.Queue()                 # app-level events from workers / conductor
        self.serial_q_left = queue.Queue()     # raw serial events from left reader
        self.serial_q_right = queue.Queue()    # raw serial events from right reader

        self.stop_evt_left = threading.Event()
        self.stop_evt_right = threading.Event()

        self._seq_stop_evt = threading.Event()
        self._seq_pause_evt = threading.Event()

        # ---------------------------
        # Readers / workers / conductor
        # ---------------------------
        self.reader_left = None
        self.reader_right = None

        self.worker_left = None
        self.worker_right = None
        self.conductor = None

        # optional VOFA listener:
        # current VOFAListener seems to keep only one reader_ref,
        # so here we keep it alive but bind it to RIGHT by default.
        self.vofa_stop_evt = threading.Event()
        self.vofa_listener = VOFAListener(self.q, self.vofa_stop_evt)
        self.vofa_listener.start()

        # ---------------------------
        # Connection state
        # ---------------------------
        self.left_connected = False
        self.right_connected = False

        # ---------------------------
        # Left telemetry cache
        # ---------------------------
        self.left_desired_center = None
        self.left_actual_center = None
        self.left_dir_center = 0

        self.left_desired_spread = None
        self.left_actual_spread = None
        self.left_dir_spread = 0

        self.left_solenoid = 0
        self.left_homing = 0

        # ---------------------------
        # Right telemetry cache
        # ---------------------------
        self.right_desired_center = None
        self.right_actual_center = None
        self.right_dir_center = 0

        self.right_desired_spread = None
        self.right_actual_spread = None
        self.right_dir_spread = 0

        self.right_solenoid = 0
        self.right_homing = 0

        # ---------------------------
        # Song / UI state
        # ---------------------------
        self._current_song_name = list(SONGS.keys())[0] if SONGS else "(empty)"
        self._current_score = {"left": [], "right": []}
        self._current_planned = {"left": [], "right": []}

        self._current_song_mode = "—"

        self._play_state = "Stopped"
        self._current_note_name = "—"
        self._current_time_s = 0.0
        self._song_total_s = 1.0
        self._planned_total_s = 1.0

        self._pressed_note = None
        self._right_panel_mode = "state"
        self._live_log_max_lines = 220

        self._latest_seq_note_left = []
        self._latest_seq_note_right = []
        self._latest_fingers_left = []
        self._latest_fingers_right = []

        # ---------------------------
        # Planner / table debug state
        # ---------------------------
        self._scaled_score_cache = {"left": [], "right": []}
        self._cleaned_score_cache = {"left": [], "right": []}

        self._event_trees = {}
        self._planned_event_row = {"left": {}, "right": {}}
        self._active_table_row = {"left": None, "right": None}


        self._right_panel_mode = "state"
        self._left_panel_mode = "piano"
        # ---------------------------
        # Planner config
        # ---------------------------
        self._right_cfg = PlannerConfig(
            note_min="C4",
            note_max="F6",
            start_center_note=None,
            start_spread=0,
            spread_distances=(2, 3, 4),
            center_note_min="F4",
            center_note_max="D6",
            enable_left_black_finger=True,
            enable_right_black_finger=True,
        )

        self._left_cfg = PlannerConfig(
            note_min="C2",
            note_max="B3",
            start_center_note=None,
            start_spread=0,
            spread_distances=(2, 3, 4),
            center_note_min="C2",
            center_note_max="E3",
            enable_left_black_finger=True,
            enable_right_black_finger=True,
        )

        self._right_planner = HandPlanner(self._right_cfg)
        self._left_planner = HandPlanner(self._left_cfg)
        self._dual_cfg = default_dual_path_config()

        self._build_ui()
        self._seq_load_song()

        self.after(UI_POLL_MS, self._poll_all_queues)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.loaded_midi_path = None
        self.loaded_raw_score = None
        self.detected_key_info = None
        self.current_target_key = "Original"

        self.key_popup_auto_open = tk.BooleanVar(value=True)

        self.raw_score_original = None          # MIDI load 后的原始 score
        self.detected_key_info = None           # 识别出来的 key
        self.current_target_tonic = None        # 当前目标调 tonic，比如 "C#" / "D"
        self.current_mode = None                # "major" / "minor"

        self.current_transpose_semitones = 0    # 当前转调半音数
        self.shifted_score_current = None       # 转调后的 raw_score（仅缓存）
        self.planned_song_right = None          # DP 后右手
        self.planned_song_left = None            # DP 后左手

    # ============================================================
    # UI
    # ============================================================
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        root = tk.Frame(self, bg="#0b0f14")
        root.pack(fill=tk.BOTH, expand=True)

        top = tk.Frame(root, bg="#0b0f14")
        top.pack(side=tk.TOP, fill=tk.X, padx=14, pady=(12, 8))

        # ---------------------------
        # Connection box
        # ---------------------------
        conn_box = tk.Frame(top, bg="#11161e")
        conn_box.pack(side=tk.LEFT, padx=(0, 12), ipadx=8, ipady=8)

        self.conn_dot = tk.Canvas(conn_box, width=18, height=18, bg="#11161e", highlightthickness=0)
        self.conn_dot.grid(row=0, column=0, rowspan=2, padx=(4, 6))
        self._draw_conn_dot("#ff5f56")

        self.conn_text_var = tk.StringVar(value="Disconnected")
        tk.Label(
            conn_box,
            textvariable=self.conn_text_var,
            fg="#dfe7f2",
            bg="#11161e",
            font=("Segoe UI", 11, "bold")
        ).grid(row=0, column=1, sticky="w", padx=(0, 10))

        tk.Label(conn_box, text="Left Port", fg="#9eb0c6", bg="#11161e", font=("Segoe UI", 10)).grid(row=0, column=2, sticky="w")
        self.port_var_left = tk.StringVar(value=DEFAULT_PORT_LEFT)
        self.port_combo_left = ttk.Combobox(conn_box, textvariable=self.port_var_left, values=list_serial_ports(), width=10)
        self.port_combo_left.grid(row=0, column=3, padx=(6, 10))

        tk.Label(conn_box, text="Right Port", fg="#9eb0c6", bg="#11161e", font=("Segoe UI", 10)).grid(row=0, column=4, sticky="w")
        self.port_var_right = tk.StringVar(value=DEFAULT_PORT_RIGHT)
        self.port_combo_right = ttk.Combobox(conn_box, textvariable=self.port_var_right, values=list_serial_ports(), width=10)
        self.port_combo_right.grid(row=0, column=5, padx=(6, 10))

        tk.Label(conn_box, text="Baud", fg="#9eb0c6", bg="#11161e", font=("Segoe UI", 10)).grid(row=1, column=2, sticky="w")
        self.baud_var = tk.IntVar(value=DEFAULT_BAUD)
        self.baud_entry = ttk.Entry(conn_box, textvariable=self.baud_var, width=12)
        self.baud_entry.grid(row=1, column=3, padx=(6, 10), sticky="w")

        ttk.Button(conn_box, text="Refresh", command=self._refresh_ports).grid(row=1, column=4, padx=4, sticky="w")
        self.btn_connect = ttk.Button(conn_box, text="Connect Both", command=self._connect)
        self.btn_connect.grid(row=1, column=5, padx=4, sticky="w")
        self.btn_disconnect = ttk.Button(conn_box, text="Disconnect Both", command=self._disconnect, state=tk.DISABLED)
        self.btn_disconnect.grid(row=1, column=6, padx=4, sticky="w")

        # ---------------------------
        # Song box
        # ---------------------------
        song_box = tk.Frame(top, bg="#11161e")
        song_box.pack(side=tk.LEFT, padx=(0, 12), ipadx=8, ipady=8)

        tk.Label(song_box, text="Song", fg="#9eb0c6", bg="#11161e", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.song_var = tk.StringVar(value=self._current_song_name)
        self.song_menu = ttk.Combobox(
            song_box,
            textvariable=self.song_var,
            values=list(SONGS.keys()),
            state="readonly",
            width=20
        )
        self.song_menu.pack(side=tk.LEFT, padx=(6, 4))
        self.song_menu.bind("<<ComboboxSelected>>", lambda e: self._seq_load_song())

        self.btn_song_search = ttk.Button(song_box, text="🎲", width=3, command=self._load_random_demo_song)
        self.btn_song_search.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(song_box, text="Tempo", fg="#9eb0c6", bg="#11161e", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.tempo_var = tk.DoubleVar(value=1.0)
        self.tempo_scale = ttk.Scale(
            song_box,
            variable=self.tempo_var,
            from_=0.25,
            to=3.0,
            orient=tk.HORIZONTAL,
            length=110,
            command=self._on_tempo_change
        )
        self.tempo_scale.pack(side=tk.LEFT, padx=(6, 8))

        self.tempo_lbl = tk.Label(song_box, text="1.00×", fg="#dfe7f2", bg="#11161e", font=("Consolas", 10))
        self.tempo_lbl.pack(side=tk.LEFT)

        # ---------------------------
        # Playback box
        # ---------------------------
        playback_box = tk.Frame(top, bg="#11161e")
        playback_box.pack(side=tk.LEFT, padx=(0, 12), ipadx=8, ipady=8)

        self.btn_play = ttk.Button(playback_box, text="Play", command=self._seq_play, width=10)
        self.btn_play.pack(side=tk.LEFT, padx=4)

        self.btn_pause_resume = ttk.Button(
            playback_box,
            text="Pause",
            command=self._seq_toggle_pause,
            width=10,
            state=tk.DISABLED
        )
        self.btn_pause_resume.pack(side=tk.LEFT, padx=4)

        self.btn_reset = ttk.Button(playback_box, text="Reset", command=self._seq_reset, width=10)
        self.btn_reset.pack(side=tk.LEFT, padx=4)

        # ---------------------------
        # Header right info
        # ---------------------------
        right_box = tk.Frame(top, bg="#11161e")
        right_box.pack(side=tk.RIGHT, ipadx=10, ipady=8)

        self.status_var = tk.StringVar(value="Stopped")
        self.now_song_var = tk.StringVar(value="Song: —")
        self.now_mode_var = tk.StringVar(value="Key: —")
        self.now_note_var = tk.StringVar(value="Note: —")
        self.now_time_var = tk.StringVar(value="00:00.00 / 00:00.00")

        tk.Label(right_box, textvariable=self.status_var, fg="#89f0dd", bg="#11161e", font=("Segoe UI", 12, "bold")).pack(anchor="e")
        tk.Label(right_box, textvariable=self.now_song_var, fg="#dfe7f2", bg="#11161e", font=("Segoe UI", 10)).pack(anchor="e")
        tk.Label(right_box, textvariable=self.now_note_var, fg="#dfe7f2", bg="#11161e", font=("Segoe UI", 10)).pack(anchor="e")
        tk.Label(right_box, textvariable=self.now_time_var, fg="#9eb0c6", bg="#11161e", font=("Consolas", 10)).pack(anchor="e")
        tk.Label(right_box, textvariable=self.now_mode_var, fg="#dfe7f2", bg="#11161e", font=("Segoe UI", 10)).pack(anchor="e")
        # ---------------------------
        # Progress
        # ---------------------------
        progress_wrap = tk.Frame(root, bg="#0b0f14")
        progress_wrap.pack(fill=tk.X, padx=14, pady=(0, 8))

        self.progress = ttk.Progressbar(progress_wrap, mode="determinate")
        self.progress.pack(fill=tk.X, expand=True)

        # ---------------------------
        # Main content
        # ---------------------------
        content = tk.Frame(root, bg="#0b0f14")
        content.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))

        left = tk.Frame(content, bg="#0b0f14")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ---------------------------
        # Left page toggle bar
        # ---------------------------
        left_toggle_bar = tk.Frame(left, bg="#0b0f14")
        left_toggle_bar.pack(fill=tk.X, pady=(0, 8))

        self.btn_table_page = tk.Button(
            left_toggle_bar,
            text="Planner Table",
            command=self._show_table_page,
            bg="#11161e",
            fg="#9eb0c6",
            activebackground="#253244",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
            cursor="hand2"
        )
        self.btn_table_page.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_piano_page = tk.Button(
            left_toggle_bar,
            text="Piano View",
            command=self._show_piano_page,
            bg="#1b2430",
            fg="#dfe7f2",
            activebackground="#253244",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
            cursor="hand2"
        )
        self.btn_piano_page.pack(side=tk.LEFT)

        # ---------------------------
        # Left page host
        # ---------------------------
        self.left_panel_host = tk.Frame(left, bg="#0b0f14")
        self.left_panel_host.pack(fill=tk.BOTH, expand=True)

        # Page 1: planner tables
        self.table_page = tk.Frame(self.left_panel_host, bg="#0b0f14")
        self.table_page.place(relx=0, rely=0, relwidth=1, relheight=1)

        tables_wrap = tk.Frame(self.table_page, bg="#0b0f14")
        tables_wrap.pack(fill=tk.BOTH, expand=True)

        left_box, left_tree = self._build_hand_event_table(tables_wrap, "LEFT HAND")
        left_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        right_box, right_tree = self._build_hand_event_table(tables_wrap, "RIGHT HAND")
        right_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        self._event_trees["left"] = left_tree
        self._event_trees["right"] = right_tree

        # Page 2: piano
        self.piano_page = tk.Frame(self.left_panel_host, bg="#0b0f14")
        self.piano_page.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.piano_view = PianoRollView(self.piano_page, start_note="C2", end_note="F6")
        self.piano_view.pack(fill=tk.BOTH, expand=True)

        # default page
        self._show_piano_page()

        right = tk.Frame(content, bg="#11161e", width=420)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        right.pack_propagate(False)

        toggle_bar = tk.Frame(right, bg="#11161e")
        toggle_bar.pack(fill=tk.X, padx=14, pady=(16, 8))

        self.btn_state_panel = tk.Button(
            toggle_bar,
            text="Machine State",
            command=self._show_state_panel,
            bg="#1b2430",
            fg="#dfe7f2",
            activebackground="#253244",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
            cursor="hand2"
        )
        self.btn_state_panel.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_log_panel = tk.Button(
            toggle_bar,
            text="Live Log",
            command=self._show_log_panel,
            bg="#11161e",
            fg="#9eb0c6",
            activebackground="#253244",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
            cursor="hand2"
        )
        self.btn_log_panel.pack(side=tk.LEFT)

        self.top_panel_host = tk.Frame(right, bg="#11161e", height=260)
        self.top_panel_host.pack(fill=tk.X, padx=14, pady=(0, 10))
        self.top_panel_host.pack_propagate(False)

        # State panel
        self.state_panel = tk.Frame(self.top_panel_host, bg="#11161e")
        self.state_panel.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.machine_vars = {}
        fields = [
            ("Left Arm", "—"),
            ("Left Spread", "—"),
            ("Right Arm", "—"),
            ("Right Spread", "—"),
            ("Notes L", "—"),
            ("Fingers L", "—"),
            ("Notes R", "—"),
            ("Fingers R", "—"),
        ]
        for key, default in fields:
            row = tk.Frame(self.state_panel, bg="#11161e")
            row.pack(fill=tk.X, pady=3)

            tk.Label(
                row,
                text=key,
                width=14,
                anchor="w",
                bg="#11161e",
                fg="#9eb0c6",
                font=("Segoe UI", 10)
            ).pack(side=tk.LEFT)

            var = tk.StringVar(value=default)
            self.machine_vars[key] = var
            tk.Label(
                row,
                textvariable=var,
                anchor="e",
                bg="#11161e",
                fg="#dfe7f2",
                font=("Consolas", 10, "bold")
            ).pack(side=tk.RIGHT)

        # Log panel
        self.log_panel = tk.Frame(self.top_panel_host, bg="#11161e")
        self.log_panel.place(relx=0, rely=0, relwidth=1, relheight=1)

        tk.Label(
            self.log_panel,
            text="Live Log",
            bg="#11161e",
            fg="#dfe7f2",
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", pady=(0, 12))

        log_box_frame = tk.Frame(self.log_panel, bg="#11161e")
        log_box_frame.pack(fill=tk.BOTH, expand=True)

        self.live_log = tk.Text(
            log_box_frame,
            bg="#0c1016",
            fg="#dfe7f2",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            wrap=tk.WORD,
            font=("Consolas", 9),
            height=14
        )
        self.live_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        live_log_scroll = ttk.Scrollbar(log_box_frame, orient="vertical", command=self.live_log.yview)
        live_log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.live_log.configure(yscrollcommand=live_log_scroll.set)

        self._show_state_panel()

        # ---------------------------
        # Editor
        # ---------------------------
        editor_frame = tk.Frame(right, bg="#11161e")
        editor_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 16))

        tk.Label(
            editor_frame,
            text="Custom Song Editor",
            bg="#11161e",
            fg="#dfe7f2",
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")

        tk.Label(
            editor_frame,
            text=(
                "Default hand = RIGHT\n"
                "Formats:\n"
                "R C4/E4 0.5\n"
                "L C3 0.5\n"
                "R REST 0.2"
            ),
            justify=tk.LEFT,
            bg="#11161e",
            fg="#9eb0c6",
            font=("Consolas", 9)
        ).pack(anchor="w", pady=(6, 8))

        self.custom_text = tk.Text(
            editor_frame,
            height=8,
            bg="#0c1016",
            fg="#dfe7f2",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            font=("Consolas", 10)
        )
        self.custom_text.pack(fill=tk.BOTH, expand=True)

        btns = tk.Frame(editor_frame, bg="#11161e")
        btns.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(btns, text="Load Custom", command=self._seq_load_custom).pack(side=tk.LEFT)
        ttk.Button(btns, text="Load MIDI", command=self._load_midi_file).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="Clear", command=lambda: self.custom_text.delete("1.0", tk.END)).pack(side=tk.LEFT)


    
    def _build_musical_demo_score(self, seed=None):
        rng = random.Random(seed if seed is not None else time.time())

        mode = rng.choice(["major", "minor"])

        if mode == "major":
            key_label = "C major"

            right_pool = ["E4", "G4", "A4", "B4", "C5", "D5", "E5", "G5"]
            left_roots = ["C3", "G2", "A2", "F2", "C3", "G2", "D3", "G2"]

            motifs = [
                [("E4", 0.5), ("G4", 0.5), ("A4", 1.0)],
                [("G4", 0.5), ("A4", 0.5), ("C5", 1.0)],
                [("E5", 0.5), ("D5", 0.5), ("C5", 1.0)],
                [("A4", 0.5), ("G4", 0.5), ("E4", 1.0)],
                [("C5", 0.5), ("B4", 0.5), ("A4", 1.0)],
                [("G4", 0.5), ("E4", 0.5), ("D4", 1.0)],
            ]

            def build_left_bar(root):
                if root == "C3":
                    return [(["C3", "E3"], 1.0), (["G2", "C3"], 1.0)]
                if root == "G2":
                    return [(["G2", "D3"], 1.0), (["B2", "D3"], 1.0)]
                if root == "A2":
                    return [(["A2", "C3"], 1.0), (["E3"], 1.0)]
                if root == "F2":
                    return [(["F2", "A2"], 1.0), (["C3"], 1.0)]
                if root == "D3":
                    return [(["D3", "F3"], 1.0), (["A2"], 1.0)]
                return [([root], 1.0), ([], 1.0)]

            ending_left = [
                (["C3", "E3"], 1.0),
                (["G2", "C3"], 1.5),
            ]
            ending_right = [
                (["G4"], 0.5),
                (["E4"], 0.5),
                (["C4"], 1.5),
            ]

        else:
            key_label = "A minor"

            right_pool = ["E4", "G4", "A4", "B4", "C5", "D5", "E5", "G5"]
            left_roots = ["A2", "E2", "F2", "C3", "A2", "E2", "D3", "E2"]

            motifs = [
                [("A4", 0.5), ("C5", 0.5), ("E5", 1.0)],
                [("E5", 0.5), ("D5", 0.5), ("C5", 1.0)],
                [("A4", 0.5), ("G4", 0.5), ("E4", 1.0)],
                [("C5", 0.5), ("B4", 0.5), ("A4", 1.0)],
                [("E4", 0.5), ("G4", 0.5), ("A4", 1.0)],
                [("B4", 0.5), ("A4", 0.5), ("G4", 1.0)],
            ]

            def build_left_bar(root):
                if root == "A2":
                    return [(["A2", "C3"], 1.0), (["E3"], 1.0)]
                if root == "E2":
                    return [(["E2", "B2"], 1.0), (["G#2", "B2"], 1.0)]
                if root == "F2":
                    return [(["F2", "A2"], 1.0), (["C3"], 1.0)]
                if root == "C3":
                    return [(["C3", "E3"], 1.0), (["G2", "C3"], 1.0)]
                if root == "D3":
                    return [(["D3", "F3"], 1.0), (["A2"], 1.0)]
                return [([root], 1.0), ([], 1.0)]

            ending_left = [
                (["A2", "C3"], 1.0),
                (["E2", "A2"], 1.5),
            ]
            ending_right = [
                (["E4"], 0.5),
                (["C4"], 0.5),
                (["A3"], 1.5),
            ]

        score = {"left": [], "right": []}

        for _bar in range(8):
            root = left_roots[_bar % len(left_roots)]
            left_bar = build_left_bar(root)

            motif = list(rng.choice(motifs))

            if rng.random() < 0.35:
                idx = rng.randrange(len(motif))
                old_note, old_dur = motif[idx]
                for _ in range(6):
                    midi = self._note_to_midi_simple(old_note) + rng.choice([-2, -1, 1, 2])
                    cand = self._midi_to_note_simple(midi)
                    if cand in right_pool:
                        motif[idx] = (cand, old_dur)
                        break

            score["left"].extend(left_bar)
            score["right"].extend([([n], d) for n, d in motif])

            if rng.random() < 0.25:
                score["right"].append(([], 0.25))

        score["left"].extend(ending_left)
        score["right"].extend(ending_right)

        return score, key_label


    def _note_to_midi_simple(self, note: str) -> int:
        note = str(note).strip().upper()
        names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        if len(note) >= 3 and note[1] == '#':
            pitch = note[:2]
            octave = int(note[2:])
        else:
            pitch = note[:1]
            octave = int(note[1:])
        return names.index(pitch) + (octave + 1) * 12


    def _midi_to_note_simple(self, midi_num: int) -> str:
        names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
        pitch = names[midi_num % 12]
        octave = (midi_num // 12) - 1
        return f"{pitch}{octave}"


    def _load_random_demo_song(self):
        self.loaded_midi_path = None
        self.loaded_raw_score = None
        self.detected_key_info = None
        self.current_target_key = "Original"

        seed = int(time.time())
        score_dict, key_label = self._build_musical_demo_score(seed=seed)

        self._current_song_mode = key_label
        self._set_loaded_score(f"({key_label} demo {seed % 10000})", score_dict, mirror_to_editor=True)


    def _build_hand_event_table(self, master, title: str):
        box = tk.Frame(master, bg="#11161e", bd=0, highlightthickness=0)

        tk.Label(
            box,
            text=title,
            bg="#11161e",
            fg="#dfe7f2",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 6))

        tree_wrap = tk.Frame(box, bg="#11161e")
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        cols = ("idx", "ideal", "planner", "actual")
        tree = ttk.Treeview(
            tree_wrap,
            columns=cols,
            show="headings",
            height=7
        )

        tree.heading("idx", text="#")
        tree.heading("ideal", text="Ideal")
        tree.heading("planner", text="Planner")
        tree.heading("actual", text="Actual")

        tree.column("idx", width=40, anchor="center", stretch=False)
        tree.column("ideal", width=180, anchor="w", stretch=True)
        tree.column("planner", width=180, anchor="w", stretch=True)
        tree.column("actual", width=120, anchor="w", stretch=True)

        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tree.tag_configure("playing", background="#173b36", foreground="#dffcf6")
        tree.tag_configure("done", background="#11161e", foreground="#dfe7f2")
        tree.tag_configure("rest_row", background="#1a2330", foreground="#9fb0c5")

        return box, tree

    def _notes_text(self, notes):
        notes = [str(x).strip().upper() for x in (notes or []) if str(x).strip()]
        return "REST" if not notes else "/".join(notes)

    def _event_timeline_from_score(self, score_events):
        out = []
        t = 0.0
        for idx, item in enumerate(score_events or []):
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue

            notes_obj, dur = item[0], item[1]
            try:
                d = max(0.01, float(dur))
            except Exception:
                d = 0.01

            if isinstance(notes_obj, str):
                s = notes_obj.strip().upper()
                notes = [] if s in ("", "REST") else [x.strip().upper() for x in s.split("/") if x.strip()]
            else:
                notes = []
                for x in (notes_obj or []):
                    n = str(x).strip().upper()
                    if n and n != "REST" and n not in notes:
                        notes.append(n)

            out.append({
                "idx": idx,
                "start": t,
                "end": t + d,
                "mid": t + 0.5 * d,
                "notes": notes,
                "duration": d,
            })
            t += d
        return out

    def _event_timeline_from_planned(self, planned_events):
        out = []
        t = 0.0
        for idx, ev in enumerate(planned_events or []):
            try:
                d = max(0.01, float(ev.get("duration", 0.01)))
            except Exception:
                d = 0.01

            notes = [str(x).strip().upper() for x in ev.get("notes", []) if str(x).strip()]

            out.append({
                "idx": idx,
                "start": t,
                "end": t + d,
                "mid": t + 0.5 * d,
                "notes": notes,
                "duration": d,
                "event": ev,
            })
            t += d
        return out

    def _find_event_at_time(self, timeline, t: float):
        if not timeline:
            return None

        best = None
        best_key = None

        for ev in timeline:
            if not ev["notes"]:
                continue

            if ev["start"] <= t < ev["end"]:
                key = (0, abs(t - ev["start"]), ev["idx"])
            else:
                gap = min(abs(t - ev["start"]), abs(t - ev["end"]))
                key = (1, gap, ev["idx"])

            if best_key is None or key < best_key:
                best_key = key
                best = ev

        return best
    
    def _find_best_planned_match(self, raw_ev, planned_timeline):
        if not planned_timeline:
            return None

        mid = raw_ev["mid"]
        best = None
        best_key = None

        for ev in planned_timeline:
            overlap = min(raw_ev["end"], ev["end"]) - max(raw_ev["start"], ev["start"])
            overlap = max(0.0, overlap)

            if ev["start"] <= mid < ev["end"]:
                key = (0, -overlap, abs(mid - ev["mid"]), ev["idx"])
            else:
                if mid < ev["start"]:
                    gap = ev["start"] - mid
                else:
                    gap = mid - ev["end"]
                key = (1, gap, -overlap, ev["idx"])

            if best_key is None or key < best_key:
                best_key = key
                best = ev

        return best

    def _clear_event_table_actuals(self):
        for hand, tree in self._event_trees.items():
            for iid in tree.get_children():
                tags = tuple(tree.item(iid, "tags"))
                if "rest_row" in tags:
                    tree.set(iid, "actual", "REST")
                    tree.item(iid, tags=("rest_row",))
                else:
                    tree.set(iid, "actual", "—")
                    tree.item(iid, tags=())
        self._active_table_row = {"left": None, "right": None}

    def _reset_event_tables(self):
        for hand, tree in self._event_trees.items():
            for iid in tree.get_children():
                tree.delete(iid)
        self._planned_event_row = {"left": {}, "right": {}}
        self._active_table_row = {"left": None, "right": None}

    def _rebuild_event_tables(self, raw_score_dict, planned_score_dict):
        self._reset_event_tables()

        # 每只手：原始 MIDI 的非 REST 事件，按顺序取
        raw_press_seq = {
            "left": [
                ev for ev in self._event_timeline_from_score(raw_score_dict.get("left", []))
                if ev["notes"]
            ],
            "right": [
                ev for ev in self._event_timeline_from_score(raw_score_dict.get("right", []))
                if ev["notes"]
            ],
        }

        # 每只手：当前取到第几个 raw MIDI 事件
        raw_press_ptr = {"left": 0, "right": 0}

        global_timeline = build_global_timeline(
            planned_left=planned_score_dict.get("left", []),
            planned_right=planned_score_dict.get("right", []),
        )

        row_num = 1

        for slot in global_timeline.get("global_slots", []):
            left_press = next(
                (ev for ev in slot.get("left_events", []) if ev.get("event_type") == "PRESS"),
                None
            )
            right_press = next(
                (ev for ev in slot.get("right_events", []) if ev.get("event_type") == "PRESS"),
                None
            )

            # 没有任何 PRESS 的纯 release slot，不建行
            if not left_press and not right_press:
                continue

            for hand, press_ev in (("left", left_press), ("right", right_press)):
                tree = self._event_trees.get(hand)
                if tree is None:
                    continue

                iid = f"{hand}_{row_num}"

                # 这一全局拍该手没有按下 -> REST
                if press_ev is None:
                    tree.insert(
                        "",
                        tk.END,
                        iid=iid,
                        values=(row_num, "REST", "REST", "REST"),
                        tags=("rest_row",)
                    )
                    continue

                planner_notes = list(press_ev.get("notes", []))

                # Ideal：按该手 raw MIDI 顺序一一对应，不再按时间最近匹配
                ptr = raw_press_ptr[hand]
                if ptr < len(raw_press_seq[hand]):
                    ideal_notes = list(raw_press_seq[hand][ptr]["notes"])
                    raw_press_ptr[hand] += 1
                else:
                    ideal_notes = list(planner_notes)

                tree.insert(
                    "",
                    tk.END,
                    iid=iid,
                    values=(
                        row_num,
                        self._notes_text(ideal_notes),
                        self._notes_text(planner_notes),
                        "—",
                    )
                )

                try:
                    seg_idx = int(press_ev.get("segment_index"))
                    self._planned_event_row[hand][seg_idx] = iid
                except Exception:
                    pass

            row_num += 1

    def _mark_table_actual_press(self, hand: str, event_index: int, actual_notes):
        tree = self._event_trees.get(hand)
        if tree is None:
            return

        iid = self._planned_event_row.get(hand, {}).get(event_index)
        if not iid:
            return

        old_iid = self._active_table_row.get(hand)
        if old_iid and tree.exists(old_iid):
            tree.item(old_iid, tags=("done",))

        tree.set(iid, "actual", self._notes_text(actual_notes))
        tree.item(iid, tags=("playing",))
        tree.see(iid)

        self._active_table_row[hand] = iid

    def _mark_table_release(self, hand: str, event_index: int):
        tree = self._event_trees.get(hand)
        if tree is None:
            return

        iid = self._planned_event_row.get(hand, {}).get(event_index)
        if not iid:
            return

        tags = tuple(tree.item(iid, "tags"))
        if "rest_row" in tags:
            return

        tree.item(iid, tags=("done",))
        if self._active_table_row.get(hand) == iid:
            self._active_table_row[hand] = None
    # ============================================================
    # Small UI helpers
    # ============================================================
    def _draw_conn_dot(self, color):
        self.conn_dot.delete("all")
        self.conn_dot.create_oval(3, 3, 15, 15, fill=color, outline="")

    def _show_state_panel(self):
        self._right_panel_mode = "state"
        self.state_panel.lift()
        self.btn_state_panel.config(bg="#1b2430", fg="#dfe7f2")
        self.btn_log_panel.config(bg="#11161e", fg="#9eb0c6")

    def _show_log_panel(self):
        self._right_panel_mode = "log"
        self.log_panel.lift()
        self.btn_state_panel.config(bg="#11161e", fg="#9eb0c6")
        self.btn_log_panel.config(bg="#1b2430", fg="#dfe7f2")

    def _show_table_page(self):
        self._left_panel_mode = "table"
        self.table_page.lift()
        self.btn_table_page.config(bg="#1b2430", fg="#dfe7f2")
        self.btn_piano_page.config(bg="#11161e", fg="#9eb0c6")

    def _show_piano_page(self):
        self._left_panel_mode = "piano"
        self.piano_page.lift()
        self.btn_table_page.config(bg="#11161e", fg="#9eb0c6")
        self.btn_piano_page.config(bg="#1b2430", fg="#dfe7f2")

    def _append_live_log(self, text: str, blank_after: bool = False):
        if not hasattr(self, "live_log"):
            return

        self.live_log.insert(tk.END, text.rstrip() + "\n")
        if blank_after:
            self.live_log.insert(tk.END, "\n")

        line_count = int(self.live_log.index("end-1c").split(".")[0])
        if line_count > self._live_log_max_lines:
            remove_to = line_count - self._live_log_max_lines
            self.live_log.delete("1.0", f"{remove_to + 1}.0")

        self.live_log.see(tk.END)

    def _log_time_prefix(self):
        return time.strftime("[%H:%M:%S]")

    def _fmt_time(self, s: float) -> str:
        s = max(0.0, float(s))
        m = int(s // 60)
        sec = s - m * 60
        return f"{m:02d}:{sec:05.2f}"

    def _set_play_state(self, text: str):
        self._play_state = text
        self.status_var.set(text)
        self.piano_view.set_status(text)

    def _update_header_labels(self):
        self.now_song_var.set(f"Song: {self._current_song_name}")
        self.now_mode_var.set(f"Key: {self._current_song_mode}")
        self.now_note_var.set(f"Note: {self._current_note_name}")
        self.now_time_var.set(f"{self._fmt_time(self._current_time_s)} / {self._fmt_time(self._song_total_s)}")

        ratio = 0.0 if self._song_total_s <= 1e-9 else min(1.0, self._current_time_s / self._song_total_s)
        self.progress["maximum"] = 1000
        self.progress["value"] = ratio * 1000

    # ============================================================
    # Score normalization / loading
    # ============================================================
    def _normalize_score_events(self, score_events):
        out = []
        for item in score_events or []:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue

            notes_obj, dur = item[0], item[1]

            try:
                d = max(0.01, float(dur))
            except Exception:
                d = 0.01

            if isinstance(notes_obj, str):
                s = notes_obj.strip().upper()
                if s in ("REST", ""):
                    notes = []
                else:
                    notes = [x.strip().upper() for x in s.split("/") if x.strip()]
            else:
                notes = []
                for x in (notes_obj or []):
                    n = str(x).strip().upper()
                    if n and n != "REST" and n not in notes:
                        notes.append(n)

            out.append((notes, d))
        return out

    def _song_entry_to_score_dict(self, entry):
        if isinstance(entry, dict):
            return {
                "left": self._normalize_score_events(entry.get("left", [])),
                "right": self._normalize_score_events(entry.get("right", [])),
            }

        # old one-hand format defaults to right hand
        return {
            "left": [],
            "right": self._normalize_score_events(entry),
        }

    def _parse_custom_text_to_score_dict(self, raw: str):
        score = {"left": [], "right": []}
        errors = []

        for lineno, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()

            hand = "right"
            note_part = None
            dur_str = None

            # Formats:
            #   C4 0.5
            #   R C4/E4 0.5
            #   L REST 0.2
            if len(parts) == 2:
                note_part, dur_str = parts
            elif len(parts) == 3:
                hand_tok, note_part, dur_str = parts
                hand_tok = hand_tok.strip().upper()
                if hand_tok in ("L", "LEFT"):
                    hand = "left"
                elif hand_tok in ("R", "RIGHT"):
                    hand = "right"
                else:
                    errors.append(f"Line {lineno}: bad hand prefix {hand_tok}")
                    continue
            else:
                errors.append(f"Line {lineno}: expected NOTE DURATION or HAND NOTE DURATION")
                continue

            try:
                dur = float(dur_str)
            except Exception as e:
                errors.append(f"Line {lineno}: {e}")
                continue

            note_part = note_part.strip().upper()
            if note_part == "REST":
                notes = []
            else:
                notes = [x.strip().upper() for x in note_part.split("/") if x.strip()]

            score[hand].append((notes, dur))

        if errors:
            raise ValueError("\n".join(errors))

        return {
            "left": self._normalize_score_events(score["left"]),
            "right": self._normalize_score_events(score["right"]),
        }

    def _score_dict_to_editor_text(self, score_dict):
        lines = []
        for hand in ("left", "right"):
            prefix = "L" if hand == "left" else "R"
            for notes, dur in score_dict.get(hand, []):
                if not notes:
                    lines.append(f"{prefix} REST {dur:.3f}")
                else:
                    lines.append(f"{prefix} {'/'.join(notes)} {dur:.3f}")
        return "\n".join(lines)

    def _score_dict_to_display_song(self, score_dict):
        """
        PianoRollView is still single-line.
        For now, display the RIGHT hand if available;
        otherwise display LEFT hand.
        """
        display_events = score_dict.get("right", [])
        if not display_events:
            display_events = score_dict.get("left", [])

        out = []
        for notes, dur in display_events:
            d = max(0.01, float(dur))
            if not notes:
                out.append(("REST", d))
            else:
                out.append((str(notes[-1]).strip().upper(), d))
        return out

    def _scaled_score_dict(self):
        scale = max(0.25, float(self.tempo_var.get()))
        out = {"left": [], "right": []}
        for hand in ("left", "right"):
            for notes, dur in self._current_score.get(hand, []):
                out[hand].append((list(notes), float(dur) / scale))
        return out

    def _apply_selected_key_and_replan(self, selected_key: str, mirror_to_editor: bool = True):
        if self.loaded_raw_score is None or self.detected_key_info is None:
            return

        selected_key = (selected_key or "Original").strip()

        if selected_key == "Original":
            shift = 0
            shifted_score = self.loaded_raw_score
            target_tonic = self.detected_key_info["tonic"]
        else:
            target_tonic = selected_key
            shift = semitone_delta(
                self.detected_key_info["tonic"],
                target_tonic,
            )
            shifted_score = transpose_score(self.loaded_raw_score, shift)

        # 这些状态以后统一都从这里更新
        self.current_target_key = selected_key
        self.current_target_tonic = target_tonic
        self.current_mode = self.detected_key_info["mode"]
        self._current_song_mode = f"{target_tonic} {self.current_mode}"
        self.current_transpose_semitones = shift
        self.shifted_score_current = shifted_score

        # 真正进入当前工作 score
        self._set_loaded_score(
            os.path.basename(self.loaded_midi_path) if self.loaded_midi_path else self._current_song_name,
            shifted_score,
            mirror_to_editor=mirror_to_editor,
        )

    def _set_loaded_score(self, song_name: str, score_dict, mirror_to_editor: bool = True):
        score_dict = self._song_entry_to_score_dict(score_dict)
        self._current_song_name = song_name
        self._current_score = score_dict

        self._planned_total_s = sum(
            max(0.01, float(d)) for hand in ("left", "right") for _, d in score_dict.get(hand, [])
        )
        self._song_total_s = max(0.01, self._planned_total_s)

        self._reset_event_tables()
        self._clear_play_ui_state()

        if mirror_to_editor:
            self.custom_text.delete("1.0", tk.END)
            self.custom_text.insert(tk.END, self._score_dict_to_editor_text(score_dict))

        try:
            self._replan_loaded_score(switch_to_table=True)
        except Exception as e:
            self._reset_event_tables()
            self._append_live_log(f"{self._log_time_prefix()} PLANNER ERROR {e}")
            messagebox.showerror("Planner error", str(e))
            return

        # 这里改成显示 planner 结果
        self.piano_view.set_song(self._current_song_name, self._current_planned)
        self._update_header_labels()
        self._set_play_state("Stopped")

    def _seq_load_song(self):
        self.loaded_midi_path = None
        self.loaded_raw_score = None
        self.detected_key_info = None
        self.current_target_key = "Original"

        name = self.song_var.get()
        if name not in SONGS:
            return
        score_dict = self._song_entry_to_score_dict(SONGS[name])
        self._set_loaded_score(name, score_dict, mirror_to_editor=True)

    def _seq_load_custom(self):
        self.loaded_midi_path = None
        self.loaded_raw_score = None
        self.detected_key_info = None
        self.current_target_key = "Original"

        raw = self.custom_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("Empty", "Type some notes first.")
            return

        try:
            score_dict = self._parse_custom_text_to_score_dict(raw)
        except Exception as e:
            messagebox.showerror("Parse errors", str(e))
            return

        if not score_dict["left"] and not score_dict["right"]:
            messagebox.showwarning("Empty", "No valid notes found.")
            return

        self._set_loaded_score("(custom)", score_dict, mirror_to_editor=False)

    def _load_midi_file(self):
        path = filedialog.askopenfilename(
            title="Select MIDI file",
            initialdir=r"E:\ELEC 391\code\test1\test1\Python_work\Song\Song_list",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            raw_score = load_score_from_midi(
                path,
                split_note="C4",
                verbose=False,
                max_song_seconds=90.0,
            )
        except Exception as e:
            messagebox.showerror("MIDI load error", str(e))
            return

        self.loaded_midi_path = path
        self.loaded_raw_score = raw_score
        self.raw_score_original = raw_score

        self.detected_key_info = detect_key(path, raw_score)
        self.current_target_key = "Original"
        self.current_target_tonic = self.detected_key_info["tonic"]
        self.current_mode = self.detected_key_info["mode"]
        self._current_song_mode = self.detected_key_info["label"]
        self.current_transpose_semitones = 0
        self.shifted_score_current = raw_score

        self._open_key_settings_popup()

    def _open_key_settings_popup(self):
        if self.loaded_raw_score is None or self.detected_key_info is None:
            return

        popup = tk.Toplevel(self)
        popup.title("Key / Transpose Settings")
        popup.geometry("520x340")
        popup.minsize(520, 340)
        popup.resizable(False, False)
        popup.transient(self)
        popup.grab_set()

        frame = tk.Frame(popup, bg="#11161e")
        frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        fg_main = "#dfe7f2"
        fg_sub = "#9eb0c6"
        bg = "#11161e"

        tk.Label(
            frame,
            text=f"Detected Key: {self.detected_key_info['label']}",
            bg=bg, fg=fg_main, font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(
            frame,
            text=f"Mode: {self.detected_key_info['mode']}",
            bg=bg, fg=fg_sub, font=("Segoe UI", 10)
        ).pack(anchor="w", pady=(0, 12))

        # 只生成当前这首歌在你们音域 / 手型下比较可弹的同模式目标调
        target_infos = get_viable_target_keys(
            raw_score=self.loaded_raw_score,
            detected_key=self.detected_key_info,
            right_planner=self._right_planner,
            left_planner=self._left_planner,
            max_abs_shift=5,
            min_quality=0.78,
        )

        target_values = ["Original"]
        for item in target_infos:
            tonic = item["tonic"]
            if tonic != self.detected_key_info["tonic"]:
                target_values.append(tonic)

        tk.Label(
            frame,
            text="Target Key:",
            bg=bg, fg=fg_main, font=("Segoe UI", 10)
        ).pack(anchor="w")

        target_var = tk.StringVar(value="Original")
        combo = ttk.Combobox(
            frame,
            textvariable=target_var,
            state="readonly",
            width=18,
            values=target_values,
        )
        combo.pack(anchor="w", pady=(6, 12))

        tk.Label(
            frame,
            text="Available: " + ", ".join(target_values),
            bg=bg, fg=fg_sub, font=("Segoe UI", 9),
            wraplength=360, justify="left"
        ).pack(anchor="w", pady=(0, 14))

        ttk.Checkbutton(
            frame,
            text="Auto-open next time",
            variable=self.key_popup_auto_open
        ).pack(anchor="w", pady=(0, 16))

        btn_row = tk.Frame(frame, bg=bg)
        btn_row.pack(fill=tk.X)

        def on_apply():
            selected = target_var.get().strip() or "Original"
            popup.destroy()
            self._apply_selected_key_and_replan(selected, mirror_to_editor=True)

        def on_cancel():
            popup.destroy()
            self._apply_selected_key_and_replan("Original", mirror_to_editor=True)

        ttk.Button(btn_row, text="Apply", command=on_apply).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Cancel", command=on_cancel).pack(side=tk.LEFT)

    # ============================================================
    # Planner / playback
    # ============================================================
    def _build_planned_score(self, score_dict):
        cleaned_score = sanitize_score_for_robot(
            score_dict,
            right_planner=self._right_planner,
            left_planner=self._left_planner,
        )

        planned = plan_robot_score_dual_path(
            score=cleaned_score,
            right_planner=self._right_planner,
            left_planner=self._left_planner,
            dual_cfg=self._dual_cfg,
            note_to_angle_fn=note_to_angle,
        )

        return cleaned_score, planned

    def _replan_loaded_score(self, switch_to_table: bool = False):
        scaled_score = self._scaled_score_dict()
        cleaned_score, planned = self._build_planned_score(scaled_score)

        self._scaled_score_cache = scaled_score
        self._cleaned_score_cache = cleaned_score
        self._current_planned = planned

        self._planned_total_s = 0.0
        for hand in ("left", "right"):
            self._planned_total_s = max(
                self._planned_total_s,
                sum(max(0.01, float(ev.get("duration", 0.01))) for ev in planned.get(hand, []))
            )
        self._song_total_s = max(0.01, self._planned_total_s)

        self._rebuild_event_tables(
            raw_score_dict=self._scaled_score_cache,
            planned_score_dict=self._current_planned,
        )
        self._update_header_labels()

        if switch_to_table:
            self._show_table_page()

    def _on_tempo_change(self, _=None):
        self.tempo_lbl.config(text=f"{self.tempo_var.get():.2f}×")

        if self.conductor and self.conductor.is_alive():
            return

        if not self._current_score["left"] and not self._current_score["right"]:
            return

        try:
            self._replan_loaded_score(switch_to_table=False)
            self.piano_view.set_song(
                self._current_song_name,
                self._current_planned
            )
        except Exception:
            pass

    def _seq_play(self):
        if not (self.reader_left and self.reader_left.is_alive()):
            messagebox.showwarning("Not connected", "Left serial port is not connected.")
            return
        if not (self.reader_right and self.reader_right.is_alive()):
            messagebox.showwarning("Not connected", "Right serial port is not connected.")
            return
        if self.conductor and self.conductor.is_alive():
            return

        if not self._current_score["left"] and not self._current_score["right"]:
            messagebox.showwarning("No song", "Load a song first.")
            return

        self._seq_stop_evt.clear()
        self._seq_pause_evt.clear()

        try:
            self._replan_loaded_score(switch_to_table=False)
        except Exception as e:
            messagebox.showerror("Planner error", str(e))
            self._append_live_log(f"{self._log_time_prefix()} PLANNER ERROR {e}")
            return

        planned = self._current_planned

        if not planned.get("left") and not planned.get("right"):
            messagebox.showwarning("Planner", "Planner returned an empty plan.")
            return

        self._clear_play_ui_state()
        self.piano_view.set_song(self._current_song_name, planned)
        self._update_header_labels()



        self.conductor = DualHandConductor(
            planned_left=planned.get("left", []),
            planned_right=planned.get("right", []),
            out_q=self.q,
            stop_evt=self._seq_stop_evt,
            pause_evt=self._seq_pause_evt,
            hand_transports={
                "left": HandTransport("left", self.reader_left),
                "right": HandTransport("right", self.reader_right),
            },
            get_actual_fn={
                "left": lambda: self.left_actual_center,
                "right": lambda: self.right_actual_center,
            },
            get_actual_spread_fn={
                "left": lambda: self.left_actual_spread,
                "right": lambda: self.right_actual_spread,
            },
        )

        self.worker_left = None
        self.worker_right = None
        self.conductor.start()

        self.btn_play.config(state=tk.DISABLED)
        self.btn_pause_resume.config(state=tk.NORMAL, text="Pause")
        self._set_play_state("Playing")
        self._show_piano_page()
        return

    def _seq_pause(self):
        if not (self.conductor and self.conductor.is_alive()):
            return
        if self._seq_pause_evt.is_set():
            return
        self._seq_pause_evt.set()
        self.btn_pause_resume.config(text="Resume", state=tk.NORMAL)

    def _seq_resume(self):
        if not (self.conductor and self.conductor.is_alive()):
            return
        if not self._seq_pause_evt.is_set():
            return
        self._seq_pause_evt.clear()
        self.btn_pause_resume.config(text="Pause", state=tk.NORMAL)

    def _seq_toggle_pause(self):
        if not (self.conductor and self.conductor.is_alive()):
            return
        if self._seq_pause_evt.is_set():
            self._seq_resume()
        else:
            self._seq_pause()

    def _send_release_and_home_both(self, send_home: bool):
        for reader in (self.reader_left, self.reader_right):
            try:
                if reader and reader.is_alive():
                    reader.write("SL=0!")
                    if send_home:
                        reader.write("RE=1!")
            except Exception:
                pass

    def _seq_stop(self):
        self._seq_stop_evt.set()
        self._seq_pause_evt.clear()
        self._send_release_and_home_both(send_home=False)

        self._pressed_note = None
        self.piano_view.set_pressed_notes(None, None)

        self.btn_play.config(state=tk.NORMAL)
        self.btn_pause_resume.config(state=tk.DISABLED, text="Pause")

        self._append_live_log(f"{self._log_time_prefix()} Stop pressed")

    def _seq_reset(self):
        self._seq_stop_evt.set()
        self._seq_pause_evt.clear()
        self._send_release_and_home_both(send_home=True)

        self._pressed_note = None
        self.piano_view.set_pressed_notes(None, None)

        self.btn_play.config(state=tk.NORMAL)
        self.btn_pause_resume.config(state=tk.DISABLED, text="Pause")

        self._clear_play_ui_state()
        self.piano_view.reset_view()

        try:
            self._replan_loaded_score(switch_to_table=False)
            self.piano_view.set_song(self._current_song_name, self._current_planned)
        except Exception:
            pass

        self._update_header_labels()
        self._set_play_state("Homing...")
        self._append_live_log(f"{self._log_time_prefix()} Reset / Homing")

    # ============================================================
    # Serial connection
    # ============================================================
    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_combo_left["values"] = ports
        self.port_combo_right["values"] = ports

    def _update_connection_ui(self):
        both = self.left_connected and self.right_connected
        if both:
            self.conn_text_var.set("Connected")
            self._draw_conn_dot("#2dd36f")
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.NORMAL)
        elif self.left_connected or self.right_connected:
            self.conn_text_var.set("Partial")
            self._draw_conn_dot("#f5c542")
            self.btn_connect.config(state=tk.NORMAL)
            self.btn_disconnect.config(state=tk.NORMAL)
        else:
            self.conn_text_var.set("Disconnected")
            self._draw_conn_dot("#ff5f56")
            self.btn_connect.config(state=tk.NORMAL)
            self.btn_disconnect.config(state=tk.DISABLED)

    def _connect(self):
        if self.reader_left and self.reader_left.is_alive() and self.reader_right and self.reader_right.is_alive():
            return

        port_left = self.port_var_left.get().strip()
        port_right = self.port_var_right.get().strip()
        if not port_left or not port_right:
            messagebox.showwarning("Ports", "Select both left and right ports.")
            return
        if port_left == port_right:
            messagebox.showwarning("Ports", "Left and right ports must be different.")
            return

        try:
            baud = int(self.baud_var.get())
        except Exception:
            messagebox.showwarning("Baud", "Bad baud rate.")
            return

        # if one side was half-connected before, clean first
        self._disconnect()

        # reset stop events
        self.stop_evt_left = threading.Event()
        self.stop_evt_right = threading.Event()

        try:
            self.reader_left = SerialReader(port_left, baud, self.serial_q_left, self.stop_evt_left)
            self.reader_right = SerialReader(port_right, baud, self.serial_q_right, self.stop_evt_right)

            # bind VOFA to the NEW readers
            self.vofa_listener.reader_left_ref = self.reader_left
            self.vofa_listener.reader_right_ref = self.reader_right
            self.vofa_listener.reader_ref = self.reader_right   # default target = right

            self.reader_left.start()
            self.reader_right.start()

            # don't mark connected yet; wait for status from serial threads
            self.left_connected = False
            self.right_connected = False
            self._update_connection_ui()

            self._append_live_log(
                f"{self._log_time_prefix()} CONNECT start left={port_left}, right={port_right}"
            )

        except Exception as e:
            try:
                if self.reader_left:
                    self.reader_left.close()
            except Exception:
                pass
            try:
                if self.reader_right:
                    self.reader_right.close()
            except Exception:
                pass

            self.reader_left = None
            self.reader_right = None
            self.left_connected = False
            self.right_connected = False
            self.vofa_listener.reader_left_ref = None
            self.vofa_listener.reader_right_ref = None
            self.vofa_listener.reader_ref = None
            self._update_connection_ui()
            messagebox.showerror("Connect error", str(e))

    def _disconnect(self):
        self._seq_stop()

        try:
            self.stop_evt_left.set()
        except Exception:
            pass
        try:
            self.stop_evt_right.set()
        except Exception:
            pass

        try:
            if self.reader_left:
                self.reader_left.close()
        except Exception:
            pass
        try:
            if self.reader_right:
                self.reader_right.close()
        except Exception:
            pass

        self.vofa_listener.reader_left_ref = None
        self.vofa_listener.reader_right_ref = None
        self.vofa_listener.reader_ref = None

        self.reader_left = None
        self.reader_right = None

        self.left_connected = False
        self.right_connected = False
        self._update_connection_ui()

        self._append_live_log(f"{self._log_time_prefix()} DISCONNECT")
    # ============================================================
    # Telemetry
    # ============================================================
    def _parse_mcu_telemetry(self, line: str):
        """
        STM32 raw telemetry format:
        desired1, actual1, duty1, u1, dir1,
        desired2, actual2, duty2, u2, dir2,
        solenoidState, homingActive
        """
        parts = [p.strip() for p in str(line).split(",")]
        if len(parts) != 12:
            return None

        try:
            return {
                "desired1": float(parts[0]),
                "actual1": float(parts[1]),
                "duty1": float(parts[2]),
                "u1": float(parts[3]),
                "dir1": int(float(parts[4])),

                "desired2": float(parts[5]),
                "actual2": float(parts[6]),
                "duty2": float(parts[7]),
                "u2": float(parts[8]),
                "dir2": int(float(parts[9])),

                "solenoid": int(float(parts[10])),
                "homing": int(float(parts[11])),
            }
        except Exception:
            return None

    def _apply_telemetry(self, hand: str, tele: dict):
        if hand == "left":
            self.left_desired_center = tele["desired1"]
            self.left_actual_center = tele["actual1"]
            self.left_dir_center = tele["dir1"]

            self.left_desired_spread = tele["desired2"]
            self.left_actual_spread = tele["actual2"]
            self.left_dir_spread = tele["dir2"]

            self.left_solenoid = tele["solenoid"]
            self.left_homing = tele["homing"]
        else:
            self.right_desired_center = tele["desired1"]
            self.right_actual_center = tele["actual1"]
            self.right_dir_center = tele["dir1"]

            self.right_desired_spread = tele["desired2"]
            self.right_actual_spread = tele["actual2"]
            self.right_dir_spread = tele["dir2"]

            self.right_solenoid = tele["solenoid"]
            self.right_homing = tele["homing"]

        self._refresh_machine_state_view()

    def _arm_note_from_angle(self, angle, hand: str):
        if angle is None:
            return "—"
        try:
            return angle_to_note(angle, hand)
        except Exception:
            return "—"

    def _refresh_machine_state_view(self):
        left_arm = self._arm_note_from_angle(self.left_actual_center, "left")
        right_arm = self._arm_note_from_angle(self.right_actual_center, "right")

        self.machine_vars["Left Arm"].set(left_arm)
        self.machine_vars["Right Arm"].set(right_arm)

        if self.left_desired_spread is not None and self.left_actual_spread is not None:
            self.machine_vars["Left Spread"].set(f"T:{self.left_desired_spread:.0f}° A:{self.left_actual_spread:.0f}°")
        else:
            self.machine_vars["Left Spread"].set("—")

        if self.right_desired_spread is not None and self.right_actual_spread is not None:
            self.machine_vars["Right Spread"].set(f"T:{self.right_desired_spread:.0f}° A:{self.right_actual_spread:.0f}°")
        else:
            self.machine_vars["Right Spread"].set("—")

        self.machine_vars["Notes L"].set("—" if not self._latest_seq_note_left else "/".join(self._latest_seq_note_left))
        self.machine_vars["Fingers L"].set("—" if not self._latest_fingers_left else "/".join(self._latest_fingers_left))
        self.machine_vars["Notes R"].set("—" if not self._latest_seq_note_right else "/".join(self._latest_seq_note_right))
        self.machine_vars["Fingers R"].set("—" if not self._latest_fingers_right else "/".join(self._latest_fingers_right))

        left_arm_note = None if left_arm == "—" else left_arm
        right_arm_note = None if right_arm == "—" else right_arm

        self.piano_view.set_arm_notes(
            left_note=left_arm_note,
            right_note=right_arm_note,
        )

    def _poll_one_serial_queue(self, hand: str, qobj: queue.Queue):
        while True:
            try:
                kind, payload = qobj.get_nowait()
            except queue.Empty:
                break

            if kind == "line":
                tele = self._parse_mcu_telemetry(payload)
                if tele is not None:
                    self._apply_telemetry(hand, tele)

            elif kind == "status":
                txt = str(payload).strip()

                if txt.startswith("Connected to"):
                    if hand == "left":
                        self.left_connected = True
                    else:
                        self.right_connected = True
                    self._update_connection_ui()

                elif txt.startswith("Disconnected"):
                    if hand == "left":
                        self.left_connected = False
                    else:
                        self.right_connected = False
                    self._update_connection_ui()

            elif kind == "error":
                if hand == "left":
                    self.left_connected = False
                else:
                    self.right_connected = False
                self._update_connection_ui()

    # ============================================================
    # App event queue
    # ============================================================
    def _clear_play_ui_state(self):
        self._current_time_s = 0.0
        self._current_note_name = "—"
        self._pressed_note = None

        self._latest_seq_note_left = []
        self._latest_seq_note_right = []
        self._latest_fingers_left = []
        self._latest_fingers_right = []

        self.piano_view.set_current_note(None)
        self.piano_view.set_pressed_notes(None, None)
        self.piano_view.set_arm_notes(None, None)
        self.piano_view.set_play_time(0.0)
        self.piano_view.set_move_info(None, None)

        self._song_total_s = max(0.01, self._planned_total_s)
        self._update_header_labels()
        self._refresh_machine_state_view()
        self._clear_event_table_actuals()

        if hasattr(self, "live_log"):
            self.live_log.delete("1.0", tk.END)

    def _display_note_from_lists(self, left_notes, right_notes):
        if right_notes:
            return right_notes[-1]
        if left_notes:
            return left_notes[-1]
        return "REST"
    
    def _get_planned_event(self, hand: str, payload: dict):
        hand = str(hand).strip().lower()
        try:
            idx = int(payload.get("event_index", payload.get("idx", -1)))
        except Exception:
            return None

        seq = self._current_planned.get(hand, [])
        if 0 <= idx < len(seq):
            return seq[idx]
        return None


    def _format_command_line(self, hand: str, ev: dict):
        hand_tag = "L" if str(hand).lower() == "left" else "R"

        notes = list(ev.get("notes", []))
        fingers = list(ev.get("finger_ids", []))

        notes_txt = "REST" if not notes else "/".join(notes)
        fingers_txt = "-" if not fingers else "/".join(fingers)

        return f"{hand_tag}  {notes_txt}  [{fingers_txt}]"

    def _poll_app_queue(self):
        latest_dhc_time = None
        processed = 0
        max_per_poll = 200   # 防止一次 poll 卡太久

        while processed < max_per_poll:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break

            processed += 1

            if kind in ("dhc_time", "seq_time"):
                try:
                    latest_dhc_time = float(payload)
                except Exception:
                    pass
                continue

            elif kind == "seq_wait":
                try:
                    latest_dhc_time = float(payload.get("elapsed", latest_dhc_time or 0.0))
                except Exception:
                    pass

            elif kind in ("dhc_press", "seq_press"):
                hand = str(payload.get("hand", "")).strip().lower()

                ev = self._get_planned_event(hand, payload)
                if ev is None:
                    continue

                notes = list(ev.get("notes", []))
                fingers = list(ev.get("finger_ids", []))

                if hand == "left":
                    self._latest_seq_note_left = notes
                    self._latest_fingers_left = fingers
                elif hand == "right":
                    self._latest_seq_note_right = notes
                    self._latest_fingers_right = fingers

                left_pressed = self._latest_seq_note_left[-1] if self._latest_seq_note_left else None
                right_pressed = self._latest_seq_note_right[-1] if self._latest_seq_note_right else None

                display_note = self._display_note_from_lists(
                    self._latest_seq_note_left,
                    self._latest_seq_note_right,
                )
                self._current_note_name = display_note
                self.piano_view.set_current_note(None if display_note == "REST" else display_note)
                self.piano_view.set_pressed_notes(
                    left_note=left_pressed,
                    right_note=right_pressed,
                )
                try:
                    latest_dhc_time = float(payload.get("elapsed", latest_dhc_time or 0.0))
                except Exception:
                    pass
                self._update_header_labels()
                self._refresh_machine_state_view()

                self._append_live_log(self._format_command_line(hand, ev))

                try:
                    ev_idx = int(payload.get("event_index", payload.get("idx", -1)))
                except Exception:
                    ev_idx = -1

                if ev_idx >= 0:
                    self._mark_table_actual_press(hand, ev_idx, notes)

            elif kind in ("dhc_release", "seq_release"):
                hand = str(payload.get("hand", "")).strip().lower()

                if hand == "left":
                    self._latest_seq_note_left = []
                    self._latest_fingers_left = []
                elif hand == "right":
                    self._latest_seq_note_right = []
                    self._latest_fingers_right = []

                display_note = self._display_note_from_lists(
                    self._latest_seq_note_left,
                    self._latest_seq_note_right,
                )
                self._current_note_name = display_note
                left_pressed = self._latest_seq_note_left[-1] if self._latest_seq_note_left else None
                right_pressed = self._latest_seq_note_right[-1] if self._latest_seq_note_right else None

                self.piano_view.set_pressed_notes(
                    left_note=left_pressed,
                    right_note=right_pressed,
                )
                self.piano_view.set_current_note(None if display_note == "REST" else display_note)
                try:
                    latest_dhc_time = float(payload.get("elapsed", latest_dhc_time or 0.0))
                except Exception:
                    pass
                self._update_header_labels()
                self._refresh_machine_state_view()

                try:
                    ev_idx = int(payload.get("event_index", payload.get("idx", -1)))
                except Exception:
                    ev_idx = -1

                if ev_idx >= 0:
                    self._mark_table_release(hand, ev_idx)

            elif kind == "seq_rest_start":
                hand = str(payload.get("hand", "")).strip().lower()

                if hand == "left":
                    self._latest_seq_note_left = []
                    self._latest_fingers_left = []
                elif hand == "right":
                    self._latest_seq_note_right = []
                    self._latest_fingers_right = []

                self._current_note_name = "REST"
                self.piano_view.set_current_note(None)
                self.piano_view.set_pressed_notes(None, None)
                try:
                    latest_dhc_time = float(payload.get("elapsed", latest_dhc_time or 0.0))
                except Exception:
                    pass
                self._update_header_labels()
                self._refresh_machine_state_view()

            elif kind == "seq_rest_end":
                try:
                    latest_dhc_time = float(payload.get("elapsed", latest_dhc_time or 0.0))
                except Exception:
                    pass

            elif kind in ("dhc_started", "seq_started"):
                self._set_play_state("Playing")

            elif kind == "seq_home_start":
                self._set_play_state("Homing...")

            elif kind == "seq_home_done":
                self._set_play_state("Playing")

            elif kind in ("dhc_done", "seq_done"):
                try:
                    self._current_time_s = float(payload.get("elapsed", self._current_time_s))
                except Exception:
                    pass
                self._update_header_labels()
                self._set_play_state("Done")
                self.btn_play.config(state=tk.NORMAL)
                self.btn_pause_resume.config(state=tk.DISABLED, text="Pause")

            elif kind in ("dhc_stop", "seq_stop"):
                self._set_play_state("Stopped")
                self.btn_play.config(state=tk.NORMAL)
                self.btn_pause_resume.config(state=tk.DISABLED, text="Pause")

            elif kind in ("dhc_paused", "seq_paused"):
                self._set_play_state("Paused")

            elif kind in ("dhc_resumed", "seq_resumed"):
                self._set_play_state("Playing")

            elif kind == "error":
                self._append_live_log(f"{self._log_time_prefix()} ERROR {payload}")

            elif kind == "status":
                txt = str(payload).strip()
                if txt:
                    self._append_live_log(f"{self._log_time_prefix()} {txt}")

            # 这些先不刷 UI，不然太多
            elif kind in (
                "dhc_ready", "dhc_prepare", "dhc_idle_prepare",
                "hw_paused", "hw_resumed",
                "seq_note", "seq_move_time",
            ):
                pass

        if latest_dhc_time is not None:
            self._current_time_s = latest_dhc_time
            self.piano_view.set_play_time(self._current_time_s)
            self._update_header_labels()

    def _poll_all_queues(self):
        try:
            self._poll_one_serial_queue("left", self.serial_q_left)
            self._poll_one_serial_queue("right", self.serial_q_right)
            self._poll_app_queue()
        finally:
            self.after(UI_POLL_MS, self._poll_all_queues)

    # ============================================================
    # Close
    # ============================================================
    def on_close(self):
        try:
            self._seq_stop_evt.set()
            self._seq_pause_evt.clear()
            self._send_release_and_home_both(send_home=False)
        except Exception:
            pass

        try:
            self.stop_evt_left.set()
        except Exception:
            pass
        try:
            self.stop_evt_right.set()
        except Exception:
            pass
        try:
            self.vofa_stop_evt.set()
        except Exception:
            pass

        try:
            self.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    app = ShowApp()
    app.mainloop()
