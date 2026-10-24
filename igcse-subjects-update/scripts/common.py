"""Shared helpers for the extraction and tagging scripts.

Also usable from the command line:
  python scripts/common.py asset biology-p6   # prints the release file name
  python scripts/common.py manifest           # rewrites site/data/manifest.json
"""
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MANIFEST_FILE = SITE / "data" / "manifest.json"

PAPER_RE = re.compile(r"(\d{4})_([a-z])(\d{2})_qp_(\d{2})")


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- subjects and banks
def iter_banks(cfg):
    """Yield (bank_id, bank) for every bank, in config order."""
    for skey in cfg["subjects"]:
        for bkey in cfg["subjects"][skey]["banks"]:
            yield f"{skey}-{bkey}", get_bank(cfg, f"{skey}-{bkey}")


def get_bank(cfg, bank_id):
    """A bank's settings plus its subject's name, code and syllabus."""
    if "-" not in bank_id:
        bank_id = f"physics-{bank_id}"          # old short names: p2, p6
    skey, bkey = bank_id.split("-", 1)
    try:
        subject = cfg["subjects"][skey]
        bank = dict(subject["banks"][bkey])
    except KeyError:
        ids = ", ".join(i for i, _ in iter_banks(cfg))
        sys.exit(f"Unknown bank {bank_id!r}. Choose from: {ids}")
    bank.update({"id": bank_id, "subject": skey, "subject_name": subject["name"],
                 "code": subject["code"], "data_root": subject["data_root"],
                 "syllabus": subject.get("syllabus", {})})
    return bank


def bank_paths(cfg, bank_id):
    """Where a bank's data and images live (all inside site/)."""
    b = get_bank(cfg, bank_id)
    data = SITE / b["data_dir"]
    return {
        "data": data,
        "images": SITE / b["image_dir"],
        "image_prefix": b["image_dir"].strip("/"),
        "questions": data / "questions.json",
        "tags": data / "tags.json",
        "review": data / "review.json",
        "syllabus": SITE / b["data_root"] / "syllabus.json",
    }


def write_manifest(cfg):
    """Tell the website which subjects and banks exist and which have questions."""
    subjects = []
    for skey, s in cfg["subjects"].items():
        banks = []
        for bkey, b in s["banks"].items():
            bid = f"{skey}-{bkey}"
            banks.append({
                "key": bid, "label": b["label"], "long": b["long"], "kind": b["kind"],
                "data": b["data_dir"],
                "available": (SITE / b["data_dir"] / "questions.json").exists(),
            })
        subjects.append({"key": skey, "name": s["name"], "code": s["code"],
                         "syllabus": f'{s["data_root"]}/syllabus.json', "banks": banks})
    save_json(MANIFEST_FILE, {"subjects": subjects, "skills": cfg.get("skills", {})})


# ---------------------------------------------------------------- files
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


# ---------------------------------------------------------------- text
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
    _, session, yy, comp = m.groups()
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
    Sections with no questions (same start page as the next) get an empty range.
    Returns [] when the bank has no contents page in the config."""
    secs = bank_cfg.get("sections") or []
    if not secs:
        return []
    last = bank_cfg.get("answers_start_page") or 10 ** 6
    last -= 1
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


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["asset"] and len(sys.argv) == 3:
        print(get_bank(load_config(), sys.argv[2])["release_asset"])
    elif cmd == ["manifest"]:
        write_manifest(load_config())
        print(f"Wrote {MANIFEST_FILE.relative_to(ROOT)}")
    else:
        sys.exit(__doc__)
