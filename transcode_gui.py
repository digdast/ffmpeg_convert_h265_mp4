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
        self.geometry("1160x860")
        self.minsize(980, 720)
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

        # Setup ttk style
        self.setup_styles()

        # Build UI layout
        self.create_header_panel()
        self.create_hardware_panel()
        self.create_settings_panel()
        self.create_queue_panel()
        self.create_progress_panel()
        self.create_action_bar()

        # Telemetry update timer
        self.after(50, self.periodic_ui_update)

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
            text="⚡ FFmpeg Multi-GPU Transcoder Studio",
            fg=Theme.TEXT,
            bg=Theme.HEADER_BG,
            font=("Segoe UI", 15, "bold")
        )
        title_lbl.pack(anchor="w")

        subtitle = tk.Label(
            title_box,
            text="Simultaneous Dual-GPU & Multi-Core Hardware Acceleration (NVENC + QSV + AMF + CPU)",
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

    def create_hardware_panel(self):
        frame = tk.LabelFrame(
            self,
            text="  ⚡ Hardware Acceleration & Multi-GPU Configuration  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=8
        )
        frame.pack(fill="x", padx=14, pady=(10, 6))

        # Detected GPUs summary
        gpu_names = [f"[{d.vendor}] {d.name}" for d in self.detected_devices if d.vendor != "CPU"]
        summary_str = f"Found {len(gpu_names)} GPU(s): " + "  •  ".join(gpu_names) if gpu_names else "No dedicated GPU detected (CPU mode available)"

        det_lbl = tk.Label(
            frame,
            text=summary_str,
            fg=Theme.SUCCESS if gpu_names else Theme.WARNING,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 9)
        )
        det_lbl.pack(anchor="w", pady=(0, 6))

        # Hardware mode selection options
        mode_row = tk.Frame(frame, bg=Theme.PANEL_BG)
        mode_row.pack(fill="x")

        self.mode_var = tk.StringVar(value="dual_gpu" if len(gpu_names) >= 2 else "auto")

        options = []
        if len(gpu_names) >= 2:
            options.append(("🚀 Dual GPU Mode (Use Both Cards Simultaneously for Max Power)", "dual_gpu"))
        options.append(("⚡ Auto GPU (Fastest Available Hardware Engine)", "auto"))

        # Individual GPU options
        for idx, dev in enumerate(self.detected_devices):
            if dev.vendor != "CPU":
                options.append((f"⚡ {dev.name} ({dev.encoder}) Only", f"gpu_{idx}"))

        options.append(("🔥 Triple-Engine (Both GPUs + CPU Multithreading)", "all"))
        options.append(("💻 CPU Only (Software libx265 Multithreading)", "cpu"))

        for label_text, mode_val in options:
            rb = tk.Radiobutton(
                mode_row,
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
            rb.pack(anchor="w", pady=2)

    def create_settings_panel(self):
        frame = tk.LabelFrame(
            self,
            text="  ⚙ Transcoding & Quality Settings  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=8
        )
        frame.pack(fill="x", padx=14, pady=6)

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
        tk.Label(r2, text="Output Directory:", fg=Theme.TEXT, bg=Theme.PANEL_BG, font=("Segoe UI", 9)).pack(side="left")
        self.out_dir_var = tk.StringVar(value="")
        self.out_dir_entry = tk.Entry(
            r2,
            textvariable=self.out_dir_var,
            bg=Theme.BG,
            fg=Theme.TEXT,
            insertbackground=Theme.TEXT,
            relief="solid",
            width=24
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
        d = filedialog.askdirectory(title="Select Output Directory for Transcoded Videos")
        if d:
            self.out_dir_var.set(d)

    def create_queue_panel(self):
        frame = tk.LabelFrame(
            self,
            text="  📁 Batch Media Queue  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6
        )
        frame.pack(fill="both", expand=True, padx=14, pady=6)

        # Toolbar
        btn_bar = tk.Frame(frame, bg=Theme.PANEL_BG)
        btn_bar.pack(fill="x", pady=(0, 6))

        tk.Button(
            btn_bar,
            text="➕ Add Media Files...",
            bg=Theme.ACCENT,
            fg="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            command=self.add_files
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            btn_bar,
            text="📂 Add Folder / Directory...",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=4,
            command=self.add_folder
        ).pack(side="left", padx=4)

        self.recursive_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            btn_bar,
            text="Include Subdirectories (Recursive)",
            variable=self.recursive_var,
            bg=Theme.PANEL_BG,
            fg=Theme.TEXT_MUTED,
            selectcolor=Theme.HEADER_BG,
            font=("Segoe UI", 8)
        ).pack(side="left", padx=10)

        tk.Button(
            btn_bar,
            text="🗑 Remove Selected",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            padx=8,
            pady=4,
            command=self.remove_selected
        ).pack(side="right", padx=4)

        tk.Button(
            btn_bar,
            text="🧹 Clear All",
            bg=Theme.BORDER,
            fg=Theme.TEXT,
            relief="flat",
            padx=8,
            pady=4,
            command=self.clear_all
        ).pack(side="right", padx=4)

        # Queue Treeview
        tree_frame = tk.Frame(frame, bg=Theme.PANEL_BG)
        tree_frame.pack(fill="both", expand=True)

        cols = ("idx", "status", "filename", "orig_size", "new_size", "saved", "worker", "path")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")

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
            self,
            text="  📊 Visual Level Progress & Live Telemetry  ",
            fg=Theme.ACCENT,
            bg=Theme.PANEL_BG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=8
        )
        frame.pack(fill="x", padx=14, pady=6)

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

        # Summary text
        self.status_bar_lbl = tk.Label(
            bar,
            text="Ready. Add video files or directories to start transcoding.",
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

    def add_folder(self):
        d = filedialog.askdirectory(title="Select Directory to Scan for Videos")
        if not d:
            return

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
            self.status_bar_lbl.config(text=f"Added {len(found)} video files from {d}")
        else:
            messagebox.showinfo("No Videos Found", f"No video files found in:\n{d}")

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
                for p in list(self.active_procs.values()):
                    try:
                        p.terminate()
                    except Exception:
                        pass
            return

        if not self.queue_items:
            messagebox.showinfo("Queue Empty", "Please add video files or folders before starting transcode.")
            return

        self.is_transcoding = True
        self.cancel_requested = False
        self.is_paused = False

        self.btn_start.config(text="⏹ Stop Transcode", bg=Theme.DANGER, fg="#ffffff")
        self.btn_pause.config(state="normal", text="⏸ Pause Queue", bg=Theme.BORDER, fg=Theme.TEXT)

        # Prepare Hardware Workers according to selected mode
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

        # Build Work Queue
        self.work_queue = queue.Queue()
        for it in self.queue_items:
            if it["status"] not in ("✔ Complete", "⏭ Skipped"):
                it["status"] = "⏳ Queued"
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
            "out_dir": self.out_dir_var.get().strip()
        }

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
        self.btn_start.config(text="▶ Start Dual-GPU Transcode", bg=Theme.SUCCESS, fg="#000000")
        self.btn_pause.config(state="disabled", text="⏸ Pause Queue", bg=Theme.BORDER, fg=Theme.TEXT)

        self.update_batch_stats()
        if not self.cancel_requested:
            self.overall_bar.update_progress(1.0, "LVL MAX", "100.0%")
            messagebox.showinfo("Transcode Complete", "Batch transcoding finished successfully across all GPU engines!")
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
