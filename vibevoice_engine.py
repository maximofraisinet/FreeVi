import difflib
import logging
import re
import numpy as np
import soundfile as sf
import torch
from pathlib import Path

log = logging.getLogger("freevi")

VIBEVOICE_PROMPTS_PATH = Path(__file__).parent / "vibevoices"

class VibeVoiceEngine:
    """
    Generates TTS audio using Microsoft VibeVoice 0.5B.
    Optimized for RTX 3050 6GB with torch.bfloat16 and flash_attention_2.
    """
    def __init__(
        self,
        voice: str = "sp/sp-Spk4_woman",  # Relative path without extension
        subtitle_max_words: int = 4,
    ):
        self.voice = voice
        self.subtitle_max_words = subtitle_max_words
        self.subtitle_method = "pro"  # Forced to Whisper for VibeVoice
        
        self.model = None
        self.processor = None
        self._whisper_model = None

    def _initialize(self):
        """Loads VibeVoice model strictly following VRAM constraints."""
        if self.model is not None:
            return

        try:
            from vibevoice.modular.modeling_vibevoice_streaming_inference import VibeVoiceStreamingForConditionalGenerationInference
            from vibevoice.processor.vibevoice_streaming_processor import VibeVoiceStreamingProcessor
        except ImportError:
            raise ImportError(
                "VibeVoice is not installed. Please install it using:\n"
                "pip install git+https://github.com/microsoft/VibeVoice.git"
            )

        model_id = "microsoft/VibeVoice-Realtime-0.5B"
        log.info(f"  Loading VibeVoice ({model_id}) with flash_attention_2 and bfloat16...")
        
        # Load processor
        self.processor = VibeVoiceStreamingProcessor.from_pretrained(model_id)
        
        # Load model with critical constraints
        self.model = VibeVoiceStreamingForConditionalGenerationInference.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            device_map="cuda",
            attn_implementation="flash_attention_2"
        )
        self.model.eval()
        
        # Recommended for VibeVoice
        self.model.set_ddpm_inference_steps(num_steps=5)
        log.info("  VibeVoice loaded successfully into VRAM.")

    @staticmethod
    def _clean_tts_text(text: str) -> str:
        """Removes characters that TTS engines read aloud literally."""
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'^\s*#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(
            r'[\U0001F600-\U0001F64F'
            r'\U0001F300-\U0001F5FF'
            r'\U0001F680-\U0001F6FF'
            r'\U0001F700-\U0001F77F'
            r'\U0001F780-\U0001F7FF'
            r'\U0001F800-\U0001F8FF'
            r'\U0001F900-\U0001F9FF'
            r'\U0001FA00-\U0001FA6F'
            r'\U0001FA70-\U0001FAFF'
            r'\U00002600-\U000026FF'
            r'\U00002700-\U000027BF'
            r'\U0000FE00-\U0000FE0F'
            r'\U0001F1E0-\U0001F1FF'
            r']',
            '',
            text,
        )
        text = re.sub(r'  +', ' ', text)
        return text.strip()

    def _synth(self, text: str) -> tuple[np.ndarray, int]:
        """Synthesizes text in a single pass using VibeVoice."""
        voice_path = VIBEVOICE_PROMPTS_PATH / f"{self.voice}.pt"
        if not voice_path.exists():
            raise FileNotFoundError(
                f"Voice prompt file missing: {voice_path}\n"
                f"Please download .pt voice files and place them in {VIBEVOICE_PROMPTS_PATH}"
            )

        cached_prompt = torch.load(voice_path, map_location="cuda", weights_only=False)

        inputs = self.processor.process_input_with_cached_prompt(
            text=text,
            cached_prompt=cached_prompt,
            padding=True,
            return_tensors="pt",
            return_attention_mask=True,
        )

        inputs = {k: v.to("cuda") if torch.is_tensor(v) else v for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=None,
                cfg_scale=2.5,
                tokenizer=self.processor.tokenizer,
                generation_config={'do_sample': False},
                all_prefilled_outputs=cached_prompt,
            )

        if not outputs.speech_outputs or outputs.speech_outputs[0] is None:
            raise RuntimeError("VibeVoice returned empty audio.")

        # VibeVoice returns a bfloat16 tensor, convert to float32 before numpy
        audio_samples = outputs.speech_outputs[0].cpu().float().numpy().squeeze()
        
        # VibeVoice operates natively at 24000 Hz
        return audio_samples, 24000

    def _get_whisper_model(self):
        if self._whisper_model is not None:
            return self._whisper_model

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(
                "faster-whisper is required for VibeVoice subtitle sync. "
                "Install it with: pip install faster-whisper"
            )

        log.info("  Loading Whisper model (tiny, ~75MB, first-time download)...")
        self._whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        log.info("  Whisper model loaded.")
        return self._whisper_model

    def _transcribe_with_whisper(self, audio_path: str) -> list[dict]:
        model = self._get_whisper_model()
        segments, info = model.transcribe(audio_path, word_timestamps=True)

        words = []
        for segment in segments:
            if segment.words:
                for word in segment.words:
                    words.append({
                        "word": word.word.strip(),
                        "start": word.start,
                        "end": word.end,
                    })

        log.info(f"  Whisper detected {len(words)} words")
        return words

    def _align_text_to_whisper(self, original_text: str, whisper_words: list[dict]) -> list[dict]:
        original_words = re.findall(r'\b\w+\S*', original_text.lower())
        original_words_clean = [re.sub(r'[^\w]', '', w) for w in original_words]
        whisper_texts = [re.sub(r'[^\w]', '', w["word"].lower().strip()) for w in whisper_words]

        if not original_words or not whisper_texts:
            log.warning("  No words to align")
            return []

        matcher = difflib.SequenceMatcher(None, whisper_texts, original_words_clean)
        aligned = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for idx in range(i2 - i1):
                    w = whisper_words[i1 + idx]
                    aligned.append({
                        "word": original_words[j1 + idx],
                        "start": w["start"],
                        "end": w["end"],
                    })
            elif tag == "replace":
                replacements = whisper_words[i1:i2]
                originals = original_words[j1:j2]
                if replacements and originals:
                    total_duration = sum(r["end"] - r["start"] for r in replacements)
                    per_word = total_duration / len(originals)
                    for idx, orig_word in enumerate(originals):
                        start = replacements[0]["start"] + (idx * per_word)
                        end = start + per_word
                        aligned.append({
                            "word": orig_word,
                            "start": start,
                            "end": end,
                        })
            elif tag == "insert":
                pass
            elif tag == "delete":
                for orig_word in original_words[j1:j2]:
                    if aligned:
                        aligned.append({
                            "word": orig_word,
                            "start": aligned[-1]["end"],
                            "end": aligned[-1]["end"] + 0.3,
                        })

        return aligned

    def _chunk_words_for_subtitles(self, aligned_words: list[dict], max_duration: float = 2.0) -> list[dict]:
        if not aligned_words:
            return []

        def _clean_text_for_display(text: str) -> str:
            text = re.sub(r'[.,;:!¡¿]', '', text)
            return text.strip()

        chunks = []
        current_chunk = []
        chunk_start = aligned_words[0]["start"]

        for word_data in aligned_words:
            current_chunk.append(word_data)
            chunk_end = word_data["end"]
            chunk_duration = chunk_end - chunk_start

            current_word = word_data["word"]
            ends_with_punct = current_word.endswith(('.', ',', ';', ':', '!', '?', '¿', '¡'))

            should_cut = (
                len(current_chunk) >= self.subtitle_max_words or 
                chunk_duration >= max_duration or 
                ends_with_punct
            )

            if should_cut:
                chunk_text = " ".join(w["word"] for w in current_chunk)
                chunk_text = _clean_text_for_display(chunk_text)
                chunks.append({
                    "text": chunk_text,
                    "start": round(chunk_start, 3),
                    "end": round(chunk_end, 3),
                })
                if word_data != aligned_words[-1]:
                    current_chunk = []
                    chunk_start = word_data["end"]

        if current_chunk:
            chunk_text = " ".join(w["word"] for w in current_chunk)
            chunk_text = _clean_text_for_display(chunk_text)
            chunks.append({
                "text": chunk_text,
                "start": round(chunk_start, 3),
                "end": round(current_chunk[-1]["end"], 3),
            })

        log.info(f"  Created {len(chunks)} subtitle chunks from {len(aligned_words)} words")
        return chunks

    def generate_audio(self, text: str, output_path: str) -> tuple[float, list[dict]]:
        """Generates full scene audio and creates subtitle timings."""
        self._initialize()
        text = self._clean_tts_text(text)
        
        log.info(f"  [Audio] Generating full narration with VibeVoice")
        samples, sample_rate = self._synth(text)
        
        sf.write(output_path, samples, sample_rate)
        duration = len(samples) / sample_rate
        log.info(f"  Audio generated: {duration:.2f}s, transcribing with Whisper...")

        whisper_words = self._transcribe_with_whisper(output_path)
        if not whisper_words:
            log.warning("  Whisper returned no words. Empty subtitles.")
            return duration, []

        aligned_words = self._align_text_to_whisper(text, whisper_words)
        if not aligned_words:
            aligned_words = whisper_words

        timings = self._chunk_words_for_subtitles(aligned_words)
        return duration, timings
