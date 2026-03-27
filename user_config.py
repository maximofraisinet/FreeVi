"""
user_config.py — Persistent user configuration for FreeVi GUI
==============================================================
Stores the last-used settings in ``user_config.json`` next to this script
(i.e. inside the project directory).  The file is git-ignored and created
automatically on first launch.
"""

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("freevi.config")


# ── Config file path ──────────────────────────────────────────────────────────

def _config_path() -> Path:
    """Returns the path to the config file (same directory as this script)."""
    return Path(__file__).parent / "user_config.json"


# ── Default values ────────────────────────────────────────────────────────────

# Used when the file does not exist yet (first launch).
# NOTE: pexels_key and pdf_path are intentionally NOT persisted here.
#   pexels_key  → loaded from .env / environment variable (security).
#   pdf_path    → changes every run; no value in remembering it.
DEFAULTS: dict = {
    "model":               "qwen3",
    "max_scenes":          8,
    "chunk_size":          4096,
    "tts_engine":          "kokoro",
    "voice":               "im_nicola",
    "speed":               100,          # slider int (50–200); divide by 100 → actual speed
    "resolution":          "1920×1080 (Full HD)",
    "fps":                 "24 fps",
    "preset":              "medium",
    "orientation":         "landscape",
    "output":              "output/video_final.mp4",
    "open_when_done":      True,
    # Empty string means "use the built-in DEFAULT_CUSTOM_INSTRUCTIONS from freevi.py"
    "custom_instructions": "",
    "lang_code":           "a",
    "visual_source":       "pexels",
    "slide_theme":         "tokyo_night",
    "subtitle_position":   None,
    "subtitle_method":     "fast",        # "fast" = sentence-level, "pro" = word-level with Whisper
    "subtitle_max_words":  4,             # max words per subtitle chunk (1-10)
    "input_mode":          "pdf",
}


# ── Internal helpers (defined first so load() can call them) ──────────────────

def _write(path: Path, data: dict) -> None:
    """Atomically writes *data* as indented JSON to *path*, creating dirs."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.debug(f"User config written to {path}")
    except Exception as e:
        log.warning(f"Could not write user config ({path}): {e}")


def _resolution_label(resolution) -> str:
    """Converts a (width, height) tuple back to the combo-box label string."""
    mapping = {
        (1920, 1080): "1920×1080 (Full HD)",
        (1080, 1920): "1080×1920 (Full HD Vertical)",
        (1280, 720):  "1280×720 (HD)",
        (720, 1280):  "720×1280 (HD Vertical)",
        (3840, 2160): "3840×2160 (4K)",
    }
    if isinstance(resolution, (list, tuple)) and len(resolution) == 2:
        return mapping.get(tuple(resolution), DEFAULTS["resolution"])
    return resolution if isinstance(resolution, str) else DEFAULTS["resolution"]


def _fps_label(fps) -> str:
    """Converts an integer fps value back to the combo-box label string."""
    mapping = {24: "24 fps", 30: "30 fps", 60: "60 fps"}
    if isinstance(fps, int):
        return mapping.get(fps, DEFAULTS["fps"])
    return fps if isinstance(fps, str) else DEFAULTS["fps"]


# ── Public API ────────────────────────────────────────────────────────────────

def load() -> dict:
    """
    Loads the user configuration from disk.

    On first launch the file does not exist yet: it is created immediately
    with the default values so it is always present after the program opens.

    On subsequent launches the stored values are merged with DEFAULTS, so any
    new key added in future versions always has a sensible fallback.

    Returns:
        A dict with all configuration keys ready to be fed to
        ConfigPanel.load_from_config().
    """
    path = _config_path()
    if not path.exists():
        log.debug(f"First launch — creating default config at {path}")
        defaults = dict(DEFAULTS)
        _write(path, defaults)
        return defaults

    try:
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        merged = {**DEFAULTS, **stored}   # DEFAULTS fill missing keys
        log.debug(f"User config loaded from {path}")
        return merged
    except Exception as e:
        log.warning(f"Could not read user config ({path}): {e}. Using defaults.")
        return dict(DEFAULTS)


def save(config: dict) -> None:
    """
    Persists the relevant subset of the current GUI configuration to disk.

    Args:
        config: The full config dict returned by ConfigPanel.get_config()
                (keys pdf_path and pexels_key are silently ignored).
    """
    to_save = {
        "model":               config.get("model",               DEFAULTS["model"]),
        "max_scenes":          config.get("max_scenes",          DEFAULTS["max_scenes"]),
        "chunk_size":          config.get("chunk_size",        DEFAULTS["chunk_size"]),
        "tts_engine":          config.get("tts_engine",          DEFAULTS["tts_engine"]),
        "voice":               config.get("voice",               DEFAULTS["voice"]),
        # Convert float speed → slider int so load_from_config() can apply it directly
        "speed":               int(config.get("speed", 1.0) * 100),
        "resolution":          _resolution_label(config.get("resolution", (1920, 1080))),
        "fps":                 _fps_label(config.get("fps", 24)),
        "preset":              config.get("preset",              DEFAULTS["preset"]),
        "orientation":         config.get("orientation",         DEFAULTS["orientation"]),
        "output":              config.get("output",              DEFAULTS["output"]),
        "open_when_done":      config.get("open_when_done",      DEFAULTS["open_when_done"]),
        "custom_instructions": config.get("custom_instructions", DEFAULTS["custom_instructions"]),
        "lang_code":           config.get("lang_code",           DEFAULTS["lang_code"]),
        "visual_source":       config.get("visual_source",      DEFAULTS["visual_source"]),
        "slide_theme":         config.get("slide_theme",        DEFAULTS["slide_theme"]),
        "subtitle_position":   config.get("subtitle_position",  DEFAULTS["subtitle_position"]),
        "subtitle_method":     config.get("subtitle_method",    DEFAULTS["subtitle_method"]),
        "subtitle_max_words":  config.get("subtitle_max_words", DEFAULTS["subtitle_max_words"]),
        "input_mode":          config.get("input_mode",         DEFAULTS["input_mode"]),
    }
    _write(_config_path(), to_save)


def save_from_panel(panel) -> None:
    """
    Reads the current widget state directly from a ConfigPanel instance and
    saves it. Used by closeEvent so settings are always persisted on exit,
    even if the pipeline was never run during that session.

    Args:
        panel: A ConfigPanel instance.
    """
    res_map = {
        "1920×1080 (Full HD)":       (1920, 1080),
        "1080×1920 (Full HD Vertical)": (1080, 1920),
        "1280×720 (HD)":             (1280, 720),
        "720×1280 (HD Vertical)":   (720, 1280),
        "3840×2160 (4K)":            (3840, 2160),
        "1080×1080 (Square HD)":    (1080, 1080),
        "720×720 (Square)":         (720, 720),
    }
    fps_map = {"24 fps": 24, "30 fps": 30, "60 fps": 60}

    raw = {
        "model":               panel.combo_model.currentText(),
        "max_scenes":          panel.spin_max_scenes.value(),
        "chunk_size":        panel.spin_chunk_size.value(),
        "tts_engine":          panel.combo_engine.currentData(),
        "voice":               panel.combo_voice.currentText(),
        "speed":               panel.slider_speed.value() / 100.0,
        "resolution":          res_map.get(panel.combo_res.currentText(), (1920, 1080)),
        "fps":                 fps_map.get(panel.combo_fps.currentText(), 24),
        "preset":              panel.combo_preset.currentData() or "medium",
        "orientation":         panel.combo_orientation.currentText(),
        "output":              panel.edit_output.text(),
        "open_when_done":      panel.chk_open_when_done.isChecked(),
        "custom_instructions": panel.txt_prompt.toPlainText(),
        "lang_code":           panel.combo_language.currentData(),
    }

    visual_idx = panel.combo_visual_source.currentIndex()
    visual_map = {0: "pexels", 1: "slides_simple", 2: "slides_svg"}
    raw["visual_source"] = visual_map.get(visual_idx, "pexels")

    theme_map = {"Tokyo Night": "tokyo_night", "Executive": "executive", "Minimal": "minimal"}
    raw["slide_theme"] = theme_map.get(panel.combo_slide_theme.currentText(), "tokyo_night")

    subtitle_idx = panel.combo_subtitles.currentIndex()
    subtitle_map = {0: None, 1: "bottom", 2: "middle", 3: "top"}
    raw["subtitle_position"] = subtitle_map.get(subtitle_idx)

    subtitle_method_idx = panel.combo_subtitle_method.currentIndex()
    subtitle_method_map = {0: "fast", 1: "pro"}
    raw["subtitle_method"] = subtitle_method_map.get(subtitle_method_idx, "fast")

    raw["subtitle_max_words"] = panel.spin_subtitle_max_words.value()

    raw["input_mode"] = "json" if panel.radio_json.isChecked() else "pdf"

    save(raw)
