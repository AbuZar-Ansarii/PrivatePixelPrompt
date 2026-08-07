<div align="center"> 
 
 # .✦ ݁˖ PortableLM (PrivatePixelPrompt) .✦ ݁˖

</div>

**PortableLM** is a fully air-gapped, zero-dependency, plug-and-play local AI environment designed to run seamlessly from your **local hard drive** or a **portable USB/SSD**. It bypasses complex installations — natively executing large language models, image generation, and high-quality text-to-speech directly on your hardware with no internet required.

With a unified architecture, you can initialize your AI models once and carry them with you across **Windows, macOS, Linux, and Android (Termux)**.

---

## Core Features

*   **Multi-Modal AI Hub:** A single interface for **Text Chat (LLM)**, **Image Generation (Stable Diffusion)**, and **Text-to-Speech (Piper TTS)**.
*   **Persistent Image Library:** Generated images are automatically saved to `Shared/chat_data/generated_images/` with sidecar metadata. Browse, view full-screen, reuse prompt parameters, download, or delete images directly from the built-in Image Library gallery.
*   **Aspect Ratio & Live Step Progress:** Image generator includes Aspect Ratio selectors (**16:9 Landscape**, **9:16 Portrait**, **1:1 Square**, **Custom**) and a real-time progress bar showing active step counts (`Step 1 / 20` ... `20 / 20`) and ETA calculations.
*   **Persistent Audio Library:** Generated TTS speech files are saved to `Shared/chat_data/tts_output/` with sidecar metadata. Listen with the inline player, reuse text, download, or delete clips from the Audio Library gallery.
*   **Lightweight Model Options:** Includes ultra-fast local LLM models like **Liquid AI LFM2.5 230M** (`LFM2.5-230M.Q4_K_M.gguf`), Gemma 2, Llama 3, and Qwen.
*   **Zero Dependency Setup:** Ships with portable Python and isolated engine binaries. No system permissions, registry edits, or package managers required.
*   **Cross-Platform:** Uses an intelligent `Shared` volume system — download your AI models *once*, and use them natively on **Windows, macOS, Linux, and Android (Termux)**.
*   **Fully Offline:** Runs completely air-gapped after initial setup. Your data never leaves your machine.
*   **Network Proxied UI:** The custom Python HTTP server serves a blazing-fast dark mode UI. Access the AI from your phone or tablet on the same WiFi — no CORS headaches.

---

## Feature Modules

### 💬 Local Chat (LLM)
Powered by **Ollama**, run world-class models like Liquid AI LFM2.5 230M, Gemma 2, Llama 3, and Qwen entirely locally. Full support for custom `.gguf` models and advanced system instructions.

### 🎨 Image Generation
Powered by **Stable Diffusion** (`stable-diffusion.cpp`), generate high-quality images using CyberRealistic v3.3. Supports aspect ratio presets (16:9, 9:16, 1:1, Custom), sampler selection, seeds, and real-time step progress tracking.

### 🎙️ Text-to-Speech (TTS)
Powered by **Piper**, transform text into natural-sounding speech instantly. Includes 5+ high-quality female and male voices (Amy, Lili, Kusal, Arctic, Lessac, Alan) with persistent sidecar storage and full Audio Library management.

---

## System Requirements

-   **Storage:** USB 3.0+ flash drive or SSD with at least **12 GB** free (for Chat + Image + TTS models).
-   **RAM:** At least **4 GB - 8 GB** for base models (e.g. Liquid AI LFM2.5 230M), **16 GB** recommended for multi-modal workloads.
-   **OS:** Windows 10/11, macOS (Intel/Silicon), modern Linux distributions, or Android (Termux).

---

## Folder Architecture

```text
[PortableLM Drive]
 ├── 📁 Android    # Native Termux / Android installers & scripts
 ├── 📁 Linux      # Native Linux (Ubuntu/Debian) launchers & installers
 ├── 📁 Mac        # Native macOS (Intel/Silicon) launchers & installers
 ├── 📁 Windows    # Native Windows launchers & installers
 └── 📁 Shared     # Unified Cross-Platform Data System
      ├── 📁 bin         (Isolated engine binaries: Ollama, Stable Diffusion, Piper)
      ├── 📁 chat_data   (Persistent chat history, generated_images, and tts_output)
      ├── 📁 models      (LLM weights, SD checkpoints, and Piper voice models)
      └── 📁 vendor      (Local UI assets: JS/CSS/Fonts for 100% offline usage)
```

---

## Quick Start

### Step 1: Initialize & Download
Run the install script for your platform. This will prompt you to select your preferred LLM model (including Liquid AI LFM2.5 230M) and download execution engines.

| Platform | Command |
|---|---|
| **Windows** | Double-click `Windows/install.bat` |
| **macOS** | Open Terminal -> drag `Mac/install.command` -> Enter |
| **Linux** | `bash Linux/install.sh` |
| **Android** | `bash Android/install.sh` |

### Step 2: Launch
| Platform | Command |
|---|---|
| **Windows** | `Windows/start-fast-chat.bat` |
| **macOS** | `Mac/start.command` |
| **Linux** | `bash Linux/start.sh` |
| **Android** | `bash Android/start.sh` |

The server will start, and your browser will open to `http://localhost:3333`.

---

## LAN Mobile Access

Use your PC's AI from your phone or tablet on the same network:

1. Ensure the app is running on your PC.
2. The terminal will show a **Network Access** IP (e.g., `http://192.168.1.15:3333`).
3. Open that URL on your mobile browser.
4. Generate text, images, or speech directly from your mobile device!

---

## Privacy Policy

PortableLM runs 100% offline after initial setup. No data—including chat history, generated images, or voice outputs—ever leaves your machine. No telemetry, no analytics, no cloud dependencies, no hidden phone-home requests.

What we do **NOT** collect:
- Personal information
- Usage statistics
- AI prompts or responses
- Generated content
- Hardware or system data

By using PortableLM, you retain full ownership of all content you generate.

---

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

Copyright (c) 2026 **Mohd Abuzar**

---

> *PortableLM — Your Personal, Portable AI Command Center.*
