from youtok.core.transcriber import Transcript

FIFTEEN_RULES = """
## Group A — Detect boundaries

R1. Tutorial markers as boundary signals.
Sentences containing these patterns → prefer as OPENING of a new sub-topic:
"Let's start with...", "First...", "Now let's look at...", "Moving on to...",
"Another...", "Next...", "Finally...", "Before we...", "But first...", "Step 1/2/3..."

R2. Conclusion markers.
Sentences containing these patterns → prefer as CLOSING of a sub-topic:
"So that's how X works", "In summary", "To recap", "That's the basic idea",
"And that's it for..."

R3. Long pauses are boundary candidates.
Pauses ≥ 1.0s flagged as candidates; ≥ 2.0s strong signal.

## Group B — Don't break wrong things

R4. Q→A integrity. Question + answer is one atomic unit.
R5. Definition→Explanation integrity. "X is Y. It works by..." is atomic.
R6. Numbered sequence integrity. "There are 3 components: First/Second/Third..." stays in one sub-topic.
R7. Example chains. "For example...", "Let me show you...", "Imagine..." continues previous sentence.
R8. Visual references. "As you can see...", "Watch this..." → keep with whatever the visual is showing.

## Group C — Clean opening / closing of clips

R9. First-sentence rule. Clip's first sentence cannot start with continuation words: But, And, So, Then, However, Therefore, Which, That. Else shift boundary back 1 sentence.
R10. Last-sentence rule. Clip's last sentence ends with . or ! (not ,). Not a dependent clause.
R11. Anti-cliffhanger. Last sentence cannot promise next content: "But there's another problem..." Shift forward.
R12. Hook preservation. Educational hooks ("But how does X really work?") must stay inside the clip, not at boundary.

## Group D — Sanity check

R13. Title hint. Use video title to anchor main topic outline.
R14. Length distribution. All clips same ±5s = over-merge, alert. Single clip > 4 min = under-split, suggest split.
R15. Coverage. Total clip duration ≈ video duration minus intro/outro strip. Mismatch > 5% → flag.
"""

STAGE_A_TOOL = {
    "name": "submit_topic_tree",
    "description": "Submit the analyzed topic tree.",
    "parameters": {
        "type": "object",
        "properties": {
            "main_topic": {"type": "string", "description": "The main topic of the entire video"},
            "intro_strip": {
                "type": ["object", "null"],
                "description": "Sentence range to strip as intro (optional)",
                "properties": {
                    "start": {"type": "string", "pattern": "^S\\d{3}$"},
                    "end": {"type": "string", "pattern": "^S\\d{3}$"},
                },
                "required": ["start", "end"],
            },
            "outro_strip": {
                "type": ["object", "null"],
                "description": "Sentence range to strip as outro (optional)",
                "properties": {
                    "start": {"type": "string", "pattern": "^S\\d{3}$"},
                    "end": {"type": "string", "pattern": "^S\\d{3}$"},
                },
                "required": ["start", "end"],
            },
            "sub_topics": {
                "type": "array",
                "description": "List of sub-topics forming the topic tree",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "start_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                        "end_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                        "parent": {"type": ["string", "null"]},
                        "children": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "start_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                                    "end_sentence": {"type": "string", "pattern": "^S\\d{3}$"},
                                    "parent": {"type": ["string", "null"]},
                                    "children": {"type": "array", "items": {"type": "object"}},
                                },
                                "required": ["name", "start_sentence", "end_sentence"],
                            },
                        },
                    },
                    "required": ["name", "start_sentence", "end_sentence"],
                },
            },
        },
        "required": ["main_topic", "sub_topics"],
    },
}

_STAGE_B_VALIDATION_PROPS = {
    "coherence_score": {
        "type": "integer",
        "minimum": 1,
        "maximum": 5,
        "description": "1=incoherent, 5=perfectly self-contained",
    },
    "start_adjust": {
        "type": "integer",
        "description": "Signed sentence shift for start boundary (negative=earlier, positive=later)",
    },
    "end_adjust": {
        "type": "integer",
        "description": "Signed sentence shift for end boundary",
    },
    "internal_break": {
        "type": ["object", "null"],
        "description": "If the segment has an internal topic shift, specify the break point",
        "properties": {
            "start": {"type": "string", "pattern": "^S\\d{3}$"},
            "end": {"type": "string", "pattern": "^S\\d{3}$"},
        },
        "required": ["start", "end"],
    },
    "notes": {"type": "string", "description": "Explanation of adjustments"},
}

STAGE_B_TOOL = {
    "name": "submit_validation",
    "description": "Submit validation result for a sub-topic segment.",
    "parameters": {
        "type": "object",
        "properties": _STAGE_B_VALIDATION_PROPS,
        "required": ["coherence_score", "start_adjust", "end_adjust", "notes"],
    },
}

# Batched Stage B: validate ALL sub-topics in 1 LLM call.
# Saves N-1 round-trips + system prompt cache hits for repeat usage.
STAGE_B_BATCH_TOOL = {
    "name": "submit_validations_batch",
    "description": "Submit validation results for multiple sub-topic segments at once.",
    "parameters": {
        "type": "object",
        "properties": {
            "validations": {
                "type": "array",
                "description": "One validation per input sub-topic, in the SAME ORDER as the input list. Use 'topic_index' to verify alignment.",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic_index": {
                            "type": "integer",
                            "description": "0-based index matching the input sub-topic order",
                        },
                        **_STAGE_B_VALIDATION_PROPS,
                    },
                    "required": ["topic_index", "coherence_score", "start_adjust", "end_adjust", "notes"],
                },
            },
        },
        "required": ["validations"],
    },
}


def build_stage_b_batch(
    transcript: Transcript,
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Build a single-LLM-call prompt that validates all sub-topics at once.

    items: [{name, parent, start_id, end_id}, ...]
    Returns (system_blocks, messages).
    """
    parts: list[str] = [
        f"Validate {len(items)} sub-topic segments in a SINGLE call. "
        "Return a `validations` array with one entry per input, preserving order via `topic_index`.\n"
    ]
    for idx, it in enumerate(items):
        sentences = transcript.sentences_between(it["start_id"], it["end_id"])
        segment_text = "\n".join(f"{s.id}: {s.text}" for s in sentences)
        parts.append(
            f"\n=== TOPIC {idx} ===\n"
            f"Name: \"{it['name']}\"\n"
            f"Parent: \"{it.get('parent') or 'None'}\"\n"
            f"Range: {it['start_id']} → {it['end_id']}\n"
            f"Segment:\n{segment_text}\n"
        )
    parts.append("\nUse the `submit_validations_batch` tool. Return ONE entry per topic, indexed 0..N-1.")
    user_msg = "".join(parts)

    system_blocks = [
        {"type": "text", "text": SYSTEM_STAGE_B, "cache_control": {"type": "ephemeral"}},
    ]
    messages = [{"role": "user", "content": user_msg}]
    return system_blocks, messages


def _format_transcript(transcript: Transcript) -> str:
    """Standard format: 'S###: text'. Reverted from compact — quality > token savings."""
    lines = []
    for s in transcript.sentences:
        lines.append(f"{s.id}: {s.text}")
    return "\n".join(lines)


_STAGE_A_DETAILS = """

## Output expectations

Return a hierarchical topic tree via the `submit_topic_tree` tool. Structure:

- `main_topic`: One short noun phrase summarizing the entire video. Use the video title as anchor (R13). Avoid generic words like "video", "content", "tutorial".
- `intro_strip` / `outro_strip` (optional): sentence ranges to discard. Strip ONLY pure greetings ("Hi everyone, welcome back..."), channel branding, sponsor reads, "subscribe" prompts, or sign-offs ("Thanks for watching, see you next time"). Do NOT strip a sentence that already explains a real topic — when in doubt, leave it in.
- `sub_topics`: list of self-contained units, each suitable for a 60-180s clip. Order matches transcript order (left-to-right, no overlap, no gap).
- Each sub_topic has: `name`, `start_sentence` (S###), `end_sentence` (S###), optional `parent`, optional `children` (for hierarchy).

## How to scope a sub-topic

A leaf sub-topic should answer ONE question or explain ONE concept. Tests:
1. If you described the leaf in one sentence, would the description fit the entire span? If you need "and also..." it is two topics, split them.
2. Does removing the first or last sentence break understanding? If yes, the boundary is set correctly. If no, tighten the boundary.
3. Could the clip stand alone if shown to a viewer with NO context? If a pronoun ("it", "this", "that approach") at the start has no antecedent inside the clip, shift start back one sentence.

## Hierarchy

Use parent/child only when content is naturally nested (e.g. "How browsers work" parent → "DNS lookup", "TCP handshake", "rendering" children). For flat lists ("5 tips for X"), keep all 5 as siblings, no parent.

## Boundary placement priorities (in order)

1. Tutorial markers (R1) — strong opening signal.
2. Conclusion markers (R2) — strong closing signal.
3. Long pauses (R3) — pauses ≥1.0s in the speaker's audio are likely topic boundaries; pauses ≥2.0s almost always are.
4. Q→A pairs, Definition→Explanation, numbered sequences, example chains, visual references — DO NOT split these (R4-R8).
5. Clean sentence start (no continuation word — R9) and clean sentence end (period/exclamation, not comma — R10) MUST hold.
6. Avoid cliffhangers at clip end (R11). Keep hooks inside (R12).

## Length guidance

- Aim for 60-180s per leaf when possible.
- A leaf < 60s is acceptable IF the topic is genuinely short (a one-shot definition); the post-processor will merge with siblings.
- A leaf > 240s is suspicious — split if there is a natural sub-boundary, otherwise leave it and the post-processor will warn.
- All clips having identical length ±5s is a sign of over-merging; vary length naturally with content.

## Common mistakes to avoid

- Don't use sentence IDs that don't exist (must be S001 to S{N}).
- Don't overlap ranges (sub_topic[i].end < sub_topic[i+1].start strictly).
- Don't leave gaps (each S### should belong to exactly one leaf, intro_strip, or outro_strip).
- Don't put a sentence containing a tutorial marker (R1) at the END of a sub-topic; it belongs at the START of the NEXT sub-topic.
- Don't split a sub-topic just to hit a length target — content coherence > length symmetry.
"""

SYSTEM_STAGE_A = (
    "You are an expert video editor analyzing transcripts of educational "
    '"explainer" videos (how-things-work content). Your job: produce a '
    "hierarchical topic tree where each leaf sub-topic is a self-contained "
    "unit suitable for a 60-180 second short-form video clip.\n\n"
    "Apply these 15 rules strictly:\n\n" + FIFTEEN_RULES + _STAGE_A_DETAILS
)

SYSTEM_STAGE_B = (
    "You verify whether a transcript segment is a complete, "
    "self-contained sub-topic suitable for a short clip.\n\n"
    "Rules to verify:\n\n" + FIFTEEN_RULES
)


def build_stage_a(transcript: Transcript, title: str) -> tuple[list[dict], list[dict]]:
    """Returns (system_blocks_with_cache, messages)."""
    formatted = _format_transcript(transcript)
    n = len(transcript.sentences)
    duration = transcript.duration_sec

    user_msg = f"""Video title: "{title}"
Total sentences: {n}
Total duration: {duration:.1f} sec

Transcript (sentence-numbered):
{formatted}

Use the `submit_topic_tree` tool to return your analysis.
Sentence IDs are S001 to S{n:03d}."""

    system_blocks = [
        {"type": "text", "text": SYSTEM_STAGE_A, "cache_control": {"type": "ephemeral"}},
    ]
    messages = [{"role": "user", "content": user_msg}]
    return system_blocks, messages


def build_stage_b(
    transcript: Transcript,
    name: str,
    parent: str | None,
    start_id: str,
    end_id: str,
) -> tuple[list[dict], list[dict]]:
    sentences = transcript.sentences_between(start_id, end_id)
    segment_text = "\n".join(f"{s.id}: {s.text}" for s in sentences)

    user_msg = f"""Sub-topic claimed name: "{name}"
Parent topic: "{parent or 'None'}"

Segment:
{segment_text}

Use the `submit_validation` tool."""

    system_blocks = [
        {"type": "text", "text": SYSTEM_STAGE_B, "cache_control": {"type": "ephemeral"}},
    ]
    messages = [{"role": "user", "content": user_msg}]
    return system_blocks, messages


def build_stage_a_retry(
    transcript: Transcript, title: str, issues: list[str]
) -> tuple[list[dict], list[dict]]:
    formatted = _format_transcript(transcript)
    n = len(transcript.sentences)
    duration = transcript.duration_sec

    issues_text = "\n".join(f"- {issue}" for issue in issues)

    user_msg = f"""Your previous attempt had these issues:
{issues_text}

Re-attempt the topic tree with these corrections.

Video title: "{title}"
Total sentences: {n}
Total duration: {duration:.1f} sec

Transcript (sentence-numbered):
{formatted}

Use the `submit_topic_tree` tool to return your corrected analysis.
Sentence IDs: S001 to S{n:03d}."""

    system_blocks = [
        {"type": "text", "text": SYSTEM_STAGE_A, "cache_control": {"type": "ephemeral"}},
    ]
    messages = [{"role": "user", "content": user_msg}]
    return system_blocks, messages
