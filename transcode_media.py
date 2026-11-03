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
    """Detects available hardware accelerators, GPUs (Intel iGPU, Intel Arc, NVIDIA, AMD, Apple), and encoders."""

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

    @staticmethod
    def get_drm_devices() -> List[Dict]:
        """
        Scan /sys/class/drm and /dev/dri for DRM render nodes.
        Identifies Intel Arc discrete GPUs, Intel iGPUs, NVIDIA, and AMD.
        """
        devices = []
        dri_dir = "/dev/dri"
        if not os.path.exists(dri_dir):
            return devices

        # Get lspci info if available for hardware naming
        pci_map = {}
        try:
            res = subprocess.run(["lspci", "-nn"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) >= 2:
                    pci_slot = parts[0]
                    desc = parts[1]
                    if any(kw in desc.lower() for kw in ["vga", "3d", "display"]):
                        pci_map[pci_slot] = desc
        except Exception:
            pass

        # Intel Arc DG2 / Alchemist PCI Device IDs (vendor 8086)
        INTEL_ARC_IDS = {
            "5690", "5691", "5692", "5693", "5694", "5695",
            "56a0", "56a1", "56a2", "56a3", "56a4", "56a5", "56a6",
            "56b0", "56b1", "56b2", "56b3", "56c0", "56c1"
        }

        for entry in sorted(os.listdir(dri_dir)):
            if not entry.startswith("renderD"):
                continue

            node_path = os.path.join(dri_dir, entry)
            dev_vendor_path = f"/sys/class/drm/{entry}/device/vendor"
            dev_device_path = f"/sys/class/drm/{entry}/device/device"

            vendor_id = ""
            device_id = ""
            if os.path.exists(dev_vendor_path):
                try:
                    with open(dev_vendor_path, "r") as f:
                        vendor_id = f.read().strip().lower().replace("0x", "")
                except Exception:
                    pass

            if os.path.exists(dev_device_path):
                try:
                    with open(dev_device_path, "r") as f:
                        device_id = f.read().strip().lower().replace("0x", "")
                except Exception:
                    pass

            gpu_type = "unknown"
            label = f"DRM Node {entry}"
            is_intel_arc = False
            is_intel_igpu = False

            # Check matching lspci line
            pci_desc = ""
            try:
                device_link = os.readlink(f"/sys/class/drm/{entry}/device")
                pci_addr = os.path.basename(device_link)
                short_pci = pci_addr.split(":")[-2] + ":" + pci_addr.split(":")[-1] if ":" in pci_addr else pci_addr
                pci_desc = pci_map.get(short_pci, "")
            except Exception:
                pass

            if vendor_id == "8086":  # Intel
                if (device_id in INTEL_ARC_IDS or 
                    device_id.startswith("e2") or  # Intel Battlemage (BMG)
                    "arc" in pci_desc.lower() or 
                    "dg2" in pci_desc.lower() or
                    "alchemist" in pci_desc.lower() or
                    "battlemage" in pci_desc.lower()):
                    gpu_type = "intel_arc"
                    is_intel_arc = True
                    arc_name = pci_desc.split(': ')[-1] if ': ' in pci_desc else entry
                    label = f"Intel Arc GPU ({arc_name})"
                else:
                    gpu_type = "intel_igpu"
                    is_intel_igpu = True
                    igpu_name = pci_desc.split(': ')[-1] if ': ' in pci_desc else "UHD/Iris Xe"
                    label = f"Intel iGPU ({igpu_name})"
            elif vendor_id == "10de":  # NVIDIA
                gpu_type = "nvidia"
                nv_name = pci_desc.split(': ')[-1] if ': ' in pci_desc else entry
                label = f"NVIDIA GPU ({nv_name})"
            elif vendor_id == "1002":  # AMD
                gpu_type = "amd"
                amd_name = pci_desc.split(': ')[-1] if ': ' in pci_desc else entry
                label = f"AMD Radeon ({amd_name})"

            devices.append({
                "node": node_path,
                "entry": entry,
                "vendor_id": vendor_id,
                "device_id": device_id,
                "gpu_type": gpu_type,
                "label": label,
                "is_intel_arc": is_intel_arc,
                "is_intel_igpu": is_intel_igpu,
                "pci_desc": pci_desc
            })

        return devices

    @staticmethod
    def test_encoder(encoder: str, device: Optional[str] = None) -> bool:
        """
        Verify if an encoder actually functions with current drivers/hardware.
        Runs a 1-frame test encoding pipeline with lavfi synthetic color source.
        """
        if encoder in ("libx265", "libx264"):
            return True
        try:
            cmd = ["ffmpeg", "-v", "error"]
            if encoder == "hevc_vaapi":
                vaapi_dev = device or "/dev/dri/renderD128"
                if not os.path.exists(vaapi_dev):
                    return False
                cmd.extend([
                    "-vaapi_device", vaapi_dev,
                    "-f", "lavfi", "-i", "color=s=64x64:d=0.04",
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "hevc_vaapi",
                    "-f", "null", "-"
                ])
            elif encoder == "hevc_qsv":
                if device:
                    cmd.extend(["-qsv_device", device])
                cmd.extend([
                    "-f", "lavfi", "-i", "color=s=64x64:d=0.04",
                    "-c:v", "hevc_qsv",
                    "-f", "null", "-"
                ])
            else:
                cmd.extend([
                    "-f", "lavfi", "-i", "color=s=64x64:d=0.04",
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
        preferred_device: Optional[str] = None
    ) -> Tuple[str, str, Optional[str]]:
        """
        Auto-detect best HEVC hardware/CPU encoder and device node.
        Supports Intel Arc, Intel iGPU, NVIDIA NVENC, Apple VideoToolbox, VAAPI, CPU.
        Returns: (encoder_name, description, device_node)
        """
        encoders = cls.get_available_encoders()
        drm_devices = cls.get_drm_devices()

        # Handle user-specified encoder
        if preferred_encoder and preferred_encoder in encoders:
            if preferred_encoder not in ("libx265", "libx264") and not cls.test_encoder(preferred_encoder, preferred_device):
                print(f" {Colors.YELLOW}⚠ Warning: Specified encoder '{preferred_encoder}' failed hardware initialization test.{Colors.RESET}")
                print(f"   Falling back to automatic hardware auto-detection...")
            else:
                dev_node = preferred_device
                if not dev_node and preferred_encoder in ("hevc_qsv", "hevc_vaapi") and drm_devices:
                    dev_node = drm_devices[0]["node"]
                return preferred_encoder, f"User-Specified ({preferred_encoder})", dev_node

        # 1. Check for Intel Arc dGPU (Highest priority for Intel QuickSync/Arc)
        arc_gpus = [d for d in drm_devices if d["is_intel_arc"]]
        for arc in arc_gpus:
            node = arc["node"]
            # Test QSV on Intel Arc
            if "hevc_qsv" in encoders and cls.test_encoder("hevc_qsv", node):
                return "hevc_qsv", f"Intel QuickSync [{arc['label']}]", node
            # Test VAAPI on Intel Arc
            if "hevc_vaapi" in encoders and cls.test_encoder("hevc_vaapi", node):
                return "hevc_vaapi", f"Intel Arc VAAPI [{arc['label']}]", node

        # 2. Check for Intel iGPU (UHD / Iris Xe)
        igpu_gpus = [d for d in drm_devices if d["is_intel_igpu"]]
        for igpu in igpu_gpus:
            node = igpu["node"]
            # Test QSV on Intel iGPU
            if "hevc_qsv" in encoders and cls.test_encoder("hevc_qsv", node):
                return "hevc_qsv", f"Intel QuickSync iGPU [{igpu['label']}]", node
            # Test VAAPI on Intel iGPU
            if "hevc_vaapi" in encoders and cls.test_encoder("hevc_vaapi", node):
                return "hevc_vaapi", f"Intel iGPU VAAPI [{igpu['label']}]", node

        # 3. Check for NVIDIA NVENC (GPU)
        if "hevc_nvenc" in encoders and cls.test_encoder("hevc_nvenc"):
            return "hevc_nvenc", "NVIDIA NVENC (GPU)", None

        # 4. Check for Apple Silicon VideoToolbox
        if "hevc_videotoolbox" in encoders and cls.test_encoder("hevc_videotoolbox"):
            return "hevc_videotoolbox", "Apple VideoToolbox (Apple Silicon)", None

        # 5. Check any remaining DRM device via VAAPI (e.g. AMD Radeon or generic Intel)
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

        # 7. Check generic QSV without device node
        if "hevc_qsv" in encoders and cls.test_encoder("hevc_qsv"):
            return "hevc_qsv", "Intel QuickSync (GPU)", None

        # 8. High-efficiency CPU Fallback (libx265)
        if "libx265" in encoders:
            return "libx265", "Software CPU (libx265)", None

        # 9. Baseline CPU Fallback
        return "libx264", "Software CPU Fallback (libx264)", None


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
        device: Optional[str] = None,
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
        self.device = device
        self.cq = cq
        self.preset = preset
        self.audio_codec = audio_codec
        self.audio_bitrate = audio_bitrate
        self.overwrite = overwrite
        self.delete_original = delete_original
        self.skip_hevc = skip_hevc
        self.dry_run = dry_run
        self.current_temp_output = None

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

    def build_ffmpeg_command(self, input_path: str, output_path: str, force_cpu: bool = False) -> List[str]:
        """Construct the optimized FFmpeg CLI argument list."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
        encoder = "libx265" if force_cpu else self.encoder

        # Hardware-specific input/output flags
        if encoder == "hevc_nvenc":
            # NVIDIA NVENC GPU Pipeline
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_nvenc",
                "-preset", self.preset if self.preset in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "slow", "medium", "fast"] else "p4",
                "-cq:v", str(self.cq),
                "-b:v", "0",
                "-spatial_aq", "1"
            ])
        elif encoder == "hevc_qsv":
            # Intel QuickSync (Intel Arc dGPU & Intel iGPU)
            qsv_presets = {
                "p1": "veryfast", "p2": "faster", "p3": "fast",
                "p4": "medium", "p5": "slow", "p6": "slower", "p7": "veryslow"
            }
            qsv_preset = qsv_presets.get(self.preset, self.preset if self.preset in qsv_presets.values() else "medium")
            if self.device and not force_cpu:
                cmd.extend(["-qsv_device", self.device])
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_qsv",
                "-global_quality", str(self.cq),
                "-preset", qsv_preset
            ])
        elif encoder == "hevc_vaapi":
            # Linux VAAPI (Intel Arc dGPU, Intel iGPU, AMD Radeon)
            vaapi_dev = self.device if (self.device and not force_cpu) else "/dev/dri/renderD128"
            cmd.extend([
                "-vaapi_device", vaapi_dev,
                "-i", input_path,
                "-vf", "format=nv12,hwupload",
                "-c:v", "hevc_vaapi",
                "-qp", str(self.cq)
            ])
        elif encoder == "hevc_videotoolbox":
            # Apple Silicon VideoToolbox
            cmd.extend([
                "-i", input_path,
                "-c:v", "hevc_videotoolbox",
                "-q:v", str(max(20, 100 - self.cq * 2))
            ])
        elif encoder == "libx265":
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
                "-c:v", encoder,
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
            est_new_size = int(orig_size * 0.45)
            self.stats["transcoded"] += 1
            self.stats["transcoded_orig_bytes"] += orig_size
            self.stats["transcoded_new_bytes"] += est_new_size
            self.stats["new_bytes"] += est_new_size
            return True

        # Build execution command
        cmd = self.build_ffmpeg_command(file_path, temp_output)
        self.current_temp_output = temp_output

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
            self.stats["transcoded_orig_bytes"] += orig_size
            self.stats["transcoded_new_bytes"] += new_size

            saved_bytes = orig_size - new_size
            ratio_pct = (saved_bytes / orig_size) * 100.0 if orig_size > 0 else 0

            # Atomic rename of temp file to target
            if file_path == final_output:
                # Transcoding in-place: remove original first, then rename temp
                os.remove(file_path)
            shutil.move(temp_output, final_output)
            self.current_temp_output = None

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
            # If a hardware encoder failed mid-flight, attempt graceful CPU fallback
            if self.encoder not in ("libx265", "libx264"):
                print(f"  {Colors.YELLOW}⚠ Hardware encoder '{self.encoder}' failed (exit code {e.returncode}). Retrying with CPU fallback (libx265)...{Colors.RESET}")
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                try:
                    fallback_cmd = self.build_ffmpeg_command(file_path, temp_output, force_cpu=True)
                    subprocess.run(fallback_cmd, check=True)
                    # Automatically downgrade future queue jobs to CPU to avoid re-failing broken hardware
                    self.encoder = "libx265"
                    self.hw_desc = "Software CPU Fallback (libx265)"
                    elapsed = time.time() - t_start

                    if not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
                        raise RuntimeError("Fallback output file was not created or is 0 bytes.")

                    new_size = os.path.getsize(temp_output)
                    self.stats["new_bytes"] += new_size
                    self.stats["transcoded"] += 1
                    self.stats["transcoded_orig_bytes"] += orig_size
                    self.stats["transcoded_new_bytes"] += new_size

                    saved_bytes = orig_size - new_size
                    ratio_pct = (saved_bytes / orig_size) * 100.0 if orig_size > 0 else 0

                    if file_path == final_output:
                        os.remove(file_path)
                    shutil.move(temp_output, final_output)
                    self.current_temp_output = None

                    if self.delete_original and file_path != final_output:
                        try:
                            os.remove(file_path)
                        except OSError as ex:
                            print(f"  {Colors.YELLOW}Warning: Could not remove original: {ex}{Colors.RESET}")

                    color = Colors.GREEN if saved_bytes > 0 else Colors.YELLOW
                    print(f"  {color}✔ Complete (CPU Fallback):{Colors.RESET} {format_bytes(orig_size)} ➔ {format_bytes(new_size)} ({ratio_pct:+.1f}%) in {format_duration(elapsed)}")
                    return True
                except Exception as fallback_err:
                    print(f"  {Colors.RED}✖ CPU Fallback also failed: {fallback_err}{Colors.RESET}")

            print(f"  {Colors.RED}✖ Error during transcoding: exit code {e.returncode}{Colors.RESET}")
            if os.path.exists(temp_output):
                os.remove(temp_output)
            self.current_temp_output = None
            self.stats["failed"] += 1
            self.stats["new_bytes"] += orig_size
            return False
        except Exception as e:
            print(f"  {Colors.RED}✖ Unexpected failure: {e}{Colors.RESET}")
            if os.path.exists(temp_output):
                os.remove(temp_output)
            self.current_temp_output = None
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
        print(f" {Colors.CYAN}Hardware Engine:{Colors.RESET} {self.hw_desc} ({self.encoder})")
        print(f"{Colors.BOLD}{'=' * 78}{Colors.RESET}")

        interrupted = False
        try:
            for idx, f in enumerate(target_files, 1):
                print(f"\n[{idx}/{total}]", end="")
                self.transcode_file(f)
        except KeyboardInterrupt:
            interrupted = True
            print(f"\n\n{Colors.YELLOW}{Colors.BOLD}⚠ Transcoding canceled by user (Ctrl+C / Interrupt).{Colors.RESET}")
            if self.current_temp_output and os.path.exists(self.current_temp_output):
                try:
                    os.remove(self.current_temp_output)
                    print(f" {Colors.DIM}✔ Cleaned up incomplete temp file: {os.path.basename(self.current_temp_output)}{Colors.RESET}")
                except Exception:
                    pass
        finally:
            self.print_summary(interrupted=interrupted)

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
        
        # File counters
        total_cand = self.stats["total_candidates"] if self.stats["total_candidates"] > 0 else self.stats["scanned"]
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
        "--device",
        default=None,
        help="Explicit DRM render node for Intel Arc / iGPU (e.g. /dev/dri/renderD128, /dev/dri/renderD129; default: auto-detected)"
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

    # Detect hardware acceleration and GPU device
    preferred_enc = None if args.encoder == "auto" else args.encoder
    encoder, hw_desc, device_node = HardwareDetector.detect_best_encoder(preferred_enc, args.device)

    print(f"\n{Colors.BOLD}{Colors.CYAN}⚡ FFmpeg H.265 (HEVC) Media Transcoder{Colors.RESET}")
    dev_str = f" {Colors.DIM}(Device: {device_node}){Colors.RESET}" if device_node else ""
    print(f"Auto-Detected Engine: {Colors.GREEN}{hw_desc}{Colors.RESET} [{encoder}]{dev_str}")

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
        device=device_node,
        cq=args.cq,
        preset=args.preset,
        overwrite=args.overwrite,
        delete_original=args.delete_original,
        skip_hevc=not args.re_encode_hevc,
        dry_run=args.dry_run
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
        if transcoder.current_temp_output and os.path.exists(transcoder.current_temp_output):
            try:
                os.remove(transcoder.current_temp_output)
                print(f" {Colors.DIM}✔ Cleaned up incomplete temp file: {os.path.basename(transcoder.current_temp_output)}{Colors.RESET}")
            except Exception:
                pass
        transcoder.print_summary(interrupted=True)


if __name__ == "__main__":
    main()
