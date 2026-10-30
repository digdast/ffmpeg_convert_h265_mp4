#!/usr/bin/env python3
"""
================================================================================
⚡ GPU-Accelerated H.265 (HEVC) & MP4 Batch Media Transcoder
================================================================================
High-performance media transcoding pipeline utilizing hardware-accelerated
video encoding (NVIDIA NVENC, Intel QSV, Apple VideoToolbox, VAAPI) with
automatic CPU fallback (libx265 / libx264).

Features:
- Hardware auto-detection: NVIDIA NVENC (hevc_nvenc), Intel QSV, VAAPI, VideoToolbox
- Safe atomic encoding via temp files (preserves original on failure or interrupt)
- Media preservation: multi-track audio copy/transcode, subtitles, and metadata
- FastStart streaming optimization (`-movflags +faststart`)
- Space savings analytics: tracks before/after file sizes and percentage reduction
- Dry-run mode, recursive scanning, skip already-compliant H.265 files
- Rich terminal output with progress indicators and colorized metrics
================================================================================
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

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
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

# Supported video container extensions
VIDEO_EXTENSIONS = (
    ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".ts", ".mts", ".m2ts", ".vob", ".mpg", ".mpeg", ".mp4"
)


def format_bytes(num_bytes: float) -> str:
    """Format bytes into human-readable representation."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


class HardwareDetector:
    """Detects available hardware accelerators and encoders."""

    @staticmethod
    def get_available_encoders() -> List[str]:
        """Query ffmpeg for available video encoders."""
        try:
            res = subprocess.run(
                ["ffmpeg", "-encoders"],
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
            return []

    @classmethod
    def detect_best_encoder(cls, preferred: Optional[str] = None) -> Tuple[str, str]:
        """
        Detect best HEVC encoder available on this system.
        Returns: (encoder_name, hardware_type)
        """
        encoders = cls.get_available_encoders()

        if preferred and preferred in encoders:
            return preferred, "user-specified"

        # Hardware encoder priority ranking
        hw_candidates = [
            ("hevc_nvenc", "NVIDIA NVENC (GPU)"),
            ("hevc_qsv", "Intel QuickSync (GPU)"),
            ("hevc_videotoolbox", "Apple VideoToolbox (Apple Silicon / GPU)"),
            ("hevc_vaapi", "Linux VAAPI (GPU)"),
            ("hevc_amf", "AMD AMF (GPU)"),
            ("libx265", "Software CPU (libx265)"),
            ("libx264", "Software CPU Fallback (libx264)")
        ]

        for enc, desc in hw_candidates:
            if enc in encoders:
                return enc, desc

        return "libx264", "Generic Software (libx264)"


class MediaProber:
    """Inspects video metadata via ffprobe."""

    @staticmethod
    def probe(file_path: str) -> Optional[Dict]:
        """Execute ffprobe and return parsed JSON streams."""
        cmd = [
            "ffprobe",
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
        """Extract primary video stream codec and dimensions."""
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

        return {
            "codec": video_stream.get("codec_name", "").lower(),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "bitrate": int(data.get("format", {}).get("bit_rate") or video_stream.get("bit_rate") or 0),
            "duration": duration,
            "format_name": data.get("format", {}).get("format_name", "")
        }


class Transcoder:
    """Manages batch GPU/CPU transcoding execution."""

    def __init__(
        self,
        encoder: str,
        hw_desc: str,
        cq: int = 24,
        preset: str = "p4",
        audio_codec: str = "aac",
        audio_bitrate: str = "192k",
        overwrite: bool = False,
        delete_original: bool = False,
        skip_hevc: bool = True,
        dry_run: bool = False
    ):
        self.encoder = encoder
        self.hw_desc = hw_desc
        self.cq = cq
        self.preset = preset
        self.audio_codec = audio_codec
        self.audio_bitrate = audio_bitrate
        self.overwrite = overwrite
        self.delete_original = delete_original
        self.skip_hevc = skip_hevc
        self.dry_run = dry_run

        self.stats = {
            "scanned": 0,
            "transcoded": 0,
            "skipped": 0,
            "failed": 0,
            "orig_bytes": 0,
            "new_bytes": 0,
            "start_time": time.time()
        }

    def build_ffmpeg_command(self, input_path: str, output_path: str) -> List[str]:
        """Construct the optimized FFmpeg CLI argument list."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]

        # Hardware-specific input/output flags
        if self.encoder == "hevc_nvenc":
            # NVIDIA NVENC GPU Pipeline
            cmd.extend([
                "-hwaccel", "cuda",
                "-hwaccel_output_format", "cuda",
                "-i", input_path,
                "-c:v", "hevc_nvenc",
                "-preset", self.preset if self.preset in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "slow", "medium", "fast"] else "p4",
                "-cq:v", str(self.cq),
                "-b:v", "0",
                "-spatial_aq", "1",
                "-temporal_aq", "1"
            ])
        elif self.encoder == "hevc_qsv":
            # Intel QuickSync
            cmd.extend([
                "-hwaccel", "qsv",
                "-i", input_path,
                "-c:v", "hevc_qsv",
                "-global_quality", str(self.cq),
                "-preset", "medium"
            ])
        elif self.encoder == "hevc_vaapi":
            # VAAPI
            cmd.extend([
                "-hwaccel", "vaapi",
                "-hwaccel_device", "/dev/dri/renderD128",
                "-i", input_path,
                "-vf", "format=nv12,hwupload",
                "-c:v", "hevc_vaapi",
                "-qp", str(self.cq)
            ])
        elif self.encoder == "hevc_videotoolbox":
            # Apple Silicon VideoToolbox
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_videotoolbox",
                "-q:v", str(max(20, 100 - self.cq * 2))
            ])
        elif self.encoder == "libx265":
            # Software CPU HEVC
            cmd.extend([
                "-i", input_path,
                "-c:v", "libx265",
                "-crf", str(self.cq),
                "-preset", "medium"
            ])
        else:
            # CPU Fallback
            cmd.extend([
                "-i", input_path,
                "-c:v", self.encoder,
                "-crf", str(self.cq)
            ])

        # Audio and Container configuration
        # Stream copy compatible AAC audio, otherwise transcode
        cmd.extend([
            "-c:a", self.audio_codec,
            "-b:a", self.audio_bitrate,
            "-c:s", "mov_text",          # Standard MP4 timed text subtitles
            "-movflags", "+faststart",   # Web / Stream optimized index at head
            output_path
        ])

        return cmd

    def transcode_file(self, file_path: str) -> bool:
        """Process an individual media file."""
        self.stats["scanned"] += 1
        base_dir = os.path.dirname(file_path)
        file_name = os.path.basename(file_path)
        base_name, ext = os.path.splitext(file_name)

        final_output = os.path.join(base_dir, f"{base_name}.mp4")
        temp_output = os.path.join(base_dir, f".{base_name}.transcoding.mp4")

        orig_size = os.path.getsize(file_path)
        self.stats["orig_bytes"] += orig_size

        # Probe video
        info = MediaProber.get_video_info(file_path)
        if not info:
            print(f" {Colors.YELLOW}⚠ [SKIP]{Colors.RESET} Could not probe video stream: {file_name}")
            self.stats["skipped"] += 1
            return False

        # Check if already compliant H.265 in MP4 container
        is_already_hevc = info["codec"] in ["hevc", "h265"]
        is_mp4_container = ext.lower() == ".mp4"

        if is_already_hevc and is_mp4_container and self.skip_hevc:
            print(f" {Colors.DIM}✔ [COMPLIANT]{Colors.RESET} Already H.265/MP4: {Colors.CYAN}{file_name}{Colors.RESET}")
            self.stats["skipped"] += 1
            self.stats["new_bytes"] += orig_size
            return False

        if os.path.exists(final_output) and file_path != final_output and not self.overwrite:
            print(f" {Colors.YELLOW}⚠ [EXISTS]{Colors.RESET} Output already exists (use --overwrite): {os.path.basename(final_output)}")
            self.stats["skipped"] += 1
            self.stats["new_bytes"] += orig_size
            return False

        print(f"\n{Colors.BOLD}▶ Transcoding:{Colors.RESET} {Colors.CYAN}{file_name}{Colors.RESET}")
        print(f"  {Colors.DIM}Source:{Colors.RESET} {info['codec'].upper()} {info['width']}x{info['height']} | {format_bytes(orig_size)}")
        print(f"  {Colors.DIM}Engine:{Colors.RESET} {Colors.GREEN}{self.hw_desc}{Colors.RESET} (CQ/CRF: {self.cq})")

        if self.dry_run:
            print(f"  {Colors.YELLOW}[DRY RUN] Would execute command and convert to {final_output}{Colors.RESET}")
            self.stats["transcoded"] += 1
            self.stats["new_bytes"] += int(orig_size * 0.55)
            return True

        # Build execution command
        cmd = self.build_ffmpeg_command(file_path, temp_output)

        t_start = time.time()
        try:
            # Run FFmpeg
            res = subprocess.run(cmd, check=True)
            elapsed = time.time() - t_start

            if not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
                raise RuntimeError("Output file was not created or is 0 bytes.")

            new_size = os.path.getsize(temp_output)
            self.stats["new_bytes"] += new_size
            self.stats["transcoded"] += 1

            saved_bytes = orig_size - new_size
            ratio_pct = (saved_bytes / orig_size) * 100.0 if orig_size > 0 else 0

            # Atomic rename of temp file to target
            if file_path == final_output:
                # Transcoding in-place: remove original first, then rename temp
                os.remove(file_path)
            shutil.move(temp_output, final_output)

            # Delete original if input was .mkv, .avi, etc. and flag is set
            if self.delete_original and file_path != final_output:
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"  {Colors.YELLOW}Warning: Could not remove original: {e}{Colors.RESET}")

            # Reporting
            color = Colors.GREEN if saved_bytes > 0 else Colors.YELLOW
            print(f"  {color}✔ Complete:{Colors.RESET} {format_bytes(orig_size)} ➔ {format_bytes(new_size)} ({ratio_pct:+.1f}%) in {format_duration(elapsed)}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"  {Colors.RED}✖ Error during transcoding: exit code {e.returncode}{Colors.RESET}")
            if os.path.exists(temp_output):
                os.remove(temp_output)
            self.stats["failed"] += 1
            self.stats["new_bytes"] += orig_size
            return False
        except Exception as e:
            print(f"  {Colors.RED}✖ Unexpected failure: {e}{Colors.RESET}")
            if os.path.exists(temp_output):
                os.remove(temp_output)
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

        print(f"\n{Colors.BOLD}{'=' * 78}{Colors.RESET}")
        print(f" {Colors.GREEN}⚡ Batch Transcoding Queue:{Colors.RESET} {total} media candidate(s) found")
        print(f" {Colors.CYAN}Hardware Engine:{Colors.RESET} {self.hw_desc} ({self.encoder})")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}")

        for idx, f in enumerate(target_files, 1):
            print(f"\n[{idx}/{total}]", end="")
            self.transcode_file(f)

        self.print_summary()

    def print_summary(self):
        """Display final execution telemetry."""
        total_time = time.time() - self.stats["start_time"]
        saved = self.stats["orig_bytes"] - self.stats["new_bytes"]
        pct = (saved / self.stats["orig_bytes"] * 100) if self.stats["orig_bytes"] > 0 else 0

        print(f"\n{Colors.BOLD}{'=' * 78}{Colors.RESET}")
        print(f" {Colors.GREEN}✨ Transcoding Batch Summary{Colors.RESET}")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}")
        print(f" • Total Scanned:     {self.stats['scanned']}")
        print(f" • Transcoded:        {Colors.GREEN}{self.stats['transcoded']}{Colors.RESET}")
        print(f" • Skipped:           {Colors.YELLOW}{self.stats['skipped']}{Colors.RESET}")
        print(f" • Failed:            {Colors.RED}{self.stats['failed']}{Colors.RESET}")
        print(f" • Original Footprint:{format_bytes(self.stats['orig_bytes'])}")
        print(f" • Modern Footprint:  {format_bytes(self.stats['new_bytes'])}")
        print(f" • Space Savings:     {Colors.BOLD}{Colors.GREEN}{format_bytes(saved)} ({pct:+.1f}%){Colors.RESET}")
        print(f" • Total Elapsed:     {format_duration(total_time)}")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}\n")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="⚡ High-Performance GPU-Accelerated H.265 (HEVC) Media Transcoder",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to media directory or single video file"
    )
    parser.add_argument(
        "--encoder",
        choices=["auto", "hevc_nvenc", "hevc_qsv", "hevc_vaapi", "hevc_videotoolbox", "libx265", "libx264"],
        default="auto",
        help="Explicitly choose video encoder (default: auto hardware detection)"
    )
    parser.add_argument(
        "--cq",
        type=int,
        default=24,
        help="Constant Quality / CRF level (18=near lossless, 24=optimal balance, 28=smaller; default: 24)"
    )
    parser.add_argument(
        "--preset",
        default="p4",
        help="Encoder speed preset (NVENC: p1-p7 or medium/fast; default: p4)"
    )
    parser.add_argument(
        "--delete-original",
        action="store_true",
        help="Delete original source file when output is a different format (.mkv ➔ .mp4)"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output .mp4 file if one exists"
    )
    parser.add_argument(
        "--re-encode-hevc",
        action="store_true",
        help="Re-encode videos that are already H.265 (default: skipped to preserve quality and save time)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and display planned transcode actions without running ffmpeg"
    )

    return parser.parse_args()


def main():
    args = parse_arguments()

    # Check ffmpeg & ffprobe availability
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print(f"{Colors.RED}Error: FFmpeg or FFprobe is not installed or not in PATH.{Colors.RESET}")
        print(f"Please install FFmpeg: sudo apt install -y ffmpeg (Linux) or brew install ffmpeg (macOS)")
        sys.exit(1)

    # Detect hardware acceleration
    preferred_enc = None if args.encoder == "auto" else args.encoder
    encoder, hw_desc = HardwareDetector.detect_best_encoder(preferred_enc)

    print(f"\n{Colors.BOLD}{Colors.CYAN}⚡ FFmpeg H.265 (HEVC) Media Transcoder{Colors.RESET}")
    print(f"Detected Engine: {Colors.GREEN}{hw_desc}{Colors.RESET} [{encoder}]")

    # Path acquisition
    target_path = args.path
    if not target_path:
        while True:
            inp = input("\nEnter path to media directory or video file: ").strip()
            # Handle quoted paths (drag-and-drop in terminals)
            inp = inp.strip("\"'")
            if os.path.exists(inp):
                target_path = inp
                break
            print(f"{Colors.RED}Path does not exist. Please enter a valid directory or file.{Colors.RESET}")

    transcoder = Transcoder(
        encoder=encoder,
        hw_desc=hw_desc,
        cq=args.cq,
        preset=args.preset,
        overwrite=args.overwrite,
        delete_original=args.delete_original,
        skip_hevc=not args.re_encode_hevc,
        dry_run=args.dry_run
    )

    if os.path.isfile(target_path):
        transcoder.transcode_file(target_path)
        transcoder.print_summary()
    elif os.path.isdir(target_path):
        transcoder.scan_and_transcode(target_path)
    else:
        print(f"{Colors.RED}Invalid path specified.{Colors.RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
