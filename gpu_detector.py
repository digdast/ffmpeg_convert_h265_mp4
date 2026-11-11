#!/usr/bin/env python3
"""
Hardware & Multi-GPU Discovery Engine for FFmpeg Transcoding.
Enumerates and validates all installed GPUs:
- NVIDIA GPUs via nvidia-smi / NVENC (-gpu 0, -gpu 1, ...)
- Intel iGPU & Arc dGPU via QSV / D3D11 / VAAPI
- AMD Radeon GPUs via AMF / VAAPI
- Apple Silicon VideoToolbox
- Software Multi-Threaded CPU (libx265 / libx264)
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


def get_ffmpeg_path() -> str:
    """Find the best ffmpeg executable path on Windows, Linux, or macOS."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            winget_pattern = os.path.join(local_app, "Microsoft", "WinGet", "Packages", "**", "ffmpeg.exe")
            candidates = glob.glob(winget_pattern, recursive=True)
            for c in candidates:
                if os.path.isfile(c):
                    return c
    return "ffmpeg"


def get_ffprobe_path() -> str:
    """Find the best ffprobe executable path."""
    found = shutil.which("ffprobe")
    if found:
        return found
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            winget_pattern = os.path.join(local_app, "Microsoft", "WinGet", "Packages", "**", "ffprobe.exe")
            candidates = glob.glob(winget_pattern, recursive=True)
            for c in candidates:
                if os.path.isfile(c):
                    return c
    return "ffprobe"


class GPUCard:
    """Represents a discovered and verified GPU hardware device."""
    def __init__(
        self,
        device_id: str,
        vendor: str,
        name: str,
        encoder: str,
        index: int = 0,
        vram_mb: Optional[int] = None,
        extra_args: Optional[List[str]] = None,
        device_node: Optional[str] = None
    ):
        self.device_id = device_id
        self.vendor = vendor  # "NVIDIA", "Intel", "AMD", "Apple", "CPU"
        self.name = name
        self.encoder = encoder  # "hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_vaapi", "libx265"
        self.index = index
        self.vram_mb = vram_mb
        self.extra_args = extra_args or []
        self.device_node = device_node
        self.is_functional = False

    @property
    def display_name(self) -> str:
        mem_str = f" ({self.vram_mb // 1024} GB)" if self.vram_mb and self.vram_mb > 0 else ""
        return f"{self.name}{mem_str} [{self.encoder}]"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "vendor": self.vendor,
            "name": self.name,
            "encoder": self.encoder,
            "index": self.index,
            "vram_mb": self.vram_mb,
            "extra_args": self.extra_args,
            "device_node": self.device_node,
            "is_functional": self.is_functional,
            "display_name": self.display_name
        }


class MultiGPUManager:
    """Manages multi-GPU hardware detection and verification."""

    def __init__(self):
        self.ffmpeg_bin = get_ffmpeg_path()
        self.ffprobe_bin = get_ffprobe_path()
        self._available_encoders: Optional[List[str]] = None

    def get_supported_encoders(self) -> List[str]:
        if self._available_encoders is not None:
            return self._available_encoders
        try:
            res = subprocess.run(
                [self.ffmpeg_bin, "-encoders"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            encoders = []
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    encoders.append(parts[1])
            self._available_encoders = encoders
            return encoders
        except Exception:
            self._available_encoders = ["libx265", "libx264"]
            return self._available_encoders

    def test_encoder(self, encoder: str, extra_args: Optional[List[str]] = None, device_node: Optional[str] = None) -> bool:
        """Runs a test transcode to verify hardware ASIC functionality."""
        if encoder in ("libx265", "libx264"):
            return True
        cmd = [self.ffmpeg_bin, "-v", "error"]
        if encoder == "hevc_vaapi":
            node = device_node or "/dev/dri/renderD128"
            if not os.path.exists(node):
                return False
            cmd.extend([
                "-vaapi_device", node,
                "-f", "lavfi", "-i", "color=s=256x256:d=0.04",
                "-vf", "format=nv12,hwupload",
                "-c:v", "hevc_vaapi",
                "-f", "null", "-"
            ])
        elif encoder == "hevc_qsv":
            if device_node:
                cmd.extend(["-qsv_device", device_node])
            cmd.extend([
                "-f", "lavfi", "-i", "color=s=256x256:d=0.04",
                "-vf", "format=nv12",
                "-c:v", "hevc_qsv",
                "-f", "null", "-"
            ])
        else:
            cmd.extend([
                "-f", "lavfi", "-i", "color=s=256x256:d=0.04",
                "-c:v", encoder
            ])
            if extra_args:
                cmd.extend(extra_args)
            cmd.extend(["-f", "null", "-"])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
            return res.returncode == 0
        except Exception:
            return False

    def detect_all_devices(self) -> List[GPUCard]:
        """Detect and return all verified GPU devices and CPU fallback."""
        devices: List[GPUCard] = []
        supported_encs = self.get_supported_encoders()

        # 1. Detect NVIDIA GPUs
        smi = shutil.which("nvidia-smi")
        if smi:
            try:
                res = subprocess.run(
                    [smi, "--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if res.returncode == 0:
                    for line in res.stdout.strip().splitlines():
                        if not line.strip():
                            continue
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 2:
                            idx = int(parts[0])
                            name = parts[1]
                            vram = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
                            vram_used = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
                            card = GPUCard(
                                device_id=f"nvidia:{idx}",
                                vendor="NVIDIA",
                                name=name,
                                encoder="hevc_nvenc",
                                index=idx,
                                vram_mb=vram,
                                extra_args=["-gpu", str(idx)]
                            )
                            if "hevc_nvenc" in supported_encs:
                                # Verify encoder; physical NVIDIA cards detected via nvidia-smi are always retained
                                # so transient VRAM pressure from LLMs (Ollama) doesn't hide the card
                                is_tested = self.test_encoder("hevc_nvenc", card.extra_args)
                                card.is_functional = is_tested or (vram is not None and vram > 2000)
                                devices.append(card)
            except Exception:
                pass

        # 2. Windows CIM / WMI Video Controllers
        if os.name == "nt":
            try:
                cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object DeviceID, Name, AdapterRAM | ConvertTo-Json"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if res.returncode == 0 and res.stdout.strip():
                    data = json.loads(res.stdout)
                    if isinstance(data, dict):
                        data = [data]
                    for item in data:
                        name = item.get("Name", "")
                        lower_name = name.lower()
                        adapter_ram = int(item.get("AdapterRAM", 0) or 0)
                        vram_mb = adapter_ram // (1024 * 1024) if adapter_ram > 0 else None

                        if "intel" in lower_name and ("uhd" in lower_name or "iris" in lower_name or "arc" in lower_name or "graphics" in lower_name):
                            if "hevc_qsv" in supported_encs and self.test_encoder("hevc_qsv"):
                                devices.append(GPUCard(
                                    device_id="intel:qsv",
                                    vendor="Intel",
                                    name=name,
                                    encoder="hevc_qsv",
                                    index=len([d for d in devices if d.vendor == "Intel"]),
                                    vram_mb=vram_mb,
                                    extra_args=["-vf", "format=nv12"]
                                ))
                                devices[-1].is_functional = True
                        elif ("radeon" in lower_name or "amd" in lower_name) and "hevc_amf" in supported_encs:
                            if self.test_encoder("hevc_amf"):
                                devices.append(GPUCard(
                                    device_id="amd:amf",
                                    vendor="AMD",
                                    name=name,
                                    encoder="hevc_amf",
                                    index=len([d for d in devices if d.vendor == "AMD"]),
                                    vram_mb=vram_mb,
                                    extra_args=[]
                                ))
                                devices[-1].is_functional = True
            except Exception:
                pass

        # 3. Linux DRM Render Nodes
        if os.path.exists("/dev/dri"):
            by_path = "/dev/dri/by-path"
            nodes = []
            if os.path.exists(by_path):
                for e in sorted(os.listdir(by_path)):
                    if e.endswith("-render"):
                        nodes.append(os.path.realpath(os.path.join(by_path, e)))
            if not nodes:
                for f in sorted(os.listdir("/dev/dri")):
                    if f.startswith("renderD"):
                        nodes.append(os.path.join("/dev/dri", f))

            for idx, node in enumerate(nodes):
                # Test QSV / VAAPI on this node
                if "hevc_qsv" in supported_encs and self.test_encoder("hevc_qsv", device_node=node):
                    devices.append(GPUCard(
                        device_id=f"dri_qsv:{idx}",
                        vendor="Intel",
                        name=f"Intel QSV ({os.path.basename(node)})",
                        encoder="hevc_qsv",
                        index=idx,
                        device_node=node,
                        extra_args=["-qsv_device", node, "-vf", "format=nv12"]
                    ))
                    devices[-1].is_functional = True
                elif "hevc_vaapi" in supported_encs and self.test_encoder("hevc_vaapi", device_node=node):
                    devices.append(GPUCard(
                        device_id=f"dri_vaapi:{idx}",
                        vendor="Linux-DRM",
                        name=f"VAAPI Hardware ({os.path.basename(node)})",
                        encoder="hevc_vaapi",
                        index=idx,
                        device_node=node,
                        extra_args=["-vaapi_device", node]
                    ))
                    devices[-1].is_functional = True

        # 4. Apple Silicon VideoToolbox
        if "hevc_videotoolbox" in supported_encs and self.test_encoder("hevc_videotoolbox"):
            devices.append(GPUCard(
                device_id="apple:videotoolbox",
                vendor="Apple",
                name="Apple Silicon VideoToolbox",
                encoder="hevc_videotoolbox",
                index=0,
                extra_args=[]
            ))
            devices[-1].is_functional = True

        # Remove duplicate device registrations if any
        seen_ids = set()
        unique_devices = []
        for d in devices:
            if d.device_id not in seen_ids:
                seen_ids.add(d.device_id)
                unique_devices.append(d)

        # 5. Always provide Software CPU worker as fallback/companion
        cpu_enc = "libx265" if "libx265" in supported_encs else "libx264"
        cpu_card = GPUCard(
            device_id="cpu:software",
            vendor="CPU",
            name=f"Multi-Core CPU ({os.cpu_count() or 4} cores)",
            encoder=cpu_enc,
            index=0,
            extra_args=[]
        )
        cpu_card.is_functional = True
        unique_devices.append(cpu_card)

        return unique_devices


if __name__ == "__main__":
    mgr = MultiGPUManager()
    devs = mgr.detect_all_devices()
    print("=== Discovered Hardware Acceleration Devices ===")
    for d in devs:
        print(f" • [{d.vendor}] {d.name} -> {d.encoder} (Device: {d.device_id}, Extra: {d.extra_args})")
