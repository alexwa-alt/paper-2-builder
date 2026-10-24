"""Stage 2: enrich questions with Gemini, a few at a time.

Designed to run on a schedule (see .github/workflows/tag.yml). Each run:
  1. reads each subject's syllabus PDF into a list of learning objectives,
     once (saved as syllabus.json in the subject's data folder)
  2. tags untagged questions bank by bank, in config order
  3. saves results after every batch
  4. stops early (without failing) when Gemini is busy or the free quota runs out
  5. Paper 2 only: marks re-scanned copies of the same question as repeats

Every Gemini answer is checked against fixed lists before it is saved, so a
bad response is simply retried on a later run.

Usage:
  GEMINI_API_KEY=... python scripts/tag.py
  python scripts/tag.py --max-requests 2      # a quick test
  python scripts/tag.py --bank biology-p4     # only one bank
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

from common import (ROOT, SITE, bank_paths, get_bank, iter_banks, load_config,
                    load_json, parse_json_text, save_json, write_manifest)

MCQ_TYPES = ["calculation", "graph_or_diagram", "conceptual", "practical"]
SKILL_HINTS = {
    "readings": "taking readings from instruments or diagrams",
    "tables": "recording results in tables",
    "graphs": "plotting, drawing or interpreting graphs",
    "calculations": "processing data",
    "conclusions": "drawing and justifying conclusions from results",
    "planning": "planning an investigation",
    "precautions": "precautions, accuracy, sources of error and improvements",
    "drawings": "making or labelling scientific drawings, such as of specimens",
    "tests": "chemical or food tests, identifying substances from observations",
}
THEORY_HINTS = {
    "recall": "stating facts, definitions or names",
    "explanation": "describing or explaining processes and observations",
    "calculation": "numerical working",
    "data": "reading, plotting or interpreting graphs, tables or diagrams",
    "application": "applying knowledge to an unfamiliar situation",
}


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
OUTLINE_PROMPT = """The attached PDF is the Cambridge IGCSE {subject} ({code}) syllabus.

List the numbered topics and subtopics of its subject content (not the practical or assessment sections).
Return JSON exactly in this form:
{{"topics": [{{"num": 1, "title": "...", "subtopics": [{{"code": "1.1", "title": "..."}}]}}]}}"""

SYLLABUS_PROMPT = """The attached PDF is the Cambridge IGCSE {subject} ({code}) syllabus.

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


def syllabus_outline(client, cfg, subject, pdf_part, data):
    """Topics and subtopics: from config.yaml if written there, else read from the PDF."""
    syl = subject["syllabus"]
    if syl.get("chapters") and syl.get("sections"):
        return syl["chapters"], syl["sections"]
    if not data.get("outline"):
        print(f'Reading the {subject["subject_name"]} syllabus outline')
        result = ask(client, cfg["gemini"]["model"], [pdf_part, OUTLINE_PROMPT.format(
            subject=subject["subject_name"], code=subject["code"])])
        topics = [t for t in result.get("topics", []) if isinstance(t, dict) and t.get("subtopics")]
        if not topics:
            raise ValueError("the syllabus outline came back empty")
        data["outline"] = {
            "chapters": [{"num": int(t["num"]), "title": str(t["title"])} for t in topics],
            "sections": [{"code": str(st["code"]), "title": str(st["title"])}
                         for t in topics for st in t["subtopics"] if isinstance(st, dict)],
        }
        time.sleep(cfg["gemini"]["seconds_between_requests"])
    return data["outline"]["chapters"], data["outline"]["sections"]


def build_syllabus(client, cfg, subject, path):
    """Turn a subject's syllabus PDF into a fixed list of learning objectives
    (once; progress is saved after every topic). Returns None if the PDF is missing."""
    from google.genai import types
    pdf = ROOT / subject["syllabus"]["pdf"]
    if not pdf.exists():
        print(f'{subject["subject_name"]}: no syllabus PDF at {subject["syllabus"]["pdf"]} '
              "- skipping its questions until it is added.")
        return None
    data = load_json(path, {"objectives": [], "chapters_done": []})
    pdf_part = types.Part.from_bytes(data=pdf.read_bytes(), mime_type="application/pdf")
    chapters, sections = syllabus_outline(client, cfg, subject, pdf_part, data)
    save_json(path, data)
    model = cfg["gemini"]["model"]
    for ch in chapters:
        if ch["num"] in data["chapters_done"]:
            continue
        secs = [s for s in sections if s["code"].split(".")[0] == str(ch["num"])]
        prompt = SYLLABUS_PROMPT.format(
            subject=subject["subject_name"], code=subject["code"],
            num=ch["num"], title=ch["title"],
            sections="\n".join(f'{s["code"]} {s["title"]}' for s in secs))
        print(f'Reading {subject["subject_name"]} syllabus topic {ch["num"]} {ch["title"]}')
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
        save_json(path, data)
        print(f"  {added} objectives")
        time.sleep(cfg["gemini"]["seconds_between_requests"])
    data["complete"] = all(ch["num"] in data["chapters_done"] for ch in chapters)
    save_json(path, data)
    return data


# ---------------------------------------------------------------- prompts
COMMON_FIELDS = """- "id": the id given.
- "subpoints": the learning-objective codes from the list above that the question assesses (1 to 3, most relevant first). Use only codes from the list.
- "section_ok": true if the question belongs in the section it is filed under, otherwise false.
- "suggested_section": if section_ok is false, the section code it belongs in (from the section list); otherwise null."""

MCQ_PROMPT = """You are tagging Cambridge IGCSE {subject} ({code}) multiple-choice questions.
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

STRUCTURED_PROMPT = """You are tagging Cambridge IGCSE {subject} ({code}) Paper 6 (Alternative to Practical) questions.
Each question below is one or more page images, preceded by its id and the section it is filed under. The pages of one question follow each other.

Sections it can be filed under (possibly an older syllabus layout):
{sections}

Learning objectives from the current syllabus for topic {chapter} ({C} = Core, {S} = Supplement):
{objectives}

For every question return:
""" + COMMON_FIELDS + """
- "skills": every practical skill the question assesses, from: {skills}.
- "difficulty": 1 (mostly routine readings and recall), 2 (standard), 3 (demanding analysis, planning or evaluation).
- "marks": the question's total mark, from "[Total: ...]" on its last page, as a number; null if you cannot see it.
- "text": a plain-text summary of 60 to 120 words: the experiment, the apparatus, and what each part asks. Write superscripts as ^.

Return JSON exactly in this form:
{{"results": [{{"id": "p6-1", "subpoints": ["1.2.3"], "section_ok": true, "suggested_section": null, "skills": ["readings", "graphs"], "difficulty": 2, "marks": 11, "text": "..."}}]}}"""


THEORY_PROMPT = """You are tagging Cambridge IGCSE {subject} ({code}) Paper 4 (Theory, Extended) questions.
Each question below is one or more page images, preceded by its id and the section it is filed under. The pages of one question follow each other.

Sections it can be filed under (possibly an older syllabus layout):
{sections}

Learning objectives from the current syllabus for topic {chapter} ({C} = Core, {S} = Supplement):
{objectives}

For every question return:
""" + COMMON_FIELDS + """
- "types": every kind of demand the question makes, from: {types}.
- "difficulty": 1 (mostly recall), 2 (standard), 3 (demanding: multi-step reasoning, extended explanation or unfamiliar contexts).
- "marks": the question's total mark, from "[Total: ...]" on its last page, as a number; null if you cannot see it.
- "text": a plain-text summary of 60 to 120 words: the context and what each part asks. Write superscripts as ^.

Return JSON exactly in this form:
{{"results": [{{"id": "p4-1", "subpoints": ["1.2.3"], "section_ok": true, "suggested_section": null, "types": ["recall", "explanation"], "difficulty": 2, "marks": 12, "text": "..."}}]}}"""


def validate(item, kind, valid_codes, valid_sections, skills_allowed, focus="practical"):
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
        field = "types" if focus == "theory" else "skills"
        values = [s for s in item.get(field) or [] if s in skills_allowed]
        if not values:
            return None
        out[field] = list(dict.fromkeys(values))
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


STOPWORDS = {"and", "of", "the", "in", "into", "out", "on", "a", "an", "to", "their",
             "including", "physics", "biology", "chemistry"}


def title_words(title):
    return {w for w in re.findall(r"[a-z]+", title.lower()) if w not in STOPWORDS}


def syllabus_topic(bank_title, chapter, syllabus_chapters):
    """Which syllabus topic a bank chapter corresponds to. Older banks number
    their topics differently from the current syllabus (Biology added a topic,
    for example), so match on the title first and fall back to the number."""
    words = title_words(bank_title or "")
    best, score = None, 0.0
    for ch in syllabus_chapters:
        other = title_words(ch["title"])
        if words and other:
            s = len(words & other) / min(len(words), len(other))
            # on a tie, prefer the topic numbered closest to the bank's
            if s > score or (s == score and best is not None
                             and abs(ch["num"] - chapter) < abs(best - chapter)):
                best, score = ch["num"], s
    return best if score >= 0.5 else chapter


def tag_batch(client, cfg, bank, batch, chapter, syllabus, sections, tags, chapter_title=""):
    gcfg = cfg["gemini"]
    kind = bank["kind"]
    focus = bank.get("focus", "practical")
    if focus == "theory":
        labels, hints = cfg.get("theory_types", {}), THEORY_HINTS
    else:
        labels, hints = cfg.get("skills", {}), SKILL_HINTS
    skills_allowed = list(labels)
    syllabus_chapters = (bank["syllabus"].get("chapters")
                         or (syllabus.get("outline") or {}).get("chapters") or [])
    topic = syllabus_topic(chapter_title, chapter, syllabus_chapters)
    objectives = [o for o in syllabus["objectives"] if o["code"].split(".")[0] == str(topic)]
    if len(objectives) < 3:   # the bank's topic numbers don't line up with the syllabus
        objectives = syllabus["objectives"]
    valid_codes = {o["code"] for o in syllabus["objectives"]}
    valid_sections = {s["code"] for s in sections}
    titles = {s["code"]: s["title"] for s in sections}
    template = (MCQ_PROMPT if kind == "mcq"
                else THEORY_PROMPT if focus == "theory" else STRUCTURED_PROMPT)
    prompt = template.format(
        subject=bank["subject_name"], code=bank["code"],
        skills=", ".join(f'"{k}" ({hints.get(k, labels[k])})' for k in skills_allowed),
        types=", ".join(f'"{k}" ({hints.get(k, labels[k])})' for k in skills_allowed),
        sections="\n".join(f'{s["code"]} {s["title"]}' for s in sections),
        chapter=topic, C="C", S="S",
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
        clean = validate(returned.get(q["id"], {}), kind, valid_codes, valid_sections,
                         skills_allowed, focus)
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
def load_bank(cfg, bank_id):
    paths = bank_paths(cfg, bank_id)
    qdata = load_json(paths["questions"])
    if not qdata:
        return None
    return {"id": bank_id, "cfg": get_bank(cfg, bank_id), "paths": paths,
            "questions": qdata["questions"], "sections": qdata["sections"],
            "chapter_titles": {c["num"]: c["title"] for c in qdata.get("chapters", [])},
            "tags": load_json(paths["tags"], {})}


def pending_in(bank):
    return [q for q in bank["questions"]
            if q.get("section") and bank["tags"].get(q["id"], {}).get("status") in (None, "retry")]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-requests", type=int, help="override max_requests_per_run")
    ap.add_argument("--bank", help="only tag this bank, e.g. biology-p6")
    ap.add_argument("--repeats-only", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    gcfg = cfg["gemini"]
    ids = [get_bank(cfg, args.bank)["id"]] if args.bank else [i for i, _ in iter_banks(cfg)]
    banks = [b for b in (load_bank(cfg, i) for i in ids) if b]
    write_manifest(cfg)
    if not banks:
        print("No questions yet - run the extraction first (scripts/extract.py).")
        return

    if args.repeats_only:
        for b in banks:
            if b["cfg"]["kind"] == "mcq":
                n = mark_repeats(b["questions"], b["tags"])
                save_json(b["paths"]["tags"], b["tags"])
                print(f"{b['id']}: marked {n} repeats.")
        return

    if not any(pending_in(b) for b in banks):
        print("Every question is tagged. You can disable the tagging workflow.")
        return

    client = make_client()
    max_requests = args.max_requests or gcfg["max_requests_per_run"]
    requests = 0
    syllabi = {}
    try:
        for b in banks:
            if not pending_in(b):
                continue
            subject = b["cfg"]["subject"]
            if subject not in syllabi:
                path = b["paths"]["syllabus"]
                data = load_json(path)
                if not data or not (data.get("complete") or _physics_done(cfg, b["cfg"], data)):
                    data = build_syllabus(client, cfg, b["cfg"], path)
                    requests += 3
                    if data and not data.get("complete"):
                        print(f"{b['cfg']['subject_name']} syllabus not complete yet; "
                              "tagging will start once it is.")
                        data = None
                syllabi[subject] = data
            syllabus = syllabi[subject]
            if not syllabus:
                continue
            size = b["cfg"]["questions_per_request"]
            by_chapter = {}
            for q in pending_in(b):
                by_chapter.setdefault(q["chapter"], []).append(q)
            batches = [(ch, qs[i:i + size]) for ch, qs in sorted(by_chapter.items())
                       for i in range(0, len(qs), size)]
            for chapter, batch in batches:
                if requests >= max_requests:
                    break
                print(f"[{b['id']}] tagging {', '.join(q['id'] for q in batch)}")
                tag_batch(client, cfg, b["cfg"], batch, chapter, syllabus, b["sections"], b["tags"],
                          b["chapter_titles"].get(chapter, ""))
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
        print(f"{b['id']}: tagged {tagged}/{len(b['questions'])} questions "
              f"({failed} gave up after {gcfg['max_attempts']} attempts{extra}).")


def _physics_done(cfg, bank, data):
    """Syllabus files written before 'complete' existed: done if every topic is in."""
    chapters = bank["syllabus"].get("chapters") or (data.get("outline") or {}).get("chapters") or []
    return bool(chapters) and all(c["num"] in data.get("chapters_done", []) for c in chapters)


if __name__ == "__main__":
    main()
