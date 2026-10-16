"""Stage 2: enrich questions with Gemini, a few at a time.

Designed to run on a schedule (see .github/workflows/tag.yml). Each run:
  1. builds site/data/syllabus.json from the syllabus PDF if it doesn't exist yet
  2. tags untagged questions bank by bank (Paper 2 first, then Paper 6)
  3. saves results after every batch
  4. stops early (without failing) when Gemini is busy or the free quota runs out
  5. Paper 2 only: marks re-scanned copies of the same question as repeats

Every Gemini answer is checked against fixed lists before it is saved, so a
bad response is simply retried on a later run.

Usage:
  GEMINI_API_KEY=... python scripts/tag.py
  python scripts/tag.py --max-requests 2      # a quick test
  python scripts/tag.py --bank p6             # only one bank
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

from common import (ROOT, SITE, SYLLABUS_FILE, bank_paths, load_config,
                    load_json, parse_json_text, save_json)

MCQ_TYPES = ["calculation", "graph_or_diagram", "conceptual", "practical"]
SKILLS = ["readings", "tables", "graphs", "calculations", "conclusions",
          "planning", "precautions"]


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
    """One Gemini request returning parsed JSON.

    Rate limits (429) and temporary overloads (500/503) are retried a few
    times with increasing waits. If they persist, QuotaExhausted is raised so
    the run stops cleanly and the next scheduled run carries on.
    """
    from google.genai import errors, types
    config = types.GenerateContentConfig(response_mime_type="application/json",
                                         temperature=0.2)
    waits = [30, 90, 180]
    for attempt in range(len(waits) + 1):
        try:
            resp = client.models.generate_content(model=model, contents=parts,
                                                  config=config)
            return parse_json_text(resp.text or "")
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code == 404:
                sys.exit(f"Gemini model '{model}' was not found: {str(e)[:300]}\n"
                         "Update gemini.model in config.yaml.")
            temporary = code in (429, 500, 502, 503, 504) or any(
                s in str(e) for s in ("RESOURCE_EXHAUSTED", "UNAVAILABLE"))
            if not temporary:
                raise
            if attempt == len(waits):
                raise QuotaExhausted(str(e)[:200])
            print(f"  Gemini busy or rate limited ({code}) - waiting {waits[attempt]} s")
            time.sleep(waits[attempt])


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
    syl = cfg["syllabus"]
    pdf = ROOT / syl["pdf"]
    if not pdf.exists():
        sys.exit(f"Syllabus PDF not found at {syl['pdf']}. "
                 "Download the 0625 syllabus for 2023-2025 from Cambridge and add it there.")
    data = load_json(SYLLABUS_FILE, {"objectives": [], "chapters_done": []})
    pdf_part = types.Part.from_bytes(data=pdf.read_bytes(), mime_type="application/pdf")
    model = cfg["gemini"]["model"]
    for ch in syl["chapters"]:
        if ch["num"] in data["chapters_done"]:
            continue
        secs = [s for s in syl["sections"] if s["code"].startswith(f'{ch["num"]}.')]
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


# ---------------------------------------------------------------- prompts
COMMON_FIELDS = """- "id": the id given.
- "subpoints": the learning-objective codes from the list above that the question assesses (1 to 3, most relevant first). Use only codes from the list.
- "section_ok": true if the question belongs in the section it is filed under, otherwise false.
- "suggested_section": if section_ok is false, the section code it belongs in (from the section list); otherwise null."""

MCQ_PROMPT = """You are tagging Cambridge IGCSE Physics (0625) multiple-choice questions.
Each question below is an image, preceded by its id, the section it is filed under, and its correct answer.

Sections it can be filed under:
{sections}

Learning objectives for topic {chapter} ({C} = Core, {S} = Supplement):
{objectives}

For every question return:
""" + COMMON_FIELDS + """
- "type": one of "calculation" (a numerical answer must be worked out), "graph_or_diagram" (mainly reading or interpreting a graph or diagram), "practical" (experimental method, instruments or measurement technique), "conceptual" (anything else).
- "difficulty": 1 (recall or a single simple step), 2 (standard application), 3 (several steps, or a common misconception is the main trap).
- "text": a plain-text transcription of the question stem and options A to D. Write superscripts as ^ (m/s^2). Describe any diagram or graph briefly in square brackets.

Return JSON exactly in this form:
{{"results": [{{"id": "q1", "subpoints": ["1.2.3"], "section_ok": true, "suggested_section": null, "type": "calculation", "difficulty": 2, "text": "..."}}]}}"""

STRUCTURED_PROMPT = """You are tagging Cambridge IGCSE Physics (0625) Paper 6 (Alternative to Practical) questions.
Each question below is one or more page images, preceded by its id and the section it is filed under. The pages of one question follow each other.

Sections it can be filed under (an older syllabus layout):
{sections}

Learning objectives from the current syllabus for topic {chapter} ({C} = Core, {S} = Supplement):
{objectives}

For every question return:
""" + COMMON_FIELDS + """
- "skills": every practical skill the question assesses, from: "readings" (taking readings from instruments or diagrams), "tables" (recording results in tables), "graphs" (plotting, drawing or interpreting graphs), "calculations" (processing data), "conclusions" (drawing and justifying conclusions from results), "planning" (planning an investigation), "precautions" (precautions, accuracy, sources of error and improvements).
- "difficulty": 1 (mostly routine readings and recall), 2 (standard), 3 (demanding analysis, planning or evaluation).
- "marks": the question's total mark, from "[Total: ...]" on its last page, as a number; null if you cannot see it.
- "text": a plain-text summary of 60 to 120 words: the experiment, the apparatus, and what each part asks. Write superscripts as ^.

Return JSON exactly in this form:
{{"results": [{{"id": "p6-1", "subpoints": ["1.2.3"], "section_ok": true, "suggested_section": null, "skills": ["readings", "graphs"], "difficulty": 2, "marks": 11, "text": "..."}}]}}"""


def validate(item, kind, valid_codes, valid_sections):
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
    try:
        difficulty = int(item.get("difficulty"))
    except (TypeError, ValueError):
        return None
    text = str(item.get("text") or "").strip()
    if difficulty not in (1, 2, 3) or len(text) < 10:
        return None
    out = {"status": "done", "subpoints": subpoints, "section_ok": section_ok,
           "suggested_section": None if section_ok else suggested,
           "difficulty": difficulty, "text": text}
    if kind == "mcq":
        if item.get("type") not in MCQ_TYPES:
            return None
        out["type"] = item["type"]
    else:
        skills = [s for s in item.get("skills") or [] if s in SKILLS]
        if not skills:
            return None
        out["skills"] = list(dict.fromkeys(skills))
        try:
            marks = int(item.get("marks"))
            out["marks"] = marks if 1 <= marks <= 60 else None
        except (TypeError, ValueError):
            out["marks"] = None
    return out


def record_failure(tags, qid, reason, max_attempts):
    t = tags.get(qid) or {}
    attempts = t.get("attempts", 0) + 1
    tags[qid] = {"status": "failed" if attempts >= max_attempts else "retry",
                 "attempts": attempts, "error": reason[:200], "updated": now()}


def question_images(q):
    pages = q.get("pages") or [{"image": q["image"]}]
    return [SITE / p["image"] for p in pages]


def tag_batch(client, cfg, kind, batch, chapter, syllabus, sections, tags):
    gcfg = cfg["gemini"]
    objectives = [o for o in syllabus["objectives"] if o["code"].startswith(f"{chapter}.")]
    valid_codes = {o["code"] for o in syllabus["objectives"]}
    valid_sections = {s["code"] for s in sections}
    titles = {s["code"]: s["title"] for s in sections}
    template = MCQ_PROMPT if kind == "mcq" else STRUCTURED_PROMPT
    prompt = template.format(
        sections="\n".join(f'{s["code"]} {s["title"]}' for s in sections),
        chapter=chapter, C="C", S="S",
        objectives="\n".join(f'{o["code"]} [{"C" if o["level"] == "core" else "S"}] {o["text"]}'
                             for o in objectives))
    parts = [prompt]
    for q in batch:
        intro = f'Question id {q["id"]}: filed under {q["section"]} {titles.get(q["section"], "")}'
        if kind == "mcq":
            intro += f'; correct answer {q["answer"] or "unknown"}'
        parts.append(intro + ".")
        parts.extend(image_part(p) for p in question_images(q))

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
        clean = validate(returned.get(q["id"], {}), kind, valid_codes, valid_sections)
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
    """Paper 2: mark questions whose transcription and answer match an earlier
    question, and whose images look alike, as repeats of it. The website hides
    repeats and lists their paper references under the original."""
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
def load_bank(cfg, name):
    paths = bank_paths(cfg, name)
    qdata = load_json(paths["questions"])
    if not qdata:
        return None
    return {"name": name, "cfg": cfg["banks"][name], "paths": paths,
            "questions": qdata["questions"], "sections": qdata["sections"],
            "tags": load_json(paths["tags"], {})}


def pending_in(bank):
    return [q for q in bank["questions"]
            if q.get("section") and bank["tags"].get(q["id"], {}).get("status") in (None, "retry")]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-requests", type=int, help="override max_requests_per_run")
    ap.add_argument("--bank", help="only tag this bank (p2, p6)")
    ap.add_argument("--repeats-only", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    gcfg = cfg["gemini"]
    names = [args.bank] if args.bank else list(cfg["banks"])
    banks = [b for b in (load_bank(cfg, n) for n in names) if b]
    if not banks:
        print("No questions yet - run the extraction first (scripts/extract.py).")
        return

    if args.repeats_only:
        for b in banks:
            if b["cfg"]["kind"] == "mcq":
                n = mark_repeats(b["questions"], b["tags"])
                save_json(b["paths"]["tags"], b["tags"])
                print(f"{b['name']}: marked {n} repeats.")
        return

    if not any(pending_in(b) for b in banks):
        print("Every question is tagged. You can disable the tagging workflow.")
        return

    client = make_client()
    max_requests = args.max_requests or gcfg["max_requests_per_run"]
    requests = 0
    try:
        syllabus = load_json(SYLLABUS_FILE)
        chapters = cfg["syllabus"]["chapters"]
        if not syllabus or len(syllabus.get("chapters_done", [])) < len(chapters):
            syllabus = build_syllabus(client, cfg)
            requests += len(chapters)
            if len(syllabus.get("chapters_done", [])) < len(chapters):
                print("Syllabus not complete yet; tagging will start once it is.")
                return

        for b in banks:
            kind = b["cfg"]["kind"]
            size = b["cfg"]["questions_per_request"]
            by_chapter = {}
            for q in pending_in(b):
                by_chapter.setdefault(q["chapter"], []).append(q)
            batches = [(ch, qs[i:i + size]) for ch, qs in sorted(by_chapter.items())
                       for i in range(0, len(qs), size)]
            for chapter, batch in batches:
                if requests >= max_requests:
                    break
                print(f"[{b['name']}] tagging {', '.join(q['id'] for q in batch)}")
                tag_batch(client, cfg, kind, batch, chapter, syllabus, b["sections"], b["tags"])
                requests += 1
                save_json(b["paths"]["tags"], b["tags"])
                time.sleep(gcfg["seconds_between_requests"])
    except QuotaExhausted as e:
        print(f"Gemini is busy or the free quota is used up for now ({e}). "
              "The next scheduled run will continue.")
    finally:
        for b in banks:
            if b["cfg"]["kind"] == "mcq":
                b["repeats"] = mark_repeats(b["questions"], b["tags"])
            save_json(b["paths"]["tags"], b["tags"])

    print()
    for b in banks:
        tags = b["tags"]
        tagged = sum(1 for t in tags.values() if t.get("status") == "done")
        failed = sum(1 for t in tags.values() if t.get("status") == "failed")
        extra = f", {b['repeats']} marked as repeats" if "repeats" in b else ""
        print(f"{b['name']}: tagged {tagged}/{len(b['questions'])} questions "
              f"({failed} gave up after {gcfg['max_attempts']} attempts{extra}).")


if __name__ == "__main__":
    main()
