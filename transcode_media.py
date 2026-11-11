#!/usr/bin/env python3
"""
================================================================================
⚡ GPU-Accelerated H.265 (HEVC) & MP4 Batch Media Transcoder
================================================================================
High-performance media transcoding pipeline utilizing hardware-accelerated
video encoding (NVIDIA NVENC, Intel QSV, Apple VideoToolbox, VAAPI, AMD AMF)
with multi-threaded CPU encoding (libx265 / libx264) and Dual-Engine (GPU+CPU)
parallel execution.

Features:
- Real-time graduated Level Progress Bar with speed, FPS, ETA, bitrate & level meters
- Acceleration Modes:
    * --mode gpu  (or --gpu): Force hardware GPU encoding & decoding
    * --mode cpu  (or --cpu): Force multi-threaded software CPU encoding
    * --mode both (or --both): Dual-Engine concurrent GPU + CPU parallel execution
    * --mode auto: Auto-detect best hardware encoder with CPU fallback
- Safe atomic encoding via temp files (preserves original on failure or interrupt)
- Media preservation: multi-track audio copy/transcode, subtitles, and metadata
- FastStart streaming optimization (`-movflags +faststart`)
- Space savings analytics: tracks before/after file sizes and percentage reduction
- Dry-run mode, recursive scanning, skip already-compliant H.265 files
================================================================================
"""

import argparse
import glob
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Ensure stdout and stderr handle utf-8 safely on Windows/Linux
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Terminal ANSI styling
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


VIDEO_EXTENSIONS = (
    ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".ts", ".mts", ".m2ts", ".vob", ".mpg", ".mpeg", ".mp4"
)


def get_binary_path(binary_name: str) -> str:
    """Resolve binary executable path, with Windows fallback lookups."""
    found = shutil.which(binary_name)
    if found:
        return found
    if os.name == "nt":
        ext = ".exe" if not binary_name.lower().endswith(".exe") else ""
        candidates = [
            rf"C:\ffmpeg\bin\{binary_name}{ext}",
            rf"C:\ProgramData\chocolatey\bin\{binary_name}{ext}",
            os.path.expandvars(rf"%LOCALAPPDATA%\ffmpeg-transcoder\{binary_name}{ext}"),
            os.path.expandvars(rf"%ProgramFiles%\ffmpeg\bin\{binary_name}{ext}"),
        ]
        # Check WinGet package directories recursively
        winget_pattern = os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\**\{binary_name}{ext}")
        candidates.extend(glob.glob(winget_pattern, recursive=True))
        for c in candidates:
            if os.path.isfile(c):
                return c
    return binary_name


def format_bytes(num_bytes: float) -> str:
    """Format bytes into human-readable binary prefix."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    seconds = max(0.0, seconds)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ==============================================================================
# 📊 Graduated Level Progress Bar
# ==============================================================================
class LevelProgressBar:
    """
    Renders an interactive, graduated level progress bar.
    Displays:
    - 10-level graduated meter [LVL 1/10] through [LVL MAX]
    - Dynamic segmented fill: [██████████░░░░░░░░░░] or [▰▰▰▰▰▱▱▱▱▱]
    - Stage color thresholds (Cyan -> Yellow -> Blue -> Lime -> Bold Green)
    - Real-time telemetry: Speed multiplier (⚡ 8.4x), FPS, ETA, elapsed/total time, bitrate
    - Dual-level queue indicator for batch jobs
    """
    def __init__(
        self,
        total_duration: float,
        file_name: str,
        file_idx: int = 1,
        total_files: int = 1,
        engine_label: str = "GPU",
        style: str = "level",
        print_lock: Optional[threading.Lock] = None
    ):
        self.total_duration = max(0.001, total_duration)
        self.file_name = file_name
        self.file_idx = file_idx
        self.total_files = total_files
        self.engine_label = engine_label
        self.style = style
        self.print_lock = print_lock
        self.start_time = time.time()
        self.last_render_time = 0.0
        self.last_pct = 0.0
        self.last_non_tty_milestone = -1
        self.is_tty = sys.stdout.isatty() if sys.stdout else False

    def get_stage_color(self, pct: float) -> str:
        if pct >= 99.5:
            return Colors.GREEN + Colors.BOLD
        elif pct >= 75.0:
            return Colors.GREEN
        elif pct >= 50.0:
            return Colors.CYAN
        elif pct >= 25.0:
            return Colors.YELLOW
        else:
            return Colors.BLUE

    def render(
        self,
        out_seconds: float,
        speed_str: str = "N/A",
        fps_str: str = "0",
        bitrate_str: str = "N/A",
        current_bytes: int = 0,
        force: bool = False
    ):
        now = time.time()
        if not force and (now - self.last_render_time < 0.08):
            return
        self.last_render_time = now

        pct = min(100.0, max(0.0, (out_seconds / self.total_duration) * 100.0))
        self.last_pct = pct

        # Level calculation (1 to 10 or MAX)
        level_num = min(10, max(1, int(pct // 10) + (1 if (pct % 10 > 0.01 and pct < 99.0) else 0)))
        if pct >= 99.9:
            lvl_tag = f"{Colors.GREEN}{Colors.BOLD}[LVL MAX]{Colors.RESET}"
        else:
            lvl_tag = f"{Colors.YELLOW}[LVL {level_num:2d}/10]{Colors.RESET}"

        # Segmented Level Bar (20 characters)
        bar_len = 20
        filled = int(round(bar_len * (pct / 100.0)))
        unfilled = max(0, bar_len - filled)
        stage_color = self.get_stage_color(pct)

        if self.style == "level":
            bar_str = f"{stage_color}{'▰' * filled}{Colors.DIM}{'▱' * unfilled}{Colors.RESET}"
        elif self.style == "blocks":
            bar_str = f"{stage_color}{'█' * filled}{Colors.DIM}{'░' * unfilled}{Colors.RESET}"
        else:
            bar_str = f"{stage_color}{'=' * filled}{Colors.DIM}{'-' * unfilled}{Colors.RESET}"

        # Timestamps
        time_str = f"{format_duration(out_seconds)}/{format_duration(self.total_duration)}"

        # Dynamic ETA
        elapsed = now - self.start_time
        if pct > 0.5:
            est_total = elapsed / (pct / 100.0)
            remaining = max(0, est_total - elapsed)
            eta_str = format_duration(remaining)
        else:
            eta_str = "--:--"

        # Speed indicator
        clean_speed = speed_str.strip()
        if clean_speed and clean_speed != "N/A":
            speed_disp = f"{Colors.GREEN}⚡ {clean_speed}{Colors.RESET}"
        else:
            speed_disp = f"{Colors.DIM}N/A{Colors.RESET}"

        # Batch Queue Level Indicator
        queue_tag = ""
        if self.total_files > 1:
            q_pct = ((self.file_idx - 1) / self.total_files) * 100.0
            q_lvl = min(10, max(1, int(q_pct // 10) + 1))
            queue_tag = f"{Colors.DIM}[Queue {self.file_idx}/{self.total_files} | LVL {q_lvl}/10]{Colors.RESET} "

        engine_tag = f"{Colors.CYAN}[{self.engine_label}]{Colors.RESET}"

        if self.is_tty:
            # Build TTY in-place update line
            line = (
                f"\r\033[K {queue_tag}{engine_tag} "
                f"Level: [{bar_str}] {stage_color}{pct:5.1f}%{Colors.RESET} {lvl_tag} "
                f"{speed_disp} | {fps_str} fps | ETA: {eta_str} | {time_str}"
            )
            # Ensure line does not wrap terminal columns
            term_cols = shutil.get_terminal_size((100, 24)).columns
            if len(re.sub(r'\x1b\[[0-9;]*m', '', line)) > term_cols:
                line = (
                    f"\r\033[K {engine_tag} [{bar_str}] {stage_color}{pct:5.1f}%{Colors.RESET} "
                    f"{lvl_tag} {speed_disp} | ETA: {eta_str} | {time_str}"
                )

            if self.print_lock:
                with self.print_lock:
                    sys.stdout.write(line)
                    sys.stdout.flush()
            else:
                sys.stdout.write(line)
                sys.stdout.flush()
        else:
            # Non-TTY clean milestone output (every 10%)
            milestone = int(pct // 10)
            if milestone > self.last_non_tty_milestone or force:
                self.last_non_tty_milestone = milestone
                plain_line = (
                    f"  {queue_tag}{engine_tag} Progress: {pct:5.1f}% [LVL {level_num}/10] | "
                    f"Speed: {clean_speed} | FPS: {fps_str} | ETA: {eta_str} | {time_str}\n"
                )
                if self.print_lock:
                    with self.print_lock:
                        sys.stdout.write(plain_line)
                        sys.stdout.flush()
                else:
                    sys.stdout.write(plain_line)
                    sys.stdout.flush()

    def finish(self):
        self.render(self.total_duration, speed_str="Done", force=True)
        if self.is_tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


# ==============================================================================
# 🔍 Hardware Detector & Encoder Selector
# ==============================================================================
class HardwareDetector:
    """Detects available hardware accelerators, GPUs (Intel, NVIDIA, AMD, Apple), and encoders."""

    @staticmethod
    def get_available_encoders() -> List[str]:
        """Query ffmpeg for available video encoders."""
        ffmpeg_bin = get_binary_path("ffmpeg")
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-encoders"],
                capture_output=True,
                text=True,
                check=True
            )
            encoders = []
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    encoders.append(parts[1])
            return encoders
        except Exception:
            return ["libx265", "libx264"]

    @staticmethod
    def get_drm_devices() -> List[Dict]:
        """Inspect Linux DRM render nodes (/dev/dri/by-path and /dev/dri/renderD*)."""
        devices = []
        if not os.path.exists("/dev/dri"):
            return devices

        by_path_dir = "/dev/dri/by-path"
        if os.path.exists(by_path_dir):
            for entry in os.listdir(by_path_dir):
                if entry.endswith("-render"):
                    full_sym = os.path.join(by_path_dir, entry)
                    real_node = os.path.realpath(full_sym)
                    node_name = os.path.basename(real_node)
                    pci_match = re.search(r"pci-[0-9a-fA-F]{4}:([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])", entry)
                    pci_addr = pci_match.group(1) if pci_match else ""

                    device_name = "Unknown DRM Device"
                    is_intel_arc = False
                    is_intel_igpu = False

                    if pci_addr:
                        lspci_cmd = f"lspci -s {pci_addr} -v 2>/dev/null"
                        try:
                            lspci_out = subprocess.check_output(lspci_cmd, shell=True, text=True)
                            for line in lspci_out.splitlines():
                                if "VGA compatible controller" in line or "Display controller" in line:
                                    device_name = line.split(":", 2)[-1].strip()
                                    break
                            lower_name = device_name.lower()
                            if "arc" in lower_name or "battlemage" in lower_name or "alchemist" in lower_name:
                                is_intel_arc = True
                            elif "intel" in lower_name and ("uhd" in lower_name or "iris" in lower_name or "graphics" in lower_name):
                                is_intel_igpu = True
                        except Exception:
                            pass

                    devices.append({
                        "node": real_node,
                        "name": node_name,
                        "pci": pci_addr,
                        "label": device_name,
                        "is_intel_arc": is_intel_arc,
                        "is_intel_igpu": is_intel_igpu
                    })

        # Fallback to direct /dev/dri/renderD* enumeration
        if not devices:
            for node_name in sorted(os.listdir("/dev/dri")):
                if node_name.startswith("renderD"):
                    full_path = os.path.join("/dev/dri", node_name)
                    devices.append({
                        "node": full_path,
                        "name": node_name,
                        "pci": "",
                        "label": f"DRM Node {node_name}",
                        "is_intel_arc": False,
                        "is_intel_igpu": True
                    })

        return devices

    @staticmethod
    def test_encoder(encoder: str, device: Optional[str] = None) -> bool:
        """Verify if an encoder actually functions with current drivers/hardware."""
        if encoder in ("libx265", "libx264"):
            return True
        ffmpeg_bin = get_binary_path("ffmpeg")
        try:
            cmd = [ffmpeg_bin, "-v", "error"]
            if encoder == "hevc_vaapi":
                vaapi_dev = device or "/dev/dri/renderD128"
                if not os.path.exists(vaapi_dev):
                    return False
                cmd.extend([
                    "-vaapi_device", vaapi_dev,
                    "-f", "lavfi", "-i", "color=s=256x256:d=0.04",
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "hevc_vaapi",
                    "-f", "null", "-"
                ])
            elif encoder == "hevc_qsv":
                if device:
                    cmd.extend(["-qsv_device", device])
                cmd.extend([
                    "-f", "lavfi", "-i", "color=s=256x256:d=0.04",
                    "-vf", "format=nv12",
                    "-c:v", "hevc_qsv",
                    "-f", "null", "-"
                ])
            else:
                cmd.extend([
                    "-f", "lavfi", "-i", "color=s=256x256:d=0.04",
                    "-c:v", encoder,
                    "-f", "null", "-"
                ])
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    @classmethod
    def detect_best_encoder(
        cls,
        preferred_encoder: Optional[str] = None,
        preferred_device: Optional[str] = None,
        mode: str = "auto"
    ) -> Tuple[str, str, Optional[str]]:
        """
        Detect best HEVC hardware/CPU encoder and device node based on chosen mode:
        - mode='cpu': Forces multi-threaded software CPU encoding
        - mode='gpu': Forces hardware GPU encoding (fails if no GPU found)
        - mode='both': Prioritizes fastest GPU encoder (with companion CPU worker)
        - mode='auto': Auto-probes best GPU, falls back to CPU
        """
        encoders = cls.get_available_encoders()
        drm_devices = cls.get_drm_devices()

        # Mode: Forced CPU
        if mode == "cpu":
            if preferred_encoder and preferred_encoder in encoders:
                return preferred_encoder, f"Forced CPU ({preferred_encoder})", None
            if "libx265" in encoders:
                return "libx265", "Multi-Threaded CPU (libx265)", None
            return "libx264", "Multi-Threaded CPU (libx264)", None

        # User-specified encoder
        if preferred_encoder and preferred_encoder in encoders:
            if preferred_encoder not in ("libx265", "libx264") and not cls.test_encoder(preferred_encoder, preferred_device):
                print(f" {Colors.YELLOW}⚠ Warning: Specified encoder '{preferred_encoder}' failed hardware initialization test.{Colors.RESET}")
                if mode == "gpu":
                    raise RuntimeError(f"GPU encoder '{preferred_encoder}' is not functional on this system.")
                print("   Falling back to automatic hardware auto-detection...")
            else:
                dev_node = preferred_device
                if not dev_node and preferred_encoder in ("hevc_qsv", "hevc_vaapi") and drm_devices:
                    dev_node = drm_devices[0]["node"]
                return preferred_encoder, f"User-Specified ({preferred_encoder})", dev_node

        # 1. Check for NVIDIA NVENC
        if "hevc_nvenc" in encoders and cls.test_encoder("hevc_nvenc"):
            return "hevc_nvenc", "NVIDIA NVENC (GPU)", None

        # 2. Check for Intel Arc dGPU
        arc_gpus = [d for d in drm_devices if d["is_intel_arc"]]
        for arc in arc_gpus:
            node = arc["node"]
            if "hevc_qsv" in encoders and cls.test_encoder("hevc_qsv", node):
                return "hevc_qsv", f"Intel QuickSync [{arc['label']}]", node
            if "hevc_vaapi" in encoders and cls.test_encoder("hevc_vaapi", node):
                return "hevc_vaapi", f"Intel Arc VAAPI [{arc['label']}]", node

        # 3. Check for Intel iGPU (UHD / Iris Xe)
        igpu_gpus = [d for d in drm_devices if d["is_intel_igpu"]]
        for igpu in igpu_gpus:
            node = igpu["node"]
            if "hevc_qsv" in encoders and cls.test_encoder("hevc_qsv", node):
                return "hevc_qsv", f"Intel QuickSync iGPU [{igpu['label']}]", node
            if "hevc_vaapi" in encoders and cls.test_encoder("hevc_vaapi", node):
                return "hevc_vaapi", f"Intel iGPU VAAPI [{igpu['label']}]", node

        # 4. Check for Apple Silicon VideoToolbox
        if "hevc_videotoolbox" in encoders and cls.test_encoder("hevc_videotoolbox"):
            return "hevc_videotoolbox", "Apple VideoToolbox (Apple Silicon)", None

        # 5. Check any remaining DRM device via VAAPI (AMD Radeon, etc.)
        if "hevc_vaapi" in encoders:
            for d in drm_devices:
                node = d["node"]
                if cls.test_encoder("hevc_vaapi", node):
                    return "hevc_vaapi", f"VAAPI Hardware [{d['label']}]", node
            if cls.test_encoder("hevc_vaapi", "/dev/dri/renderD128"):
                return "hevc_vaapi", "Linux VAAPI (/dev/dri/renderD128)", "/dev/dri/renderD128"

        # 6. Check for AMD AMF
        if "hevc_amf" in encoders and cls.test_encoder("hevc_amf"):
            return "hevc_amf", "AMD AMF (GPU)", None

        # 7. Check generic QSV without explicit node
        if "hevc_qsv" in encoders and cls.test_encoder("hevc_qsv"):
            return "hevc_qsv", "Intel QuickSync (GPU)", None

        # If mode was strictly 'gpu', do not fallback to CPU
        if mode == "gpu":
            raise RuntimeError(
                "No functional GPU hardware encoder (NVENC, QSV, VAAPI, VideoToolbox, AMF) could be initialized. "
                "Ensure GPU drivers and FFmpeg hardware libraries are installed, or run with --mode cpu."
            )

        # 8. High-efficiency CPU Fallback (libx265)
        if "libx265" in encoders:
            return "libx265", "Software CPU (libx265)", None

        # 9. Baseline CPU Fallback
        return "libx264", "Software CPU Fallback (libx264)", None


# Subtitle formats
BITMAP_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvdsub",
    "xsub",
    "dvb_subtitle",
    "dvbsub",
    "pgssub"
}

TEXT_SUBTITLE_CODECS = {
    "subrip",
    "srt",
    "mov_text",
    "webvtt",
    "text",
    "ttml",
    "ass",
    "ssa"
}


# ==============================================================================
# 🎞 Media Prober
# ==============================================================================
class MediaProber:
    """Extracts stream metadata using ffprobe."""

    @staticmethod
    def probe(file_path: str) -> Optional[Dict]:
        """Execute ffprobe and return parsed JSON streams."""
        ffprobe_bin = get_binary_path("ffprobe")
        cmd = [
            ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(res.stdout)
        except Exception:
            return None

    @classmethod
    def get_video_info(cls, file_path: str) -> Optional[Dict]:
        """Extract primary video stream codec, duration, dimensions, and subtitle tracks."""
        data = cls.probe(file_path)
        if not data or "streams" not in data:
            return None

        video_stream = next(
            (s for s in data["streams"] if s.get("codec_type") == "video"),
            None
        )
        if not video_stream:
            return None

        duration = 0.0
        try:
            if "duration" in data.get("format", {}):
                duration = float(data["format"]["duration"])
            elif "duration" in video_stream:
                duration = float(video_stream["duration"])
        except (ValueError, TypeError):
            duration = 0.0

        subtitles = []
        for s in data.get("streams", []):
            if s.get("codec_type") == "subtitle":
                cname = s.get("codec_name", "").lower()
                subtitles.append({
                    "index": s.get("index"),
                    "codec": cname,
                    "is_bitmap": cname in BITMAP_SUBTITLE_CODECS
                })

        return {
            "codec": video_stream.get("codec_name", "").lower(),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "bitrate": int(data.get("format", {}).get("bit_rate") or video_stream.get("bit_rate") or 0),
            "duration": duration,
            "format_name": data.get("format", {}).get("format_name", ""),
            "subtitles": subtitles
        }


# ==============================================================================
# ⚡ Transcoder Engine
# ==============================================================================
class Transcoder:
    """Manages GPU, CPU, and Dual-Engine (Both) batch media transcoding execution."""

    def __init__(
        self,
        encoder: str,
        hw_desc: str,
        device: Optional[str] = None,
        mode: str = "auto",
        cq: int = 24,
        preset: str = "p4",
        cpu_threads: int = 0,
        gpu_workers: int = 1,
        cpu_workers: int = 1,
        hwaccel: str = "auto",
        bar_style: str = "level",
        audio_codec: str = "aac",
        audio_bitrate: str = "192k",
        overwrite: bool = False,
        delete_original: bool = False,
        skip_hevc: bool = True,
        container: str = "auto",
        dry_run: bool = False,
        gui_telemetry: bool = False,
        gpu_id: Optional[int] = None
    ):
        self.encoder = encoder
        self.hw_desc = hw_desc
        self.device = device
        self.mode = mode
        self.cq = cq
        self.preset = preset
        self.cpu_threads = cpu_threads
        self.gpu_workers = max(1, gpu_workers)
        self.cpu_workers = max(1, cpu_workers)
        self.hwaccel = hwaccel
        self.bar_style = bar_style
        self.audio_codec = audio_codec
        self.audio_bitrate = audio_bitrate
        self.overwrite = overwrite
        self.delete_original = delete_original
        self.skip_hevc = skip_hevc
        self.container = container
        self.dry_run = dry_run
        self.gui_telemetry = gui_telemetry
        self.gpu_id = gpu_id

        self.current_temp_outputs: List[str] = []
        self.stats_lock = threading.Lock()
        self.print_lock = threading.Lock()

        self.stats = {
            "scanned": 0,
            "transcoded": 0,
            "skipped": 0,
            "failed": 0,
            "total_candidates": 0,
            "transcoded_orig_bytes": 0,
            "transcoded_new_bytes": 0,
            "orig_bytes": 0,
            "new_bytes": 0,
            "start_time": time.time()
        }

    def build_ffmpeg_command(
        self,
        input_path: str,
        output_path: str,
        force_cpu: bool = False,
        active_encoder: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        subtitles: Optional[List[Dict]] = None
    ) -> List[str]:
        """Construct the optimized FFmpeg CLI argument list with hardware decoding & encoding."""
        ffmpeg_bin = get_binary_path("ffmpeg")
        cmd = [ffmpeg_bin, "-y", "-hide_banner", "-nostats", "-loglevel", "error"]
        encoder = "libx265" if force_cpu else (active_encoder or self.encoder)

        # Hardware-accelerated decoding input flags
        if not force_cpu and self.hwaccel != "none":
            if self.hwaccel == "auto":
                if encoder == "hevc_nvenc":
                    cmd.extend(["-hwaccel", "cuda"])
                elif encoder == "hevc_qsv":
                    cmd.extend(["-hwaccel", "qsv"])
                elif encoder == "hevc_vaapi":
                    cmd.extend(["-hwaccel", "vaapi"])
                elif encoder == "hevc_videotoolbox":
                    cmd.extend(["-hwaccel", "videotoolbox"])
            else:
                cmd.extend(["-hwaccel", self.hwaccel])

        # Explicit stream mapping: primary video and audio
        cmd.extend(["-map", "0:v:0", "-map", "0:a?"])

        # Hardware-specific encoding flags
        if encoder == "hevc_nvenc":
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_nvenc",
                "-preset", self.preset if self.preset in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "slow", "medium", "fast"] else "p4",
                "-cq:v", str(self.cq),
                "-b:v", "0",
                "-spatial_aq", "1",
                "-temporal_aq", "1"
            ])
        elif encoder == "hevc_qsv":
            qsv_presets = {
                "p1": "veryfast", "p2": "faster", "p3": "fast",
                "p4": "medium", "p5": "slow", "p6": "slower", "p7": "veryslow"
            }
            qsv_preset = qsv_presets.get(self.preset, self.preset if self.preset in qsv_presets.values() else "medium")
            if self.device and not force_cpu:
                cmd.extend(["-qsv_device", self.device])
            cmd.extend([
                "-i", input_path,
                "-vf", "format=nv12",
                "-c:v", "hevc_qsv",
                "-global_quality", str(self.cq),
                "-preset", qsv_preset
            ])
        elif encoder == "hevc_vaapi":
            vaapi_dev = self.device if (self.device and not force_cpu) else "/dev/dri/renderD128"
            cmd.extend([
                "-vaapi_device", vaapi_dev,
                "-i", input_path,
                "-vf", "format=nv12,hwupload",
                "-c:v", "hevc_vaapi",
                "-qp", str(self.cq)
            ])
        elif encoder == "hevc_videotoolbox":
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_videotoolbox",
                "-q:v", str(max(20, 100 - self.cq * 2))
            ])
        elif encoder == "hevc_amf":
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_amf",
                "-quality", "speed",
                "-rc", "cqp",
                "-qp_i", str(self.cq),
                "-qp_p", str(self.cq)
            ])
        elif encoder == "libx265":
            cpu_p = self.preset if self.preset in ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"] else "medium"
            cmd.extend([
                "-i", input_path,
                "-c:v", "libx265",
                "-crf", str(self.cq),
                "-preset", cpu_p,
                "-threads", str(self.cpu_threads),
                "-x265-params", "no-sao=1:aq-mode=3"
            ])
        else:
            # Fallback (libx264 or generic)
            cmd.extend([
                "-i", input_path,
                "-c:v", encoder,
                "-crf", str(self.cq),
                "-threads", str(self.cpu_threads)
            ])

        # Extra hardware args (e.g. -gpu 0, -gpu 1)
        if encoder == "hevc_nvenc" and self.gpu_id is not None:
            if "-gpu" not in cmd:
                cmd.extend(["-gpu", str(self.gpu_id)])

        if extra_args:
            for arg in extra_args:
                if arg not in cmd:
                    cmd.append(arg)

        # Audio & Subtitle stream mapping based on container format
        out_ext = os.path.splitext(output_path)[1].lower()
        if out_ext == ".mkv":
            # Matroska container: natively preserves all audio codecs and bitmap/text subtitles losslessly
            cmd.extend(["-c:a", "copy"])
            if subtitles:
                cmd.extend(["-map", "0:s?", "-c:s", "copy"])
            else:
                cmd.extend(["-sn"])
        else:
            # MP4 / M4V / MOV container
            cmd.extend(["-c:a", self.audio_codec, "-b:a", self.audio_bitrate])
            text_subs = [s for s in (subtitles or []) if not s.get("is_bitmap")]
            if text_subs:
                for ts in text_subs:
                    cmd.extend(["-map", f"0:{ts['index']}"])
                cmd.extend(["-c:s", "mov_text"])
            else:
                # Strictly disable subtitles so FFmpeg never attempts to encode bitmap subs into mov_text
                cmd.extend(["-sn"])
            cmd.extend(["-movflags", "+faststart"])

        cmd.extend([
            "-progress", "pipe:1",
            output_path
        ])

        return cmd

    def transcode_file(
        self,
        file_path: str,
        file_idx: int = 1,
        total_files: int = 1,
        worker_engine: Optional[str] = None,
        worker_desc: Optional[str] = None,
        extra_args: Optional[List[str]] = None
    ) -> bool:
        """Process an individual media file with the interactive Level Progress Bar."""
        with self.stats_lock:
            self.stats["scanned"] += 1

        base_dir = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)
        base_name, in_ext = os.path.splitext(file_name)

        # Determine target container format
        if self.container == "mkv":
            out_ext = ".mkv"
        elif self.container == "mp4":
            out_ext = ".mp4"
        else:  # auto / same
            out_ext = in_ext.lower() if in_ext.lower() in [".mkv", ".mp4"] else ".mp4"

        final_output = os.path.join(base_dir, f"{base_name}{out_ext}")
        temp_output = os.path.join(base_dir, f".{base_name}.transcoding_{file_idx}{out_ext}")

        orig_size = os.path.getsize(file_path)
        with self.stats_lock:
            self.stats["orig_bytes"] += orig_size

        # Probe video and subtitle streams
        info = MediaProber.get_video_info(file_path)
        if not info:
            with self.print_lock:
                print(f" {Colors.YELLOW}⚠ [SKIP]{Colors.RESET} Could not probe video stream: {file_name}")
            with self.stats_lock:
                self.stats["skipped"] += 1
            return False

        # Check if already compliant H.265 / AV1
        is_already_hevc = info["codec"] in ["hevc", "h265", "av1"]
        if is_already_hevc and self.skip_hevc:
            if self.container in ("auto", "same") or in_ext.lower() == out_ext:
                with self.print_lock:
                    print(f" {Colors.DIM}✔ [COMPLIANT]{Colors.RESET} Already {info['codec'].upper()}: {Colors.CYAN}{file_name}{Colors.RESET}")
                if self.gui_telemetry:
                    print(f"__GUI_RESULT__:status=skipped|file={file_name}|orig_size={orig_size}|new_size={orig_size}|saved_bytes=0|saved_pct=0.0", flush=True)
                with self.stats_lock:
                    self.stats["skipped"] += 1
                    self.stats["new_bytes"] += orig_size
                return False

        if os.path.exists(final_output) and file_path != final_output and not self.overwrite:
            with self.print_lock:
                print(f" {Colors.YELLOW}⚠ [EXISTS]{Colors.RESET} Output already exists: {os.path.basename(final_output)}")
            with self.stats_lock:
                self.stats["skipped"] += 1
                self.stats["new_bytes"] += orig_size
            return False

        active_enc = worker_engine or self.encoder
        active_desc = worker_desc or self.hw_desc
        engine_label = "GPU" if active_enc not in ("libx265", "libx264") else "CPU"

        # Check for bitmap subtitles when outputting to MP4
        sub_list = info.get("subtitles", [])
        bitmap_subs = [s for s in sub_list if s.get("is_bitmap")]
        if bitmap_subs and out_ext != ".mkv":
            with self.print_lock:
                print(f"  {Colors.YELLOW}ℹ [SUBTITLES]{Colors.RESET} Safely bypassing {len(bitmap_subs)} PGS/bitmap subtitle track(s) for MP4 output (use MKV container to preserve PGS subtitles).")

        with self.print_lock:
            print(f"\n{Colors.BOLD}▶ Transcoding:{Colors.RESET} {Colors.CYAN}{file_name}{Colors.RESET} [{file_idx}/{total_files}]")
            print(f"  {Colors.DIM}Source:{Colors.RESET} {info['codec'].upper()} {info['width']}x{info['height']} | {format_bytes(orig_size)} | {format_duration(info['duration'])}")
            print(f"  {Colors.DIM}Engine:{Colors.RESET} {Colors.GREEN}{active_desc}{Colors.RESET} (CQ/CRF: {self.cq}) [Container: {out_ext.upper()}]")

        if self.dry_run:
            with self.print_lock:
                print(f"  {Colors.YELLOW}[DRY RUN] Would convert to {final_output}{Colors.RESET}")
            est_new_size = int(orig_size * 0.45)
            with self.stats_lock:
                self.stats["transcoded"] += 1
                self.stats["transcoded_orig_bytes"] += orig_size
                self.stats["transcoded_new_bytes"] += est_new_size
                self.stats["new_bytes"] += est_new_size
            return True

        # Build FFmpeg execution command
        cmd = self.build_ffmpeg_command(
            file_path,
            temp_output,
            active_encoder=active_enc,
            extra_args=extra_args,
            subtitles=sub_list
        )
        self.current_temp_outputs.append(temp_output)

        progress_bar = LevelProgressBar(
            total_duration=info["duration"],
            file_name=file_name,
            file_idx=file_idx,
            total_files=total_files,
            engine_label=f"{engine_label}: {active_enc}",
            style=self.bar_style,
            print_lock=self.print_lock
        )

        t_start = time.time()
        stderr_lines = []

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=1
            )

            def read_err():
                for eline in proc.stderr:
                    stderr_lines.append(eline.strip())

            err_thread = threading.Thread(target=read_err, daemon=True)
            err_thread.start()

            # Read machine-readable FFmpeg progress pipe
            cur_kv = {}
            for line in proc.stdout:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    cur_kv[k.strip()] = v.strip()
                    if k.strip() == "progress":
                        # Compute current playback seconds
                        out_sec = 0.0
                        if "out_time_us" in cur_kv and cur_kv["out_time_us"].lstrip("-").isdigit():
                            out_sec = float(cur_kv["out_time_us"]) / 1_000_000.0
                        elif "out_time_ms" in cur_kv and cur_kv["out_time_ms"].lstrip("-").isdigit():
                            out_sec = float(cur_kv["out_time_ms"]) / 1_000.0
                        elif "out_time" in cur_kv:
                            parts = cur_kv["out_time"].split(":")
                            if len(parts) == 3:
                                try:
                                    out_sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                                except ValueError:
                                    pass

                        progress_bar.render(
                            out_seconds=out_sec,
                            speed_str=cur_kv.get("speed", "N/A"),
                            fps_str=cur_kv.get("fps", "0"),
                            bitrate_str=cur_kv.get("bitrate", "N/A"),
                            current_bytes=int(cur_kv.get("total_size", 0)) if cur_kv.get("total_size", "").isdigit() else 0
                        )

                        if self.gui_telemetry and info["duration"] > 0:
                            fraction = min(1.0, max(0.0, out_sec / info["duration"]))
                            print(
                                f"__GUI_TELEMETRY__:fraction={fraction:.4f}|"
                                f"speed={cur_kv.get('speed', 'N/A')}|"
                                f"fps={cur_kv.get('fps', '0')}|"
                                f"bitrate={cur_kv.get('bitrate', 'N/A')}|"
                                f"out_sec={out_sec:.2f}|total_sec={info['duration']:.2f}",
                                flush=True
                            )

            proc.wait()
            err_thread.join(timeout=1.0)
            elapsed = time.time() - t_start

            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd, output="\n".join(stderr_lines))

            progress_bar.finish()

            if not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
                raise RuntimeError("Output file was not created or is 0 bytes.")

            new_size = os.path.getsize(temp_output)
            saved_bytes = orig_size - new_size
            ratio_pct = (saved_bytes / orig_size) * 100.0 if orig_size > 0 else 0

            with self.stats_lock:
                self.stats["new_bytes"] += new_size
                self.stats["transcoded"] += 1
                self.stats["transcoded_orig_bytes"] += orig_size
                self.stats["transcoded_new_bytes"] += new_size

            if self.gui_telemetry:
                print(
                    f"__GUI_RESULT__:status=complete|file={file_name}|"
                    f"orig_size={orig_size}|new_size={new_size}|"
                    f"saved_bytes={saved_bytes}|saved_pct={ratio_pct:.2f}",
                    flush=True
                )

            # Atomic swap to target destination
            if file_path == final_output:
                os.remove(file_path)
            shutil.move(temp_output, final_output)
            if temp_output in self.current_temp_outputs:
                self.current_temp_outputs.remove(temp_output)

            # Optional delete original
            if self.delete_original and file_path != final_output:
                try:
                    os.remove(file_path)
                except OSError as e:
                    with self.print_lock:
                        print(f"  {Colors.YELLOW}Warning: Could not remove original: {e}{Colors.RESET}")

            # Reporting
            color = Colors.GREEN if saved_bytes > 0 else Colors.YELLOW
            with self.print_lock:
                print(f"  {color}✔ Complete:{Colors.RESET} {format_bytes(orig_size)} ➔ {format_bytes(new_size)} ({ratio_pct:+.1f}%) in {format_duration(elapsed)}")
            return True

        except (subprocess.CalledProcessError, RuntimeError) as e:
            # Automatic graceful fallback to CPU if GPU encoder failed mid-flight
            if active_enc not in ("libx265", "libx264") and self.mode != "gpu":
                with self.print_lock:
                    print(f"  {Colors.YELLOW}⚠ Hardware encoder '{active_enc}' failed. Retrying with CPU fallback (libx265)...{Colors.RESET}")
                if os.path.exists(temp_output):
                    try:
                        os.remove(temp_output)
                    except Exception:
                        pass
                return self.transcode_file(
                    file_path,
                    file_idx=file_idx,
                    total_files=total_files,
                    worker_engine="libx265",
                    worker_desc="Software CPU Fallback (libx265)"
                )

            with self.print_lock:
                err_msg = "\n".join(stderr_lines[-5:]) if stderr_lines else str(e)
                print(f"  {Colors.RED}✖ Error during transcoding: {err_msg}{Colors.RESET}")
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception:
                    pass
            if temp_output in self.current_temp_outputs:
                self.current_temp_outputs.remove(temp_output)
            with self.stats_lock:
                self.stats["failed"] += 1
                self.stats["new_bytes"] += orig_size
            return False

    def scan_and_transcode(self, root_dir: str):
        """Recursively scan directory for video files and transcode."""
        target_files = []
        for root, _, files in os.walk(root_dir):
            for f in files:
                if f.startswith("."):
                    continue
                if f.lower().endswith(VIDEO_EXTENSIONS):
                    target_files.append(os.path.join(root, f))

        target_files.sort()
        total = len(target_files)
        self.stats["total_candidates"] = total

        print(f"\n{Colors.BOLD}{'=' * 78}{Colors.RESET}")
        print(f" {Colors.GREEN}⚡ Batch Transcoding Queue:{Colors.RESET} {total} media candidate(s) found")
        print(f" {Colors.CYAN}Hardware Strategy:{Colors.RESET} Mode: {self.mode.upper()} | Engine: {self.hw_desc} ({self.encoder})")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}")

        interrupted = False
        try:
            if self.mode in ("both", "dual-gpu", "all") and total >= 2:
                self.run_dual_engine_batch(target_files)
            else:
                for idx, f in enumerate(target_files, 1):
                    self.transcode_file(f, file_idx=idx, total_files=total)
        except KeyboardInterrupt:
            interrupted = True
            print(f"\n\n{Colors.YELLOW}{Colors.BOLD}⚠ Transcoding canceled by user (Ctrl+C / Interrupt).{Colors.RESET}")
            for tmp in list(self.current_temp_outputs):
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                        print(f" {Colors.DIM}✔ Cleaned up incomplete temp file: {os.path.basename(tmp)}{Colors.RESET}")
                    except Exception:
                        pass
        finally:
            self.print_summary(interrupted=interrupted)

    def run_dual_engine_batch(self, target_files: List[str]):
        """
        High-Throughput Parallel Pipeline:
        Dispatches transcoding jobs across Dual GPUs, GPU+CPU, or All hardware engines simultaneously.
        """
        total = len(target_files)
        work_queue = queue.Queue()
        for idx, f in enumerate(target_files, 1):
            work_queue.put((idx, f))

        def worker_loop(w_type: str, w_engine: str, w_desc: str, w_extra: List[str]):
            while not work_queue.empty():
                try:
                    idx, file_path = work_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.transcode_file(
                        file_path,
                        file_idx=idx,
                        total_files=total,
                        worker_engine=w_engine,
                        worker_desc=w_desc,
                        extra_args=w_extra
                    )
                finally:
                    work_queue.task_done()

        # Discover GPUs
        threads = []
        try:
            from gpu_detector import MultiGPUManager
            mgr = MultiGPUManager()
            all_devs = mgr.detect_all_devices()
            gpu_cards = [d for d in all_devs if d.vendor != "CPU"]
        except Exception:
            gpu_cards = []

        if self.mode == "dual-gpu" and len(gpu_cards) >= 2:
            print(f"\n {Colors.MAGENTA}⚡ Simultaneous Dual-GPU Hardware Acceleration Active!{Colors.RESET}")
            print(f"   • {Colors.GREEN}Worker 1 (GPU 0){Colors.RESET}: {gpu_cards[0].name} [{gpu_cards[0].encoder}]")
            print(f"   • {Colors.CYAN}Worker 2 (GPU 1){Colors.RESET}: {gpu_cards[1].name} [{gpu_cards[1].encoder}]")
            print(f"   • Dynamic Dispatch: 2 GPU cards transcoding simultaneously for max power...\n")

            for i, card in enumerate(gpu_cards[:2]):
                t = threading.Thread(
                    target=worker_loop,
                    args=(f"GPU-{i}", card.encoder, f"{card.name} [{card.encoder}]", card.extra_args),
                    name=f"GPU-Worker-{i+1}"
                )
                t.daemon = True
                t.start()
                threads.append(t)

        elif self.mode in ("both", "all"):
            print(f"\n {Colors.MAGENTA}⚡ Multi-Engine Parallel Acceleration Active!{Colors.RESET}")
            if gpu_cards:
                for i, card in enumerate(gpu_cards[:2]):
                    print(f"   • {Colors.GREEN}Worker {i+1} (GPU {i}){Colors.RESET}: {card.name} [{card.encoder}]")
                    t = threading.Thread(
                        target=worker_loop,
                        args=(f"GPU-{i}", card.encoder, f"{card.name} [{card.encoder}]", card.extra_args),
                        name=f"GPU-Worker-{i+1}"
                    )
                    t.daemon = True
                    t.start()
                    threads.append(t)
            else:
                t = threading.Thread(
                    target=worker_loop,
                    args=("GPU", self.encoder, self.hw_desc, []),
                    name="GPU-Worker-1"
                )
                t.daemon = True
                t.start()
                threads.append(t)

            print(f"   • {Colors.CYAN}CPU Companion Worker{Colors.RESET}: Multi-Threaded CPU (libx265 -threads {self.cpu_threads})")
            t_cpu = threading.Thread(
                target=worker_loop,
                args=("CPU", "libx265", f"Multi-Threaded CPU (libx265 -threads {self.cpu_threads})", []),
                name="CPU-Worker-1"
            )
            t_cpu.daemon = True
            t_cpu.start()
            threads.append(t_cpu)

        else:
            # Fallback single GPU worker + CPU workers
            for i in range(self.gpu_workers):
                t_gpu = threading.Thread(
                    target=worker_loop,
                    args=("GPU", self.encoder, self.hw_desc, []),
                    name=f"GPU-Worker-{i+1}"
                )
                t_gpu.daemon = True
                t_gpu.start()
                threads.append(t_gpu)

        for t in threads:
            t.join()

    def print_summary(self, interrupted: bool = False):
        """Display final execution telemetry and comprehensive space saved."""
        total_time = max(0.1, time.time() - self.stats["start_time"])
        orig_bytes = self.stats["transcoded_orig_bytes"]
        new_bytes = self.stats["transcoded_new_bytes"]
        saved_bytes = max(0, orig_bytes - new_bytes)
        pct_saved = (saved_bytes / orig_bytes * 100.0) if orig_bytes > 0 else 0.0

        banner_title = "⚠ Transcoding Canceled (Partial Batch Summary)" if interrupted else "✨ Transcoding Batch Summary"
        banner_color = Colors.YELLOW if interrupted else Colors.GREEN

        print(f"\n{Colors.BOLD}{'=' * 78}{Colors.RESET}")
        print(f" {banner_color}{Colors.BOLD}{banner_title}{Colors.RESET}")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}")

        total_cand = self.stats["total_candidates"] if self.stats["total_candidates"] > 0 else self.stats["scanned"]
        print(f" • Mode / Strategy:        {self.mode.upper()} ({self.hw_desc})")
        print(f" • Total Candidates:       {total_cand}")
        print(f" • Successfully Converted: {Colors.GREEN}{Colors.BOLD}{self.stats['transcoded']}{Colors.RESET}")
        print(f" • Skipped (Compliant):    {Colors.YELLOW}{self.stats['skipped']}{Colors.RESET}")
        if self.stats["failed"] > 0:
            print(f" • Failed:                 {Colors.RED}{self.stats['failed']}{Colors.RESET}")
        if interrupted:
            remaining = max(0, total_cand - self.stats["transcoded"] - self.stats["skipped"] - self.stats["failed"])
            print(f" • Unprocessed Remaining:  {Colors.DIM}{remaining}{Colors.RESET}")

        print(f"\n {Colors.BOLD}📦 Storage Footprint & Net Space Reclaimed:{Colors.RESET}")
        print(f" • Converted Original Footprint: {format_bytes(orig_bytes)}")
        print(f" • Modern H.265 Space Occupied:  {format_bytes(new_bytes)}")

        if saved_bytes > 0:
            saved_gb = saved_bytes / (1024 ** 3)
            print(f" • {Colors.BOLD}Net Storage Space Saved:       {Colors.GREEN}{Colors.BOLD}✔ {format_bytes(saved_bytes)} ({saved_gb:.2f} GB) SAVED (-{pct_saved:.1f}%){Colors.RESET}")
            if self.stats["transcoded"] > 0:
                avg_saved = saved_bytes / self.stats["transcoded"]
                print(f" • Average Space Saved / Video:  {Colors.CYAN}{format_bytes(avg_saved)} / media file{Colors.RESET}")
        else:
            if self.stats["transcoded"] == 0:
                print(f" • Net Storage Space Saved:       {Colors.DIM}0 B (no transcodes completed yet){Colors.RESET}")
            else:
                print(f" • Net Storage Space Saved:       {Colors.DIM}0 B (already optimal){Colors.RESET}")

        print(f"\n • Elapsed Runtime:              {format_duration(total_time)}")
        if self.stats["transcoded"] > 0:
            throughput = orig_bytes / total_time
            print(f" • Throughput Efficiency:        {Colors.BOLD}{format_bytes(throughput)}/s{Colors.RESET}")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}\n")


# ==============================================================================
# 🚀 CLI Argument Parsing & Entry Point
# ==============================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="⚡ High-Performance GPU/CPU/Dual-Engine H.265 (HEVC) Media Transcoder",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to media directory or single video file (e.g. \"D:\\Movies\")"
    )
    parser.add_argument(
        "-d", "--dir", "--directory",
        dest="input_dir",
        default=None,
        help="Explicit flag to specify the media directory or video file to transcode/encode"
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the interactive Graphical User Interface (GUI) Studio with Folder Picker & Dual-GPU controls"
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Force terminal interactive CLI mode (prompt for directory path in terminal instead of opening GUI)"
    )

    # Acceleration Modes
    mode_group = parser.add_argument_group("Acceleration Strategy Options")
    mode_group.add_argument(
        "--mode",
        choices=["auto", "gpu", "dual-gpu", "both", "all", "cpu"],
        default="auto",
        help="Transcoding hardware mode: 'dual-gpu' (simultaneously utilize both GPU cards), 'gpu' (single GPU), 'both'/'all' (GPU + CPU concurrent), 'cpu' (CPU software), or 'auto' (default)"
    )
    mode_group.add_argument(
        "--dual-gpu",
        dest="mode",
        action="store_const",
        const="dual-gpu",
        help="Shorthand to force simultaneous Dual-GPU hardware transcoding across both video cards"
    )
    mode_group.add_argument(
        "--gpu",
        dest="mode",
        action="store_const",
        const="gpu",
        help="Shorthand to force GPU hardware transcoding"
    )
    mode_group.add_argument(
        "--cpu",
        dest="mode",
        action="store_const",
        const="cpu",
        help="Shorthand to force multi-threaded CPU software transcoding"
    )
    mode_group.add_argument(
        "--both",
        dest="mode",
        action="store_const",
        const="both",
        help="Shorthand for dual-engine mode (runs GPU and CPU simultaneously for maximum conversion speed)"
    )
    mode_group.add_argument(
        "--all",
        dest="mode",
        action="store_const",
        const="all",
        help="Shorthand to utilize all available hardware: both GPUs + CPU multithreading"
    )

    # Progress & Visual Options
    visual_group = parser.add_argument_group("Progress & Visual Meter Options")
    visual_group.add_argument(
        "--bar-style",
        choices=["level", "blocks", "ascii"],
        default="level",
        help="Visual style of the level progress bar: 'level' (▰▰▰▱▱ graduated), 'blocks' (███░░), or 'ascii' (default: level)"
    )

    # Encoder & Quality Options
    enc_group = parser.add_argument_group("Encoding Quality & Tuning")
    enc_group.add_argument(
        "--encoder",
        choices=["auto", "hevc_nvenc", "hevc_qsv", "hevc_vaapi", "hevc_videotoolbox", "hevc_amf", "libx265", "libx264"],
        default="auto",
        help="Explicitly choose video encoder (default: auto detection based on mode)"
    )
    enc_group.add_argument(
        "--cq",
        type=int,
        default=24,
        help="Constant Quality / CRF level (18=near lossless, 24=optimal balance, 28=smaller; default: 24)"
    )
    enc_group.add_argument(
        "--gpu-id",
        type=int,
        default=None,
        help="Specific NVIDIA GPU device index to encode on (e.g. 0 for RTX 5070, 1 for RTX 3060)"
    )
    enc_group.add_argument(
        "--preset",
        default="p4",
        help="Encoder speed preset (NVENC: p1-p7 or medium/fast; CPU: ultrafast-veryslow; default: p4)"
    )
    enc_group.add_argument(
        "--cpu-threads",
        type=int,
        default=0,
        help="Number of threads for CPU encoding (default: 0 = all logical cores)"
    )
    enc_group.add_argument(
        "--gpu-workers",
        type=int,
        default=1,
        help="Number of parallel GPU workers in 'both' mode (default: 1)"
    )
    enc_group.add_argument(
        "--cpu-workers",
        type=int,
        default=1,
        help="Number of parallel CPU workers in 'both' mode (default: 1)"
    )
    enc_group.add_argument(
        "--hwaccel",
        choices=["auto", "cuda", "qsv", "vaapi", "d3d11va", "videotoolbox", "none"],
        default="auto",
        help="Hardware decoding acceleration API (default: auto)"
    )

    # File & Batch Options
    file_group = parser.add_argument_group("File & Safety Options")
    file_group.add_argument(
        "--delete-original",
        action="store_true",
        help="Delete original source file when output is a different format (.mkv ➔ .mp4)"
    )
    file_group.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output .mp4 file if one exists"
    )
    file_group.add_argument(
        "--re-encode-hevc",
        action="store_true",
        help="Re-encode videos that are already H.265 (default: skipped to preserve quality and save time)"
    )
    file_group.add_argument(
        "--container",
        choices=["auto", "same", "mp4", "mkv"],
        default="auto",
        help="Target container format: 'auto'/'same' (preserves .mkv for .mkv and .mp4 for .mp4, keeping full subtitles), 'mp4', or 'mkv' (default: auto)"
    )
    file_group.add_argument(
        "--device",
        default=None,
        help="Explicit DRM render node for Intel Arc / iGPU (e.g. /dev/dri/renderD128, /dev/dri/renderD129; default: auto-detected)"
    )
    file_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and display planned transcode actions without running ffmpeg"
    )
    file_group.add_argument(
        "--gui-telemetry",
        action="store_true",
        help="Emit structured telemetry lines for GUI frontends (__GUI_TELEMETRY__: fraction=...)"
    )

    return parser.parse_args()


def main():
    args = parse_arguments()
    if not args.path and getattr(args, "input_dir", None):
        args.path = args.input_dir

    # GUI Studio Launch
    if args.gui or (not args.cli and not args.path and ((sys.stdin.isatty() and len(sys.argv) == 1 and os.environ.get("DISPLAY")) or (os.name == "nt" and len(sys.argv) == 1))):
        try:
            from transcode_gui import main as gui_main
            if args.path:
                sys.argv = [sys.argv[0], args.path]
            gui_main()
            return
        except Exception:
            # If graphical display is not available, fall through to CLI prompt
            pass

    # Check ffmpeg & ffprobe availability
    ffmpeg_bin = get_binary_path("ffmpeg")
    ffprobe_bin = get_binary_path("ffprobe")
    if not shutil.which(ffmpeg_bin) and not os.path.isfile(ffmpeg_bin):
        print(f"{Colors.RED}Error: FFmpeg is not installed or not in PATH.{Colors.RESET}")
        print("Please install FFmpeg: sudo apt install -y ffmpeg (Linux), brew install ffmpeg (macOS), or winget install Gyan.FFmpeg (Windows)")
        sys.exit(1)

    # Detect hardware acceleration and GPU device based on chosen mode
    preferred_enc = None if args.encoder == "auto" else args.encoder
    encoder, hw_desc, device_node = HardwareDetector.detect_best_encoder(
        preferred_encoder=preferred_enc,
        preferred_device=args.device,
        mode=args.mode
    )

    print(f"\n{Colors.BOLD}{Colors.CYAN}⚡ FFmpeg H.265 (HEVC) Media Transcoder{Colors.RESET}")
    dev_str = f" {Colors.DIM}(Device: {device_node}){Colors.RESET}" if device_node else ""
    print(f"Selected Mode:   {Colors.YELLOW}{args.mode.upper()}{Colors.RESET}")
    print(f"Selected Engine: {Colors.GREEN}{hw_desc}{Colors.RESET} [{encoder}]{dev_str}")
    print(f"Progress Bar:    {Colors.CYAN}Level Progress Bar ({args.bar_style}){Colors.RESET}")

    # Path acquisition
    target_path = args.path
    if not target_path:
        while True:
            inp = input("\nEnter path to media directory or video file: ").strip()
            inp = inp.strip("\"'")
            if os.path.exists(inp):
                target_path = inp
                break
            print(f"{Colors.RED}Path does not exist. Please enter a valid directory or file.{Colors.RESET}")

    transcoder = Transcoder(
        encoder=encoder,
        hw_desc=hw_desc,
        device=device_node,
        mode=args.mode,
        cq=args.cq,
        preset=args.preset,
        cpu_threads=args.cpu_threads,
        gpu_workers=args.gpu_workers,
        cpu_workers=args.cpu_workers,
        hwaccel=args.hwaccel,
        bar_style=args.bar_style,
        overwrite=args.overwrite,
        delete_original=args.delete_original,
        skip_hevc=not args.re_encode_hevc,
        container=args.container,
        dry_run=args.dry_run,
        gui_telemetry=args.gui_telemetry,
        gpu_id=args.gpu_id
    )

    try:
        if os.path.isfile(target_path):
            transcoder.stats["total_candidates"] = 1
            transcoder.transcode_file(target_path)
            transcoder.print_summary()
        elif os.path.isdir(target_path):
            transcoder.scan_and_transcode(target_path)
        else:
            print(f"{Colors.RED}Invalid path specified.{Colors.RESET}")
            sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}{Colors.BOLD}⚠ Transcoding canceled by user (Ctrl+C / Interrupt).{Colors.RESET}")
        for tmp in list(transcoder.current_temp_outputs):
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                    print(f" {Colors.DIM}✔ Cleaned up incomplete temp file: {os.path.basename(tmp)}{Colors.RESET}")
                except Exception:
                    pass
        transcoder.print_summary(interrupted=True)


if __name__ == "__main__":
    main()
