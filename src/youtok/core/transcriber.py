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


def transcribe(audio_path: Path, language: str = "en") -> Transcript:
    from faster_whisper import WhisperModel

    device, compute_type, model_name = detect_device()
    logger.info(f"WhisperX: device={device}, compute_type={compute_type}, model={model_name}")

    if device == "cpu":
        logger.warning("CPU mode: transcription will be slow (5-10min for 15min video)")

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(str(audio_path), language=language, word_timestamps=True)

    all_words: list[WordToken] = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                all_words.append(WordToken(
                    word=w.word.strip(),
                    start=round(w.start, 3),
                    end=round(w.end, 3),
                ))

    try:
        import whisperx
        import torch

        align_model, align_meta = whisperx.load_align_model(
            language_code=language, device=device
        )
        result_segments = []
        for segment in model.transcribe(str(audio_path), language=language)[0]:
            result_segments.append({
                "text": segment.text,
                "start": segment.start,
                "end": segment.end,
            })

        if result_segments:
            aligned = whisperx.align(
                result_segments, align_model, align_meta,
                str(audio_path), device,
            )
            aligned_words: list[WordToken] = []
            for seg in aligned.get("segments", []):
                for w in seg.get("words", []):
                    if "start" in w and "end" in w:
                        aligned_words.append(WordToken(
                            word=w["word"].strip(),
                            start=round(w["start"], 3),
                            end=round(w["end"], 3),
                        ))
            if aligned_words:
                all_words = aligned_words
                logger.info("Using WhisperX-aligned word timestamps")
    except ImportError:
        logger.info("whisperx not available, using faster-whisper word timestamps")
    except Exception as e:
        logger.warning(f"WhisperX alignment failed, falling back: {e}")

    all_words = [w for w in all_words if w.word]

    sentences = split_sentences(all_words)
    duration = all_words[-1].end if all_words else 0.0

    logger.info(f"Transcribed: {len(all_words)} words, {len(sentences)} sentences, {duration:.1f}s")

    return Transcript(
        language=language,
        duration_sec=duration,
        sentences=sentences,
    )
