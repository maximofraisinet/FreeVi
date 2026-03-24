"""
FreeVi GUI - Graphical interface for the PDF video generator
=============================================================
Run with: python freevi_gui.py
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env file into the environment (so PEXELS_API_KEY and others are available)
load_dotenv()

from PyQt6.QtCore import (
    QMutex,
    QObject,
    Qt,
    QThread,
    QWaitCondition,
    pyqtSignal,
)
from PyQt6.QtGui import QFont, QIcon, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QRadioButton,
)

# ---------------------------------------------------------------------------
# Logging handler that redirects to the GUI text widget
# ---------------------------------------------------------------------------
class QtLogHandler(logging.Handler, QObject):
    """Redirects logging messages to the GUI QTextEdit."""

    log_signal = pyqtSignal(str, str)  # (message, level)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record):
        msg = self.format(record)
        level = record.levelname
        self.log_signal.emit(msg, level)


# ---------------------------------------------------------------------------
# Script review dialog — shown after the LLM generates the script so the
# user can edit video_query values before any audio/video is processed.
# ---------------------------------------------------------------------------
class ScriptReviewDialog(QDialog):
    """
    Modal dialog that shows the generated script (narrator text + video query)
    and lets the user edit the video_query for each scene before download.
    """

    def __init__(self, scenes: list, parent=None):
        """
        Args:
            scenes: List of Scene dataclass instances from generate_script().
            parent: Parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Review Script — Edit Video Queries")
        self.setMinimumSize(900, 500)
        self.resize(1000, 580)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(
            "The AI generated the script below. "
            "You can edit the <b>Video Query</b> column before downloading stock footage.\n"
            "Queries are searched on Pexels — be specific and visual (e.g. "
            "<i>\"scientists analyzing dna\"</i>, <i>\"city traffic aerial view\"</i>)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._table = QTableWidget(len(scenes), 2)
        self._table.setHorizontalHeaderLabels(["Narrator Text", "Video Query (editable)"])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.setColumnWidth(0, 620)
        self._table.setColumnWidth(1, 280)
        self._table.verticalHeader().setDefaultSectionSize(60)
        self._table.setWordWrap(True)
        self._table.setAlternatingRowColors(True)

        for row, scene in enumerate(scenes):
            # Narrator text — read-only
            narrator_item = QTableWidgetItem(scene.narrator_text)
            narrator_item.setFlags(narrator_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            narrator_item.setToolTip(scene.narrator_text)
            self._table.setItem(row, 0, narrator_item)

            # Video query — editable
            query_item = QTableWidgetItem(scene.video_query)
            query_item.setToolTip(
                "Edit this to improve the stock footage match.\n"
                "Use 2-5 specific English words describing what the camera should show."
            )
            self._table.setItem(row, 1, query_item)

        self._table.resizeRowsToContents()
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continue with these queries")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel pipeline")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_queries(self) -> list[str]:
        """Returns the (possibly edited) video query for each row."""
        return [
            (self._table.item(row, 1).text() or "").strip()
            for row in range(self._table.rowCount())
        ]


# ---------------------------------------------------------------------------
# JSON Format dialog — shows the expected JSON schema with copy button
# ---------------------------------------------------------------------------
class JsonFormatDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("JSON Format")
        self.setMinimumSize(560, 560)
        self.resize(620, 620)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(
            "Your JSON file must follow one of these structures. "
            "Choose based on your selected visual source."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(info)

        self._code = QTextEdit()
        self._code.setReadOnly(True)
        self._code.setFont(QFont("Monospace", 10))
        self._code.setText(self._get_example())
        self._code.setStyleSheet(
            "background-color: #161b22; color: #c9d1d9; "
            "border: 1px solid #30363d; border-radius: 6px; padding: 12px;"
        )
        layout.addWidget(self._code)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Copy to clipboard")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Close")
        buttons.accepted.connect(self._copy)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _get_example(self) -> str:
        return """# Example: Pexels Videos or Images
# Use with visual source: Pexels (Stock Videos) or Pexels (Stock Images)
{
  "scenes": [
    {
      "narrator_text": "The universe expands constantly, revealing mysteries beyond our comprehension.",
      "video_query": "galaxy stars space",
      "image": false    // optional — true for images, false (or omit) for videos
    },
    {
      "narrator_text": "Black holes are regions where gravity is so intense that nothing can escape.",
      "video_query": "black hole animation",
      "image": true     // optional
    }
  ]
}

# Example: AI Slides
# Use with visual source: Slides (Simple) or Slides (with AI SVG)
{
  "scenes": [
    {
      "narrator_text": "Photosynthesis is the process by which plants convert light into energy.",
      "title": "Photosynthesis",
      "content": ["Absorbing sunlight", "Converting to glucose", "Releasing oxygen"],
      "icon": "leaf.svg",        // optional — icon from Tabler Icons library
      "generate_svg": false       // optional — set true to also generate an AI SVG illustration
    },
    {
      "narrator_text": "The water cycle describes the continuous movement of water on Earth.",
      "title": "Water Cycle",
      "content": ["Evaporation", "Condensation", "Precipitation"],
      "icon": "droplet.svg",     // optional
      "generate_svg": true       // optional
    }
  ]
}"""

    def _copy(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._code.toPlainText())
        self.accept()


# ---------------------------------------------------------------------------
# Pipeline worker
# ---------------------------------------------------------------------------
class PipelineWorker(QThread):
    """
    Runs the VideoGenerator pipeline in a separate thread.
    Emits signals to communicate progress, logs and result to the UI.

    After the script is generated the worker pauses and emits script_ready.
    The main thread shows ScriptReviewDialog; when the user confirms it calls
    resume(edited_queries) which unblocks the worker with the updated queries.
    """

    # Signals
    progress = pyqtSignal(int, str)            # (percentage, description)
    log_msg = pyqtSignal(str, str)             # (message, level: INFO/WARNING/ERROR)
    scene_started = pyqtSignal(int, int, str)  # (num, total, text_preview)
    script_ready = pyqtSignal(list)            # list of Scene objects — pause point

    finished = pyqtSignal(str)                 # path to the final video
    error = pyqtSignal(str)                    # error message

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._cancel = False
        # Synchronisation primitives for the review pause
        self._mutex = QMutex()
        self._wait = QWaitCondition()
        self._reviewed_queries: list[str] | None = None  # set by resume()

    def cancel(self):
        self._cancel = True
        # Unblock the wait in case we're paused at the review step
        self._wait.wakeAll()

    def resume(self, edited_queries: list[str]) -> None:
        """
        Called from the main thread after the user confirms the review dialog.
        Stores the edited queries and unblocks _run_pipeline.
        """
        self._mutex.lock()
        self._reviewed_queries = edited_queries
        self._wait.wakeAll()
        self._mutex.unlock()

    def run(self):
        try:
            self._run_pipeline()
        except Exception as e:
            self.error.emit(str(e))

    def _run_pipeline(self):
        """Runs the full pipeline step by step."""
        from freevi import (
            AudioEngine,
            assemble_image_scene,
            assemble_scene_from_raw,
            assemble_slide_scene,
            concatenate_scenes,
            extract_pdf_text,
            generate_script,
            generate_slide_content,
            get_language_label,
            render_slide_image,
            search_and_download_image,
            search_and_download_video,
            VISUAL_PEXELS,
            VISUAL_PEXELS_IMAGES,
            VISUAL_SLIDES_SIMPLE,
            VISUAL_SLIDES_SVG,
        )
        from slide_templates import get_theme
        from slide_svg_generator import generate_svg_illustration
        import tempfile, shutil, os

        cfg = self.config
        visual_source = cfg.get("visual_source", "pexels")
        slide_theme = get_theme(cfg.get("slide_theme", "tokyo_night")).to_dict()

        # ── JSON mode: use preloaded scenes, skip PDF extraction and LLM ──
        if cfg.get("input_mode") == "json":
            script_scenes = cfg.get("preloaded_scenes", [])
            if not script_scenes:
                self.error.emit("No scenes loaded from JSON.")
                return
            self.progress.emit(5, "Loading from JSON...")
            self.log_msg.emit(f"Loaded {len(script_scenes)} scenes from JSON.", "INFO")
        else:
            # ── Step 1: Extract PDF text ──
            self.progress.emit(5, "Extracting text from PDF...")
            self.log_msg.emit("Extracting text from PDF...", "INFO")
            text = extract_pdf_text(cfg["pdf_path"])
            if self._cancel:
                return
            self.log_msg.emit(f"Text extracted: {len(text)} characters", "INFO")

            # ── Step 2: Generate script ──
            self.progress.emit(15, f"Generating script with {cfg['model']}...")
            self.log_msg.emit(f"Generating script with model '{cfg['model']}'...", "INFO")

            total_chunks: list[int] = []

            def _on_script_progress(done: int, total: int) -> None:
                total_chunks[:] = [total]
                if total > 0:
                    pct = 15 + int(15 * done / total)
                    self.progress.emit(pct, f"Generating script — chunk {done}/{total}...")

            script = generate_script(
                text,
                cfg["model"],
                cfg["max_scenes"],
                cfg.get("custom_instructions", ""),
                cfg.get("chunk_size", 4096),
                narration_language=get_language_label(cfg.get("lang_code", "a")),
                on_progress=_on_script_progress,
            )
            if self._cancel:
                return
            self.log_msg.emit(f"Script generated: {len(script.scenes)} scenes", "INFO")

            # ── Step 2b: Pause for user review of video queries (only for Pexels) ──
            self.progress.emit(18, "Waiting for script review...")
            self._mutex.lock()
            self._reviewed_queries = None
            self.script_ready.emit(script.scenes)
            self._wait.wait(self._mutex)
            reviewed = self._reviewed_queries
            self._mutex.unlock()

            if self._cancel or reviewed is None:
                return

            for scene, new_query in zip(script.scenes, reviewed):
                q = new_query.strip()
                if q:
                    scene.video_query = " ".join(q.split()[:5])

            script_scenes = script.scenes

        # ── Step 2c: Generate slide content (if using slides) ──
        if visual_source != VISUAL_PEXELS:
            use_icons = visual_source == "slides_svg"
            has_slide_content = any(s.slide_title and s.slide_content for s in script_scenes)
            if has_slide_content:
                self.log_msg.emit("Using slide content from JSON.", "INFO")
            else:
                self.log_msg.emit("Generating slide content...", "INFO")
                for scene in script_scenes:
                    if self._cancel:
                        return
                    title, content, icon = generate_slide_content(
                        scene, cfg["model"], get_language_label(cfg.get("lang_code", "a")),
                        use_icons=use_icons
                    )
                    scene.slide_title = title
                    scene.slide_content = content
                    scene.slide_icon = icon
                    self.log_msg.emit(f"  Slide {scene.number}: {title[:40]}... (icon: {icon})", "INFO")

        # ── Steps 3–4: Process scenes ──
        audio_engine = AudioEngine(voice=cfg["voice"], speed=cfg["speed"],
                                    lang_code=cfg.get("lang_code", "a"))
        pexels_key = cfg.get("pexels_key", "")
        temp_dir = tempfile.mkdtemp(prefix="freevi_")
        final_paths = []
        used_video_urls: set[str] = set()
        slide_dir = ""

        if visual_source not in (VISUAL_PEXELS, VISUAL_PEXELS_IMAGES):
            slide_dir = os.path.join(temp_dir, "slides")
            os.makedirs(slide_dir, exist_ok=True)

        total_scenes = len(script_scenes)
        base_progress = 20
        progress_per_scene = 65 // total_scenes

        for scene in script_scenes:
            if self._cancel:
                return

            num = scene.number
            self.scene_started.emit(num, total_scenes, scene.narrator_text[:80])
            self.progress.emit(
                base_progress + (num - 1) * progress_per_scene,
                f"Scene {num}/{total_scenes}: generating audio...",
            )
            self.log_msg.emit(
                f"── Scene {num}/{total_scenes}: [{scene.video_query}]", "INFO"
            )

            # Audio
            audio_path = os.path.join(temp_dir, f"scene_{num:02d}_audio.wav")
            duration, subtitle_timings = audio_engine.generate_audio(scene.narrator_text, audio_path)
            scene.audio_path = audio_path
            scene.audio_duration = duration
            scene.subtitle_timings = subtitle_timings
            self.log_msg.emit(f"  Audio: {duration:.2f}s, {len(subtitle_timings)} subtitle(s)", "INFO")
            if self._cancel:
                return

            # Visual content — per-scene type in JSON mode, global in PDF mode
            if cfg.get("input_mode") == "json" and visual_source in (VISUAL_PEXELS, VISUAL_PEXELS_IMAGES):
                use_image = getattr(scene, "image", False)
                scene_vs = VISUAL_PEXELS_IMAGES if use_image else VISUAL_PEXELS
            else:
                scene_vs = visual_source

            if scene_vs == VISUAL_PEXELS:
                self._process_pexels_scene_gui(
                    scene, duration, pexels_key, cfg, temp_dir, used_video_urls
                )
            elif scene_vs == VISUAL_PEXELS_IMAGES:
                self._process_pexels_image_scene_gui(
                    scene, pexels_key, cfg, temp_dir, used_video_urls
                )
            elif scene_vs == VISUAL_SLIDES_SVG:
                self._process_slide_svg_scene_gui(
                    scene, duration, cfg, slide_dir, slide_theme
                )
            else:
                self._process_slide_simple_scene_gui(
                    scene, duration, slide_dir, slide_theme
                )

            if self._cancel:
                return

            # Fit visual + mix audio in a single encode pass
            self.progress.emit(
                base_progress + (num - 1) * progress_per_scene + progress_per_scene // 2,
                f"Scene {num}/{total_scenes}: encoding video + audio...",
            )
            scene_path = os.path.join(temp_dir, f"scene_{num:02d}_final.mp4")

            subtitle_position = cfg.get("subtitle_position")
            has_subtitles = subtitle_position and subtitle_timings

            if scene_vs == VISUAL_PEXELS:
                assemble_scene_from_raw(
                    scene.video_path, audio_path, duration, scene_path,
                    subtitle_timings=has_subtitles and subtitle_timings,
                    subtitle_position=subtitle_position,
                )
            elif scene_vs == VISUAL_PEXELS_IMAGES:
                assemble_image_scene(
                    scene.image_path, audio_path, duration, scene_path, True,
                    subtitle_timings=has_subtitles and subtitle_timings,
                    subtitle_position=subtitle_position,
                )
            else:
                assemble_slide_scene(
                    scene.slide_image_path, audio_path, duration, scene_path,
                    subtitle_timings=has_subtitles and subtitle_timings,
                    subtitle_position=subtitle_position,
                )

            final_paths.append(scene_path)
            self.log_msg.emit(f"  Scene {num} complete.", "INFO")

        # ── Step 5: Concatenate ──
        self.progress.emit(88, "Concatenating scenes...")
        self.log_msg.emit("Concatenating all scenes...", "INFO")

        output_path = cfg["output"]

        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)

        if len(final_paths) == 1:
            shutil.copy2(final_paths[0], output_path)
        else:
            concatenate_scenes(final_paths, output_path)

        self.progress.emit(100, "Video generated!")
        self.log_msg.emit(f"Final video: {output_path}", "INFO")
        self.finished.emit(output_path)

    def _process_pexels_scene_gui(self, scene, duration, pexels_key, cfg, temp_dir, used_video_urls):
        """Downloads Pexels video for a scene in GUI worker."""
        from freevi import search_and_download_video
        import freevi
        from moviepy import ColorClip

        raw_path = os.path.join(temp_dir, f"scene_{scene.number:02d}_raw.mp4")
        success = search_and_download_video(
            scene.video_query,
            pexels_key,
            raw_path,
            orientation=cfg.get("orientation", "landscape"),
            target_duration=duration,
            used_urls=used_video_urls,
        )
        if not success:
            self.log_msg.emit(
                f"  Pexels failed for '{scene.video_query}', using black fallback",
                "WARNING",
            )
            clip = ColorClip(
                size=(freevi.TARGET_WIDTH, freevi.TARGET_HEIGHT),
                color=(0, 0, 0),
                duration=duration,
            )
            clip = clip.with_fps(freevi.TARGET_FPS)
            clip.write_videofile(raw_path, codec="libx264", audio=False, logger=None)
            clip.close()
        scene.video_path = raw_path

    def _process_pexels_image_scene_gui(self, scene, pexels_key, cfg, temp_dir, used_video_urls):
        """Downloads Pexels image for a scene in GUI worker."""
        from freevi import search_and_download_image
        import freevi
        from moviepy import ColorClip

        raw_path = os.path.join(temp_dir, f"scene_{scene.number:02d}_image.jpg")
        success = search_and_download_image(
            scene.video_query,
            pexels_key,
            raw_path,
            orientation=cfg.get("orientation", "landscape"),
            used_urls=used_video_urls,
        )
        if not success:
            self.log_msg.emit(
                f"  Pexels failed for '{scene.video_query}', using placeholder",
                "WARNING",
            )
            clip = ColorClip(
                size=(freevi.TARGET_WIDTH, freevi.TARGET_HEIGHT),
                color=(30, 30, 50),
            )
            clip = clip.with_duration(10).with_fps(freevi.TARGET_FPS)
            clip.write_videofile(raw_path, codec="libx264", audio=False, logger=None)
            clip.close()
        scene.image_path = raw_path

    def _process_slide_simple_scene_gui(self, scene, duration, slide_dir, slide_theme):
        """Renders a slide with icon for a scene in GUI worker."""
        from freevi import render_slide_image
        from icon_manager import load_and_recolor_icon
        self.log_msg.emit(f"  Rendering slide with icon...", "INFO")

        icon_svg = None
        if scene.slide_icon and scene.slide_icon != "none":
            icon_svg = load_and_recolor_icon(
                scene.slide_icon, slide_theme["accent_primary"]
            )
            if icon_svg:
                self.log_msg.emit(f"  Using icon: {scene.slide_icon}", "INFO")

        scene.slide_image_path = render_slide_image(
            scene, slide_theme, slide_dir, 
            svg_illustration=None, icon_svg=icon_svg
        )

    def _process_slide_svg_scene_gui(self, scene, duration, cfg, slide_dir, slide_theme):
        """Renders a slide with SVG illustration and icon for a scene in GUI worker."""
        from freevi import render_slide_image
        from slide_svg_generator import generate_svg_illustration
        from icon_manager import load_and_recolor_icon
        self.log_msg.emit(f"  Generating SVG illustration...", "INFO")
        svg_illustration = generate_svg_illustration(
            scene_text=scene.narrator_text,
            llm_model=cfg["model"],
            color_primary=slide_theme["background"],
            color_secondary=slide_theme["accent_primary"],
            color_accent=slide_theme["accent_secondary"],
        )

        icon_svg = None
        if scene.slide_icon and scene.slide_icon != "none":
            icon_svg = load_and_recolor_icon(
                scene.slide_icon, slide_theme["accent_secondary"]
            )
            if icon_svg:
                self.log_msg.emit(f"  Using icon: {scene.slide_icon}", "INFO")

        scene.slide_image_path = render_slide_image(
            scene, slide_theme, slide_dir, 
            svg_illustration=svg_illustration, icon_svg=icon_svg
        )


# ---------------------------------------------------------------------------
# Configuration panel with GroupBoxes
# ---------------------------------------------------------------------------
class ConfigPanel(QWidget):
    """Left panel with all configuration selectors."""

    def __init__(self, ollama_models: list[str], kokoro_voices: list[str]):
        super().__init__()
        self._models = ollama_models
        self._all_voices = kokoro_voices
        self._pdf_text: str = ""
        self._n_chunks: int = 0
        self._json_path: str = ""
        self._json_scenes: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 0. Input Mode Selector ──
        grp_mode = QGroupBox("Input")
        grp_mode.setObjectName("groupbox")
        lay_mode = QHBoxLayout(grp_mode)
        lay_mode.setSpacing(10)

        self.radio_pdf = QRadioButton("From PDF")
        self.radio_json = QRadioButton("From JSON")
        self.radio_pdf.setChecked(True)
        self.radio_pdf.toggled.connect(self._set_input_mode)

        lay_mode.addWidget(self.radio_pdf)
        lay_mode.addWidget(self.radio_json)
        layout.addWidget(grp_mode)

        # ── 1. PDF Input (visible only in PDF mode) ──
        self.grp_pdf = QGroupBox("PDF File")
        self.grp_pdf.setObjectName("groupbox")
        lay_pdf = QGridLayout(self.grp_pdf)
        lay_pdf.setSpacing(8)

        self.lbl_pdf = QLabel("No file selected")
        self.lbl_pdf.setWordWrap(True)
        self.lbl_pdf.setStyleSheet("color: #888; font-style: italic;")
        btn_pdf = QPushButton("Select PDF")
        btn_pdf.setObjectName("btn_secondary")
        btn_pdf.clicked.connect(self._select_pdf)

        self.lbl_chunks_info = QLabel("")
        self.lbl_chunks_info.setStyleSheet("color: #565f89; font-size: 11px;")
        self.lbl_chunks_info.setWordWrap(True)

        lay_pdf.addWidget(QLabel("PDF file:"), 0, 0)
        lay_pdf.addWidget(btn_pdf, 0, 1)
        lay_pdf.addWidget(self.lbl_pdf, 1, 0, 1, 2)
        lay_pdf.addWidget(self.lbl_chunks_info, 2, 0, 1, 2)
        layout.addWidget(self.grp_pdf)

        # ── 1b. JSON Input (visible only in JSON mode) ──
        self.grp_json = QGroupBox("JSON File")
        self.grp_json.setObjectName("groupbox")
        self.grp_json.setVisible(False)
        lay_json = QGridLayout(self.grp_json)
        lay_json.setSpacing(8)

        self.lbl_json = QLabel("No file selected")
        self.lbl_json.setWordWrap(True)
        self.lbl_json.setStyleSheet("color: #888; font-style: italic;")
        btn_json = QPushButton("Select JSON")
        btn_json.setObjectName("btn_secondary")
        btn_json.clicked.connect(self._select_json)

        btn_json_format = QPushButton("View JSON format")
        btn_json_format.setObjectName("btn_secondary")
        btn_json_format.clicked.connect(self._show_json_format)

        lay_json.addWidget(QLabel("JSON file:"), 0, 0)
        lay_json.addWidget(btn_json, 0, 1)
        lay_json.addWidget(self.lbl_json, 1, 0, 1, 2)
        lay_json.addWidget(btn_json_format, 2, 0, 1, 2)
        layout.addWidget(self.grp_json)

        # ── 2. LLM model (hidden in JSON mode) ──
        self.grp_llm = QGroupBox("Language Model (LLM)")
        self.grp_llm.setObjectName("groupbox")
        lay_llm = QGridLayout(self.grp_llm)
        lay_llm.setSpacing(8)

        self.combo_model = QComboBox()
        self.combo_model.addItems(self._models if self._models else ["(no models found)"])
        self.combo_model.setToolTip("Ollama models installed on this system")

        self.spin_max_scenes = QSpinBox()
        self.spin_max_scenes.setRange(1, 500)
        self.spin_max_scenes.setSingleStep(1)
        self.spin_max_scenes.setValue(8)
        self.spin_max_scenes.setToolTip("Maximum number of scenes the LLM will generate")

        self.spin_chunk_size = QSpinBox()
        self.spin_chunk_size.setRange(512, 32768)
        self.spin_chunk_size.setSingleStep(512)
        self.spin_chunk_size.setValue(4096)
        self.spin_chunk_size.setToolTip(
            "How many tokens of PDF text to send to the LLM per call.\n"
            "The actual Ollama context window is derived automatically\n"
            "from this value (chunk + output budget + overhead).\n"
            "Increase if the PDF is long; decrease to save VRAM/RAM.\n"
            "Common values: 2048, 4096, 8192"
        )

        self.lbl_context_info = QLabel("")
        self.lbl_context_info.setStyleSheet("color: #565f89; font-size: 11px;")
        self.lbl_context_info.setWordWrap(True)

        self.combo_model.currentTextChanged.connect(self._update_context_info)
        self.spin_chunk_size.valueChanged.connect(self._update_context_info)
        self.spin_max_scenes.valueChanged.connect(self._update_context_info)
        self.spin_chunk_size.valueChanged.connect(self._recompute_chunks)

        lay_llm.addWidget(QLabel("Model:"), 0, 0)
        lay_llm.addWidget(self.combo_model, 0, 1)
        lay_llm.addWidget(QLabel("Max scenes:"), 1, 0)
        lay_llm.addWidget(self.spin_max_scenes, 1, 1)
        lay_llm.addWidget(QLabel("Chunk size (tokens):"), 2, 0)
        lay_llm.addWidget(self.spin_chunk_size, 2, 1)
        lay_llm.addWidget(self.lbl_context_info, 3, 0, 1, 2)
        layout.addWidget(self.grp_llm)

        self._update_context_info()

        # ── 3. Language & Voice (Kokoro) ──
        grp_voice = QGroupBox("Language & Voice (Kokoro 1.0)")
        grp_voice.setObjectName("groupbox")
        lay_voice = QGridLayout(grp_voice)
        lay_voice.setSpacing(8)

        # Language selector — drives which voices are shown
        self.combo_language = QComboBox()
        from freevi import KOKORO_LANGUAGES
        self._lang_data = KOKORO_LANGUAGES  # keep reference
        for code, info in KOKORO_LANGUAGES.items():
            self.combo_language.addItem(info["label"], userData=code)
        self.combo_language.setToolTip(
            "Select the narration language.\n"
            "The LLM will be forced to write narrator text in this language.\n"
            "The voice list will update to show only matching voices."
        )
        # Default to Spanish (lang_code "e")
        idx_es = self.combo_language.findData("e")
        if idx_es >= 0:
            self.combo_language.setCurrentIndex(idx_es)

        # Voice selector — filtered by language
        self.combo_voice = QComboBox()
        self.combo_voice.setToolTip("Select a Kokoro voice for narration")

        # Wire: when language changes, repopulate voices
        self.combo_language.currentIndexChanged.connect(self._on_language_changed)
        # Populate voices for the initial language selection
        self._on_language_changed()

        self.slider_speed = QSlider(Qt.Orientation.Horizontal)
        self.slider_speed.setRange(50, 200)
        self.slider_speed.setValue(100)
        self.slider_speed.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_speed.setTickInterval(25)
        self.lbl_speed_val = QLabel("1.00×")
        self.lbl_speed_val.setFixedWidth(40)
        self.slider_speed.valueChanged.connect(
            lambda v: self.lbl_speed_val.setText(f"{v/100:.2f}×")
        )

        lay_voice.addWidget(QLabel("Language:"), 0, 0)
        lay_voice.addWidget(self.combo_language, 0, 1, 1, 2)
        lay_voice.addWidget(QLabel("Voice:"), 1, 0)
        lay_voice.addWidget(self.combo_voice, 1, 1, 1, 2)
        lay_voice.addWidget(QLabel("Speed:"), 2, 0)
        lay_voice.addWidget(self.slider_speed, 2, 1)
        lay_voice.addWidget(self.lbl_speed_val, 2, 2)
        layout.addWidget(grp_voice)

        # ── 4. Video ──
        grp_video = QGroupBox("Video")
        grp_video.setObjectName("groupbox")
        lay_video = QGridLayout(grp_video)
        lay_video.setSpacing(8)

        self.combo_res = QComboBox()
        self.combo_res.addItems([
            "1920×1080 (Full HD)",
            "1080×1920 (Full HD Vertical)",
            "1280×720 (HD)",
            "720×1280 (HD Vertical)",
            "3840×2160 (4K)",
        ])
        self.combo_res.setToolTip("Output video resolution")

        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["24 fps", "30 fps", "60 fps"])
        self.combo_fps.setToolTip("Output video frames per second")

        self.combo_preset = QComboBox()
        for _label, _value in [
            ("Ultrafast — fastest encoding, largest file", "ultrafast"),
            ("Very Fast",                                  "veryfast"),
            ("Fast",                                       "fast"),
            ("Medium — balanced (default)",                "medium"),
            ("Slow — best compression, smallest file",     "slow"),
        ]:
            self.combo_preset.addItem(_label, userData=_value)
        self.combo_preset.setCurrentIndex(3)   # default: Medium
        self.combo_preset.setToolTip(
            "x264 encoding preset — controls the trade-off between encoding\n"
            "speed and output file size. Visual quality stays the same across\n"
            "all presets at the same bitrate; only speed and file size differ.\n"
            "\n"
            "  Ultrafast  — encodes in seconds; file ~40% larger than Medium.\n"
            "               Good for quick previews or testing.\n"
            "  Very Fast  — still much faster than Medium; file ~20% larger.\n"
            "  Fast       — good daily-use balance; file ~10% larger.\n"
            "  Medium     — ffmpeg/x264 default. Recommended for final output.\n"
            "  Slow       — takes noticeably longer; file ~10–15% smaller.\n"
            "               Worth it only if storage space is a concern.\n"
            "\n"
            "Tip: use Ultrafast or Fast while iterating, then switch to\n"
            "Medium or Slow for the final render."
        )

        self.combo_orientation = QComboBox()
        self.combo_orientation.addItems(["landscape", "portrait", "square"])
        self.combo_orientation.setToolTip("Preferred orientation when searching videos on Pexels")

        self._res_landscape = {
            "1920×1080 (Full HD)": (1920, 1080),
            "1280×720 (HD)": (1280, 720),
            "3840×2160 (4K)": (3840, 2160),
        }
        self._res_portrait = {
            "1080×1920 (Full HD Vertical)": (1080, 1920),
            "720×1280 (HD Vertical)": (720, 1280),
        }
        self._res_square = {
            "1080×1080 (Square HD)": (1080, 1080),
            "720×720 (Square)": (720, 720),
        }
        self._current_res_map = self._res_landscape
        self.combo_orientation.currentIndexChanged.connect(self._on_orientation_changed)

        lay_video.addWidget(QLabel("Resolution:"), 0, 0)
        lay_video.addWidget(self.combo_res, 0, 1)
        lay_video.addWidget(QLabel("FPS:"), 1, 0)
        lay_video.addWidget(self.combo_fps, 1, 1)
        lay_video.addWidget(QLabel("Codec preset:"), 2, 0)
        lay_video.addWidget(self.combo_preset, 2, 1)
        lay_video.addWidget(QLabel("Orientation:"), 3, 0)
        lay_video.addWidget(self.combo_orientation, 3, 1)
        layout.addWidget(grp_video)

        # ── 4b. Visual Source ──
        self.grp_visual = QGroupBox("Visual Source")
        self.grp_visual.setObjectName("groupbox")
        lay_visual = QGridLayout(self.grp_visual)
        lay_visual.setSpacing(8)

        self.combo_visual_source = QComboBox()
        self.combo_visual_source.addItems([
            "Pexels (Stock Videos)",
            "Pexels (Stock Images)",
            "Slides (Simple)",
            "Slides (with AI SVG)",
        ])
        self.combo_visual_source.setToolTip(
            "Choose the visual source for the video:\n"
            "  - Pexels (Stock Videos): Downloaded stock video clips\n"
            "  - Pexels (Stock Images): Photos with Ken Burns zoom effect\n"
            "  - Slides (Simple): AI-generated slides with text only\n"
            "  - Slides (with AI SVG): AI-generated slides with SVG illustrations"
        )
        self.combo_visual_source.currentIndexChanged.connect(self._on_visual_source_changed)

        self.combo_slide_theme = QComboBox()
        self.combo_slide_theme.addItems(["Tokyo Night", "Executive", "Minimal"])
        self.combo_slide_theme.setToolTip("Visual theme for AI-generated slides")
        self.combo_slide_theme.setVisible(False)

        self.lbl_theme_warning = QLabel(
            "Note: Slides with SVG takes longer (extra LLM call per scene)"
        )
        self.lbl_theme_warning.setStyleSheet("color: #565f89; font-size: 11px;")
        self.lbl_theme_warning.setVisible(False)

        lay_visual.addWidget(QLabel("Visual type:"), 0, 0)
        lay_visual.addWidget(self.combo_visual_source, 0, 1)
        lay_visual.addWidget(QLabel("Slide theme:"), 1, 0)
        lay_visual.addWidget(self.combo_slide_theme, 1, 1)
        lay_visual.addWidget(self.lbl_theme_warning, 2, 0, 1, 2)
        layout.addWidget(self.grp_visual)

        # ── Subtitles ──
        grp_subtitles = QGroupBox("Subtitles")
        grp_subtitles.setObjectName("groupbox")
        lay_subtitles = QGridLayout(grp_subtitles)
        lay_subtitles.setSpacing(8)

        self.combo_subtitles = QComboBox()
        self.combo_subtitles.addItems(["None", "Bottom", "Middle", "Top"])
        self.combo_subtitles.setToolTip(
            "Add subtitles to the video:\n"
            "  - None: No subtitles\n"
            "  - Bottom: Subtitles at the bottom\n"
            "  - Middle: Subtitles in the center\n"
            "  - Top: Subtitles at the top"
        )

        lay_subtitles.addWidget(QLabel("Position:"), 0, 0)
        lay_subtitles.addWidget(self.combo_subtitles, 0, 1)
        layout.addWidget(grp_subtitles)

        # ── 5. API Keys ──
        grp_api = QGroupBox("API Keys")
        grp_api.setObjectName("groupbox")
        lay_api = QGridLayout(grp_api)
        lay_api.setSpacing(8)

        self.edit_pexels = QLineEdit()
        self.edit_pexels.setPlaceholderText("Paste your Pexels key here...")
        self.edit_pexels.setEchoMode(QLineEdit.EchoMode.Password)
        # Load from environment variable if set
        env_key = os.environ.get("PEXELS_API_KEY", "")
        if env_key:
            self.edit_pexels.setText(env_key)

        btn_toggle_key = QPushButton("Show")
        btn_toggle_key.setObjectName("btn_secondary")
        btn_toggle_key.setFixedWidth(70)
        btn_toggle_key.setCheckable(True)
        btn_toggle_key.toggled.connect(
            lambda checked: (
                self.edit_pexels.setEchoMode(
                    QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
                ),
                btn_toggle_key.setText("Hide" if checked else "Show"),
            )
        )

        lbl_pexels_info = QLabel(
            '<a href="https://www.pexels.com/api/" style="color:#7aa2f7;">Get your free key at pexels.com/api</a>'
        )
        lbl_pexels_info.setOpenExternalLinks(True)

        lay_api.addWidget(QLabel("Pexels API Key:"), 0, 0)
        lay_api.addWidget(self.edit_pexels, 0, 1)
        lay_api.addWidget(btn_toggle_key, 0, 2)
        lay_api.addWidget(lbl_pexels_info, 1, 0, 1, 3)
        layout.addWidget(grp_api)

        # ── 6. Output ──
        grp_output = QGroupBox("Output File")
        grp_output.setObjectName("groupbox")
        lay_output = QGridLayout(grp_output)
        lay_output.setSpacing(8)

        self.edit_output = QLineEdit("output/video_final.mp4")
        btn_output = QPushButton("Save as...")
        btn_output.setObjectName("btn_secondary")
        btn_output.clicked.connect(self._select_output)

        self.chk_open_when_done = QCheckBox("Open video when done")
        self.chk_open_when_done.setChecked(True)

        lay_output.addWidget(QLabel("Path:"), 0, 0)
        lay_output.addWidget(self.edit_output, 0, 1)
        lay_output.addWidget(btn_output, 0, 2)
        lay_output.addWidget(self.chk_open_when_done, 1, 0, 1, 3)
        layout.addWidget(grp_output)

        # ── 7. Prompt customization (hidden in JSON mode) ──
        self.grp_prompt = QGroupBox("Narrator Instructions (AI Prompt)")
        self.grp_prompt.setObjectName("groupbox")
        lay_prompt = QVBoxLayout(self.grp_prompt)
        lay_prompt.setSpacing(6)

        lbl_prompt_info = QLabel(
            "These instructions tell the AI how to write the narration "
            "(language, tone, length, etc.).\n"
            "The JSON output format is enforced automatically — you cannot break it."
        )
        lbl_prompt_info.setWordWrap(True)
        lbl_prompt_info.setStyleSheet("color: #565f89; font-size: 11px;")

        self.txt_prompt = QTextEdit()
        self.txt_prompt.setPlaceholderText(
            "Leave empty to use the built-in default.\n\n"
            "Example:\n"
            "- Narrate in English, formal tone (50-70 words per scene).\n"
            "- Focus on practical applications.\n"
            "- Keep technical terms but explain them briefly."
        )
        self.txt_prompt.setMinimumHeight(110)
        self.txt_prompt.setMaximumHeight(200)
        self.txt_prompt.setFont(QFont("Monospace", 9))

        btn_reset_prompt = QPushButton("Reset to default")
        btn_reset_prompt.setObjectName("btn_secondary")
        btn_reset_prompt.setFixedWidth(130)
        btn_reset_prompt.setToolTip("Restore the built-in narrator instructions")
        btn_reset_prompt.clicked.connect(self._reset_prompt)

        lay_prompt.addWidget(lbl_prompt_info)
        lay_prompt.addWidget(self.txt_prompt)
        lay_prompt.addWidget(btn_reset_prompt, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.grp_prompt)

        layout.addStretch()

    def _set_input_mode(self):
        if self.radio_pdf.isChecked():
            self.grp_pdf.setVisible(True)
            self.grp_json.setVisible(False)
            self.grp_llm.setVisible(True)
            self.grp_prompt.setVisible(True)
            self.grp_visual.setVisible(True)
        else:
            self.grp_pdf.setVisible(False)
            self.grp_json.setVisible(True)
            self.grp_llm.setVisible(False)
            self.grp_prompt.setVisible(False)
            self.grp_visual.setVisible(False)

    def _select_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select JSON file", "", "JSON files (*.json);;All files (*)"
        )
        if path:
            from freevi import load_scenes_from_json
            scenes, error = load_scenes_from_json(path)
            if error:
                self.lbl_json.setText(f"Error: {error}")
                self.lbl_json.setStyleSheet("color: #f7768e; font-style: normal;")
            else:
                self.lbl_json.setText(f"{len(scenes)} scenes loaded from:\n{path}")
                self.lbl_json.setStyleSheet("color: #9ece6a; font-style: normal;")
                self._json_path = path
                self._json_scenes = scenes

    def _show_json_format(self):
        dlg = JsonFormatDialog(self)
        dlg.exec()

    def _reset_prompt(self):
        self.txt_prompt.clear()

    def _update_context_info(self, *_args):
        """
        Recalculates the auto-derived Ollama context window and updates
        lbl_context_info.  Called whenever combo_model or spin_chunk_size changes.
        """
        try:
            from freevi import (
                _calculate_context_size,
                _is_thinking_model,
            )
            model = self.combo_model.currentText()
            chunk = self.spin_chunk_size.value()
            thinking = _is_thinking_model(model)
            # Use 1 scene per chunk as the minimum; gives a conservative estimate
            max_scenes = self.spin_max_scenes.value()
            ctx = _calculate_context_size(chunk, max(1, max_scenes), thinking)
            thinking_note = "  (thinking model: +2048 overhead)" if thinking else ""
            self.lbl_context_info.setText(
                f"→ Ollama context window: {ctx:,} tokens{thinking_note}"
            )
        except Exception:
            self.lbl_context_info.setText("")

    def _on_language_changed(self):
        """Repopulates the voice combo when the user selects a different language."""
        lang_code = self.combo_language.currentData()
        if lang_code is None:
            return
        lang_info = self._lang_data.get(lang_code, {})
        voices = lang_info.get("voices", [])

        self.combo_voice.blockSignals(True)
        self.combo_voice.clear()
        if voices:
            self.combo_voice.addItems(voices)
        else:
            self.combo_voice.addItems(self._all_voices)
        self.combo_voice.blockSignals(False)

    def _on_orientation_changed(self):
        """Updates resolution options based on selected orientation."""
        orientation = self.combo_orientation.currentText()
        if orientation == "portrait":
            self._current_res_map = self._res_portrait
            res_items = list(self._res_portrait.keys())
        elif orientation == "square":
            self._current_res_map = self._res_square
            res_items = list(self._res_square.keys())
        else:
            self._current_res_map = self._res_landscape
            res_items = list(self._res_landscape.keys())

        self.combo_res.blockSignals(True)
        current = self.combo_res.currentText()
        self.combo_res.clear()
        self.combo_res.addItems(res_items)
        if current in self._current_res_map:
            self.combo_res.setCurrentText(current)
        else:
            self.combo_res.setCurrentIndex(0)
        self.combo_res.blockSignals(False)

    def _select_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "", "PDF files (*.pdf)"
        )
        if path:
            self.lbl_pdf.setText(path)
            self.lbl_pdf.setStyleSheet("color: #c0caf5;")
            self.lbl_pdf.setToolTip(path)
            self._load_pdf_and_recompute(path)

    def _load_pdf_and_recompute(self, path: str) -> None:
        """
        Extracts text from *path* (showing a progress dialog while doing so),
        caches it in self._pdf_text, then calls _recompute_chunks().
        """
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import Qt

        dlg = QProgressDialog("Reading PDF…", None, 0, 0, self)
        dlg.setWindowTitle("Please wait")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        QApplication.processEvents()

        try:
            from freevi import extract_pdf_text
            self._pdf_text = extract_pdf_text(path)
        except Exception as exc:
            self._pdf_text = ""
            self._n_chunks = 0
            self.lbl_chunks_info.setText(f"Could not read PDF: {exc}")
        finally:
            dlg.close()

        if self._pdf_text:
            self._recompute_chunks()

    def _recompute_chunks(self, *_args) -> None:
        """
        Recalculates n_chunks from the cached PDF text and the current chunk
        size, then updates spin_max_scenes (minimum, step, and value).

        Rules applied to spin_max_scenes:
          • minimum  = n_chunks          (at least 1 scene per chunk)
          • singleStep = n_chunks        (values grow in steps of n_chunks)
          • value is rounded UP to the nearest multiple of n_chunks if it
            is not already valid; otherwise it is left unchanged.

        Does nothing if no PDF has been loaded yet.
        """
        if not self._pdf_text:
            return

        try:
            from freevi import count_chunks
            chunk_tokens = self.spin_chunk_size.value()
            n = count_chunks(self._pdf_text, chunk_tokens)
        except Exception:
            return

        self._n_chunks = n

        # Update the info label in the Input group
        self.lbl_chunks_info.setText(
            f"→ {n} chunk(s) with {chunk_tokens:,} tokens/chunk"
        )

        # Block signals so setting min/step/value doesn't trigger _recompute_chunks again
        self.spin_max_scenes.blockSignals(True)
        self.spin_max_scenes.setMinimum(n)
        self.spin_max_scenes.setMaximum(max(500, n * 50))
        self.spin_max_scenes.setSingleStep(n)

        current = self.spin_max_scenes.value()
        # Round current value UP to the nearest multiple of n
        if current < n:
            self.spin_max_scenes.setValue(n)
        elif current % n != 0:
            self.spin_max_scenes.setValue(current + (n - current % n))
        # else: already a valid multiple — leave it as-is

        self.spin_max_scenes.blockSignals(False)

        # Refresh context info label (depends on max_scenes which may have changed)
        self._update_context_info()

    def _select_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save video as", "output/video_final.mp4", "MP4 Video (*.mp4)"
        )
        if path:
            self.edit_output.setText(path)

    def _on_visual_source_changed(self, index: int):
        is_slides = index in (2, 3)
        self.combo_slide_theme.setVisible(is_slides)
        self.lbl_theme_warning.setVisible(index == 3)

    def get_config(self) -> dict | None:
        """Validates and returns the current configuration. Returns None on errors."""
        if self.radio_pdf.isChecked():
            pdf = self.lbl_pdf.text()
            if pdf == "No file selected" or not Path(pdf).exists():
                return None
            input_path = pdf
        else:
            if not self._json_path:
                return None
            input_path = self._json_path

        visual_source_map = {
            0: "pexels",
            1: "pexels_images",
            2: "slides_simple",
            3: "slides_svg",
        }
        visual_source = visual_source_map.get(self.combo_visual_source.currentIndex(), "pexels")

        theme_map = {
            "Tokyo Night": "tokyo_night",
            "Executive": "executive",
            "Minimal": "minimal",
        }
        slide_theme = theme_map.get(self.combo_slide_theme.currentText(), "tokyo_night")

        pexels_key = self.edit_pexels.text().strip()
        if visual_source in ("pexels", "pexels_images") and not pexels_key:
            return None

        fps_map = {"24 fps": 24, "30 fps": 30, "60 fps": 60}

        return {
            "pdf_path": input_path,
            "model": self.combo_model.currentText(),
            "lang_code": self.combo_language.currentData() or "a",
            "voice": self.combo_voice.currentText(),
            "speed": self.slider_speed.value() / 100.0,
            "max_scenes": self.spin_max_scenes.value(),
            "chunk_size": self.spin_chunk_size.value(),
            "resolution": self._current_res_map[self.combo_res.currentText()],
            "fps": fps_map[self.combo_fps.currentText()],
            "preset": self.combo_preset.currentData() or "medium",
            "orientation": self.combo_orientation.currentText(),
            "pexels_key": pexels_key,
            "output": self.edit_output.text(),
            "open_when_done": self.chk_open_when_done.isChecked(),
            "custom_instructions": self.txt_prompt.toPlainText(),
            "visual_source": visual_source,
            "slide_theme": slide_theme,
            "subtitle_position": {0: None, 1: "bottom", 2: "middle", 3: "top"}.get(self.combo_subtitles.currentIndex()),
            "input_mode": "json" if self.radio_json.isChecked() else "pdf",
        }

    def load_from_config(self, cfg: dict) -> None:
        """Restores all widgets from a previously saved user config dict."""
        # LLM model
        idx = self.combo_model.findText(cfg.get("model", ""))
        if idx >= 0:
            self.combo_model.setCurrentIndex(idx)

        # Max scenes
        self.spin_max_scenes.setValue(int(cfg.get("max_scenes", 8)))

        # Chunk size
        self.spin_chunk_size.setValue(int(cfg.get("chunk_size", 4096)))

        # Language — must be set BEFORE voice so _on_language_changed populates
        # the correct voice list first
        lang_code = cfg.get("lang_code", "e")
        idx = self.combo_language.findData(lang_code)
        if idx >= 0:
            self.combo_language.setCurrentIndex(idx)
        # _on_language_changed fires automatically via signal; but if the index
        # was already at that position it won't fire, so call it explicitly
        self._on_language_changed()

        # Voice (now the combo is populated for the correct language)
        idx = self.combo_voice.findText(cfg.get("voice", ""))
        if idx >= 0:
            self.combo_voice.setCurrentIndex(idx)

        # Speed (stored as int 50-200)
        self.slider_speed.setValue(int(cfg.get("speed", 100)))

        # Orientation — must be set BEFORE resolution so _on_orientation_changed
        # populates the correct resolution list first
        idx = self.combo_orientation.findText(cfg.get("orientation", ""))
        if idx >= 0:
            self.combo_orientation.setCurrentIndex(idx)
        self._on_orientation_changed()

        # Resolution (now the combo has the correct options for the orientation)
        idx = self.combo_res.findText(cfg.get("resolution", ""))
        if idx >= 0:
            self.combo_res.setCurrentIndex(idx)

        # FPS
        idx = self.combo_fps.findText(cfg.get("fps", ""))
        if idx >= 0:
            self.combo_fps.setCurrentIndex(idx)

        # Codec preset — items use userData (raw value) so we search by data
        preset_val = cfg.get("preset", "medium")
        for i in range(self.combo_preset.count()):
            if self.combo_preset.itemData(i) == preset_val:
                self.combo_preset.setCurrentIndex(i)
                break

        # Output path
        if cfg.get("output"):
            self.edit_output.setText(cfg["output"])

        # Open when done
        self.chk_open_when_done.setChecked(bool(cfg.get("open_when_done", True)))

        # Custom prompt instructions
        self.txt_prompt.setPlainText(cfg.get("custom_instructions", ""))

        # Visual source
        visual_source_map = {"pexels": 0, "slides_simple": 1, "slides_svg": 2}
        idx = visual_source_map.get(cfg.get("visual_source", "pexels"), 0)
        self.combo_visual_source.setCurrentIndex(idx)
        self._on_visual_source_changed(idx)

        # Slide theme
        theme_name_map = {"tokyo_night": "Tokyo Night", "executive": "Executive", "minimal": "Minimal"}
        theme_name = theme_name_map.get(cfg.get("slide_theme", "tokyo_night"), "Tokyo Night")
        idx = self.combo_slide_theme.findText(theme_name)
        if idx >= 0:
            self.combo_slide_theme.setCurrentIndex(idx)

        # Subtitles position
        subtitle_pos_map = {None: 0, "bottom": 1, "middle": 2, "top": 3}
        idx = subtitle_pos_map.get(cfg.get("subtitle_position"), 0)
        self.combo_subtitles.setCurrentIndex(idx)

        # Input mode (PDF/JSON)
        if cfg.get("input_mode") == "json":
            self.radio_json.setChecked(True)
        else:
            self.radio_pdf.setChecked(True)
        self._set_input_mode()

    def validation_errors(self) -> list[str]:
        """Returns a list of validation errors."""
        errors = []

        if self.radio_pdf.isChecked():
            pdf = self.lbl_pdf.text()
            if pdf == "No file selected":
                errors.append("No PDF file has been selected.")
            elif not Path(pdf).exists():
                errors.append(f"PDF file does not exist: {pdf}")
        else:
            if not self._json_path:
                errors.append("No JSON file has been selected.")

        visual_source_idx = self.combo_visual_source.currentIndex()
        if visual_source_idx in (0, 1) and not self.edit_pexels.text().strip():
            errors.append("Pexels API Key is required when using Pexels.")

        if not self.edit_output.text().strip():
            errors.append("Specify an output path for the video.")

        return errors


# ---------------------------------------------------------------------------
# Progress and log panel
# ---------------------------------------------------------------------------
class ProgressPanel(QWidget):
    """Right panel with progress bar, scene status and log."""

    def __init__(self):
        super().__init__()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Overall progress ──
        grp_prog = QGroupBox("Progress")
        grp_prog.setObjectName("groupbox")
        lay_prog = QVBoxLayout(grp_prog)
        lay_prog.setSpacing(6)

        self.lbl_status = QLabel("Ready — select input and press Generate")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            "color: #58a6ff; font-weight: 700; font-size: 12px; letter-spacing: 0.3px;"
        )

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(22)

        self.lbl_scene = QLabel("")
        self.lbl_scene.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_scene.setStyleSheet("color: #7dcfff; font-style: italic;")
        self.lbl_scene.setWordWrap(True)

        lay_prog.addWidget(self.lbl_status)
        lay_prog.addWidget(self.progress_bar)
        lay_prog.addWidget(self.lbl_scene)
        layout.addWidget(grp_prog)

        # ── Log ──
        grp_log = QGroupBox("Execution log")
        grp_log.setObjectName("groupbox")
        lay_log = QVBoxLayout(grp_log)
        lay_log.setSpacing(4)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Monospace", 9))
        self.txt_log.setMinimumHeight(300)

        btn_clear = QPushButton("Clear log")
        btn_clear.setObjectName("btn_secondary")
        btn_clear.setFixedWidth(100)
        btn_clear.clicked.connect(self.txt_log.clear)

        lay_log.addWidget(self.txt_log)
        lay_log.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(grp_log, stretch=1)

    def update_progress(self, percentage: int, description: str):
        self.progress_bar.setValue(percentage)
        self.lbl_status.setText(description)

    def update_scene(self, num: int, total: int, preview: str):
        self.lbl_scene.setText(f"Scene {num}/{total}: \"{preview}...\"")

    def append_log(self, message: str, level: str):
        colors = {
            "INFO": "#c9d1d9",
            "WARNING": "#d29922",
            "ERROR": "#f85149",
            "DEBUG": "#484f58",
        }
        color = colors.get(level, "#c9d1d9")
        if level in ("WARNING", "ERROR"):
            prefix_color = colors[level]
            html = f'<span style="color:{prefix_color}; font-weight:bold;">[{level}]</span> <span style="color:{color};">{message}</span>'
        else:
            html = f'<span style="color:{color};">{message}</span>'

        self.txt_log.append(html)
        # Auto-scroll to bottom
        cursor = self.txt_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.txt_log.setTextCursor(cursor)

    def reset(self):
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Ready — select input and press Generate")
        self.lbl_scene.setText("")
        self.txt_log.clear()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._worker: PipelineWorker | None = None
        self._final_video_path = None
        self._log_handler = QtLogHandler()
        self._log_handler.log_signal.connect(self._receive_log)
        self._connect_logging()

        self._models = self._get_ollama_models()
        self._voices = self._get_kokoro_voices()

        self._build_ui()
        self._apply_styles()
        self.setWindowTitle("FreeVi — PDF Video Generator")
        self.setWindowIcon(QIcon("logo.svg"))
        self.setMinimumSize(1050, 700)
        self.resize(1200, 800)

        # Restore last-used configuration
        import user_config as _uc
        self._uc = _uc
        self.panel_config.load_from_config(_uc.load())

    # ── Initialization helpers ──

    def _connect_logging(self):
        """Connects the Qt handler to the freevi root logger."""
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("freevi").addHandler(self._log_handler)
        logging.getLogger("freevi").setLevel(logging.DEBUG)

    def _get_ollama_models(self) -> list[str]:
        try:
            import ollama
            result = ollama.list()
            names = []
            for m in result.models:
                name = m.model if hasattr(m, "model") else str(m)
                names.append(name)
            return names if names else ["qwen3"]
        except Exception:
            return ["qwen3", "llama2", "mistral"]

    def _get_kokoro_voices(self) -> list[str]:
        try:
            from kokoro_onnx import Kokoro
            base = Path(__file__).parent / "kokoro-v1.0"
            onnx = base / "kokoro-v1.0.onnx"
            voices = base / "voices-v1.0.bin"
            if onnx.exists() and voices.exists():
                k = Kokoro(str(onnx), str(voices))
                return k.get_voices()
        except Exception:
            pass
        # Fallback with known voice list
        return [
            "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
            "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
            "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
            "am_michael", "am_onyx", "am_puck", "am_santa",
            "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
            "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
            "ef_dora", "em_alex", "em_santa",
            "ff_siwis",
            "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
            "if_sara", "im_nicola",
            "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
            "pf_dora", "pm_alex", "pm_santa",
            "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
            "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
        ]

    # ── UI construction ──

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setSpacing(0)
        root_layout.setContentsMargins(16, 16, 16, 16)

        # ── Header ──
        header = self._create_header()
        root_layout.addWidget(header)
        root_layout.addSpacing(12)

        # ── Main splitter (config | progress) ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)

        # Left panel: configuration with scroll
        scroll_config = QScrollArea()
        scroll_config.setWidgetResizable(True)
        scroll_config.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_config.setMinimumWidth(380)

        self.panel_config = ConfigPanel(self._models, self._voices)
        scroll_config.setWidget(self.panel_config)
        splitter.addWidget(scroll_config)

        # Right panel: progress and logs
        self.panel_progress = ProgressPanel()
        splitter.addWidget(self.panel_progress)
        splitter.setSizes([420, 680])

        root_layout.addWidget(splitter, stretch=1)
        root_layout.addSpacing(12)

        # ── Action buttons ──
        self.button_bar = self._create_button_bar()
        root_layout.addWidget(self.button_bar)

        # ── Status bar ──
        self.status = QStatusBar()
        self.status.showMessage("Ready.")
        self.setStatusBar(self.status)

    def _create_header(self) -> QWidget:
        w = QFrame()
        w.setObjectName("header")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)

        accent_bar = QFrame()
        accent_bar.setFixedWidth(3)
        accent_bar.setFixedHeight(28)
        accent_bar.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            "stop:0 #1f6feb, stop:1 #58a6ff); "
            "border-radius: 2px;"
        )
        lay.addWidget(accent_bar)
        lay.addSpacing(10)

        lbl_title = QLabel("FreeVi")
        lbl_title.setObjectName("titulo")
        lbl_subtitle = QLabel("AI Video Generator")
        lbl_subtitle.setObjectName("subtitulo")

        lay.addWidget(lbl_title)
        lay.addSpacing(8)
        lay.addWidget(lbl_subtitle)
        lay.addStretch()

        self.lbl_ollama_status = self._badge("OLLAMA", self._check_ollama())
        self.lbl_kokoro_status = self._badge("KOKORO", self._check_kokoro())
        lay.addWidget(self.lbl_ollama_status)
        lay.addSpacing(4)
        lay.addWidget(self.lbl_kokoro_status)

        return w

    def _badge(self, text: str, ok: bool) -> QLabel:
        color = "#3fb950" if ok else "#f85149"
        sign = "●" if ok else "○"
        lbl = QLabel(f"{sign} {text}")
        lbl.setStyleSheet(
            f"color: {color}; background: #161b22; border: 1px solid #30363d;"
            f"border-radius: 4px; padding: 2px 8px; font-size: 10px; font-weight: 700;"
            f"letter-spacing: 0.5px;"
        )
        return lbl

    def _check_ollama(self) -> bool:
        try:
            import ollama
            ollama.list()
            return True
        except Exception:
            return False

    def _check_kokoro(self) -> bool:
        base = Path(__file__).parent / "kokoro-v1.0"
        return (base / "kokoro-v1.0.onnx").exists() and (base / "voices-v1.0.bin").exists()

    def _create_button_bar(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.btn_start = QPushButton("Generate Video")
        self.btn_start.setObjectName("btn_primary")
        self.btn_start.setFixedHeight(42)
        self.btn_start.clicked.connect(self._start_pipeline)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btn_danger")
        self.btn_cancel.setFixedHeight(42)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_pipeline)

        self.btn_open_result = QPushButton("Open Video")
        self.btn_open_result.setObjectName("btn_secondary")
        self.btn_open_result.setFixedHeight(42)
        self.btn_open_result.setEnabled(False)
        self.btn_open_result.clicked.connect(self._open_video)

        lay.addStretch()
        lay.addWidget(self.btn_open_result)
        lay.addWidget(self.btn_cancel)
        lay.addWidget(self.btn_start)

        return w

    # ── Styles (Tokyo Night) ──

    def _apply_styles(self):
        self.setStyleSheet("""
        /* ── Main background ────────────────────────────────────────────── */
        QMainWindow, QWidget {
            background-color: #0d1117;
            color: #c9d1d9;
            font-family: "Segoe UI Variable", "Segoe UI", "Noto Sans", sans-serif;
            font-size: 12px;
            outline: none;
        }

        /* ── Header — cinema control bar ────────────────────────────────── */
        QFrame#header {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #0d1117, stop:0.5 #131929, stop:1 #0d1117);
            border-bottom: 1px solid #21262d;
            border-radius: 0px;
        }
        QFrame#header::outer {
            background: #0d1117;
        }
        QLabel#titulo {
            font-size: 20px;
            font-weight: 800;
            color: #58a6ff;
            letter-spacing: -0.5px;
        }
        QLabel#subtitulo {
            font-size: 11px;
            color: #484f58;
            letter-spacing: 0.5px;
        }

        /* ── Input mode selector — segmented pill ─────────────────────────── */
        QRadioButton {
            background: transparent;
            color: #8b949e;
            border-radius: 6px;
            padding: 6px 14px;
            font-size: 12px;
            font-weight: 500;
            spacing: 0px;
        }
        QRadioButton::indicator {
            width: 0px;
            height: 0px;
            border-radius: 0px;
            border: none;
            background: none;
        }
        QRadioButton:hover {
            color: #c9d1d9;
            background: #161b22;
        }
        QRadioButton:checked {
            color: #58a6ff;
            background: #1c2128;
            border: 1px solid #30363d;
            font-weight: 600;
        }

        /* ── GroupBoxes — cinema panel ────────────────────────────────────── */
        QGroupBox {
            border: 1px solid #21262d;
            border-radius: 8px;
            margin-top: 12px;
            padding: 14px 12px 10px;
            background-color: #0d1117;
            /* subtle left glow accent */
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 12px;
            padding: 0 8px;
            color: #58a6ff;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.8px;
            text-transform: uppercase;
        }

        /* ── Labels ──────────────────────────────────────────────────────── */
        QLabel {
            color: #c9d1d9;
        }

        /* ── Inputs ──────────────────────────────────────────────────────── */
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 6px 10px;
            color: #c9d1d9;
            selection-background-color: #1f6feb;
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 1px solid #58a6ff;
            background-color: #0d1117;
        }
        QLineEdit:disabled, QComboBox:disabled {
            color: #484f58;
            background-color: #161b22;
            border-color: #21262d;
        }
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        QComboBox::down-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid #8b949e;
            margin-right: 6px;
        }
        QComboBox QAbstractItemView {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            selection-background-color: #1f6feb;
            color: #c9d1d9;
            padding: 4px;
        }

        /* ── Slider ──────────────────────────────────────────────────────── */
        QSlider::groove:horizontal {
            height: 4px;
            background: #21262d;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #58a6ff;
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 7px;
            border: 2px solid #0d1117;
        }
        QSlider::handle:horizontal:hover {
            background: #79c0ff;
        }
        QSlider::sub-page:horizontal {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #1f6feb, stop:1 #58a6ff);
            border-radius: 2px;
        }

        /* ── CheckBox ───────────────────────────────────────────────────── */
        QCheckBox {
            color: #c9d1d9;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid #30363d;
            background: #161b22;
        }
        QCheckBox::indicator:hover {
            border-color: #58a6ff;
        }
        QCheckBox::indicator:checked {
            background: #1f6feb;
            border-color: #1f6feb;
            image: none;
            border-radius: 4px;
        }
        QCheckBox::indicator:checked:after {
            content: "";
        }

        /* ── Progress Bar — cinematic ────────────────────────────────────── */
        QProgressBar {
            border: 1px solid #30363d;
            border-radius: 6px;
            text-align: center;
            background-color: #161b22;
            color: #c9d1d9;
            font-weight: 600;
            font-size: 11px;
            min-height: 20px;
        }
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #1f6feb, stop:0.5 #58a6ff, stop:1 #79c0ff);
            border-radius: 5px;
        }

        /* ── TextEdit (log) — terminal ──────────────────────────────────── */
        QTextEdit {
            background-color: #161b22;
            border: 1px solid #21262d;
            border-radius: 6px;
            color: #c9d1d9;
            font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", "Consolas", monospace;
            font-size: 11px;
            line-height: 1.5;
        }
        QTextEdit:focus {
            border: 1px solid #30363d;
        }

        /* ── Table ───────────────────────────────────────────────────────── */
        QTableWidget {
            background-color: #161b22;
            border: 1px solid #21262d;
            border-radius: 6px;
            color: #c9d1d9;
            gridline-color: #21262d;
            font-size: 12px;
        }
        QTableWidget::item {
            padding: 4px 6px;
        }
        QTableWidget::item:selected {
            background-color: #1f6feb;
            color: #ffffff;
        }
        QHeaderView::section {
            background-color: #0d1117;
            color: #8b949e;
            border: none;
            border-bottom: 1px solid #21262d;
            padding: 6px;
            font-weight: 600;
            font-size: 11px;
        }

        /* ── ScrollArea ────────────────────────────────────────────────── */
        QScrollArea {
            border: none;
            background: transparent;
        }
        QScrollBar:vertical {
            background: transparent;
            width: 6px;
            border-radius: 3px;
            margin: 3px 0;
        }
        QScrollBar::handle:vertical {
            background: #30363d;
            border-radius: 3px;
            min-height: 24px;
        }
        QScrollBar::handle:vertical:hover {
            background: #484f58;
        }
        QScrollBar::handle:vertical:pressed {
            background: #58a6ff;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 6px;
            border-radius: 3px;
            margin: 0 3px;
        }
        QScrollBar::handle:horizontal {
            background: #30363d;
            border-radius: 3px;
            min-width: 24px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #484f58;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }

        /* ── Splitter ───────────────────────────────────────────────────── */
        QSplitter::handle {
            background: #21262d;
            width: 1px;
        }
        QSplitter::handle:hover {
            background: #30363d;
        }

        /* ── Primary button — cinematic CTA ─────────────────────────────── */
        QPushButton#btn_primary {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #1f6feb, stop:1 #1158c7);
            color: #ffffff;
            border: 1px solid #1f6feb;
            border-radius: 8px;
            padding: 10px 28px;
            font-weight: 700;
            font-size: 13px;
            min-width: 140px;
            letter-spacing: 0.3px;
        }
        QPushButton#btn_primary:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #388bfd, stop:1 #1f6feb);
            border-color: #388bfd;
        }
        QPushButton#btn_primary:pressed {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #0d419d, stop:1 #1158c7);
            padding-top: 11px;
            padding-bottom: 9px;
        }
        QPushButton#btn_primary:disabled {
            background: #21262d;
            color: #484f58;
            border-color: #30363d;
        }

        /* ── Secondary button ─────────────────────────────────────────────── */
        QPushButton#btn_secondary {
            background-color: #161b22;
            color: #58a6ff;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 6px 16px;
            font-weight: 500;
        }
        QPushButton#btn_secondary:hover {
            background-color: #1c2128;
            border-color: #58a6ff;
            color: #79c0ff;
        }
        QPushButton#btn_secondary:pressed {
            background-color: #161b22;
        }

        /* ── Danger button ───────────────────────────────────────────────── */
        QPushButton#btn_danger {
            background-color: #161b22;
            color: #f85149;
            border: 1px solid #f8514922;
            border-radius: 6px;
            padding: 6px 16px;
            font-weight: 600;
            font-size: 12px;
            min-width: 90px;
        }
        QPushButton#btn_danger:hover {
            background-color: #f8514915;
            border-color: #f85149;
        }
        QPushButton#btn_danger:disabled {
            color: #484f58;
            border-color: #21262d;
        }

        /* ── Status bar ─────────────────────────────────────────────────── */
        QStatusBar {
            background: #0d1117;
            color: #484f58;
            font-size: 11px;
            border-top: 1px solid #21262d;
        }
        QStatusBar::item {
            border: none;
        }

        /* ── Tooltip ─────────────────────────────────────────────────────── */
        QToolTip {
            background-color: #161b22;
            color: #c9d1d9;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 11px;
        }

        /* ── Dialog ─────────────────────────────────────────────────────── */
        QDialog {
            background-color: #0d1117;
        }

        /* ── Text browser (JSON modal) ──────────────────────────────────── */
        QTextEdit[readOnly="true"] {
            background-color: #161b22;
            border: 1px solid #21262d;
            border-radius: 6px;
        }

        /* ── Menu ───────────────────────────────────────────────────────── */
        QMenu {
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 4px;
        }
        QMenu::item {
            padding: 6px 12px;
            border-radius: 4px;
            color: #c9d1d9;
        }
        QMenu::item:selected {
            background-color: #1f6feb;
        }
        """)

    # ── Pipeline control slots ──

    def _start_pipeline(self):
        errors = self.panel_config.validation_errors()
        if errors:
            QMessageBox.warning(
                self,
                "Incomplete configuration",
                "Please fix the following errors:\n\n• " + "\n• ".join(errors),
            )
            return

        config = self.panel_config.get_config()
        if config is None:
            return

        # JSON mode: require a loaded JSON file
        if self.panel_config.radio_json.isChecked():
            if not getattr(self.panel_config, "_json_path", None):
                QMessageBox.warning(self, "No JSON loaded", "Please select a JSON file first.")
                return
            config["input_mode"] = "json"
            config["preloaded_scenes"] = self.panel_config._json_scenes
        else:
            config["input_mode"] = "pdf"

        # Apply video config to the freevi module before running
        self._apply_video_config(config)

        # Persist configuration so it is restored on next launch
        self._uc.save(config)

        # Reset UI
        self.panel_progress.reset()
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_open_result.setEnabled(False)
        self._final_video_path = None
        self.status.showMessage("Pipeline running...")

        # Create and launch worker
        self._worker = PipelineWorker(config)
        self._worker.progress.connect(self.panel_progress.update_progress)
        self._worker.log_msg.connect(self.panel_progress.append_log)
        self._worker.scene_started.connect(self.panel_progress.update_scene)
        self._worker.script_ready.connect(self._show_review_dialog)
        self._worker.finished.connect(self._pipeline_finished)
        self._worker.error.connect(self._pipeline_error)
        self._worker.start()

    def _show_review_dialog(self, scenes: list) -> None:
        """
        Slot called from the main thread when the worker emits script_ready.
        Shows ScriptReviewDialog; on accept resumes the worker with the
        (possibly edited) queries; on reject cancels the pipeline.
        """
        self.status.showMessage("Waiting for script review...")
        self.panel_progress.append_log(
            f"Script ready ({len(scenes)} scenes) — review and edit queries before download.",
            "INFO",
        )

        dlg = ScriptReviewDialog(scenes, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            queries = dlg.get_queries()
            self.panel_progress.append_log("Script approved — continuing pipeline.", "INFO")
            self.status.showMessage("Pipeline running...")
            self._worker.resume(queries)
        else:
            self.panel_progress.append_log("Pipeline cancelled by user at review step.", "WARNING")
            self.status.showMessage("Pipeline cancelled.")
            self._worker.cancel()
            self.btn_start.setEnabled(True)
            self.btn_cancel.setEnabled(False)

    def _apply_video_config(self, config: dict):
        """Applies video options (resolution, fps, preset) to the freevi module."""
        try:
            import freevi
            w, h = config["resolution"]
            freevi.TARGET_WIDTH = w
            freevi.TARGET_HEIGHT = h
            freevi.TARGET_FPS = config["fps"]
            freevi.TARGET_PRESET = config["preset"]
        except Exception:
            pass

    def _cancel_pipeline(self):
        if self._worker:
            self._worker.cancel()
            self.panel_progress.append_log("Cancelling pipeline...", "WARNING")
            self.btn_cancel.setEnabled(False)
            self.status.showMessage("Cancelling...")

    def _pipeline_finished(self, path: str):
        self._final_video_path = path
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_open_result.setEnabled(True)
        self.panel_progress.update_progress(100, "Video generated successfully!")
        self.status.showMessage(f"Done → {path}")

        config = self.panel_config.get_config()
        if config and config.get("open_when_done"):
            self._open_video()

        QMessageBox.information(
            self,
            "Video generated!",
            f"The video has been generated successfully:\n\n{path}",
        )

    def _pipeline_error(self, message: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.panel_progress.append_log(f"FATAL ERROR: {message}", "ERROR")
        self.panel_progress.update_progress(
            self.panel_progress.progress_bar.value(), "Error during execution"
        )
        self.status.showMessage("Error during execution.")
        QMessageBox.critical(self, "Pipeline error", message)

    def _receive_log(self, message: str, level: str):
        self.panel_progress.append_log(message, level)

    def _open_video(self):
        if not self._final_video_path:
            return
        path = Path(self._final_video_path)
        if not path.exists():
            QMessageBox.warning(self, "File not found", str(path))
            return
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            os.startfile(str(path))

    # ── Clean shutdown ──

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Pipeline running",
                "The pipeline is still running. Are you sure you want to quit?\n"
                "(The process will be cancelled)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            self._worker.cancel()
            self._worker.wait(3000)
        # Persist current settings before closing
        self._uc.save_from_panel(self.panel_config)
        # Detach the Qt log handler before Qt destroys the C++ objects,
        # so Python's logging atexit hook doesn't crash on a dangling pointer.
        logging.getLogger("freevi").removeHandler(self._log_handler)
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    # Required on some Linux systems with Wayland
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setApplicationName("FreeVi")
    app.setApplicationDisplayName("FreeVi")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
