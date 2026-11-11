#!/usr/bin/env python3
"""
================================================================================
🌐 Remote Computer & NAS Transcode Engine (SSH/SFTP)
================================================================================
Enables the Transcoder GUI to connect to any remote server or NAS host:
- Connects securely via SSH (Password or Private Key).
- Probes remote hardware & GPUs (NVIDIA NVENC, Intel QSV / Arc, VAAPI, CPU).
- Automatically syncs the latest transcode engine to /tmp/ on the remote machine.
- Scans remote directories / mounted NAS paths (e.g. /data/media/...).
- Dispatches transcoding on the remote host silicon directly against NAS drives.
- Streams live FFmpeg progress, speed, FPS, and level meters back to the GUI.
================================================================================
"""

import json
import os
import re
import shlex
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

from gpu_detector import GPUCard


VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv",
    ".ts", ".m2ts", ".webm", ".m4v", ".vob"
)


class RemoteHostConfig:
    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: Optional[str] = None,
        key_path: Optional[str] = None,
        name: str = "Remote Server"
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path
        self.name = name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "has_password": bool(self.password),
            "password": self.password,
            "key_path": self.key_path,
            "name": self.name
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RemoteHostConfig":
        return cls(
            host=data.get("host", ""),
            port=int(data.get("port", 22)),
            username=data.get("username", "root"),
            password=data.get("password"),
            key_path=data.get("key_path"),
            name=data.get("name", "Remote Server")
        )


DEFAULT_PRESET_CONFIGS = [
    RemoteHostConfig(host="192.168.0.17", port=22, username="plexadmin", password="sone_420", name="ai-auto (Dual GPU: RTX 5070 + RTX 3060)"),
    RemoteHostConfig(host="192.168.0.31", port=22, username="root", password="sone_420", name="pve-01 (Proxmox Host / Intel Iris GPU)"),
    RemoteHostConfig(host="192.168.0.19", port=22, username="root", name="arr-server (Media Host / NFS Mount /data)"),
    RemoteHostConfig(host="192.168.0.3", port=22, username="root", name="nas-01-data (TrueNAS 52TB Storage)"),
]


def get_config_file_path() -> str:
    user_home = os.path.expanduser("~")
    return os.path.join(user_home, ".ffmpeg_gui_remote.json")


def load_remote_configs() -> List[RemoteHostConfig]:
    cfg_file = get_config_file_path()
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [RemoteHostConfig.from_dict(item) for item in data]
        except Exception:
            pass
    return list(DEFAULT_PRESET_CONFIGS)


def save_remote_configs(configs: List[RemoteHostConfig]):
    cfg_file = get_config_file_path()
    try:
        data = [c.to_dict() for c in configs]
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class RemoteTranscodeManager:
    """Manages remote SSH connections, hardware detection, and remote transcoding."""

    def __init__(self):
        self.config: Optional[RemoteHostConfig] = None
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.is_connected = False
        self.remote_os_info = ""
        self.remote_devices: List[GPUCard] = []
        self.active_channels: List[Any] = []
        self._lock = threading.Lock()

    def connect(self, config: RemoteHostConfig) -> Tuple[bool, str]:
        """Establish SSH connection to remote host."""
        if not PARAMIKO_AVAILABLE:
            return False, "Paramiko library is not installed. Please install: pip install paramiko"

        self.disconnect()
        self.config = config

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            connect_kwargs: Dict[str, Any] = {
                "hostname": config.host,
                "port": config.port,
                "username": config.username,
                "timeout": 8,
                "banner_timeout": 8
            }
            if config.key_path and os.path.exists(config.key_path):
                connect_kwargs["key_filename"] = config.key_path
            elif config.password:
                connect_kwargs["password"] = config.password

            client.connect(**connect_kwargs)
            self.ssh_client = client
            self.is_connected = True

            # Query OS info
            _, stdout, _ = client.exec_command("uname -s -r -m", timeout=5)
            self.remote_os_info = stdout.read().decode("utf-8", errors="replace").strip()

            # Ensure remote transcode script is present
            self.sync_remote_scripts()

            # Detect remote hardware
            self.remote_devices = self.detect_remote_hardware()

            gpu_summary = ", ".join([d.name for d in self.remote_devices if d.vendor != "CPU"]) or "CPU Only"
            msg = f"Connected to {config.username}@{config.host} ({self.remote_os_info}) | Hardware: {gpu_summary}"
            return True, msg

        except Exception as e:
            self.disconnect()
            return False, f"Failed to connect to {config.host}: {str(e)}"

    def disconnect(self):
        """Close remote SSH connection."""
        with self._lock:
            if self.ssh_client:
                try:
                    self.ssh_client.close()
                except Exception:
                    pass
            self.ssh_client = None
            self.is_connected = False
            self.remote_devices.clear()

    def sync_remote_scripts(self) -> bool:
        """Uploads transcode_media.py and gpu_detector.py to /tmp/ on remote machine."""
        if not self.is_connected or not self.ssh_client:
            return False

        try:
            sftp = self.ssh_client.open_sftp()
            local_dir = os.path.dirname(os.path.abspath(__file__))

            files_to_sync = ["transcode_media.py", "gpu_detector.py"]
            for f in files_to_sync:
                local_path = os.path.join(local_dir, f)
                if os.path.exists(local_path):
                    remote_path = f"/tmp/{f}"
                    sftp.put(local_path, remote_path)
                    # Make executable
                    self.ssh_client.exec_command(f"chmod +x {remote_path}")

            sftp.close()
            return True
        except Exception:
            return False

    def detect_remote_hardware(self) -> List[GPUCard]:
        """Discovers GPU accelerators and CPU cores on remote host."""
        if not self.is_connected or not self.ssh_client:
            return []

        devices: List[GPUCard] = []

        # Run remote python gpu discovery script if available
        probe_cmd = (
            "python3 -c \""
            "import sys, json;"
            "sys.path.insert(0, '/tmp');"
            "from gpu_detector import MultiGPUManager;"
            "mgr = MultiGPUManager();"
            "devs = [d.to_dict() for d in mgr.detect_all_devices()];"
            "print(json.dumps(devs))"
            "\" 2>/dev/null"
        )
        try:
            _, stdout, _ = self.ssh_client.exec_command(probe_cmd, timeout=8)
            output = stdout.read().decode("utf-8", errors="replace").strip()
            if output and output.startswith("["):
                data = json.loads(output)
                for item in data:
                    card = GPUCard(
                        device_id=item["device_id"],
                        vendor=item["vendor"],
                        name=item["name"],
                        encoder=item["encoder"],
                        index=item["index"],
                        vram_mb=item["vram_mb"],
                        extra_args=item["extra_args"],
                        device_node=item.get("device_node")
                    )
                    card.is_functional = item.get("is_functional", True)
                    devices.append(card)
                if devices:
                    return devices
        except Exception:
            pass

        # Fallback manual remote probing
        # 1. NVIDIA via nvidia-smi
        try:
            _, stdout, _ = self.ssh_client.exec_command(
                "which nvidia-smi && nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader,nounits",
                timeout=5
            )
            out_lines = stdout.read().decode("utf-8", errors="replace").strip().splitlines()
            for line in out_lines:
                if "," in line:
                    parts = [p.strip() for p in line.split(",")]
                    idx = int(parts[0])
                    devices.append(GPUCard(
                        device_id=f"nvidia:{idx}",
                        vendor="NVIDIA",
                        name=f"{parts[1]} (#{idx})",
                        encoder="hevc_nvenc",
                        index=idx,
                        vram_mb=int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None,
                        extra_args=["-gpu", str(idx)]
                    ))
        except Exception:
            pass

        # 2. Linux DRM render nodes (Intel QSV / Arc / VAAPI)
        try:
            _, stdout, _ = self.ssh_client.exec_command("ls /dev/dri/renderD* 2>/dev/null", timeout=4)
            nodes = stdout.read().decode("utf-8", errors="replace").strip().split()
            for idx, node in enumerate(nodes):
                devices.append(GPUCard(
                    device_id=f"remote_drm:{idx}",
                    vendor="Intel/VAAPI",
                    name=f"Intel QSV/VAAPI ({os.path.basename(node)})",
                    encoder="hevc_vaapi",
                    index=idx,
                    device_node=node,
                    extra_args=["-vaapi_device", node]
                ))
        except Exception:
            pass

        # 3. Remote CPU
        try:
            _, stdout, _ = self.ssh_client.exec_command("nproc", timeout=4)
            cores = stdout.read().decode("utf-8", errors="replace").strip()
            num_cores = int(cores) if cores.isdigit() else 4
        except Exception:
            num_cores = 4

        devices.append(GPUCard(
            device_id="cpu:software",
            vendor="CPU",
            name=f"Remote CPU ({num_cores} cores)",
            encoder="libx265",
            index=0,
            extra_args=[]
        ))

        return devices

    def probe_remote_video(self, file_path: str) -> Dict[str, Any]:
        """Queries duration, video codec, and subtitle streams of a remote video via SSH."""
        if not self.is_connected or not self.ssh_client:
            return {"duration": 0.0, "vcodec": "", "subtitles": []}

        probe_cmd = (
            f"ffprobe -v quiet -print_format json -show_format -show_streams {shlex.quote(file_path)}"
        )
        try:
            _, stdout, _ = self.ssh_client.exec_command(probe_cmd, timeout=12)
            output = stdout.read().decode("utf-8", errors="replace").strip()
            if output:
                data = json.loads(output)
                duration = 0.0
                if "format" in data and "duration" in data["format"]:
                    try:
                        duration = float(data["format"]["duration"])
                    except ValueError:
                        pass
                vcodec = ""
                for s in data.get("streams", []):
                    if s.get("codec_type") == "video":
                        vcodec = s.get("codec_name", "")
                        if duration <= 0 and "duration" in s:
                            try:
                                duration = float(s["duration"])
                            except ValueError:
                                pass
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

    def scan_remote_directory(self, remote_dir: str, recursive: bool = True) -> List[Dict[str, Any]]:
        """Scans remote directory / NAS mount for candidate video files."""
        if not self.is_connected or not self.ssh_client:
            return []

        clean_dir = remote_dir.strip().rstrip("/")
        maxdepth_arg = "" if recursive else "-maxdepth 1"

        ext_pattern = " -o ".join([f"-iname '*{ext}'" for ext in VIDEO_EXTENSIONS])
        find_cmd = (
            f"find {shlex.quote(clean_dir)} {maxdepth_arg} -type f \\( {ext_pattern} \\) "
            f"-printf '%s\\t%p\\n' 2>/dev/null | sort -k2"
        )

        items: List[Dict[str, Any]] = []
        try:
            _, stdout, stderr = self.ssh_client.exec_command(find_cmd, timeout=30)
            for line in stdout:
                line = line.strip()
                if not line or "\t" not in line:
                    continue
                size_str, file_path = line.split("\t", 1)
                try:
                    size_bytes = int(size_str)
                except ValueError:
                    size_bytes = 0

                items.append({
                    "path": file_path,
                    "filename": os.path.basename(file_path),
                    "size": size_bytes,
                    "is_remote": True
                })
        except Exception as e:
            print(f"Error scanning remote directory: {e}")

        return items

    def run_remote_transcode(
        self,
        file_path: str,
        settings: Dict[str, Any],
        worker_cfg: Dict[str, Any],
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_log: Optional[Callable[[str], None]] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Executes transcode on remote host directly against mounted NAS storage.
        Streams live progress and telemetry back line-by-line over SSH channel.
        """
        if not self.is_connected or not self.ssh_client:
            return False, {"error": "Not connected to remote host"}

        mode = settings.get("mode", "auto")
        cq = settings.get("cq", 22)
        preset = settings.get("preset", "p4")
        container = settings.get("container", "auto")
        skip_hevc = "--re-encode-hevc" if not settings.get("skip_hevc", True) else ""
        in_place = "--delete-original" if settings.get("in_place", False) else ""

        # Check for specific GPU targeting
        gpu_id_flag = ""
        if "gpu_id" in worker_cfg and worker_cfg["gpu_id"] is not None:
            gpu_id_flag = f"--gpu-id {worker_cfg['gpu_id']}"
        elif worker_cfg.get("extra_args"):
            for idx, a in enumerate(worker_cfg["extra_args"]):
                if a == "-gpu" and idx + 1 < len(worker_cfg["extra_args"]):
                    gpu_id_flag = f"--gpu-id {worker_cfg['extra_args'][idx+1]}"
                    break

        device_flag = ""
        if worker_cfg.get("device_node"):
            device_flag = f"--device {shlex.quote(worker_cfg['device_node'])}"

        # Build execution command on remote machine
        remote_cmd = (
            f"python3 /tmp/transcode_media.py "
            f"--mode {shlex.quote(mode)} "
            f"--cq {cq} "
            f"--preset {shlex.quote(preset)} "
            f"--container {shlex.quote(container)} "
            f"--gui-telemetry "
            f"{gpu_id_flag} {device_flag} "
            f"{skip_hevc} {in_place} "
            f"{shlex.quote(file_path)}"
        )

        if on_log:
            on_log(f"⚡ Remote Execute on {self.config.host}: {remote_cmd}")

        transport = self.ssh_client.get_transport()
        if not transport:
            return False, {"error": "SSH transport is unavailable"}

        channel = transport.open_session()
        with self._lock:
            self.active_channels.append(channel)
        channel.set_combine_stderr(True)
        channel.exec_command(remote_cmd)

        # Stream stdout line by line
        stdout_file = channel.makefile("r", -1)
        cur_telemetry: Dict[str, Any] = {
            "progress": 0.0,
            "speed": "N/A",
            "fps": "0",
            "bitrate": "N/A",
            "out_time": "00:00:00",
            "raw_line": ""
        }
        result_info: Dict[str, Any] = {
            "status": "unknown",
            "orig_size": 0,
            "new_size": 0,
            "saved_bytes": 0,
            "saved_pct": 0.0
        }

        exit_code = 0
        try:
            for line in stdout_file:
                clean_line = line.strip()
                if on_log:
                    on_log(clean_line)

                # 1. Parse high-precision structured GUI telemetry
                if clean_line.startswith("__GUI_TELEMETRY__:"):
                    payload = clean_line[len("__GUI_TELEMETRY__:"):].strip()
                    parts = payload.split("|")
                    for p in parts:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if k == "fraction":
                                try:
                                    cur_telemetry["progress"] = float(v)
                                except ValueError:
                                    pass
                            elif k in ("speed", "fps", "bitrate", "out_sec", "total_sec"):
                                cur_telemetry[k] = v

                    if on_progress:
                        cur_telemetry["raw_line"] = clean_line
                        on_progress(cur_telemetry)
                    continue

                # 2. Parse structured transcode result
                if clean_line.startswith("__GUI_RESULT__:"):
                    payload = clean_line[len("__GUI_RESULT__:"):].strip()
                    parts = payload.split("|")
                    for p in parts:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if k == "status":
                                result_info["status"] = v
                            elif k in ("orig_size", "new_size", "saved_bytes"):
                                try:
                                    result_info[k] = int(v)
                                except ValueError:
                                    pass
                            elif k == "saved_pct":
                                try:
                                    result_info[k] = float(v)
                                except ValueError:
                                    pass
                    if on_result:
                        on_result(result_info)
                    continue

                # 3. Fallback: Parse FFmpeg key=value progress markers
                if "=" in clean_line:
                    k, v = clean_line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k in ("speed", "fps", "bitrate", "out_time"):
                        cur_telemetry[k] = v
                    elif k == "progress":
                        if on_progress:
                            cur_telemetry["raw_line"] = clean_line
                            on_progress(cur_telemetry)

                # 4. Fallback: Parse LevelProgressBar text
                if "%" in clean_line and ("[" in clean_line or "LVL" in clean_line):
                    pct_match = re.search(r"(\d{1,3}\.\d)%", clean_line)
                    if pct_match:
                        try:
                            cur_telemetry["progress"] = float(pct_match.group(1)) / 100.0
                        except ValueError:
                            pass
                    speed_match = re.search(r"(\d+\.\d+x)", clean_line)
                    if speed_match:
                        cur_telemetry["speed"] = speed_match.group(1)
                    fps_match = re.search(r"(\d+)\s*fps", clean_line)
                    if fps_match:
                        cur_telemetry["fps"] = fps_match.group(1)
                    if on_progress:
                        cur_telemetry["raw_line"] = clean_line
                        on_progress(cur_telemetry)

            exit_code = channel.recv_exit_status()
        finally:
            with self._lock:
                if channel in self.active_channels:
                    self.active_channels.remove(channel)
            try:
                channel.close()
            except Exception:
                pass

        if exit_code == 0:
            return True, result_info
        return False, {"error": f"Remote process exited with code {exit_code}", "exit_code": exit_code}

    def cancel_active_job(self):
        """Cancels running remote process(es)."""
        with self._lock:
            for ch in list(self.active_channels):
                try:
                    # Send Ctrl+C interrupt signal across SSH channel
                    ch.send("\x03")
                    time.sleep(0.2)
                    ch.close()
                except Exception:
                    pass
            self.active_channels.clear()
