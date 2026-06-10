<div align="center"> # .✦ ݁˖ PortableLM (PrivatePixelPrompt).✦ ݁˖

</div>

**PortableLM** is a fully air-gapped, zero-dependency, plug-and-play local AI environment designed to run seamlessly from your **local hard drive** or a **portable USB/SSD**. It bypasses complex installations — natively executing large language models, image generation, and high-quality text-to-speech directly on your hardware with no internet required.

With a unified architecture, you can initialize your AI models once and carry them with you across **Windows, macOS, and Linux**.

---

## Core Features

*   **Multi-Modal AI Hub:** A single interface for **Text Chat**, **Image Generation**, and **Text-to-Speech**.
*   **Zero Dependency Setup:** Ships with portable Python and isolated engine binaries. No system permissions, registry edits, or package managers required.
*   **Cross-Platform:** Uses an intelligent `Shared` volume system — download your AI models *once*, and use them natively on Windows, macOS, and Linux without duplication.
*   **Fully Offline:** Runs completely air-gapped after initial setup. Your data never leaves your machine.
*   **Network Proxied UI:** The custom Python HTTP server serves a blazing-fast dark mode UI. Access the AI from your phone or tablet on the same WiFi — no CORS headaches.
*   **Hardware Accelerated:** Natively capitalizes on AVX CPU instructions, NVIDIA CUDA, or Apple Metal GPU accelerators dynamically when plugged into different host machines.

---

## Feature Modules

### 💬 Local Chat (LLM)
Powered by **Ollama**, run world-class models like Gemma 2, Llama 3, and Qwen entirely locally. Support for custom `.gguf` models and advanced system instructions.

### 🎨 Image Generation
Powered by **Stable Diffusion**, generate high-quality, uncensored images using the included CyberRealistic model. Optimized for CPU and GPU execution.

### 🎙️ Text-to-Speech (TTS)
Powered by **Piper**, transform text into natural-sounding speech instantly. Includes 5+ high-quality female and male voices (Amy, Lili, Kusal, Arctic, Lessac, Alan) that work entirely offline.

---

## System Requirements

-   **Storage:** USB 3.0+ flash drive or SSD with at least **12 GB** free (for Chat + Image + TTS models).
-   **RAM:** At least **8 GB** for base models, **16 GB** recommended for smoother multi-modal performance.
-   **OS:** Windows 10/11, macOS (Intel/Silicon), or modern Linux distributions.

---

## Folder Architecture

```text
[PortableLM Drive]
 ├── 📁 Linux      # Native Linux (Ubuntu/Debian) launchers
 ├── 📁 Mac        # Native macOS (Intel/Silicon) launchers
 ├── 📁 Windows    # Native Windows installers & launchers
 └── 📁 Shared     # Unified Cross-Platform Data System
      ├── 📁 bin         (Isolated engine binaries: Ollama, Stable Diffusion, Piper)
      ├── 📁 chat_data   (Persistent chat history, generated images, and TTS output)
      ├── 📁 models      (LLM weights, SD checkpoints, and Piper voice models)
      └── 📁 vendor      (Local UI assets: JS/CSS/Fonts for 100% offline usage)
```

---

## Quick Start

### Step 1: Initialize & Download
Run the install script for your OS. This will download the execution engines and your selected models.

| OS | Command |
|---|---|
| **Windows** | Double-click `Windows/install.bat` |
| **macOS** | Open Terminal -> drag `Mac/install.command` -> Enter |
| **Linux** | `bash Linux/install.sh` |

### Step 2: Launch
| OS | Command |
|---|---|
| **Windows** | `Windows/start-fast-chat.bat` |
| **macOS** | `Mac/start.command` |
| **Linux** | `bash Linux/start.sh` |

The server will start, and your default browser will open to `http://localhost:3333`.

---

## LAN Mobile Access

Use your PC's AI from your phone or tablet on the same network:

1. Ensure the app is running on your PC.
2. The terminal will show a **Network Access** IP (e.g., `http://192.168.1.15:3333`).
3. Open that URL on your mobile browser.
4. Generate text, images, or speech directly from your mobile device!

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Image Engine "Not Ready" | Stop the Chat Engine using the **"Stop Chat Engine"** button in the Image panel to free up RAM. |
| TTS Engine "Not Installed" | Re-run the `install` script to ensure the Piper binary was extracted correctly. |
| "Engine Not Found" | Ensure you ran the `install` script before the `start` script. |
| Slow Generation | The model may be too large for your RAM. Try a smaller model (e.g., Gemma 2 2B). |

---
## Privacy Policy
PortableLM runs 100% offline after initial setup. No data—including chat history, generated images, or voice outputs—ever leaves your machine. No telemetry, no analytics, no cloud dependencies, no hidden phone-home requests.

What we do NOT collect:

Personal information

Usage statistics

AI prompts or responses

Generated content

Hardware or system data

Third-party models: The LLM, image generation, and TTS models you download run locally and do not transmit data externally. Review each model's license individually (e.g., Llama 3, Gemma 2, Stable Diffusion) if you require specific compliance.

#### By using PortableLM, you retain full ownership of all content you generate.

## License

MIT

---

> *PortableLM — Your Personal, Portable AI Command Center.*
