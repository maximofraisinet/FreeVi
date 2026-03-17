# FreeVi

Automatically generate engaging videos from PDF documents using local AI and Text-to-Speech.

<img width="1271" height="800" alt="1" src="https://github.com/user-attachments/assets/22a523a6-599a-4ce4-9afd-3073e3bf706b" />

<img width="1272" height="801" alt="2" src="https://github.com/user-attachments/assets/b29a9c03-a1ca-4f67-9758-af96bc4e1326" />

## About

FreeVi is a powerful tool that transforms PDF documents into audiovisual narratives. By leveraging local Large Language Models (LLMs) via Ollama, advanced Text-to-Speech (TTS) with Kokoro, and automatic background videos from Pexels, FreeVi allows you to create engaging content from text simply and efficiently.

## Features

- **PDF to Video:** Convert the text of your PDF documents into an audiovisual narrative.
- **Smart Processing:** Uses local LLMs (via Ollama) to process and adapt the text into a script.
- **Natural Voices (TTS):** High-quality, multilingual voice synthesis using Kokoro ONNX.
- **Automatic Visuals:** Automatically integrates relevant background video clips using the Pexels API.
- **Dual Interface:** Offers both an intuitive Graphical User Interface (GUI) and a Command-Line Interface (CLI).

## Requirements

Before you begin, ensure you have the following configured:

1. **Python 3.8+** installed on your system.
2. **Ollama:** Installed and running (`ollama serve`). You also need to download a base model (e.g., `ollama pull qwen3`).
3. **Kokoro Models:** The Kokoro ONNX models must be placed in the `./kokoro-v1.0/` directory.
4. **Pexels API Key:** Required to automatically download background videos.

## Installation

### 1. Clone and Install Dependencies

```bash
# Clone the repository
git clone https://github.com/maximofraisinet/FreeVi.git
cd FreeVi

# Install dependencies
pip install -r requirements.txt
```

### 2. Download the Kokoro v1.0 Voice Model

Kokoro powers all built-in voices (English + Spanish). The model files are too large for GitHub, so download them manually:

1. Go to the [kokoro-onnx releases page](https://github.com/thewh1teagle/kokoro-onnx/releases).
2. Download `kokoro-v1.0.onnx` and `voices-v1.0.bin`.
3. Place both files inside the `./kokoro-v1.0/` directory in the project.

### 3. Configure Environment Variables

```bash
# Configure Environment Variables
cp .env.example .env
# Open .env and add your PEXELS_API_KEY
```

## Usage

### Graphical Interface (Recommended)

The quickest path to get started is using the GUI:

```bash
python freevi_gui.py
```

### Command-Line Interface (CLI)

For advanced users and automation:

```bash
# Basic example
python freevi.py document.pdf

# Advanced example with options
python freevi.py book.pdf --model qwen3 --voice af_heart --lang a --output result.mp4
```

**Available languages (`--lang`):**
- `a` = English (American)
- `b` = English (British)
- `e` = Spanish
- `f` = French
- `h` = Hindi
- `i` = Italian
- `j` = Japanese
- `p` = Portuguese (Brazilian)
- `z` = Chinese (Mandarin)

## Acknowledgments

- Built using [Ollama](https://ollama.ai/), [Kokoro](https://github.com/hexgrad/kokoro), and the [Pexels API](https://www.pexels.com/api/).
