#!/usr/bin/env python3
"""
Portable AI Chat Server
=======================
A zero-dependency Python HTTP server that:
  1. Serves the FastChatUI.html web interface
  2. Saves/loads chat history as JSON files on the USB drive
  3. Proxies all Ollama API requests (eliminates CORS issues)

Works on Windows, macOS, and Linux without installing anything.
"""

import http.server
import json
import os
import sys
import urllib.request
import urllib.error
import threading
import webbrowser
import time
import platform
import ctypes
import logging
import logging.handlers
import queue
import uuid
import subprocess
import base64
import re
import atexit
import shutil
import socket
import secrets
from collections import defaultdict

# Disable system proxies for internal local connections to avoid 502 errors
os.environ["no_proxy"] = "127.0.0.1,localhost,0.0.0.0"
os.environ["NO_PROXY"] = "127.0.0.1,localhost,0.0.0.0"
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# ── Image Generation Job Tracking ──────────────────────────────
IMAGE_JOBS = {}
IMAGE_JOBS_LOCK = threading.RLock()
IMAGE_JOB_MAX_AGE_SECS = 3600  # Cleanup old jobs after 1 hour

def _register_image_job(job_id):
    with IMAGE_JOBS_LOCK:
        IMAGE_JOBS[job_id] = {
            "status": "starting",
            "step": 0,
            "total_steps": 0,
            "elapsed_ms": 0,
            "eta_ms": None,
            "started_at": time.time(),
            "updated_at": time.time(),
            "error": None,
            "image_b64": None,
            "params": {},
        }

def _update_image_job(job_id, **kwargs):
    with IMAGE_JOBS_LOCK:
        if job_id in IMAGE_JOBS:
            IMAGE_JOBS[job_id].update(kwargs)
            IMAGE_JOBS[job_id]["updated_at"] = time.time()

def _get_image_job(job_id):
    with IMAGE_JOBS_LOCK:
        return IMAGE_JOBS.get(job_id, {}).copy()

def _cleanup_old_image_jobs():
    now = time.time()
    with IMAGE_JOBS_LOCK:
        stale = [jid for jid, j in IMAGE_JOBS.items() if now - j["updated_at"] > IMAGE_JOB_MAX_AGE_SECS]
        for jid in stale:
            del IMAGE_JOBS[jid]

# Optional: psutil for hardware stats (graceful fallback to native APIs if not installed)
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ── Configuration ──────────────────────────────────────────────
CHAT_SERVER_PORT = int(os.environ.get("CHAT_SERVER_PORT", 3333))
# Allow OLLAMA_HOST_URL as an explicit override (used by platform launchers)
# Fallback to OLLAMA_HOST, then default
OLLAMA_HOST = os.environ.get("OLLAMA_HOST_URL") or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
# Normalise: ensure it has a scheme
if OLLAMA_HOST and not OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = "http://" + OLLAMA_HOST
LLAMA_CPP_MODE = "--llama-cpp" in sys.argv or os.environ.get("LLAMA_CPP_MODE") == "1"
if LLAMA_CPP_MODE:
    OLLAMA_HOST = os.environ.get("LLAMA_CPP_HOST", "http://127.0.0.1:8080")

# Optional API token for LAN security
API_TOKEN = os.environ.get("PORTABLE_AI_TOKEN", "")

# Rate limiting config
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", 100))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", 60))  # seconds

# Version
VERSION = "1.2.0"

# Always resolve paths relative to THIS script's location (the USB drive)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Stable Diffusion Engine Paths ──────────────────────────────
def _find_sd_binary():
    """Locate the stable-diffusion.cpp CLI binary for this platform."""
    plat = platform.system()
    bin_dir = os.path.join(SCRIPT_DIR, "bin")
    candidates = []
    if plat == "Windows":
        sd_dir = os.path.join(bin_dir, "sd-windows")
        candidates = [os.path.join(sd_dir, "sd.exe"), os.path.join(sd_dir, "sd-cli.exe")]
    elif plat == "Linux":
        sd_dir = os.path.join(bin_dir, "sd-linux")
        candidates = [os.path.join(sd_dir, "sd"), os.path.join(sd_dir, "sd-cli")]
    else:  # Darwin / macOS
        sd_dir = os.path.join(bin_dir, "sd-mac")
        candidates = [os.path.join(sd_dir, "sd"), os.path.join(sd_dir, "sd-cli")]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None

SD_BINARY = _find_sd_binary()
SD_MODEL = os.path.join(SCRIPT_DIR, "models", "CyberRealistic_V3.3_FP16.safetensors")

def _test_sd_binary():
    """Fast check for SD binary existence."""
    return bool(SD_BINARY and os.path.isfile(SD_BINARY))

def _is_sd_enabled():
    """Check if SD is fully ready to run."""
    return bool(SD_BINARY and os.path.isfile(SD_BINARY) and os.path.isfile(SD_MODEL))

SD_ENABLED = _is_sd_enabled()

def _find_tts_binary():
    """Locate the piper TTS binary for this platform."""
    plat = platform.system()
    bin_dir = os.path.join(SCRIPT_DIR, "bin", "piper")
    if plat == "Windows":
        candidates = [os.path.join(bin_dir, "piper.exe")]
    else:
        candidates = [os.path.join(bin_dir, "piper")]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None

TTS_BINARY = _find_tts_binary()
TTS_MODELS_DIR = os.path.join(SCRIPT_DIR, "models", "tts")
TTS_ENABLED = TTS_BINARY is not None and os.path.isdir(TTS_MODELS_DIR)

# Ollama binary path for engine management
OLLAMA_BIN = None
if platform.system() == "Windows":
    OLLAMA_BIN = os.path.join(SCRIPT_DIR, "bin", "ollama-windows.exe")
elif platform.system() == "Linux":
    OLLAMA_BIN = os.path.join(SCRIPT_DIR, "bin", "ollama-linux")
else:
    OLLAMA_BIN = os.path.join(SCRIPT_DIR, "bin", "ollama-darwin")

CHATS_DIR = os.path.join(SCRIPT_DIR, "chat_data")
CHATS_FILE = os.path.join(CHATS_DIR, "chats.json")
SETTINGS_FILE = os.path.join(CHATS_DIR, "settings.json")
HTML_FILE = os.path.join(SCRIPT_DIR, "FastChatUI.html")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "chat_server.log")
LOG_MODE_ERRORS_ONLY = "errors_only"
LOG_MODE_ALL = "all"
DEFAULT_LOG_MODE = LOG_MODE_ERRORS_ONLY

# Lock shared file persistence paths to avoid concurrent write corruption.
DATA_FILE_LOCK = threading.RLock()
SETTINGS_FILE_LOCK = threading.RLock()
LOG_MODE_LOCK = threading.RLock()
ACTIVE_LOG_MODE = DEFAULT_LOG_MODE

# Rate limiting state
request_counts = defaultdict(lambda: {"count": 0, "reset_time": time.time()})
RATE_LIMIT_LOCK = threading.RLock()

def _get_local_ip():
    """Attempt to find the local LAN IP."""
    local_ip = "127.0.0.1"
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            # doesn't even have to be reachable
            s.connect(('10.254.254.254', 1))
            local_ip = s.getsockname()[0]
        except Exception:
            pass
        finally:
            s.close()
    except Exception:
        pass
    return local_ip

LOCAL_IP = _get_local_ip()

# ── Pure-Python Hardware Stats (no psutil needed) ──────────────
_cpu_times_last = None  # (idle, total) from previous sample

def _get_hw_stats():
    """Return (cpu_percent, ram_percent) using only stdlib / ctypes."""
    global _cpu_times_last  # must be at top of function, before any branch uses it

    if HAS_PSUTIL:
        cpu = round(psutil.cpu_percent(interval=0.25), 1)
        ram = round(psutil.virtual_memory().percent, 1)
        return cpu, ram

    plat = platform.system()

    # ── Windows ──────────────────────────────────────────────────
    if plat == "Windows":
        # RAM via GlobalMemoryStatusEx
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength",                ctypes.c_ulong),
                ("dwMemoryLoad",            ctypes.c_ulong),
                ("ullTotalPhys",            ctypes.c_ulonglong),
                ("ullAvailPhys",            ctypes.c_ulonglong),
                ("ullTotalPageFile",        ctypes.c_ulonglong),
                ("ullAvailPageFile",        ctypes.c_ulonglong),
                ("ullTotalVirtual",         ctypes.c_ulonglong),
                ("ullAvailVirtual",         ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        msx = MEMORYSTATUSEX()
        msx.dwLength = ctypes.sizeof(msx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(msx))
        ram = float(msx.dwMemoryLoad)

        # CPU via GetSystemTimes (idle/kernel/user tick counts)
        FILETIME = ctypes.c_ulonglong
        idle, kern, user = FILETIME(), FILETIME(), FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        idle_v = idle.value
        total_v = kern.value + user.value
        if _cpu_times_last is None:
            # First call — sleep briefly and sample again
            time.sleep(0.25)
            idle2, kern2, user2 = FILETIME(), FILETIME(), FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle2), ctypes.byref(kern2), ctypes.byref(user2))
            d_idle  = idle2.value - idle_v
            d_total = (kern2.value + user2.value) - total_v
            _cpu_times_last = (idle2.value, kern2.value + user2.value)
        else:
            prev_idle, prev_total = _cpu_times_last
            d_idle  = idle_v  - prev_idle
            d_total = total_v - prev_total
            _cpu_times_last = (idle_v, total_v)

        cpu = round((1.0 - d_idle / max(d_total, 1)) * 100.0, 1)
        cpu = max(0.0, min(100.0, cpu))
        return cpu, ram

    # ── Linux ─────────────────────────────────────────────────────
    elif plat == "Linux":
        # RAM
        ram = 0.0
        try:
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = int(parts[1])
            total = mem.get("MemTotal", 1)
            avail = mem.get("MemAvailable", total)
            ram = round((1 - avail / total) * 100, 1)
        except Exception:
            pass
        # CPU via /proc/stat delta
        cpu = 0.0
        try:
            def read_cpu():
                with open("/proc/stat") as f:
                    parts = f.readline().split()
                vals = [int(x) for x in parts[1:]]
                idle = vals[3]
                total = sum(vals)
                return idle, total
            if _cpu_times_last is None:
                i1, t1 = read_cpu()
                time.sleep(0.25)
                i2, t2 = read_cpu()
            else:
                i1, t1 = _cpu_times_last
                i2, t2 = read_cpu()
            _cpu_times_last = (i2, t2)
            d_idle  = i2 - i1
            d_total = t2 - t1
            cpu = round((1 - d_idle / max(d_total, 1)) * 100, 1)
        except Exception:
            pass
        return cpu, ram

    # ── macOS ─────────────────────────────────────────────────────
    else:
        if HAS_PSUTIL:
            cpu = round(psutil.cpu_percent(interval=0.25), 1)
            ram = round(psutil.virtual_memory().percent, 1)
        else:
            # Signal unavailable so UI can show N/A
            cpu = -1.0
            ram = -1.0
        return cpu, ram

def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _normalize_log_mode(value):
    if value == LOG_MODE_ALL:
        return LOG_MODE_ALL
    return LOG_MODE_ERRORS_ONLY

def _is_log_enabled(level):
    with LOG_MODE_LOCK:
        mode = ACTIVE_LOG_MODE
    if mode == LOG_MODE_ALL:
        return True
    return level >= logging.ERROR

def _load_settings_file():
    default_settings = {
        "globalSystemPrompt": "",
        "temperature": 0.7,
        "logMode": DEFAULT_LOG_MODE,
    }
    try:
        with SETTINGS_FILE_LOCK:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        if isinstance(loaded, dict):
            merged = dict(default_settings)
            merged.update(loaded)
            merged["logMode"] = _normalize_log_mode(merged.get("logMode"))
            return merged
    except Exception:
        pass
    return default_settings

def _persist_settings_file(settings):
    with SETTINGS_FILE_LOCK:
        temp_file = SETTINGS_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush()
        os.replace(temp_file, SETTINGS_FILE)

def _save_chats_with_backup(chats):
    """Atomically save chats with backup."""
    with DATA_FILE_LOCK:
        backup_file = CHATS_FILE + ".bak"
        if os.path.exists(CHATS_FILE):
            try:
                shutil.copy2(CHATS_FILE, backup_file)
            except Exception:
                pass
        temp_file = CHATS_FILE + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(chats, f, ensure_ascii=False, indent=2)
                f.flush()
            os.replace(temp_file, CHATS_FILE)
        except Exception:
            # Restore from backup on failure
            if os.path.exists(backup_file):
                try:
                    os.replace(backup_file, CHATS_FILE)
                except Exception:
                    pass
            raise

def _set_active_log_mode(mode):
    global ACTIVE_LOG_MODE
    with LOG_MODE_LOCK:
        ACTIVE_LOG_MODE = _normalize_log_mode(mode)

def _get_hardware_specs():
    """Collect a stable host hardware snapshot for log enrichment."""
    plat = platform.system()
    specs = {
        "platform": plat,
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python_version": sys.version.split()[0],
        "cpu_count_logical": os.cpu_count() or 0,
        "has_psutil": HAS_PSUTIL,
        "ram_total_gb": None,
    }
    if HAS_PSUTIL:
        try:
            total_ram = psutil.virtual_memory().total
            specs["ram_total_gb"] = round(total_ram / (1024 ** 3), 2)
        except Exception:
            pass
    elif plat == "Windows":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            msx = MEMORYSTATUSEX()
            msx.dwLength = ctypes.sizeof(msx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(msx))
            specs["ram_total_gb"] = round(msx.ullTotalPhys / (1024 ** 3), 2)
        except Exception:
            pass
    elif plat == "Linux":
        try:
            mem = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem[parts[0].rstrip(":")] = int(parts[1])
            total_kb = mem.get("MemTotal")
            if total_kb:
                specs["ram_total_gb"] = round((total_kb * 1024) / (1024 ** 3), 2)
        except Exception:
            pass
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        _, value = line.split(":", 1)
                        model = value.strip()
                        if model:
                            specs["processor"] = model
                            break
        except Exception:
            pass
    else:
        # Keep parity with _get_hw_stats fallback behavior:
        # skip macOS/native probing in the non-psutil path.
        pass
    return specs

# ── Stable Diffusion Helpers ───────────────────────────────────
def _is_ollama_running():
    """Check if Ollama responds on its default port."""
    try:
        req = urllib.request.Request(OLLAMA_HOST + "/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False

def _kill_ollama():
    """Stop any running Ollama process. Platform-specific."""
    plat = platform.system()
    try:
        if plat == "Windows":
            subprocess.run(["taskkill", "/F", "/IM", "ollama-windows.exe"], capture_output=True)
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True)
            subprocess.run(["taskkill", "/F", "/IM", "ollama app.exe"], capture_output=True)
            # Try to stop Windows service if present (requires admin, may fail silently)
            subprocess.run(["sc", "stop", "Ollama"], capture_output=True)
            # Aggressive fallback: kill any process with 'ollama' in the name
            try:
                result = subprocess.run(["wmic", "process", "where", "name like '%ollama%'", "delete"],
                                        capture_output=True, text=True, timeout=5)
            except Exception:
                pass
        elif plat == "Linux":
            subprocess.run(["pkill", "-f", "ollama-linux"], capture_output=True)
            subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        else:  # macOS
            subprocess.run(["pkill", "-f", "ollama-darwin"], capture_output=True)
            subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True)
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
    except Exception:
        pass
    # Wait for port to be free
    for _ in range(20):
        if not _is_ollama_running():
            break
        time.sleep(0.5)

def _start_ollama():
    """Start Ollama in the background if binary exists."""
    if not OLLAMA_BIN or not os.path.isfile(OLLAMA_BIN):
        return False
    try:
        env = os.environ.copy()
        env["OLLAMA_MODELS"] = os.path.join(SCRIPT_DIR, "models", "ollama_data")
        env["OLLAMA_ORIGINS"] = "*"
        # Use the same host the proxy expects (respects env override)
        parsed = urllib.parse.urlparse(OLLAMA_HOST)
        env["OLLAMA_HOST"] = f"{parsed.hostname or '127.0.0.1'}:{parsed.port or 11434}"
        env["OLLAMA_LIBRARY_PATH"] = os.path.join(SCRIPT_DIR, "bin", "lib", "ollama")
        subprocess.Popen([OLLAMA_BIN, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Wait for it to be ready
        for _ in range(30):
            if _is_ollama_running():
                return True
            time.sleep(1)
        return False
    except Exception:
        return False

def _parse_sd_output(pipe, job_id, total_steps):
    """Read sd stdout+stderr line-by-line and update job progress.
    stable-diffusion.cpp prints step info like 'step 1 sampling completed'.
    We merge stdout+stderr so we catch progress regardless of which stream
    the binary writes it to."""
    # Match: "step 1 sampling completed" or "step 1/20" or "step 1 of 20"
    step_pattern = re.compile(r"step\s+(\d+)", re.IGNORECASE)
    # Only match X/Y when it follows "sampling" to avoid model-loading bars
    sampling_pattern = re.compile(r"sampling.*?\b(\d+)\s*/\s*(\d+)", re.IGNORECASE)
    # Only match % when it follows "sampling"
    pct_pattern = re.compile(r"sampling.*?(\d+)")

    last_step = 0
    step_start_time = None
    step_times = []

    try:
        for raw_line in iter(pipe.readline, b""):
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            # Try to extract step / total
            current_step = 0
            matched_total = total_steps

            m = step_pattern.search(line)
            if m:
                current_step = int(m.group(1))
            else:
                m = sampling_pattern.search(line)
                if m:
                    current_step = int(m.group(1))
                    matched_total = int(m.group(2))
                else:
                    m = pct_pattern.search(line)
                    if m and total_steps > 0:
                        pct = int(m.group(1))
                        current_step = int(pct / 100.0 * total_steps)

            if current_step > 0:
                now = time.time()
                if step_start_time is not None and current_step > last_step:
                    for _ in range(current_step - last_step):
                        step_times.append(now - step_start_time)
                        if len(step_times) > 10:
                            step_times.pop(0)
                step_start_time = now
                last_step = current_step

                job = _get_image_job(job_id)
                elapsed = now - job.get("started_at", now)
                eta_ms = None
                if current_step < matched_total and step_times:
                    avg_step = sum(step_times) / len(step_times)
                    eta_ms = int(avg_step * (matched_total - current_step) * 1000)

                _update_image_job(
                    job_id,
                    status="generating",
                    step=current_step,
                    total_steps=matched_total,
                    elapsed_ms=int(elapsed * 1000),
                    eta_ms=eta_ms,
                )
    except Exception:
        pass
    finally:
        pipe.close()

def _run_sd_generation(job_id, payload, output_path):
    """Run stable-diffusion.cpp CLI to generate an image asynchronously. Updates job dict."""
    if not SD_BINARY or not os.path.isfile(SD_BINARY):
        _update_image_job(job_id, status="error", error="Stable Diffusion engine not found. Please run the installer.")
        return
    if not os.path.isfile(SD_MODEL):
        _update_image_job(job_id, status="error", error="Image model not found. Please run the installer to download CyberRealistic.")
        return

    prompt = payload.get("prompt", "").strip()
    if not prompt:
        _update_image_job(job_id, status="error", error="Prompt is required.")
        return

    # Clamp parameters to safe ranges
    steps = max(1, min(50, _safe_int(payload.get("steps"), 20)))
    cfg = max(1.0, min(15.0, float(payload.get("cfg_scale", 7.0))))
    width = max(256, min(768, _safe_int(payload.get("width"), 512)))
    height = max(256, min(768, _safe_int(payload.get("height"), 512)))
    seed = _safe_int(payload.get("seed"), -1)
    negative = payload.get("negative_prompt", "").strip()
    sampling = payload.get("sampling_method", "euler_a")
    if sampling not in ("euler", "euler_a", "heun", "dpm2", "dpm++2m", "dpm++2mv2"):
        sampling = "euler_a"

    cmd = [
        SD_BINARY,
        "-m", SD_MODEL,
        "-p", prompt,
        "-o", output_path,
        "--steps", str(steps),
        "--cfg-scale", str(cfg),
        "-W", str(width),
        "-H", str(height),
        "--sampling-method", sampling,
        "--threads", str(os.cpu_count() or 4),
    ]
    if negative:
        cmd += ["-n", negative]
    if seed != -1:
        cmd += ["--seed", str(seed)]

    print(f"\n[SD] GENERATING IMAGE...")
    print(f"     Prompt: {prompt}")
    print(f"     Engine: {SD_BINARY}")
    print(f"     Model:  {SD_MODEL}")
    print(f"     Threads: {os.cpu_count() or 4}")
    
    _update_image_job(
        job_id,
        status="starting",
        step=0,
        total_steps=steps,
        params={
            "prompt": prompt,
            "negative_prompt": negative,
            "steps": steps,
            "cfg_scale": cfg,
            "width": width,
            "height": height,
            "seed": seed,
            "sampling_method": sampling,
        },
    )

    proc = None
    try:
        # Check model file before running
        if not os.path.isfile(SD_MODEL):
            raise FileNotFoundError(f"Model file not found: {SD_MODEL}")

        # Start process
        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            bufsize=1
        )
        print(f"     Process started (PID: {proc.pid})")
        
        # Start a thread to parse combined output
        output_thread = threading.Thread(
            target=_parse_sd_output,
            args=(proc.stdout, job_id, steps),
            daemon=True,
        )
        output_thread.start()

        return_code = proc.wait()
        output_thread.join()

        if return_code != 0:
            print(f"[SD] ERR: Process exited with code {return_code}")
            _update_image_job(job_id, status="error", error=f"SD engine error (code {return_code}). Ensure your hardware supports this model.")
            return
        
        if not os.path.exists(output_path):
            print(f"[SD] ERR: Output file was not created.")
            _update_image_job(job_id, status="error", error="Image file not created by engine.")
            return

        print(f"[SD] SUCCESS: Created image at {output_path}")

        if not os.path.isfile(output_path):
            _update_image_job(job_id, status="error", error="Image generation failed: output file not created.")
            return

        # Read and encode the PNG
        with open(output_path, "rb") as f:
            img_bytes = f.read()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        # Clean up temp file
        try:
            os.remove(output_path)
        except Exception:
            pass

        _update_image_job(
            job_id,
            status="done",
            image_b64=img_b64,
            elapsed_ms=int((time.time() - IMAGE_JOBS[job_id]["started_at"]) * 1000),
        )
    except Exception as e:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        _update_image_job(job_id, status="error", error=str(e))

class LogFormatter(logging.Formatter):
    """Readable, multi-line formatter with strict spacing and rich context."""

    def format(self, record):
        local_now = datetime.now().astimezone()
        timestamp = local_now.isoformat()
        tz_label = local_now.tzname() or "local"
        exception_text = ""
        if record.exc_info:
            exception_text = self.formatException(record.exc_info)
        hardware = getattr(record, "hardware_specs", None) or {}
        request_headers = getattr(record, "request_headers", None) or {}
        lines = [
            "=" * 96,
            f"timestamp     : {timestamp}",
            f"timezone      : {tz_label}",
            f"level         : {record.levelname}",
            f"event         : {record.getMessage()}",
            "",
            "request_context",
            f"  request_id   : {getattr(record, 'request_id', '-')}",
            f"  method       : {getattr(record, 'http_method', '-')}",
            f"  path         : {getattr(record, 'path', '-')}",
            f"  api_source   : {getattr(record, 'api_source', '-')}",
            f"  client_ip    : {getattr(record, 'client_ip', '-')}",
            f"  user_agent   : {request_headers.get('User-Agent', '-')}",
            f"  model_name   : {getattr(record, 'model_name', '-')}",
            f"  model_temp   : {getattr(record, 'model_temperature', '-')}",
            f"  model_stream : {getattr(record, 'model_stream', '-')}",
            "",
            "code_context",
            f"  logger       : {record.name}",
            f"  module       : {record.module}",
            f"  function     : {record.funcName}",
            f"  line         : {record.lineno}",
            f"  thread       : {record.threadName}",
            "",
            "hardware_specs",
            f"  platform     : {hardware.get('platform', '-')}",
            f"  release      : {hardware.get('platform_release', '-')}",
            f"  machine      : {hardware.get('machine', '-')}",
            f"  processor    : {hardware.get('processor', '-')}",
            f"  cpu_logical  : {hardware.get('cpu_count_logical', '-')}",
            f"  ram_total_gb : {hardware.get('ram_total_gb', '-')}",
            f"  python       : {hardware.get('python_version', '-')}",
        ]
        if exception_text:
            lines.extend(["", "exception", exception_text])
        lines.append("=" * 96)
        return "\n".join(lines)

class ImmediateFlushRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Flush each record immediately so logs are visible without waiting."""

    def emit(self, record):
        super().emit(record)
        try:
            self.flush()
        except Exception:
            pass

def configure_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("chat_server")
    # Allow --debug and --quiet CLI overrides
    if "--debug" in sys.argv:
        logger.setLevel(logging.DEBUG)
    elif "--quiet" in sys.argv:
        logger.setLevel(logging.ERROR)
    else:
        logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.propagate = False

    log_queue = queue.Queue(maxsize=10000)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    # Smaller rotation for USB drives (5MB with 2 backups)
    file_handler = ImmediateFlushRotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(LogFormatter())

    listener = logging.handlers.QueueListener(log_queue, file_handler, respect_handler_level=True)
    listener.start()
    return logger, listener

def _log_event(level, message, request_context=None, exc_info=False):
    if not _is_log_enabled(level):
        return
    request_context = request_context or {}
    LOGGER.log(
        level,
        message,
        extra={
            "request_id": request_context.get("request_id", "-"),
            "http_method": request_context.get("method", "-"),
            "path": request_context.get("path", "-"),
            "api_source": request_context.get("api_source", "-"),
            "client_ip": request_context.get("client_ip", "-"),
            "request_headers": request_context.get("request_headers", {}),
            "model_name": request_context.get("model_name", "-"),
            "model_temperature": request_context.get("model_temperature", "-"),
            "model_stream": request_context.get("model_stream", "-"),
            "hardware_specs": HOST_HARDWARE_SPECS,
        },
        exc_info=exc_info,
    )

def ensure_data_dir():
    """Create required data folders if they don't exist."""
    os.makedirs(CHATS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(CHATS_FILE):
        with open(CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "globalSystemPrompt": "",
                    "temperature": 0.7,
                    "logMode": DEFAULT_LOG_MODE,
                },
                f,
            )

class ChatHandler(http.server.BaseHTTPRequestHandler):
    """Handles all HTTP requests for the Portable AI Chat."""

    def _build_request_context(self, api_source):
        headers = {}
        for key in ("User-Agent", "Authorization"):
            value = self.headers.get(key)
            if value:
                headers[key] = value
        return {
            "request_id": str(uuid.uuid4()),
            "method": getattr(self, "command", "-"),
            "path": getattr(self, "path", "-"),
            "api_source": api_source,
            "client_ip": self.client_address[0] if self.client_address else "-",
            "request_headers": headers,
        }

    def _validate_auth(self):
        """Returns True if auth is disabled or token matches."""
        if not API_TOKEN:
            return True
        auth_header = self.headers.get("Authorization", "")
        expected = f"Bearer {API_TOKEN}"
        return secrets.compare_digest(auth_header, expected)

    def _check_rate_limit(self):
        """Returns (allowed, retry_after_seconds)."""
        client_ip = self.client_address[0] if self.client_address else "unknown"
        now = time.time()
        with RATE_LIMIT_LOCK:
            # Prune stale entries periodically
            stale = [ip for ip, e in request_counts.items() if now - e["reset_time"] > RATE_LIMIT_WINDOW]
            for ip in stale:
                del request_counts[ip]
            entry = request_counts[client_ip]
            if now - entry["reset_time"] > RATE_LIMIT_WINDOW:
                entry["count"] = 0
                entry["reset_time"] = now
            entry["count"] += 1
            if entry["count"] > RATE_LIMIT_REQUESTS:
                retry_after = int(RATE_LIMIT_WINDOW - (now - entry["reset_time"])) + 1
                return False, retry_after
        return True, 0

    def _read_body(self):
        length = _safe_int(self.headers.get("Content-Length"), 0)
        return self.rfile.read(length) if length > 0 else b""

    def log_message(self, format, *args):
        """Print all requests for easy debugging."""
        msg = format % args
        ts = time.strftime("%H:%M:%S")
        # Colour-code by status: errors red, warnings yellow, ok green
        if "404" in msg or "500" in msg or "502" in msg:
            prefix = "  \033[91m[ERR]\033[0m"
        elif "200" in msg or "204" in msg:
            prefix = "  \033[92m[ OK]\033[0m"
        else:
            prefix = "  \033[93m[---]\033[0m"
        print(f"{prefix} {ts}  {msg}")

    # ── CORS headers ───────────────────────────────────────────
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Routing ────────────────────────────────────────────────
    def _send_json(self, status, data):
        """Send a JSON response with security and CORS headers."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_security_headers()
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _require_auth(self):
        if not self._validate_auth():
            self._send_json(401, {"error": "Unauthorized"})
            return False
        return True

    def _apply_rate_limit(self):
        allowed, retry_after = self._check_rate_limit()
        if not allowed:
            self.send_response(429)
            self.send_header("Retry-After", str(retry_after))
            self.send_header("Content-Type", "application/json")
            self._send_security_headers()
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Rate limited. Too many requests."}).encode())
            return False
        return True

    def do_GET(self):
        if not self._apply_rate_limit():
            return
        path = urlparse(self.path).path

        # Serve the main UI
        if path == "/" or path == "/index.html":
            self._serve_html()

        # Chat data API
        elif path == "/api/chats":
            self._get_chats()

        # Settings API
        elif path == "/api/settings":
            self._get_settings()

        # Hardware stats API
        elif path == "/api/stats":
            self._get_stats()

        # Engine status API
        elif path == "/api/engine-status":
            self._get_engine_status()

        # Image model info API
        elif path == "/api/image-models":
            self._get_image_models()

        # TTS voices info API
        elif path == "/api/tts-voices":
            self._get_tts_voices()

        # Image generation progress API
        elif path == "/api/image-progress":
            self._get_image_progress()

        # Health check API
        elif path == "/api/health":
            self._get_health()

        # Version API
        elif path == "/api/version":
            self._get_version()

        # Proxy Ollama API
        elif path.startswith("/ollama/"):
            self._proxy_ollama("GET")

        else:
            # Try serving static files from SCRIPT_DIR
            self._serve_static(path)

    def do_POST(self):
        if not self._apply_rate_limit():
            return
        path = urlparse(self.path).path

        if path == "/api/chats":
            if not self._require_auth():
                return
            self._save_chats()

        elif path == "/api/settings":
            if not self._require_auth():
                return
            self._save_settings()

        # Image generation API
        elif path == "/api/generate-image":
            if not self._require_auth():
                return
            self._generate_image()

        # TTS generation API
        elif path == "/api/generate-tts":
            if not self._require_auth():
                return
            self._generate_tts()

        # Engine control APIs
        elif path == "/api/stop-ollama":
            if not self._require_auth():
                return
            self._stop_ollama_endpoint()

        elif path == "/api/start-ollama":
            if not self._require_auth():
                return
            self._start_ollama_endpoint()

        # Proxy Ollama API
        elif path.startswith("/ollama/"):
            if not self._require_auth():
                return
            self._proxy_ollama("POST")

        else:
            self.send_response(404)
            self._send_security_headers()
            self._cors_headers()
            self.end_headers()

    def do_DELETE(self):
        if not self._apply_rate_limit():
            return
        path = urlparse(self.path).path
        if path.startswith("/ollama/"):
            if not self._require_auth():
                return
            self._proxy_ollama("DELETE")
        else:
            self.send_response(404)
            self._send_security_headers()
            self._cors_headers()
            self.end_headers()

    # ── Serve HTML ─────────────────────────────────────────────
    def _send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' ws: wss:;")

    def _serve_html(self):
        try:
            with open(HTML_FILE, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_security_headers()
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"FastChatUI.html not found.")

    def _serve_static(self, path):
        """Serve static files (CSS, JS, images) from SCRIPT_DIR."""
        safe_path = os.path.normpath(path.lstrip("/"))
        full_path = os.path.join(SCRIPT_DIR, safe_path)

        # Security: don't allow path traversal
        if not full_path.startswith(SCRIPT_DIR):
            self.send_response(403)
            self.end_headers()
            return

        if os.path.isfile(full_path):
            ext = os.path.splitext(full_path)[1].lower()
            mime_types = {
                ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
                ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
                ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2"
            }
            content_type = mime_types.get(ext, "application/octet-stream")
            with open(full_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self._send_security_headers()
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()

    # ── Chat Persistence ───────────────────────────────────────
    def _get_chats(self):
        request_context = self._build_request_context("/api/chats")
        try:
            with DATA_FILE_LOCK:
                with open(CHATS_FILE, "r", encoding="utf-8") as f:
                    data = f.read()
        except (FileNotFoundError, json.JSONDecodeError):
            data = "[]"
        except Exception:
            _log_event(logging.ERROR, "Failed to read chats file", request_context=request_context, exc_info=True)
            data = "[]"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _save_chats(self):
        request_context = self._build_request_context("/api/chats")
        body = self._read_body()
        try:
            chats = json.loads(body)
            _save_chats_with_backup(chats)
            _log_event(logging.INFO, "Chats saved successfully", request_context=request_context)
            self._send_json(200, {"ok": True})
        except Exception as e:
            _log_event(logging.ERROR, "Failed to save chats", request_context=request_context, exc_info=True)
            self._send_json(500, {"error": str(e)})

    def _get_settings(self):
        request_context = self._build_request_context("/api/settings")
        try:
            settings = _load_settings_file()
            data = json.dumps(settings)
        except Exception:
            _log_event(logging.ERROR, "Failed to read settings file", request_context=request_context, exc_info=True)
            data = "{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _save_settings(self):
        request_context = self._build_request_context("/api/settings")
        body = self._read_body()
        try:
            incoming = json.loads(body)
            if not isinstance(incoming, dict):
                raise ValueError("Settings payload must be a JSON object")

            # Prevent client from overriding logMode if not allowed
            settings = _load_settings_file()
            settings.update(incoming)
            settings["logMode"] = _normalize_log_mode(settings.get("logMode"))
            _persist_settings_file(settings)
            _set_active_log_mode(settings["logMode"])
            _log_event(logging.INFO, "Settings saved successfully", request_context=request_context)
            self._send_json(200, {"ok": True, "logMode": settings["logMode"]})
        except Exception as e:
            _log_event(logging.ERROR, "Failed to save settings", request_context=request_context, exc_info=True)
            self._send_json(500, {"error": str(e)})

    # ── Hardware Stats ─────────────────────────────────────────
    def _get_stats(self):
        """Return CPU % and RAM % as JSON. Works with no external packages."""
        try:
            cpu, ram = _get_hw_stats()
            self._send_json(200, {"cpu_percent": cpu, "ram_percent": ram, "has_psutil": HAS_PSUTIL})
        except Exception as e:
            _log_event(
                logging.ERROR,
                "Failed to collect hardware stats",
                request_context=self._build_request_context("/api/stats"),
                exc_info=True,
            )
            self._send_json(500, {"error": str(e)})

    # ── Image Generation API ───────────────────────────────────
    def _get_image_models(self):
        """Return the available image generation model(s)."""
        models = []
        sd_ready = _is_sd_enabled()
        if sd_ready:
            models.append({
                "name": "CyberRealistic v3.3 FP16",
                "local": "cyberrealistic-local",
                "file": "CyberRealistic_V3.3_FP16.safetensors",
                "size": "1.99 GB",
                "label": "UNCENSORED",
                "badge": "SD 1.5 - CYBER REALISTIC",
            })
        self._send_json(200, {"models": models, "sd_enabled": sd_ready})

    def _get_engine_status(self):
        """Return which engines are currently running."""
        try:
            ollama_up = _is_ollama_running()
            sd_ready = _is_sd_enabled()

            # Check if SD binary and model exist
            sd_binary_exists = bool(SD_BINARY and os.path.isfile(SD_BINARY))
            sd_model_exists = bool(SD_MODEL and os.path.isfile(SD_MODEL))

            self._send_json(200, {
                "ollama": ollama_up,
                "sd_enabled": sd_ready,
                "sd_binary": sd_binary_exists,
                "sd_model": sd_model_exists,
                "sd_healthy": sd_binary_exists,
                "tts_enabled": bool(TTS_ENABLED),
                "tts_binary": bool(TTS_BINARY and os.path.isfile(TTS_BINARY)),
                "local_ip": LOCAL_IP,
                "port": CHAT_SERVER_PORT,
                "version": VERSION,
                "auth_enabled": bool(API_TOKEN),
            })
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _stop_ollama_endpoint(self):
        """Stop the Ollama chat engine to free RAM for image generation."""
        request_context = self._build_request_context("/api/stop-ollama")
        try:
            _kill_ollama()
            _log_event(logging.INFO, "Ollama engine stopped by user", request_context=request_context)
            self._send_json(200, {"ok": True, "message": "Chat engine stopped."})
        except Exception as e:
            _log_event(logging.ERROR, "Failed to stop Ollama", request_context=request_context, exc_info=True)
            self._send_json(500, {"error": str(e)})

    def _start_ollama_endpoint(self):
        """Restart the Ollama chat engine."""
        request_context = self._build_request_context("/api/start-ollama")
        try:
            if _is_ollama_running():
                self._send_json(200, {"ok": True, "message": "Chat engine already running."})
                return
            ok = _start_ollama()
            if ok:
                _log_event(logging.INFO, "Ollama engine started by user", request_context=request_context)
                self._send_json(200, {"ok": True, "message": "Chat engine started."})
            else:
                raise RuntimeError("Ollama failed to start. Check that the engine is installed.")
        except Exception as e:
            _log_event(logging.ERROR, "Failed to start Ollama", request_context=request_context, exc_info=True)
            self._send_json(500, {"error": str(e)})

    def _generate_image(self):
        """Start an image generation job and return a job_id immediately."""
        request_context = self._build_request_context("/api/generate-image")
        body = self._read_body()
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}

        if not _is_sd_enabled():
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Image generation is not set up. Please run the installer."}).encode())
            return

        # Warn if Ollama is still running (RAM collision risk)
        if _is_ollama_running():
            self.send_response(409)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Chat engine is running. Please stop it first to free memory for image generation.",
                "needs_stop": True
            }).encode())
            return

        # Validate prompt
        prompt = payload.get("prompt", "").strip()
        if not prompt:
            self._send_json(400, {"error": "Prompt is required."})
            return
        if len(prompt) > 2000:
            self._send_json(400, {"error": "Prompt too long (max 2000 chars)."})
            return

        # Validate numeric parameters
        try:
            steps = max(1, min(50, int(payload.get("steps", 20))))
            cfg = max(1.0, min(15.0, float(payload.get("cfg_scale", 7.0))))
            width = max(256, min(768, int(payload.get("width", 512))))
            height = max(256, min(768, int(payload.get("height", 512))))
        except (ValueError, TypeError):
            self._send_json(400, {"error": "Invalid numeric parameters."})
            return

        # Inject validated params back into payload for _run_sd_generation
        payload["steps"] = steps
        payload["cfg_scale"] = cfg
        payload["width"] = width
        payload["height"] = height

        # Clean up stale jobs occasionally
        _cleanup_old_image_jobs()

        job_id = str(uuid.uuid4())
        output_id = str(uuid.uuid4())
        output_dir = os.path.join(SCRIPT_DIR, "chat_data", "generated_images")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{output_id}.png")

        _register_image_job(job_id)

        # Start generation in a background thread so we can return job_id immediately
        thread = threading.Thread(
            target=_run_sd_generation,
            args=(job_id, payload, output_path),
            daemon=True,
        )
        thread.start()

        _log_event(logging.INFO, "Image generation job started", request_context=request_context)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "job_id": job_id}).encode())

    def _get_image_progress(self):
        """Poll endpoint for image generation progress."""
        query = parse_qs(urlparse(self.path).query)
        job_id = query.get("job_id", [None])[0]
        if not job_id:
            self._send_json(400, {"error": "Missing job_id parameter."})
            return

        job = _get_image_job(job_id)
        if not job:
            self._send_json(404, {"error": "Job not found."})
            return

        # Build response with progress info
        resp = {
            "status": job.get("status"),
            "step": job.get("step", 0),
            "total_steps": job.get("total_steps", 0),
            "elapsed_ms": job.get("elapsed_ms", 0),
            "eta_ms": job.get("eta_ms"),
            "params": job.get("params", {}),
        }

        if job.get("status") == "done":
            resp["image_b64"] = job.get("image_b64")
            resp["mime_type"] = "image/png"
        elif job.get("status") == "error":
            resp["error"] = job.get("error", "Unknown error.")

        self._send_json(200, resp)

    # ── Text to Speech API ─────────────────────────────────────
    def _get_tts_voices(self):
        """Return the available TTS voice models."""
        voices = []
        if os.path.isdir(TTS_MODELS_DIR):
            for f in os.listdir(TTS_MODELS_DIR):
                if f.endswith(".onnx"):
                    name = f.replace(".onnx", "")
                    voices.append({"id": f, "name": name})
        self._send_json(200, {"voices": voices, "tts_enabled": TTS_ENABLED})

    def _generate_tts(self):
        """Generate speech from text using Piper and return the audio file."""
        request_context = self._build_request_context("/api/generate-tts")
        body = self._read_body()
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {}

        text = payload.get("text", "").strip()
        voice_id = payload.get("voice", "")

        if not TTS_ENABLED or not text or not voice_id:
            self.send_response(400)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid request or TTS not enabled"}).encode())
            return

        voice_path = os.path.join(TTS_MODELS_DIR, voice_id)
        if not os.path.isfile(voice_path):
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Voice model not found"}).encode())
            return

        # Output to a temporary file
        output_id = str(uuid.uuid4())
        output_dir = os.path.join(SCRIPT_DIR, "chat_data", "tts_output")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{output_id}.wav")

        try:
            # Piper command: piper --model <voice> --output_file <out>
            cmd = [TTS_BINARY, "--model", voice_path, "--output_file", output_path]
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=SCRIPT_DIR
            )
            stdout, stderr = process.communicate(input=text.encode('utf-8'), timeout=60)

            if process.returncode != 0:
                raise RuntimeError(f"Piper failed: {stderr.decode('utf-8', errors='ignore')}")

            if not os.path.isfile(output_path):
                raise RuntimeError("Piper finished but no output file was created.")

            with open(output_path, "rb") as f:
                audio_data = f.read()

            try: os.remove(output_path)
            except: pass

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Disposition", 'attachment; filename="speech.wav"')
            self._cors_headers()
            self.end_headers()
            self.wfile.write(audio_data)

            _log_event(logging.INFO, "TTS generated successfully", request_context=request_context)

        except Exception as e:
            _log_event(logging.ERROR, "TTS generation failed", request_context=request_context, exc_info=True)
            self._send_json(500, {"error": str(e)})

    def _get_health(self):
        """Return system health status."""
        try:
            ollama_ok = _is_ollama_running()
            sd_ok = _is_sd_enabled()
            tts_ok = TTS_ENABLED
            self._send_json(200, {
                "status": "healthy" if ollama_ok else "degraded",
                "timestamp": time.time(),
                "engines": {
                    "ollama": ollama_ok,
                    "stable_diffusion": sd_ok,
                    "tts": tts_ok,
                },
                "version": VERSION,
            })
        except Exception as e:
            self._send_json(500, {"status": "error", "error": str(e)})

    def _get_version(self):
        self._send_json(200, {"version": VERSION})

    # ── Ollama Proxy (streaming-aware) ─────────────────────────
    def _proxy_ollama(self, method):
        """
        Proxy requests from /ollama/* to the local Ollama engine.
        Supports streaming responses for /api/chat and /api/generate.
        """
        request_context = self._build_request_context("/ollama")
        ollama_path = self.path[len("/ollama"):]

        # Try primary host, then fallback if needed
        hosts_to_try = [OLLAMA_HOST]
        if "127.0.0.1" in OLLAMA_HOST:
            hosts_to_try.append(OLLAMA_HOST.replace("127.0.0.1", "localhost"))
        elif "localhost" in OLLAMA_HOST:
            hosts_to_try.append(OLLAMA_HOST.replace("localhost", "127.0.0.1"))

        body = None
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body = self.rfile.read(content_length)

        # Build request context from payload
        payload = {}
        if body:
            try:
                payload = json.loads(body)
            except Exception:
                pass
        if isinstance(payload, dict):
            request_context["model_name"] = payload.get("model", "-")
            request_context["model_stream"] = payload.get("stream", "-")

        # Handle llama.cpp compatibility mode
        if LLAMA_CPP_MODE:
            if ollama_path == "/api/tags":
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"models":[{"name": "local-llama-model"}]}).encode())
                return
            if ollama_path == "/api/chat":
                ollama_req = payload
                openai_req = {
                    "messages": ollama_req.get("messages", []),
                    "stream": True,
                    "temperature": ollama_req.get("options", {}).get("temperature", 0.7)
                }
                body = json.dumps(openai_req).encode()
                # When in llama-cpp mode, we hit the v1/chat/completions endpoint
                # We'll update the target_url in the loop below

        # Use different timeouts for streaming vs non-streaming
        is_streaming = ("/api/chat" in ollama_path or "/api/generate" in ollama_path)
        timeout = 600 if is_streaming else 30

        last_error = None
        retry_count = 0
        for base_url in hosts_to_try:
            target_url = base_url + ( "/v1/chat/completions" if LLAMA_CPP_MODE and ollama_path == "/api/chat" else ollama_path )

            try:
                req = urllib.request.Request(
                    target_url,
                    data=body,
                    method=method,
                    headers={"Content-Type": self.headers.get("Content-Type", "application/json")}
                )
                if "Authorization" in self.headers:
                    req.add_header("Authorization", self.headers.get("Authorization"))

                # Explicitly disable proxy for this request
                proxy_handler = urllib.request.ProxyHandler({})
                opener = urllib.request.build_opener(proxy_handler)

                response = opener.open(req, timeout=timeout)

                # Success! Forward the response
                self.send_response(response.status)
                is_stream = ("/api/chat" in ollama_path or "/api/generate" in ollama_path)

                for header, value in response.getheaders():
                    if header.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(header, value)

                self._cors_headers()
                self.end_headers()

                # Stream chunks
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break

                    if LLAMA_CPP_MODE and is_stream and ollama_path == "/api/chat":
                        # Translate llama.cpp SSE to Ollama JSONL (simplified)
                        text = chunk.decode(errors="ignore")
                        for line in text.split("\n"):
                            if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                                try:
                                    j = json.loads(line[6:])
                                    if "choices" in j and len(j["choices"]) > 0:
                                        out = {
                                            "message": {"role": "assistant", "content": j["choices"][0].get("delta", {}).get("content", "")},
                                            "done": False
                                        }
                                        self.wfile.write((json.dumps(out) + "\n").encode())
                                        self.wfile.flush()
                                except: pass
                    else:
                        self.wfile.write(chunk)
                        if is_stream: self.wfile.flush()
                return # Done with successful proxy

            except urllib.error.URLError as e:
                retry_count += 1
                last_error = e
                _log_event(
                    logging.WARNING,
                    f"Ollama proxy attempt {retry_count}/{len(hosts_to_try)} failed: {getattr(e, 'reason', e)}",
                    request_context=request_context
                )
                if retry_count < len(hosts_to_try):
                    time.sleep(0.5)
                continue
            except Exception as e:
                last_error = e
                break

        # If we reach here, all hosts failed
        _log_event(logging.ERROR, f"Ollama proxy failed: {str(last_error)}", request_context=request_context)
        self.send_response(502)
        self._cors_headers()
        self.end_headers()
        err_msg = str(getattr(last_error, 'reason', last_error))
        msg = json.dumps({"error": f"Connection failed: {err_msg}. Ensure AI engine is running."})
        self.wfile.write(msg.encode())

    # ── Stable Diffusion Proxy ────────────────────────────────────
    def _proxy_sd(self, method):
        """
        Proxy requests from /sd/* to the local Stable Diffusion engine.
        """
        request_context = self._build_request_context("/sd")
        sd_path = self.path[len("/sd"):]
        target_url = "http://127.0.0.1:8080" + sd_path

        body = None
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            body = self.rfile.read(content_length)

        try:
            req = urllib.request.Request(
                target_url,
                data=body,
                method=method,
                headers={"Content-Type": self.headers.get("Content-Type", "application/json")}
            )

            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            response = opener.open(req, timeout=300)

            self.send_response(response.status)
            for header, value in response.getheaders():
                if header.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(header, value)
            self._cors_headers()
            self.end_headers()
            
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                
        except urllib.error.URLError as e:
            _log_event(logging.ERROR, f"SD proxy failed: {e}", request_context=request_context)
            self.send_response(502)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Cannot reach SD engine: {e.reason}"}).encode())
        except Exception as e:
            _log_event(logging.ERROR, "Unhandled SD proxy error", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())


class ThreadedHTTPServer(http.server.HTTPServer):
    """Handle each request in a new thread for concurrent streaming."""
    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._handle, args=(request, client_address))
        thread.daemon = True
        thread.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

HOST_HARDWARE_SPECS = _get_hardware_specs()
LOGGER, LOG_LISTENER = configure_logging()

def cleanup_on_exit():
    """Clean up resources on exit."""
    try:
        _log_event(logging.INFO, "Shutting down chat server...")
        _kill_ollama()
        # Remove temp files
        for root, dirs, files in os.walk(CHATS_DIR):
            for f in files:
                if f.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass
    except Exception as e:
        _log_event(logging.ERROR, f"Error during cleanup: {e}")

atexit.register(cleanup_on_exit)

def open_browser_delayed():
    """Open the browser after a short delay to ensure server is ready."""
    time.sleep(1.0)
    webbrowser.open(f"http://localhost:{CHAT_SERVER_PORT}")

def main():
    ensure_data_dir()
    _set_active_log_mode(_load_settings_file().get("logMode"))

    # Try to find the local LAN IP
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print()
    print("=" * 55)
    print("  Portable AI — Chat Server")
    print("=" * 55)
    print()
    print(f"  Local Access:    http://localhost:{CHAT_SERVER_PORT}")
    print(f"  Network Access:  http://{local_ip}:{CHAT_SERVER_PORT}   <-- Use this on phone/other PC!")
    print(f"  Ollama/Llama Proxy: {OLLAMA_HOST}")
    if LLAMA_CPP_MODE:
        print("  Running in LLAMA_CPP_MODE (Translating API requests)")
    if API_TOKEN:
        print("  API Token:       Enabled (PORTABLE_AI_TOKEN is set)")
    print()
    print("  All chats auto-save to the USB drive!")
    print("  Press Ctrl+C to shut down.")
    print()
    print("-" * 55)

    server = ThreadedHTTPServer(("0.0.0.0", CHAT_SERVER_PORT), ChatHandler)

    # Open browser in background thread
    if "--no-browser" not in sys.argv:
        threading.Thread(target=open_browser_delayed, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down chat server...")
    finally:
        server.shutdown()
        print("  Goodbye!")
        try:
            LOG_LISTENER.stop()
        except Exception:
            pass

if __name__ == "__main__":
    main()
