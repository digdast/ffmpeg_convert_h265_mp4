#!/usr/bin/env bash
# ==============================================================================
# GPU-Accelerated H.265 / HEVC Transcoder Universal Installer v2.0 (Linux/macOS)
# ==============================================================================
# Usage:
#   curl -fsSL https://ffmpeg.ertechlab.com/install.sh | bash
#   or
#   curl -fsSL https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main/install.sh | bash
# ==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

PRIMARY_BASE="https://ffmpeg.ertechlab.com"
FALLBACK_BASE="https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main"

echo -e "${CYAN}${BOLD}"
echo "  ⚡=============================================================⚡"
echo "    GPU-Accelerated H.265 (HEVC) Media Transcoder v2.0 Installer"
echo "    CLI Pipeline + Multi-GPU Studio GUI | ffmpeg.ertechlab.com"
echo "  ⚡=============================================================⚡"
echo -e "${NC}"

OS_TYPE="$(uname -s)"
case "${OS_TYPE}" in
    Linux*)     PLATFORM="linux";;
    Darwin*)    PLATFORM="macos";;
    CYGWIN*|MINGW*|MSYS*) PLATFORM="windows";;
    *)          PLATFORM="unknown";;
esac

echo -e "${YELLOW}[*] Detected OS:${NC} ${BOLD}${PLATFORM} (${OS_TYPE})${NC}"

echo -e "${YELLOW}[*] Checking Python 3...${NC}"
if command -v python3 >/dev/null 2>&1; then
    PY_VER="$(python3 -V)"
    echo -e "${GREEN}[✓] Found:${NC} ${PY_VER}"
else
    echo -e "${RED}[✗] Python 3 is not installed.${NC}"
    if [ "$PLATFORM" = "macos" ]; then
        echo -e "    Install with: ${CYAN}brew install python${NC}"
    else
        echo -e "    Install with: ${CYAN}sudo apt install python3 -y${NC}"
    fi
    exit 1
fi

echo -e "${YELLOW}[*] Checking FFmpeg & FFprobe...${NC}"
MISSING_FFMPEG=0
command -v ffmpeg >/dev/null 2>&1 || MISSING_FFMPEG=1
command -v ffprobe >/dev/null 2>&1 || MISSING_FFMPEG=1

if [ $MISSING_FFMPEG -eq 1 ]; then
    echo -e "${YELLOW}[!] Warning: FFmpeg/FFprobe not found in PATH.${NC}"
    if [ "$PLATFORM" = "macos" ]; then
        echo -e "    Install with: ${CYAN}brew install ffmpeg${NC}"
    else
        echo -e "    Debian/Ubuntu: ${CYAN}sudo apt update && sudo apt install ffmpeg -y${NC}"
        echo -e "    Arch Linux:    ${CYAN}sudo pacman -S ffmpeg${NC}"
        echo -e "    Fedora/RHEL:   ${CYAN}sudo dnf install ffmpeg -y${NC}"
    fi
else
    FF_VER="$(ffmpeg -version | head -n1)"
    echo -e "${GREEN}[✓] Found FFmpeg:${NC} ${FF_VER}"
fi

echo -e "\n${YELLOW}[*] Probing Hardware GPU Accelerators...${NC}"
HW_FOUND=0
if command -v ffmpeg >/dev/null 2>&1; then
    if ffmpeg -v error -f lavfi -i color=s=64x64:d=0.04 -c:v hevc_nvenc -f null - >/dev/null 2>&1; then
        echo -e "  ${GREEN}✔ NVIDIA NVENC (hevc_nvenc):${NC} Functional"
        HW_FOUND=1
    fi
    if ffmpeg -v error -f lavfi -i color=s=64x64:d=0.04 -c:v hevc_qsv -f null - >/dev/null 2>&1; then
        echo -e "  ${GREEN}✔ Intel QuickSync (hevc_qsv):${NC} Functional"
        HW_FOUND=1
    fi
    if ffmpeg -v error -vaapi_device /dev/dri/renderD128 -f lavfi -i color=s=64x64:d=0.04 -vf "format=nv12,hwupload" -c:v hevc_vaapi -f null - >/dev/null 2>&1 || \
       ffmpeg -v error -f lavfi -i color=s=64x64:d=0.04 -c:v hevc_vaapi -f null - >/dev/null 2>&1; then
        echo -e "  ${GREEN}✔ Linux VAAPI (hevc_vaapi):${NC} Functional"
        HW_FOUND=1
    fi
    if ffmpeg -v error -f lavfi -i color=s=64x64:d=0.04 -c:v hevc_videotoolbox -f null - >/dev/null 2>&1; then
        echo -e "  ${GREEN}✔ Apple VideoToolbox (hevc_videotoolbox):${NC} Functional"
        HW_FOUND=1
    fi
fi
if [ $HW_FOUND -eq 0 ]; then
    echo -e "  ${YELLOW}ℹ Using CPU fallback (libx265) until GPU encoder/driver is active.${NC}"
fi

if [ -w "/usr/local/bin" ]; then
    APP_DIR="/opt/ffmpeg-transcoder"
    BIN_DIR="/usr/local/bin"
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    APP_DIR="/opt/ffmpeg-transcoder"
    BIN_DIR="/usr/local/bin"
    SUDO="sudo"
else
    APP_DIR="$HOME/.local/share/ffmpeg-transcoder"
    BIN_DIR="$HOME/.local/bin"
    SUDO=""
    mkdir -p "$BIN_DIR"
fi

$SUDO mkdir -p "$APP_DIR"

download_file() {
    local fname="$1"
    local tmpf
    tmpf="$(mktemp /tmp/fftrans.XXXXXX)"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$PRIMARY_BASE/$fname" -o "$tmpf" 2>/dev/null || curl -fsSL "$FALLBACK_BASE/$fname" -o "$tmpf"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$tmpf" "$PRIMARY_BASE/$fname" 2>/dev/null || wget -qO "$tmpf" "$FALLBACK_BASE/$fname"
    else
        echo -e "${RED}[✗] Neither curl nor wget is installed.${NC}"
        rm -f "$tmpf"
        return 1
    fi
    if grep -qi "<!DOCTYPE html" "$tmpf" 2>/dev/null; then
        rm -f "$tmpf"
        return 1
    fi
    $SUDO mv "$tmpf" "$APP_DIR/$fname"
    $SUDO chmod 755 "$APP_DIR/$fname"
    return 0
}

echo -e "\n${YELLOW}[*] Installing FFmpeg Transcoder Suite to ${APP_DIR}...${NC}"
download_file "transcode_media.py"
for opt_mod in gpu_detector.py gpu_monitor.py remote_engine.py transcode_gui.py; do
    download_file "$opt_mod" || true
done

create_wrapper() {
    local cmd_name="$1"
    local py_file="$2"
    local tmpw
    tmpw="$(mktemp /tmp/ffwrap.XXXXXX)"
    cat << EOF > "$tmpw"
#!/usr/bin/env bash
exec python3 "$APP_DIR/$py_file" "\$@"
EOF
    $SUDO mv "$tmpw" "$BIN_DIR/$cmd_name"
    $SUDO chmod 755 "$BIN_DIR/$cmd_name"
}

create_wrapper "transcode-media" "transcode_media.py"
create_wrapper "ffmpeg-convert"  "transcode_media.py"
[ -f "$APP_DIR/gpu_monitor.py" ]   && create_wrapper "gpu-monitor"   "gpu_monitor.py"
[ -f "$APP_DIR/transcode_gui.py" ] && create_wrapper "transcode-gui" "transcode_gui.py"

echo -e "\n${GREEN}${BOLD}[✓] Installation Complete!${NC}"
echo -e "  CLI Commands: ${CYAN}transcode-media${NC}, ${CYAN}ffmpeg-convert${NC}, ${CYAN}gpu-monitor${NC}"
echo -e "  Studio GUI:   ${CYAN}transcode-gui${NC}"
echo -e "\n${BOLD}Quick Test:${NC}"
echo -e "  ${CYAN}transcode-media --help${NC}\n"
