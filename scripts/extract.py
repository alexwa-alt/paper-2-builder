"""Stage 1: split a question bank PDF into individual questions.

Works for any subject and bank in config.yaml (Physics, Biology, Chemistry;
Paper 2 multiple choice and Paper 6 alternative to practical).

No AI involved. For every question this script:
  * finds its reference line, e.g. "22. 0625_s20_qp_23 Q: 1"
  * renders the question image(s) that follow it
  * works out its chapter and section from the page headings
  * joins its answer (Paper 2) or mark scheme (Paper 6) from the back
  * Paper 2 only: merges visually identical duplicates

Two kinds of bank are supported (see config.yaml):
  mcq         Paper 2 - one image per question, answers in a table of letters
  structured  Paper 6 - questions span pages, mark schemes are images

Some PDFs have a real text layer; others draw every heading and reference as
little pictures of letters. For those (ocr: true) the reference lines are
read with Tesseract OCR.

Outputs (inside site/ so the website can use them directly):
  <image_dir>/...               question images
  <data_dir>/questions.json     the question list
  <data_dir>/review.json        anything that looked wrong and is worth a glance

Usage:
  python scripts/extract.py --bank physics-p2
  python scripts/extract.py --bank biology-p6 --pdf sample.pdf    # try a few pages first
"""
import argparse
import datetime as dt
import re
import sys
from collections import defaultdict

import numpy as np
import pymupdf
from PIL import Image

from common import (ROOT, bank_paths, get_bank, load_config, norm, parse_paper,
                    save_json, section_ranges, write_manifest)

SECTION_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+([A-Za-z].*)$")
ANSWER_RE = re.compile(r"(\d+)\s+(\d{4}_[a-z]\d{2}_qp_\d{2})\s+(\d+)\s+([ABCD])\b")
# Running headers, e.g. "1.1. LENGTH AND TIME 7" and "6 CHAPTER 1. GENERAL PHYSICS"
HEADER_SECTION_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.?\s+([A-Za-z][^0-9]*?)\s*\d*$")
HEADER_CHAPTER_RE = re.compile(r"CHAPTER\s+(\d{1,2})\.?\s+([A-Za-z][^0-9]*?)\s*\d*$", re.I)

# Reference lines, tolerant of OCR slips (l for 1, O for 0, missing underscores).
D = r"[0-9lIi|oO]"
REF_RE = re.compile(
    rf"^({D}{{1,4}})[.,:;]?({D}{{4}})_*([a-zA-Z])({D}{{2,3}})_*([a-zA-Z0-9]{{2}})_*({D}{{2,3}})"
    rf"[Qq][:;.,]?({D}{{1,2}})")
NOT_APPLICABLE_RE = re.compile(rf"^({D}{{1,4}})[.,:;]?Not(?:applicable)?", re.I)
DIGIT_FIX = str.maketrans({"l": "1", "I": "1", "i": "1", "|": "1", "o": "0", "O": "0"})


def digits(s):
    return s.translate(DIGIT_FIX)


def sentence_case(title):
    t = re.sub(r"\s+", " ", title).strip(" .,")
    return t[:1].upper() + t[1:].lower() if t.isupper() else t


def parse_ref(text, code):
    """'22. 0625_s20_qp_23 Q: 1' -> {sn, paper, q, kind}; kind is qp or ms."""
    s = text.replace(" ", "").replace("\u2014", "_").replace("-", "_")
    m = REF_RE.match(s)
    if not m:
        return None
    sn, prefix, session, yy, kind, comp, q = m.groups()
    sn, prefix, yy, comp, q = map(digits, (sn, prefix, yy, comp, q))
    yy, comp = yy[-2:], comp[-2:]   # OCR sometimes doubles a character ("sl17")
    if prefix != code or not (sn + yy + comp + q).isdigit():
        return None
    kind = "ms" if kind.lower().startswith("m") else "qp"
    return {"sn": int(sn), "paper": f"{code}_{session.lower()}{yy}_qp_{comp}",
            "q": int(q), "kind": kind}


def parse_not_applicable(text):
    m = NOT_APPLICABLE_RE.match(text.replace(" ", ""))
    if m and digits(m[1]).isdigit():
        return int(digits(m[1]))
    return None


def printed_page_number(header_text):
    t = header_text.strip()
    m = re.search(r"(?:^|\s)(\d+)$", t) or re.match(r"^(\d+)\s", t)
    return int(m[1]) if m else None


# ---------------------------------------------------------------- text lines
def text_layer_lines(page, tol=3.0):
    """Lines from a real text layer (works across side-by-side tables too)."""
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
        line["x0"] = line["words"][0][0]
        line["x1"] = max(w[2] for w in line["words"])
    return lines


def is_glyph(rect):
    """A small picture of a single character, as used for headings in some PDFs."""
    return rect.height < 30 and rect.width < 30


def ocr_lines(page):
    """Read lines drawn as pictures of letters, using Tesseract."""
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
    except Exception:
        sys.exit("This PDF needs OCR but Tesseract isn't installed. "
                 "On GitHub the extraction workflow installs it; on your own "
                 "computer see https://github.com/tesseract-ocr/tesseract.")
    glyphs = [r for r in (pymupdf.Rect(i["bbox"]) for i in page.get_image_info())
              if is_glyph(r) and 35 < r.x0 < 560]   # skip the sideways watermark
    glyphs.sort(key=lambda r: r.y0)
    groups = []
    for r in glyphs:
        if groups and r.y0 <= groups[-1].y1 + 2:
            groups[-1] |= r
        else:
            groups.append(pymupdf.Rect(r))
    out = []
    for g in groups:
        pix = page.get_pixmap(dpi=300, clip=g + (-3, -3, 3, 3),
                              colorspace=pymupdf.csGRAY, alpha=False)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        text = pytesseract.image_to_string(img, config="--psm 7").strip()
        out.append({"y0": g.y0, "y1": g.y1, "x0": g.x0, "x1": g.x1, "text": text, "ocr": True})
    return out


def page_lines(page, bank):
    """Use the page's real text if it has any; otherwise read glyph pictures."""
    lines = text_layer_lines(page)
    if not lines and any(is_glyph(pymupdf.Rect(i["bbox"])) for i in page.get_image_info()):
        lines = ocr_lines(page)
    return lines


def is_rule(rect):
    """A long thin horizontal line, such as the separator after a question."""
    return rect.height < 2.5 and rect.width > 250


def content_boxes(page, bank, hz, skip):
    """Everything drawn in the body of the page (text, drawings, pictures),
    minus headers, the publisher's logo and watermark, and the reference and
    heading lines themselves. Works whether the content is images or text."""
    logo_line = bank.get("footer_logo_pt")
    out = []
    for kind, bbox in page.get_bboxlog():
        if kind.startswith("clip") or kind == "ignore-text":
            continue
        rect = pymupdf.Rect(bbox) & page.rect
        if rect.is_empty or rect.y1 <= hz or rect.x1 < 35 or rect.x0 > 560:
            continue
        if "image" in kind and is_glyph(rect):
            continue
        if logo_line and rect.y0 >= logo_line and rect.width < 200 and rect.height > 20:
            continue
        if any(rect.intersects(r) for r in skip):
            continue
        out.append(rect)
    return out


def content_rects(page, bank, hz):
    """The question (or mark scheme) pictures on a page."""
    out = []
    logo_line = bank.get("footer_logo_pt")
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        if rect.is_empty or rect.y1 <= hz or is_glyph(rect):
            continue
        if rect.width < 20 or rect.height < 5:
            continue
        if logo_line and rect.y0 >= logo_line and rect.width < 200:
            continue
        out.append(rect)
    return out


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


def render_question(doc, segments, dpi, margin=-1.5):
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
        clip = (by_page[pno] + (-margin, -margin, margin, margin)) & doc[pno].rect
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
def scan_pdf(doc, cfg, bank, review, stats):
    """Walk the PDF once, collecting question records, mark-scheme records and
    answer-key rows, in the order they appear."""
    hz = cfg["header_zone_pt"]
    code = bank["code"]
    # With a contents page in the config only its sections are accepted;
    # without one, sections and chapters are discovered from the headings.
    known_sections = {s["code"] for s in bank["sections"]} if bank.get("sections") else None
    found_sections, found_chapters = stats["sections"], stats["chapters"]
    questions, schemes, answers = [], [], []
    not_applicable = set()
    current, current_section, in_answers = None, None, False

    for pno in range(len(doc)):
        page = doc[pno]
        lines = page_lines(page, bank)
        stats["pages"] += 1
        stats["text_lines"] += sum(1 for l in lines if l["y1"] > hz)
        if len(stats["sample"]) < 12:
            stats["sample"].extend(f'p{pno + 1}: {l["text"][:70]!r}' for l in lines[:4])
        printed = None
        items, page_answers, skip = [], [], []
        for line in lines:
            text = line["text"]
            if line.get("x1", 100) < 35 or line.get("x0", 100) > 560:
                continue   # the sideways watermark in the margin
            if line["y1"] <= hz:
                if printed is None:
                    printed = printed_page_number(text)
                if known_sections is None and not in_answers:
                    m = HEADER_SECTION_RE.match(text.strip())
                    if m:
                        sec = f"{int(m[1])}.{int(m[2])}"
                        found_sections.setdefault(sec, sentence_case(m[3]))
                        items.append(("header_section", -1, sec))
                    m = HEADER_CHAPTER_RE.search(text)
                    if m:
                        found_chapters.setdefault(int(m[1]), sentence_case(m[2]))
                continue
            if bank["kind"] == "mcq":
                for m in ANSWER_RE.finditer(text):
                    page_answers.append({"sn": int(m[1]), "paper": m[2],
                                         "q": int(m[3]), "answer": m[4]})
            line_rect = pymupdf.Rect(line.get("x0", 0), line["y0"], line.get("x1", 600), line["y1"])
            ref = parse_ref(text, code)
            if ref:
                items.append(("ref", line["y0"], ref))
                skip.append(line_rect)
                continue
            sn = parse_not_applicable(text)
            if sn is not None:
                items.append(("na", line["y0"], sn))
                skip.append(line_rect)
                continue
            m = SECTION_RE.match(text)
            sec = f"{int(m[1])}.{int(m[2])}" if m else None
            heading_like = line.get("ocr") or line["y1"] - line["y0"] >= 12
            if sec and (sec in known_sections if known_sections is not None else heading_like):
                if known_sections is None:
                    found_sections.setdefault(sec, sentence_case(m[3]))
                items.append(("section", line["y0"], sec))
                skip.append(line_rect)
            elif (line["y1"] - line["y0"] > 14 and not in_answers
                  and line.get("x1", 600) - line.get("x0", 0) > 3 * (line["y1"] - line["y0"])):
                # large type (chapter titles etc.) ends the previous question
                items.append(("heading", line["y0"], None))
                skip.append(line_rect)
            elif code in text.replace(" ", "") and (line.get("ocr") or len(text) < 40):
                review["unreadable_reference_lines"].append(f"PDF page {pno + 1}: {text!r}")

        if len(page_answers) >= 3:
            in_answers, current = True, None
        answers.extend(page_answers)
        if in_answers and bank["kind"] == "mcq":
            continue

        rects = (content_rects(page, bank, hz) if bank["kind"] == "mcq"
                 else content_boxes(page, bank, hz, skip))
        for rect in rects:
            items.append(("image", rect.y0, rect))

        items.sort(key=lambda t: (t[1], t[0] == "image"))
        for kind, _, val in items:
            if kind == "header_section":
                current_section = val   # applies to questions starting on this page
            elif kind == "heading":
                current = None
            elif kind == "section" and not in_answers:
                current_section = val
                current = None
            elif kind == "na":
                not_applicable.add(val)
                current = None
            elif kind == "ref":
                rec = {"sn": val["sn"], "paper": val["paper"], "q": val["q"],
                       "segments": [], "section": current_section,
                       "printed_page": printed, "pdf_page": pno + 1}
                if val["kind"] == "ms":
                    in_answers = True
                    schemes.append(rec)
                elif in_answers:
                    review["question_reference_inside_answers"].append(
                        f'PDF page {pno + 1}: {val["paper"]} Q{val["q"]}')
                    rec = None
                else:
                    questions.append(rec)
                current = rec
            elif current is None:
                review["images_without_a_question"].append(pno + 1)
            else:
                current["segments"].append((pno, val))
        if (pno + 1) % 50 == 0:
            print(f"  scanned {pno + 1}/{len(doc)} pages")
    return questions, schemes, answers, not_applicable


def render_pages(doc, segments, dpi, prefix, stem, folder):
    """Paper 6: one image per page a question (or mark scheme) occupies."""
    by_page, rules = {}, {}
    for pno, rect in segments:
        if is_rule(rect):
            rules.setdefault(pno, []).append(rect)
        elif pno in by_page:
            by_page[pno] |= rect
        else:
            by_page[pno] = pymupdf.Rect(rect)
    for pno, rs in rules.items():   # keep table borders, drop trailing separators
        if pno in by_page:
            u = by_page[pno]
            for r in rs:
                if u.y0 - 1 <= r.y0 and r.y1 <= u.y1 + 1:
                    by_page[pno] |= r
    out = []
    for k, pno in enumerate(sorted(by_page), 1):
        # Paper 6 separators are already left out, so add a little margin
        # instead of trimming, to keep table borders at the edges.
        img = render_question(doc, [(pno, by_page[pno])], dpi, margin=2)
        name = f"{stem}{k}.png"
        img.save(folder / name, optimize=True)
        out.append({"image": f"{prefix}/{name}",
                    "w_mm": round(img.width / dpi * 25.4, 1),
                    "h_mm": round(img.height / dpi * 25.4, 1)})
    return out


def assign_section(q, ranges, review, ref):
    expected = [s["code"] for s in ranges
                if q["printed_page"] is not None and s["start"] <= q["printed_page"] <= s["end"]]
    section = q["section"] or (expected[0] if expected else None)
    if section is None:
        review["no_section"].append(ref)
    elif expected and section not in expected:
        review["section_disagrees_with_contents_page"].append(
            f"{ref}: heading says {section}, contents page says {'/'.join(expected)}")
    return section


def build_mcq(doc, cfg, bank, paths, raw, answers, review):
    by_sn = {a["sn"]: a for a in answers}
    by_ref = {(a["paper"], a["q"]): a for a in answers}
    ranges = section_ranges(bank)
    dpi = cfg["render_dpi"]
    records = []
    for i, q in enumerate(raw, 1):
        ref = f'{q["sn"]}. {q["paper"]} Q{q["q"]}'
        if not q["segments"]:
            review["no_image_found"].append(ref)
            continue
        if len(q["segments"]) > 1:
            review["joined_from_several_images"].append(ref)
        section = assign_section(q, ranges, review, ref)

        ans = by_sn.get(q["sn"])
        if ans and (ans["paper"], ans["q"]) != (q["paper"], q["q"]):
            review["answer_key_number_mismatch"].append(
                f'{ref}: key row {ans["sn"]} is {ans["paper"]} Q{ans["q"]}')
            ans = None
        ans = ans or by_ref.get((q["paper"], q["q"]))
        if not ans:
            review["missing_answer"].append(ref)

        img = render_question(doc, q["segments"], dpi)
        path = paths["images"] / f'{q["sn"]}.png'
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
            "id": f'{bank.get("id_prefix", "q")}{keep["sn"]}',
            "image": f'{paths["image_prefix"]}/{keep["sn"]}.png',
            "w_mm": round(w / dpi * 25.4, 1),
            "h_mm": round(h / dpi * 25.4, 1),
            "chapter": int(section.split(".")[0]) if section else None,
            "section": section,
            "levels": sorted({r["level"] for r in refs}),
            "answer": sorted(answers_seen)[0] if len(answers_seen) == 1 else None,
            "refs": sorted(refs, key=lambda r: (-r["year"], r["paper"], r["q"])),
        })
    return questions, len(records) - len(questions)


def build_structured(doc, cfg, bank, paths, raw, schemes, not_applicable, review):
    ranges = section_ranges(bank)
    dpi = cfg["render_dpi"]
    prefix, folder = paths["image_prefix"], paths["images"]
    ms_by_sn = {m["sn"]: m for m in schemes}
    ms_by_ref = {(m["paper"], m["q"]): m for m in schemes}
    questions = []
    for i, q in enumerate(raw, 1):
        ref = f'{q["sn"]}. {q["paper"]} Q{q["q"]}'
        if not q["segments"]:
            review["no_image_found"].append(ref)
            continue
        section = assign_section(q, ranges, review, ref)
        meta = parse_paper(q["paper"])
        if meta is None:
            review["unrecognised_paper_code"].append(ref)
            continue

        ms = ms_by_sn.get(q["sn"])
        if ms and (ms["paper"], ms["q"]) != (q["paper"], q["q"]):
            review["mark_scheme_number_mismatch"].append(
                f'{ref}: mark scheme {ms["sn"]} is for {ms["paper"]} Q{ms["q"]}')
            ms = None
        ms = ms or ms_by_ref.get((q["paper"], q["q"]))
        if ms and ms["segments"]:
            ms_pages, status = render_pages(doc, ms["segments"], dpi, prefix,
                                            f'{q["sn"]}_ms', folder), "ok"
        elif q["sn"] in not_applicable:
            ms_pages, status = [], "not_applicable"
        else:
            ms_pages, status = [], "missing"
            review["missing_mark_scheme"].append(ref)

        questions.append({
            "id": f'{bank.get("id_prefix", "p6-")}{q["sn"]}',
            "pages": render_pages(doc, q["segments"], dpi, prefix, f'{q["sn"]}_', folder),
            "ms": ms_pages,
            "ms_status": status,
            "chapter": int(section.split(".")[0]) if section else None,
            "section": section,
            "levels": [],
            "answer": None,
            "refs": [{"sn": q["sn"], "q": q["q"], **meta}],
        })
        if i % 25 == 0:
            print(f"  rendered {i}/{len(raw)}")
    return questions, 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bank", required=True,
                    help="which bank, e.g. physics-p2, biology-p6 (see config.yaml)")
    ap.add_argument("--pdf", help="override the PDF path in config.yaml")
    args = ap.parse_args()

    cfg = load_config()
    bank = get_bank(cfg, args.bank)
    paths = bank_paths(cfg, bank["id"])
    pdf_path = args.pdf or (ROOT / "input" / bank["release_asset"])
    print(f"{bank['subject_name']} {bank['label']} ({bank['long']}): opening {pdf_path}")
    doc = pymupdf.open(pdf_path)
    review = defaultdict(list)
    stats = {"pages": 0, "text_lines": 0, "sample": [], "sections": {}, "chapters": {}}

    raw, schemes, answers, not_applicable = scan_pdf(doc, cfg, bank, review, stats)
    print(f"Found {len(raw)} questions, {len(schemes)} mark schemes, "
          f"{len(answers)} answer-key rows")
    if raw and not bank.get("sections"):
        # No contents page: questions before the first heading on a page
        # inherit the section of the question before them (or after, at the start).
        last = None
        for q in raw:
            last = q["section"] = q["section"] or last
        first = next((q["section"] for q in raw if q["section"]), None)
        for q in raw:
            q["section"] = q["section"] or first
    if not raw:
        print("\nNo questions found. What the script could read:")
        print(f"  pages scanned: {stats['pages']}, text lines read: {stats['text_lines']}")
        for k, v in review.items():
            print(f"  {k.replace('_', ' ')}: {len(v)}, e.g. {v[:3]}")
        print("  first lines seen:")
        for line in stats["sample"][:12]:
            print(f"    {line}")
        page = doc[min(10, len(doc) - 1)]
        print(f"  page {page.number + 1}: {len(page.get_text())} text characters, "
              f"{len(page.get_image_info())} images")
        sys.exit("Please copy this log into the chat so the extractor can be adjusted.")

    paths["images"].mkdir(parents=True, exist_ok=True)
    for old in paths["images"].glob("*.png"):
        old.unlink()

    if bank["kind"] == "mcq":
        questions, merged = build_mcq(doc, cfg, bank, paths, raw, answers, review)
    else:
        questions, merged = build_structured(doc, cfg, bank, paths, raw, schemes,
                                             not_applicable, review)

    ranges = section_ranges(bank)
    if ranges:
        sections = [{"code": s["code"], "title": s["title"], "chapter": s["chapter"]} for s in ranges]
        chapters = bank["chapters"]
    else:   # discovered from the headings
        key = lambda c: tuple(int(x) for x in c.split("."))
        used = {q["section"] for q in questions if q["section"]}
        codes = sorted(set(stats["sections"]) | used, key=key)
        sections = [{"code": c, "title": stats["sections"].get(c, f"Section {c}"),
                     "chapter": int(c.split(".")[0])} for c in codes]
        nums = sorted({s["chapter"] for s in sections} | set(stats["chapters"]))
        chapters = [{"num": n, "title": stats["chapters"].get(n, f"Topic {n}")} for n in nums]
        if codes:
            print(f"Sections read from the page headings: {', '.join(codes)}")
    save_json(paths["questions"], {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "bank": bank["id"], "subject": bank["subject"], "label": bank["label"],
        "kind": bank["kind"], "chapters": chapters, "sections": sections,
        "questions": questions,
    }, compact=True)
    write_manifest(cfg)

    summary = {k: len(v) for k, v in review.items()}
    save_json(paths["review"], {"summary": summary, "duplicates_merged": merged,
                                **{k: v for k, v in review.items()}})

    print(f"\nSaved {len(questions)} questions"
          + (f" ({merged} duplicates merged)." if merged else "."))
    for k, n in summary.items():
        print(f"  check: {k.replace('_', ' ')}: {n}")
    print(f"Details in {paths['review'].relative_to(ROOT)}")


if __name__ == "__main__":
    main()
