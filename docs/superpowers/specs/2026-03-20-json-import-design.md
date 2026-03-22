# FreeVi JSON Import Feature — Design Spec

**Date:** 2026-03-20
**Status:** Approved
**Feature:** Import JSON to generate videos directly, bypassing PDF + LLM pipeline

---

## 1. Overview

FreeVi currently generates videos from PDFs by extracting text, sending it to an LLM to generate a script (scenes with narrator_text + video_query), and then processing each scene. This feature adds a parallel path: the user can provide a JSON file with scenes already defined, and FreeVi will generate the video directly without PDF extraction or LLM script generation.

---

## 2. JSON Format

### 2.1 Schema

```json
{
  "scenes": [
    {
      "narrator_text": "Required. The narration text for TTS.",
      "video_query": "Optional. 2-5 word search query for Pexels video.",
      "title": "Optional. Slide title.",
      "content": "Optional. Array of strings for slide bullet points.",
      "icon": "Optional. Icon filename from Tabler Icons (e.g. 'flask.svg').",
      "generate_svg": "Optional. Boolean, overrides global SVG toggle for this scene."
    }
  ]
}
```

At least one of `video_query` OR (`title` + `content`) must be present per scene.

### 2.2 Auto-Detection Per Scene

| Fields present | Visual type |
|---|---|
| `video_query` only | Pexels video |
| `title` + `content` | AI slide |
| Both | Default to `video_query`; user can override per scene |

---

## 3. UI Changes

### 3.1 Input Mode Selector

Add radio buttons at the top of the left panel:

```
○ Desde PDF   ● Desde JSON
```

Default: **Desde PDF** (preserves current behavior).

### 3.2 Mode-Specific UI Visibility

| UI Group | Modo PDF | Modo JSON |
|---|---|---|
| Select PDF button | Visible | Hidden |
| LLM Model + Chunk settings | Visible | Hidden |
| Max scenes | Visible | Hidden |
| Prompt customization | Visible | Hidden |
| **JSON Group** | Hidden | **Visible** |
| Visual Source selector | Visible | Visible (auto-detected default) |
| Slide theme selector | Visible | Visible (if slides used) |
| Generate SVGs toggle | Visible | Visible |
| Language & Voice | Visible | Visible |
| API Keys | Visible | Visible |
| Output | Visible | Visible |

### 3.3 JSON Group (visible only in JSON mode)

- **"Seleccionar JSON"** button → opens file dialog filtered to `*.json`
- File path label (shows selected file or empty)
- **"Ver formato JSON"** button → opens modal

### 3.4 JSON Format Modal

Window title: `Formato del JSON`

Code block (monospace, dark theme) showing the full example schema with field descriptions.

Buttons: **Copiar al portapapeles** | **Cerrar**

### 3.5 Script Review Dialog

Skipped in JSON mode (no LLM generation occurred). The JSON is validated on load and errors shown inline.

---

## 4. Code Changes

### 4.1 Scene dataclass additions

File: `freevi.py`

Add serialization methods to `Scene`:
- `to_dict() -> dict`
- `from_dict(data: dict, number: int) -> Scene`

Add `validate_json_scenes(data: dict) -> tuple[bool, str | None]` function.

### 4.2 New functions in `freevi.py`

```
load_scenes_from_json(path: str) -> list[Scene]
detect_scene_type(scene: Scene) -> Literal["pexels", "slides"]
```

### 4.3 GUI state

In `freevi_gui.py`, add:
```python
self.input_mode = "pdf"  # or "json"
```

### 4.4 UI toggle method

```python
def set_input_mode(self, mode: str):
    """Toggle visibility of groups based on input mode."""
    self.input_mode = mode
    # show/hide relevant groups
```

### 4.5 JSON validation on load

When JSON is selected, validate immediately and show error in status bar if invalid.

### 4.6 Pipeline bypass in JSON mode

In `FreeViGUI.start_pipeline()`:
```python
if self.input_mode == "json":
    self.scenes = load_scenes_from_json(self.json_path)
    # Skip: extract_pdf_text(), generate_script()
    # Continue from: AudioEngine → video/slide → assemble
```

---

## 5. Validation Rules

1. JSON must be valid JSON.
2. Must have `scenes` key with array value.
3. Each scene must have `narrator_text` (non-empty string).
4. Each scene must have at least one of: `video_query`, or (`title` + `content`).
5. `content` must be an array of strings (or empty).
6. `generate_svg` must be boolean if present.
7. `icon` must be a string if present (no validation that file exists — checked at render time).

On validation failure: show error message in status bar, disable "Generate Video" button.

---

## 6. Interaction Summary

| Action | Result |
|---|---|
| Select "Desde JSON" | UI reconfigures; JSON group appears; PDF/LLM groups hidden |
| Select "Desde PDF" | UI resets to current behavior |
| Click "Seleccionar JSON" | File dialog; on valid load → show path; on error → status bar error |
| Click "Ver formato JSON" | Modal opens with schema example |
| Click "Generate Video" (JSON mode) | Validate JSON → process scenes → generate video |

---

## 7. Files to Modify

| File | Changes |
|---|---|
| `freevi.py` | Add `to_dict`/`from_dict` to Scene, add `load_scenes_from_json`, add `detect_scene_type`, add validation |
| `freevi_gui.py` | Add input mode state, add JSON group, add modal, add toggle logic, bypass LLM pipeline |
| `docs/superpowers/specs/YYYY-MM-DD-json-import-design.md` | This document |

---

## 8. Out of Scope

- JSON export (saving generated scenes to JSON)
- Scene reordering in UI
- Per-scene overrides beyond `generate_svg`
- Multiple visual sources in one scene (forced choice, no mix)

## 9. Related Feature: Pexels Images

A parallel feature adds **Pexels Images** as a visual source option alongside videos.
This shares the same JSON `video_query` field for searching.

**Implementation:**
- New `VISUAL_PEXELS_IMAGES = "pexels_images"` constant
- `PEXELS_PHOTOS_API_URL = "https://api.pexels.com/photos/search"`
- `search_pexels_image()` — searches and returns best photo URL
- `search_and_download_image()` — downloads photo to disk
- `assemble_image_scene()` — creates video from image with Ken Burns effect
  - Zoom: 1.0 → 1.15x over audio duration (alternating in/out per scene)
  - Single encode pass with audio mixing
- GUI: new "Pexels (Stock Images)" option in visual source selector
- Fallback chain: full query → first two words → first word → generic fallback
