/* Builds an exam-style A4 PDF from the chosen questions (runs in the browser).
   Paper 2 (mcq): questions flow down the page, answer key at the end.
   Paper 6 (structured): each question starts on a new page and keeps its
   original page breaks; mark schemes can be added at the end. */
window.PaperPDF = (() => {
  "use strict";

  const PW = 210, PH = 297;           // A4 in mm
  const ML = 18, MR = 18, MT = 18, MB = 22;
  const NUMW = 13;                    // column for the question number
  const GAP = 9;                      // space between Paper 2 questions
  const REF_H = 3.5;                  // extra space when printing references
  const MAX_W = PW - ML - MR - NUMW;
  const KEY_ROW = 6.5, KEY_TOP = 42;
  const MS_TOP = 32, MS_HEAD = 7, MS_GAP = 8;

  // ------------------------------------------------------------ helpers
  function pagesOf(q) { return q.pages || [{ image: q.image, w_mm: q.w_mm, h_mm: q.h_mm }]; }

  function totalMarks(questions, kind) {
    if (kind === "mcq") return questions.length;
    if (!questions.length || questions.some((q) => !q.tag?.marks)) return null;
    return questions.reduce((a, q) => a + q.tag.marks, 0);
  }

  function suggestMinutes(questions, kind) {
    const round5 = (m) => Math.max(5, Math.ceil(m / 5) * 5);
    if (kind === "mcq") return round5((questions.length * 45) / 40);   // 40 questions in 45 min
    const marks = totalMarks(questions, kind);
    return marks ? round5(marks * 1.5) : round5(questions.length * 15); // 40 marks in 60 min
  }

  function fit(p, maxW = MAX_W, maxH = PH - MT - MB) {
    let w = p.w_mm, h = p.h_mm;
    if (w > maxW) { h *= maxW / w; w = maxW; }
    if (h > maxH) { w *= maxH / h; h = maxH; }
    return { w, h };
  }

  // ------------------------------------------------------------ layout
  function layoutMCQ(questions, withRefs) {
    const pages = [];
    let page = [], y = MT;
    questions.forEach((q, i) => {
      const { w, h } = fit(pagesOf(q)[0]);
      const need = h + (withRefs ? REF_H : 0);
      if (page.length && y + need > PH - MB) {
        pages.push(page);
        page = [];
        y = MT;
      }
      page.push({ q, n: i + 1, first: true, img: pagesOf(q)[0],
                  x: ML + NUMW, y: y + (withRefs ? REF_H : 0), w, h });
      y += need + GAP;
    });
    if (page.length) pages.push(page);
    return pages;
  }

  function layoutStructured(questions, withRefs) {
    const pages = [];
    const top = MT + (withRefs ? REF_H : 0);
    questions.forEach((q, i) => {
      pagesOf(q).forEach((img, k) => {
        const { w, h } = fit(img, MAX_W, PH - top - MB);
        pages.push([{ q, n: i + 1, first: k === 0, img, x: ML + NUMW, y: top, w, h }]);
      });
    });
    return pages;
  }

  function layout(questions, kind, withRefs) {
    return kind === "mcq" ? layoutMCQ(questions, withRefs) : layoutStructured(questions, withRefs);
  }

  function keyLayout(n) {
    const rows = Math.floor((PH - MB - KEY_TOP) / KEY_ROW);
    return { rows, perPage: rows * 2, pages: Math.ceil(n / (rows * 2)) };
  }

  /* Mark schemes flow one after another; each image stays whole. */
  function msLayout(questions) {
    const pages = [];
    let page = [], y = MS_TOP;
    const newPage = () => { pages.push(page); page = []; y = MT; };
    questions.forEach((q, i) => {
      const imgs = q.ms || [];
      const firstH = imgs.length ? fit(imgs[0], PW - ML - MR).h : 6;
      if (page.length && y + MS_HEAD + firstH > PH - MB) newPage();
      page.push({ kind: "head", q, n: i + 1, y });
      y += MS_HEAD;
      if (!imgs.length) {
        page.push({ kind: "none", q, y });
        y += 6;
      }
      imgs.forEach((img) => {
        const { w, h } = fit(img, PW - ML - MR, PH - MT - MB);
        if (y + h > PH - MB) newPage();
        page.push({ kind: "img", img, x: ML, y, w, h });
        y += h + 3;
      });
      y += MS_GAP;
    });
    if (page.length) pages.push(page);
    return pages;
  }

  function estimatePages(questions, kind) {
    return layout(questions, kind, false).length;
  }

  // ------------------------------------------------------------ drawing
  function toDataURL(url) {
    return fetch(url).then((r) => {
      if (!r.ok) throw new Error(`could not load ${url}`);
      return r.blob();
    }).then((blob) => new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = () => reject(new Error(`could not read ${url}`));
      fr.readAsDataURL(blob);
    }));
  }

  function refOf(q) { return `${q.refs[0].paper} Q${q.refs[0].q}`; }

  function footer(doc, label, turnOver) {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.setTextColor(0);
    doc.text(label, PW / 2, PH - 10, { align: "center" });
    if (turnOver) {
      doc.setFont("helvetica", "bold");
      doc.text("[Turn over", PW - MR, PH - 10, { align: "right" });
    }
  }

  function drawCover(doc, opts, questions, totalPages) {
    const n = questions.length;
    const mcq = opts.kind === "mcq";
    const marks = totalMarks(questions, opts.kind);
    let y = 32;
    doc.setTextColor(0);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(11);
    doc.text("Cambridge IGCSE Physics 0625", ML, y);
    doc.text(mcq ? "Multiple choice" : "Alternative to practical", PW - MR, y, { align: "right" });
    y += 4;
    doc.setLineWidth(0.6);
    doc.line(ML, y, PW - MR, y);

    y += 16;
    doc.setFont("helvetica", "bold");
    doc.setFontSize(20);
    const titleLines = doc.splitTextToSize(opts.title, PW - ML - MR);
    doc.text(titleLines, ML, y);
    y += titleLines.length * 8 + 10;

    doc.setFontSize(10);
    doc.setLineWidth(0.3);
    const box = (label, lx, bx, by, bw) => {
      doc.setFont("helvetica", "bold");
      doc.text(label, lx, by + 6);
      doc.rect(bx, by, bw, 9);
    };
    box("Name", ML, ML + 16, y, PW - MR - ML - 16);
    box("Class", ML, ML + 16, y + 14, 60);
    box("Date", ML + 84, ML + 96, y + 14, PW - MR - ML - 96);
    y += 38;

    doc.setFont("helvetica", "bold");
    doc.setFontSize(12);
    doc.text(`Time allowed: ${opts.minutes} minutes`, ML, y);
    y += 14;

    const block = (heading, items) => {
      doc.setFont("helvetica", "bold");
      doc.setFontSize(11);
      doc.text(heading, ML, y);
      y += 6.5;
      doc.setFont("helvetica", "normal");
      doc.setFontSize(10.5);
      items.forEach((t) => {
        const lines = doc.splitTextToSize(t, PW - ML - MR - 6);
        doc.text("•", ML + 1, y);
        doc.text(lines, ML + 6, y);
        y += lines.length * 5 + 2;
      });
      y += 6;
    };
    const count = `There ${n === 1 ? "is one question" : `are ${n} questions`} on this paper. Answer all questions.`;
    if (mcq) {
      block("Instructions", [
        count,
        "For each question there are four possible answers, A, B, C and D. Choose the one you consider correct and write its letter clearly next to the question number.",
        "Use a black or dark blue pen. You may use a calculator.",
        "Take the weight of 1.0 kg to be 9.8 N (acceleration of free fall = 9.8 m/s²).",
      ]);
      block("Information", [
        `Each correct answer scores one mark. The total mark for this paper is ${n}.`,
        "Any rough working should be done on this paper.",
      ]);
    } else {
      block("Instructions", [
        count,
        "Write your answers in the spaces provided on the question paper.",
        "Use a black or dark blue pen. You may use an HB pencil for any diagrams or graphs.",
        "You may use a calculator. You should show all your working and use appropriate units.",
      ]);
      block("Information", [
        ...(marks ? [`The total mark for this paper is ${marks}.`] : []),
        "The number of marks for each question or part question is shown in brackets [ ].",
      ]);
    }

    if (mcq) {
      const cols = 10, cellW = (PW - ML - MR) / cols, cellH = 9;
      const rows = Math.ceil(n / cols);
      if (y + rows * cellH * 2 + 12 < PH - MB) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(11);
        doc.text("Your answers", ML, y);
        y += 4;
        doc.setFontSize(9);
        for (let i = 0; i < n; i++) {
          const cx = ML + (i % cols) * cellW, cy = y + Math.floor(i / cols) * cellH * 2;
          doc.setFillColor(235, 235, 235);
          doc.rect(cx, cy, cellW, cellH * 0.8, "FD");
          doc.setFont("helvetica", "bold");
          doc.text(String(i + 1), cx + cellW / 2, cy + cellH * 0.55, { align: "center" });
          doc.rect(cx, cy + cellH * 0.8, cellW, cellH * 1.2);
        }
      }
    } else if (marks) {
      // marking grid: one row per question
      doc.setFont("helvetica", "bold");
      doc.setFontSize(11);
      doc.text("For the marker", ML, y);
      y += 4;
      doc.setFontSize(9.5);
      const cw = 22, ch = 7;
      const cells = [["Question", "Marks"], ...questions.map((q, i) => [String(i + 1), `/ ${q.tag.marks}`]), ["Total", `/ ${marks}`]];
      if (y + ch * 2 < PH - MB) {
        cells.forEach(([a, b], i) => {
          const cx = ML + i * cw;
          if (cx + cw > PW - MR) return;
          doc.setFont("helvetica", i === 0 ? "bold" : "normal");
          doc.rect(cx, y, cw, ch);
          doc.rect(cx, y + ch, cw, ch * 1.4);
          doc.text(a, cx + cw / 2, y + 4.8, { align: "center" });
          doc.text(b, cx + cw - 1.5, y + ch * 2.2, { align: "right" });
        });
      }
    }

    doc.setFont("helvetica", "normal");
    doc.setFontSize(9);
    doc.text(`This document has ${totalPages} page${totalPages === 1 ? "" : "s"}.`, ML, PH - 14);
    footer(doc, "", true);
  }

  function drawKey(doc, opts, questions, startPage) {
    const { rows, perPage } = keyLayout(questions.length);
    const colW = (PW - ML - MR - 10) / 2;
    for (let p = 0; p * perPage < questions.length; p++) {
      doc.addPage();
      doc.setTextColor(0);
      doc.setFont("helvetica", "bold");
      doc.setFontSize(16);
      doc.text("Answer key", ML, 26);
      doc.setFont("helvetica", "normal");
      doc.setFontSize(10);
      doc.text(opts.title, ML, 33);
      for (let c = 0; c < 2; c++) {
        const x = ML + c * (colW + 10);
        const chunk = questions.slice(p * perPage + c * rows, p * perPage + (c + 1) * rows);
        if (!chunk.length) continue;
        doc.setFont("helvetica", "bold");
        doc.setFontSize(9.5);
        doc.text("Q", x, KEY_TOP - 2);
        doc.text("Answer", x + 12, KEY_TOP - 2);
        doc.text("Source", x + 30, KEY_TOP - 2);
        doc.setLineWidth(0.3);
        doc.line(x, KEY_TOP, x + colW, KEY_TOP);
        chunk.forEach((q, i) => {
          const n = p * perPage + c * rows + i + 1;
          const y = KEY_TOP + (i + 1) * KEY_ROW - 1.8;
          doc.setFont("helvetica", "bold");
          doc.text(String(n), x, y);
          doc.text(q.answer || "?", x + 16, y);
          doc.setFont("helvetica", "normal");
          doc.text(refOf(q), x + 30, y);
          doc.setDrawColor(220);
          doc.line(x, y + 1.8, x + colW, y + 1.8);
          doc.setDrawColor(0);
        });
      }
      footer(doc, String(startPage + p), false);
    }
  }

  function drawMarkSchemes(doc, opts, pages, images, startPage) {
    pages.forEach((page, pi) => {
      doc.addPage();
      doc.setTextColor(0);
      if (pi === 0) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(16);
        doc.text("Mark schemes", ML, 22);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text(opts.title, ML, 28);
      }
      page.forEach((it) => {
        if (it.kind === "head") {
          doc.setFont("helvetica", "bold");
          doc.setFontSize(11);
          doc.text(`Question ${it.n}`, ML, it.y + 4.5);
          doc.setFont("helvetica", "normal");
          doc.setFontSize(9);
          doc.setTextColor(110);
          doc.text(refOf(it.q), ML + 24, it.y + 4.5);
          doc.setTextColor(0);
        } else if (it.kind === "none") {
          doc.setFont("helvetica", "italic");
          doc.setFontSize(10);
          doc.text(it.q.ms_status === "not_applicable"
            ? "No mark scheme is published for this question."
            : "No mark scheme was found for this question.", ML, it.y + 4);
        } else {
          doc.addImage(images.get(it.img.image), "PNG", it.x, it.y, it.w, it.h, it.img.image, "FAST");
        }
      });
      footer(doc, String(startPage + pi), false);
    });
  }

  // ------------------------------------------------------------ build
  async function build(questions, opts) {
    if (!window.jspdf) throw new Error("the PDF library didn't load - check your connection");
    const { jsPDF } = window.jspdf;
    const mcq = opts.kind === "mcq";
    const pages = layout(questions, opts.kind, opts.refs);
    const msPages = !mcq && opts.key ? msLayout(questions) : [];
    const keyPages = mcq && opts.key ? keyLayout(questions.length).pages : 0;
    const total = (opts.cover ? 1 : 0) + pages.length + keyPages + msPages.length;

    const urls = new Set();
    questions.forEach((q) => {
      pagesOf(q).forEach((p) => urls.add(p.image));
      if (msPages.length) (q.ms || []).forEach((p) => urls.add(p.image));
    });
    const list = [...urls];
    const data = await Promise.all(list.map(toDataURL));
    const images = new Map(list.map((u, i) => [u, data[i]]));

    const doc = new jsPDF({ unit: "mm", format: "a4", compress: true });
    doc.setProperties({ title: opts.title, creator: "0625 question bank" });
    let first = true;
    if (opts.cover) {
      drawCover(doc, opts, questions, total);
      first = false;
    }
    let pageNo = opts.cover ? 2 : 1;
    pages.forEach((page, pi) => {
      if (!first) doc.addPage();
      first = false;
      page.forEach((it) => {
        doc.setTextColor(0);
        if (it.first) {
          doc.setFont("helvetica", "bold");
          doc.setFontSize(11);
          doc.text(String(it.n), ML, it.y + 4);
          if (opts.refs) {
            doc.setFont("helvetica", "normal");
            doc.setFontSize(7.5);
            doc.setTextColor(120);
            doc.text(refOf(it.q), it.x, it.y - 1.2);
            doc.setTextColor(0);
          }
        }
        doc.addImage(images.get(it.img.image), "PNG", it.x, it.y, it.w, it.h, it.img.image, "FAST");
      });
      footer(doc, String(pageNo), pi < pages.length - 1);
      pageNo++;
    });
    if (keyPages) drawKey(doc, opts, questions, pageNo);
    if (msPages.length) drawMarkSchemes(doc, opts, msPages, images, pageNo);

    const slug = opts.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "paper";
    doc.save(`${slug}.pdf`);
  }

  return { build, estimatePages, suggestMinutes, totalMarks };
})();
