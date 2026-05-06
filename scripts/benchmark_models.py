"""Benchmark all model combos across Claude / OpenAI / Gemini on the same video.

Usage:
    uv run python scripts/benchmark_models.py [--video URL] [--skip-gemini] [--skip-openai] [--skip-claude]

Same video cached → transcript + shot detection are reused across runs (fair LLM comparison).
Each combo: temporarily set active_provider + stage_a_model + stage_b_model, submit job, wait, collect metrics.

Output: markdown table ranked by cost-per-quality ratio.
"""
import argparse
import json
import sys
import time
from pathlib import Path

# Ensure src/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from youtok.db.base import SessionLocal
from youtok.db.models import Job, ApiKey, Setting
from youtok.db.crud import (
    create_job, get_active_license, get_setting, set_setting,
    get_api_key, upsert_api_key,
)
from youtok.queue.tasks import process_job
from youtok.llm.cost_tracker import get_total_cost_for_job, get_history_summary
from youtok.llm.fx import fmt_vnd

# Video to benchmark — short enough to be fast, complex enough to differentiate quality
DEFAULT_VIDEO = "https://www.youtube.com/watch?v=SvcqjV-SDC8"  # 25-min bowling tutorial
TEST_OUTPUT_DIR = "/tmp/youtok-benchmark"

# Test combos — provider, stage_a, stage_b, label
COMBOS = [
    # Claude
    ("anthropic", "claude-sonnet-4-6", "claude-haiku-4-5-20251001", "Claude Sonnet+Haiku (baseline)"),
    ("anthropic", "claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001", "Claude Haiku-only (cheap)"),
    # OpenAI
    ("openai", "gpt-4o", "gpt-4o-mini", "OpenAI 4o + 4o-mini (mid)"),
    ("openai", "gpt-4o", "gpt-4o", "OpenAI 4o + 4o"),
    ("openai", "o3-mini", "gpt-4o-mini", "OpenAI o3-mini + 4o-mini (reasoning)"),
    # Gemini
    ("google", "gemini/gemini-2.5-pro", "gemini/gemini-2.5-flash", "Gemini Pro + Flash"),
    ("google", "gemini/gemini-2.5-flash", "gemini/gemini-2.5-flash", "Gemini Flash-only"),
    ("google", "gemini/gemini-2.5-flash-lite", "gemini/gemini-2.5-flash-lite", "Gemini Flash-Lite (cheapest)"),
]


def save_state(db) -> dict:
    """Snapshot current settings to restore after benchmark."""
    saved = {
        "active_provider": get_setting(db, "active_provider"),
        "concurrent_jobs": get_setting(db, "concurrent_jobs"),
    }
    for p in ("anthropic", "openai", "google"):
        ak = get_api_key(db, p)
        saved[f"{p}_stage_a_model"] = ak.stage_a_model if ak else None
        saved[f"{p}_stage_b_model"] = ak.stage_b_model if ak else None
    return saved


def restore_state(db, saved: dict):
    """Restore settings to pre-benchmark state."""
    if saved.get("active_provider"):
        set_setting(db, "active_provider", saved["active_provider"])
    if saved.get("concurrent_jobs"):
        set_setting(db, "concurrent_jobs", saved["concurrent_jobs"])
    for p in ("anthropic", "openai", "google"):
        ak = get_api_key(db, p)
        if ak:
            ak.stage_a_model = saved.get(f"{p}_stage_a_model")
            ak.stage_b_model = saved.get(f"{p}_stage_b_model")
    db.commit()


def configure_combo(db, provider: str, stage_a: str, stage_b: str):
    """Set DB to use this provider + models."""
    set_setting(db, "active_provider", provider)
    ak = get_api_key(db, provider)
    if not ak:
        raise RuntimeError(f"No API key for provider '{provider}' — paste in /settings/ first")
    ak.stage_a_model = stage_a
    ak.stage_b_model = stage_b
    db.commit()


def submit_and_wait(db, source_url: str, output_dir: str, label: str, timeout: int = 900) -> int:
    """Create job, dispatch synchronously to worker queue, wait for completion, return job_id."""
    license = get_active_license(db)
    if not license:
        raise RuntimeError("No active license")

    job = create_job(
        db,
        license_id=license.id,
        source_type="video",
        source_url=source_url,
        output_dir=output_dir,
        config_json=json.dumps({"benchmark_label": label}),
    )
    job_id = job.id

    # Enqueue via Huey
    process_job(job_id)

    print(f"  Submitted job {job_id} → polling status...", flush=True)
    start = time.time()
    last_step = None
    while time.time() - start < timeout:
        with SessionLocal() as poll_db:
            j = poll_db.query(Job).filter(Job.id == job_id).first()
            if j is None:
                time.sleep(2)
                continue
            if j.current_step and j.current_step != last_step:
                print(f"    [{int(time.time()-start):>3}s] {j.status:>12} {j.progress_pct:>3}% | {j.current_step}", flush=True)
                last_step = j.current_step
            if j.status in ("done", "failed"):
                return job_id
        time.sleep(3)
    print(f"    TIMEOUT after {timeout}s", flush=True)
    return job_id


def collect_metrics(db, job_id: int) -> dict:
    """Read job + manifest + cost log → metrics dict."""
    j = db.query(Job).filter(Job.id == job_id).first()
    metrics = {
        "job_id": job_id,
        "status": j.status,
        "duration_sec": (j.finished_at - j.started_at).total_seconds() if j.started_at and j.finished_at else None,
        "error": j.error_message,
        "video_duration_sec": j.video_duration_sec or 0,
        "clips_count": j.clips_count or 0,
        "cost_usd": round(get_total_cost_for_job(job_id), 4),
        "avg_coherence": None,
        "coverage_pct": None,
    }
    if j.status == "done":
        # Read manifest
        out_dir = Path(j.output_dir)
        manifest = None
        for sub in out_dir.iterdir() if out_dir.exists() else []:
            mf = sub / "manifest.json"
            if mf.exists():
                manifest = json.loads(mf.read_text(encoding="utf-8"))
                break
        if manifest and manifest.get("clips"):
            scores = [c["coherence_score"] for c in manifest["clips"] if c.get("coherence_score", 0) > 0]
            metrics["avg_coherence"] = round(sum(scores) / len(scores), 2) if scores else None
            total_clip_dur = sum(c.get("duration_sec", 0) for c in manifest["clips"])
            if metrics["video_duration_sec"]:
                metrics["coverage_pct"] = round(100 * total_clip_dur / metrics["video_duration_sec"], 1)
    return metrics


def quality_score(m: dict) -> float:
    """Composite quality 0-10. Higher = better."""
    if m["status"] != "done":
        return 0.0
    coh = m.get("avg_coherence") or 0
    cov = m.get("coverage_pct") or 0
    # 60% weight coherence (1-5 scale → ×2 = 0-10), 40% weight coverage (0-100 → /10 = 0-10)
    return round(0.6 * (coh * 2) + 0.4 * (cov / 10), 2)


def value_score(m: dict) -> float | None:
    """Quality per dollar. Higher = better value."""
    q = quality_score(m)
    c = m.get("cost_usd", 0)
    if q == 0 or c == 0:
        return None
    return round(q / max(c, 0.0001), 1)


def print_results(results: list[dict]):
    print()
    print("=" * 110)
    print("BENCHMARK RESULTS — same video, fair comparison (transcript + shot cached)")
    print("=" * 110)
    print()

    # Header
    cols = ["#", "Provider+Models", "Status", "Clips", "Cov%", "Coh", "Cost USD", "Cost VND", "Time", "Quality", "Value"]
    print(f"{'#':<3} {'Setup':<40} {'Status':<8} {'Clips':<6} {'Cov%':<6} {'Coh':<5} {'$':<8} {'VND':<10} {'Time':<6} {'Q':<5} {'Q/$':<6}")
    print("-" * 110)

    # Sort by value (Quality/Cost) descending
    sorted_results = sorted(
        [r for r in results if r["status"] == "done"],
        key=lambda r: -(value_score(r) or 0),
    )
    failed_results = [r for r in results if r["status"] != "done"]

    for i, r in enumerate(sorted_results, 1):
        cov = f"{r.get('coverage_pct', 0)}%" if r.get('coverage_pct') else "-"
        coh = str(r.get("avg_coherence", "-"))
        cost = f"${r['cost_usd']:.4f}"
        vnd = fmt_vnd(r['cost_usd']) if r['cost_usd'] else "0 ₫"
        time_str = f"{int(r['duration_sec'])}s" if r['duration_sec'] else "-"
        q = quality_score(r)
        v = value_score(r) or "-"
        print(f"{i:<3} {r['label'][:39]:<40} {'done':<8} {r['clips_count']:<6} {cov:<6} {coh:<5} {cost:<8} {vnd:<10} {time_str:<6} {q:<5} {v}")

    # Failed at bottom
    for r in failed_results:
        err = (r.get("error") or "?")[:50]
        print(f"-   {r['label'][:39]:<40} {'FAILED':<8} {err}")

    print()
    print("Q (Quality) = 0.6 × (coherence × 2) + 0.4 × (coverage% / 10).  Higher = better.")
    print("Q/$ (Value) = Quality / Cost.  Higher = better value-for-money.")
    print()

    if sorted_results:
        winner = sorted_results[0]
        print(f"🏆 BEST VALUE: {winner['label']}")
        print(f"   Quality {quality_score(winner):.2f}, Cost ${winner['cost_usd']:.4f} ({fmt_vnd(winner['cost_usd'])})")
        # Find best quality regardless of cost
        best_quality = max(sorted_results, key=lambda r: quality_score(r))
        print(f"\n🥇 BEST QUALITY: {best_quality['label']}")
        print(f"   Quality {quality_score(best_quality):.2f}, Cost ${best_quality['cost_usd']:.4f} ({fmt_vnd(best_quality['cost_usd'])})")
        # Find cheapest that hit minimum quality (Q >= 7.0)
        passable = [r for r in sorted_results if quality_score(r) >= 7.0]
        if passable:
            cheapest = min(passable, key=lambda r: r['cost_usd'])
            print(f"\n💰 CHEAPEST PASSABLE (Q≥7.0): {cheapest['label']}")
            print(f"   Quality {quality_score(cheapest):.2f}, Cost ${cheapest['cost_usd']:.4f} ({fmt_vnd(cheapest['cost_usd'])})")

    # Save markdown report
    report_path = Path(__file__).parent.parent / "data" / f"benchmark-{int(time.time())}.md"
    with report_path.open("w", encoding="utf-8") as f:
        f.write("# Model Benchmark Report\n\n")
        f.write(f"Video: {DEFAULT_VIDEO}\n\n")
        f.write("| Rank | Setup | Clips | Coverage | Coherence | Cost USD | Cost VND | Time | Quality | Q/$ |\n")
        f.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for i, r in enumerate(sorted_results, 1):
            cov = f"{r.get('coverage_pct', 0)}%" if r.get('coverage_pct') else "-"
            coh = str(r.get("avg_coherence", "-"))
            f.write(f"| {i} | {r['label']} | {r['clips_count']} | {cov} | {coh} | "
                    f"${r['cost_usd']:.4f} | {fmt_vnd(r['cost_usd'])} | "
                    f"{int(r['duration_sec']) if r['duration_sec'] else '-'}s | "
                    f"{quality_score(r):.2f} | {value_score(r) or '-'} |\n")
        for r in failed_results:
            f.write(f"| - | {r['label']} | FAILED | - | - | - | - | - | - | {r.get('error', '')[:80]} |\n")
    print(f"\nFull report saved: {report_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=DEFAULT_VIDEO)
    parser.add_argument("--output-dir", default=TEST_OUTPUT_DIR)
    parser.add_argument("--skip-gemini", action="store_true")
    parser.add_argument("--skip-openai", action="store_true")
    parser.add_argument("--skip-claude", action="store_true")
    parser.add_argument("--timeout", type=int, default=900, help="Per-job timeout in seconds")
    args = parser.parse_args()

    print(f"Benchmark video: {args.video}")
    print(f"Output dir: {args.output_dir}")
    print()

    # Filter combos by provider availability
    with SessionLocal() as db:
        # Check API keys exist
        present = {p: get_api_key(db, p) is not None for p in ("anthropic", "openai", "google")}
        print(f"API keys: anthropic={present['anthropic']}, openai={present['openai']}, google={present['google']}")
        if args.skip_claude:
            present["anthropic"] = False
        if args.skip_openai:
            present["openai"] = False
        if args.skip_gemini:
            present["google"] = False

        active_combos = [(p, sa, sb, label) for p, sa, sb, label in COMBOS if present.get(p)]
        if not active_combos:
            print("ERROR: no providers available. Add API keys via /settings/ first.")
            return 1
        print(f"Will test {len(active_combos)} combos")
        print()

        saved = save_state(db)
        # Force concurrent_jobs=1 during benchmark to avoid contention
        set_setting(db, "concurrent_jobs", "1")

    results = []
    try:
        for i, (provider, stage_a, stage_b, label) in enumerate(active_combos, 1):
            print(f"\n[{i}/{len(active_combos)}] {label}")
            print(f"  Provider: {provider}, Stage A: {stage_a}, Stage B: {stage_b}")

            with SessionLocal() as db:
                try:
                    configure_combo(db, provider, stage_a, stage_b)
                except RuntimeError as e:
                    print(f"  SKIP: {e}")
                    continue

            with SessionLocal() as db:
                job_id = submit_and_wait(db, args.video, args.output_dir, label, timeout=args.timeout)

            with SessionLocal() as db:
                m = collect_metrics(db, job_id)
                m["label"] = label
                m["provider"] = provider
                m["stage_a"] = stage_a
                m["stage_b"] = stage_b
                results.append(m)

            print(f"  Result: status={m['status']}, clips={m['clips_count']}, coherence={m.get('avg_coherence')}, cost=${m['cost_usd']:.4f}")

    finally:
        with SessionLocal() as db:
            restore_state(db, saved)
            print("\nRestored original settings.")

    print_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
