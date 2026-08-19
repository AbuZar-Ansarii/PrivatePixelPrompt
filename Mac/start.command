#!/bin/bash
# ===================================================
#  Portable AI - Fast Web Chat (Mac)
# ===================================================

echo "==================================================="
echo "    Portable AI - Fast Web Chat Mode (Mac)"
echo "==================================================="
echo ""
echo "  Launches the AI engine + browser chat UI."
echo "  All chats auto-save to the USB drive."
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
USB_ROOT="$(dirname "$SCRIPT_DIR")"
SHARED_DIR="$USB_ROOT/Shared"
OLLAMA_RUNTIME="$SHARED_DIR/.ollama-runtime"
mkdir -p "$OLLAMA_RUNTIME"

# ---- Full portability: keep EVERYTHING on the USB ----
export OLLAMA_MODELS="$SHARED_DIR/models/ollama_data"
export OLLAMA_HOME="$OLLAMA_RUNTIME"
export OLLAMA_RUNNERS_DIR="$OLLAMA_RUNTIME/runners"
export OLLAMA_TMPDIR="$OLLAMA_RUNTIME/tmp"
export OLLAMA_ORIGINS="*"
export OLLAMA_HOST="127.0.0.1:11435"
mkdir -p "$OLLAMA_RUNTIME/runners" "$OLLAMA_RUNTIME/tmp"
# -------------------------------------------------------

# Check if the portable Mac engine is downloaded
if [ ! -f "$SHARED_DIR/bin/ollama-darwin" ]; then
    echo "==================================================="
    echo "  ERROR: Mac AI Engine Not Found!"
    echo "==================================================="
    echo ""
    echo "  It looks like the AI engine hasn't been set up yet."
    echo "  Please double-click 'install.command' in this Mac"
    echo "  folder first to safely download the components!"
    echo ""
    read -n 1 -s -r -p "Press any key to continue..."
    echo ""
    exit 1
fi

# Ensure executable permissions & strip quarantine flags if needed
chmod +x "$SHARED_DIR/bin/ollama-darwin" 2>/dev/null || true
xattr -d com.apple.quarantine "$SHARED_DIR/bin/ollama-darwin" 2>/dev/null || true

# Cleanup helper on exit
cleanup() {
    if [ -n "$OLLAMA_PID" ]; then
        kill -9 "$OLLAMA_PID" 2>/dev/null || true
    fi
}
trap cleanup INT TERM EXIT

# Check if Ollama is already running
if curl -s http://127.0.0.1:11435/api/tags > /dev/null 2>&1; then
    echo "[OK] Ollama engine is already running!"
else
    echo "Starting offline Mac AI Engine..."
    HOME="$OLLAMA_RUNTIME" "$SHARED_DIR/bin/ollama-darwin" serve > "$OLLAMA_RUNTIME/server.log" 2>&1 &
    OLLAMA_PID=$!
    
    echo "Waiting for engine to initialize..."
    WAIT_COUNT=0
    until curl -s http://127.0.0.1:11435/api/tags > /dev/null 2>&1; do
        sleep 1
        WAIT_COUNT=$((WAIT_COUNT + 1))
        if [ "$WAIT_COUNT" -ge 60 ]; then
            echo ""
            echo "ERROR: Engine failed to respond after 60 seconds."
            echo "Check log: $OLLAMA_RUNTIME/server.log"
            echo "Please try running install.command again to repair the engine."
            cleanup
            exit 1
        fi
    done
    echo "[OK] Engine is online!"
fi

echo ""
echo "==================================================="
echo "  AI ENGINE IS RUNNING"
echo "  Chat UI will open automatically."
echo "  Press Ctrl+C to shut down."
echo "==================================================="
echo ""

# Launch Python chat server using system Python (comes pre-installed on Mac)
if command -v python3 &> /dev/null; then
    python3 "$SHARED_DIR/chat_server.py"
elif command -v python &> /dev/null; then
    python "$SHARED_DIR/chat_server.py"
else
    echo "ERROR: Python not found. Please type 'brew install python' in terminal."
    cleanup
    exit 1
fi

cleanup
echo "Goodbye!"
