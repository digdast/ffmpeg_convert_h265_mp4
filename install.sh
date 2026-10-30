#!/usr/bin/env bash
# ==============================================================================
# ⚡ GPU-Accelerated H.265 / HEVC Transcoder Universal Installer (Linux & macOS)
# ==============================================================================
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main/install.sh | bash
#   or
#   curl -fsSL https://ffmpeg.ertechlab.com/install.sh | bash
# ==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
PURPLE='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

REPO_URL="https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main/transcode_media.py"
FALLBACK_URL="https://ffmpeg.ertechlab.com/transcode_media.py"

echo -e "${CYAN}${BOLD}"
echo "  ⚡=============================================================⚡"
echo "    GPU-Accelerated H.265 (HEVC) Media Transcoder Installer"
echo "    Author: Efrain Rivas (@digdast) | Portfolio: ertechlab.com"
echo "  ⚡=============================================================⚡"
echo -e "${NC}"

# Detect OS
OS_TYPE="$(uname -s)"
case "${OS_TYPE}" in
    Linux*)     PLATFORM="linux";;
    Darwin*)    PLATFORM="macos";;
    CYGWIN*|MINGW*|MSYS*) PLATFORM="windows";;
    *)          PLATFORM="unknown";;
esac

echo -e "${YELLOW}[*] Detected OS:${NC} ${BOLD}${PLATFORM} (${OS_TYPE})${NC}"

# Check for Python 3
echo -e "${YELLOW}[*] Checking Python 3...${NC}"
if command -v python3 >/dev/null 2>&1; then
    PY_VER="$(python3 -V)"
    echo -e "${GREEN}[✓] Found:${NC} ${PY_VER}"
else
    echo -e "${RED}[✗] Python 3 is not installed.${NC}"
    if [ "$PLATFORM" = "macos" ]; then
        echo -e "    Install with: ${CYAN}brew install python${NC}"
    else
        echo -e "    Install with: ${CYAN}sudo apt install python3 -y${NC} (Debian/Ubuntu) or ${CYAN}sudo dnf install python3 -y${NC} (Fedora)"
    fi
    exit 1
fi

# Check for FFmpeg & FFprobe
echo -e "${YELLOW}[*] Checking FFmpeg & FFprobe...${NC}"
MISSING_FFMPEG=0
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${RED}[✗] ffmpeg binary not found in PATH.${NC}"
    MISSING_FFMPEG=1
fi
if ! command -v ffprobe >/dev/null 2>&1; then
    echo -e "${RED}[✗] ffprobe binary not found in PATH.${NC}"
    MISSING_FFMPEG=1
fi

if [ $MISSING_FFMPEG -eq 1 ]; then
    echo -e "${YELLOW}[!] Warning: FFmpeg is required for transcoding.${NC}"
    if [ "$PLATFORM" = "macos" ]; then
        echo -e "    Install with: ${CYAN}brew install ffmpeg${NC}"
    else
        echo -e "    Debian/Ubuntu: ${CYAN}sudo apt update && sudo apt install ffmpeg -y${NC}"
        echo -e "    Arch Linux:    ${CYAN}sudo pacman -S ffmpeg${NC}"
        echo -e "    Fedora/RHEL:   ${CYAN}sudo dnf install ffmpeg ffmpeg-free -y${NC}"
    fi
else
    FF_VER="$(ffmpeg -version | head -n1)"
    echo -e "${GREEN}[✓] Found FFmpeg:${NC} ${FF_VER}"
fi

# Check GPU hardware acceleration capabilities
echo -e "\n${YELLOW}[*] Probing Hardware GPU Accelerators...${NC}"
HW_FOUND=0

if command -v ffmpeg >/dev/null 2>&1; then
    ENCODERS="$(ffmpeg -encoders 2>/dev/null || true)"
    
    if echo "$ENCODERS" | grep -q "hevc_nvenc"; then
        echo -e "  ${GREEN}✔ NVIDIA NVENC (hevc_nvenc):${NC} Supported"
        HW_FOUND=1
    fi
    if echo "$ENCODERS" | grep -q "hevc_qsv"; then
        echo -e "  ${GREEN}✔ Intel QuickSync (hevc_qsv):${NC} Supported"
        HW_FOUND=1
    fi
    if echo "$ENCODERS" | grep -q "hevc_videotoolbox"; then
        echo -e "  ${GREEN}✔ Apple VideoToolbox (hevc_videotoolbox):${NC} Supported"
        HW_FOUND=1
    fi
    if echo "$ENCODERS" | grep -q "hevc_vaapi"; then
        echo -e "  ${GREEN}✔ Linux VAAPI (hevc_vaapi):${NC} Supported"
        HW_FOUND=1
    fi
fi

if [ $HW_FOUND -eq 0 ]; then
    echo -e "  ${YELLOW}ℹ No hardware encoder found in current FFmpeg build. CPU fallback (libx265) will be used.${NC}"
fi

# Choose destination directory
TARGET_DIR="/usr/local/bin"
USE_SUDO=0

if [ -w "$TARGET_DIR" ]; then
    USE_SUDO=0
elif command -v sudo >/dev/null 2>&1; then
    USE_SUDO=1
else
    TARGET_DIR="$HOME/.local/bin"
    mkdir -p "$TARGET_DIR"
fi

BIN_TARGET="$TARGET_DIR/transcode-media"
ALIAS_TARGET="$TARGET_DIR/ffmpeg-convert"

echo -e "\n${YELLOW}[*] Installing binary to:${NC} ${BOLD}${BIN_TARGET}${NC}"

# Download latest transcode_media.py
TMP_FILE="$(mktemp /tmp/transcode_media.XXXXXX.py)"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$REPO_URL" -o "$TMP_FILE" || curl -fsSL "$FALLBACK_URL" -o "$TMP_FILE"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TMP_FILE" "$REPO_URL" || wget -qO "$TMP_FILE" "$FALLBACK_URL"
else
    echo -e "${RED}[✗] Neither curl nor wget is available.${NC}"
    exit 1
fi

chmod +x "$TMP_FILE"

if [ $USE_SUDO -eq 1 ]; then
    echo -e "${YELLOW}[*] Elevating with sudo to place in /usr/local/bin...${NC}"
    sudo mv "$TMP_FILE" "$BIN_TARGET"
    sudo chmod 755 "$BIN_TARGET"
    sudo ln -sf "$BIN_TARGET" "$ALIAS_TARGET"
else
    mv "$TMP_FILE" "$BIN_TARGET"
    chmod 755 "$BIN_TARGET"
    ln -sf "$BIN_TARGET" "$ALIAS_TARGET"
fi

# Verify installation
echo -e "\n${GREEN}${BOLD}[✓] Installation Complete!${NC}"
echo -e "  Commands installed: ${CYAN}transcode-media${NC} and ${CYAN}ffmpeg-convert${NC}"

if [ "$TARGET_DIR" = "$HOME/.local/bin" ]; then
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *)
            echo -e "${YELLOW}[!] Notice: Add ~/.local/bin to your PATH by running:${NC}"
            echo -e "    ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
            ;;
    esac
fi

echo -e "\n${BOLD}Quick Test / Help:${NC}"
echo -e "  ${CYAN}transcode-media --help${NC}"
echo -e "\n${BOLD}Example Transcode Command:${NC}"
echo -e "  ${CYAN}transcode-media /path/to/media --dry-run${NC}"
echo -e "  ${CYAN}transcode-media /path/to/media --cq 24 --delete-original${NC}\n"
