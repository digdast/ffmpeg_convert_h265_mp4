#!/usr/bin/env python3
"""
================================================================================
🎬 FFmpeg H.265 Multi-GPU Interactive Transcoder Studio (GUI)
================================================================================
A native, high-performance GUI application for accelerated video transcoding.
Key Features:
- 🚀 True Dual-GPU / Multi-GPU Concurrent Transcoding (NVIDIA NVENC, Intel QSV,
  AMD AMF, Apple VideoToolbox, Linux DRM/VAAPI, and Multi-Core CPU).
- 📊 Graduated Level Progress Bars with real-time visual level meters.
- ⚡ Per-GPU Live Telemetry Cards (Speed, FPS, Bitrate, ETA, Level Meter).
- 📁 Interactive Batch Queue Manager with File/Folder picker and scan filters.
- 🎚 Interactive Quality (CQ / CRF) and Preset sliders.
- 🛡 Atomic media replacement and verification safeguards.
================================================================================
"""

import os
import sys
import time
import json
import queue
import shutil
import subprocess
import threading
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Import hardware discovery
try:
    from gpu_detector import GPUCard, MultiGPUManager, get_ffmpeg_path, get_ffprobe_path
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from gpu_detector import GPUCard, MultiGPUManager, get_ffmpeg_path, get_ffprobe_path

# Import remote NAS transcode engine
try:
    from remote_engine import (
        RemoteTranscodeManager, RemoteHostConfig, load_remote_configs,
        save_remote_configs, DEFAULT_PRESET_CONFIGS
    )
except ImportError:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_dir)
    from remote_engine import (
        RemoteTranscodeManager, RemoteHostConfig, load_remote_configs,
        save_remote_configs, DEFAULT_PRESET_CONFIGS
    )

# High-DPI awareness on Windows
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".ts", ".m2ts", ".webm", ".m4v", ".vob"
)


def format_bytes(b: float) -> str:
    """Format bytes into human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024.0:
            return f"{b:5.1f} {unit}"
        b /= 1024.0
    return f"{b:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    seconds = max(0.0, seconds)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ==============================================================================
# 🎨 Modern UI Colors & Theme
# ==============================================================================
class Theme:
    BG = "#181825"             # Deep slate background
    PANEL_BG = "#1e1e2e"       # Card / section background
    HEADER_BG = "#24273a"      # Header bar
    BORDER = "#313244"         # Border tone
    ACCENT = "#89b4fa"         # Primary blue
    ACCENT_HOVER = "#b4befe"   # Lighter accent
    SUCCESS = "#a6e3a1"        # Emerald green
    WARNING = "#f9e2af"        # Soft yellow
    DANGER = "#f38ba8"         # Coral red
    PURPLE = "#cba6f7"         # Lavender / purple
    TEXT = "#cdd6f4"           # Off-white primary text
    TEXT_MUTED = "#9399b2"     # Muted text
    BAR_EMPTY = "#313244"      # Empty meter fill


# ==============================================================================
# 📊 Custom Level Progress Bar Canvas Widget
# ==============================================================================
class LevelMeterCanvas(tk.Canvas):
    """
    Renders a graduated segmented Level Progress Bar with glowing level stages.
    Displays:
    - 10 graduated block segments [LVL 1/10] through [LVL MAX]
    - Dynamic color shift based on completion (Cyan -> Blue -> Green -> Lime)
    - Embedded telemetry text and percentage
    """
    def __init__(self, parent, height: int = 24, num_levels: int = 10, **kwargs):
        super().__init__(
            parent,
            height=height,
            bg=Theme.BG,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
            **kwargs
        )
        self.num_levels = num_levels
        self.fraction = 0.0
        self.stage_text = "LVL 0/10"
        self.right_text = "0.0%"
        self.bind("<Configure>", lambda e: self.redraw())

    def update_progress(self, fraction: float, stage_text: str = "", right_text: str = ""):
        self.fraction = max(0.0, min(1.0, fraction))
        lvl = int(self.fraction * self.num_levels)
        if self.fraction >= 0.999:
            self.stage_text = "LVL MAX"
        else:
            self.stage_text = stage_text or f"LVL {lvl}/{self.num_levels}"
        self.right_text = right_text or f"{self.fraction * 100.0:5.1f}%"
        self.redraw()

    def get_color(self, pct: float) -> str:
        if pct >= 0.95:
            return "#a6e3a1"  # Emerald Green
        elif pct >= 0.70:
            return "#94e2d5"  # Teal / Mint
        elif pct >= 0.40:
            return "#89b4fa"  # Sky Blue
        elif pct >= 0.15:
            return "#b4befe"  # Lavender
        return "#cba6f7"      # Soft Violet

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        # Background
        self.create_rectangle(0, 0, w, h, fill=Theme.PANEL_BG, outline="")

        # Graduated Segmented Blocks
        gap = 3
        pad = 2
        usable_w = w - (pad * 2) - ((self.num_levels - 1) * gap)
        block_w = max(2, usable_w / self.num_levels)

        active_color = self.get_color(self.fraction)

        for i in range(self.num_levels):
            x1 = pad + (i * (block_w + gap))
            x2 = x1 + block_w
            y1 = pad
            y2 = h - pad

            block_threshold = (i + 1) / self.num_levels
            if self.fraction >= block_threshold:
                fill_col = active_color
            elif self.fraction > (i / self.num_levels):
                # Partial block fill
                part = (self.fraction - (i / self.num_levels)) * self.num_levels
                fill_col = active_color
                # Render base empty
                self.create_rectangle(x1, y1, x2, y2, fill=Theme.BAR_EMPTY, outline="")
                # Render filled fraction
                x2_part = x1 + (block_w * part)
                self.create_rectangle(x1, y1, x2_part, y2, fill=fill_col, outline="")
                continue
            else:
                fill_col = Theme.BAR_EMPTY

            self.create_rectangle(x1, y1, x2, y2, fill=fill_col, outline="")

        # Text Overlay: Stage on left, Percentage on right
        self.create_text(
            pad + 8, h // 2,
            text=self.stage_text,
            anchor="w",
            fill="#ffffff",
            font=("Segoe UI", 9, "bold")
        )
        self.create_text(
            w - pad - 8, h // 2,
            text=self.right_text,
            anchor="e",
            fill="#ffffff",
            font=("Segoe UI", 9, "bold")
        )


# ==============================================================================
# ⚡ Live GPU Worker Card
# ==============================================================================
class WorkerStatusCard(tk.Frame):
    """
    Renders an individual card for a transcode worker (GPU 0, GPU 1, or CPU).
    Shows active file name, level meter, speed, fps, bitrate, and ETA.
    """
    def __init__(self, parent, worker_id: int, card_title: str, device_name: str, **kwargs):
        super().__init__(
            parent,
            bg=Theme.PANEL_BG,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
            padx=10,
            pady=8,
            **kwargs
        )
        self.worker_id = worker_id

        # Top Header: Title & Device Name
        hdr_frame = tk.Frame(self, bg=Theme.PANEL_BG)
        hdr_frame.pack(fill="x")

        self.title_lbl = tk.Label(
            hdr_frame,
            text=card_title,
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold")
        )
        self.title_lbl.pack(side="left")

        self.dev_lbl = tk.Label(
            hdr_frame,
            text=device_name,
            fg=Theme.TEXT_MUTED,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 8)
        )
        self.dev_lbl.pack(side="right")

        # Active File Label
        self.file_lbl = tk.Label(
            self,
            text="Status: Ready / Idle",
            fg=Theme.TEXT,
            bg=Theme.PANEL_BG,
            anchor="w",
            font=("Segoe UI", 9)
        )
        self.file_lbl.pack(fill="x", pady=(4, 4))

        # Level Progress Bar
        self.level_bar = LevelMeterCanvas(self, height=22, num_levels=10)
        self.level_bar.pack(fill="x", pady=2)

        # Telemetry Metrics Line (Speed, FPS, Bitrate, Time/ETA)
        self.metrics_lbl = tk.Label(
            self,
            text="⚡ Speed: 0.0x  |  FPS: 0  |  Bitrate: 0 kb/s  |  ETA: --:--:--",
            fg=Theme.TEXT_MUTED,
            bg=Theme.PANEL_BG,
            anchor="w",
            font=("Consolas", 8)
        )
        self.metrics_lbl.pack(fill="x", pady=(4, 0))

    def update_telemetry(
        self,
        file_name: str,
        fraction: float,
        speed: str = "N/A",
        fps: str = "0",
        bitrate: str = "N/A",
        elapsed: str = "00:00",
        eta: str = "--:--:--"
    ):
        clean_file = os.path.basename(file_name) if file_name else "Idle"
        if len(clean_file) > 42:
            clean_file = clean_file[:39] + "..."
        self.file_lbl.config(text=f"🎬 {clean_file}")
        self.level_bar.update_progress(fraction)
        self.metrics_lbl.config(
            text=f"⚡ Speed: {speed:<5} | FPS: {fps:<4} | Bitrate: {bitrate:<8} | Elapsed: {elapsed} | ETA: {eta}"
        )

    def set_idle(self, msg: str = "Status: Ready / Idle"):
        self.file_lbl.config(text=msg)
        self.level_bar.update_progress(0.0, "LVL 0/10", "0.0%")
        self.metrics_lbl.config(text="⚡ Speed: 0.0x  |  FPS: 0  |  Bitrate: 0 kb/s  |  ETA: --:--:--")


# ==============================================================================
# 🎬 Main Interactive Transcoder Studio GUI
# ==============================================================================
class TranscoderStudioGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("⚡ FFmpeg Multi-GPU Transcoder Studio | Dual-Engine H.265")
        self.geometry("1200x820")
        self.minsize(980, 680)
        self.configure(bg=Theme.BG)

        # Hardware manager
        self.hw_mgr = MultiGPUManager()
        self.detected_devices: List[GPUCard] = self.hw_mgr.detect_all_devices()
        self.ffmpeg_path = get_ffmpeg_path()
        self.ffprobe_path = get_ffprobe_path()

        # Batch Queue State
        self.queue_items: List[Dict[str, Any]] = []
        self.is_transcoding = False
        self.is_paused = False
        self.cancel_requested = False
        self.worker_threads: List[threading.Thread] = []
        self.work_queue: queue.Queue = queue.Queue()
        self.ui_queue: queue.Queue = queue.Queue()
        self.active_procs: Dict[int, subprocess.Popen] = {}
        self.stats = {
            "total": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "orig_bytes": 0,
            "new_bytes": 0,
            "start_time": 0.0
        }

        # Remote Target Environment State
        self.target_mode_var = tk.StringVar(value="local")  # "local" or "remote"
        self.remote_manager = RemoteTranscodeManager()
        self.remote_configs: List[RemoteHostConfig] = load_remote_configs()
        self.mode_var = tk.StringVar(value="dual_gpu" if len([d for d in self.detected_devices if d.vendor != "CPU"]) >= 2 else "auto")

        # Setup ttk style
        self.setup_styles()

        # Shared queue/source variables initialized before panels
        self.recursive_var = tk.BooleanVar(value=True)
        self.source_dir_var = tk.StringVar(value="")
        self.remote_path_var = tk.StringVar(value="/data/media/movies")

        # Build UI layout with fail-safe docking order:
        # 1. Header (pinned to top)
        self.create_header_panel()
        # 2. STEP 1: Source Media Directory / Folder Selector (pinned right below header — ALWAYS visible!)
        self.create_source_directory_panel()
        # 3. Bottom Action/Status Bar (pinned to bottom FIRST so it is NEVER pushed off-screen)
        self.create_action_bar()
        # 4. Scrollable / Responsive Main Workspace so Queue & Controls NEVER clip on any DPI/resolution
        self.create_scrollable_workspace()
        self.create_target_environment_panel()
        self.create_queue_panel()
        self.create_hardware_panel()
        self.create_settings_panel()
        self.create_progress_panel()

        # Load any paths passed on command line (e.g., `transcode-gui "D:\Movies"`)
        if len(sys.argv) > 1:
            self.after(150, lambda: self.load_cli_paths(sys.argv[1:]))

        # Telemetry update timer
        self.after(50, self.periodic_ui_update)

    def update_start_button_state(self, text: Optional[str] = None, bg: Optional[str] = None, fg: Optional[str] = None):
        """Synchronizes text and appearance across all Start/Stop buttons in UI."""
        is_remote = (self.target_mode_var.get() == "remote")
        if text is None:
            text = "▶ Start Remote NAS Transcode" if is_remote else "▶ Start Dual-GPU Transcode"
        if bg is None:
            bg = Theme.SUCCESS
        if fg is None:
            fg = "#000000"

        for btn in (getattr(self, "btn_start", None),
                    getattr(self, "btn_start_queue_local", None),
                    getattr(self, "btn_start_queue_remote", None)):
            if btn:
                try:
                    btn.config(text=text, bg=bg, fg=fg)
                except Exception:
                    pass

    def post_ui(self, fn, *args):
        """Thread-safe UI dispatcher: queues a callback to execute on main thread."""
        self.ui_queue.put((fn, args))

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        # Treeview Customization
        style.configure(
            "Treeview",
            background=Theme.PANEL_BG,
            foreground=Theme.TEXT,
            fieldbackground=Theme.PANEL_BG,
            font=("Segoe UI", 9),
            rowheight=26,
            borderwidth=0
        )
        style.configure(
            "Treeview.Heading",
            background=Theme.HEADER_BG,
            foreground=Theme.TEXT,
            font=("Segoe UI", 9, "bold"),
            relief="flat"
        )
        style.map(
            "Treeview",
            background=[("selected", Theme.ACCENT)],
            foreground=[("selected", "#000000")]
        )

        # Scrollbar
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background=Theme.PANEL_BG,
            darkcolor=Theme.BORDER,
            lightcolor=Theme.BORDER,
            troughcolor=Theme.BG,
            bordercolor=Theme.BG,
            arrowcolor=Theme.TEXT
        )

    # --------------------------------------------------------------------------
    # UI Component Construction
    # --------------------------------------------------------------------------
    def create_header_panel(self):
        hdr = tk.Frame(self, bg=Theme.HEADER_BG, padx=16, pady=10)
        hdr.pack(fill="x")

        title_box = tk.Frame(hdr, bg=Theme.HEADER_BG)
        title_box.pack(side="left")

        title_lbl = tk.Label(
            title_box,
            text="⚡ FFmpeg Multi-GPU & Remote NAS Transcoder Studio",
            fg=Theme.TEXT,
            bg=Theme.HEADER_BG,
            font=("Segoe UI", 15, "bold")
        )
        title_lbl.pack(anchor="w")

        subtitle = tk.Label(
            title_box,
            text="Dual-GPU Acceleration + Remote SSH Direct Transcoding for Mounted NAS Drives",
            fg=Theme.ACCENT,
            bg=Theme.HEADER_BG,
            font=("Segoe UI", 9)
        )
        subtitle.pack(anchor="w")

        # FFmpeg status badge
        badge_box = tk.Frame(hdr, bg=Theme.HEADER_BG)
        badge_box.pack(side="right")

        ff_version_text = "FFmpeg: Available"
        try:
            ver_res = subprocess.run([self.ffmpeg_path, "-version"], capture_output=True, text=True, timeout=2)
            first_line = ver_res.stdout.splitlines()[0] if ver_res.stdout else ""
            if "version" in first_line:
                ff_version_text = first_line.split("Copyright")[0].strip()
        except Exception:
            pass

        ff_badge = tk.Label(
            badge_box,
            text=f"✔ {ff_version_text}",
            fg=Theme.SUCCESS,
            bg="#2a3f35",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=4,
            relief="groove"
        )
        ff_badge.pack(anchor="e")

    def create_source_directory_panel(self):
        """Pinned top bar so selecting/pasting a media directory to transcode is always Step 1."""
        src_outer = tk.Frame(
            self,
            bg="#1e293b",
            padx=14,
            pady=10,
            highlightthickness=2,
            highlightbackground=Theme.ACCENT
        )
        src_outer.pack(fill="x", padx=14, pady=(8, 4))

        top_hdr = tk.Frame(src_outer, bg="#1e293b")
        top_hdr.pack(fill="x", pady=(0, 6))

        tk.Label(
            top_hdr,
            text="📂 STEP 1: Select Media Directory or Video Files to Transcode / Encode",
            fg=Theme.ACCENT,
            bg="#1e293b",
            font=("Segoe UI", 10, "bold")
        ).pack(side="left")

        self.top_queue_badge = tk.Label(
            top_hdr,
            text="Queue: 0 videos ready",
            fg=Theme.WARNING,
            bg="#0f172a",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=2
        )
        self.top_queue_badge.pack(side="right")

        # Local Source Row
        self.top_local_source_row = tk.Frame(src_outer, bg="#1e293b")
        self.top_local_source_row.pack(fill="x")

        tk.Button(
            self.top_local_source_row,
            text="📂 Browse Directory...",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=5,
            cursor="hand2",
            command=self.add_folder
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            self.top_local_source_row,
            text="➕ Add Video Files...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.add_files
        ).pack(side="left", padx=(0, 12))

        tk.Label(
            self.top_local_source_row,
            text="Or Paste Folder/UNC Path:",
            fg=Theme.TEXT,
            bg="#1e293b",
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 6))

        self.source_dir_entry = tk.Entry(
            self.top_local_source_row,
            textvariable=self.source_dir_var,
            bg=Theme.BG,
            fg=Theme.TEXT,
            insertbackground=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="solid"
        )
        self.source_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=4)
        self.source_dir_entry.bind("<Return>", lambda e: self.scan_typed_source_path())

        tk.Button(
            self.top_local_source_row,
            text="🔍 Add Path",
            bg=Theme.SUCCESS,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.scan_typed_source_path
        ).pack(side="left", padx=(0, 10))

        tk.Checkbutton(
            self.top_local_source_row,
            text="Subfolders",
            variable=self.recursive_var,
            bg="#1e293b",
            fg=Theme.TEXT,
            selectcolor=Theme.HEADER_BG,
            activebackground="#1e293b",
            font=("Segoe UI", 9)
        ).pack(side="left")

        # Remote Source Row (shown when Remote NAS mode is selected)
        self.top_remote_source_row = tk.Frame(src_outer, bg="#1e293b")

        tk.Label(
            self.top_remote_source_row,
            text="Remote NAS Directory:",
            fg=Theme.TEXT,
            bg="#1e293b",
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 6))

        ttk.Combobox(
            self.top_remote_source_row,
            textvariable=self.remote_path_var,
            values=[
                "/data/media/movies",
                "/data/media/tv-shows",
                "/mnt/user/data/media",
                "/data"
            ],
            width=44
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        tk.Button(
            self.top_remote_source_row,
            text="📂 Scan Remote Directory",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=12,
            pady=4,
            command=self.scan_remote_dir
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            self.top_remote_source_row,
            text="📄 Add Remote File...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            padx=8,
            pady=4,
            command=self.prompt_add_remote_file
        ).pack(side="left")

    def create_scrollable_workspace(self):
        """Scrollable container so no panel is ever clipped on High-DPI or smaller screens."""
        container = tk.Frame(self, bg=Theme.BG)
        container.pack(fill="both", expand=True)

        self.workspace_canvas = tk.Canvas(container, bg=Theme.BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(container, orient="vertical", command=self.workspace_canvas.yview)
        self.workspace_canvas.configure(yscrollcommand=vscroll.set)

        vscroll.pack(side="right", fill="y")
        self.workspace_canvas.pack(side="left", fill="both", expand=True)

        self.workspace_inner = tk.Frame(self.workspace_canvas, bg=Theme.BG)
        self._workspace_win = self.workspace_canvas.create_window((0, 0), window=self.workspace_inner, anchor="nw")

        self.workspace_inner.bind(
            "<Configure>",
            lambda e: self.workspace_canvas.configure(scrollregion=self.workspace_canvas.bbox("all"))
        )
        self.workspace_canvas.bind(
            "<Configure>",
            lambda e: self.workspace_canvas.itemconfigure(self._workspace_win, width=e.width)
        )

        def _on_mousewheel(event):
            if getattr(event, "delta", 0):
                self.workspace_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.bind_all("<MouseWheel>", _on_mousewheel)

    def create_target_environment_panel(self):
        env_frame = tk.LabelFrame(
            self.workspace_inner,
            text="  🎯 Target Environment & Remote NAS Connection  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=8
        )
        env_frame.pack(fill="x", padx=14, pady=(8, 4))

        # Switcher Row: Local vs Remote
        switch_row = tk.Frame(env_frame, bg=Theme.PANEL_BG)
        switch_row.pack(fill="x")

        tk.Radiobutton(
            switch_row,
            text="🖥 Local Host Machine (Direct Local NVIDIA / Intel / AMD / CPU)",
            variable=self.target_mode_var,
            value="local",
            command=self.on_target_mode_changed,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT,
            selectcolor=Theme.HEADER_BG,
            activebackground=Theme.PANEL_BG,
            activeforeground=Theme.ACCENT,
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 24))

        tk.Radiobutton(
            switch_row,
            text="🌐 Remote Computer / NAS (SSH Direct Transcoding on Mounted Storage)",
            variable=self.target_mode_var,
            value="remote",
            command=self.on_target_mode_changed,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT,
            selectcolor=Theme.HEADER_BG,
            activebackground=Theme.PANEL_BG,
            activeforeground=Theme.ACCENT,
            font=("Segoe UI", 9, "bold")
        ).pack(side="left")

        # Remote Server Configuration Box (shown when Remote mode selected)
        self.remote_box = tk.Frame(env_frame, bg=Theme.HEADER_BG, padx=10, pady=8, highlightthickness=1, highlightbackground=Theme.BORDER)
        # Note: packed dynamically in on_target_mode_changed

        # Row 1 of Remote Box: Preset Dropdown + Host + Port + User + Password + Key
        r1 = tk.Frame(self.remote_box, bg=Theme.HEADER_BG)
        r1.pack(fill="x", pady=2)

        tk.Label(r1, text="Host Preset:", fg=Theme.TEXT, bg=Theme.HEADER_BG, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        preset_names = [f"{c.name} ({c.host})" for c in self.remote_configs] + ["Custom Host..."]
        self.remote_preset_var = tk.StringVar(value=preset_names[0] if preset_names else "Custom Host...")
        self.remote_preset_cb = ttk.Combobox(
            r1,
            textvariable=self.remote_preset_var,
            values=preset_names,
            state="readonly",
            width=36
        )
        self.remote_preset_cb.pack(side="left", padx=(0, 10))
        self.remote_preset_cb.bind("<<ComboboxSelected>>", self.on_remote_preset_selected)

        # Host
        tk.Label(r1, text="Host IP:", fg=Theme.TEXT, bg=Theme.HEADER_BG, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self.remote_host_var = tk.StringVar(value=self.remote_configs[0].host if self.remote_configs else "192.168.0.31")
        tk.Entry(r1, textvariable=self.remote_host_var, width=13, bg=Theme.BG, fg=Theme.TEXT, insertbackground=Theme.TEXT, relief="solid").pack(side="left", padx=(0, 8))

        # Port
        tk.Label(r1, text="Port:", fg=Theme.TEXT, bg=Theme.HEADER_BG, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self.remote_port_var = tk.StringVar(value="22")
        tk.Entry(r1, textvariable=self.remote_port_var, width=5, bg=Theme.BG, fg=Theme.TEXT, insertbackground=Theme.TEXT, relief="solid").pack(side="left", padx=(0, 8))

        # User
        tk.Label(r1, text="User:", fg=Theme.TEXT, bg=Theme.HEADER_BG, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self.remote_user_var = tk.StringVar(value=self.remote_configs[0].username if self.remote_configs else "root")
        tk.Entry(r1, textvariable=self.remote_user_var, width=9, bg=Theme.BG, fg=Theme.TEXT, insertbackground=Theme.TEXT, relief="solid").pack(side="left", padx=(0, 8))

        # Password
        tk.Label(r1, text="Password:", fg=Theme.TEXT, bg=Theme.HEADER_BG, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        self.remote_pwd_var = tk.StringVar(value=self.remote_configs[0].password or "sone_420" if self.remote_configs else "")
        self.remote_pwd_entry = tk.Entry(r1, textvariable=self.remote_pwd_var, show="*", width=12, bg=Theme.BG, fg=Theme.TEXT, insertbackground=Theme.TEXT, relief="solid")
        self.remote_pwd_entry.pack(side="left", padx=(0, 4))

        self.remote_show_pwd_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r1,
            text="👁",
            variable=self.remote_show_pwd_var,
            command=self.toggle_show_pwd,
            bg=Theme.HEADER_BG,
            fg=Theme.TEXT_MUTED,
            selectcolor=Theme.HEADER_BG
        ).pack(side="left", padx=(0, 10))

        # Connect / Disconnect button
        self.btn_remote_connect = tk.Button(
            r1,
            text="🔗 Connect & Detect Hardware",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            command=self.toggle_remote_connection
        )
        self.btn_remote_connect.pack(side="left", padx=4)

        # Row 2: Status indicator line + SSH Key browse
        r2 = tk.Frame(self.remote_box, bg=Theme.HEADER_BG)
        r2.pack(fill="x", pady=(4, 0))

        self.remote_status_lbl = tk.Label(
            r2,
            text="🔴 Disconnected. Click 'Connect & Detect Hardware' to link to remote host.",
            fg=Theme.TEXT_MUTED,
            bg=Theme.HEADER_BG,
            font=("Segoe UI", 8, "italic")
        )
        self.remote_status_lbl.pack(side="left")

        # Save config button
        tk.Button(
            r2,
            text="💾 Save Host Profile",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 8),
            relief="flat",
            padx=6,
            pady=2,
            command=self.save_current_remote_profile
        ).pack(side="right", padx=(8, 0))

        # SSH Key input
        tk.Button(
            r2,
            text="🔑 SSH Key...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 8),
            relief="flat",
            padx=6,
            pady=2,
            command=self.browse_ssh_key
        ).pack(side="right")
        self.remote_key_var = tk.StringVar(value="")

    def on_target_mode_changed(self):
        mode = self.target_mode_var.get()
        if mode == "remote":
            self.remote_box.pack(fill="x", pady=(6, 2))
            if hasattr(self, "top_local_source_row") and hasattr(self, "top_remote_source_row"):
                self.top_local_source_row.pack_forget()
                self.top_remote_source_row.pack(fill="x")
            if hasattr(self, "local_queue_toolbar") and hasattr(self, "remote_queue_toolbar"):
                self.local_queue_toolbar.pack_forget()
                self.remote_queue_toolbar.pack(fill="x", pady=(0, 6))
            self.update_start_button_state()
        else:
            self.remote_box.pack_forget()
            if hasattr(self, "top_local_source_row") and hasattr(self, "top_remote_source_row"):
                self.top_remote_source_row.pack_forget()
                self.top_local_source_row.pack(fill="x")
            if hasattr(self, "local_queue_toolbar") and hasattr(self, "remote_queue_toolbar"):
                self.remote_queue_toolbar.pack_forget()
                self.local_queue_toolbar.pack(fill="x", pady=(0, 6))
            self.update_start_button_state()
        self.refresh_hardware_panel()

    def on_remote_preset_selected(self, event=None):
        sel = self.remote_preset_var.get()
        for c in self.remote_configs:
            if f"{c.name} ({c.host})" == sel or c.host == sel:
                self.remote_host_var.set(c.host)
                self.remote_port_var.set(str(c.port))
                self.remote_user_var.set(c.username)
                if c.password:
                    self.remote_pwd_var.set(c.password)
                if c.key_path:
                    self.remote_key_var.set(c.key_path)
                break

    def toggle_show_pwd(self):
        if self.remote_show_pwd_var.get():
            self.remote_pwd_entry.config(show="")
        else:
            self.remote_pwd_entry.config(show="*")

    def browse_ssh_key(self):
        k = filedialog.askopenfilename(
            title="Select SSH Private Key (id_rsa, id_ed25519, etc.)",
            filetypes=[("All Files", "*.*")]
        )
        if k:
            self.remote_key_var.set(k)
            messagebox.showinfo("SSH Key Loaded", f"Loaded key:\n{k}")

    def save_current_remote_profile(self):
        host = self.remote_host_var.get().strip()
        port = int(self.remote_port_var.get().strip() or "22")
        user = self.remote_user_var.get().strip() or "root"
        pwd = self.remote_pwd_var.get()
        key = self.remote_key_var.get().strip() or None

        if not host:
            messagebox.showwarning("Host Required", "Please enter a valid remote host.")
            return

        cfg = RemoteHostConfig(
            host=host,
            port=port,
            username=user,
            password=pwd if pwd else None,
            key_path=key,
            name=f"{user}@{host}"
        )
        updated = False
        for idx, c in enumerate(self.remote_configs):
            if c.host == host:
                self.remote_configs[idx] = cfg
                updated = True
                break
        if not updated:
            self.remote_configs.append(cfg)

        save_remote_configs(self.remote_configs)
        preset_names = [f"{c.name} ({c.host})" for c in self.remote_configs] + ["Custom Host..."]
        self.remote_preset_cb.config(values=preset_names)
        messagebox.showinfo("Saved", f"Remote host profile for {host} saved successfully.")

    def toggle_remote_connection(self):
        if self.remote_manager.is_connected:
            self.remote_manager.disconnect()
            self.btn_remote_connect.config(text="🔗 Connect & Detect Hardware", bg=Theme.ACCENT, fg="#000000")
            self.remote_status_lbl.config(
                text="🔴 Disconnected. Click 'Connect & Detect Hardware' to link to remote host.",
                fg=Theme.TEXT_MUTED
            )
            self.refresh_hardware_panel()
            self.status_bar_lbl.config(text="Disconnected from remote server.")
            return

        host = self.remote_host_var.get().strip()
        port = int(self.remote_port_var.get().strip() or "22")
        user = self.remote_user_var.get().strip() or "root"
        pwd = self.remote_pwd_var.get()
        key = self.remote_key_var.get().strip() or None

        if not host:
            messagebox.showerror("Host Required", "Please enter a valid remote host IP or hostname.")
            return

        cfg = RemoteHostConfig(
            host=host,
            port=port,
            username=user,
            password=pwd if pwd else None,
            key_path=key,
            name=host
        )

        self.btn_remote_connect.config(state="disabled", text="⏳ Connecting...")
        self.remote_status_lbl.config(text=f"🟡 Connecting to {user}@{host}:{port} and probing hardware...", fg=Theme.WARNING)

        def _bg_connect():
            ok, msg = self.remote_manager.connect(cfg)
            self.post_ui(self._on_remote_connected, ok, msg, cfg)

        threading.Thread(target=_bg_connect, daemon=True).start()

    def _on_remote_connected(self, ok: bool, msg: str, cfg: RemoteHostConfig):
        self.btn_remote_connect.config(state="normal")
        if ok:
            self.btn_remote_connect.config(text="🔌 Disconnect", bg=Theme.DANGER, fg="#ffffff")
            self.remote_status_lbl.config(text=f"✔ {msg}", fg=Theme.SUCCESS)
            self.status_bar_lbl.config(text=f"Connected to remote host: {cfg.host}")
            self.refresh_hardware_panel()
        else:
            self.btn_remote_connect.config(text="🔗 Connect & Detect Hardware", bg=Theme.ACCENT, fg="#000000")
            self.remote_status_lbl.config(text=f"✖ Connection failed: {msg}", fg=Theme.DANGER)
            messagebox.showerror("Connection Error", f"Could not connect to {cfg.host}:\n{msg}")

    def create_hardware_panel(self):
        frame = tk.LabelFrame(
            self.workspace_inner,
            text="  ⚡ Hardware Acceleration & Multi-Engine Configuration  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6
        )
        frame.pack(fill="x", padx=14, pady=(4, 4))

        self.hw_det_lbl = tk.Label(
            frame,
            text="Probing hardware acceleration...",
            fg=Theme.TEXT_MUTED,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 9)
        )
        self.hw_det_lbl.pack(anchor="w", pady=(0, 6))

        self.hw_mode_row = tk.Frame(frame, bg=Theme.PANEL_BG)
        self.hw_mode_row.pack(fill="x")

        self.refresh_hardware_panel()

    def refresh_hardware_panel(self):
        for child in self.hw_mode_row.winfo_children():
            child.destroy()

        is_remote = (self.target_mode_var.get() == "remote")
        if is_remote and self.remote_manager.is_connected:
            devices = self.remote_manager.remote_devices
            gpu_names = [f"[{d.vendor}] {d.name}" for d in devices if d.vendor != "CPU"]
            summary_str = (
                f"🌐 Remote Hardware ({self.remote_manager.config.host}): " +
                ("  •  ".join(gpu_names) if gpu_names else "Remote CPU Only")
            )
            self.hw_det_lbl.config(
                text=summary_str,
                fg=Theme.SUCCESS if gpu_names else Theme.WARNING
            )

            gpu_devs = [d for d in devices if d.vendor != "CPU"]
            options = []
            if len(gpu_devs) >= 2:
                options.append((f"🚀 Remote Dual-GPU Mode (Use Both {gpu_devs[0].name} + {gpu_devs[1].name} Simultaneously)", "dual_gpu"))
            options.append(("⚡ Auto Remote Engine (Fastest Available Hardware Engine)", "auto"))
            for idx, dev in enumerate(devices):
                if dev.vendor != "CPU":
                    options.append((f"⚡ Remote {dev.name} ({dev.encoder}) Only", f"remote_gpu_{idx}"))
            options.append(("💻 Remote Multi-Core CPU (libx265 Multithreading)", "cpu"))

            self.mode_var.set("dual_gpu" if len(gpu_devs) >= 2 else "auto")
            for i, (label_text, mode_val) in enumerate(options):
                rb = tk.Radiobutton(
                    self.hw_mode_row,
                    text=label_text,
                    variable=self.mode_var,
                    value=mode_val,
                    bg=Theme.PANEL_BG,
                    fg=Theme.TEXT,
                    selectcolor=Theme.HEADER_BG,
                    activebackground=Theme.PANEL_BG,
                    activeforeground=Theme.ACCENT,
                    font=("Segoe UI", 9)
                )
                rb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 28), pady=1)
        elif is_remote:
            self.hw_det_lbl.config(
                text="🌐 Remote Mode: Not connected to remote host. Connect above to probe remote hardware.",
                fg=Theme.WARNING
            )
            lbl = tk.Label(
                self.hw_mode_row,
                text="Please connect to remote server above to query hardware accelerators.",
                fg=Theme.TEXT_MUTED,
                bg=Theme.PANEL_BG,
                font=("Segoe UI", 9, "italic")
            )
            lbl.grid(row=0, column=0, sticky="w", pady=4)
        else:
            devices = self.detected_devices
            gpu_names = [f"[{d.vendor}] {d.name}" for d in devices if d.vendor != "CPU"]
            summary_str = f"Found {len(gpu_names)} GPU(s): " + "  •  ".join(gpu_names) if gpu_names else "No dedicated GPU detected (CPU mode available)"
            self.hw_det_lbl.config(
                text=summary_str,
                fg=Theme.SUCCESS if gpu_names else Theme.WARNING
            )

            options = []
            if len(gpu_names) >= 2:
                options.append(("🚀 Dual GPU Mode (Use Both Cards Simultaneously for Max Power)", "dual_gpu"))
            options.append(("⚡ Auto GPU (Fastest Available Hardware Engine)", "auto"))

            for idx, dev in enumerate(devices):
                if dev.vendor != "CPU":
                    options.append((f"⚡ {dev.name} ({dev.encoder}) Only", f"gpu_{idx}"))

            options.append(("🔥 Triple-Engine (Both GPUs + CPU Multithreading)", "all"))
            options.append(("💻 CPU Only (Software libx265 Multithreading)", "cpu"))

            self.mode_var.set("dual_gpu" if len(gpu_names) >= 2 else "auto")
            for i, (label_text, mode_val) in enumerate(options):
                rb = tk.Radiobutton(
                    self.hw_mode_row,
                    text=label_text,
                    variable=self.mode_var,
                    value=mode_val,
                    bg=Theme.PANEL_BG,
                    fg=Theme.TEXT,
                    selectcolor=Theme.HEADER_BG,
                    activebackground=Theme.PANEL_BG,
                    activeforeground=Theme.ACCENT,
                    font=("Segoe UI", 9)
                )
                rb.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 28), pady=1)

    def create_settings_panel(self):
        frame = tk.LabelFrame(
            self.workspace_inner,
            text="  ⚙ Transcoding & Quality Settings  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6
        )
        frame.pack(fill="x", padx=14, pady=4)

        # Row 1: CQ/CRF Slider + Preset Dropdown
        r1 = tk.Frame(frame, bg=Theme.PANEL_BG)
        r1.pack(fill="x", pady=2)

        # Quality slider
        tk.Label(r1, text="Constant Quality (CQ/CRF):", fg=Theme.TEXT, bg=Theme.PANEL_BG, font=("Segoe UI", 9)).pack(side="left")
        self.cq_val_lbl = tk.Label(r1, text="22 (Optimal High Quality)", fg=Theme.ACCENT, bg=Theme.PANEL_BG, font=("Segoe UI", 9, "bold"), width=24, anchor="w")

        self.cq_slider = tk.Scale(
            r1,
            from_=16,
            to=32,
            orient="horizontal",
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT,
            troughcolor=Theme.BG,
            highlightthickness=0,
            length=180,
            command=self.on_cq_change
        )
        self.cq_slider.set(22)
        self.cq_slider.pack(side="left", padx=(6, 12))
        self.cq_val_lbl.pack(side="left", padx=(0, 24))

        # Preset Dropdown
        tk.Label(r1, text="Speed Preset:", fg=Theme.TEXT, bg=Theme.PANEL_BG, font=("Segoe UI", 9)).pack(side="left")
        self.preset_var = tk.StringVar(value="p4 (Balanced / Medium)")
        preset_cb = ttk.Combobox(
            r1,
            textvariable=self.preset_var,
            values=[
                "p1 (Fastest / Ultra-Fast)",
                "p2 (Faster)",
                "p4 (Balanced / Medium)",
                "p6 (High Quality)",
                "p7 (Slowest / Maximum Compression)"
            ],
            state="readonly",
            width=28
        )
        preset_cb.pack(side="left", padx=6)

        # Audio stream mode
        tk.Label(r1, text="Audio:", fg=Theme.TEXT, bg=Theme.PANEL_BG, font=("Segoe UI", 9)).pack(side="left", padx=(16, 4))
        self.audio_var = tk.StringVar(value="copy")
        audio_cb = ttk.Combobox(
            r1,
            textvariable=self.audio_var,
            values=["copy (Direct Stream Copy - Zero Loss)", "aac (AAC Stereo 192k)"],
            state="readonly",
            width=26
        )
        audio_cb.pack(side="left", padx=4)

        # Container format
        tk.Label(r1, text="Format:", fg=Theme.TEXT, bg=Theme.PANEL_BG, font=("Segoe UI", 9)).pack(side="left", padx=(16, 4))
        self.container_var = tk.StringVar(value="auto (Preserve MKV/MP4)")
        container_cb = ttk.Combobox(
            r1,
            textvariable=self.container_var,
            values=["auto (Preserve MKV/MP4)", "mp4 (Modern MP4)", "mkv (Matroska)"],
            state="readonly",
            width=22
        )
        container_cb.pack(side="left", padx=4)

        # Row 2: Safety options & destination
        r2 = tk.Frame(frame, bg=Theme.PANEL_BG)
        r2.pack(fill="x", pady=(6, 2))

        self.skip_hevc_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            r2,
            text="Skip videos already encoded in HEVC/H.265 or AV1",
            variable=self.skip_hevc_var,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT,
            selectcolor=Theme.HEADER_BG,
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 16))

        self.in_place_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            r2,
            text="In-Place Safe Replace (Replace original file after verification)",
            variable=self.in_place_var,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT,
            selectcolor=Theme.HEADER_BG,
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 16))

        # Output folder selection
        tk.Label(r2, text="Output Folder (Blank = Same Folder):", fg=Theme.TEXT, bg=Theme.PANEL_BG, font=("Segoe UI", 9)).pack(side="left")
        self.out_dir_var = tk.StringVar(value="")
        self.out_dir_entry = tk.Entry(
            r2,
            textvariable=self.out_dir_var,
            bg=Theme.BG,
            fg=Theme.TEXT,
            insertbackground=Theme.TEXT,
            relief="solid",
            width=22
        )
        self.out_dir_entry.pack(side="left", padx=6)
        tk.Button(
            r2,
            text="Browse...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            command=self.browse_output_dir
        ).pack(side="left")

    def on_cq_change(self, val):
        v = int(float(val))
        desc = "Near Lossless" if v <= 18 else "Optimal High Quality" if v <= 23 else "High Compression"
        self.cq_val_lbl.config(text=f"{v} ({desc})")

    def browse_output_dir(self):
        d = filedialog.askdirectory(title="Select Optional Output Folder (Leave Blank to Save Alongside Originals)")
        if d:
            self.out_dir_var.set(d)

    def create_queue_panel(self):
        frame = tk.LabelFrame(
            self.workspace_inner,
            text="  📁 2. Batch Media Queue (Directories & Files Scheduled for H.265 Encoding)  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6
        )
        frame.pack(fill="both", expand=True, padx=14, pady=4)

        # 1. Local Queue Toolbar
        self.local_queue_toolbar = tk.Frame(frame, bg=Theme.PANEL_BG)
        self.local_queue_toolbar.pack(fill="x", pady=(0, 6))

        tk.Button(
            self.local_queue_toolbar,
            text="📂 Add Folder / Directory...",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            command=self.add_folder
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            self.local_queue_toolbar,
            text="➕ Add Media Files...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=4,
            command=self.add_files
        ).pack(side="left", padx=4)

        tk.Checkbutton(
            self.local_queue_toolbar,
            text="Include Subdirectories (Recursive)",
            variable=self.recursive_var,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT_MUTED,
            selectcolor=Theme.HEADER_BG,
            font=("Segoe UI", 8)
        ).pack(side="left", padx=10)

        self.btn_start_queue_local = tk.Button(
            self.local_queue_toolbar,
            text="▶ Start Dual-GPU Transcode",
            bg=Theme.SUCCESS,
            fg="#000000",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=4,
            command=self.start_transcode_batch
        )
        self.btn_start_queue_local.pack(side="right", padx=(8, 0))

        tk.Button(
            self.local_queue_toolbar,
            text="🧹 Clear All",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            padx=8,
            pady=4,
            command=self.clear_all
        ).pack(side="right", padx=4)

        tk.Button(
            self.local_queue_toolbar,
            text="🗑 Remove Selected",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            padx=8,
            pady=4,
            command=self.remove_selected
        ).pack(side="right", padx=4)

        # 2. Remote Queue Toolbar (for NAS mounts)
        self.remote_queue_toolbar = tk.Frame(frame, bg=Theme.PANEL_BG)
        # Note: packed dynamically in on_target_mode_changed

        tk.Label(
            self.remote_queue_toolbar,
            text="Remote NAS Path:",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(0, 4))

        self.remote_path_cb = ttk.Combobox(
            self.remote_queue_toolbar,
            textvariable=self.remote_path_var,
            values=[
                "/data/media/movies",
                "/data/media/tv-shows",
                "/mnt/user/data/media",
                "/data"
            ],
            width=42
        )
        self.remote_path_cb.pack(side="left", padx=(0, 6))

        self.btn_scan_remote = tk.Button(
            self.remote_queue_toolbar,
            text="📂 Scan Remote NAS Directory",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            command=self.scan_remote_dir
        )
        self.btn_scan_remote.pack(side="left", padx=(0, 6))

        tk.Button(
            self.remote_queue_toolbar,
            text="📄 Add Remote File...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            padx=8,
            pady=4,
            command=self.prompt_add_remote_file
        ).pack(side="left", padx=(0, 8))

        tk.Checkbutton(
            self.remote_queue_toolbar,
            text="Recursive",
            variable=self.recursive_var,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT_MUTED,
            selectcolor=Theme.HEADER_BG,
            font=("Segoe UI", 8)
        ).pack(side="left", padx=(0, 8))

        self.btn_start_queue_remote = tk.Button(
            self.remote_queue_toolbar,
            text="▶ Start Remote NAS Transcode",
            bg=Theme.SUCCESS,
            fg="#000000",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=14,
            pady=4,
            command=self.start_transcode_batch
        )
        self.btn_start_queue_remote.pack(side="right", padx=(8, 0))

        tk.Button(
            self.remote_queue_toolbar,
            text="🧹 Clear All",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            padx=8,
            pady=4,
            command=self.clear_all
        ).pack(side="right", padx=4)

        tk.Button(
            self.remote_queue_toolbar,
            text="🗑 Remove Selected",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            padx=8,
            pady=4,
            command=self.remove_selected
        ).pack(side="right", padx=4)

        # Queue Treeview
        tree_frame = tk.Frame(frame, bg=Theme.PANEL_BG)
        tree_frame.pack(fill="both", expand=True)

        cols = ("idx", "status", "filename", "orig_size", "new_size", "saved", "worker", "path")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended", height=6)

        self.tree.heading("idx", text="#", anchor="center")
        self.tree.heading("status", text="Status", anchor="center")
        self.tree.heading("filename", text="File Name", anchor="w")
        self.tree.heading("orig_size", text="Original Size", anchor="e")
        self.tree.heading("new_size", text="New Size", anchor="e")
        self.tree.heading("saved", text="Space Saved", anchor="e")
        self.tree.heading("worker", text="Hardware Engine", anchor="w")
        self.tree.heading("path", text="Full Path", anchor="w")

        self.tree.column("idx", width=40, anchor="center")
        self.tree.column("status", width=120, anchor="center")
        self.tree.column("filename", width=220, anchor="w")
        self.tree.column("orig_size", width=95, anchor="e")
        self.tree.column("new_size", width=95, anchor="e")
        self.tree.column("saved", width=90, anchor="e")
        self.tree.column("worker", width=160, anchor="w")
        self.tree.column("path", width=300, anchor="w")

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def create_progress_panel(self):
        frame = tk.LabelFrame(
            self.workspace_inner,
            text="  📊 Visual Level Progress & Live Telemetry  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6
        )
        frame.pack(fill="x", padx=14, pady=4)

        # 1. Overall Batch Level Meter
        batch_top = tk.Frame(frame, bg=Theme.PANEL_BG)
        batch_top.pack(fill="x")

        self.batch_title_lbl = tk.Label(
            batch_top,
            text="📦 Overall Batch Queue Progress:",
            fg=Theme.TEXT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 9, "bold")
        )
        self.batch_title_lbl.pack(side="left")

        self.batch_stats_lbl = tk.Label(
            batch_top,
            text="0 / 0 Completed (0.0%)  |  Reclaimed: 0 MB  |  Elapsed: 00:00  |  ETA: --:--:--",
            fg=Theme.TEXT_MUTED,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 9)
        )
        self.batch_stats_lbl.pack(side="right")

        self.overall_bar = LevelMeterCanvas(frame, height=24, num_levels=10)
        self.overall_bar.pack(fill="x", pady=(4, 10))

        # 2. Worker Cards Container (Dual GPU or Multi-Engine)
        self.cards_frame = tk.Frame(frame, bg=Theme.PANEL_BG)
        self.cards_frame.pack(fill="x")

        # Create Worker 1 (GPU 0) and Worker 2 (GPU 1) cards
        gpu_list = [d for d in self.detected_devices if d.vendor != "CPU"]

        w1_name = gpu_list[0].name if len(gpu_list) > 0 else "Primary GPU"
        self.card_worker1 = WorkerStatusCard(
            self.cards_frame,
            worker_id=1,
            card_title="⚡ WORKER 1 (GPU 0)",
            device_name=w1_name
        )
        self.card_worker1.pack(side="left", fill="x", expand=True, padx=(0, 6))

        w2_name = gpu_list[1].name if len(gpu_list) > 1 else "Secondary GPU / CPU"
        self.card_worker2 = WorkerStatusCard(
            self.cards_frame,
            worker_id=2,
            card_title="⚡ WORKER 2 (GPU 1 / QSV)",
            device_name=w2_name
        )
        self.card_worker2.pack(side="left", fill="x", expand=True, padx=(6, 0))

    def create_action_bar(self):
        bar = tk.Frame(self, bg=Theme.HEADER_BG, padx=16, pady=10)
        bar.pack(fill="x", side="bottom")

        # Quick Add Directory / Files buttons on persistent bottom bar too
        tk.Button(
            bar,
            text="📂 Add Directory...",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=12,
            pady=5,
            command=self.add_folder
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            bar,
            text="➕ Add Files...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=5,
            command=self.add_files
        ).pack(side="left", padx=(0, 14))

        # Summary text
        self.status_bar_lbl = tk.Label(
            bar,
            text="Step 1: Click 'Browse Directory...' or paste a folder path above to load videos.",
            fg=Theme.TEXT_MUTED,
            bg=Theme.HEADER_BG,
            font=("Segoe UI", 9)
        )
        self.status_bar_lbl.pack(side="left")

        # Action Buttons
        self.btn_pause = tk.Button(
            bar,
            text="⏸ Pause Queue",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            padx=12,
            pady=6,
            state="disabled",
            command=self.toggle_pause
        )
        self.btn_pause.pack(side="right", padx=(8, 0))

        self.btn_start = tk.Button(
            bar,
            text="▶ Start Dual-GPU Transcode",
            bg=Theme.SUCCESS,
            fg="#000000",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=18,
            pady=6,
            command=self.start_transcode_batch
        )
        self.btn_start.pack(side="right")

    # --------------------------------------------------------------------------
    # Queue Management
    # --------------------------------------------------------------------------
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select Video Files to Transcode",
            filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.ts *.m4v *.webm"), ("All Files", "*.*")]
        )
        if files:
            self.append_to_queue(list(files))

    def scan_local_directory_into_queue(self, d: str, show_empty_popup: bool = True) -> int:
        if not d or not os.path.exists(d):
            if show_empty_popup:
                messagebox.showwarning("Invalid Path", f"Directory or file path does not exist:\n{d}")
            return 0

        if os.path.isfile(d):
            self.source_dir_var.set(d)
            self.append_to_queue([d])
            self.status_bar_lbl.config(text=f"Added video file: {d}")
            return 1

        self.source_dir_var.set(d)
        recursive = self.recursive_var.get()
        found = []
        if recursive:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.startswith("."):
                        continue
                    if f.lower().endswith(VIDEO_EXTENSIONS):
                        found.append(os.path.join(root, f))
        else:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p) and f.lower().endswith(VIDEO_EXTENSIONS):
                    found.append(p)

        found.sort()
        if found:
            self.append_to_queue(found)
            self.status_bar_lbl.config(text=f"✔ Added {len(found)} video file(s) from {d}")
            return len(found)
        else:
            if show_empty_popup:
                messagebox.showinfo("No Videos Found", f"No video files found in:\n{d}")
            return 0

    def scan_typed_source_path(self):
        raw = self.source_dir_var.get().strip().strip('"').strip("'")
        if not raw:
            self.add_folder()
            return
        self.scan_local_directory_into_queue(raw, show_empty_popup=True)

    def load_cli_paths(self, cli_args: List[str]):
        for arg in cli_args:
            clean = arg.strip().strip('"').strip("'")
            if clean and not clean.startswith("-") and os.path.exists(clean):
                self.scan_local_directory_into_queue(clean, show_empty_popup=False)

    def add_folder(self):
        initial = self.source_dir_var.get().strip().strip('"')
        d = filedialog.askdirectory(
            title="Select Media Directory to Scan & Transcode",
            initialdir=initial if (initial and os.path.isdir(initial)) else None
        )
        if not d:
            return
        self.scan_local_directory_into_queue(d, show_empty_popup=True)

    def scan_remote_dir(self):
        if not self.remote_manager.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to the remote host first before scanning.")
            return

        remote_path = self.remote_path_var.get().strip()
        if not remote_path:
            messagebox.showwarning("Path Missing", "Please enter a valid remote directory path.")
            return

        recursive = self.recursive_var.get()
        self.status_bar_lbl.config(text=f"🔍 Scanning remote NAS directory on {self.remote_manager.config.host}: {remote_path}...")
        self.btn_scan_remote.config(state="disabled", text="⏳ Scanning...")

        def _bg_scan():
            items = self.remote_manager.scan_remote_directory(remote_path, recursive=recursive)
            self.post_ui(self._on_remote_scan_finished, items, remote_path)

        threading.Thread(target=_bg_scan, daemon=True).start()

    def _on_remote_scan_finished(self, items: List[Dict[str, Any]], remote_path: str):
        self.btn_scan_remote.config(state="normal", text="📂 Scan Remote NAS Directory")
        if not items:
            self.status_bar_lbl.config(text=f"No video files found in remote path: {remote_path}")
            messagebox.showinfo("No Videos Found", f"No video files found in remote NAS directory:\n{remote_path}")
            return

        added = 0
        existing_paths = {item["path"] for item in self.queue_items}
        for it in items:
            p = it["path"]
            if p in existing_paths:
                continue
            idx = len(self.queue_items) + 1
            entry = {
                "idx": idx,
                "path": p,
                "filename": it["filename"],
                "orig_size": it.get("size", 0),
                "new_size": 0,
                "saved_bytes": 0,
                "saved_pct": 0.0,
                "status": "⏳ Queued (Remote)",
                "worker": f"Remote ({self.remote_manager.config.host})",
                "duration": 0.0,
                "is_remote": True
            }
            self.queue_items.append(entry)
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    idx,
                    entry["status"],
                    entry["filename"],
                    format_bytes(entry["orig_size"]),
                    "-",
                    "-",
                    entry["worker"],
                    entry["path"]
                )
            )
            added += 1

        self.update_batch_stats()
        self.status_bar_lbl.config(text=f"✔ Added {added} remote files from {self.remote_manager.config.host}:{remote_path}")

    def prompt_add_remote_file(self):
        if not self.remote_manager.is_connected:
            messagebox.showwarning("Not Connected", "Please connect to the remote host first.")
            return

        from tkinter import simpledialog
        p = simpledialog.askstring(
            "Add Remote File Path",
            "Enter full path to remote video file on NAS (e.g. /data/media/.../file.mkv):",
            initialvalue="/data/media/tv-shows/Accused (US)/Season 2/"
        )
        if p and p.strip():
            clean_p = p.strip()
            existing_paths = {item["path"] for item in self.queue_items}
            if clean_p in existing_paths:
                return

            idx = len(self.queue_items) + 1
            entry = {
                "idx": idx,
                "path": clean_p,
                "filename": os.path.basename(clean_p),
                "orig_size": 0,
                "new_size": 0,
                "saved_bytes": 0,
                "saved_pct": 0.0,
                "status": "⏳ Queued (Remote)",
                "worker": f"Remote ({self.remote_manager.config.host})",
                "duration": 0.0,
                "is_remote": True
            }
            self.queue_items.append(entry)
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    idx,
                    entry["status"],
                    entry["filename"],
                    "-",
                    "-",
                    "-",
                    entry["worker"],
                    entry["path"]
                )
            )
            self.update_batch_stats()

    def append_to_queue(self, paths: List[str]):
        existing_paths = {item["path"] for item in self.queue_items}
        added = 0
        for p in paths:
            if p in existing_paths:
                continue
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0

            idx = len(self.queue_items) + 1
            item = {
                "idx": idx,
                "path": p,
                "filename": os.path.basename(p),
                "orig_size": sz,
                "new_size": 0,
                "saved_bytes": 0,
                "saved_pct": 0.0,
                "status": "⏳ Queued",
                "worker": "Pending",
                "duration": 0.0
            }
            self.queue_items.append(item)
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    idx,
                    item["status"],
                    item["filename"],
                    format_bytes(sz),
                    "-",
                    "-",
                    "-",
                    item["path"]
                )
            )
            added += 1

        self.update_batch_stats()

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        to_del = set(sel)
        self.queue_items = [it for it in self.queue_items if str(it["idx"]) not in to_del]
        for s in sel:
            self.tree.delete(s)
        # Re-index
        for idx, it in enumerate(self.queue_items, 1):
            it["idx"] = idx
        self.update_batch_stats()

    def clear_all(self):
        if self.is_transcoding:
            messagebox.showwarning("Busy", "Cannot clear queue while transcoding is active.")
            return
        self.tree.delete(*self.tree.get_children())
        self.queue_items.clear()
        self.update_batch_stats()

    def update_batch_stats(self):
        total = len(self.queue_items)
        done = sum(1 for it in self.queue_items if it["status"] == "✔ Complete")
        orig_tot = sum(it["orig_size"] for it in self.queue_items if it["status"] == "✔ Complete")
        new_tot = sum(it["new_size"] for it in self.queue_items if it["status"] == "✔ Complete")
        saved = max(0, orig_tot - new_tot)
        ratio = (saved / orig_tot * 100.0) if orig_tot > 0 else 0.0

        fraction = (done / total) if total > 0 else 0.0
        self.overall_bar.update_progress(fraction)

        self.batch_stats_lbl.config(
            text=f"{done} / {total} Completed ({fraction*100:.1f}%)  |  Reclaimed: {format_bytes(saved)} ({ratio:.1f}%)"
        )
        self.status_bar_lbl.config(
            text=f"Queue: {total} items | {done} completed | Net space saved: {format_bytes(saved)}"
        )
        if hasattr(self, "top_queue_badge"):
            queued_bytes = sum(it.get("orig_size", 0) for it in self.queue_items)
            if total > 0:
                self.top_queue_badge.config(
                    text=f"✔ Queue: {total} video(s) ({format_bytes(queued_bytes).strip()})",
                    fg=Theme.SUCCESS
                )
            else:
                self.top_queue_badge.config(
                    text="Queue: 0 videos ready",
                    fg=Theme.WARNING
                )

    # --------------------------------------------------------------------------
    # Video Probe Helper
    # --------------------------------------------------------------------------
    def probe_video(self, file_path: str) -> Dict[str, Any]:
        """Query duration, video codec, and subtitle streams."""
        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                duration = 0.0
                if "format" in data and "duration" in data["format"]:
                    duration = float(data["format"]["duration"])
                vcodec = ""
                for s in data.get("streams", []):
                    if s.get("codec_type") == "video":
                        vcodec = s.get("codec_name", "")
                        if duration <= 0 and "duration" in s:
                            duration = float(s["duration"])
                        break

                subtitles = []
                bitmap_subs = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvdsub", "xsub", "dvb_subtitle", "pgssub"}
                for s in data.get("streams", []):
                    if s.get("codec_type") == "subtitle":
                        cname = s.get("codec_name", "").lower()
                        subtitles.append({
                            "index": s.get("index"),
                            "codec": cname,
                            "is_bitmap": cname in bitmap_subs
                        })

                return {"duration": duration, "vcodec": vcodec, "subtitles": subtitles}
        except Exception:
            pass
        return {"duration": 0.0, "vcodec": "", "subtitles": []}

    # --------------------------------------------------------------------------
    # Transcode Execution Engine
    # --------------------------------------------------------------------------
    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.config(text="▶ Resume Queue", bg=Theme.WARNING, fg="#000000")
            self.status_bar_lbl.config(text="Queue PAUSED. Current items will finish, new items paused.")
        else:
            self.btn_pause.config(text="⏸ Pause Queue", bg=Theme.BORDER, fg=Theme.TEXT)
            self.status_bar_lbl.config(text="Queue Resumed.")

    def start_transcode_batch(self):
        if self.is_transcoding:
            # Stop action
            if messagebox.askyesno("Stop Transcode", "Are you sure you want to cancel the active batch transcoding?"):
                self.cancel_requested = True
                self.status_bar_lbl.config(text="Cancelling active tasks...")
                if self.target_mode_var.get() == "remote":
                    self.remote_manager.cancel_active_job()
                for p in list(self.active_procs.values()):
                    try:
                        p.terminate()
                    except Exception:
                        pass
            return

        if not self.queue_items:
            typed = self.source_dir_var.get().strip().strip('"').strip("'")
            if self.target_mode_var.get() == "local" and typed and os.path.exists(typed):
                self.scan_local_directory_into_queue(typed, show_empty_popup=True)
            elif self.target_mode_var.get() == "local":
                if messagebox.askyesno(
                    "Select Media Directory",
                    "No videos are in the queue yet.\n\nWould you like to choose a media folder/directory to transcode now?"
                ):
                    self.add_folder()

        if not self.queue_items:
            return

        is_remote = (self.target_mode_var.get() == "remote")
        if is_remote and not self.remote_manager.is_connected:
            messagebox.showerror("Not Connected", "Please connect to the remote computer first before starting transcode.")
            return

        self.is_transcoding = True
        self.cancel_requested = False
        self.is_paused = False

        self.update_start_button_state(text="⏹ Stop Transcode", bg=Theme.DANGER, fg="#ffffff")
        self.btn_pause.config(state="normal", text="⏸ Pause Queue", bg=Theme.BORDER, fg=Theme.TEXT)

        # Build Work Queue
        self.work_queue = queue.Queue()
        for it in self.queue_items:
            if it["status"] not in ("✔ Complete", "⏭ Skipped (Compliant)", "⏭ Skipped"):
                it["status"] = "⏳ Queued (Remote)" if is_remote else "⏳ Queued"
                self.work_queue.put(it)

        self.stats["start_time"] = time.time()
        self.worker_threads.clear()

        # Capture settings safely on main UI thread
        job_settings = {
            "cq": int(self.cq_slider.get()),
            "preset": self.preset_var.get().split()[0],
            "audio": self.audio_var.get().split()[0],
            "container": self.container_var.get().split()[0].lower(),
            "skip_hevc": bool(self.skip_hevc_var.get()),
            "in_place": bool(self.in_place_var.get()),
            "out_dir": self.out_dir_var.get().strip(),
            "mode": self.mode_var.get()
        }

        if is_remote:
            # Remote transcode workers
            remote_host = self.remote_manager.config.host if self.remote_manager.config else "Remote"
            remote_gpus = [d for d in self.remote_manager.remote_devices if d.vendor != "CPU"]
            mode = self.mode_var.get()

            if mode == "dual_gpu" and len(remote_gpus) >= 2:
                # Concurrent dual remote GPU workers!
                wcfgs = [
                    {"id": 0, "name": remote_gpus[0].name, "gpu_id": remote_gpus[0].index, "encoder": remote_gpus[0].encoder, "extra_args": remote_gpus[0].extra_args},
                    {"id": 1, "name": remote_gpus[1].name, "gpu_id": remote_gpus[1].index, "encoder": remote_gpus[1].encoder, "extra_args": remote_gpus[1].extra_args}
                ]
                self.card_worker1.title_lbl.config(text=f"⚡ REMOTE WORKER 1 ({remote_gpus[0].name})")
                self.card_worker1.dev_lbl.config(text=f"{remote_gpus[0].encoder} [GPU {remote_gpus[0].index}]")
                self.card_worker2.title_lbl.config(text=f"⚡ REMOTE WORKER 2 ({remote_gpus[1].name})")
                self.card_worker2.dev_lbl.config(text=f"{remote_gpus[1].encoder} [GPU {remote_gpus[1].index}]")
                self.card_worker2.pack(side="left", fill="x", expand=True, padx=(6, 0))

                for idx, wcfg in enumerate(wcfgs):
                    t = threading.Thread(
                        target=self.remote_worker_thread_proc,
                        args=(idx, wcfg, job_settings),
                        name=f"RemoteWorker-{idx+1}",
                        daemon=True
                    )
                    t.start()
                    self.worker_threads.append(t)
            else:
                # Single remote worker
                wcfg = {"id": 0, "name": "Remote Engine", "encoder": "auto"}
                if mode.startswith("remote_gpu_"):
                    try:
                        g_idx = int(mode.split("_")[-1])
                        target_g = self.remote_manager.remote_devices[g_idx]
                        wcfg = {
                            "id": 0,
                            "name": target_g.name,
                            "gpu_id": target_g.index,
                            "encoder": target_g.encoder,
                            "extra_args": target_g.extra_args,
                            "device_node": target_g.device_node
                        }
                    except Exception:
                        pass
                elif mode == "cpu":
                    wcfg = {"id": 0, "name": "Remote CPU", "encoder": "libx265"}

                self.card_worker1.title_lbl.config(text=f"🌐 REMOTE WORKER ({remote_host})")
                self.card_worker1.dev_lbl.config(text=wcfg.get("name", "Auto"))
                self.card_worker2.pack_forget()

                t = threading.Thread(
                    target=self.remote_worker_thread_proc,
                    args=(0, wcfg, job_settings),
                    name="RemoteWorker-1",
                    daemon=True
                )
                t.start()
                self.worker_threads.append(t)
        else:
            # Local multi-GPU workers
            mode = self.mode_var.get()
            workers = self.build_worker_configs(mode)

            # Update card displays
            if len(workers) >= 1:
                self.card_worker1.title_lbl.config(text=f"⚡ WORKER 1 ({workers[0]['name']})")
                self.card_worker1.dev_lbl.config(text=workers[0]["encoder"])
            if len(workers) >= 2:
                self.card_worker2.title_lbl.config(text=f"⚡ WORKER 2 ({workers[1]['name']})")
                self.card_worker2.dev_lbl.config(text=workers[1]["encoder"])
                self.card_worker2.pack(side="left", fill="x", expand=True, padx=(6, 0))
            else:
                self.card_worker2.pack_forget()

            # Launch Worker Threads
            for idx, wcfg in enumerate(workers):
                t = threading.Thread(
                    target=self.worker_thread_proc,
                    args=(idx, wcfg, job_settings),
                    name=f"Worker-{idx+1}",
                    daemon=True
                )
                t.start()
                self.worker_threads.append(t)

        # Monitor thread to detect batch completion
        threading.Thread(target=self.monitor_batch_completion, daemon=True).start()

    def remote_worker_thread_proc(self, worker_idx: int, wcfg: Dict[str, Any], settings: Dict[str, Any]):
        """Runs transcode queue items directly on the remote machine across SSH."""
        card_widget = self.card_worker1 if worker_idx == 0 else self.card_worker2
        remote_host = self.remote_manager.config.host if self.remote_manager.config else "Remote"

        while not self.cancel_requested:
            while self.is_paused and not self.cancel_requested:
                time.sleep(0.5)

            try:
                item = self.work_queue.get_nowait()
            except queue.Empty:
                break

            file_path = item["path"]
            item["worker"] = f"{wcfg['name']} ({remote_host})"
            item["status"] = f"⚡ Transcoding ({wcfg.get('encoder', 'hevc_nvenc')})"
            self.post_ui(self.update_item_row, item)

            start_t = time.time()

            def on_remote_progress(telemetry):
                frac = telemetry.get("progress", 0.0)
                speed = telemetry.get("speed", "N/A")
                fps = telemetry.get("fps", "0")
                bitrate = telemetry.get("bitrate", "N/A")
                out_sec = 0.0
                try:
                    out_sec = float(telemetry.get("out_sec", 0.0))
                except ValueError:
                    pass
                tot_sec = 0.0
                try:
                    tot_sec = float(telemetry.get("total_sec", 0.0))
                except ValueError:
                    pass

                elapsed_str = format_duration(time.time() - start_t)
                eta_str = telemetry.get("eta", "--:--:--")
                if eta_str == "--:--:--" and speed not in ("N/A", ""):
                    try:
                        sp_val = float(speed.rstrip("x"))
                        if sp_val > 0.05 and tot_sec > out_sec:
                            eta_str = format_duration((tot_sec - out_sec) / sp_val)
                    except Exception:
                        pass

                self.post_ui(
                    card_widget.update_telemetry,
                    item["filename"],
                    frac,
                    speed,
                    fps,
                    bitrate,
                    elapsed_str,
                    eta_str
                )

            def on_remote_result(res):
                st = res.get("status")
                if st == "complete":
                    item["new_size"] = res.get("new_size", item["orig_size"])
                    item["saved_bytes"] = res.get("saved_bytes", 0)
                    item["saved_pct"] = res.get("saved_pct", 0.0)
                elif st == "skipped":
                    item["new_size"] = item["orig_size"]
                    item["saved_bytes"] = 0
                    item["saved_pct"] = 0.0

            success, res_dict = self.remote_manager.run_remote_transcode(
                file_path=file_path,
                settings=settings,
                worker_cfg=wcfg,
                on_progress=on_remote_progress,
                on_result=on_remote_result
            )

            if self.cancel_requested:
                item["status"] = "⏹ Cancelled"
            elif success:
                if res_dict.get("status") == "skipped":
                    item["status"] = "⏭ Skipped (Compliant)"
                    item["new_size"] = item["orig_size"]
                else:
                    item["status"] = "✔ Complete"
                    if res_dict.get("new_size"):
                        item["new_size"] = res_dict["new_size"]
                        item["saved_bytes"] = res_dict.get("saved_bytes", 0)
                        item["saved_pct"] = res_dict.get("saved_pct", 0.0)
            else:
                item["status"] = "✖ Failed"

            self.post_ui(self.update_item_row, item)
            self.post_ui(self.update_batch_stats)
            self.work_queue.task_done()

        self.post_ui(card_widget.set_idle, "Status: Idle / Ready")

    def build_worker_configs(self, mode: str) -> List[Dict[str, Any]]:
        """Construct hardware worker definitions based on user selection."""
        gpu_cards = [d for d in self.detected_devices if d.vendor != "CPU"]
        cpu_card = next((d for d in self.detected_devices if d.vendor == "CPU"), None)

        if mode == "dual_gpu" and len(gpu_cards) >= 2:
            return [
                {
                    "id": 0,
                    "name": gpu_cards[0].name,
                    "encoder": gpu_cards[0].encoder,
                    "extra_args": gpu_cards[0].extra_args,
                    "device_node": gpu_cards[0].device_node
                },
                {
                    "id": 1,
                    "name": gpu_cards[1].name,
                    "encoder": gpu_cards[1].encoder,
                    "extra_args": gpu_cards[1].extra_args,
                    "device_node": gpu_cards[1].device_node
                }
            ]
        elif mode == "all":
            cfgs = []
            for idx, g in enumerate(gpu_cards[:2]):
                cfgs.append({
                    "id": idx,
                    "name": g.name,
                    "encoder": g.encoder,
                    "extra_args": g.extra_args,
                    "device_node": g.device_node
                })
            if cpu_card:
                cfgs.append({
                    "id": len(cfgs),
                    "name": "Multi-Core CPU",
                    "encoder": cpu_card.encoder,
                    "extra_args": [],
                    "device_node": None
                })
            return cfgs
        elif mode.startswith("gpu_"):
            try:
                g_idx = int(mode.split("_")[1])
                target = self.detected_devices[g_idx]
                return [{
                    "id": 0,
                    "name": target.name,
                    "encoder": target.encoder,
                    "extra_args": target.extra_args,
                    "device_node": target.device_node
                }]
            except Exception:
                pass
        elif mode == "cpu":
            enc = cpu_card.encoder if cpu_card else "libx265"
            return [{
                "id": 0,
                "name": "Software Multi-Core CPU",
                "encoder": enc,
                "extra_args": [],
                "device_node": None
            }]

        # Default fallback: pick best GPU or CPU
        if gpu_cards:
            return [{
                "id": 0,
                "name": gpu_cards[0].name,
                "encoder": gpu_cards[0].encoder,
                "extra_args": gpu_cards[0].extra_args,
                "device_node": gpu_cards[0].device_node
            }]
        return [{
            "id": 0,
            "name": "Software CPU",
            "encoder": "libx265",
            "extra_args": [],
            "device_node": None
        }]

    def worker_thread_proc(self, worker_idx: int, wcfg: Dict[str, Any], settings: Dict[str, Any]):
        """Runs transcode jobs from queue on assigned hardware ASIC."""
        card_widget = self.card_worker1 if worker_idx == 0 else self.card_worker2

        while not self.cancel_requested:
            while self.is_paused and not self.cancel_requested:
                time.sleep(0.5)

            try:
                item = self.work_queue.get_nowait()
            except queue.Empty:
                break

            file_path = item["path"]
            item["worker"] = wcfg["name"]
            item["status"] = f"⚡ Transcoding ({wcfg['encoder']})"
            self.post_ui(self.update_item_row, item)

            # Probe media
            probe = self.probe_video(file_path)
            item["duration"] = probe["duration"]
            item["subtitles"] = probe.get("subtitles", [])

            # Skip if already HEVC/AV1 and checkbox is set
            if settings.get("skip_hevc") and probe["vcodec"].lower() in ("hevc", "h265", "av1"):
                item["status"] = "⏭ Skipped (Compliant)"
                item["new_size"] = item["orig_size"]
                self.post_ui(self.update_item_row, item)
                self.work_queue.task_done()
                continue

            # Execute Transcode
            success = self.execute_single_transcode(worker_idx, wcfg, item, card_widget, settings)

            if success:
                item["status"] = "✔ Complete"
            elif self.cancel_requested:
                item["status"] = "⏹ Cancelled"
            else:
                item["status"] = "✖ Failed"

            self.post_ui(self.update_item_row, item)
            self.post_ui(self.update_batch_stats)
            self.work_queue.task_done()

        self.post_ui(card_widget.set_idle, "Status: Idle / Ready")

    def execute_single_transcode(
        self,
        worker_idx: int,
        wcfg: Dict[str, Any],
        item: Dict[str, Any],
        card_widget: WorkerStatusCard,
        settings: Dict[str, Any]
    ) -> bool:
        file_path = item["path"]
        orig_size = item["orig_size"]
        duration = item["duration"]

        cq = settings.get("cq", 22)
        preset_choice = settings.get("preset", "p4")
        audio_mode = settings.get("audio", "copy")

        # Destination file setup
        dir_name = os.path.dirname(file_path)
        base_name, in_ext = os.path.splitext(os.path.basename(file_path))
        out_dir = settings.get("out_dir") or dir_name

        container_choice = settings.get("container", "auto")
        if container_choice == "mkv":
            out_ext = ".mkv"
        elif container_choice == "mp4":
            out_ext = ".mp4"
        else:  # auto / same
            out_ext = in_ext.lower() if in_ext.lower() in [".mkv", ".mp4"] else ".mp4"

        final_output = os.path.join(out_dir, f"{base_name}{out_ext}")
        temp_output = os.path.join(out_dir, f"{base_name}.transcoding_{worker_idx}.tmp{out_ext}")

        # Build FFmpeg command
        cmd = [self.ffmpeg_path, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1"]

        # Hardware acceleration input flags
        if wcfg.get("device_node") and "vaapi" in wcfg["encoder"]:
            cmd.extend(["-vaapi_device", wcfg["device_node"]])

        cmd.extend(["-i", file_path])

        # Video filter & hardware pixel formats
        vf_filters = []
        if wcfg["encoder"] == "hevc_qsv":
            vf_filters.append("format=nv12")
        elif wcfg["encoder"] == "hevc_vaapi":
            vf_filters.append("format=nv12,hwupload")

        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])

        # Encoder selection
        cmd.extend(["-c:v", wcfg["encoder"]])

        # Rate control / Quality & Preset
        if wcfg["encoder"] == "hevc_nvenc":
            cmd.extend(["-cq:v", str(cq), "-preset", preset_choice])
        elif wcfg["encoder"] == "hevc_qsv":
            cmd.extend(["-global_quality", str(cq), "-preset", "medium"])
        elif wcfg["encoder"] == "hevc_amf":
            cmd.extend(["-rc", "cqp", "-qp_p", str(cq), "-qp_i", str(cq)])
        elif wcfg["encoder"] == "hevc_vaapi":
            cmd.extend(["-qp", str(cq)])
        elif wcfg["encoder"] == "hevc_videotoolbox":
            cmd.extend(["-q:v", str(cq)])
        else:  # libx265 CPU
            cmd.extend(["-crf", str(cq), "-preset", "medium", "-threads", "0"])

        # Extra hardware args (e.g. -gpu 0, -gpu 1)
        if wcfg.get("extra_args"):
            for arg in wcfg["extra_args"]:
                if arg not in cmd:
                    cmd.append(arg)

        # Explicit stream mapping: primary video and audio
        cmd.extend(["-map", "0:v:0", "-map", "0:a?"])

        # Audio & Subtitle stream handling
        sub_list = item.get("subtitles", [])
        if out_ext == ".mkv":
            cmd.extend(["-c:a", "copy"])
            if sub_list:
                cmd.extend(["-map", "0:s?", "-c:s", "copy"])
            else:
                cmd.extend(["-sn"])
        else:
            if audio_mode == "copy":
                cmd.extend(["-c:a", "copy"])
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])

            text_subs = [s for s in sub_list if not s.get("is_bitmap")]
            if text_subs:
                for ts in text_subs:
                    cmd.extend(["-map", f"0:{ts['index']}"])
                cmd.extend(["-c:s", "mov_text"])
            else:
                cmd.extend(["-sn"])
            cmd.extend(["-movflags", "+faststart"])

        cmd.append(temp_output)

        start_time = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )
            self.active_procs[worker_idx] = proc

            # Read progress
            cur_kv = {}
            for line in proc.stdout:
                if self.cancel_requested:
                    proc.terminate()
                    break
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    cur_kv[k.strip()] = v.strip()
                    if k.strip() == "progress":
                        out_sec = 0.0
                        if "out_time_us" in cur_kv and cur_kv["out_time_us"].lstrip("-").isdigit():
                            out_sec = float(cur_kv["out_time_us"]) / 1_000_000.0
                        elif "out_time_ms" in cur_kv and cur_kv["out_time_ms"].lstrip("-").isdigit():
                            out_sec = float(cur_kv["out_time_ms"]) / 1_000.0

                        fraction = (out_sec / duration) if duration > 0 else 0.0
                        elapsed_s = time.time() - start_time
                        elapsed_str = format_duration(elapsed_s)

                        speed_str = cur_kv.get("speed", "N/A")
                        fps_str = cur_kv.get("fps", "0")
                        bitrate_str = cur_kv.get("bitrate", "N/A")

                        eta_str = "--:--:--"
                        try:
                            sp_val = float(speed_str.rstrip("x"))
                            if sp_val > 0.05 and duration > out_sec:
                                rem_sec = (duration - out_sec) / sp_val
                                eta_str = format_duration(rem_sec)
                        except Exception:
                            pass

                        self.post_ui(
                            card_widget.update_telemetry,
                            item["filename"],
                            fraction,
                            speed_str,
                            fps_str,
                            bitrate_str,
                            elapsed_str,
                            eta_str
                        )

            proc.wait()
            if self.active_procs.get(worker_idx) == proc:
                del self.active_procs[worker_idx]

            if proc.returncode != 0 or self.cancel_requested:
                if os.path.exists(temp_output):
                    try:
                        os.remove(temp_output)
                    except Exception:
                        pass
                return False

            if not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
                return False

            # Verify and atomically finalize
            new_size = os.path.getsize(temp_output)
            saved_bytes = max(0, orig_size - new_size)
            ratio_pct = (saved_bytes / orig_size) * 100.0 if orig_size > 0 else 0.0

            item["new_size"] = new_size
            item["saved_bytes"] = saved_bytes
            item["saved_pct"] = ratio_pct

            if settings.get("in_place"):
                if file_path == final_output:
                    os.remove(file_path)
                shutil.move(temp_output, final_output)
                if file_path != final_output and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
            else:
                if os.path.exists(final_output) and final_output != temp_output:
                    os.remove(final_output)
                shutil.move(temp_output, final_output)

            return True

        except Exception as e:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception:
                    pass
            return False

    def update_item_row(self, item: Dict[str, Any]):
        """Update row in Treeview."""
        iid = str(item["idx"])
        if self.tree.exists(iid):
            saved_str = f"-{item['saved_pct']:.1f}%" if item['saved_bytes'] > 0 else "-"
            new_sz_str = format_bytes(item["new_size"]) if item["new_size"] > 0 else "-"
            self.tree.item(
                iid,
                values=(
                    item["idx"],
                    item["status"],
                    item["filename"],
                    format_bytes(item["orig_size"]),
                    new_sz_str,
                    saved_str,
                    item["worker"],
                    item["path"]
                )
            )

    def monitor_batch_completion(self):
        """Monitors worker threads and updates UI on completion."""
        for t in self.worker_threads:
            t.join()

        self.post_ui(self.finish_batch_ui)

    def finish_batch_ui(self):
        self.is_transcoding = False
        is_remote = (self.target_mode_var.get() == "remote")
        self.update_start_button_state()
        self.btn_pause.config(state="disabled", text="⏸ Pause Queue", bg=Theme.BORDER, fg=Theme.TEXT)

        self.update_batch_stats()
        if not self.cancel_requested:
            self.overall_bar.update_progress(1.0, "LVL MAX", "100.0%")
            target_str = "remote host" if is_remote else "all GPU engines"
            messagebox.showinfo("Transcode Complete", f"Batch transcoding finished successfully across {target_str}!")
        else:
            self.status_bar_lbl.config(text="Batch transcode was cancelled by user.")

    def periodic_ui_update(self):
        """Periodic UI refresh loop processing queued UI actions on the main thread."""
        while not self.ui_queue.empty():
            try:
                fn, args = self.ui_queue.get_nowait()
                fn(*args)
            except Exception:
                pass
        self.after(50, self.periodic_ui_update)


# ==============================================================================
# Main Launcher
# ==============================================================================
def main():
    app = TranscoderStudioGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
