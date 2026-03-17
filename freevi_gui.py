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
            VideoGenerator,
            assemble_scene_from_raw,
            concatenate_scenes,
            extract_pdf_text,
            generate_script,
            get_language_label,
            search_and_download_video,
        )
        import tempfile, shutil, os

        cfg = self.config

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

        total_chunks: list[int] = []  # will be populated by on_progress callback

        def _on_script_progress(done: int, total: int) -> None:
            """Relays chunk-level progress (15 % → 30 %) to the GUI."""
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

        # ── Step 2b: Pause for user review of video queries ──
        # Emit the scene list to the main thread, then block until the user
        # confirms (or cancels) the ScriptReviewDialog.
        self.progress.emit(18, "Waiting for script review...")
        self._mutex.lock()
        self._reviewed_queries = None
        self.script_ready.emit(script.scenes)   # triggers dialog in main thread
        self._wait.wait(self._mutex)             # blocks here until resume() or cancel()
        reviewed = self._reviewed_queries
        self._mutex.unlock()

        if self._cancel or reviewed is None:
            return

        # Apply the (possibly edited) queries back to the scenes
        for scene, new_query in zip(script.scenes, reviewed):
            q = new_query.strip()
            if q:
                scene.video_query = " ".join(q.split()[:5])

        # ── Steps 3–4: Process scenes ──
        audio_engine = AudioEngine(voice=cfg["voice"], speed=cfg["speed"],
                                    lang_code=cfg.get("lang_code", "a"))
        pexels_key = cfg["pexels_key"]
        temp_dir = tempfile.mkdtemp(prefix="freevi_")
        final_paths = []
        used_video_urls: set[str] = set()   # prevents reusing the same Pexels clip

        total_scenes = len(script.scenes)
        base_progress = 20
        progress_per_scene = 65 // total_scenes

        for scene in script.scenes:
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
            duration = audio_engine.generate_audio(scene.narrator_text, audio_path)
            scene.audio_path = audio_path
            scene.audio_duration = duration
            self.log_msg.emit(f"  Audio: {duration:.2f}s", "INFO")
            if self._cancel:
                return

            # Stock video
            self.progress.emit(
                base_progress + (num - 1) * progress_per_scene + progress_per_scene // 2,
                f"Scene {num}/{total_scenes}: downloading video...",
            )
            raw_path = os.path.join(temp_dir, f"scene_{num:02d}_raw.mp4")
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
                import freevi
                from moviepy import ColorClip
                clip = ColorClip(
                    size=(freevi.TARGET_WIDTH, freevi.TARGET_HEIGHT),
                    color=(0, 0, 0),
                    duration=duration,
                )
                clip = clip.with_fps(freevi.TARGET_FPS)
                clip.write_videofile(raw_path, codec="libx264", audio=False, logger=None)
                clip.close()
            scene.video_path = raw_path
            if self._cancel:
                return

            # Fit video + mix audio in a single encode pass
            self.progress.emit(
                base_progress + (num - 1) * progress_per_scene + progress_per_scene // 2,
                f"Scene {num}/{total_scenes}: encoding video + audio...",
            )
            scene_path = os.path.join(temp_dir, f"scene_{num:02d}_final.mp4")
            assemble_scene_from_raw(raw_path, audio_path, duration, scene_path)
            final_paths.append(scene_path)
            self.log_msg.emit(f"  Scene {num} complete.", "INFO")

        # ── Step 5: Concatenate ──
        self.progress.emit(88, "Concatenating scenes...")
        self.log_msg.emit("Concatenating all scenes...", "INFO")

        output_path = cfg["output"]

        # Ensure output directory exists
        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)

        if len(final_paths) == 1:
            shutil.copy2(final_paths[0], output_path)
        else:
            concatenate_scenes(final_paths, output_path)

        self.progress.emit(100, "Video generated!")
        self.log_msg.emit(f"Final video: {output_path}", "INFO")
        self.finished.emit(output_path)


# ---------------------------------------------------------------------------
# Configuration panel with GroupBoxes
# ---------------------------------------------------------------------------
class ConfigPanel(QWidget):
    """Left panel with all configuration selectors."""

    def __init__(self, ollama_models: list[str], kokoro_voices: list[str]):
        super().__init__()
        self._models = ollama_models
        self._all_voices = kokoro_voices  # full flat list (used as fallback)
        self._pdf_text: str = ""          # cached text from the last loaded PDF
        self._n_chunks: int = 0           # last computed chunk count (0 = no PDF)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 1. Input ──
        grp_input = QGroupBox("Input")
        grp_input.setObjectName("groupbox")
        lay_input = QGridLayout(grp_input)
        lay_input.setSpacing(8)

        self.lbl_pdf = QLabel("No file selected")
        self.lbl_pdf.setWordWrap(True)
        self.lbl_pdf.setStyleSheet("color: #888; font-style: italic;")
        btn_pdf = QPushButton("Select PDF")
        btn_pdf.setObjectName("btn_secondary")
        btn_pdf.clicked.connect(self._select_pdf)

        self.lbl_chunks_info = QLabel("")
        self.lbl_chunks_info.setStyleSheet("color: #565f89; font-size: 11px;")
        self.lbl_chunks_info.setWordWrap(True)

        lay_input.addWidget(QLabel("PDF file:"), 0, 0)
        lay_input.addWidget(btn_pdf, 0, 1)
        lay_input.addWidget(self.lbl_pdf, 1, 0, 1, 2)
        lay_input.addWidget(self.lbl_chunks_info, 2, 0, 1, 2)
        layout.addWidget(grp_input)

        # ── 2. LLM model ──
        grp_llm = QGroupBox("Language Model (LLM)")
        grp_llm.setObjectName("groupbox")
        lay_llm = QGridLayout(grp_llm)
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

        # Live info label: shows the auto-calculated Ollama context window
        self.lbl_context_info = QLabel("")
        self.lbl_context_info.setStyleSheet("color: #565f89; font-size: 11px;")
        self.lbl_context_info.setWordWrap(True)

        # Update context info label whenever model or chunk size changes
        self.combo_model.currentTextChanged.connect(self._update_context_info)
        self.spin_chunk_size.valueChanged.connect(self._update_context_info)
        self.spin_max_scenes.valueChanged.connect(self._update_context_info)

        # Recompute n_chunks (and update max_scenes spinner) when chunk size changes
        self.spin_chunk_size.valueChanged.connect(self._recompute_chunks)

        lay_llm.addWidget(QLabel("Model:"), 0, 0)
        lay_llm.addWidget(self.combo_model, 0, 1)
        lay_llm.addWidget(QLabel("Max scenes:"), 1, 0)
        lay_llm.addWidget(self.spin_max_scenes, 1, 1)
        lay_llm.addWidget(QLabel("Chunk size (tokens):"), 2, 0)
        lay_llm.addWidget(self.spin_chunk_size, 2, 1)
        lay_llm.addWidget(self.lbl_context_info, 3, 0, 1, 2)
        layout.addWidget(grp_llm)

        # Populate the info label with the initial values
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
        self.combo_res.addItems(["1920×1080 (Full HD)", "1280×720 (HD)", "3840×2160 (4K)"])
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

        lay_video.addWidget(QLabel("Resolution:"), 0, 0)
        lay_video.addWidget(self.combo_res, 0, 1)
        lay_video.addWidget(QLabel("FPS:"), 1, 0)
        lay_video.addWidget(self.combo_fps, 1, 1)
        lay_video.addWidget(QLabel("Codec preset:"), 2, 0)
        lay_video.addWidget(self.combo_preset, 2, 1)
        lay_video.addWidget(QLabel("Orientation:"), 3, 0)
        lay_video.addWidget(self.combo_orientation, 3, 1)
        layout.addWidget(grp_video)

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

        # ── 7. Prompt customization ──
        grp_prompt = QGroupBox("Narrator Instructions (AI Prompt)")
        grp_prompt.setObjectName("groupbox")
        lay_prompt = QVBoxLayout(grp_prompt)
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
        layout.addWidget(grp_prompt)

        layout.addStretch()

    def _reset_prompt(self):
        """Clears the prompt editor so the built-in default will be used."""
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
            # Fallback: show all voices (should not happen)
            self.combo_voice.addItems(self._all_voices)
        self.combo_voice.blockSignals(False)

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

    def get_config(self) -> dict | None:
        """Validates and returns the current configuration. Returns None on errors."""
        pdf = self.lbl_pdf.text()
        if pdf == "No file selected" or not Path(pdf).exists():
            return None

        pexels_key = self.edit_pexels.text().strip()
        if not pexels_key:
            return None

        res_map = {
            "1920×1080 (Full HD)": (1920, 1080),
            "1280×720 (HD)": (1280, 720),
            "3840×2160 (4K)": (3840, 2160),
        }
        fps_map = {"24 fps": 24, "30 fps": 30, "60 fps": 60}

        return {
            "pdf_path": pdf,
            "model": self.combo_model.currentText(),
            "lang_code": self.combo_language.currentData() or "a",
            "voice": self.combo_voice.currentText(),
            "speed": self.slider_speed.value() / 100.0,
            "max_scenes": self.spin_max_scenes.value(),
            "chunk_size": self.spin_chunk_size.value(),
            "resolution": res_map[self.combo_res.currentText()],
            "fps": fps_map[self.combo_fps.currentText()],
            "preset": self.combo_preset.currentData() or "medium",
            "orientation": self.combo_orientation.currentText(),
            "pexels_key": pexels_key,
            "output": self.edit_output.text(),
            "open_when_done": self.chk_open_when_done.isChecked(),
            "custom_instructions": self.txt_prompt.toPlainText(),
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

        # Resolution
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

        # Orientation
        idx = self.combo_orientation.findText(cfg.get("orientation", ""))
        if idx >= 0:
            self.combo_orientation.setCurrentIndex(idx)

        # Output path
        if cfg.get("output"):
            self.edit_output.setText(cfg["output"])

        # Open when done
        self.chk_open_when_done.setChecked(bool(cfg.get("open_when_done", True)))

        # Custom prompt instructions
        self.txt_prompt.setPlainText(cfg.get("custom_instructions", ""))

    def validation_errors(self) -> list[str]:
        """Returns a list of validation errors."""
        errors = []
        pdf = self.lbl_pdf.text()
        if pdf == "No file selected":
            errors.append("No PDF file has been selected.")
        elif not Path(pdf).exists():
            errors.append(f"PDF file does not exist: {pdf}")

        if not self.edit_pexels.text().strip():
            errors.append("Pexels API Key is required.")

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

        self.lbl_status = QLabel("Waiting to start...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("font-weight: bold; font-size: 13px;")

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
            "INFO": "#c0caf5",
            "WARNING": "#e0af68",
            "ERROR": "#f7768e",
            "DEBUG": "#565f89",
        }
        color = colors.get(level, "#c0caf5")
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
        self.lbl_status.setText("Waiting to start...")
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
        lay.setContentsMargins(16, 10, 16, 10)

        lbl_title = QLabel("FreeVi")
        lbl_title.setObjectName("titulo")
        lbl_subtitle = QLabel("PDF Video Generator — 100% Local and Free")
        lbl_subtitle.setObjectName("subtitulo")

        lay.addWidget(lbl_title)
        lay.addSpacing(12)
        lay.addWidget(lbl_subtitle)
        lay.addStretch()

        # Dependency status badges
        self.lbl_ollama_status = self._badge("Ollama", self._check_ollama())
        self.lbl_kokoro_status = self._badge("Kokoro", self._check_kokoro())
        lay.addWidget(self.lbl_ollama_status)
        lay.addSpacing(6)
        lay.addWidget(self.lbl_kokoro_status)

        return w

    def _badge(self, text: str, ok: bool) -> QLabel:
        color = "#9ece6a" if ok else "#f7768e"
        sign = "✓" if ok else "✗"
        lbl = QLabel(f"{sign} {text}")
        lbl.setStyleSheet(
            f"color: {color}; background: #1e2030; border-radius: 4px;"
            f"padding: 2px 8px; font-size: 11px; font-weight: bold;"
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
        /* Main background */
        QMainWindow, QWidget {
            background-color: #1a1b2e;
            color: #c0caf5;
            font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif;
            font-size: 12px;
        }

        /* Header */
        QFrame#header {
            background-color: #16213e;
            border-radius: 8px;
            border: 1px solid #2a2c5a;
        }
        QLabel#titulo {
            font-size: 22px;
            font-weight: bold;
            color: #7aa2f7;
        }
        QLabel#subtitulo {
            font-size: 12px;
            color: #565f89;
        }

        /* GroupBoxes */
        QGroupBox {
            border: 1px solid #2a2c5a;
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 8px;
            background-color: #1e2030;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
            color: #7aa2f7;
            font-weight: bold;
            font-size: 11px;
        }

        /* Labels */
        QLabel {
            color: #c0caf5;
        }

        /* Inputs */
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
            background-color: #1e2030;
            border: 1px solid #2a2c5a;
            border-radius: 5px;
            padding: 5px 8px;
            color: #c0caf5;
            selection-background-color: #7aa2f7;
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 1px solid #7aa2f7;
        }
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        QComboBox::down-arrow {
            width: 10px;
            height: 10px;
        }
        QComboBox QAbstractItemView {
            background-color: #1e2030;
            border: 1px solid #2a2c5a;
            selection-background-color: #283457;
            color: #c0caf5;
        }

        /* Slider */
        QSlider::groove:horizontal {
            height: 6px;
            background: #2a2c5a;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #7aa2f7;
            width: 16px;
            height: 16px;
            margin: -5px 0;
            border-radius: 8px;
        }
        QSlider::sub-page:horizontal {
            background: #7aa2f7;
            border-radius: 3px;
        }

        /* CheckBox */
        QCheckBox {
            color: #c0caf5;
            spacing: 6px;
        }
        QCheckBox::indicator {
            width: 15px;
            height: 15px;
            border-radius: 3px;
            border: 1px solid #2a2c5a;
            background: #1e2030;
        }
        QCheckBox::indicator:checked {
            background: #7aa2f7;
            border-color: #7aa2f7;
        }

        /* Progress Bar */
        QProgressBar {
            border: 1px solid #2a2c5a;
            border-radius: 5px;
            text-align: center;
            background-color: #1e2030;
            color: #c0caf5;
            font-weight: bold;
        }
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #7aa2f7, stop:1 #7dcfff);
            border-radius: 4px;
        }

        /* TextEdit (log) */
        QTextEdit {
            background-color: #13141e;
            border: 1px solid #2a2c5a;
            border-radius: 5px;
            color: #c0caf5;
            font-family: "JetBrains Mono", "Fira Code", "Cascadia Code", monospace;
        }

        /* ScrollArea */
        QScrollArea {
            border: none;
            background: transparent;
        }
        QScrollBar:vertical {
            background: #1a1b2e;
            width: 8px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #2a2c5a;
            border-radius: 4px;
            min-height: 20px;
        }
        QScrollBar::handle:vertical:hover {
            background: #7aa2f7;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }

        /* Splitter */
        QSplitter::handle {
            background: #2a2c5a;
            width: 2px;
        }
        QSplitter::handle:hover {
            background: #7aa2f7;
        }

        /* Primary button */
        QPushButton#btn_primary {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #7aa2f7, stop:1 #5a87d6);
            color: #1a1b2e;
            border: none;
            border-radius: 6px;
            padding: 8px 24px;
            font-weight: bold;
            font-size: 13px;
            min-width: 150px;
        }
        QPushButton#btn_primary:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #89b0ff, stop:1 #6a97e6);
        }
        QPushButton#btn_primary:pressed {
            background: #5a87d6;
        }
        QPushButton#btn_primary:disabled {
            background: #2a2c5a;
            color: #565f89;
        }

        /* Secondary button */
        QPushButton#btn_secondary {
            background-color: #1e2030;
            color: #7aa2f7;
            border: 1px solid #2a2c5a;
            border-radius: 5px;
            padding: 5px 14px;
        }
        QPushButton#btn_secondary:hover {
            background-color: #283457;
            border-color: #7aa2f7;
        }
        QPushButton#btn_secondary:pressed {
            background-color: #1e2030;
        }

        /* Danger button */
        QPushButton#btn_danger {
            background-color: #1e2030;
            color: #f7768e;
            border: 1px solid #3d1f2a;
            border-radius: 5px;
            padding: 5px 14px;
            font-size: 12px;
            min-width: 90px;
        }
        QPushButton#btn_danger:hover {
            background-color: #3d1f2a;
            border-color: #f7768e;
        }
        QPushButton#btn_danger:disabled {
            color: #565f89;
            border-color: #2a2c5a;
        }

        /* Status bar */
        QStatusBar {
            background: #13141e;
            color: #565f89;
            font-size: 11px;
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
