# FreeVi

Automatically generate engaging videos from PDF documents using local AI, Text-to-Speech, and customizable visual sources.

<img width="1200" height="801" alt="1" src="https://github.com/user-attachments/assets/674a1b9d-8106-491b-8b70-1aad46e1ec53" />

<img width="1200" height="799" alt="2" src="https://github.com/user-attachments/assets/bb2bda70-0d05-4ff1-9b26-fb5be2a22715" />

## About

FreeVi transforms PDF documents into dynamic audiovisual narratives. Choose between **stock videos**, **stock photos** (with Ken Burns effect), or **AI-generated slides** as your visual source. Built with local LLMs (via Ollama), high-quality TTS (Kokoro), and modern visual themes.

## Features

- **PDF to Video:** Convert PDF text into engaging audiovisual content.
- **JSON to Video:** Import a JSON file with scenes to generate videos without going through the LLM script generation step.
- **Multiple Visual Sources:** Choose between stock videos, photos, or AI-generated slides.
  - **Pexels Videos:** Automatic background video clips.
  - **Pexels Images:** Stock photos with Ken Burns (pan & zoom) effect.
  - **AI Slides (Simple):** Clean, themed presentations with text and icons.
  - **AI Slides (with SVG):** Presentations with AI-generated geometric illustrations and icons.
- **Dynamic Subtitles:** Choose between fast sentence-level captions or "Pro" word-level sync powered by Whisper AI (perfect for vertical Shorts/Reels).
- **Smart Icon Selection:** 6,000+ icons from Tabler Icons, automatically selected based on content.
- **Smart Processing:** Local LLM (Ollama) adapts text into natural narration scripts.
- **Natural Voices (Dual Engine):** Choose between two powerful Text-to-Speech engines:
  - **Kokoro v1.0 (ONNX):** Ultra-fast, lightweight, and supports multiple languages.
  - **Microsoft VibeVoice 0.5B (PyTorch):** Premium, conversational voices for highly natural documentaries (requires 6GB+ VRAM GPU).
- **Customizable Themes:** Tokyo Night, Executive, and Minimal slide themes.
- **JSON Import:** Bring your own scenes (narrator text + video/slide data) via JSON — no PDF or LLM needed.
- **Dual Interface:** Intuitive GUI or flexible CLI.

## Requirements

- **Python 3.10+** installed on your system.
- **Ollama:** Installed and running (`ollama serve`). Download a model: `ollama pull qwen3`.
- **Kokoro Models:** Place `kokoro-v1.0.onnx` and `voices-v1.0.bin` in `./kokoro-v1.0/`.
- **VibeVoice Models (Optional):** Requires a GPU with at least 6GB VRAM and `flash-attn`.
- **Pexels API Key:** Required only for Pexels video/image mode.

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

### 3. Download TTS Voice Models

**Option A: Kokoro (Fast, Low Resource)**
1. Download from [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases):
   - `kokoro-v1.0.onnx`
   - `voices-v1.0.bin`
2. Place both in `./kokoro-v1.0/`.

**Option B: VibeVoice (Premium, GPU Required)**
1. Install VibeVoice and Flash Attention:
   ```bash
   # We strongly recommend Python 3.12 and PyTorch 2.5 to avoid compiling Flash Attention from source.
   pip install git+https://github.com/microsoft/VibeVoice.git
   pip install flash-attn --no-build-isolation
   ```
2. Download the VibeVoice voice files for your desired languages and extract them into the `vibevoices` folder:
   ```bash
   mkdir -p vibevoices
   
   # Download Spanish voices
   wget https://github.com/user-attachments/files/24035884/experimental_voices_sp.tar.gz
   tar -xzf experimental_voices_sp.tar.gz -C vibevoices
   
   # Download English voices (Pack 1)
   wget https://github.com/user-attachments/files/24189272/experimental_voices_en1.tar.gz
   tar -xzf experimental_voices_en1.tar.gz -C vibevoices
   
   # Clean up downloaded archives
   rm experimental_voices_*.tar.gz
   ```

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
- **Subtitles:** Position, Sync Method (Fast/Pro), and Max Words per chunk.
- All other options (voice, language, resolution, etc.)

### JSON Import (Hybrid Videos)

Instead of feeding a PDF, you can provide a JSON file with your own scenes. The system will intelligently detect the visual source per scene, allowing you to create **hybrid videos** that mix Pexels videos, static images, and AI slides in a single render. The LLM script generation step is skipped entirely.

**Example: Hybrid JSON format**
```json
{
  "scenes": [
    {
      "narrator_text": "Welcome to our presentation about the cosmos.",
      "video_query": "galaxy stars space" 
      // Detects video_query -> Uses Pexels Video
    },
    {
      "narrator_text": "Let's review the main topics we will cover today.",
      "title": "Agenda",
      "content": ["Black Holes", "Dark Matter", "Exoplanets"],
      "icon": "telescope.svg"
      // Detects title + content -> Generates an AI Slide
    },
    {
      "narrator_text": "Here is an incredible view of the James Webb telescope.",
      "video_query": "james webb telescope in space",
      "image": true 
      // Detects image: true -> Uses Pexels Image with Ken Burns effect
    }
  ]
}
```

Each scene needs at least `video_query` **or** `title` + `content`. Add as many scenes as you need — they become consecutive clips in the final video.

### Command-Line Interface

The FreeVi CLI is powerful and allows you to build completely automated video pipelines.

#### Examples

**Full JSON Pipeline (Hybrid Vertical Video with VibeVoice)**
Skip the LLM and PDF steps. Pass a JSON file to build a high-quality vertical short with dynamic subtitles using VibeVoice:
```bash
python freevi.py script.json \
  --orientation portrait \
  --resolution 1080x1920 \
  --tts-engine vibevoice \
  --voice sp/sp-Spk0_woman \
  --lang e \
  --speed 1.0 \
  --subtitles middle \
  --subtitle-sync pro \
  --subtitle-max-words 1 \
  --output output/my_short.mp4
```

**Full PDF Pipeline (Horizontal Video with Kokoro)**
Full automated pipeline: extracts text, asks the LLM to generate 10 scenes, adds Pexels videos, and renders using Kokoro:
```bash
python freevi.py document.pdf \
  --model qwen3 \
  --max-scenes 10 \
  --chunk-size 4096 \
  --visual-source pexels \
  --orientation landscape \
  --resolution 1920x1080 \
  --tts-engine kokoro \
  --voice bm_daniel \
  --lang b \
  --speed 1.0 \
  --subtitles bottom \
  --subtitle-sync fast \
  --output output/my_documentary.mp4
```

#### CLI Reference: Available Flags

**Common Flags (Used in both JSON and PDF modes)**
| Flag | Description | Default |
|------|-------------|---------|
| `--orientation` | Video orientation (`landscape`, `portrait`, `square`). | `landscape` |
| `--resolution` | Output resolution in WxH format (e.g., `1920x1080`). Validates against orientation. | Auto |
| `--tts-engine` | `kokoro` or `vibevoice`. | `kokoro` |
| `--voice` | Voice ID. For Kokoro: e.g. `af_heart`. For VibeVoice: include the subfolder, e.g. `sp/sp-Spk0_woman`. | `af_heart` |
| `--lang` | Narration language code (see Languages section below). | `a` |
| `--speed` | Narration speed multiplier. | `1.0` |
| `--subtitles` | Subtitle position (`bottom`, `middle`, `top`). Omit for no subtitles. | None |
| `--subtitle-sync`| `fast` (sentence-level) or `pro` (word-level via Whisper). | `fast` |
| `--subtitle-max-words`| Max words per subtitle chunk (1-10). Only applies to `pro` mode. | `4` |
| `--output` | Path to save the final video. | `output/video_final.mp4` |
| `--slide-theme` | Theme for slides (`tokyo_night`, `executive`, `minimal`). | `tokyo_night` |

**PDF-Only Flags (Ignored in JSON mode)**
| Flag | Description | Default |
|------|-------------|---------|
| `--model` | Local Ollama model name to use for generating the script. | `qwen3` |
| `--max-scenes` | Target number of scenes to generate. | `8` |
| `--chunk-size` | Number of tokens per PDF chunk sent to the LLM. | `4096` |
| `--visual-source`| Visual style to apply globally (`pexels`, `pexels_images`, `slides_simple`, `slides_svg`). | `pexels` |

### Visual Source Options

| Option | Description | Time |
|--------|-------------|------|
| `pexels` | Stock videos from Pexels API | Slow |
| `pexels_images` | Stock photos with Ken Burns (pan & zoom) effect | Medium |
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
              ┌────────────────┴────────────────┐
              ↓                                 ↓
      PDF Path (LLM)                   JSON Path (direct)
              ↓                                 ↓
   Text Extraction                   Load scenes directly
              ↓
   LLM Script Generation
              ↓                                 ↓
              └──────────────┬──────────────────┘
                             ↓
                      TTS Audio
                             ↓
        ┌────────────────────┴────────────────────┐
        ↓                   ↓                    ↓
 Pexels Videos       Pexels Images       AI Slides
 (Download)          (Download)          (Generate)
        ↓                   ↓                    ↓
        └────────────────────┴────────────────────┘
                             ↓
                        MoviePy
                             ↓
                      Final Video
```

## Dependencies

Built with:
- [Ollama](https://ollama.ai/) - Local LLM inference
- [Kokoro](https://github.com/hexgrad/kokoro) - Text-to-Speech
- [Faster Whisper](https://github.com/SYSTRAN/faster-whisper) - Word-level subtitle synchronization
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

This project is released under the **MIT License**. See [LICENSE](LICENSE) for details.

**Note:** The icon library (Tabler Icons) is licensed separately under the MIT License. See credits above.
