# FreeVi

Automatically generate engaging videos from PDF documents using local AI, Text-to-Speech, and customizable visual sources.

<img width="1200" height="801" alt="1" src="https://github.com/user-attachments/assets/674a1b9d-8106-491b-8b70-1aad46e1ec53" />

<img width="1200" height="799" alt="2" src="https://github.com/user-attachments/assets/bb2bda70-0d05-4ff1-9b26-fb5be2a22715" />

## About

FreeVi transforms PDF documents into dynamic audiovisual narratives. Choose between **stock videos from Pexels** or **AI-generated slides** as your visual source. Built with local LLMs (via Ollama), high-quality TTS (Kokoro), and modern visual themes.

## Features

- **PDF to Video:** Convert PDF text into engaging audiovisual content.
- **JSON to Video:** Import a JSON file with scenes to generate videos without going through the LLM script generation step.
- **Multiple Visual Sources:** Choose between stock videos or AI-generated slides.
  - **Pexels Videos:** Automatic background video clips.
  - **AI Slides (Simple):** Clean, themed presentations with text and icons.
  - **AI Slides (with SVG):** Presentations with AI-generated geometric illustrations and icons.
- **Smart Icon Selection:** 6,000+ icons from Tabler Icons, automatically selected based on content.
- **Smart Processing:** Local LLM (Ollama) adapts text into natural narration scripts.
- **Natural Voices:** Multilingual TTS using Kokoro ONNX.
- **Customizable Themes:** Tokyo Night, Executive, and Minimal slide themes.
- **JSON Import:** Bring your own scenes (narrator text + video/slide data) via JSON — no PDF or LLM needed.
- **Dual Interface:** Intuitive GUI or flexible CLI.

## Requirements

- **Python 3.10+** installed on your system.
- **Ollama:** Installed and running (`ollama serve`). Download a model: `ollama pull qwen3`.
- **Kokoro Models:** Place `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `./kokoro-v1.0/`.
- **Pexels API Key:** Required only for stock video mode.

## Installation

### 1. Clone and Install Dependencies

```bash
git clone https://github.com/maximofraisinet/FreeVi.git
cd FreeVi
pip install -r requirements.txt
```

### 2. Install System Dependencies

For slide generation (AI Slides mode):

```bash
# Ubuntu/Debian
sudo apt install libcairo2 libcairo2-dev

# macOS
brew install cairo

# Arch
sudo pacman -S cairo
```

### 3. Download Kokoro Voice Models

1. Download from [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases):
   - `kokoro-v1.0.onnx`
   - `voices-v1.0.bin`
2. Place both in `./kokoro-v1.0/`.

### 4. Configure Environment Variables (Optional)

```bash
cp .env.example .env
# Edit .env and add PEXELS_API_KEY (only needed for Pexels mode)
```

## Usage

### Graphical Interface (Recommended)

```bash
python freevi_gui.py
```

The GUI lets you select:
- **Input Mode:** "From PDF" (LLM generates the script) or "From JSON" (use your own scenes)
- **Visual Source:** Pexels (Videos), Slides (Simple), or Slides (with AI SVG)
- **Slide Theme:** Tokyo Night, Executive, or Minimal
- All other options (voice, language, resolution, etc.)

### JSON Import

Instead of feeding a PDF, you can provide a JSON file with your own scenes. Select **"From JSON"** in the GUI, load your file, and click **Generate Video**. The LLM script generation step is skipped entirely.

**JSON Format:**

**Example: Pexels Videos**
```json
{
  "scenes": [
    {
      "narrator_text": "The universe expands constantly, revealing mysteries beyond our comprehension.",
      "video_query": "galaxy stars space"
    },
    {
      "narrator_text": "Black holes are regions where gravity is so intense that nothing can escape.",
      "video_query": "black hole animation"
    }
  ]
}
```

**Example: AI Slides**
```json
{
  "scenes": [
    {
      "narrator_text": "Photosynthesis is the process by which plants convert light into energy.",
      "title": "Photosynthesis",
      "content": ["Absorbing sunlight", "Converting to glucose", "Releasing oxygen"],
      "icon": "leaf.svg",
      "generate_svg": false
    },
    {
      "narrator_text": "The water cycle describes the continuous movement of water on Earth.",
      "title": "Water Cycle",
      "content": ["Evaporation", "Condensation", "Precipitation"],
      "icon": "droplet.svg",
      "generate_svg": true
    }
  ]
}
```

Each scene needs at least `video_query` **or** `title` + `content`. Add as many scenes as you need — they become consecutive clips in the final video.

### Command-Line Interface

```bash
# Basic example (uses Pexels videos by default)
python freevi.py document.pdf

# Use AI slides instead of videos
python freevi.py document.pdf --visual-source slides_simple --slide-theme executive

# AI slides with SVG illustrations (slower, requires extra LLM calls)
python freevi.py document.pdf --visual-source slides_svg --slide-theme tokyo_night
```

### Visual Source Options

| Option | Description | Time |
|--------|-------------|------|
| `pexels` | Stock videos from Pexels API | Slow |
| `slides_simple` | Themed slides with text and icons | Fast |
| `slides_svg` | Themed slides with AI-generated SVG illustrations and icons | Medium |

### Available Slide Themes

| Theme | Colors | Best For |
|-------|--------|----------|
| `tokyo_night` | Dark blue/purple | General purpose |
| `executive` | Navy/white/blue | Professional presentations |
| `minimal` | Black/white/cyan | Modern, clean look |

### Languages (`--lang`)

- `a` = English (American)
- `b` = English (British)
- `e` = Spanish
- `f` = French
- `h` = Hindi
- `i` = Italian
- `j` = Japanese
- `p` = Portuguese (Brazilian)
- `z` = Chinese (Mandarin)

## Architecture

```
                    ┌─────────────────────────────┐
                    │          INPUT              │
                    │  PDF  ──or──   JSON file   │
                    └───────────┬─────────────────┘
                                ↓
              ┌─────────────────┴─────────────────┐
              ↓                                   ↓
      PDF Path (LLM)                    JSON Path (direct)
              ↓                                   ↓
   Text Extraction                    Load scenes directly
              ↓
   LLM Script Generation                     
              ↓                                   ↓
              └──────────────┬────────────────────┘
                             ↓
                      TTS Audio
                             ↓
           ┌────────────────┴────────────────┐
           ↓                                 ↓
    Pexels Videos                     AI Slides
    (Download)                      (Generate)
           ↓                                 ↓
           └──────────────┬────────────────────┘
                          ↓
                     MoviePy
                          ↓
                    Final Video
```

## Dependencies

Built with:
- [Ollama](https://ollama.ai/) - Local LLM inference
- [Kokoro](https://github.com/hexgrad/kokoro) - Text-to-Speech
- [Pexels API](https://www.pexels.com/api/) - Stock videos
- [PyMuPDF](https://pymupdf.readthedocs.io/) - PDF processing
- [MoviePy](https://zulko.github.io/moviepy/) - Video editing
- [Pillow](https://pillow.readthedocs.io/) - Image generation
- [CairoSVG](https://cairosvg.org/) - SVG rendering
- [PyQt6](https://www.qt.io/qt-for-python) - Graphical interface
- [Tabler Icons](https://tabler-icons.io/) - Icon library (SVG)

## Credits

### Icons

The icon library included in this project uses **Tabler Icons**, licensed under the [MIT License](https://github.com/tabler/tabler-icons/blob/master/LICENSE).

Tabler Icons is an open-source SVG icon library by [Tabler](https://tabler.io/). The icons are automatically recolored to match the selected slide theme during video generation.

### Other Assets

- **Stock Videos:** Videos are sourced from [Pexels](https://www.pexels.com/) and require attribution according to [Pexels License](https://www.pexels.com/license/).

## License

This project is released into the public domain under **The Unlicense**. See [LICENSE](LICENSE) for details.

**Note:** The icon library (Tabler Icons) is licensed separately under the MIT License. See credits above.
