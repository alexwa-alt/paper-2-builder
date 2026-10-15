"""Stage 2: enrich questions with Gemini, a few at a time.

Designed to run on a schedule (see .github/workflows/tag.yml). Each run:
  1. builds site/data/syllabus.json from the syllabus PDF if it doesn't exist yet
  2. sends a few batches of untagged questions to Gemini
  3. saves results to site/data/tags.json after every batch
  4. stops early (without failing) when the free quota runs out
  5. marks re-scanned copies of the same question as repeats

Every Gemini answer is checked against fixed lists before it is saved, so a
bad response is simply retried on a later run.

Usage:
  GEMINI_API_KEY=... python scripts/tag.py
  python scripts/tag.py --max-requests 2      # a quick test
  python scripts/tag.py --repeats-only        # just redo the repeat detection
"""
import argparse
import datetime as dt
import os
import re
import sys
import time

import numpy as np
from PIL import Image

from common import (QUESTIONS_FILE, ROOT, SITE, SYLLABUS_FILE, TAGS_FILE,
                    load_config, load_json, parse_json_text, save_json)

TYPES = ["calculation", "graph_or_diagram", "conceptual", "practical"]


class QuotaExhausted(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- Gemini
def make_client():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY is not set. Add it as a repository secret "
                 "(Settings > Secrets and variables > Actions).")
    from google import genai
    return genai.Client(api_key=key)


def ask(client, model, parts):
    """One Gemini request returning parsed JSON. Retries once on a per-minute
    rate limit; raises QuotaExhausted if the limit persists."""
    from google.genai import errors, types
    config = types.GenerateContentConfig(response_mime_type="application/json",
                                         temperature=0.2)
    for attempt in range(2):
        try:
            resp = client.models.generate_content(model=model, contents=parts,
                                                  config=config)
            return parse_json_text(resp.text or "")
        except errors.APIError as e:
            if getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e):
                if attempt == 0:
                    print("  rate limited - waiting 65 s and trying once more")
                    time.sleep(65)
                    continue
                raise QuotaExhausted(str(e)[:200])
            raise


def image_part(path):
    from google.genai import types
    return types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png")


# ---------------------------------------------------------------- syllabus
SYLLABUS_PROMPT = """The attached PDF is the Cambridge IGCSE Physics (0625) syllabus for 2023, 2024 and 2025.

List every numbered learning objective in the subject content for topic {num} "{title}".
Its subtopics are:
{sections}

Each subtopic (or sub-subtopic, such as 1.5.1) lists Core points and Supplement points.
Return JSON exactly in this form:
{{"objectives": [{{"code": "1.2.3", "section": "1.2", "level": "core", "text": "..."}}]}}

Rules:
- code: the deepest numbered heading the point sits under, then a dot, then the point's own number as printed (point 3 under 1.5.1 gives "1.5.1.3").
- section: the two-part subtopic code, such as "1.5".
- level: "core" or "supplement".
- text: the objective's wording, shortened to at most 25 words if longer.
- Include every point in this topic and nothing from other topics. Do not invent points."""


def build_syllabus(client, cfg):
    """Turn the syllabus PDF into a fixed list of learning objectives (once)."""
    from google.genai import types
    pdf = ROOT / cfg["pdf"]["syllabus"]
    if not pdf.exists():
        sys.exit(f"Syllabus PDF not found at {cfg['pdf']['syllabus']}. "
                 "Download the 0625 syllabus for 2023-2025 from Cambridge and add it there.")
    data = load_json(SYLLABUS_FILE, {"objectives": [], "chapters_done": []})
    pdf_part = types.Part.from_bytes(data=pdf.read_bytes(), mime_type="application/pdf")
    model = cfg["gemini"]["model"]
    for ch in cfg["chapters"]:
        if ch["num"] in data["chapters_done"]:
            continue
        secs = [s for s in cfg["sections"] if s["code"].startswith(f'{ch["num"]}.')]
        prompt = SYLLABUS_PROMPT.format(
            num=ch["num"], title=ch["title"],
            sections="\n".join(f'{s["code"]} {s["title"]}' for s in secs))
        print(f'Reading syllabus topic {ch["num"]} {ch["title"]}')
        result = ask(client, model, [pdf_part, prompt])
        seen = {o["code"] for o in data["objectives"]}
        valid_sections = {s["code"] for s in secs}
        added = 0
        for o in result.get("objectives", []):
            code = str(o.get("code", "")).strip()
            section = str(o.get("section", "")).strip()
            if section not in valid_sections or not code.startswith(section + "."):
                continue
            level = "supplement" if str(o.get("level", "")).lower().startswith("s") else "core"
            if code in seen:  # some syllabuses restart numbering for Supplement
                code += "s"
            if code in seen:
                continue
            seen.add(code)
            data["objectives"].append({"code": code, "section": section,
                                       "level": level, "text": str(o.get("text", "")).strip()})
            added += 1
        if added == 0:
            print(f"  no valid objectives returned for topic {ch['num']} - will retry next run")
            continue
        data["chapters_done"].append(ch["num"])
        data["generated"] = now()
        save_json(SYLLABUS_FILE, data)
        print(f"  {added} objectives")
        time.sleep(cfg["gemini"]["seconds_between_requests"])
    return data


# ---------------------------------------------------------------- tagging
TAG_PROMPT = """You are tagging Cambridge IGCSE Physics (0625) multiple-choice questions.
Each question below is an image, preceded by its id, the syllabus section it is filed under, and its correct answer.

Syllabus sections:
{sections}

Learning objectives for topic {chapter} ({C} = Core, {S} = Supplement):
{objectives}

For every question return:
- "id": the id given.
- "subpoints": the learning-objective codes from the list above that the question assesses (1 to 3, most relevant first). Use only codes from the list.
- "section_ok": true if the question belongs in the section it is filed under, otherwise false.
- "suggested_section": if section_ok is false, the section code it belongs in; otherwise null.
- "type": one of "calculation" (a numerical answer must be worked out), "graph_or_diagram" (mainly reading or interpreting a graph or diagram), "practical" (experimental method, instruments or measurement technique), "conceptual" (anything else).
- "difficulty": 1 (recall or a single simple step), 2 (standard application), 3 (several steps, or a common misconception is the main trap).
- "text": a plain-text transcription of the question stem and options A to D. Write superscripts as ^ (m/s^2). Describe any diagram or graph briefly in square brackets.

Return JSON exactly in this form:
{{"results": [{{"id": "q1", "subpoints": ["1.2.3"], "section_ok": true, "suggested_section": null, "type": "calculation", "difficulty": 2, "text": "..."}}]}}"""


def validate(item, valid_codes, valid_sections):
    """Return a clean tag record, or None if the answer is unusable."""
    subpoints = [str(c).strip() for c in item.get("subpoints") or []]
    subpoints = [c for c in subpoints if c in valid_codes][:3]
    section_ok = item.get("section_ok")
    if not isinstance(section_ok, bool):
        return None
    suggested = item.get("suggested_section")
    suggested = suggested if suggested in valid_sections else None
    if not section_ok and suggested is None:
        section_ok = True  # a "misfiled" claim without a valid target is ignored
    if not subpoints and section_ok:
        return None
    qtype = item.get("type")
    try:
        difficulty = int(item.get("difficulty"))
    except (TypeError, ValueError):
        return None
    text = str(item.get("text") or "").strip()
    if qtype not in TYPES or difficulty not in (1, 2, 3) or len(text) < 10:
        return None
    return {"status": "done", "subpoints": subpoints, "section_ok": section_ok,
            "suggested_section": None if section_ok else suggested,
            "type": qtype, "difficulty": difficulty, "text": text}


def record_failure(tags, qid, reason, max_attempts):
    t = tags.get(qid) or {}
    attempts = t.get("attempts", 0) + 1
    tags[qid] = {"status": "failed" if attempts >= max_attempts else "retry",
                 "attempts": attempts, "error": reason[:200], "updated": now()}


def tag_batch(client, cfg, batch, chapter, syllabus, sections_text, tags):
    gcfg = cfg["gemini"]
    objectives = [o for o in syllabus["objectives"] if o["code"].startswith(f"{chapter}.")]
    valid_codes = {o["code"] for o in syllabus["objectives"]}
    valid_sections = {s["code"] for s in cfg["sections"]}
    titles = {s["code"]: s["title"] for s in cfg["sections"]}
    prompt = TAG_PROMPT.format(
        sections=sections_text, chapter=chapter, C="C", S="S",
        objectives="\n".join(f'{o["code"]} [{"C" if o["level"] == "core" else "S"}] {o["text"]}'
                             for o in objectives))
    parts = [prompt]
    for q in batch:
        parts.append(f'Question id {q["id"]}: filed under {q["section"]} '
                     f'{titles.get(q["section"], "")}; correct answer {q["answer"] or "unknown"}.')
        parts.append(image_part(SITE / q["image"]))

    try:
        result = ask(client, gcfg["model"], parts)
    except QuotaExhausted:
        raise
    except Exception as e:  # malformed JSON, server error, etc.
        for q in batch:
            record_failure(tags, q["id"], f"request failed: {e}", gcfg["max_attempts"])
        return 0

    returned = {str(r.get("id")): r for r in result.get("results", []) if isinstance(r, dict)}
    ok = 0
    for q in batch:
        clean = validate(returned.get(q["id"], {}), valid_codes, valid_sections)
        if clean is None:
            record_failure(tags, q["id"], "missing or invalid tags in response",
                           gcfg["max_attempts"])
            continue
        clean.update({"model": gcfg["model"], "updated": now()})
        tags[q["id"]] = clean
        ok += 1
    return ok


# ---------------------------------------------------------------- repeats
def text_key(text):
    """Normalise a transcription so re-scans of the same question compare equal."""
    text = re.sub(r"\[[^\]]*\]", " ", text.lower())   # drop diagram descriptions
    return re.sub(r"[^a-z0-9.]+", "", text)


def small_hash(path):
    img = Image.open(path).convert("L").resize((9, 8), Image.LANCZOS)
    a = np.asarray(img, dtype=np.int16)
    return int("".join("1" if b else "0" for b in (a[:, 1:] > a[:, :-1]).flatten()), 2)


def mark_repeats(questions, tags):
    """Mark questions whose transcription and answer match an earlier question,
    and whose images look alike, as repeats of it. The website hides repeats
    and lists their paper references under the original."""
    for t in tags.values():
        t.pop("repeat_of", None)
    groups = {}
    for q in questions:
        t = tags.get(q["id"])
        if not t or t.get("status") != "done" or not q.get("answer"):
            continue
        key = (q["chapter"], q["answer"], text_key(t["text"]))
        if len(key[2]) < 30:
            continue
        groups.setdefault(key, []).append(q)
    marked = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        first = group[0]
        h0 = small_hash(SITE / first["image"])
        for q in group[1:]:
            size_close = abs(q["w_mm"] - first["w_mm"]) < 2 and abs(q["h_mm"] - first["h_mm"]) < 2
            if size_close and (small_hash(SITE / q["image"]) ^ h0).bit_count() <= 10:
                tags[q["id"]]["repeat_of"] = first["id"]
                marked += 1
    return marked


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-requests", type=int, help="override max_requests_per_run")
    ap.add_argument("--repeats-only", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    gcfg = cfg["gemini"]
    qdata = load_json(QUESTIONS_FILE)
    if not qdata:
        print("No questions yet - run the extraction first (scripts/extract.py).")
        return
    questions = qdata["questions"]
    tags = load_json(TAGS_FILE, {})

    if args.repeats_only:
        n = mark_repeats(questions, tags)
        save_json(TAGS_FILE, tags)
        print(f"Marked {n} repeats.")
        return

    pending = [q for q in questions
               if q.get("section") and tags.get(q["id"], {}).get("status") in (None, "retry")]
    if not pending:
        print("Every question is tagged. You can disable the tagging workflow.")
        return

    client = make_client()
    max_requests = args.max_requests or gcfg["max_requests_per_run"]
    requests = 0
    try:
        syllabus = load_json(SYLLABUS_FILE)
        if not syllabus or len(syllabus.get("chapters_done", [])) < len(cfg["chapters"]):
            syllabus = build_syllabus(client, cfg)
            requests += len(cfg["chapters"])
            if len(syllabus.get("chapters_done", [])) < len(cfg["chapters"]):
                print("Syllabus not complete yet; tagging will start once it is.")
                return

        sections_text = "\n".join(f'{s["code"]} {s["title"]}' for s in cfg["sections"])
        by_chapter = {}
        for q in pending:
            by_chapter.setdefault(q["chapter"], []).append(q)
        size = gcfg["questions_per_request"]
        batches = [(ch, qs[i:i + size]) for ch, qs in sorted(by_chapter.items())
                   for i in range(0, len(qs), size)]

        done = 0
        for chapter, batch in batches:
            if requests >= max_requests:
                break
            print(f"Tagging {', '.join(q['id'] for q in batch)}")
            done += tag_batch(client, cfg, batch, chapter, syllabus, sections_text, tags)
            requests += 1
            save_json(TAGS_FILE, tags)
            time.sleep(gcfg["seconds_between_requests"])
    except QuotaExhausted as e:
        print(f"Free quota used up for now ({e}). The next scheduled run will continue.")
    finally:
        n = mark_repeats(questions, tags)
        save_json(TAGS_FILE, tags)

    total = len(questions)
    tagged = sum(1 for t in tags.values() if t.get("status") == "done")
    failed = sum(1 for t in tags.values() if t.get("status") == "failed")
    print(f"\nTagged {tagged}/{total} questions ({failed} gave up after "
          f"{gcfg['max_attempts']} attempts, {n} marked as repeats).")


if __name__ == "__main__":
    main()
