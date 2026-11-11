#!/usr/bin/env python3
"""
Build official distributable packages for FFmpeg H.265 GPU Media Transcoder v2.0:
  1. FFmpeg-Transcoder-Setup-x64.exe          (Windows Native GUI + Silent /S Setup Installer)
  2. FFmpeg-Transcoder-Windows-Portable.zip   (Windows Portable Archive + Offline PATH Installer)
  3. ffmpeg_h265_transcoder-2.0.0-py3-none-any.whl (Python PEP 427 Wheel for pip / pipx)
  4. ffmpeg_h265_transcoder-2.0.0.tar.gz      (Python Source Distribution)
  5. ffmpeg-transcoder_2.0.0_all.deb          (Debian / Ubuntu / Proxmox .deb Package)
  6. ffmpeg-transcoder-linux-x64.tar.gz       (Linux / macOS Portable Tarball)
  7. SHA256SUMS.txt                           (Cryptographic Checksums)
"""

import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERSION = "2.0.0"
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT_DIR, "web")
DOWNLOADS_DIR = os.path.join(WEB_DIR, "downloads")

PY_MODULES = [
    "transcode_media.py",
    "transcode_gui.py",
    "gpu_detector.py",
    "gpu_monitor.py",
    "remote_engine.py",
]


def make_cmd_shim(py_script: str, windowed: bool = False) -> str:
    if windowed:
        return (
            "@echo off\r\n"
            "setlocal\r\n"
            f'where pythonw.exe >nul 2>nul && (start "" pythonw.exe "%~dp0{py_script}" %* & exit /b 0)\r\n'
            f'where pyw.exe >nul 2>nul && (start "" pyw.exe -3 "%~dp0{py_script}" %* & exit /b 0)\r\n'
            f'where python.exe >nul 2>nul && (python.exe "%~dp0{py_script}" %* & exit /b %ERRORLEVEL%)\r\n'
            f'where py.exe >nul 2>nul && (py.exe -3 "%~dp0{py_script}" %* & exit /b %ERRORLEVEL%)\r\n'
            "echo [ERROR] Python 3 not found. Install via: winget install --id Python.Python.3.12 -e\r\n"
            "exit /b 1\r\n"
        )
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "where python.exe >nul 2>nul\r\n"
        "if %ERRORLEVEL% EQU 0 (\r\n"
        f'    python.exe "%~dp0{py_script}" %*\r\n'
        "    exit /b %ERRORLEVEL%\r\n"
        ")\r\n"
        "where py.exe >nul 2>nul\r\n"
        "if %ERRORLEVEL% EQU 0 (\r\n"
        f'    py.exe -3 "%~dp0{py_script}" %*\r\n'
        "    exit /b %ERRORLEVEL%\r\n"
        ")\r\n"
        "echo [ERROR] Python 3 not found. Install via: winget install --id Python.Python.3.12 -e\r\n"
        "exit /b 1\r\n"
    )


def make_ps1_shim(py_script: str) -> str:
    return (
        "param([Parameter(ValueFromRemainingArguments=$true)]$RemainingArgs)\r\n"
        f'$scriptPath = Join-Path $PSScriptRoot "{py_script}"\r\n'
        '$py = Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*\\WindowsApps\\*" } | Select-Object -First 1\r\n'
        "if ($py) { & $py.Source $scriptPath @RemainingArgs; exit $LASTEXITCODE }\r\n"
        "$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1\r\n"
        "if ($pyLauncher) { & $pyLauncher.Source -3 $scriptPath @RemainingArgs; exit $LASTEXITCODE }\r\n"
        'Write-Error "Python 3 not found. Install via: winget install --id Python.Python.3.12 -e"\r\n'
        "exit 1\r\n"
    )


def build_windows_portable_zip() -> str:
    out_zip = os.path.join(DOWNLOADS_DIR, "FFmpeg-Transcoder-Windows-Portable.zip")
    print(f"[*] Building Windows Portable ZIP: {os.path.basename(out_zip)}...")

    offline_ps1 = (
        '$ErrorActionPreference = "Stop"\r\n'
        '$targetDir = Join-Path $env:LOCALAPPDATA "ffmpeg-transcoder"\r\n'
        'if ($PSScriptRoot -ne $targetDir) {\r\n'
        '    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null\r\n'
        '    Copy-Item -Path (Join-Path $PSScriptRoot "*") -Destination $targetDir -Recurse -Force\r\n'
        '}\r\n'
        '$userPath = [Environment]::GetEnvironmentVariable("Path", "User")\r\n'
        'if (($userPath -split ";") -notcontains $targetDir) {\r\n'
        '    $newPath = if ($userPath) { "$($userPath.TrimEnd(\';\'));$targetDir" } else { $targetDir }\r\n'
        '    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")\r\n'
        '}\r\n'
        'Write-Host "[OK] Installed FFmpeg H.265 Transcoder to $targetDir and added to Windows PATH!" -ForegroundColor Green\r\n'
        'Write-Host "Commands available: transcode-media, ffmpeg-convert, transcode-gui, gpu-monitor" -ForegroundColor Cyan\r\n'
    )
    offline_bat = (
        "@echo off\r\n"
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_to_PATH.ps1"\r\n'
        "pause\r\n"
    )

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for mod in PY_MODULES:
            zf.write(os.path.join(ROOT_DIR, mod), arcname=mod)
        zf.write(os.path.join(ROOT_DIR, "README.md"), arcname="README.md")
        zf.writestr("transcode-media.cmd", make_cmd_shim("transcode_media.py"))
        zf.writestr("transcode-media.ps1", make_ps1_shim("transcode_media.py"))
        zf.writestr("ffmpeg-convert.cmd", make_cmd_shim("transcode_media.py"))
        zf.writestr("ffmpeg-convert.ps1", make_ps1_shim("transcode_media.py"))
        zf.writestr("gpu-monitor.cmd", make_cmd_shim("gpu_monitor.py"))
        zf.writestr("gpu-monitor.ps1", make_ps1_shim("gpu_monitor.py"))
        zf.writestr("transcode-gui.cmd", make_cmd_shim("transcode_gui.py", windowed=True))
        zf.writestr("transcode-gui.ps1", make_ps1_shim("transcode_gui.py"))
        zf.writestr("Launch_Transcoder_GUI.bat", '@echo off\r\ncall "%~dp0transcode-gui.cmd" %*\r\n')
        zf.writestr("Install_to_PATH.ps1", offline_ps1)
        zf.writestr("Install_to_PATH.bat", offline_bat)

    return out_zip


CSHARP_INSTALLER_SOURCE = r"""
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

[assembly: AssemblyTitle("FFmpeg H.265 GPU Transcoder Setup")]
[assembly: AssemblyDescription("GPU-Accelerated H.265 Media Transcoder CLI & Studio GUI Installer")]
[assembly: AssemblyCompany("ERTechLab (ffmpeg.ertechlab.com)")]
[assembly: AssemblyProduct("FFmpeg H.265 GPU Transcoder")]
[assembly: AssemblyVersion("2.0.0.0")]
[assembly: AssemblyFileVersion("2.0.0.0")]

namespace FFmpegTranscoderSetup
{
    static class Program
    {
        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        [STAThread]
        static int Main(string[] args)
        {
            bool silent = false;
            string customDir = null;

            foreach (string arg in args)
            {
                string a = arg.Trim();
                if (a.Equals("/S", StringComparison.OrdinalIgnoreCase) ||
                    a.Equals("/SILENT", StringComparison.OrdinalIgnoreCase) ||
                    a.Equals("/VERYSILENT", StringComparison.OrdinalIgnoreCase) ||
                    a.Equals("--silent", StringComparison.OrdinalIgnoreCase) ||
                    a.Equals("-q", StringComparison.OrdinalIgnoreCase))
                {
                    silent = true;
                }
                else if (a.StartsWith("/D=", StringComparison.OrdinalIgnoreCase))
                {
                    customDir = a.Substring(3).Trim('"');
                }
            }

            string targetDir = string.IsNullOrWhiteSpace(customDir)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ffmpeg-transcoder")
                : customDir;

            if (silent)
            {
                try
                {
                    InstallEngine.RunInstall(targetDir, true, true, false, false, null);
                    return 0;
                }
                catch
                {
                    return 1;
                }
            }

            try { SetProcessDPIAware(); } catch { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm(targetDir));
            return 0;
        }
    }

    static class InstallEngine
    {
        public static void RunInstall(
            string installDir,
            bool addToPath,
            bool startMenuShortcut,
            bool desktopShortcut,
            bool installDepsWinget,
            Action<string> log)
        {
            if (log != null) log("[*] Preparing directory: " + installDir);
            Directory.CreateDirectory(installDir);

            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream resStream = asm.GetManifestResourceStream("FFmpegPayload.zip"))
            {
                if (resStream == null)
                    throw new InvalidOperationException("Embedded payload FFmpegPayload.zip missing.");

                using (ZipArchive archive = new ZipArchive(resStream, ZipArchiveMode.Read))
                {
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        if (string.IsNullOrEmpty(entry.Name)) continue;
                        string destPath = Path.Combine(installDir, entry.FullName);
                        string destParent = Path.GetDirectoryName(destPath);
                        if (!string.IsNullOrEmpty(destParent)) Directory.CreateDirectory(destParent);
                        entry.ExtractToFile(destPath, true);
                        if (log != null) log("  [OK] Extracted " + entry.FullName);
                    }
                }
            }

            // Write Uninstaller helper
            string uninstPs1 = Path.Combine(installDir, "uninstall.ps1");
            File.WriteAllText(uninstPs1,
                "$installDir = '" + installDir.Replace("'", "''") + "'\r\n" +
                "$u = [Environment]::GetEnvironmentVariable('Path', 'User')\r\n" +
                "if ($u) {\r\n" +
                "  $c = ($u -split ';' | Where-Object { $_ -and ($_.TrimEnd('\\') -ne $installDir.TrimEnd('\\')) }) -join ';'\r\n" +
                "  [Environment]::SetEnvironmentVariable('Path', $c, 'User')\r\n" +
                "}\r\n" +
                "$sm = Join-Path ([Environment]::GetFolderPath('Programs')) 'FFmpeg H.265 Transcoder'\r\n" +
                "if (Test-Path $sm) { Remove-Item $sm -Recurse -Force -ErrorAction SilentlyContinue }\r\n" +
                "Write-Host '[OK] Unregistered FFmpeg H.265 Transcoder from User PATH and Start Menu.' -ForegroundColor Green\r\n");

            if (addToPath)
            {
                if (log != null) log("[*] Registering CLI commands in Windows User PATH...");
                string userPath = Environment.GetEnvironmentVariable("Path", EnvironmentVariableTarget.User) ?? "";
                string[] parts = userPath.Split(new char[] { ';' }, StringSplitOptions.RemoveEmptyEntries);
                bool found = false;
                foreach (string p in parts)
                {
                    if (string.Equals(p.TrimEnd('\\'), installDir.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                    {
                        found = true;
                        break;
                    }
                }
                if (!found)
                {
                    string updated = string.IsNullOrEmpty(userPath)
                        ? installDir
                        : userPath.TrimEnd(';') + ";" + installDir;
                    Environment.SetEnvironmentVariable("Path", updated, EnvironmentVariableTarget.User);
                }
                if (log != null) log("  [OK] Added transcode-media, ffmpeg-convert, transcode-gui, gpu-monitor to PATH");
            }

            string guiTarget = Path.Combine(installDir, "transcode-gui.cmd");
            if (startMenuShortcut)
            {
                try
                {
                    string smDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "FFmpeg H.265 Transcoder");
                    Directory.CreateDirectory(smDir);
                    CreateShortcut(Path.Combine(smDir, "FFmpeg H.265 Transcoder Studio.lnk"), guiTarget, installDir);
                    if (log != null) log("  [OK] Created Windows Start Menu shortcut");
                }
                catch (Exception ex)
                {
                    if (log != null) log("  [!] Start Menu shortcut skipped: " + ex.Message);
                }
            }

            if (desktopShortcut)
            {
                try
                {
                    string deskDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    CreateShortcut(Path.Combine(deskDir, "FFmpeg H.265 Transcoder Studio.lnk"), guiTarget, installDir);
                    if (log != null) log("  [OK] Created Desktop shortcut");
                }
                catch (Exception ex)
                {
                    if (log != null) log("  [!] Desktop shortcut skipped: " + ex.Message);
                }
            }

            if (installDepsWinget)
            {
                if (log != null) log("[*] Checking Python 3 & FFmpeg via WinGet...");
                RunCmd("winget.exe", "install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements", log);
                RunCmd("winget.exe", "install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements", log);
            }

            if (log != null) log("\r\n[COMPLETE] Installation succeeded! Open any terminal and run: transcode-media --help");
        }

        private static void CreateShortcut(string lnkPath, string targetPath, string workDir)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null) return;
            dynamic shell = Activator.CreateInstance(shellType);
            dynamic sc = shell.CreateShortcut(lnkPath);
            sc.TargetPath = targetPath;
            sc.WorkingDirectory = workDir;
            sc.WindowStyle = 7;
            sc.Description = "GPU-Accelerated H.265 Media Transcoder Studio";
            sc.Save();
        }

        private static void RunCmd(string exe, string arguments, Action<string> log)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo(exe, arguments)
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                using (Process p = Process.Start(psi))
                {
                    p.WaitForExit(180000);
                }
            }
            catch (Exception ex)
            {
                if (log != null) log("  [!] WinGet note: " + ex.Message);
            }
        }
    }

    class SetupForm : Form
    {
        private TextBox txtDir;
        private CheckBox chkPath;
        private CheckBox chkStartMenu;
        private CheckBox chkDesktop;
        private CheckBox chkWinget;
        private TextBox txtLog;
        private Button btnInstall;
        private Button btnLaunchGui;
        private bool installed = false;

        public SetupForm(string defaultDir)
        {
            this.Text = "FFmpeg H.265 GPU Transcoder v2.0 - Windows Package Setup";
            this.Size = new Size(660, 520);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(15, 23, 42);
            this.ForeColor = Color.FromArgb(241, 245, 249);
            this.Font = new Font("Segoe UI", 9.5f);

            Label lblTitle = new Label()
            {
                Text = "FFmpeg H.265 (HEVC) GPU Media Transcoder v2.0",
                Font = new Font("Segoe UI", 14f, FontStyle.Bold),
                ForeColor = Color.FromArgb(56, 189, 248),
                Location = new Point(24, 18),
                AutoSize = true
            };
            Label lblSub = new Label()
            {
                Text = "Installs CLI Pipeline (transcode-media, ffmpeg-convert) + Multi-GPU Studio GUI",
                ForeColor = Color.FromArgb(148, 163, 184),
                Location = new Point(24, 48),
                AutoSize = true
            };

            Label lblDir = new Label()
            {
                Text = "App Binary Install Location (Choose media directory inside Studio GUI or CLI):",
                Location = new Point(24, 84),
                AutoSize = true
            };
            txtDir = new TextBox()
            {
                Text = defaultDir,
                Location = new Point(24, 108),
                Width = 500,
                BackColor = Color.FromArgb(30, 41, 59),
                ForeColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
            Button btnBrowse = new Button()
            {
                Text = "Browse...",
                Location = new Point(534, 106),
                Size = new Size(92, 27),
                BackColor = Color.FromArgb(51, 65, 85),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat
            };
            btnBrowse.Click += (s, e) =>
            {
                using (FolderBrowserDialog fbd = new FolderBrowserDialog())
                {
                    fbd.SelectedPath = txtDir.Text;
                    if (fbd.ShowDialog() == DialogResult.OK) txtDir.Text = fbd.SelectedPath;
                }
            };

            chkPath = new CheckBox()
            {
                Text = "Add CLI & GUI commands to Windows User PATH (transcode-media, ffmpeg-convert, transcode-gui)",
                Checked = true,
                Location = new Point(24, 148),
                AutoSize = true
            };
            chkStartMenu = new CheckBox()
            {
                Text = "Create Windows Start Menu shortcut (FFmpeg H.265 Transcoder Studio)",
                Checked = true,
                Location = new Point(24, 176),
                AutoSize = true
            };
            chkDesktop = new CheckBox()
            {
                Text = "Create Desktop shortcut",
                Checked = true,
                Location = new Point(24, 204),
                AutoSize = true
            };
            chkWinget = new CheckBox()
            {
                Text = "Also run WinGet check/install for Python 3.12 & Gyan.FFmpeg (optional)",
                Checked = false,
                Location = new Point(24, 232),
                AutoSize = true,
                ForeColor = Color.FromArgb(203, 213, 225)
            };

            txtLog = new TextBox()
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                Location = new Point(24, 268),
                Size = new Size(602, 150),
                BackColor = Color.FromArgb(9, 14, 26),
                ForeColor = Color.FromArgb(134, 239, 172),
                Font = new Font("Consolas", 9f),
                BorderStyle = BorderStyle.FixedSingle,
                Text = "Ready to install FFmpeg H.265 GPU Transcoder v2.0.\r\nTip: Run this installer with /S from terminal for silent unattended setup.\r\n"
            };

            btnInstall = new Button()
            {
                Text = "Install Package",
                Location = new Point(466, 432),
                Size = new Size(160, 36),
                BackColor = Color.FromArgb(14, 165, 233),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold),
                FlatStyle = FlatStyle.Flat
            };
            btnInstall.Click += OnInstallClick;

            btnLaunchGui = new Button()
            {
                Text = "Launch Studio GUI",
                Location = new Point(294, 432),
                Size = new Size(160, 36),
                BackColor = Color.FromArgb(16, 185, 129),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 10f, FontStyle.Bold),
                FlatStyle = FlatStyle.Flat,
                Visible = false
            };
            btnLaunchGui.Click += (s, e) =>
            {
                string guiCmd = Path.Combine(txtDir.Text, "transcode-gui.cmd");
                if (File.Exists(guiCmd))
                {
                    Process.Start(new ProcessStartInfo(guiCmd) { WorkingDirectory = txtDir.Text });
                }
                this.Close();
            };

            this.Controls.AddRange(new Control[] {
                lblTitle, lblSub, lblDir, txtDir, btnBrowse,
                chkPath, chkStartMenu, chkDesktop, chkWinget,
                txtLog, btnLaunchGui, btnInstall
            });
        }

        private void AppendLog(string line)
        {
            if (this.InvokeRequired)
            {
                this.BeginInvoke(new Action<string>(AppendLog), line);
                return;
            }
            txtLog.AppendText(line + "\r\n");
        }

        private void OnInstallClick(object sender, EventArgs e)
        {
            if (installed)
            {
                this.Close();
                return;
            }
            btnInstall.Enabled = false;
            txtLog.Clear();

            string dir = txtDir.Text.Trim();
            bool addPath = chkPath.Checked;
            bool sm = chkStartMenu.Checked;
            bool desk = chkDesktop.Checked;
            bool wg = chkWinget.Checked;

            System.Threading.ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    InstallEngine.RunInstall(dir, addPath, sm, desk, wg, AppendLog);
                    this.BeginInvoke(new Action(() =>
                    {
                        installed = true;
                        btnInstall.Text = "Finish";
                        btnInstall.Enabled = true;
                        btnLaunchGui.Visible = true;
                    }));
                }
                catch (Exception ex)
                {
                    AppendLog("[ERROR] " + ex.Message);
                    this.BeginInvoke(new Action(() =>
                    {
                        btnInstall.Enabled = true;
                    }));
                }
            });
        }
    }
}
"""


def build_windows_setup_exe(portable_zip_path: str) -> str:
    out_exe = os.path.join(DOWNLOADS_DIR, "FFmpeg-Transcoder-Setup-x64.exe")
    print(f"[*] Compiling Windows Native Setup EXE: {os.path.basename(out_exe)}...")

    csc_path = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    if not os.path.isfile(csc_path):
        raise FileNotFoundError(f"C# compiler not found at {csc_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        cs_file = os.path.join(tmpdir, "Setup.cs")
        payload_copy = os.path.join(tmpdir, "FFmpegPayload.zip")
        shutil.copy2(portable_zip_path, payload_copy)
        with open(cs_file, "w", encoding="utf-8") as f:
            f.write(CSHARP_INSTALLER_SOURCE)

        cmd = [
            csc_path,
            "/nologo",
            "/optimize+",
            "/target:winexe",
            "/platform:x64",
            f"/out:{out_exe}",
            "/r:System.dll",
            "/r:System.Core.dll",
            "/r:System.Drawing.dll",
            "/r:System.Windows.Forms.dll",
            "/r:System.IO.Compression.dll",
            "/r:System.IO.Compression.FileSystem.dll",
            "/r:Microsoft.CSharp.dll",
            f"/resource:{payload_copy},FFmpegPayload.zip",
            cs_file,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"csc.exe compilation failed:\n{res.stdout}\n{res.stderr}")

    return out_exe


def _wheel_hash_and_size(data: bytes) -> tuple:
    digest = hashlib.sha256(data).digest()
    b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={b64}", str(len(data))


def build_python_wheel_and_sdist() -> tuple:
    dist_name = "ffmpeg_h265_transcoder"
    dist_info = f"{dist_name}-{VERSION}.dist-info"
    whl_path = os.path.join(DOWNLOADS_DIR, f"{dist_name}-{VERSION}-py3-none-any.whl")
    sdist_path = os.path.join(DOWNLOADS_DIR, f"{dist_name}-{VERSION}.tar.gz")

    print(f"[*] Building Python PEP 427 Wheel: {os.path.basename(whl_path)}...")

    readme_text = open(os.path.join(ROOT_DIR, "README.md"), "r", encoding="utf-8").read()
    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: ffmpeg-h265-transcoder\n"
        f"Version: {VERSION}\n"
        "Summary: GPU-Accelerated H.265 (HEVC) & MP4 Batch Media Transcoder with Multi-GPU Studio GUI\n"
        "Home-page: https://ffmpeg.ertechlab.com\n"
        "Author: Efrain Rivas\n"
        "Author-email: admin@ertechlab.com\n"
        "License: MIT\n"
        "Requires-Python: >=3.9\n"
        "Description-Content-Type: text/markdown\n"
        "Provides-Extra: remote\n"
        "Requires-Dist: paramiko>=3.0.0; extra == 'remote'\n\n"
        + readme_text
    ).encode("utf-8")

    wheel_meta = (
        "Wheel-Version: 1.0\n"
        "Generator: ffmpeg-transcoder-packager\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode("utf-8")

    entry_points = (
        "[console_scripts]\n"
        "transcode-media = transcode_media:main\n"
        "ffmpeg-convert = transcode_media:main\n"
        "gpu-monitor = gpu_monitor:main\n\n"
        "[gui_scripts]\n"
        "transcode-gui = transcode_gui:main\n"
    ).encode("utf-8")

    records = []
    with zipfile.ZipFile(whl_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for mod in PY_MODULES:
            raw = open(os.path.join(ROOT_DIR, mod), "rb").read()
            zf.writestr(mod, raw)
            h, sz = _wheel_hash_and_size(raw)
            records.append(f"{mod},{h},{sz}")

        for rel_name, payload in [
            (f"{dist_info}/METADATA", metadata),
            (f"{dist_info}/WHEEL", wheel_meta),
            (f"{dist_info}/entry_points.txt", entry_points),
        ]:
            zf.writestr(rel_name, payload)
            h, sz = _wheel_hash_and_size(payload)
            records.append(f"{rel_name},{h},{sz}")

        record_rel = f"{dist_info}/RECORD"
        records.append(f"{record_rel},,")
        zf.writestr(record_rel, ("\n".join(records) + "\n").encode("utf-8"))

    print(f"[*] Building Python Source Tarball: {os.path.basename(sdist_path)}...")
    prefix = f"{dist_name}-{VERSION}"
    with tarfile.open(sdist_path, "w:gz") as tf:
        for fname in PY_MODULES + ["pyproject.toml", "README.md"]:
            tf.add(os.path.join(ROOT_DIR, fname), arcname=f"{prefix}/{fname}")

    return whl_path, sdist_path


def _add_tar_bytes(tf: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    ti = tarfile.TarInfo(name=name)
    ti.size = len(data)
    ti.mode = mode
    ti.uid = 0
    ti.gid = 0
    ti.uname = "root"
    ti.gname = "root"
    ti.mtime = int(time.time())
    tf.addfile(ti, io.BytesIO(data))


def _add_tar_dir(tf: tarfile.TarFile, name: str, mode: int = 0o755) -> None:
    ti = tarfile.TarInfo(name=name)
    ti.type = tarfile.DIRTYPE
    ti.mode = mode
    ti.uid = 0
    ti.gid = 0
    ti.uname = "root"
    ti.gname = "root"
    ti.mtime = int(time.time())
    tf.addfile(ti)


def _ar_entry(name: str, data: bytes) -> bytes:
    header = (
        f"{name:<16}"
        f"{int(time.time()):<12}"
        f"{'0':<6}"
        f"{'0':<6}"
        f"{'100644':<8}"
        f"{len(data):<10}"
        "`\n"
    ).encode("ascii")
    pad = b"\n" if (len(data) % 2 == 1) else b""
    return header + data + pad


def build_linux_deb_and_tarball() -> tuple:
    deb_path = os.path.join(DOWNLOADS_DIR, f"ffmpeg-transcoder_{VERSION}_all.deb")
    linux_tar_path = os.path.join(DOWNLOADS_DIR, "ffmpeg-transcoder-linux-x64.tar.gz")

    print(f"[*] Building Debian/Ubuntu Package: {os.path.basename(deb_path)}...")

    data_buf = io.BytesIO()
    installed_kb = 0
    with tarfile.open(fileobj=data_buf, mode="w:gz", format=tarfile.GNU_FORMAT) as dtar:
        for d in [
            "./opt",
            "./opt/ffmpeg-transcoder",
            "./usr",
            "./usr/local",
            "./usr/local/bin",
            "./usr/share",
            "./usr/share/applications",
        ]:
            _add_tar_dir(dtar, d)

        for mod in PY_MODULES:
            raw = open(os.path.join(ROOT_DIR, mod), "rb").read().replace(b"\r\n", b"\n")
            installed_kb += max(1, len(raw) // 1024)
            _add_tar_bytes(dtar, f"./opt/ffmpeg-transcoder/{mod}", raw, mode=0o755)

        readme_raw = open(os.path.join(ROOT_DIR, "README.md"), "rb").read().replace(b"\r\n", b"\n")
        _add_tar_bytes(dtar, "./opt/ffmpeg-transcoder/README.md", readme_raw, mode=0o644)

        wrappers = {
            "transcode-media": "transcode_media.py",
            "ffmpeg-convert": "transcode_media.py",
            "gpu-monitor": "gpu_monitor.py",
            "transcode-gui": "transcode_gui.py",
        }
        for bin_name, py_target in wrappers.items():
            sh_bytes = f"#!/usr/bin/env bash\nexec python3 /opt/ffmpeg-transcoder/{py_target} \"$@\"\n".encode("utf-8")
            _add_tar_bytes(dtar, f"./usr/local/bin/{bin_name}", sh_bytes, mode=0o755)

        desktop_entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=FFmpeg H.265 GPU Transcoder Studio\n"
            "Comment=Hardware-Accelerated Multi-GPU H.265 Video Transcoder\n"
            "Exec=/usr/local/bin/transcode-gui\n"
            "Terminal=false\n"
            "Categories=AudioVideo;Video;\n"
        ).encode("utf-8")
        _add_tar_bytes(dtar, "./usr/share/applications/ffmpeg-transcoder.desktop", desktop_entry, mode=0o644)

    control_txt = (
        "Package: ffmpeg-transcoder\n"
        f"Version: {VERSION}\n"
        "Section: video\n"
        "Priority: optional\n"
        "Architecture: all\n"
        f"Installed-Size: {installed_kb}\n"
        "Depends: python3 (>= 3.9), ffmpeg\n"
        "Recommends: python3-tk, python3-paramiko\n"
        "Maintainer: Efrain Rivas <admin@ertechlab.com>\n"
        "Homepage: https://ffmpeg.ertechlab.com\n"
        "Description: GPU-Accelerated H.265 (HEVC) Media Transcoder & Multi-GPU Studio\n"
        " High-throughput batch video transcoding pipeline and interactive GUI studio\n"
        " supporting NVIDIA NVENC, Intel QuickSync, AMD AMF, VAAPI, and Dual-Engine CPU+GPU.\n"
    ).encode("utf-8")

    postinst_txt = (
        "#!/bin/sh\n"
        "set -e\n"
        "chmod 755 /usr/local/bin/transcode-media /usr/local/bin/ffmpeg-convert /usr/local/bin/gpu-monitor /usr/local/bin/transcode-gui\n"
        "echo '[OK] FFmpeg H.265 GPU Transcoder v2.0 installed! Run: transcode-media --help'\n"
    ).encode("utf-8")

    ctrl_buf = io.BytesIO()
    with tarfile.open(fileobj=ctrl_buf, mode="w:gz", format=tarfile.GNU_FORMAT) as ctar:
        _add_tar_dir(ctar, ".")
        _add_tar_bytes(ctar, "./control", control_txt, mode=0o644)
        _add_tar_bytes(ctar, "./postinst", postinst_txt, mode=0o755)

    deb_bytes = (
        b"!<arch>\n"
        + _ar_entry("debian-binary", b"2.0\n")
        + _ar_entry("control.tar.gz", ctrl_buf.getvalue())
        + _ar_entry("data.tar.gz", data_buf.getvalue())
    )
    with open(deb_path, "wb") as f:
        f.write(deb_bytes)

    print(f"[*] Building Linux/macOS Portable Tarball: {os.path.basename(linux_tar_path)}...")
    with tarfile.open(linux_tar_path, "w:gz", format=tarfile.GNU_FORMAT) as ltar:
        _add_tar_dir(ltar, "ffmpeg-transcoder")
        for fname in PY_MODULES + ["install.sh", "README.md"]:
            raw = open(os.path.join(ROOT_DIR, fname), "rb").read().replace(b"\r\n", b"\n")
            mode = 0o755 if fname.endswith((".py", ".sh")) else 0o644
            _add_tar_bytes(ltar, f"ffmpeg-transcoder/{fname}", raw, mode=mode)

    return deb_path, linux_tar_path


def sync_web_root_assets() -> None:
    print("[*] Syncing updated scripts & Python modules into web/ root...")
    for fname in PY_MODULES + ["install.ps1", "install.cmd", "install.sh", "Launch_Transcoder_GUI.bat"]:
        src = os.path.join(ROOT_DIR, fname)
        dst = os.path.join(WEB_DIR, fname)
        shutil.copy2(src, dst)
    # Keep extensionless /install identical to install.sh for `curl https://ffmpeg.ertechlab.com/install | bash`
    shutil.copy2(os.path.join(ROOT_DIR, "install.sh"), os.path.join(WEB_DIR, "install"))


def write_checksums(artifact_paths: list) -> str:
    sums_path = os.path.join(DOWNLOADS_DIR, "SHA256SUMS.txt")
    lines = []
    for p in artifact_paths:
        digest = hashlib.sha256(open(p, "rb").read()).hexdigest()
        size_kb = os.path.getsize(p) / 1024.0
        lines.append(f"{digest}  {os.path.basename(p)}")
        print(f"  -> {os.path.basename(p):<45} ({size_kb:7.1f} KB)  sha256:{digest[:16]}...")
    with open(sums_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return sums_path


def main():
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    sync_web_root_assets()

    portable_zip = build_windows_portable_zip()
    setup_exe = build_windows_setup_exe(portable_zip)
    whl_path, sdist_path = build_python_wheel_and_sdist()
    deb_path, linux_tar = build_linux_deb_and_tarball()

    print("\n[*] Package Summary & SHA-256 Checksums:")
    write_checksums([setup_exe, portable_zip, whl_path, sdist_path, deb_path, linux_tar])
    print("\n[OK] All packages built successfully in:", DOWNLOADS_DIR)


if __name__ == "__main__":
    main()
