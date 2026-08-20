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

# Disable system proxies for internal local connections to avoid 502 errors
os.environ["no_proxy"] = "127.0.0.1,localhost,0.0.0.0"
os.environ["NO_PROXY"] = "127.0.0.1,localhost,0.0.0.0"
from datetime import datetime
from urllib.parse import urlparse, parse_qs, quote, unquote

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
CHAT_SERVER_PORT = 3333
OLLAMA_HOST = "http://127.0.0.1:11435"
LLAMA_CPP_MODE = "--llama-cpp" in sys.argv
if LLAMA_CPP_MODE:
    OLLAMA_HOST = "http://127.0.0.1:8080"

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
LOG_MODE_LOCK = threading.RLock()
ACTIVE_LOG_MODE = DEFAULT_LOG_MODE

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

# ── GPU Discovery & Hardware Acceleration ─────────────────────
def _detect_gpus():
    """Detect available GPUs (AMD Radeon, NVIDIA CUDA, Intel) across platforms."""
    gpus = {"nvidia": False, "amd": False, "intel": False, "names": []}
    try:
        plat = platform.system()
        if plat == "Windows":
            cmd = ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                names = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                gpus["names"] = names
                for name in names:
                    n_upper = name.upper()
                    if "NVIDIA" in n_upper or "GEFORCE" in n_upper or "QUADRO" in n_upper:
                        gpus["nvidia"] = True
                    if "AMD" in n_upper or "RADEON" in n_upper or "ADVANCED MICRO DEVICES" in n_upper:
                        gpus["amd"] = True
                    if "INTEL" in n_upper or "ARC" in n_upper:
                        gpus["intel"] = True
        elif plat == "Linux":
            res = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                out = res.stdout.upper()
                if "NVIDIA" in out:
                    gpus["nvidia"] = True
                if "AMD" in out or "RADEON" in out:
                    gpus["amd"] = True
                if "INTEL" in out:
                    gpus["intel"] = True
        elif plat == "Darwin":
            gpus["apple_metal"] = True
    except Exception as e:
        print(f"[GPU DETECT] Warning during GPU discovery: {e}")
    return gpus

SYSTEM_GPUS = _detect_gpus()
if SYSTEM_GPUS.get("names"):
    print(f"[GPU DETECT] Available GPUs: {', '.join(SYSTEM_GPUS['names'])}")


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

        cpu = round(min(100.0, max(0.0, (1.0 - (d_idle) / max(d_total, 1)) * 100.0)), 1)
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
        cpu = 0.0
        ram = 0.0
        return cpu, ram

_last_gpu_util = (0.0, 0.0)

def _get_gpu_utilization():
    """Return GPU % utilization using Windows Performance Counters / stdlib tools."""
    global _last_gpu_util
    now = time.time()
    if now - _last_gpu_util[0] < 2.0:
        return _last_gpu_util[1]

    val = 0.0
    try:
        plat = platform.system()
        if plat == "Windows":
            cmd = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | Where-Object Name -like '*engtype_3D*' | Measure-Object -Property UtilizationPercentage -Sum).Sum"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                val = round(min(100.0, max(0.0, float(res.stdout.strip()))), 1)
    except Exception:
        pass
    _last_gpu_util = (now, val)
    return val


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
    with DATA_FILE_LOCK:
        temp_file = SETTINGS_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush()
        os.replace(temp_file, SETTINGS_FILE)

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
    """Start Ollama in the background if binary exists, configuring GPU acceleration."""
    if not OLLAMA_BIN or not os.path.isfile(OLLAMA_BIN):
        return False
    try:
        env = os.environ.copy()
        runtime_dir = os.path.join(SCRIPT_DIR, ".ollama-runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        os.makedirs(os.path.join(runtime_dir, "tmp"), exist_ok=True)
        env["HOME"] = runtime_dir
        env["OLLAMA_HOME"] = runtime_dir
        env["OLLAMA_MODELS"] = os.path.join(SCRIPT_DIR, "models", "ollama_data")
        env["OLLAMA_TMPDIR"] = os.path.join(runtime_dir, "tmp")
        env["OLLAMA_ORIGINS"] = "*"
        env["OLLAMA_HOST"] = "127.0.0.1:11435"

        lib_dir = os.path.join(SCRIPT_DIR, "bin", "lib", "ollama")
        if os.path.isdir(lib_dir):
            env["OLLAMA_LIBRARY_PATH"] = lib_dir
        if os.path.isdir(os.path.join(lib_dir, "runners")):
            env["OLLAMA_RUNNERS_DIR"] = os.path.join(lib_dir, "runners")
        else:
            env.pop("OLLAMA_RUNNERS_DIR", None)

        plat = platform.system()
        # Enable GPU acceleration
        if plat == "Darwin":
            # On macOS (Apple Silicon Metal & Intel), Ollama handles Metal natively.
            # Do NOT set Vulkan/ROCm variables on Darwin.
            pass
        else:
            env["OLLAMA_VULKAN"] = "1"
            env["OLLAMA_IGPU_ENABLE"] = "1"
            if SYSTEM_GPUS.get("amd"):
                env["HSA_OVERRIDE_GFX_VERSION"] = "10.3.0"
                env["HIP_VISIBLE_DEVICES"] = "0"
                env["GGML_VK_VISIBLE_DEVICES"] = "0"
                print("[OLLAMA] Configured AMD Radeon GPU acceleration (Vulkan / ROCm).")
            elif SYSTEM_GPUS.get("nvidia"):
                env["CUDA_VISIBLE_DEVICES"] = "0"
                print("[OLLAMA] Configured NVIDIA GPU acceleration (CUDA).")
            elif SYSTEM_GPUS.get("intel"):
                env["GGML_VK_VISIBLE_DEVICES"] = "0"
                print("[OLLAMA] Configured Intel GPU acceleration (Vulkan).")

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
    """Read sd stdout+stderr in real-time byte-by-byte.
    stable-diffusion.cpp prints sampling progress using: |=====> | 1/20 - 2.50s/it \r"""
    ratio_pattern = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
    loading_pattern = re.compile(r"loading|load|model\.cpp|safetensors", re.IGNORECASE)

    last_step = 0
    step_start_time = None
    step_times = []
    buffer = ""
    target_substr = f"/{total_steps}"

    try:
        while True:
            chunk = pipe.read(1)
            if not chunk:
                break
            
            ch_str = chunk.decode("utf-8", errors="ignore")
            buffer += ch_str

            is_line_end = (ch_str == '\r' or ch_str == '\n')
            has_target = (target_substr in buffer)

            if is_line_end or has_target:
                if last_step == 0 and loading_pattern.search(buffer):
                    job = _get_image_job(job_id)
                    elapsed = time.time() - job.get("started_at", time.time())
                    _update_image_job(
                        job_id,
                        status="loading_model",
                        elapsed_ms=int(elapsed * 1000),
                    )

                for m in ratio_pattern.finditer(buffer):
                    try:
                        s_val = int(m.group(1))
                        t_val = int(m.group(2))
                        if 0 < s_val <= t_val and t_val == total_steps and s_val > last_step:
                            now = time.time()
                            if step_start_time is not None:
                                for _ in range(s_val - last_step):
                                    step_times.append(now - step_start_time)
                                    if len(step_times) > 10:
                                        step_times.pop(0)
                            step_start_time = now
                            last_step = s_val

                            job = _get_image_job(job_id)
                            elapsed = now - job.get("started_at", now)
                            eta_ms = None
                            if s_val < total_steps and step_times:
                                avg_step = sum(step_times) / len(step_times)
                                eta_ms = int(avg_step * (total_steps - s_val) * 1000)

                            _update_image_job(
                                job_id,
                                status="generating",
                                step=s_val,
                                total_steps=total_steps,
                                elapsed_ms=int(elapsed * 1000),
                                eta_ms=eta_ms,
                            )
                            break
                    except Exception:
                        pass

                if is_line_end:
                    buffer = ""
                elif len(buffer) > 256:
                    buffer = buffer[-128:]
    except Exception as e:
        print(f"[SD] Progress parser info: {e}")
    finally:
        try:
            pipe.close()
        except Exception:
            pass

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

    env = os.environ.copy()
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
        "--offload-to-cpu",
        "--max-vram", "1.5",
    ]
    if negative:
        cmd += ["-n", negative]
    if seed != -1:
        cmd += ["--seed", str(seed)]

    if SYSTEM_GPUS.get("amd"):
        cmd += ["--backend", "vulkan", "--vae-on-cpu"]
        env["HSA_OVERRIDE_GFX_VERSION"] = "10.3.0"
        env["HIP_VISIBLE_DEVICES"] = "0"
        env["GGML_VK_VISIBLE_DEVICES"] = "0"
        env["OLLAMA_VULKAN"] = "1"
        gpu_label = "AMD Radeon (Vulkan/ROCm enabled, VRAM offloading active)"
    elif SYSTEM_GPUS.get("nvidia"):
        cmd += ["--backend", "cuda"]
        env["CUDA_VISIBLE_DEVICES"] = "0"
        gpu_label = "NVIDIA GPU (CUDA enabled)"
    elif SYSTEM_GPUS.get("intel"):
        cmd += ["--backend", "vulkan"]
        env["GGML_VK_VISIBLE_DEVICES"] = "0"
        gpu_label = "Intel GPU (Vulkan enabled)"
    else:
        gpu_label = "CPU Mode"

    print(f"\n[SD] GENERATING IMAGE...")
    print(f"     Prompt: {prompt}")
    print(f"     Engine: {SD_BINARY}")
    print(f"     Model:  {SD_MODEL}")
    print(f"     Threads: {os.cpu_count() or 4}")
    print(f"     GPU Accel: {gpu_label}")
    
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

        # Start process with unbuffered output (bufsize=0)
        proc = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            env=env,
            bufsize=0
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

        # Save metadata sidecar in chat_data/generated_images/
        meta_path = output_path + ".json"
        filename = os.path.basename(output_path)
        try:
            meta_data = {
                "id": os.path.splitext(filename)[0],
                "filename": filename,
                "url": f"/chat_data/generated_images/{quote(filename)}",
                "prompt": prompt,
                "negative_prompt": negative,
                "steps": steps,
                "cfg_scale": cfg,
                "width": width,
                "height": height,
                "seed": seed,
                "sampling_method": sampling,
                "created_at": datetime.now().isoformat(),
                "timestamp": time.time(),
            }
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(meta_data, mf, indent=2)
        except Exception as e:
            print(f"[SD] WARN: Could not write metadata sidecar: {e}")

        _update_image_job(
            job_id,
            status="done",
            image_b64=img_b64,
            image_url=f"/chat_data/generated_images/{quote(filename)}",
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
    logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.propagate = False

    log_queue = queue.Queue(maxsize=10000)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    logger.addHandler(queue_handler)

    file_handler = ImmediateFlushRotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=1, encoding="utf-8"
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
    def do_GET(self):
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

        # Generated images library API
        elif path == "/api/generated-images":
            self._get_generated_images()

        # Generated TTS audio library API
        elif path == "/api/generated-tts":
            self._get_generated_tts()

        # Proxy Ollama API
        elif path.startswith("/ollama/"):
            self._proxy_ollama("GET")

        else:
            # Try serving static files from SCRIPT_DIR
            self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/chats":
            self._save_chats()

        elif path == "/api/settings":
            self._save_settings()

        # Image generation API
        elif path == "/api/generate-image":
            self._generate_image()

        # Delete generated image API
        elif path == "/api/delete-generated-image":
            self._delete_generated_image()

        # TTS generation API
        elif path == "/api/generate-tts":
            self._generate_tts()

        # Delete generated TTS audio API
        elif path == "/api/delete-generated-tts":
            self._delete_generated_tts()

        # Engine control APIs
        elif path == "/api/stop-ollama":
            self._stop_ollama_endpoint()

        elif path == "/api/start-ollama":
            self._start_ollama_endpoint()

        # Proxy Ollama API
        elif path.startswith("/ollama/"):
            self._proxy_ollama("POST")

        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/ollama/"):
            self._proxy_ollama("DELETE")
        else:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()

    # ── Serve HTML ─────────────────────────────────────────────
    def _serve_html(self):
        try:
            with open(HTML_FILE, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"FastChatUI.html not found.")

    def _serve_static(self, path):
        """Serve static files (CSS, JS, images) from SCRIPT_DIR."""
        raw_path = unquote(path)
        safe_path = os.path.normpath(raw_path.lstrip("/"))
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
                ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
                ".ico": "image/x-icon", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg"
            }
            content_type = mime_types.get(ext, "application/octet-stream")
            with open(full_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
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
            with DATA_FILE_LOCK:
                temp_file = CHATS_FILE + ".tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(chats, f, ensure_ascii=False, indent=2)
                    f.flush()
                os.replace(temp_file, CHATS_FILE)
            _log_event(logging.INFO, "Chats saved successfully", request_context=request_context)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        except Exception as e:
            _log_event(logging.ERROR, "Failed to save chats", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

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

            settings = _load_settings_file()
            settings.update(incoming)
            settings["logMode"] = _normalize_log_mode(settings.get("logMode"))
            _persist_settings_file(settings)
            _set_active_log_mode(settings["logMode"])
            _log_event(logging.INFO, "Settings saved successfully", request_context=request_context)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "logMode": settings["logMode"]}).encode())
        except Exception as e:
            _log_event(logging.ERROR, "Failed to save settings", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    # ── Hardware Stats ─────────────────────────────────────────
    def _get_stats(self):
        """Return CPU %, RAM %, GPU %, and GPU hardware details as JSON."""
        try:
            cpu, ram = _get_hw_stats()
            gpu_pct = _get_gpu_utilization()
            gpu_accel = "CPU"
            if SYSTEM_GPUS.get("amd"):
                gpu_accel = "AMD Radeon (Vulkan/ROCm)"
            elif SYSTEM_GPUS.get("nvidia"):
                gpu_accel = "NVIDIA (CUDA)"
            elif SYSTEM_GPUS.get("intel"):
                gpu_accel = "Intel (Vulkan)"

            data = json.dumps({
                "cpu_percent": cpu,
                "ram_percent": ram,
                "gpu_percent": gpu_pct,
                "has_psutil": HAS_PSUTIL,
                "gpus": SYSTEM_GPUS.get("names", []),
                "gpu_accel": gpu_accel
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(data.encode())
        except Exception as e:
            _log_event(
                logging.ERROR,
                "Failed to collect hardware stats",
                request_context=self._build_request_context("/api/stats"),
                exc_info=True,
            )
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

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
        data = json.dumps({"models": models, "sd_enabled": sd_ready})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode())

    def _get_engine_status(self):
        """Return which engines are currently running."""
        try:
            ollama_up = _is_ollama_running()
            sd_ready = _is_sd_enabled()
            
            # Check if SD binary and model exist
            sd_binary_exists = bool(SD_BINARY and os.path.isfile(SD_BINARY))
            sd_model_exists = bool(SD_MODEL and os.path.isfile(SD_MODEL))
            
            data = json.dumps({
                "ollama": ollama_up,
                "sd_enabled": sd_ready,
                "sd_binary": sd_binary_exists,
                "sd_model": sd_model_exists,
                "sd_healthy": sd_binary_exists,
                "tts_enabled": bool(TTS_ENABLED),
                "tts_binary": bool(TTS_BINARY and os.path.isfile(TTS_BINARY)),
                "local_ip": LOCAL_IP,
                "port": CHAT_SERVER_PORT,
            })
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(data.encode())
        except Exception as e:
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _stop_ollama_endpoint(self):
        """Stop the Ollama chat engine to free RAM for image generation."""
        request_context = self._build_request_context("/api/stop-ollama")
        try:
            _kill_ollama()
            _log_event(logging.INFO, "Ollama engine stopped by user", request_context=request_context)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "message": "Chat engine stopped."}).encode())
        except Exception as e:
            _log_event(logging.ERROR, "Failed to stop Ollama", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _start_ollama_endpoint(self):
        """Restart the Ollama chat engine."""
        request_context = self._build_request_context("/api/start-ollama")
        try:
            if _is_ollama_running():
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "message": "Chat engine already running."}).encode())
                return
            ok = _start_ollama()
            if ok:
                _log_event(logging.INFO, "Ollama engine started by user", request_context=request_context)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "message": "Chat engine started."}).encode())
            else:
                raise RuntimeError("Ollama failed to start. Check that the engine is installed.")
        except Exception as e:
            _log_event(logging.ERROR, "Failed to start Ollama", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

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
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Missing job_id parameter."}).encode())
            return

        job = _get_image_job(job_id)
        if not job:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Job not found."}).encode())
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

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def _get_generated_images(self):
        """Return list of saved generated images with metadata from chat_data/generated_images/."""
        images_dir = os.path.join(SCRIPT_DIR, "chat_data", "generated_images")
        os.makedirs(images_dir, exist_ok=True)
        items = []
        valid_exts = {".png", ".jpg", ".jpeg", ".webp"}
        try:
            for fname in os.listdir(images_dir):
                ext = os.path.splitext(fname)[1].lower()
                if ext in valid_exts:
                    img_path = os.path.join(images_dir, fname)
                    meta_path = img_path + ".json"
                    meta = None
                    if os.path.isfile(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as mf:
                                meta = json.load(mf)
                        except Exception:
                            meta = None
                    if not meta:
                        mtime = os.path.getmtime(img_path)
                        meta = {
                            "id": os.path.splitext(fname)[0],
                            "filename": fname,
                            "url": f"/chat_data/generated_images/{quote(fname)}",
                            "prompt": os.path.splitext(fname)[0],
                            "timestamp": mtime,
                            "created_at": datetime.fromtimestamp(mtime).isoformat(),
                        }
                    else:
                        meta["url"] = f"/chat_data/generated_images/{quote(fname)}"
                    items.append(meta)
            items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        except Exception as e:
            items = []
        data = json.dumps({"images": items})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode())

    def _delete_generated_image(self):
        """Delete a generated image and its metadata sidecar from chat_data/generated_images/."""
        body = self._read_body()
        try:
            payload = json.loads(body) if body else {}
            raw_fname = payload.get("filename", "")
            filename = unquote(os.path.basename(raw_fname))
            if filename:
                images_dir = os.path.join(SCRIPT_DIR, "chat_data", "generated_images")
                img_path = os.path.join(images_dir, filename)
                meta_path = img_path + ".json"
                if os.path.isfile(img_path):
                    os.remove(img_path)
                if os.path.isfile(meta_path):
                    os.remove(meta_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        except Exception as e:
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    # ── Text to Speech API ─────────────────────────────────────
    def _get_tts_voices(self):
        """Return the available TTS voice models."""
        voices = []
        if os.path.isdir(TTS_MODELS_DIR):
            for f in os.listdir(TTS_MODELS_DIR):
                if f.endswith(".onnx"):
                    name = f.replace(".onnx", "")
                    voices.append({"id": f, "name": name})
        data = json.dumps({"voices": voices, "tts_enabled": TTS_ENABLED})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data.encode())

    def _get_generated_tts(self):
        """Scan chat_data/tts_output directory and return list of generated audio metadata."""
        audio_dir = os.path.join(SCRIPT_DIR, "chat_data", "tts_output")
        results = []
        if os.path.isdir(audio_dir):
            valid_exts = (".wav", ".mp3", ".ogg")
            for filename in os.listdir(audio_dir):
                if filename.lower().endswith(valid_exts):
                    file_path = os.path.join(audio_dir, filename)
                    meta_path = file_path + ".json"
                    stat_info = os.stat(file_path)
                    
                    item = {
                        "id": os.path.splitext(filename)[0],
                        "filename": filename,
                        "url": f"/chat_data/tts_output/{quote(filename)}",
                        "size": stat_info.st_size,
                        "timestamp": stat_info.st_mtime,
                        "created_at": datetime.fromtimestamp(stat_info.st_mtime).isoformat(),
                        "text": "",
                        "voice": "Piper TTS"
                    }
                    
                    if os.path.isfile(meta_path):
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                                item.update(meta)
                        except Exception:
                            pass
                            
                    results.append(item)
                    
        results.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps({"audio_files": results}).encode())

    def _delete_generated_tts(self):
        """Delete a generated audio file and its metadata sidecar."""
        body = self._read_body()
        try:
            payload = json.loads(body) if body else {}
            raw_fname = payload.get("filename", "")
            filename = unquote(os.path.basename(raw_fname))
            if filename:
                audio_dir = os.path.join(SCRIPT_DIR, "chat_data", "tts_output")
                audio_path = os.path.join(audio_dir, filename)
                meta_path = audio_path + ".json"
                if os.path.isfile(audio_path):
                    os.remove(audio_path)
                if os.path.isfile(meta_path):
                    os.remove(meta_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        except Exception as e:
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

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

        # Output to persistent tts_output directory
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

            # Save metadata sidecar
            clean_voice = voice_id.replace(".onnx", "")
            meta_path = output_path + ".json"
            meta_data = {
                "id": output_id,
                "filename": f"{output_id}.wav",
                "url": f"/chat_data/tts_output/{quote(output_id)}.wav",
                "text": text,
                "voice": clean_voice,
                "voice_id": voice_id,
                "created_at": datetime.now().isoformat(),
                "timestamp": time.time()
            }
            try:
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump(meta_data, mf, indent=2)
            except Exception:
                pass

            with open(output_path, "rb") as f:
                audio_data = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Disposition", 'attachment; filename="speech.wav"')
            self._cors_headers()
            self.end_headers()
            self.wfile.write(audio_data)

            _log_event(logging.INFO, "TTS generated successfully", request_context=request_context)

        except Exception as e:
            _log_event(logging.ERROR, "TTS generation failed", request_context=request_context, exc_info=True)
            self.send_response(500)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

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

        last_error = None
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
                
                response = opener.open(req, timeout=600)

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

            except urllib.error.HTTPError as e:
                # Forward Ollama's HTTP error directly (e.g. 400, 404, 500)
                _log_event(logging.WARNING, f"Ollama returned HTTP {e.code}: {e.reason}", request_context=request_context)
                self.send_response(e.code)
                for header, value in e.headers.items():
                    if header.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(header, value)
                self._cors_headers()
                self.end_headers()
                self.wfile.write(e.read())
                return
            except urllib.error.URLError as e:
                last_error = e
                continue # Try next host
            except Exception as e:
                last_error = e
                break

        # If connection failed, check if we can auto-start Ollama and retry once
        if not _is_ollama_running() and OLLAMA_BIN and os.path.isfile(OLLAMA_BIN):
            _log_event(logging.INFO, "Ollama not running; auto-starting...", request_context=request_context)
            if _start_ollama():
                try:
                    retry_url = OLLAMA_HOST + ( "/v1/chat/completions" if LLAMA_CPP_MODE and ollama_path == "/api/chat" else ollama_path )
                    req = urllib.request.Request(
                        retry_url,
                        data=body,
                        method=method,
                        headers={"Content-Type": self.headers.get("Content-Type", "application/json")}
                    )
                    if "Authorization" in self.headers:
                        req.add_header("Authorization", self.headers.get("Authorization"))
                    proxy_handler = urllib.request.ProxyHandler({})
                    opener = urllib.request.build_opener(proxy_handler)
                    response = opener.open(req, timeout=600)
                    self.send_response(response.status)
                    is_stream = ("/api/chat" in ollama_path or "/api/generate" in ollama_path)
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
                        if is_stream: self.wfile.flush()
                    return
                except urllib.error.HTTPError as e:
                    self.send_response(e.code)
                    for header, value in e.headers.items():
                        if header.lower() not in ("transfer-encoding", "connection", "content-length"):
                            self.send_header(header, value)
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(e.read())
                    return
                except Exception as retry_e:
                    last_error = retry_e

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
        import socket
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
        server.shutdown()
        print("  Goodbye!")
    finally:
        try:
            LOG_LISTENER.stop()
        except Exception:
            pass

if __name__ == "__main__":
    main()
