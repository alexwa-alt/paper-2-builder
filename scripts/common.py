"""Shared helpers for the extraction and tagging scripts."""
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
SYLLABUS_FILE = SITE / "data" / "syllabus.json"

PAPER_RE = re.compile(r"0625_([a-z])(\d{2})_qp_(\d{2})")


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def bank_paths(cfg, bank):
    """Where a bank's data and images live (all inside site/)."""
    b = cfg["banks"][bank]
    data = SITE / b["data_dir"]
    return {
        "data": data,
        "images": SITE / b["image_dir"],
        "image_prefix": b["image_dir"].strip("/"),
        "questions": data / "questions.json",
        "tags": data / "tags.json",
        "review": data / "review.json",
    }


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
    text = re.sub(r"_([a-z])\s+(\d{2})_", r"_\1\2_", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_paper(code):
    """'0625_s20_qp_23' -> session, year, component, tier."""
    m = PAPER_RE.fullmatch(code)
    if not m:
        return None
    session, yy, comp = m.groups()
    level = {"1": "core", "2": "extended"}.get(comp[0], "both")
    return {
        "paper": code,
        "session": session,
        "year": 2000 + int(yy),
        "component": int(comp),
        "level": level,
    }


def section_ranges(bank_cfg):
    """Sections from a bank's contents page with their printed page ranges.
    Sections with no questions (same start page as the next) get an empty range."""
    secs = bank_cfg["sections"]
    last = bank_cfg["answers_start_page"] - 1
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
