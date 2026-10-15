"""Stage 1: split the question bank PDF into individual questions.

No AI involved. For every question this script:
  * finds its reference line, e.g. "22. 0625_s20_qp_23 Q: 1"
  * renders the question image(s) that follow it
  * works out its chapter and section from the page headings
  * joins its answer from the answer-key table at the back
  * merges visually identical duplicates (keeping every reference)

Outputs (all inside site/ so the website can use them directly):
  site/images/<sn>.png      one image per unique question
  site/data/questions.json  the question list
  site/data/review.json     anything that looked wrong and is worth a glance

Usage:
  python scripts/extract.py                      # uses config.yaml paths
  python scripts/extract.py --pdf sample.pdf     # try it on a few pages first
"""
import argparse
import datetime as dt
import re
import sys
from collections import defaultdict

import numpy as np
import pymupdf
from PIL import Image

from common import (IMAGES, QUESTIONS_FILE, REVIEW_FILE, ROOT, load_config,
                    norm, parse_paper, save_json, section_ranges)

ANCHOR_RE = re.compile(r"^(\d+)\.\s*(0625_[msw]\d{2}_qp_\d{2})\s*Q\s*:\s*(\d+)\b")
SECTION_RE = re.compile(r"^([1-9])\.(\d{1,2})\s+([A-Za-z].*)$")
ANSWER_RE = re.compile(r"(\d+)\s+(0625_[msw]\d{2}_qp_\d{2})\s+(\d+)\s+([ABCD])\b")


# ---------------------------------------------------------------- text layer
def page_lines(page, tol=3.0):
    """Group words into visual lines (works across side-by-side tables too)."""
    words = page.get_text("words")
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines = []
    for w in words:
        yc = (w[1] + w[3]) / 2
        if lines and abs(lines[-1]["yc"] - yc) <= tol:
            line = lines[-1]
            line["words"].append(w)
            line["y0"] = min(line["y0"], w[1])
            line["y1"] = max(line["y1"], w[3])
        else:
            lines.append({"yc": yc, "y0": w[1], "y1": w[3], "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: w[0])
        line["text"] = norm(" ".join(w[4] for w in line["words"]))
    return lines


# ---------------------------------------------------------------- images
def trim_white(img, pad=8, threshold=200):
    """Crop to the ink, ignoring stray specks (rows/columns with < 2 dark pixels)."""
    arr = np.asarray(img)
    ink = arr < threshold
    rows = np.where(ink.sum(axis=1) >= 2)[0]
    cols = np.where(ink.sum(axis=0) >= 2)[0]
    if len(rows) == 0:
        return img
    top, bottom = max(rows[0] - pad, 0), min(rows[-1] + pad + 1, arr.shape[0])
    left, right = max(cols[0] - pad, 0), min(cols[-1] + pad + 1, arr.shape[1])
    return img.crop((left, top, right, bottom))


def render_question(doc, segments, dpi):
    """Render each page's part of a question, then stack the parts vertically."""
    by_page = {}
    for pno, rect in segments:
        if pno in by_page:
            by_page[pno] |= rect
        else:
            by_page[pno] = pymupdf.Rect(rect)
    parts = []
    for pno in sorted(by_page):
        # Shrink the clip slightly so the ruled separator lines that touch the
        # image edges are not rendered into the question.
        clip = by_page[pno] + (1.5, 1.5, -1.5, -1.5)
        pix = doc[pno].get_pixmap(dpi=dpi, clip=clip,
                                  colorspace=pymupdf.csGRAY, alpha=False)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        parts.append(trim_white(img))
    if len(parts) == 1:
        return parts[0]
    gap = int(dpi * 0.1)
    width = max(p.width for p in parts)
    height = sum(p.height for p in parts) + gap * (len(parts) - 1)
    out = Image.new("L", (width, height), 255)
    y = 0
    for p in parts:
        out.paste(p, (0, y))
        y += p.height + gap
    return out


# ---------------------------------------------------------------- duplicates
def dhash(img):
    small = np.asarray(img.resize((9, 8), Image.LANCZOS), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def dilate(a):
    p = np.pad(a, 1)
    out = np.zeros_like(a)
    h, w = a.shape
    for dy in range(3):
        for dx in range(3):
            out |= p[dy:dy + h, dx:dx + w]
    return out


def visually_identical(path_a, path_b, max_total, max_block, shift=3, block=32):
    """True if two question images match apart from scanning noise.

    Tries small offsets to allow for slightly different cropping. A pixel only
    counts as different if there is no matching ink within 1px. Two limits
    apply: the total number of differing pixels, and the most found in any
    32x32 patch (about one character). A changed digit is concentrated in one
    patch, so questions that differ only in their numbers are kept apart.
    """
    a = np.asarray(Image.open(path_a)) < 128
    b = np.asarray(Image.open(path_b)) < 128
    if abs(a.shape[0] - b.shape[0]) > shift or abs(a.shape[1] - b.shape[1]) > shift:
        return False
    h = min(a.shape[0], b.shape[0]) - shift
    w = min(a.shape[1], b.shape[1]) - shift
    hp, wp = -(-h // block) * block, -(-w // block) * block
    for dy in range(-shift, shift + 1):
        for dx in range(-shift, shift + 1):
            aa = a[max(dy, 0):max(dy, 0) + h, max(dx, 0):max(dx, 0) + w]
            bb = b[max(-dy, 0):max(-dy, 0) + h, max(-dx, 0):max(-dx, 0) + w]
            diff = (aa & ~dilate(bb)) | (bb & ~dilate(aa))
            total = int(diff.sum())
            if total > max_total:
                continue
            padded = np.zeros((hp, wp), dtype=bool)
            padded[:h, :w] = diff
            worst = padded.reshape(hp // block, block, wp // block, block).sum(axis=(1, 3)).max()
            if worst <= max_block:
                return True
    return False


def find_duplicate_groups(records, cfg):
    max_dist = cfg["dedupe"]["hash_distance"]
    max_total = cfg["dedupe"]["max_diff_pixels"]
    max_block = cfg["dedupe"]["max_diff_per_patch"]
    parent = list(range(len(records)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(records)), key=lambda i: records[i]["_px"][1])
    for oi, i in enumerate(order):
        ri = records[i]
        for j in order[oi + 1:]:
            rj = records[j]
            if rj["_px"][1] - ri["_px"][1] > 3:
                break  # sorted by height: nothing further can match
            if abs(rj["_px"][0] - ri["_px"][0]) > 3:
                continue
            if (ri["_hash"] ^ rj["_hash"]).bit_count() > max_dist:
                continue
            if find(i) == find(j):
                continue
            if visually_identical(ri["_file"], rj["_file"], max_total, max_block):
                parent[find(j)] = find(i)
    groups = defaultdict(list)
    for i in range(len(records)):
        groups[find(i)].append(i)
    return [sorted(g, key=lambda i: records[i]["sn"]) for g in groups.values()]


# ---------------------------------------------------------------- main pass
def scan_pdf(doc, cfg, review):
    hz = cfg["pdf"]["header_zone_pt"]
    known_sections = {s["code"] for s in cfg["sections"]}
    questions, answers = [], []
    current, current_section, in_answers = None, None, False

    for pno in range(len(doc)):
        page = doc[pno]
        printed = None
        items = []
        page_answers = []
        for line in page_lines(page):
            if line["y1"] <= hz:
                if printed is None:
                    nums = [w[4] for w in line["words"] if w[4].isdigit()]
                    if nums:
                        printed = int(nums[0])
                continue
            for m in ANSWER_RE.finditer(line["text"]):
                page_answers.append({"sn": int(m[1]), "paper": m[2],
                                     "q": int(m[3]), "answer": m[4]})
            m = ANCHOR_RE.match(line["text"])
            if m:
                items.append(("anchor", line["y0"], m))
                continue
            m = SECTION_RE.match(line["text"])
            if m and f"{m[1]}.{m[2]}" in known_sections:
                items.append(("section", line["y0"], f"{m[1]}.{m[2]}"))

        if len(page_answers) >= 3:
            in_answers = True
            current = None
        answers.extend(page_answers)
        if in_answers:
            continue

        for info in page.get_image_info(xrefs=True):
            rect = pymupdf.Rect(info["bbox"]) & page.rect
            if rect.is_empty or rect.width < 20 or rect.height < 5 or rect.y1 <= hz:
                continue
            items.append(("image", rect.y0, rect))

        items.sort(key=lambda t: (t[1], t[0] == "image"))
        for kind, _, val in items:
            if kind == "section":
                current_section = val
            elif kind == "anchor":
                current = {"sn": int(val[1]), "paper": val[2], "q": int(val[3]),
                           "segments": [], "section": current_section,
                           "printed_page": printed, "pdf_page": pno + 1}
                questions.append(current)
            elif current is None:
                review["images_without_a_question"].append(pno + 1)
            else:
                current["segments"].append((pno, val))
    return questions, answers


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", help="override the PDF path in config.yaml")
    args = ap.parse_args()

    cfg = load_config()
    pdf_path = args.pdf or (ROOT / cfg["pdf"]["question_bank"])
    print(f"Opening {pdf_path}")
    doc = pymupdf.open(pdf_path)
    review = defaultdict(list)

    raw, answers = scan_pdf(doc, cfg, review)
    print(f"Found {len(raw)} question references and {len(answers)} answer-key rows")
    if not raw:
        sys.exit("No questions found - check the PDF path.")

    # ---- answers
    by_sn = {a["sn"]: a for a in answers}
    by_ref = {(a["paper"], a["q"]): a for a in answers}

    # ---- sections
    ranges = section_ranges(cfg)

    def toc_sections(page):
        if page is None:
            return []
        return [s["code"] for s in ranges if s["start"] <= page <= s["end"]]

    # ---- render
    IMAGES.mkdir(parents=True, exist_ok=True)
    for old in IMAGES.glob("*.png"):
        old.unlink()
    dpi = cfg["pdf"]["render_dpi"]
    records = []
    for i, q in enumerate(raw, 1):
        ref = f'{q["sn"]}. {q["paper"]} Q{q["q"]}'
        if not q["segments"]:
            review["no_image_found"].append(ref)
            continue
        if len(q["segments"]) > 1:
            review["joined_from_several_images"].append(ref)

        expected = toc_sections(q["printed_page"])
        section = q["section"] or (expected[0] if expected else None)
        if section is None:
            review["no_section"].append(ref)
        elif expected and section not in expected:
            review["section_disagrees_with_contents_page"].append(
                f"{ref}: heading says {section}, contents page says {'/'.join(expected)}")

        ans = by_sn.get(q["sn"])
        if ans and (ans["paper"], ans["q"]) != (q["paper"], q["q"]):
            review["answer_key_number_mismatch"].append(
                f'{ref}: key row {ans["sn"]} is {ans["paper"]} Q{ans["q"]}')
            ans = None
        ans = ans or by_ref.get((q["paper"], q["q"]))
        if not ans:
            review["missing_answer"].append(ref)

        img = render_question(doc, q["segments"], dpi)
        path = IMAGES / f'{q["sn"]}.png'
        img.save(path, optimize=True)
        meta = parse_paper(q["paper"])
        records.append({
            "sn": q["sn"], "section": section,
            "ref": {"sn": q["sn"], "q": q["q"], **meta,
                    "answer": ans["answer"] if ans else None},
            "_file": path, "_px": img.size, "_hash": dhash(img),
        })
        if i % 100 == 0:
            print(f"  rendered {i}/{len(raw)}")

    # ---- duplicates
    groups = find_duplicate_groups(records, cfg)
    questions = []
    for g in sorted(groups, key=lambda g: records[g[0]]["sn"]):
        keep = records[g[0]]
        refs = [records[i]["ref"] for i in g]
        for i in g[1:]:
            records[i]["_file"].unlink()
        answers_seen = {r["answer"] for r in refs if r["answer"]}
        if len(answers_seen) > 1:
            review["duplicates_with_different_answers"].append(
                ", ".join(f'{r["paper"]} Q{r["q"]}={r["answer"]}' for r in refs))
        sections_seen = {records[i]["section"] for i in g}
        if len(sections_seen) > 1:
            review["duplicates_in_different_sections"].append(
                f'{keep["sn"]}: {", ".join(sorted(s or "?" for s in sections_seen))}')
        w, h = keep["_px"]
        section = keep["section"]
        questions.append({
            "id": f'q{keep["sn"]}',
            "image": f'images/{keep["sn"]}.png',
            "w_mm": round(w / dpi * 25.4, 1),
            "h_mm": round(h / dpi * 25.4, 1),
            "chapter": int(section.split(".")[0]) if section else None,
            "section": section,
            "levels": sorted({r["level"] for r in refs}),
            "answer": sorted(answers_seen)[0] if len(answers_seen) == 1 else None,
            "refs": sorted(refs, key=lambda r: (-r["year"], r["paper"], r["q"])),
        })

    merged = len(records) - len(questions)
    save_json(QUESTIONS_FILE, {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "chapters": cfg["chapters"],
        "sections": [{"code": s["code"], "title": s["title"], "chapter": s["chapter"]}
                     for s in ranges],
        "questions": questions,
    }, compact=True)

    summary = {k: len(v) for k, v in review.items()}
    save_json(REVIEW_FILE, {"summary": summary, "duplicates_merged": merged,
                            **{k: v for k, v in review.items()}})

    print(f"\nSaved {len(questions)} unique questions ({merged} duplicates merged).")
    for k, n in summary.items():
        print(f"  check: {k.replace('_', ' ')}: {n}")
    print(f"Details in {REVIEW_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
