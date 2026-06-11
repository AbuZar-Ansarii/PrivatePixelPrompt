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
export OLLAMA_HOST="127.0.0.1:11434"
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
    exit 1
fi

# Check if Ollama is already running
if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "[OK] Ollama engine is already running!"
else
    echo "Starting offline Mac AI Engine..."
    HOME="$OLLAMA_RUNTIME" "$SHARED_DIR/bin/ollama-darwin" serve &
    OLLAMA_PID=$!

    # Check if the process actually started
    if ! kill -0 $OLLAMA_PID 2>/dev/null; then
        echo "==================================================="
        echo "  ERROR: Failed to start Ollama engine!"
        echo "==================================================="
        echo ""
        echo "  Possible causes:"
        echo "  - The binary may not be executable (try: chmod +x Shared/bin/ollama-darwin)"
        echo "  - The binary may be quarantined (try: xattr -d com.apple.quarantine Shared/bin/ollama-darwin)"
        echo "  - Your Mac architecture may not be supported"
        echo ""
        read -n 1 -s -r -p "Press any key to continue..."
        exit 1
    fi

    echo "Waiting for engine to initialize..."
    WAIT_COUNT=0
    MAX_WAIT=30
    until curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; do
        sleep 1
        WAIT_COUNT=$((WAIT_COUNT + 1))
        if [ $WAIT_COUNT -ge $MAX_WAIT ]; then
            echo "==================================================="
            echo "  ERROR: Engine did not start within ${MAX_WAIT} seconds!"
            echo "==================================================="
            echo ""
            echo "  Process ID: $OLLAMA_PID"
            echo ""
            if kill -0 $OLLAMA_PID 2>/dev/null; then
                echo "  Process is still running but not responding."
                echo "  Check for errors by running in terminal:"
                echo "    cd '$SHARED_DIR'"
                echo "    HOME=.ollama-runtime ./bin/ollama-darwin serve"
            else
                echo "  Process has exited unexpectedly."
            fi
            echo ""
            read -n 1 -s -r -p "Press any key to continue..."
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
    exit 1
fi

# Cleanup
if [ -n "$OLLAMA_PID" ]; then
    kill $OLLAMA_PID 2>/dev/null
    wait $OLLAMA_PID 2>/dev/null
fi
echo "Goodbye!"
