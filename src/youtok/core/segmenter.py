from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from loguru import logger
from pydantic import BaseModel

from youtok.config import settings
from youtok.core.transcriber import Transcript
from youtok.llm.client import call_with_tool
from youtok.llm.prompts import (
    STAGE_A_TOOL,
    STAGE_B_TOOL,
    build_stage_a,
    build_stage_b,
)
from youtok.llm.schemas import StageAOutput, StageBOutput, SubTopic


_stage_b_cache: dict[str, "StageBOutput"] = {}
_cache_lock = Lock()


def clear_stage_b_cache() -> None:
    with _cache_lock:
        _stage_b_cache.clear()


class ClipPlan(BaseModel):
    topic_name: str
    parent_topic: str | None
    sentence_range_start: str
    sentence_range_end: str
    start_sec: float
    end_sec: float
    duration_sec: float
    coherence_score: float = 0.0
    warnings: list[str] = []


def _validate_sentence_ids(output: StageAOutput, transcript: Transcript) -> list[str]:
    valid_ids = {s.id for s in transcript.sentences}
    errors = []
    for st in _flatten_all(output.sub_topics):
        if st.start_sentence not in valid_ids:
            errors.append(f"Invalid start_sentence: {st.start_sentence}")
        if st.end_sentence not in valid_ids:
            errors.append(f"Invalid end_sentence: {st.end_sentence}")
    if output.intro_strip:
        if output.intro_strip.start not in valid_ids:
            errors.append(f"Invalid intro_strip start: {output.intro_strip.start}")
        if output.intro_strip.end not in valid_ids:
            errors.append(f"Invalid intro_strip end: {output.intro_strip.end}")
    if output.outro_strip:
        if output.outro_strip.start not in valid_ids:
            errors.append(f"Invalid outro_strip start: {output.outro_strip.start}")
        if output.outro_strip.end not in valid_ids:
            errors.append(f"Invalid outro_strip end: {output.outro_strip.end}")
    return errors


def _flatten_all(sub_topics: list[SubTopic]) -> list[SubTopic]:
    result = []
    for st in sub_topics:
        result.append(st)
        if st.children:
            result.extend(_flatten_all(st.children))
    return result


def _flatten_leaves(sub_topics: list[SubTopic]) -> list[SubTopic]:
    result = []
    for st in sub_topics:
        if st.children:
            result.extend(_flatten_leaves(st.children))
        else:
            result.append(st)
    return result


def _get_duration(st: SubTopic, transcript: Transcript) -> float:
    start_sent = transcript.find_sentence(st.start_sentence)
    end_sent = transcript.find_sentence(st.end_sentence)
    if not start_sent or not end_sent:
        return 0.0
    return end_sent.end - start_sent.start


def _run_stage_a(
    transcript: Transcript,
    title: str,
    stage_label: str = "stage_a",
    job_id: int | None = None,
) -> StageAOutput:
    prompt = build_stage_a(transcript, title)
    raw = call_with_tool(prompt, STAGE_A_TOOL, tier="sonnet", stage=stage_label, job_id=job_id)
    output = StageAOutput.model_validate(raw)

    errors = _validate_sentence_ids(output, transcript)
    if errors:
        logger.warning(f"Stage A validation errors, retrying: {errors}")
        prompt = build_stage_a(transcript, title)
        raw = call_with_tool(
            prompt, STAGE_A_TOOL, tier="sonnet",
            stage=f"{stage_label}_invalid_ids", job_id=job_id,
        )
        output = StageAOutput.model_validate(raw)
        errors = _validate_sentence_ids(output, transcript)
        if errors:
            raise ValueError(f"Stage A still has invalid sentence IDs: {errors}")

    return output


def _run_stage_b_single(
    transcript: Transcript,
    st: SubTopic,
    stage_label: str = "stage_b",
    job_id: int | None = None,
) -> tuple[SubTopic, StageBOutput]:
    cache_key = f"{st.start_sentence}:{st.end_sentence}:{st.parent or '_'}"
    with _cache_lock:
        if cache_key in _stage_b_cache:
            logger.debug(f"Stage B cache hit: {cache_key}")
            return st, _stage_b_cache[cache_key]
    prompt = build_stage_b(
        transcript, st.name, st.parent,
        st.start_sentence, st.end_sentence,
    )
    raw = call_with_tool(
        prompt, STAGE_B_TOOL, tier="haiku",
        stage=stage_label, job_id=job_id,
    )
    output = StageBOutput.model_validate(raw)
    with _cache_lock:
        _stage_b_cache[cache_key] = output
    return st, output


def _apply_adjustment(
    st: SubTopic,
    validation: StageBOutput,
    transcript: Transcript,
) -> list[SubTopic]:
    all_ids = [s.id for s in transcript.sentences]
    start_idx = all_ids.index(st.start_sentence)
    end_idx = all_ids.index(st.end_sentence)

    new_start = max(0, start_idx + validation.start_adjust)
    new_end = min(len(all_ids) - 1, end_idx + validation.end_adjust)

    if validation.internal_break:
        break_start_idx = all_ids.index(validation.internal_break.start)
        first = SubTopic(
            name=f"{st.name} (Part 1)",
            start_sentence=all_ids[new_start],
            end_sentence=all_ids[break_start_idx - 1] if break_start_idx > 0 else all_ids[new_start],
            parent=st.parent,
        )
        second = SubTopic(
            name=f"{st.name} (Part 2)",
            start_sentence=validation.internal_break.start,
            end_sentence=all_ids[new_end],
            parent=st.parent,
        )
        return [first, second]

    st.start_sentence = all_ids[new_start]
    st.end_sentence = all_ids[new_end]
    return [st]


def _merge_subtopics(buffer: list[SubTopic], transcript: Transcript, coherence: float = 0.0) -> ClipPlan:
    start_sent = transcript.find_sentence(buffer[0].start_sentence)
    end_sent = transcript.find_sentence(buffer[-1].end_sentence)
    start_sec = start_sent.start if start_sent else 0.0
    end_sec = end_sent.end if end_sent else 0.0

    names = [st.name for st in buffer]
    topic_name = names[0] if len(names) == 1 else " + ".join(names)

    return ClipPlan(
        topic_name=topic_name,
        parent_topic=buffer[0].parent,
        sentence_range_start=buffer[0].start_sentence,
        sentence_range_end=buffer[-1].end_sentence,
        start_sec=start_sec,
        end_sec=end_sec,
        duration_sec=end_sec - start_sec,
        coherence_score=coherence,
    )


def normalize_length(
    leaves: list[SubTopic],
    transcript: Transcript,
    coherence_map: dict[str, float],
) -> list[ClipPlan]:
    clips: list[ClipPlan] = []
    buffer: list[SubTopic] = []

    def total_duration(buf: list[SubTopic]) -> float:
        if not buf:
            return 0.0
        s = transcript.find_sentence(buf[0].start_sentence)
        e = transcript.find_sentence(buf[-1].end_sentence)
        if not s or not e:
            return 0.0
        return e.end - s.start

    def avg_coherence(buf: list[SubTopic]) -> float:
        scores = [coherence_map.get(st.name, 3.0) for st in buf]
        return sum(scores) / len(scores) if scores else 3.0

    for st in leaves:
        if buffer and buffer[-1].parent != st.parent:
            clip = _merge_subtopics(buffer, transcript, avg_coherence(buffer))
            if clip.duration_sec < settings.min_clip_duration_sec:
                clip.warnings.append(f"Duration {clip.duration_sec:.1f}s < {settings.min_clip_duration_sec}s")
            clips.append(clip)
            buffer = []
        buffer.append(st)
        if total_duration(buffer) >= settings.min_clip_duration_sec:
            clips.append(_merge_subtopics(buffer, transcript, avg_coherence(buffer)))
            buffer = []

    if buffer:
        if clips:
            last = clips[-1]
            merged = _merge_subtopics(
                [SubTopic(name=last.topic_name, start_sentence=last.sentence_range_start,
                          end_sentence=last.sentence_range_end, parent=last.parent_topic)]
                + buffer, transcript, avg_coherence(buffer)
            )
            merged.warnings = last.warnings.copy()
            merged.warnings.append("Trailing segment merged backward")
            clips[-1] = merged
        else:
            clips.append(_merge_subtopics(buffer, transcript, avg_coherence(buffer)))

    for clip in clips:
        if clip.duration_sec > settings.max_clip_duration_sec:
            clip.warnings.append(f"Duration {clip.duration_sec:.1f}s > {settings.max_clip_duration_sec}s")

    return clips


def enforce_min_duration_post_snap(clips: list[ClipPlan]) -> list[ClipPlan]:
    """After snap, if a clip is still < min, steal time from next (or absorb)."""
    if not clips:
        return clips
    min_dur = settings.min_clip_duration_sec
    i = 0
    while i < len(clips):
        c = clips[i]
        if c.end_sec - c.start_sec >= min_dur:
            i += 1
            continue
        if i + 1 < len(clips):
            nxt = clips[i + 1]
            need = min_dur - (c.end_sec - c.start_sec)
            available = (nxt.end_sec - nxt.start_sec) - 0
            if available <= need + 0.1:
                # absorb fully
                c.end_sec = nxt.end_sec
                c.duration_sec = c.end_sec - c.start_sec
                c.sentence_range_end = nxt.sentence_range_end
                c.warnings.append(f"Post-snap absorbed next clip ({available:.1f}s)")
                clips.pop(i + 1)
            else:
                c.end_sec = c.end_sec + need
                nxt.start_sec = c.end_sec
                c.duration_sec = c.end_sec - c.start_sec
                nxt.duration_sec = nxt.end_sec - nxt.start_sec
                c.warnings.append(f"Post-snap extended +{need:.1f}s into next clip")
            continue
        elif i > 0:
            prev = clips[i - 1]
            prev.end_sec = c.end_sec
            prev.duration_sec = prev.end_sec - prev.start_sec
            prev.sentence_range_end = c.sentence_range_end
            prev.warnings.append(f"Post-snap absorbed trailing short clip ({c.end_sec-c.start_sec:.1f}s)")
            clips.pop(i)
        else:
            i += 1
    return clips


def enforce_min_duration(clips: list[ClipPlan], transcript: Transcript) -> list[ClipPlan]:
    """Cross-parent steal: extend any clip < min by stealing sentences from next clip.
    If next is empty after donation, absorb it. If no next, merge backward into prev."""
    if not clips:
        return clips
    all_ids = [s.id for s in transcript.sentences]
    min_dur = settings.min_clip_duration_sec

    i = 0
    while i < len(clips):
        c = clips[i]
        if c.duration_sec >= min_dur:
            i += 1
            continue

        if i + 1 < len(clips):
            nxt = clips[i + 1]
            try:
                nxt_start_idx = all_ids.index(nxt.sentence_range_start)
                nxt_end_idx = all_ids.index(nxt.sentence_range_end)
            except ValueError:
                i += 1
                continue

            stolen = 0
            while c.duration_sec < min_dur and nxt_start_idx <= nxt_end_idx:
                steal_id = all_ids[nxt_start_idx]
                steal_sent = transcript.find_sentence(steal_id)
                if not steal_sent:
                    break
                c.sentence_range_end = steal_id
                c.end_sec = steal_sent.end
                c.duration_sec = c.end_sec - c.start_sec
                nxt_start_idx += 1
                stolen += 1

            if stolen > 0:
                cross = c.parent_topic != nxt.parent_topic
                tag = "cross-parent" if cross else "same-parent"
                if nxt_start_idx > nxt_end_idx:
                    c.warnings.append(f"Absorbed next clip ({stolen} sentences, {tag})")
                    clips.pop(i + 1)
                else:
                    new_start_id = all_ids[nxt_start_idx]
                    new_start_sent = transcript.find_sentence(new_start_id)
                    nxt.sentence_range_start = new_start_id
                    nxt.start_sec = new_start_sent.start
                    nxt.duration_sec = nxt.end_sec - nxt.start_sec
                    c.warnings.append(f"Extended by {stolen} sentence(s) from next ({tag})")
                continue
            i += 1
        elif i > 0:
            prev = clips[i - 1]
            prev.sentence_range_end = c.sentence_range_end
            prev.end_sec = c.end_sec
            prev.duration_sec = prev.end_sec - prev.start_sec
            prev.warnings.append(f"Absorbed trailing short clip ({c.duration_sec:.1f}s)")
            clips.pop(i)
        else:
            i += 1

    return clips


def _validate_leaves_batch(
    transcript: Transcript,
    leaves: list[SubTopic],
    job_id: int | None,
) -> dict[str, StageBOutput] | None:
    """Try batch API for Stage B. Returns dict by start_sentence or None on fallback."""
    if not settings.use_batch_api or len(leaves) < settings.batch_min_requests:
        return None
    try:
        from youtok.llm.batch import stage_b_batch, BatchTimeout
        items = []
        seen = set()
        for st in leaves:
            cid = f"{st.start_sentence}_{st.end_sentence}_{hash(st.name) & 0xffff:04x}"
            if cid in seen:
                continue
            seen.add(cid)
            sb, msgs = build_stage_b(
                transcript, st.name, st.parent,
                st.start_sentence, st.end_sentence,
            )
            items.append({"custom_id": cid, "system_blocks": sb, "messages": msgs})
        raw = stage_b_batch(
            items, STAGE_B_TOOL, tier="haiku", max_tokens=2000, job_id=job_id,
        )
        out: dict[str, StageBOutput] = {}
        for st in leaves:
            cid = f"{st.start_sentence}_{st.end_sentence}_{hash(st.name) & 0xffff:04x}"
            if cid in raw:
                out[st.start_sentence] = StageBOutput.model_validate(raw[cid])
        if len(out) < len(leaves) * 0.8:
            logger.warning(f"Batch returned only {len(out)}/{len(leaves)}, fallback to sync")
            return None
        return out
    except Exception as e:
        logger.warning(f"Stage B batch failed: {e}, fallback to sync")
        return None


def segment_topics(
    transcript: Transcript,
    video_title: str,
    job_id: int | None = None,
) -> list[ClipPlan]:
    logger.info("Stage A — topic outline")
    stage_a = _run_stage_a(transcript, video_title, job_id=job_id)
    logger.info(f"Stage A: {stage_a.main_topic}, {len(stage_a.sub_topics)} top-level topics")

    leaves = _flatten_leaves(stage_a.sub_topics)
    logger.info(f"Stage B — validating {len(leaves)} leaf topics")

    coherence_map: dict[str, float] = {}
    adjusted_leaves: list[SubTopic] = []

    batch_results = _validate_leaves_batch(transcript, leaves, job_id)

    if batch_results is not None:
        for leaf in leaves:
            validation = batch_results.get(leaf.start_sentence)
            if validation is None:
                continue
            coherence_map[leaf.name] = validation.coherence_score
            result_topics = _apply_adjustment(leaf, validation, transcript)
            if len(result_topics) == 2:
                for split_st in result_topics:
                    _, split_val = _run_stage_b_single(
                        transcript, split_st, "stage_b_split", job_id,
                    )
                    coherence_map[split_st.name] = split_val.coherence_score
            adjusted_leaves.extend(result_topics)
    else:
        with ThreadPoolExecutor(max_workers=min(3, len(leaves))) as pool:
            futures = {
                pool.submit(_run_stage_b_single, transcript, leaf, "stage_b", job_id): leaf
                for leaf in leaves
            }
            for future in as_completed(futures):
                st, validation = future.result()
                coherence_map[st.name] = validation.coherence_score

                result_topics = _apply_adjustment(st, validation, transcript)
                if len(result_topics) == 2:
                    for split_st in result_topics:
                        _, split_val = _run_stage_b_single(
                            transcript, split_st, "stage_b_split", job_id,
                        )
                        coherence_map[split_st.name] = split_val.coherence_score
                        split_st.start_sentence = transcript.sentences[
                            max(0, [s.id for s in transcript.sentences].index(split_st.start_sentence) + split_val.start_adjust)
                        ].id
                        split_st.end_sentence = transcript.sentences[
                            min(len(transcript.sentences) - 1,
                                [s.id for s in transcript.sentences].index(split_st.end_sentence) + split_val.end_adjust)
                        ].id
                adjusted_leaves.extend(result_topics)

    all_ids = [s.id for s in transcript.sentences]
    adjusted_leaves.sort(key=lambda st: all_ids.index(st.start_sentence))

    logger.info("Stage C — length normalization")
    clips = normalize_length(adjusted_leaves, transcript, coherence_map)
    before = len(clips)
    clips = enforce_min_duration(clips, transcript)
    if len(clips) != before:
        logger.info(f"enforce_min_duration: {before} -> {len(clips)} clips")
    logger.info(f"Segmentation complete: {len(clips)} clips")

    return clips
