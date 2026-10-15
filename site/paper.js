/* Builds an exam-style A4 PDF from the chosen questions (runs in the browser). */
window.PaperPDF = (() => {
  "use strict";

  const PW = 210, PH = 297;           // A4 in mm
  const ML = 18, MR = 18, MT = 18, MB = 22;
  const NUMW = 13;                    // column for the question number
  const GAP = 9;                      // space between questions
  const REF_H = 3.5;                  // extra space when printing references
  const MAX_W = PW - ML - MR - NUMW;
  const KEY_ROW = 6.5, KEY_TOP = 42;

  function suggestMinutes(n) {
    // Cambridge Paper 1/2: 40 questions in 45 minutes.
    return Math.max(5, Math.ceil((n * 45) / 40 / 5) * 5);
  }

  function fit(q) {
    let w = q.w_mm, h = q.h_mm;
    if (w > MAX_W) { h *= MAX_W / w; w = MAX_W; }
    const maxH = PH - MT - MB;
    if (h > maxH) { w *= maxH / h; h = maxH; }
    return { w, h };
  }

  function layout(questions, withRefs) {
    const pages = [];
    let page = [], y = MT;
    questions.forEach((q, i) => {
      const { w, h } = fit(q);
      const need = h + (withRefs ? REF_H : 0);
      if (page.length && y + need > PH - MB) {
        pages.push(page);
        page = [];
        y = MT;
      }
      page.push({ q, n: i + 1, x: ML + NUMW, y: y + (withRefs ? REF_H : 0), w, h });
      y += need + GAP;
    });
    if (page.length) pages.push(page);
    return pages;
  }

  function keyLayout(n) {
    const rows = Math.floor((PH - MB - KEY_TOP) / KEY_ROW);
    return { rows, perPage: rows * 2, pages: Math.ceil(n / (rows * 2)) };
  }

  function estimatePages(questions) {
    return layout(questions, false).length;
  }

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

  function drawCover(doc, opts, n, totalPages) {
    let y = 32;
    doc.setTextColor(0);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(11);
    doc.text("Cambridge IGCSE Physics 0625", ML, y);
    doc.text("Multiple choice", PW - MR, y, { align: "right" });
    y += 4;
    doc.setLineWidth(0.6);
    doc.line(ML, y, PW - MR, y);

    y += 16;
    doc.setFont("helvetica", "bold");
    doc.setFontSize(20);
    const titleLines = doc.splitTextToSize(opts.title, PW - ML - MR);
    doc.text(titleLines, ML, y);
    y += titleLines.length * 8 + 10;

    // name / class / date boxes
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
    block("Instructions", [
      `There ${n === 1 ? "is one question" : `are ${n} questions`} on this paper. Answer all questions.`,
      "For each question there are four possible answers, A, B, C and D. Choose the one you consider correct and write its letter clearly next to the question number.",
      "Use a black or dark blue pen. You may use a calculator.",
      "Take the weight of 1.0 kg to be 9.8 N (acceleration of free fall = 9.8 m/s²).",
    ]);
    block("Information", [
      `Each correct answer scores one mark. The total mark for this paper is ${n}.`,
      "Any rough working should be done on this paper.",
    ]);

    // answer grid for quick marking
    const cols = 10, cellW = (PW - ML - MR) / cols, cellH = 9;
    const rows = Math.ceil(n / cols);
    const gridH = rows * cellH * 2;
    if (y + gridH + 12 < PH - MB) {
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

  async function build(questions, opts) {
    if (!window.jspdf) throw new Error("the PDF library didn't load - check your connection");
    const { jsPDF } = window.jspdf;
    const pages = layout(questions, opts.refs);
    const keyPages = opts.key ? keyLayout(questions.length).pages : 0;
    const total = (opts.cover ? 1 : 0) + pages.length + keyPages;

    const images = await Promise.all(questions.map((q) => toDataURL(q.image)));
    const img = new Map(questions.map((q, i) => [q.id, images[i]]));

    const doc = new jsPDF({ unit: "mm", format: "a4", compress: true });
    doc.setProperties({ title: opts.title, creator: "0625 question bank" });
    let first = true;
    if (opts.cover) {
      drawCover(doc, opts, questions.length, total);
      first = false;
    }
    let pageNo = opts.cover ? 2 : 1;
    pages.forEach((page, pi) => {
      if (!first) doc.addPage();
      first = false;
      page.forEach((it) => {
        doc.setTextColor(0);
        doc.setFont("helvetica", "bold");
        doc.setFontSize(11);
        doc.text(String(it.n), ML, it.y + 4);
        if (opts.refs) {
          doc.setFont("helvetica", "normal");
          doc.setFontSize(7.5);
          doc.setTextColor(120);
          doc.text(refOf(it.q), it.x, it.y - 1.2);
        }
        doc.addImage(img.get(it.q.id), "PNG", it.x, it.y, it.w, it.h, it.q.id, "FAST");
      });
      footer(doc, String(pageNo), pi < pages.length - 1);
      pageNo++;
    });
    if (opts.key) drawKey(doc, opts, questions, pageNo);

    const slug = opts.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "paper";
    doc.save(`${slug}.pdf`);
  }

  return { build, estimatePages, suggestMinutes };
})();
