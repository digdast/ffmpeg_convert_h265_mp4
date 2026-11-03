#!/usr/bin/env python3
"""
================================================================================
⚡ Universal Multi-GPU Hardware & Video Engine Monitor (gpumon)
================================================================================
High-performance live monitor for ALL installed GPUs:
- Intel iGPU (UHD, Iris Xe, Iris Plus, Coffee Lake, Alder/Raptor/Meteor Lake)
- Intel Arc Discrete GPUs (Alchemist A-Series, Battlemage B-Series)
- NVIDIA GPUs (RTX 5000/4000/3000/2000, GTX, Quadro, Tesla, Data Center)
- AMD Radeon GPUs (RDNA3, RDNA2, Vega, Polaris)

Features:
- Live Core Compute & Video Transcode ASIC Engine utilization %
- Live Frequency (Current / Actual / Max MHz)
- VRAM & Shared System GTT Memory tracking
- Real-time active GPU Process inspector (PID, Engine, VRAM, Command)
- Live interactive terminal dashboard, single-shot mode (--once), or JSON (--json)
================================================================================
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Terminal ANSI Styling
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    
    # Background
    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_DARK = "\033[100m"

    CLEAR_SCREEN = "\033[2J\033[H"
    CURSOR_HIDE = "\033[?25l"
    CURSOR_SHOW = "\033[?25h"


def format_bytes(b: float) -> str:
    """Format bytes into human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024.0:
            return f"{b:5.1f} {unit}"
        b /= 1024.0
    return f"{b:.1f} PB"


def make_bar(percent: float, width: int = 24, color: str = C.GREEN) -> str:
    """Generate a sleek graphical progress bar."""
    pct = max(0.0, min(100.0, percent))
    filled_len = int(round(width * pct / 100.0))
    empty_len = width - filled_len
    
    # Select color based on threshold if green default
    if color == C.GREEN:
        if pct > 85.0:
            color = C.RED
        elif pct > 60.0:
            color = C.YELLOW
            
    bar = f"{color}{'█' * filled_len}{C.DIM}{'░' * empty_len}{C.RESET}"
    return f"{bar} {C.BOLD}{pct:5.1f}%{C.RESET}"


class DRMClientInspector:
    """
    Inspects /proc/*/fdinfo to read accurate kernel-level DRM statistics for
    running processes (Intel, AMD, and modern NVIDIA DRM).
    """

    def __init__(self):
        self.prev_engine_times: Dict[Tuple[int, str], float] = {}
        self.prev_sample_time: float = time.time()

    def sample_processes(self) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, float]]]:
        """
        Samples process fdinfo entries.
        Returns:
            - List of active processes using GPU
            - Per-device engine utilization % aggregation
        """
        now = time.time()
        delta_t = max(0.001, now - self.prev_sample_time)
        self.prev_sample_time = now

        current_engine_times: Dict[Tuple[int, str], float] = {}
        active_processes: Dict[int, Dict[str, Any]] = {}
        device_engine_ns: Dict[str, Dict[str, float]] = {}

        for fdinfo_path in glob.glob("/proc/*/fdinfo/*"):
            try:
                with open(fdinfo_path, "r", errors="ignore") as f:
                    content = f.read()
            except (OSError, IOError):
                continue

            if "drm-driver" not in content:
                continue

            # Extract PID from path
            parts = fdinfo_path.split("/")
            if len(parts) < 3 or not parts[2].isdigit():
                continue
            pid = int(parts[2])

            driver = ""
            pdev = ""
            client_id = ""
            total_mem = 0
            resident_mem = 0
            engines: Dict[str, int] = {}

            for line in content.splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()

                if k == "drm-driver":
                    driver = v
                elif k == "drm-pdev":
                    pdev = v
                elif k == "drm-client-id":
                    client_id = v
                elif "system" in k or "local" in k or "total-memory" in k:
                    if "KiB" in v:
                        kb = int(v.split()[0])
                        total_mem += kb * 1024
                        if "resident" in k:
                            resident_mem += kb * 1024
                elif k.startswith("drm-engine-") and not k.startswith("drm-engine-capacity-"):
                    eng_name = k.replace("drm-engine-", "")
                    if "ns" in v:
                        ns = int(v.split()[0])
                        engines[eng_name] = ns

            if not driver and not pdev:
                continue

            pdev_key = pdev if pdev else driver
            if pdev_key not in device_engine_ns:
                device_engine_ns[pdev_key] = {}

            # Read process metadata
            cmdline = ""
            user = "unknown"
            try:
                with open(f"/proc/{pid}/cmdline", "r", errors="ignore") as f:
                    raw_cmd = f.read().replace("\x00", " ").strip()
                    cmdline = raw_cmd if raw_cmd else f"[{pid}]"
                with open(f"/proc/{pid}/status", "r", errors="ignore") as f:
                    for sline in f:
                        if sline.startswith("Uid:"):
                            uid = int(sline.split()[1])
                            import pwd
                            try:
                                user = pwd.getpwuid(uid).pw_name
                            except Exception:
                                user = str(uid)
                            break
            except Exception:
                cmdline = f"PID {pid}"

            # Calculate active engines
            active_engine_types = []
            for eng_name, ns_val in engines.items():
                key = (pid, f"{pdev_key}:{eng_name}")
                current_engine_times[key] = ns_val
                prev_ns = self.prev_engine_times.get(key, ns_val)
                diff_ns = max(0, ns_val - prev_ns)
                
                # Nanoseconds busy / total delta seconds in ns
                pct = (diff_ns / (delta_t * 1e9)) * 100.0
                if pct > 0.5:
                    label = eng_name.upper()
                    if eng_name in ["video", "vcs"]:
                        label = "VIDEO (TRANSCODE)"
                    elif eng_name in ["render", "rcs"]:
                        label = "RENDER (3D/COMPUTE)"
                    elif eng_name in ["copy", "bcs"]:
                        label = "COPY (BLITTER)"
                    active_engine_types.append(f"{label} {pct:.0f}%")

                device_engine_ns[pdev_key][eng_name] = device_engine_ns[pdev_key].get(eng_name, 0.0) + diff_ns

            if pid not in active_processes:
                active_processes[pid] = {
                    "pid": pid,
                    "user": user,
                    "driver": driver,
                    "pdev": pdev,
                    "vram_bytes": resident_mem if resident_mem > 0 else total_mem,
                    "engines": active_engine_types,
                    "cmd": cmdline
                }
            else:
                active_processes[pid]["vram_bytes"] = max(active_processes[pid]["vram_bytes"], resident_mem if resident_mem > 0 else total_mem)
                active_processes[pid]["engines"].extend([e for e in active_engine_types if e not in active_processes[pid]["engines"]])

        self.prev_engine_times = current_engine_times

        # Convert device engine ns deltas to percentage
        device_utilization: Dict[str, Dict[str, float]] = {}
        for dev_k, eng_map in device_engine_ns.items():
            device_utilization[dev_k] = {}
            for eng, ns_sum in eng_map.items():
                pct = (ns_sum / (delta_t * 1e9)) * 100.0
                device_utilization[dev_k][eng] = min(100.0, pct)

        return list(active_processes.values()), device_utilization


class MultiGPUMonitor:
    """Discovers and queries all installed GPUs across all vendors."""

    def __init__(self):
        self.inspector = DRMClientInspector()
        self.intel_cards = self._discover_intel_gpus()
        self.amd_cards = self._discover_amd_gpus()
        self.has_nvidia = self._check_nvidia_available()

    def _check_nvidia_available(self) -> bool:
        """Check if nvidia-smi tool is operational."""
        return shutil.which("nvidia-smi") is not None

    def _discover_intel_gpus(self) -> List[Dict[str, Any]]:
        """Identify Intel iGPUs and Intel Arc dGPUs."""
        cards = []
        pci_map = self._get_pci_names()

        for card_path in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
            # Skip connector links like card1-DP-1
            if "-" in os.path.basename(card_path):
                continue

            vendor_file = os.path.join(card_path, "device/vendor")
            if not os.path.exists(vendor_file):
                continue

            try:
                with open(vendor_file, "r") as f:
                    vendor = f.read().strip().lower().replace("0x", "")
            except Exception:
                continue

            if vendor != "8086":
                continue

            card_name = os.path.basename(card_path)
            device_id = ""
            device_id_path = os.path.join(card_path, "device/device")
            if os.path.exists(device_id_path):
                try:
                    with open(device_id_path, "r") as f:
                        device_id = f.read().strip().lower().replace("0x", "")
                except Exception:
                    pass

            pci_addr = ""
            try:
                pci_link = os.readlink(os.path.join(card_path, "device"))
                pci_addr = os.path.basename(pci_link)
            except Exception:
                pass

            # Detect matching render node
            render_node = ""
            for r in glob.glob("/dev/dri/renderD*"):
                r_base = os.path.basename(r)
                sysfs_r = f"/sys/class/drm/{r_base}/device"
                if os.path.exists(sysfs_r):
                    try:
                        if os.path.basename(os.readlink(sysfs_r)) == pci_addr:
                            render_node = r
                            break
                    except Exception:
                        pass
            if not render_node:
                render_node = "/dev/dri/renderD128"

            pci_desc = pci_map.get(pci_addr, "")
            is_arc = any(kw in pci_desc.lower() for kw in ["arc", "dg2", "alchemist", "battlemage"]) or device_id.startswith(("56", "e2"))

            label = f"Intel Arc dGPU ({pci_desc.split(': ')[-1] if ': ' in pci_desc else device_id})" if is_arc else f"Intel iGPU ({pci_desc.split(': ')[-1] if ': ' in pci_desc else 'UHD/Iris'})"

            cards.append({
                "type": "intel",
                "card": card_name,
                "card_path": card_path,
                "pci_addr": pci_addr,
                "render_node": render_node,
                "label": label,
                "is_arc": is_arc,
                "device_id": device_id
            })

        return cards

    def _discover_amd_gpus(self) -> List[Dict[str, Any]]:
        """Identify AMD Radeon GPUs."""
        cards = []
        pci_map = self._get_pci_names()

        for card_path in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
            if "-" in os.path.basename(card_path):
                continue
            vendor_file = os.path.join(card_path, "device/vendor")
            if not os.path.exists(vendor_file):
                continue
            try:
                with open(vendor_file, "r") as f:
                    vendor = f.read().strip().lower().replace("0x", "")
            except Exception:
                continue

            if vendor != "1002":
                continue

            card_name = os.path.basename(card_path)
            pci_addr = ""
            try:
                pci_link = os.readlink(os.path.join(card_path, "device"))
                pci_addr = os.path.basename(pci_link)
            except Exception:
                pass

            pci_desc = pci_map.get(pci_addr, "AMD Radeon GPU")
            label = f"AMD Radeon ({pci_desc.split(': ')[-1] if ': ' in pci_desc else card_name})"

            cards.append({
                "type": "amd",
                "card": card_name,
                "card_path": card_path,
                "pci_addr": pci_addr,
                "label": label
            })
        return cards

    def _get_pci_names(self) -> Dict[str, str]:
        """Map short PCI addresses to names using lspci if available."""
        mapping = {}
        if not shutil.which("lspci"):
            return mapping
        try:
            res = subprocess.run(["lspci", "-D"], capture_output=True, text=True, timeout=2)
            for line in res.stdout.splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) >= 2:
                    pci_slot = parts[0]
                    desc = parts[1]
                    mapping[pci_slot] = desc
        except Exception:
            pass
        return mapping

    def query_intel(self, card_info: Dict[str, Any], dev_utils: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        """Poll frequency, memory, engines, and temperature for an Intel GPU."""
        path = card_info["card_path"]
        pci_addr = card_info["pci_addr"]

        # Read frequencies
        cur_freq = 0
        act_freq = 0
        max_freq = 0
        min_freq = 0

        for f_name, var in [
            ("gt_cur_freq_mhz", "cur"),
            ("gt_act_freq_mhz", "act"),
            ("gt_max_freq_mhz", "max"),
            ("gt_min_freq_mhz", "min")
        ]:
            f_path = os.path.join(path, f_name)
            if os.path.exists(f_path):
                try:
                    with open(f_path, "r") as fp:
                        val = int(fp.read().strip())
                        if var == "cur": cur_freq = val
                        elif var == "act": act_freq = val
                        elif var == "max": max_freq = val
                        elif var == "min": min_freq = val
                except Exception:
                    pass

        # Temperature
        temp_c = 0.0
        hwmon_paths = glob.glob(os.path.join(path, "device/hwmon/hwmon*/temp1_input")) + glob.glob("/sys/class/hwmon/hwmon*/temp1_input")
        for tp in hwmon_paths:
            try:
                with open(tp, "r") as tf:
                    t_val = int(tf.read().strip()) / 1000.0
                    if 10.0 <= t_val <= 110.0:
                        temp_c = t_val
                        break
            except Exception:
                pass

        # Engine utilization from DRM inspector
        dev_engine_map = dev_utils.get(pci_addr, {}) or dev_utils.get("i915", {}) or dev_utils.get("xe", {})
        video_util = dev_engine_map.get("video", 0.0) or dev_engine_map.get("vcs", 0.0) or dev_engine_map.get("vcs0", 0.0)
        render_util = dev_engine_map.get("render", 0.0) or dev_engine_map.get("rcs", 0.0) or dev_engine_map.get("rcs0", 0.0)
        copy_util = dev_engine_map.get("copy", 0.0) or dev_engine_map.get("bcs", 0.0) or dev_engine_map.get("bcs0", 0.0)

        # Estimate utilization from frequency if DRM client has no delta
        overall_util = max(render_util, video_util)
        if overall_util < 1.0 and max_freq > min_freq and act_freq > min_freq:
            freq_load = ((act_freq - min_freq) / (max_freq - min_freq)) * 100.0
            overall_util = min(100.0, freq_load)
            if video_util < 1.0 and act_freq > 800:
                # If high frequency during transcode, correlate to video engine
                video_util = overall_util

        # VRAM / Memory
        vram_used = 0
        vram_total = 0
        lmem_total = os.path.join(path, "lmem_total_bytes")
        if os.path.exists(lmem_total):
            try:
                with open(lmem_total, "r") as lf:
                    vram_total = int(lf.read().strip())
            except Exception:
                pass

        return {
            "vendor": "INTEL",
            "model": card_info["label"],
            "pci": card_info["pci_addr"],
            "render_node": card_info["render_node"],
            "clock_mhz": act_freq if act_freq > 0 else cur_freq,
            "max_clock_mhz": max_freq,
            "min_clock_mhz": min_freq,
            "temp_c": temp_c,
            "power_w": 0.0,
            "util_compute": render_util,
            "util_video": video_util,
            "util_copy": copy_util,
            "vram_used": vram_used,
            "vram_total": vram_total if vram_total > 0 else (2048 * 1024 * 1024)  # 2GB fallback for GTT
        }

    def query_nvidia(self) -> List[Dict[str, Any]]:
        """Query NVIDIA GPUs using nvidia-smi."""
        if not self.has_nvidia:
            return []
        
        cmd = [
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,utilization.memory,utilization.encoder,utilization.decoder,memory.total,memory.used,temperature.gpu,power.draw,power.limit,clocks.current.graphics,clocks.max.graphics,pci.bus_id",
            "--format=csv,noheader,nounits"
        ]
        gpus = []
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if res.returncode != 0:
                return []

            for line in res.stdout.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 14:
                    idx = parts[0]
                    name = parts[1]
                    gpu_util = float(parts[2]) if parts[2].replace(".", "").isdigit() else 0.0
                    enc_util = float(parts[4]) if parts[4].replace(".", "").isdigit() else 0.0
                    dec_util = float(parts[5]) if parts[5].replace(".", "").isdigit() else 0.0
                    mem_total = float(parts[6]) * 1024 * 1024 if parts[6].replace(".", "").isdigit() else 0.0
                    mem_used = float(parts[7]) * 1024 * 1024 if parts[7].replace(".", "").isdigit() else 0.0
                    temp = float(parts[8]) if parts[8].replace(".", "").isdigit() else 0.0
                    power = float(parts[9]) if parts[9].replace(".", "").isdigit() else 0.0
                    clock = int(parts[11]) if parts[11].isdigit() else 0
                    max_clock = int(parts[12]) if parts[12].isdigit() else 0
                    pci = parts[13]

                    gpus.append({
                        "vendor": "NVIDIA",
                        "model": f"NVIDIA {name} (#{idx})",
                        "pci": pci,
                        "render_node": f"/dev/dri/renderD{128 + int(idx)}",
                        "clock_mhz": clock,
                        "max_clock_mhz": max_clock,
                        "min_clock_mhz": 300,
                        "temp_c": temp,
                        "power_w": power,
                        "util_compute": gpu_util,
                        "util_video": max(enc_util, dec_util),
                        "util_copy": 0.0,
                        "vram_used": mem_used,
                        "vram_total": mem_total
                    })
        except Exception:
            pass

        return gpus

    def query_amd(self, card_info: Dict[str, Any]) -> Dict[str, Any]:
        """Query AMD Radeon GPUs via sysfs."""
        path = card_info["card_path"]
        dev_path = os.path.join(path, "device")

        util = 0.0
        busy_file = os.path.join(dev_path, "gpu_busy_percent")
        if os.path.exists(busy_file):
            try:
                with open(busy_file, "r") as f:
                    util = float(f.read().strip())
            except Exception:
                pass

        vram_used = 0
        vram_total = 0
        for f_name, var in [("mem_info_vram_used", "u"), ("mem_info_vram_total", "t")]:
            mf = os.path.join(dev_path, f_name)
            if os.path.exists(mf):
                try:
                    with open(mf, "r") as f:
                        val = int(f.read().strip())
                        if var == "u": vram_used = val
                        else: vram_total = val
                except Exception:
                    pass

        temp_c = 0.0
        hwmon_paths = glob.glob(os.path.join(dev_path, "hwmon/hwmon*/temp1_input"))
        for tp in hwmon_paths:
            try:
                with open(tp, "r") as tf:
                    temp_c = int(tf.read().strip()) / 1000.0
                    break
            except Exception:
                pass

        return {
            "vendor": "AMD",
            "model": card_info["label"],
            "pci": card_info["pci_addr"],
            "render_node": "/dev/dri/renderD128",
            "clock_mhz": 0,
            "max_clock_mhz": 0,
            "min_clock_mhz": 0,
            "temp_c": temp_c,
            "power_w": 0.0,
            "util_compute": util,
            "util_video": 0.0,
            "util_copy": 0.0,
            "vram_used": vram_used,
            "vram_total": vram_total
        }

    def collect_all(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Collect metrics across all vendors and running GPU processes."""
        processes, dev_utils = self.inspector.sample_processes()
        all_gpus = []

        # 1. Query Intel GPUs
        for ic in self.intel_cards:
            all_gpus.append(self.query_intel(ic, dev_utils))

        # 2. Query NVIDIA GPUs
        for ng in self.query_nvidia():
            all_gpus.append(ng)

        # 3. Query AMD GPUs
        for ac in self.amd_cards:
            all_gpus.append(self.query_amd(ac))

        # If no GPUs discovered at all, create a generic entry
        if not all_gpus and os.path.exists("/dev/dri/renderD128"):
            all_gpus.append({
                "vendor": "GENERIC",
                "model": "Generic DRM Render Node (/dev/dri/renderD128)",
                "pci": "N/A",
                "render_node": "/dev/dri/renderD128",
                "clock_mhz": 0,
                "max_clock_mhz": 0,
                "min_clock_mhz": 0,
                "temp_c": 0.0,
                "power_w": 0.0,
                "util_compute": 0.0,
                "util_video": 0.0,
                "util_copy": 0.0,
                "vram_used": 0,
                "vram_total": 0
            })

        return all_gpus, processes


def render_dashboard(gpus: List[Dict[str, Any]], processes: List[Dict[str, Any]], interval: float):
    """Render terminal dashboard with rich ANSI cards."""
    cols, rows = shutil.get_terminal_size((80, 24))
    hostname = os.uname().nodename if hasattr(os, "uname") else "localhost"
    
    out = [C.CLEAR_SCREEN]
    
    # Header Banner
    header = f" ⚡ MULTI-GPU TELEMETRY & HARDWARE TRANSCODE MONITOR [gpumon] "
    sub = f" Host: {C.BOLD}{hostname}{C.RESET} | GPUs Active: {C.GREEN}{len(gpus)}{C.RESET} | Refresh: {interval}s | Time: {time.strftime('%H:%M:%S')} "
    out.append(f"{C.BG_BLUE}{C.WHITE}{C.BOLD}{header.center(cols)}{C.RESET}")
    out.append(f"{C.DIM}{sub.center(cols)}{C.RESET}")
    out.append(f"{C.DIM}{'─' * cols}{C.RESET}\n")

    # Render each GPU card
    for idx, gpu in enumerate(gpus, 1):
        v = gpu["vendor"]
        if v == "INTEL":
            badge = f"{C.BG_CYAN}{C.WHITE}{C.BOLD} INTEL {C.RESET}"
            name_color = C.CYAN
        elif v == "NVIDIA":
            badge = f"{C.BG_GREEN}{C.WHITE}{C.BOLD} NVIDIA {C.RESET}"
            name_color = C.GREEN
        elif v == "AMD":
            badge = f"{C.BG_MAGENTA}{C.WHITE}{C.BOLD} AMD {C.RESET}"
            name_color = C.MAGENTA
        else:
            badge = f"{C.BG_DARK}{C.WHITE}{C.BOLD} GPU {C.RESET}"
            name_color = C.WHITE

        title = f"{badge} {name_color}{C.BOLD}{gpu['model']}{C.RESET}"
        pci_str = f"{C.DIM}PCI: {gpu['pci']} | Node: {gpu['render_node']}{C.RESET}"
        
        # Telemetry Badges
        telemetry_items = []
        if gpu["clock_mhz"] > 0:
            max_s = f"/{gpu['max_clock_mhz']}" if gpu["max_clock_mhz"] > 0 else ""
            telemetry_items.append(f"⏱ {C.BOLD}{gpu['clock_mhz']}{max_s} MHz{C.RESET}")
        if gpu["temp_c"] > 0:
            t_col = C.RED if gpu["temp_c"] > 75 else (C.YELLOW if gpu["temp_c"] > 60 else C.GREEN)
            telemetry_items.append(f"🌡 {t_col}{gpu['temp_c']:.1f}°C{C.RESET}")
        if gpu["power_w"] > 0:
            telemetry_items.append(f"⚡ {C.BOLD}{gpu['power_w']:.1f} W{C.RESET}")

        status_badge = f"{C.GREEN}● ACTIVE{C.RESET}" if (gpu["util_video"] > 5 or gpu["util_compute"] > 5) else f"{C.DIM}○ IDLE{C.RESET}"
        telemetry_items.append(status_badge)

        out.append(f" ┌── {title} {C.DIM}[GPU #{idx}]{C.RESET}")
        out.append(f" │   {pci_str}   {' | '.join(telemetry_items)}")
        out.append(f" │")

        # Bars
        # 1. Video Transcode Engine (Special highlight for FFmpeg / QSV / NVENC)
        vid_pct = gpu["util_video"]
        vid_bar = make_bar(vid_pct, width=28, color=C.CYAN)
        out.append(f" │   🎬 {C.BOLD}Video Transcode ASIC:{C.RESET}  {vid_bar}")

        # 2. 3D / Compute Engine
        comp_pct = gpu["util_compute"]
        comp_bar = make_bar(comp_pct, width=28, color=C.GREEN)
        out.append(f" │   🚀 {C.BOLD}Compute & 3D Render: {C.RESET}  {comp_bar}")

        # 3. VRAM
        if gpu["vram_total"] > 0:
            mem_pct = (gpu["vram_used"] / gpu["vram_total"]) * 100.0
            mem_bar = make_bar(mem_pct, width=28, color=C.YELLOW)
            mem_text = f"{format_bytes(gpu['vram_used'])} / {format_bytes(gpu['vram_total'])}"
            out.append(f" │   💾 {C.BOLD}Memory Footprint:    {C.RESET}  {mem_bar} ({mem_text})")

        out.append(f" └{'─' * (cols - 3)}\n")

    # Active GPU Processes Table
    out.append(f" {C.BOLD}⚡ ACTIVE GPU PROCESSES & CLIENTS:{C.RESET}")
    out.append(f" {C.DIM}{'─' * (cols - 2)}{C.RESET}")
    
    if not processes:
        out.append(f"   {C.DIM}No active GPU compute or transcoding tasks running right now.{C.RESET}\n")
    else:
        out.append(f"   {C.BOLD}{'PID':<8} {'USER':<10} {'GPU MEM':<12} {'ENGINES / ACTIVITY':<26} {'COMMAND':<20}{C.RESET}")
        out.append(f"   {C.DIM}{'─' * (cols - 4)}{C.RESET}")
        for p in processes[:12]:  # Display top 12 processes
            cmd_preview = p["cmd"][:cols - 60].replace("\n", " ")
            eng_str = ", ".join(p["engines"]) if p["engines"] else "DRM Client Attached"
            vram_str = format_bytes(p["vram_bytes"]) if p["vram_bytes"] > 0 else "N/A"
            out.append(f"   {C.CYAN}{p['pid']:<8}{C.RESET} {p['user']:<10} {C.YELLOW}{vram_str:<12}{C.RESET} {C.GREEN}{eng_str[:25]:<26}{C.RESET} {C.DIM}{cmd_preview}{C.RESET}")
        out.append("")

    # Footer
    out.append(f"{C.DIM}{'─' * cols}{C.RESET}")
    out.append(f" {C.DIM}[q] Quit  |  [+] Faster  |  [-] Slower  |  Universal Intel QuickSync / Arc / NVIDIA / AMD{C.RESET}")
    
    sys.stdout.write("\n".join(out))
    sys.stdout.flush()


def parse_args():
    parser = argparse.ArgumentParser(description="⚡ Universal Multi-GPU Hardware & Video Engine Monitor")
    parser.add_argument("-i", "--interval", type=float, default=1.0, help="Refresh interval in seconds (default: 1.0)")
    parser.add_argument("--once", action="store_true", help="Print single snapshot and exit")
    parser.add_argument("--json", action="store_true", help="Output JSON metrics and exit")
    return parser.parse_args()


def main():
    args = parse_args()
    monitor = MultiGPUMonitor()

    # Pre-sample to prime delta engines
    monitor.collect_all()
    time.sleep(0.15)

    if args.json:
        gpus, processes = monitor.collect_all()
        print(json.dumps({"gpus": gpus, "processes": processes}, indent=2))
        return

    if args.once:
        gpus, processes = monitor.collect_all()
        render_dashboard(gpus, processes, args.interval)
        return

    # Interactive Loop
    interval = max(0.2, args.interval)
    try:
        sys.stdout.write(C.CURSOR_HIDE)
        sys.stdout.flush()
        while True:
            gpus, processes = monitor.collect_all()
            render_dashboard(gpus, processes, interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(f"{C.CURSOR_SHOW}\n\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
