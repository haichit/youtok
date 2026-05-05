import json
import shutil
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Callable

from loguru import logger

from youtok.config import settings
from youtok.core.compositor import SubConfig, generate_ass, render_clip, wrap_title
from youtok.core.downloader import download_video
from youtok.core.segmenter import ClipPlan, enforce_min_duration_post_snap, segment_topics
from youtok.core.slug import make_folder_name, slug
from youtok.core.snapper import snap_cuts
from youtok.core.transcriber import Transcript, WordToken, transcribe
from youtok.db.base import SessionLocal
from youtok.db.models import Clip, Job
from youtok.llm.client import call_with_tool
from youtok.llm.prompts import STAGE_A_TOOL, build_stage_a_retry


MIN_VIDEO_FOR_SEG_SEC = 2 * settings.min_clip_duration_sec  # 120s default
MAX_COST_PER_VIDEO_USD = 0.50


def save_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _make_single_clip_plan(transcript: Transcript, video_duration: float) -> list[ClipPlan]:
    if not transcript.sentences:
        return []
    return [ClipPlan(
        topic_name="full",
        parent_topic=None,
        sentence_range_start=transcript.sentences[0].id,
        sentence_range_end=transcript.sentences[-1].id,
        start_sec=0.0,
        end_sec=video_duration,
        duration_sec=video_duration,
        coherence_score=5.0,
        warnings=["Video too short to segment, output as single clip"],
    )]


def categorize_issues(issues: list[str]) -> tuple[list[str], list[str]]:
    """Returns (cosmetic, structural). Structural cannot be fixed by retry."""
    cosmetic, structural = [], []
    for iss in issues:
        if "duration" in iss and "< 60s" in iss:
            structural.append(iss)
        elif "Coverage" in iss and "< 95%" in iss:
            structural.append(iss)
        elif "starts with continuation word" in iss:
            cosmetic.append(iss)
        elif "Avg coherence" in iss:
            cosmetic.append(iss)
        else:
            cosmetic.append(iss)
    return cosmetic, structural


def collect_words(start_sec: float, end_sec: float, transcript: Transcript) -> list[WordToken]:
    words = []
    for s in transcript.sentences:
        for w in s.words:
            if w.start >= start_sec and w.end <= end_sec:
                words.append(w)
    return words


CONTINUATION_WORDS = {"But", "And", "So", "Then", "However", "Therefore", "Which", "That"}


def self_check(
    clips: list[ClipPlan],
    transcript: Transcript,
    video_duration: float,
) -> list[str]:
    issues = []

    for i, c in enumerate(clips, 1):
        if c.duration_sec < settings.min_clip_duration_sec:
            issues.append(f"Clip {i} duration {c.duration_sec:.1f}s < {settings.min_clip_duration_sec}s")

        sent = transcript.find_sentence(c.sentence_range_start)
        if sent:
            first_word = sent.text.split()[0].rstrip(",.")
            if first_word in CONTINUATION_WORDS:
                issues.append(f"Clip {i} starts with continuation word '{first_word}' (R9)")

    total_clip_dur = sum(c.duration_sec for c in clips)
    if video_duration > 0:
        coverage = total_clip_dur / video_duration
        if coverage < 0.95:
            issues.append(f"Coverage {coverage:.1%} < 95% (R15)")

    coherence_scores = [c.coherence_score for c in clips if c.coherence_score > 0]
    if coherence_scores:
        avg_coh = mean(coherence_scores)
        if avg_coh < 4.0:
            issues.append(f"Avg coherence {avg_coh:.2f} < 4.0")

    return issues


_retry_counter = {"n": 0}


def re_segment_with_feedback(
    transcript: Transcript,
    title: str,
    issues: list[str],
    job_id: int | None = None,
) -> list[ClipPlan]:
    from youtok.core.segmenter import segment_topics as _segment

    _retry_counter["n"] += 1
    retry_idx = _retry_counter["n"]

    logger.info(f"Re-segmenting with feedback: {issues}")
    prompt = build_stage_a_retry(transcript, title, issues)
    raw = call_with_tool(
        prompt, STAGE_A_TOOL, tier="sonnet",
        stage=f"stage_a_retry{retry_idx}", job_id=job_id,
    )

    from youtok.llm.schemas import StageAOutput
    stage_a = StageAOutput.model_validate(raw)

    from youtok.core.segmenter import (
        _flatten_leaves,
        _run_stage_b_single,
        _apply_adjustment,
        normalize_length,
        enforce_min_duration,
    )
    from concurrent.futures import ThreadPoolExecutor, as_completed

    leaves = _flatten_leaves(stage_a.sub_topics)
    coherence_map: dict[str, float] = {}
    adjusted: list = []

    with ThreadPoolExecutor(max_workers=min(3, len(leaves))) as pool:
        futures = {
            pool.submit(
                _run_stage_b_single, transcript, leaf,
                f"stage_b_retry{retry_idx}", job_id,
            ): leaf
            for leaf in leaves
        }
        for f in as_completed(futures):
            st, val = f.result()
            coherence_map[st.name] = val.coherence_score
            adjusted.extend(_apply_adjustment(st, val, transcript))

    all_ids = [s.id for s in transcript.sentences]
    adjusted.sort(key=lambda st: all_ids.index(st.start_sentence))

    clips = normalize_length(adjusted, transcript, coherence_map)
    return enforce_min_duration(clips, transcript)


def run_pipeline(job_id: int, progress_callback: Callable[[str, int, str], None]) -> None:
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise ValueError(f"Job {job_id} not found")

        progress_callback("downloading", 0, "Starting download")
        work = settings.workdir / f"job-{job_id}"
        dl = download_video(job.source_url, work)
        job.video_title = dl.title
        job.video_duration_sec = dl.duration_sec
        db.commit()
        progress_callback("downloading", 15, f"Downloaded: {dl.title}")

        progress_callback("transcribing", 20, "Loading WhisperX")
        transcript = transcribe(dl.audio_path)
        save_json(work / "transcript.json", transcript.model_dump())
        progress_callback("transcribing", 40, f"{len(transcript.sentences)} sentences")

        from youtok.core.segmenter import clear_stage_b_cache
        from youtok.llm.cost_tracker import get_total_cost_for_job
        clear_stage_b_cache()

        if dl.duration_sec < MIN_VIDEO_FOR_SEG_SEC:
            logger.warning(
                f"Video duration {dl.duration_sec:.0f}s < {MIN_VIDEO_FOR_SEG_SEC}s threshold. "
                "Skipping topic segmentation. Output as 1 clip."
            )
            progress_callback("segmenting", 60, "Video too short — single clip")
            plan = _make_single_clip_plan(transcript, dl.duration_sec)
            progress_callback("snapping", 65, "Skip snap (single clip)")
        else:
            progress_callback("segmenting", 45, "Stage A — outline")
            plan = segment_topics(transcript, dl.title, job_id=job_id)
            progress_callback("segmenting", 60, f"{len(plan)} clips planned")

            progress_callback("snapping", 62, "Detecting shot boundaries")
            plan = snap_cuts(plan, transcript, dl.video_path)
            plan = enforce_min_duration_post_snap(plan)
            progress_callback("snapping", 65, "Cuts snapped")

            for attempt in range(3):
                if get_total_cost_for_job(job_id) > MAX_COST_PER_VIDEO_USD:
                    logger.warning(
                        f"Cost budget ${MAX_COST_PER_VIDEO_USD} exceeded for job {job_id}, stop retry"
                    )
                    break
                issues = self_check(plan, transcript, dl.duration_sec)
                if not issues:
                    break
                cosmetic, structural = categorize_issues(issues)
                if structural:
                    logger.warning(f"Structural issues, retry won't help: {structural}")
                    for c in plan:
                        c.warnings.extend(structural)
                    break
                if attempt < 2 and cosmetic:
                    progress_callback(
                        "segmenting", 60,
                        f"Self-check cosmetic fail, retry {attempt + 1}/2"
                    )
                    plan = re_segment_with_feedback(transcript, dl.title, cosmetic, job_id=job_id)
                    plan = snap_cuts(plan, transcript, dl.video_path)
                    plan = enforce_min_duration_post_snap(plan)
                else:
                    break

        out_dir = Path(job.output_dir) / make_folder_name(dl.title, dl.video_id)
        (out_dir / "clips").mkdir(parents=True, exist_ok=True)
        save_json(out_dir / "topic-tree.json", [c.model_dump() for c in plan])

        sub_config = SubConfig()
        manifest: dict = {
            "video_title": dl.title,
            "video_id": dl.video_id,
            "video_duration_sec": dl.duration_sec,
            "total_clips": len(plan),
            "clips": [],
        }

        for i, clip in enumerate(plan, 1):
            progress_callback("cutting", 65 + int(30 * i / len(plan)), f"Rendering clip {i}/{len(plan)}")

            clip_words = collect_words(clip.start_sec, clip.end_sec, transcript)
            ass_path = work / f"clip-{i:02d}.ass"
            ass_path.write_text(generate_ass(clip_words, clip.start_sec, sub_config), encoding="utf-8")

            title_lines = wrap_title(f"{dl.title} - Part {i}/{len(plan)}")
            clip_filename = f"{i:02d}_{slug(clip.topic_name)}.mp4"
            clip_path = out_dir / "clips" / clip_filename

            render_clip(dl.video_path, clip.start_sec, clip.end_sec, ass_path, title_lines, clip_path)

            clip_text = " ".join(w.word for w in clip_words)
            db.add(Clip(
                job_id=job_id,
                part_number=i,
                total_parts=len(plan),
                topic_name=clip.topic_name,
                parent_topic=clip.parent_topic,
                start_sec=clip.start_sec,
                end_sec=clip.end_sec,
                duration_sec=clip.duration_sec,
                output_path=str(clip_path),
                coherence_score=clip.coherence_score,
                warnings_json=json.dumps(clip.warnings),
                transcript_text=clip_text,
                sentence_range_start=clip.sentence_range_start,
                sentence_range_end=clip.sentence_range_end,
            ))

            manifest["clips"].append({
                "part": f"{i}/{len(plan)}",
                "topic": clip.topic_name,
                "parent_topic": clip.parent_topic,
                "start_sec": clip.start_sec,
                "end_sec": clip.end_sec,
                "duration_sec": clip.duration_sec,
                "coherence_score": clip.coherence_score,
                "warnings": clip.warnings,
                "file": clip_filename,
            })

        db.commit()

        save_json(out_dir / "manifest.json", manifest)
        save_json(out_dir / "transcript.json", transcript.model_dump())

        progress_callback("cleanup", 95, "Removing source files")
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)

        job.status = "done"
        job.clips_count = len(plan)
        job.finished_at = datetime.utcnow()
        db.commit()
        progress_callback("done", 100, "Complete")

    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}")
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(e)[:500]
            db.commit()
        raise
    finally:
        db.close()
