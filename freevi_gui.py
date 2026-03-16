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
    Q_ARG,
    QMetaObject,
    QObject,
    Qt,
    QThread,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
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
# Worker thread to run the pipeline without blocking the UI
# ---------------------------------------------------------------------------
class PipelineWorker(QThread):
    """
    Runs the VideoGenerator pipeline in a separate thread.
    Emits signals to communicate progress, logs and result to the UI.
    """

    # Signals
    progress = pyqtSignal(int, str)            # (percentage, description)
    log_msg = pyqtSignal(str, str)             # (message, level: INFO/WARNING/ERROR)
    scene_started = pyqtSignal(int, int, str)  # (num, total, text_preview)
    finished = pyqtSignal(str)                 # path to the final video
    error = pyqtSignal(str)                    # error message

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._cancel = False

    def cancel(self):
        self._cancel = True

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
            fit_video_to_duration,
            search_and_download_video,
            assemble_scene,
            concatenate_scenes,
            extract_pdf_text,
            generate_script,
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
        script = generate_script(text, cfg["model"], cfg["max_scenes"])
        if self._cancel:
            return
        self.log_msg.emit(f"Script generated: {len(script.scenes)} scenes", "INFO")

        # ── Steps 3–4: Process scenes ──
        audio_engine = AudioEngine(voice=cfg["voice"], speed=cfg["speed"])
        pexels_key = cfg["pexels_key"]
        temp_dir = tempfile.mkdtemp(prefix="freevi_")
        final_paths = []

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
            success = search_and_download_video(scene.video_query, pexels_key, raw_path)
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

            # Fit video to audio duration
            proc_path = os.path.join(temp_dir, f"scene_{num:02d}_processed.mp4")
            fit_video_to_duration(raw_path, duration, proc_path)
            scene.processed_video_path = proc_path

            # Assemble
            scene_path = os.path.join(temp_dir, f"scene_{num:02d}_final.mp4")
            assemble_scene(scene, scene_path)
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
        self._voices = kokoro_voices
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

        lay_input.addWidget(QLabel("PDF file:"), 0, 0)
        lay_input.addWidget(btn_pdf, 0, 1)
        lay_input.addWidget(self.lbl_pdf, 1, 0, 1, 2)
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
        self.spin_max_scenes.setRange(2, 20)
        self.spin_max_scenes.setValue(8)
        self.spin_max_scenes.setToolTip("Maximum number of scenes the LLM will generate")

        lay_llm.addWidget(QLabel("Model:"), 0, 0)
        lay_llm.addWidget(self.combo_model, 0, 1)
        lay_llm.addWidget(QLabel("Max scenes:"), 1, 0)
        lay_llm.addWidget(self.spin_max_scenes, 1, 1)
        layout.addWidget(grp_llm)

        # ── 3. Voice (Kokoro) ──
        grp_voice = QGroupBox("Voice (Kokoro 1.0)")
        grp_voice.setObjectName("groupbox")
        lay_voice = QGridLayout(grp_voice)
        lay_voice.setSpacing(8)

        self.combo_voice = QComboBox()
        self.combo_voice.addItems(self._voices)
        # Select im_nicola by default (Spanish)
        idx_nicola = self.combo_voice.findText("im_nicola")
        if idx_nicola >= 0:
            self.combo_voice.setCurrentIndex(idx_nicola)
        self.combo_voice.setToolTip(
            "Voice prefixes:\n"
            "  a = American English\n"
            "  b = British English\n"
            "  e = Spanish\n"
            "  f = French\n"
            "  h = Hindi\n"
            "  i = Italian\n"
            "  j = Japanese\n"
            "  p = Portuguese\n"
            "  z = Chinese\n"
            "  f/m = female/male"
        )

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

        lay_voice.addWidget(QLabel("Voice:"), 0, 0)
        lay_voice.addWidget(self.combo_voice, 0, 1, 1, 2)
        lay_voice.addWidget(QLabel("Speed:"), 1, 0)
        lay_voice.addWidget(self.slider_speed, 1, 1)
        lay_voice.addWidget(self.lbl_speed_val, 1, 2)
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
        self.combo_preset.addItems(["medium", "fast", "slow", "ultrafast", "veryslow"])
        self.combo_preset.setToolTip(
            "x264 encoding preset:\n"
            "  ultrafast = fastest, largest file\n"
            "  veryslow  = slowest, smallest file"
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

        layout.addStretch()

    def _select_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select PDF", "", "PDF files (*.pdf)"
        )
        if path:
            self.lbl_pdf.setText(path)
            self.lbl_pdf.setStyleSheet("color: #c0caf5;")
            self.lbl_pdf.setToolTip(path)

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
            "voice": self.combo_voice.currentText(),
            "speed": self.slider_speed.value() / 100.0,
            "max_scenes": self.spin_max_scenes.value(),
            "resolution": res_map[self.combo_res.currentText()],
            "fps": fps_map[self.combo_fps.currentText()],
            "preset": self.combo_preset.currentText(),
            "orientation": self.combo_orientation.currentText(),
            "pexels_key": pexels_key,
            "output": self.edit_output.text(),
            "open_when_done": self.chk_open_when_done.isChecked(),
        }

    def load_from_config(self, cfg: dict) -> None:
        """Restores all widgets from a previously saved user config dict."""
        # LLM model
        idx = self.combo_model.findText(cfg.get("model", ""))
        if idx >= 0:
            self.combo_model.setCurrentIndex(idx)

        # Max scenes
        self.spin_max_scenes.setValue(int(cfg.get("max_scenes", 8)))

        # Voice
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

        # Codec preset
        idx = self.combo_preset.findText(cfg.get("preset", ""))
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)

        # Orientation
        idx = self.combo_orientation.findText(cfg.get("orientation", ""))
        if idx >= 0:
            self.combo_orientation.setCurrentIndex(idx)

        # Output path
        if cfg.get("output"):
            self.edit_output.setText(cfg["output"])

        # Open when done
        self.chk_open_when_done.setChecked(bool(cfg.get("open_when_done", True)))

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
        self._worker.finished.connect(self._pipeline_finished)
        self._worker.error.connect(self._pipeline_error)
        self._worker.start()

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
