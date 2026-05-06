from pathlib import Path

import nltk
from loguru import logger
from pydantic import BaseModel

from youtok.config import settings


class WordToken(BaseModel):
    word: str
    start: float
    end: float


class Sentence(BaseModel):
    id: str
    text: str
    start: float
    end: float
    words: list[WordToken]


class Transcript(BaseModel):
    language: str
    duration_sec: float
    sentences: list[Sentence]

    def find_sentence(self, sid: str) -> Sentence | None:
        for s in self.sentences:
            if s.id == sid:
                return s
        return None

    def sentences_between(self, start_id: str, end_id: str) -> list[Sentence]:
        collecting = False
        result = []
        for s in self.sentences:
            if s.id == start_id:
                collecting = True
            if collecting:
                result.append(s)
            if s.id == end_id:
                break
        return result


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def detect_device() -> tuple[str, str, str]:
    if settings.whisper_device == "cuda" or (
        settings.whisper_device == "auto" and _cuda_available()
    ):
        model = settings.whisper_model if settings.whisper_model != "auto" else "large-v3"
        return "cuda", "float16", model
    model = settings.whisper_model if settings.whisper_model != "auto" else "base"
    return "cpu", "int8", model


def _ensure_nltk_data() -> None:
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        logger.info("Downloading nltk punkt_tab tokenizer...")
        nltk.download("punkt_tab", quiet=True)


def split_sentences(words: list[WordToken]) -> list[Sentence]:
    _ensure_nltk_data()

    if not words:
        return []

    full_text = ""
    char_to_word_idx: list[int] = []
    for i, w in enumerate(words):
        if full_text:
            full_text += " "
            char_to_word_idx.append(i)
        for _ in w.word:
            char_to_word_idx.append(i)
        full_text += w.word

    raw_sentences = nltk.sent_tokenize(full_text)

    sentences: list[Sentence] = []
    search_from = 0
    for i, sent_text in enumerate(raw_sentences):
        pos = full_text.find(sent_text, search_from)
        if pos == -1:
            continue
        end_pos = pos + len(sent_text) - 1

        first_word_idx = char_to_word_idx[pos]
        last_word_idx = char_to_word_idx[min(end_pos, len(char_to_word_idx) - 1)]

        sent_words = words[first_word_idx:last_word_idx + 1]
        if not sent_words:
            continue

        sentences.append(Sentence(
            id=f"S{i + 1:03d}",
            text=sent_text,
            start=sent_words[0].start,
            end=sent_words[-1].end,
            words=sent_words,
        ))
        search_from = pos + len(sent_text)

    return sentences


_whisper_model_cache: dict = {}


def _get_whisper_model(model_name: str, device: str, compute_type: str):
    """Singleton WhisperModel cached per (model, device, compute_type) — avoids reload per job."""
    from faster_whisper import WhisperModel
    key = (model_name, device, compute_type)
    if key not in _whisper_model_cache:
        logger.info(f"Loading WhisperModel: {model_name} ({device}, {compute_type})")
        _whisper_model_cache[key] = WhisperModel(model_name, device=device, compute_type=compute_type)
    return _whisper_model_cache[key]


def transcribe(audio_path: Path, language: str = "en", use_cache: bool = True) -> Transcript:
    if use_cache:
        from youtok.core.cache import load_transcript, save_transcript
        cached = load_transcript(audio_path)
        if cached is not None:
            return cached

    device, compute_type, model_name = detect_device()
    logger.info(f"Transcribe: device={device}, compute_type={compute_type}, model={model_name}")

    if device == "cpu":
        logger.warning("CPU mode: transcription will be slow")

    model = _get_whisper_model(model_name, device, compute_type)
    # Single transcribe pass with word_timestamps=True is enough.
    # Snap window in pipeline is ±2s — faster-whisper word_ts (±100-200ms) is more than precise enough.
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,  # skip long silences
    )

    all_words: list[WordToken] = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                if not w.word.strip():
                    continue
                all_words.append(WordToken(
                    word=w.word.strip(),
                    start=round(w.start, 3),
                    end=round(w.end, 3),
                ))

    sentences = split_sentences(all_words)
    duration = all_words[-1].end if all_words else 0.0

    logger.info(f"Transcribed: {len(all_words)} words, {len(sentences)} sentences, {duration:.1f}s")

    transcript = Transcript(
        language=language,
        duration_sec=duration,
        sentences=sentences,
    )

    if use_cache:
        from youtok.core.cache import save_transcript
        save_transcript(audio_path, transcript)

    return transcript
