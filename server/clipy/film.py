"""Transcript-grounded serial story planning and episode rendering."""
from __future__ import annotations

import json
import logging
import math
import subprocess
from pathlib import Path

from . import captions, keys, media, select, thumbs, tts

LOG = logging.getLogger(__name__)

SCRIPT_DIRECTION = (
    "Write natural spoken Indonesian for short film episodes. Every episode must stand alone: give a new "
    "viewer the context they need in the opening, establish one concrete question or conflict, escalate it "
    "through two to four connected turns, and deliver a payoff or outcome within the episode. Only after "
    "that payoff, use a grounded new consequence or unresolved question to hint at the next episode. "
    "The finale ends on the last confirmed consequence. Avoid ending on a generic teaser or cutting before "
    "the central question is answered. The series lead anchors continuity, but the focal character can "
    "change. Give supporting characters their own choices and consequences when that makes the episode "
    "stronger, even if the lead is absent from that scene. Link the perspectives through a shared conflict. "
    "Name people or use a clear role when a pronoun would be ambiguous. Open with a short, specific hook "
    "the footage supports. State each major fact once; develop the hook with new information, rather than "
    "repeating it. Use causal links and concrete verbs; cut lists of incidental dialogue. "
    "Use nearby dialogue to verify facts and never invent a connection, motive, event, or outcome."
)


def _shot_evidence(words, shots):
    nearby = [word for word in words if any(s["start"] - 10 <= word.start < s["end"] + 10 for s in shots)]
    return select.transcript_lines(nearby)


def _serial_story(blocks, config, target, speed=1.0):
    """Write the complete narration before choosing or dividing its episodes."""
    count = config["episodes"]
    speed = min(2.0, max(1.0, float(speed)))
    target_words = max(count, round(count * target * 1.8 * speed))
    min_words = max(count, math.floor(target_words * 0.65))
    max_words = max(count, math.floor(count * target * 2.15 * speed))
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    prompt = (
        f"Write one complete continuous Indonesian voice-over story covering the source from beginning to end. "
        f"It will later be split in order into {count} connected clips of about {target:.1f} seconds each. "
        f"Aim for {target_words} spoken words and use between {min_words} and {max_words}. "
        f"The voice speed is {speed:g}x. Tone: {config.get('style', 'informatif')}. User guidance: {config.get('additional_prompt', '')}. "
        "Include a clear beginning, causal developments, and ending. Keep the story coherent across clip "
        "boundaries, without repeated introductions, artificial standalone payoffs, headings, or part labels. "
        "Use only events and relationships supported by the evidence. Return the full spoken text as summary.\n"
        + "\n".join(blocks)
    )
    for attempt in range(3):
        data = keys.ask_json("Write one evidence-grounded film story. Return JSON only.", prompt, schema)
        story = data.get("summary", "").strip() if isinstance(data, dict) else ""
        words = story.split()
        if min_words <= len(words) <= max_words:
            return " ".join(words)
        prompt += f"\nThe story must contain between {min_words} and {max_words} words; revise the complete story using supported events only."
    raise ValueError("Rangkuman cerita tidak memenuhi kebutuhan durasi setelah 3 percobaan; sumber mungkin tidak cukup untuk jumlah dan durasi klip ini")


def _split_story(story, count):
    """Divide the single narration evenly without changing or cutting a word."""
    words = story.split()
    if count < 1 or len(words) < count:
        raise ValueError("Cerita terlalu pendek untuk dibagi menjadi jumlah klip yang dipilih")
    boundaries = [0]
    for i in range(1, count):
        ideal = round(len(words) * i / count)
        low, high = boundaries[-1] + 1, len(words) - (count - i)
        ideal = min(high, max(low, ideal))
        # Prefer a nearby complete sentence, without substantially unbalancing
        # narration lengths. Otherwise the boundary remains between whole words.
        candidates = [j for j in range(max(low, ideal - 2), min(high, ideal + 2) + 1)
                      if words[j - 1].rstrip("\"'”’)]}").endswith((".", "!", "?"))]
        boundaries.append(min(candidates, key=lambda j: (abs(j - ideal), j)) if candidates else ideal)
    boundaries.append(len(words))
    return [" ".join(words[start:end]) for start, end in zip(boundaries, boundaries[1:])]


def _write_scripts(episodes, words, config, protagonist="", throughline=""):
    """Edit the season as a whole while keeping the validated shots fixed."""
    briefs = []
    for number, episode in enumerate(episodes, 1):
        shots = episode["shots"]
        duration = sum(s["end"] - s["start"] for s in shots)
        briefs.append({
            "episode": number,
            "title": episode.get("title", ""),
            "target_words": max(3, round(duration * 1.8)),
            "max_words": max(3, math.floor(duration * 2.15)),
            "draft": episode["narration"],
            "selected_shots": shots,
            "dialogue_around_shots": _shot_evidence(words, shots),
        })
    schema = {"type": "object", "properties": {"scripts": {"type": "array", "items": {
        "type": "object", "properties": {part: {"type": "string"} for part in ("hook", "body", "ending")},
        "required": ["hook", "body", "ending"],
    }}}, "required": ["scripts"]}
    prompt = (
        f"Tone: {config.get('style', 'informatif')}. User guidance: {config.get('additional_prompt', '')}.\n"
        f"Lead character: {protagonist or 'Identify the recurring lead from the evidence.'}\n"
        f"Series throughline: {throughline or 'Track what the lead wants and how the outcome changes for them.'}\n"
        "Return one script per episode in the same order. Keep the story tied to its existing footage and "
        "Aim for target_words and stay within max_words across hook, body, and ending combined. Use the available "
        "runtime for relevant context, supporting perspectives, escalation and payoff. The draft is provisional. Use nearby dialogue to check its facts and disambiguate "
        "references. If the source leaves the purpose of an object or action unclear, describe only what is "
        "established (for example, a signature is needed) and leave its purpose unstated. "
        "Give just voice-over, no stage directions. "
        "The hook must be one short sentence. The body can be empty for a very short clip.\n"
        + json.dumps(briefs, ensure_ascii=False)
    )
    best = None
    best_shortfall = math.inf
    for attempt in range(2):
        result = keys.ask_json(SCRIPT_DIRECTION, prompt, schema)
        scripts = result.get("scripts", []) if isinstance(result, dict) else []
        if isinstance(scripts, list) and len(scripts) == len(episodes):
            narrations = []
            for script, brief in zip(scripts, briefs):
                if not isinstance(script, dict):
                    break
                parts = [script.get(part) for part in ("hook", "body", "ending")]
                if not all(isinstance(part, str) for part in parts) or not parts[0].strip() or not parts[2].strip():
                    break
                narration = " ".join(part.strip() for part in parts if part.strip())
                if len(narration.split()) > brief["max_words"]:
                    break
                narrations.append(narration)
            else:
                edited = [{**episode, "narration": narration} for episode, narration in zip(episodes, narrations)]
                shortfall = sum(max(0, round(brief["target_words"] * 0.8) - len(narration.split()))
                                for brief, narration in zip(briefs, narrations) if brief["target_words"] >= 25)
                if shortfall == 0:
                    return edited
                if shortfall < best_shortfall:
                    best, best_shortfall = edited, shortfall
        prompt += ("\nRevise the scripts. Check count and word cap. Where a script is short, add supported "
                   "context, meaningful supporting choices, and consequences from its selected scenes.")
    if best is not None:
        return best
    raise ValueError("Narasi serial tidak memenuhi struktur atau batas kata setelah dua percobaan")


def _validate_plan(result, duration, config):
    """Validate semantics separately from JSON syntax; return the first actionable error."""
    output = result.get("episodes") if isinstance(result, dict) else None
    if not isinstance(output, list) or len(output) != config["episodes"]:
        raise ValueError(f"Return exactly {config['episodes']} episodes")
    last_start = -1.0
    last_end = -1.0
    for ep_index, ep in enumerate(output, 1):
        if not isinstance(ep, dict):
            raise ValueError(f"Episode {ep_index} must be an object")
        shots = ep.get("shots")
        if not isinstance(shots, list) or not shots:
            raise ValueError(f"Episode {ep_index} must contain at least one shot")
        if not isinstance(ep.get("narration"), str) or not ep["narration"].strip():
            raise ValueError(f"Episode {ep_index} requires narration")
        total = 0.0
        for shot_index, shot in enumerate(shots, 1):
            label = f"Episode {ep_index}, shot {shot_index}"
            try:
                if not isinstance(shot, dict) or isinstance(shot.get("start"), bool) or isinstance(shot.get("end"), bool):
                    raise ValueError()
                start, end = float(shot["start"]), float(shot["end"])
            except (KeyError, TypeError, ValueError, OverflowError):
                raise ValueError(f"{label}: start and end must be numeric seconds") from None
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError(f"{label}: timestamps must be finite seconds")
            if start < 0 or end > duration:
                raise ValueError(f"{label}: timestamps {start:g}–{end:g} must lie within 0–{duration:g} seconds")
            if not 0 < end - start <= 5:
                raise ValueError(f"{label}: {start:g}–{end:g} lasts {end-start:g}s; duration must be >0 and <=5s")
            if start < last_start:
                raise ValueError(f"{label}: start {start:g}s must not precede the previous shot start {last_start:g}s")
            if start < last_end:
                raise ValueError(f"{label}: shot overlaps previous shot ending at {last_end:g}s")
            # The renderer needs real numbers even if a provider returned numeric strings.
            shot["start"], shot["end"] = start, end
            last_start, last_end = start, end
            total += end - start
        ceiling = config["max_duration"] - (0.15 if config.get("story_mode", "standalone") == "serial" else 0)
        if total > ceiling:
            raise ValueError(f"Episode {ep_index}: shots total {total:g}s exceeds {ceiling:g}s (encoding headroom)")
    return output


def plan(words, duration, config, *, speed=1.0):
    if not words:
        raise ValueError("Filming needs audible speech; visual-only films are not supported yet")
    episodes = config["episodes"]
    blocks = []
    source_votes = []
    for start in range(0, math.ceil(duration), 600):
        group = [w for w in words if start <= w.start < start + 600]
        if not group:
            continue
        data = keys.ask_json(
            "Summarize a timestamped video transcript faithfully. Reply in JSON. Do not invent visual events.",
            "Summarize key story events with timestamp references as JSON {\"summary\":\"...\",\"source_type\":\"film|recap|unknown\"}. Prioritize named characters, their goals and decisions, obstacles, consequences, reversals, and open questions. Mark title cards and credits as such. Classify recap only if clearly a third-party narration summarizing a story; film only when clearly original film dialogue, otherwise unknown.\n" + select.transcript_lines(group),
            {"type": "object", "properties": {"summary": {"type": "string"}, "source_type": {"type": "string"}}, "required": ["summary", "source_type"]},
        )
        blocks.append(f"[{start}-{min(duration, start+600)}] {data['summary']}")
        vote = data.get("source_type", "unknown")
        source_votes.append(vote if vote in ("film", "recap") else "unknown")
    known = set(source_votes) - {"unknown"}
    detected = next(iter(known)) if len(known) == 1 and source_votes.count(next(iter(known))) > len(source_votes) / 2 else "unknown"
    config["detected_source_type"] = detected
    schema = {"type": "object", "properties": {"protagonist": {"type": "string"}, "throughline": {"type": "string"}, "episodes": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "narration": {"type": "string"}, "reason": {"type": "string"},
        "shots": {"type": "array", "items": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}}, "required": ["start", "end"]}}},
        "required": ["title", "narration", "shots"]}}}, "required": ["protagonist", "throughline", "episodes"]}
    limit = config["max_duration"]
    target = min(limit - 1, duration / episodes)
    serial = config.get("story_mode", "standalone") == "serial"
    floor = max(0, target - 2) if serial else target * 0.82
    story_parts = None
    if serial:
        story = _serial_story(blocks, config, target, speed)
        story_parts = _split_story(story, episodes)
        config["story_summary"] = story
    else:
        config.pop("story_summary", None)
    system = "Plan chronological Indonesian short-film episodes from the supplied evidence. Return JSON only."
    prompt = (
        f"Source type: {detected}. Style: {config.get('style', 'informatif')}. "
        f"User guidance: {config.get('additional_prompt', '')}. "
        f"Create exactly {episodes} episodes. Aim for {target:.1f} seconds of selected footage per episode "
        f"and never exceed {limit - 1} seconds (encoding headroom). Aim for at least {floor:.1f} seconds "
        "when enough relevant footage exists. Add distinct, meaningful beats across the source rather than filler. "
        f"Use absolute timestamps between 0 and {duration:g}; each shot must be at most 5 seconds, "
        "chronological, and non-overlapping across the serial. "
        + ("Match each episode's footage to the corresponding exact section of the one continuous story below. "
           "Preserve its order and meaning. The sections connect and do not need independent introductions or endings. "
           f"Make selected footage durations differ by at most 2.8 seconds. Story sections: {json.dumps(story_parts, ensure_ascii=False)}. "
           if serial else
           "For each episode choose a focal character or group, the context a new viewer needs, a conflict, "
           "escalation, and a payoff that is shown within the episode. Only then open the next question. "
           "Choose the lead and throughline for the series, but build episodes around compelling supporting "
           "characters and parallel events when they add stakes or contrast. Make their relationship to the shared conflict clear. ")
        + "Use the source as evidence and skip incidental dialogue, title cards and credits. "
        "Draft narration at about 1.8 Indonesian words per second of chosen footage.\n" + "\n".join(blocks)
    )
    feedback = ""
    best = None
    best_shortfall = math.inf
    for attempt in range(3):
        try:
            result = keys.ask_json(system, prompt + feedback, schema)
            validated = _validate_plan(result, duration, config)
        except (ValueError, KeyError, TypeError) as exc:
            if attempt == 2 and best is None:
                raise ValueError(f"Rencana episode tidak valid setelah 3 percobaan: {exc}") from exc
            feedback = f"\nYour previous response failed validation: {exc}. Generate a complete corrected plan. Check every timestamp and episode duration before responding."
        else:
            short = []
            totals = []
            for index, episode in enumerate(validated, 1):
                total = sum(shot["end"] - shot["start"] for shot in episode["shots"])
                totals.append(total)
                if total < floor:
                    short.append((index, total))
            spread = max(totals) - min(totals)
            if serial:
                if not short and spread <= 2.8 + 1e-6:
                    return [{**episode, "narration": text} for episode, text in zip(validated, story_parts)]
                feedback = (f"\nSelected episode durations are {', '.join(f'{t:.1f}' for t in totals)} seconds. "
                            f"Regenerate the complete plan: each episode must reach at least {floor:.1f}s, "
                            "stay below the limit, and differ by no more than 2.8 seconds. Keep the story sections in order.")
                continue
            # Short or sparse sources can still produce valid, shorter stories.
            if not short or target < 25 or duration < limit * episodes:
                return _write_scripts(validated, words, config, result.get("protagonist", ""), result.get("throughline", ""))
            shortfall = sum(floor - total for _, total in short)
            if shortfall < best_shortfall:
                best, best_shortfall = (result, validated), shortfall
            gaps = ", ".join(f"episode {n}: {seconds:.1f}s" for n, seconds in short)
            feedback = f"\nThese episodes are too short: {gaps}. Extend their selected scenes toward {target:.1f}s by adding distinct causal beats and relevant supporting scenes. Keep shots chronological and within the limits."
    if best is not None:
        result, validated = best
        return _write_scripts(validated, words, config, result.get("protagonist", ""), result.get("throughline", ""))
    if serial:
        raise ValueError("Tidak dapat menyusun klip dengan durasi seimbang dari sumber ini setelah 3 percobaan")
    raise AssertionError("unreachable")


def _source_ratio(path):
    """Read display aspect ratio, taking pixel aspect and rotation into account."""
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation", "-of", "json", str(path)], capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    width, height = float(stream["width"]), float(stream["height"])
    sar = stream.get("sample_aspect_ratio", "1:1").split(":")
    if len(sar) == 2 and sar[0].isdigit() and sar[1].isdigit() and int(sar[1]):
        width *= int(sar[0]) / int(sar[1])
    rotation = stream.get("tags", {}).get("rotate", 0)
    for side in stream.get("side_data_list", []):
        rotation = side.get("rotation", rotation)
    if abs(int(float(rotation))) % 180 == 90:
        width, height = height, width
    if not width or not height:
        raise ValueError("Could not determine the video aspect ratio")
    return width / height


def _filming_graph(source_ratio, width, height, background, clip_ratio="auto", letterbox=0, *, inset=False):
    """Center-crop to the chosen clip shape, then fit it inside the output canvas."""
    if isinstance(letterbox, tuple):
        x_inset, y_inset = letterbox
        active_ratio = source_ratio * (1 - 2 * x_inset / 320) / (1 - 2 * y_inset / 360)
        source_crop = f"{media.blackbar_crop(letterbox)}," if x_inset or y_inset else ""
    else:
        active_ratio = source_ratio / (1 - 2 * letterbox / 360)
        source_crop = f"{media.letterbox_crop(letterbox)}," if letterbox else ""
    crop_ratio = "auto" if background == "none" else clip_ratio
    fg_width, fg_height = media.clip_frame_size(width, crop_ratio, active_ratio, output_height=height)
    if crop_ratio in ("none", "blackbar") and fg_height > height:
        fg_height = height
        fg_width = max(2, min(width, round(height * active_ratio / 2) * 2))
    crop = (f"[0:v]{source_crop}scale=trunc(iw*sar/2)*2:ih,setsar=1,"
            f"scale={fg_width}:{fg_height}:force_original_aspect_ratio=increase,"
            f"crop={fg_width}:{fg_height},setsar=1[r]")
    return media.compose_canvas(crop, width, height, fg_width, fg_height, background, inset=inset)


def _narration_cuts(shots, spoken, source_words):
    """Map visual beat starts to indexes in the actual synthesized narration."""
    if len(shots) < 2 or len(spoken) < 2 or not source_words:
        return [(1, 1)]
    shot_notes = [{"shot": i, **shot, "nearby_dialogue": _shot_evidence(source_words, [shot])}
                  for i, shot in enumerate(shots, 1)]
    narration = " ".join(f"{i}:{word.text}" for i, word in enumerate(spoken, 1))
    fields = {"shot": {"type": "integer"}, "word": {"type": "integer"}}
    schema = {"type": "object", "properties": {"cuts": {"type": "array", "items": {
        "type": "object", "properties": fields, "required": list(fields),
    }}}, "required": ["cuts"]}
    try:
        result = keys.ask_json(
            "You are an editor matching film shots to already recorded narration. Return JSON only.",
            "Start with shot 1 at word 1. Map the first shot of each new visual beat to the first word "
            "that introduces its event, person or location. Cut when the narration introduces the new beat, "
            "not after the sentence finishes. Use the shot times and dialogue as evidence. Use each index "
            "at most once and strictly increase both indexes. Shots illustrating the same beat can share a group. "
            f"Keep the original shot order.\nShots: {json.dumps(shot_notes, ensure_ascii=False)}\n"
            f"Numbered words in the voice-over: {narration}",
            schema,
        )
        cuts = [(row["shot"], row["word"]) for row in result["cuts"]]
        if cuts and cuts[0] == (1, 1) and all(
            type(shot) is int and type(word) is int and 1 <= shot <= len(shots) and 1 <= word <= len(spoken)
            for shot, word in cuts
        ) and all(a < b and c < d for (a, c), (b, d) in zip(cuts, cuts[1:])):
            return cuts
        LOG.warning("Narration alignment returned invalid shot or word indexes")
    except Exception:
        LOG.warning("Narration alignment unavailable; keeping the ordered visual sequence", exc_info=True)
    return [(1, 1)]


def _shot_frames(durations, spoken, cuts, total_frames):
    """Allocate exact output frames, with new beats starting at spoken word boundaries."""
    anchors = [(0, 0)]
    for shot, word in cuts[1:]:
        index = shot - 1
        frame = round(spoken[word - 1].start * media.FPS)
        # Keep at least one output frame per shot, including the final group.
        prev_index, prev_frame = anchors[-1]
        if frame - prev_frame >= index - prev_index and total_frames - frame >= len(durations) - index:
            anchors.append((index, frame))
    anchors.append((len(durations), total_frames))
    frames = []
    for (first, start), (last, end) in zip(anchors, anchors[1:]):
        group = durations[first:last]
        budget = end - start
        remaining = budget - len(group)
        weight = sum(group)
        elapsed = 0.0
        assigned = 0
        for duration in group:
            elapsed += duration
            boundary = round(remaining * elapsed / weight)
            frames.append(1 + boundary - assigned)
            assigned = boundary
    return frames


def _episode_frames(footage_duration, voice_duration, words, maximum, shots):
    """Use the planned runtime while keeping the complete voice and its final word."""
    end_of_speech = max((word.end for word in words), default=0.0)
    required = max(footage_duration, voice_duration, end_of_speech)
    frames = max(shots, math.ceil(required * media.FPS - 1e-7))
    if frames > math.floor(maximum * media.FPS + 1e-7):
        raise ValueError("Narasi tidak muat dalam durasi maksimum tanpa memotong kata")
    return frames


def _serial_duration_limit(project, config):
    """Keep every serial episode within three seconds of the shortest plan."""
    limit = float(config["max_duration"])
    totals = [sum(float(shot["end"]) - float(shot["start"]) for shot in clip.get("shots", []))
              for clip in getattr(project, "clips", []) if clip.get("shots")]
    if totals:
        shortest_frames = math.ceil(min(totals) * media.FPS - 1e-7)
        # Leave a small margin for muxing/timebase rounding in the final file.
        limit = min(limit, shortest_frames / media.FPS + 2.9)
    return limit


def _video_stream(path):
    """Read the codec configuration needed to join independently encoded shots."""
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_data_hash", "sha256",
        "-show_entries", "stream=codec_name,profile,level,width,height,pix_fmt,time_base,r_frame_rate,extradata_hash,start_time,duration,nb_frames",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True, timeout=30)
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError("Video stream tidak dapat diperiksa")
    return streams[0]


def _matching_shots(parts, frame_counts):
    """Only stream-copy when codec setup and every shot timestamp are exact."""
    fields = ("codec_name", "profile", "level", "width", "height", "pix_fmt", "time_base", "r_frame_rate", "extradata_hash")
    reference = None
    for part, expected in zip(parts, frame_counts, strict=True):
        stream = _video_stream(part)
        signature = tuple(stream.get(key) for key in fields)
        if any(value is None for value in signature) or stream.get("codec_name") != "h264" or stream.get("r_frame_rate") != f"{media.FPS}/1":
            return False
        if reference is not None and signature != reference:
            return False
        reference = signature
        start = float(stream.get("start_time", -1))
        duration = float(stream.get("duration", -1))
        if not math.isfinite(start) or not math.isfinite(duration):
            return False
        if int(stream.get("nb_frames", -1)) != expected or abs(start) > .002:
            return False
        if abs(duration - expected / media.FPS) > .003:
            return False
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
            "-of", "json", str(part),
        ], capture_output=True, text=True, check=True, timeout=30)
        timestamps = sorted(float(packet["pts_time"]) for packet in json.loads(probe.stdout).get("packets", []))
        if len(timestamps) != expected or any(not math.isfinite(t) or abs(t - i / media.FPS) > .002 for i, t in enumerate(timestamps)):
            return False
    return bool(reference)


def _copy_episode(parts, frame_counts, concat, voice, draft, total_frames):
    """Fast concat when it preserves the same frame and voice timing; otherwise fall back."""
    pending = draft.with_name(draft.stem + "_copy.pending.mp4")
    try:
        if (len(parts) != len(frame_counts) or sum(frame_counts) != total_frames
                or not _matching_shots(parts, frame_counts)):
            return False
        seconds = total_frames / media.FPS
        result = subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
            "-i", str(voice), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", "loudnorm=I=-16:TP=-2:LRA=11", "-c:a", "aac", "-t", f"{seconds:.6f}",
            "-movflags", "+faststart", str(pending),
        ], capture_output=True, text=True, timeout=300)
        if result.returncode:
            return False
        if not _matching_shots([pending], [total_frames]):
            return False
        audio = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time,duration",
            "-of", "json", str(pending),
        ], capture_output=True, text=True, check=True, timeout=30)
        streams = json.loads(audio.stdout).get("streams", [])
        if len(streams) != 1:
            return False
        start = float(streams[0].get("start_time", -1))
        duration = float(streams[0].get("duration", -1))
        if not math.isfinite(start) or not math.isfinite(duration) or abs(start) > .03:
            return False
        expected_audio = min(seconds, media.probe_duration(voice))
        if not math.isfinite(expected_audio) or abs(duration - expected_audio) > .1:
            return False
        pending.replace(draft)
        return True
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        LOG.info("Fast episode concat unavailable; using normal encoding", exc_info=True)
        return False
    finally:
        pending.unlink(missing_ok=True)


def render(p, clip, opts, config):
    """Time shot groups against the synthesized voice-over before rendering."""
    d = p.dir
    n = clip["n"]
    width, height = opts["width"], opts["height"]

    shots = clip["shots"]
    durations = [float(shot["end"]) - float(shot["start"]) for shot in shots]
    total = sum(durations)
    speed = opts.get("dubbing_speed", 1.0)
    transcript = d / "words.json"
    source_words = (
        [captions.Word(**item) for item in json.loads(transcript.read_text(encoding="utf-8"))]
        if transcript.exists() else []
    )
    if total > config["max_duration"] - 0.15:
        raise ValueError(f"Episode {n}: footage is too close to the maximum after encoding; shorten its shots and retry")
    narration = clip["narration"]
    voice_path = d / f"film_{n}_voice.mp3"
    # Older saved projects predate this setting and used standalone scripts.
    serial = config.get("story_mode", "standalone") == "serial"
    for attempt in range(3):
        voice, words = tts.speak_aligned(narration, config["voice"], voice_path, speed=speed)
        voice_duration = media.probe_duration(voice)
        # A serial script was divided from one complete story. Rewriting an
        # individual part here would break its continuity with the next part.
        if serial:
            break
        if voice_duration / total * 1.005 <= 1.12:
            break
        if attempt == 2:
            raise ValueError(f"Episode {n}: narration ({voice_duration:.1f}s) still exceeds footage ({total:.1f}s) after shortening")
        max_words = max(1, math.floor(len(narration.split()) * total / voice_duration * 0.90))
        revised = keys.ask_json(
            "Edit the supplied Indonesian narration. Return JSON only. Use the supplied facts. Preserve the opening hook, the lead character's name or role, their goal and choices, the main causal chain, and the exact unresolved turn or resolution at the end. Cut incidental details first.",
            f"Rewrite this episode in at most {max_words} words for {total:.1f} seconds of footage. Keep chronological order.\n\n{narration}",
            {"type": "object", "properties": {"narration": {"type": "string"}}, "required": ["narration"]},
        )
        narration = revised["narration"].strip()
        if not narration:
            raise ValueError(f"Episode {n}: shortened narration was empty")
    if not serial and speed == 1.0 and total >= 25 and voice_duration < total * 0.86 and source_words:
        expanded_path = d / f"film_{n}_voice_expanded.mp3"
        try:
            evidence = _shot_evidence(source_words, clip["shots"])
            if evidence.strip():
                target_words = max(3, round(len(narration.split()) * (total * 0.94) / voice_duration))
                draft = keys.ask_json(
                    SCRIPT_DIRECTION,
                    f"Expand the voice-over toward {target_words} words for {total:.1f} seconds. "
                    "Keep its existing hook and payoff, and deepen supported context, character choices "
                    f"or consequences. Avoid repetition.\n\nDraft: {narration}\nDialogue: {evidence}",
                    {"type": "object", "properties": {"narration": {"type": "string"}}, "required": ["narration"]},
                )
                expanded = draft["narration"].strip()
                if expanded and expanded != narration:
                    candidate, candidate_words = tts.speak_aligned(expanded, config["voice"], expanded_path, speed=speed)
                    candidate_duration = media.probe_duration(candidate)
                    if voice_duration < candidate_duration <= total:
                        candidate.replace(voice_path)
                        voice, words, narration, voice_duration = voice_path, candidate_words, expanded, candidate_duration
        except Exception:
            LOG.warning("Keeping original voice-over after expansion failed for episode %s", n, exc_info=True)
        finally:
            expanded_path.unlink(missing_ok=True)

    if not serial and voice_duration > total:
        fit_speed = voice_duration / total * 1.005
        adjusted = d / f"film_{n}_voice_fitted.mp3"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-filter:a", f"atempo={fit_speed:.5f}", "-c:a", "libmp3lame", str(adjusted)], check=True)
        voice = adjusted
        voice_duration = media.probe_duration(voice)
        words = [captions.Word(w.text, w.start / fit_speed, w.end / fit_speed) for w in words]
        if voice_duration > total + 0.02:
            raise ValueError(f"Episode {n}: narration still exceeds the available footage")
    # Retain the planned footage duration even if narration finishes early. The
    # last word and the encoded voice file must both fit before the final frame.
    if serial:
        duration_limit = _serial_duration_limit(p, config)
        try:
            total_frames = _episode_frames(total, voice_duration, words, duration_limit, len(shots))
        except ValueError as exc:
            raise ValueError(f"Episode {n}: narasi tidak muat dalam {duration_limit:.1f} detik tanpa memotong kata; coba ubah durasi atau kecepatan narator lalu generate ulang") from exc
    else:
        target_duration = min(total, voice_duration + 0.5)
        total_frames = max(len(shots), math.ceil(target_duration * media.FPS))
    cuts = _narration_cuts(shots, words, source_words)
    frame_counts = _shot_frames(durations, words, cuts, total_frames)
    local = p.local_video if p.upload_id else None
    crop_mode = config.get("source_ratio", "auto")
    local_inset = media.crop_insets(local, crop_mode) if local else 0
    parts = []
    for i, (shot, frames) in enumerate(zip(shots, frame_counts)):
        src = d / f"film_{n}_source_{i}.mp4"
        if p.upload_id:
            src = media.cut_section(p.local_video, shot["start"], shot["end"], src)
        else:
            src = media.download_section(p.url, shot["start"], shot["end"], src, video_only=True)
        part = d / f"film_{n}_part_{i}.mp4"
        target = frames / media.FPS
        stretch = max(1.0, target / durations[i])
        ratio = _source_ratio(src)
        config["detected_source_ratio"] = round(ratio, 5)
        if crop_mode == "none":
            inset = 0
        elif crop_mode == "blackbar":
            inset = local_inset if local else media.crop_insets(src, crop_mode)
        else:
            inset = local_inset or media.crop_insets(src, crop_mode)
        graph = _filming_graph(ratio, width, height, config.get("background", "black"),
                               crop_mode, inset, inset=opts.get("background_inset", False))
        graph += (f";[r]setpts={stretch:.8f}*(PTS-STARTPTS),fps={media.FPS},"
                  f"tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},format=yuv420p[v]")
        # Without verified source separation, drop all original audio so dialogue
        # cannot leak into the generated narration, including background voices.
        media._run_ffmpeg(["-i", str(src), "-filter_complex", graph, "-map", "[v]", "-an", "-frames:v", str(frames), "-movflags", "+faststart", str(part)], d, target)
        parts.append(part)
        src.unlink(missing_ok=True)
    concat = d / f"film_{n}_list.txt"
    concat.write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
    draft = d / f"film_{n}_draft.mp4"
    if not _copy_episode(parts, frame_counts, concat, voice, draft, total_frames):
        af = "apad" if serial else "loudnorm=I=-16:TP=-2:LRA=11"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(voice), "-vf", f"setpts=N/({media.FPS}*TB)", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "fast", "-af", af, "-c:a", "aac", "-t", f"{total_frames / media.FPS:.6f}", "-movflags", "+faststart", str(draft)], check=True)
    ass = p.clip_asset_path(clip, "ass")
    ass.write_text(captions.build_ass(words, opts["style"], width, height), encoding="utf-8")
    srt = p.clip_asset_path(clip, "srt")
    srt.write_text(captions.build_srt(words, opts["style"], width, height), encoding="utf-8")
    out = p.clip_asset_path(clip, "mp4")
    media.render_clip(draft, ass, out, width, height, "crop", brand=opts["brand"], brand_position=opts["brand_position"])
    if media.probe_duration(out) > config["max_duration"]:
        out.unlink(missing_ok=True)
        raise ValueError(f"Episode {n}: final video exceeds the maximum duration")
    if getattr(p, "music", None):
        mixed = d / f"clip_{n}_music.mp4"
        media.mix_music(out, d / p.music, mixed, start=p.settings.get("music_start", 0), length=p.settings.get("music_length"))
        mixed.replace(out)
    frame = d / f"film_{n}_frame.jpg"
    if opts["thumb_cfg"].get("source") == "upload" and p.upload:
        thumbs.prepare(d / p.upload, frame, width, height)
    else:
        media.grab_frame(parts[0], frame, width, height, "crop")
    thumb = d / f"thumb_{n}.jpg"
    thumb_cfg = opts["thumb_cfg"]
    title_text = thumbs.thumbnail_text(thumb_cfg, clip, narration)
    thumbs.add_title(frame, thumb, clip["title"], "none" if thumb_cfg.get("text_mode") == "none" else thumb_cfg.get("title_style", "bold"), custom_text=title_text,
                     number=n, sequence=thumb_cfg.get("sequence") or ("number" if thumb_cfg.get("numbered") else "off"),
                     position=thumb_cfg.get("position"))
    if opts["brand"]:
        thumbs.add_brand(thumb, opts["brand"], opts["brand_position"], media.brand_margin(width, height))
    clip.update(video=out.name, thumb=thumb.name, srt=srt.name, ass=ass.name, narration=narration)
    draft.unlink(missing_ok=True)
    for part in parts:
        part.unlink(missing_ok=True)
