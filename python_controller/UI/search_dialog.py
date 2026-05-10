import tkinter as tk
from tkinter import ttk

from Song.song_search import (
    search_song_query,
    build_random_demo,
    generate_song_from_selected_title,
)


class AISearchDialog(tk.Toplevel):
    def __init__(self, parent, on_song_selected):
        super().__init__(parent)
        self.parent = parent
        self.on_song_selected = on_song_selected

        self.title("AI Song Search")
        self.geometry("520x420")
        self.minsize(480, 360)
        self.configure(bg="#11161e")

        self.query_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Type a song name and click Search.")
        self._last_query = ""

        self._build_ui()

        self.transient(parent)
        self.grab_set()
        self.focus_force()
        self.entry.focus_set()

    def _build_ui(self):
        root = tk.Frame(self, bg="#11161e")
        root.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)

        tk.Label(
            root,
            text="AI Song Search",
            bg="#11161e",
            fg="#dfe7f2",
            font=("Segoe UI", 15, "bold")
        ).pack(anchor="w", pady=(0, 10))

        tk.Label(
            root,
            text="Enter a song name. If nothing is found, you can accept a random playable rhythm demo.",
            bg="#11161e",
            fg="#9eb0c6",
            justify=tk.LEFT,
            wraplength=470,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(0, 12))

        top_row = tk.Frame(root, bg="#11161e")
        top_row.pack(fill=tk.X, pady=(0, 10))

        self.entry = ttk.Entry(top_row, textvariable=self.query_var)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", lambda e: self._on_search())

        ttk.Button(
            top_row,
            text="Search",
            command=self._on_search
        ).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(
            root,
            textvariable=self.status_var,
            bg="#11161e",
            fg="#89f0dd",
            justify=tk.LEFT,
            anchor="w",
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(0, 10))

        self.result_frame = tk.Frame(root, bg="#0c1016", highlightthickness=1, highlightbackground="#1c2733")
        self.result_frame.pack(fill=tk.BOTH, expand=True)

        bottom_row = tk.Frame(root, bg="#11161e")
        bottom_row.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(bottom_row, text="Close", command=self.destroy).pack(side=tk.RIGHT)

        self._show_idle()

    def _clear_result_frame(self):
        for child in self.result_frame.winfo_children():
            child.destroy()

    def _section_title(self, text: str):
        tk.Label(
            self.result_frame,
            text=text,
            bg="#0c1016",
            fg="#dfe7f2",
            anchor="w",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=14, pady=(14, 8))

    def _normal_text(self, text: str, color="#9eb0c6"):
        tk.Label(
            self.result_frame,
            text=text,
            bg="#0c1016",
            fg=color,
            justify=tk.LEFT,
            wraplength=450,
            anchor="w",
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=14, pady=(0, 10))

    def _button_row(self):
        row = tk.Frame(self.result_frame, bg="#0c1016")
        row.pack(fill=tk.X, padx=14, pady=(0, 10))
        return row

    def _show_idle(self):
        self._clear_result_frame()
        self._section_title("Ready")
        self._normal_text("Search for a song title to get a playable result.")

    def _show_found(self, title: str, song):
        self._clear_result_frame()
        self._section_title("Found")
        self._normal_text(f"Matched song: {title}", color="#89f0dd")

        preview = tk.Frame(self.result_frame, bg="#0f1520")
        preview.pack(fill=tk.X, padx=14, pady=(0, 12))

        preview_lines = []
        for note, dur in song[:8]:
            preview_lines.append(f"{note:<5} {dur:.3f}s")
        if len(song) > 8:
            preview_lines.append("...")

        tk.Label(
            preview,
            text="\n".join(preview_lines) if preview_lines else "(empty)",
            bg="#0f1520",
            fg="#dfe7f2",
            justify=tk.LEFT,
            anchor="w",
            font=("Consolas", 10)
        ).pack(anchor="w", padx=10, pady=10)

        row = self._button_row()
        ttk.Button(
            row,
            text="Use This Song",
            command=lambda: self._accept_song(title, song)
        ).pack(side=tk.LEFT)

    def _show_suggestions(self, suggestions):
        self._clear_result_frame()
        self._section_title("Did you mean")
        self._normal_text("Select one of the likely song matches below.")

        btn_wrap = tk.Frame(self.result_frame, bg="#0c1016")
        btn_wrap.pack(fill=tk.X, padx=14, pady=(0, 10))

        for s in suggestions:
            ttk.Button(
                btn_wrap,
                text=s,
                command=lambda name=s: self._search_exact(name)
            ).pack(fill=tk.X, pady=4)

        row = self._button_row()
        ttk.Button(
            row,
            text="Try Random Demo Instead",
            command=self._accept_random_demo
        ).pack(side=tk.LEFT)

    def _show_not_found(self):
        self._clear_result_frame()
        self._section_title("No matching song found")
        self._normal_text(
            "This input does not look like a known song title.\n"
            "Would you like to load a random playable rhythm demo instead?",
            color="#ffb86b"
        )

        row = self._button_row()
        ttk.Button(
            row,
            text="Accept Random Demo",
            command=self._accept_random_demo
        ).pack(side=tk.LEFT)

        ttk.Button(
            row,
            text="Cancel",
            command=self.destroy
        ).pack(side=tk.LEFT, padx=(8, 0))

    def _show_error(self, text: str):
        self._clear_result_frame()
        self._section_title("Search error")
        self._normal_text(text, color="#ff8e8e")

    def _on_search(self):
        query = self.query_var.get().strip()
        self._last_query = query

        if not query:
            self.status_var.set("Please type a song name first.")
            self._show_idle()
            return

        self.status_var.set(f"Searching for: {query}")
        self.update_idletasks()

        try:
            result = search_song_query(query)
        except Exception as e:
            self.status_var.set("Search failed.")
            self._show_error(str(e))
            return

        self._render_result(result)

    def _search_exact(self, title: str):
        self.query_var.set(title)
        self.status_var.set(f"Generating melody for: {title}")
        self.update_idletasks()

        try:
            result = generate_song_from_selected_title(title)
        except Exception as e:
            self.status_var.set("Generation failed.")
            self._show_error(str(e))
            return

        self._render_result(result)

    def _render_result(self, result: dict):
        status = result.get("status", "")

        if status == "found":
            title = result.get("title", "(AI Song)")
            song = result.get("song", [])
            self.status_var.set(f"Found: {title}")
            self._show_found(title, song)
            return

        if status == "suggest":
            suggestions = result.get("suggestions", [])
            if not suggestions:
                self.status_var.set("No clear match found.")
                self._show_not_found()
                return
            self.status_var.set("Possible matches found.")
            self._show_suggestions(suggestions)
            return

        if status == "not_found":
            self.status_var.set("No matching song found.")
            self._show_not_found()
            return

        self.status_var.set("Unknown search result.")
        self._show_error(f"Unsupported result: {result}")

    def _accept_song(self, title: str, song):
        if callable(self.on_song_selected):
            self.on_song_selected(title, song)
        self.destroy()

    def _accept_random_demo(self):
        base = self._last_query if self._last_query else "random demo"
        title, song = build_random_demo(base)
        self._accept_song(title, song)