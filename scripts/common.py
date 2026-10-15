"""Shared helpers for the extraction and tagging scripts."""
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = SITE / "data"
IMAGES = SITE / "images"

QUESTIONS_FILE = DATA / "questions.json"
TAGS_FILE = DATA / "tags.json"
SYLLABUS_FILE = DATA / "syllabus.json"
REVIEW_FILE = DATA / "review.json"

PAPER_RE = re.compile(r"0625_([msw])(\d{2})_qp_(\d{2})")
SESSIONS = {"m": "Feb/Mar", "s": "May/Jun", "w": "Oct/Nov"}


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj, compact=False):
    """Write JSON atomically so an interrupted run never leaves a broken file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def norm(text):
    """Normalise extracted text: rejoin paper codes split around underscores."""
    text = re.sub(r"\s*_\s*", "_", text)
    text = re.sub(r"_([msw])\s+(\d{2})_", r"_\1\2_", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_paper(code):
    """'0625_s20_qp_23' -> session, year, component, Core/Extended."""
    m = PAPER_RE.fullmatch(code)
    if not m:
        return None
    session, yy, comp = m.groups()
    level = {"1": "core", "2": "extended"}.get(comp[0], "other")
    return {
        "paper": code,
        "session": session,
        "year": 2000 + int(yy),
        "component": int(comp),
        "level": level,
    }


def section_ranges(cfg):
    """Sections from the contents page with their printed page ranges."""
    secs = cfg["sections"]
    last = cfg["pdf"]["answers_start_page"] - 1
    out = []
    for i, s in enumerate(secs):
        end = secs[i + 1]["start_page"] - 1 if i + 1 < len(secs) else last
        out.append({
            "code": s["code"],
            "title": s["title"],
            "chapter": int(s["code"].split(".")[0]),
            "start": s["start_page"],
            "end": end,
        })
    return out


def parse_json_text(text):
    """Parse a model response that should be JSON, tolerating code fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)
