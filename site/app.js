/* 0625 question bank - browsing, filtering and paper building. */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // The banks the site knows about. Data folders match config.yaml.
  const BANKS = [
    { key: "p2", label: "Paper 2", long: "Multiple choice", data: "data", kind: "mcq",
      store: "qb0625.paper.v1" },
    { key: "p6", label: "Paper 6", long: "Alternative to practical", data: "data/p6", kind: "structured",
      store: "qb0625.paper.p6.v1" },
  ];

  const SESSION_LABEL = { m: "Feb/Mar", s: "May/Jun", w: "Oct/Nov" };
  const TYPE_LABEL = {
    calculation: "Calculation", graph_or_diagram: "Graph or diagram",
    practical: "Practical", conceptual: "Conceptual",
  };
  const SKILL_LABEL = {
    readings: "Readings", tables: "Tables", graphs: "Graphs", calculations: "Calculations",
    conclusions: "Conclusions", planning: "Planning", precautions: "Precautions and accuracy",
  };
  const DIFF_LABEL = { 1: "Easier", 2: "Standard", 3: "Harder" };
  const PAGE_SIZE = 40;

  const state = {
    bank: null, available: [], cache: {},
    questions: [], byId: new Map(), sections: [], chapters: [],
    sectionTitle: {}, objectives: new Map(),
    filtered: [], limit: PAGE_SIZE,
    paper: [], expanded: new Set(), msOpen: new Set(),
    f: {}, showAnswers: false, sort: "book",
  };
  const isMCQ = () => state.bank.kind === "mcq";

  function emptyFilters() {
    return { text: "", sections: new Set(), levels: new Set(), sessions: new Set(),
             types: new Set(), diffs: new Set(), yearFrom: 0, yearTo: 9999,
             subpoint: "", hidePaper: false };
  }

  // ------------------------------------------------------------ storage
  const store = {
    load() {
      try { return JSON.parse(localStorage.getItem(state.bank.store)) || {}; } catch { return {}; }
    },
    save() {
      try {
        localStorage.setItem(state.bank.store, JSON.stringify({
          ids: state.paper, title: $("#p-title").value, cover: $("#p-cover").checked,
          key: $("#p-key").checked, refs: $("#p-refs").checked, time: $("#p-time").value,
        }));
        localStorage.setItem("qb0625.bank", state.bank.key);
      } catch { /* storage unavailable: the paper just won't persist */ }
    },
  };

  async function fetchJSON(url, fallback) {
    try {
      const r = await fetch(url, { cache: "no-cache" });
      return r.ok ? await r.json() : fallback;
    } catch { return fallback; }
  }

  // ------------------------------------------------------------ data
  async function loadBank(bank) {
    if (state.cache[bank.key]) return state.cache[bank.key];
    const [qdata, tags] = await Promise.all([
      fetchJSON(`${bank.data}/questions.json`, null),
      fetchJSON(`${bank.data}/tags.json`, {}),
    ]);
    if (!qdata || !qdata.questions?.length) return null;

    const repeatsOf = new Map();
    for (const q of qdata.questions) {
      q.pages = q.pages || [{ image: q.image, w_mm: q.w_mm, h_mm: q.h_mm }];
      q.ms = q.ms || [];
      const t = tags[q.id];
      q.tag = t && t.status === "done" ? t : null;
      q.repeatOf = q.tag?.repeat_of || null;
      if (q.repeatOf) {
        if (!repeatsOf.has(q.repeatOf)) repeatsOf.set(q.repeatOf, []);
        repeatsOf.get(q.repeatOf).push(q);
      }
      q.effSection = q.tag && q.tag.section_ok === false && q.tag.suggested_section
        ? q.tag.suggested_section : q.section;
      q.kinds = q.tag ? (q.tag.skills || [q.tag.type]) : [];
    }
    for (const q of qdata.questions) {
      const reps = repeatsOf.get(q.id) || [];
      q.allRefs = [...q.refs, ...reps.flatMap((r) => r.refs)];
      q.allLevels = [...new Set([...q.levels, ...reps.flatMap((r) => r.levels)])];
      q.years = [...new Set(q.allRefs.map((r) => r.year))];
      q.sessions = [...new Set(q.allRefs.map((r) => r.session))];
      q.search = ((q.tag?.text || "") + " " + q.allRefs.map((r) => r.paper).join(" ")).toLowerCase();
    }
    const data = {
      all: qdata.questions,
      questions: qdata.questions.filter((q) => !q.repeatOf),
      sections: qdata.sections, chapters: qdata.chapters,
    };
    state.cache[bank.key] = data;
    return data;
  }

  // ------------------------------------------------------------ bank switching
  function renderBankTabs() {
    $("#banks").innerHTML = state.available.length < 2 ? "" : state.available.map((b) =>
      `<button data-bank="${b.key}" aria-pressed="${b === state.bank}" title="${esc(b.long)}">${esc(b.label)}<span class="only-wide">: ${esc(b.long)}</span></button>`).join("");
  }

  async function switchBank(key) {
    const bank = state.available.find((b) => b.key === key) || state.available[0];
    const data = await loadBank(bank);
    state.bank = bank;
    state.questions = data.questions;
    state.byId = new Map(data.all.map((q) => [q.id, q]));
    state.sections = data.sections;
    state.chapters = data.chapters;
    state.sectionTitle = {};
    state.sections.forEach((s) => (state.sectionTitle[s.code] = s.title));
    state.expanded.clear();
    state.msOpen.clear();
    state.f = emptyFilters();
    $("#f-text").value = "";
    $("#f-hide-paper").checked = false;

    const saved = store.load();
    state.paper = (saved.ids || []).filter((id) => state.byId.has(id));
    $("#p-title").value = saved.title || (isMCQ() ? "Physics practice paper" : "Physics practical paper");
    $("#p-cover").checked = !!saved.cover;
    $("#p-key").checked = saved.key !== undefined ? saved.key : true;
    $("#p-refs").checked = !!saved.refs;
    $("#p-time").value = saved.time || "";

    $("#f-levels-wrap").hidden = !isMCQ();
    $("#f-types-legend").textContent = isMCQ() ? "Question type" : "Practical skills";
    $("#show-answers-label").textContent = isMCQ() ? "Show answers" : "Show mark schemes";
    $("#p-key-label").textContent = isMCQ() ? "Answer key on the last page" : "Mark schemes at the end";
    document.title = `0625 ${bank.label} question bank`;

    const total = state.questions.length;
    const tagged = state.questions.filter((q) => q.tag).length;
    $("#progress").textContent = (state.available.length < 2 ? `${bank.label}: ${bank.long}. ` : "") + (tagged >= total
      ? `${total.toLocaleString()} questions, all tagged`
      : `${total.toLocaleString()} questions, ${tagged.toLocaleString()} tagged so far`);

    renderBankTabs();
    buildFilters();
    renderPaper();
    update();
    try { localStorage.setItem("qb0625.bank", bank.key); } catch { /* ignore */ }
    if (location.hash !== `#${bank.key}`) history.replaceState(null, "", `#${bank.key}`);
  }

  // ------------------------------------------------------------ filters UI
  function chip(group, value, label) {
    return `<label class="chip"><input type="checkbox" data-group="${group}" value="${esc(value)}"><span>${esc(label)}</span></label>`;
  }

  function buildFilters() {
    const counts = {};
    state.questions.forEach((q) => (counts[q.effSection] = (counts[q.effSection] || 0) + 1));
    $("#f-sections").innerHTML = state.chapters.map((ch) => {
      const secs = state.sections.filter((s) => s.chapter === ch.num && counts[s.code]);
      if (!secs.length) return "";
      const n = secs.reduce((a, s) => a + (counts[s.code] || 0), 0);
      return `<details>
        <summary><input type="checkbox" data-chapter="${ch.num}" aria-label="All of ${esc(ch.title)}">
          <span>${ch.num} ${esc(ch.title)}</span><span class="n">${n}</span></summary>
        ${secs.map((s) => `<label class="sec"><input type="checkbox" data-section="${s.code}">
          <span>${s.code} ${esc(s.title)}</span><span class="n">${counts[s.code] || 0}</span></label>`).join("")}
      </details>`;
    }).join("");

    $("#f-levels").innerHTML = chip("levels", "core", "Core") + chip("levels", "extended", "Extended");
    const sessions = [...new Set(state.questions.flatMap((q) => q.sessions))].sort();
    $("#f-sessions").innerHTML = sessions.map((k) => chip("sessions", k, SESSION_LABEL[k] || k.toUpperCase())).join("");
    const kinds = isMCQ() ? TYPE_LABEL : SKILL_LABEL;
    $("#f-types").innerHTML = Object.entries(kinds).map(([k, v]) => chip("types", k, v)).join("");
    $("#f-diffs").innerHTML = Object.entries(DIFF_LABEL).map(([k, v]) => chip("diffs", k, v)).join("");

    const years = [...new Set(state.questions.flatMap((q) => q.years))].sort();
    const opts = years.map((y) => `<option value="${y}">${y}</option>`).join("");
    $("#f-year-from").innerHTML = opts;
    $("#f-year-to").innerHTML = opts;
    state.yearMin = years[0];
    state.yearMax = years[years.length - 1];
    $("#f-year-from").value = state.yearMin;
    $("#f-year-to").value = state.yearMax;

    // learning objectives: only those actually used by this bank's tags
    const used = new Set(state.questions.flatMap((q) => q.tag?.subpoints || []));
    state.bankObjectives = [...state.objectives.values()].filter((o) => used.has(o.code));
    $("#f-subpoint-wrap").hidden = !state.bankObjectives.length;
    fillSubpoints();
  }

  function fillSubpoints() {
    const sel = $("#f-subpoint");
    const keep = sel.value;
    const chapters = new Set([...state.f.sections].map((s) => s.split(".")[0]));
    const objs = (state.bankObjectives || []).filter((o) =>
      !chapters.size || chapters.has(o.section.split(".")[0]));
    sel.innerHTML = `<option value="">Any</option>` + objs.map((o) =>
      `<option value="${esc(o.code)}">${esc(o.code)} ${esc(o.text.length > 70 ? o.text.slice(0, 68) + "…" : o.text)}</option>`).join("");
    sel.value = objs.some((o) => o.code === keep) ? keep : "";
    state.f.subpoint = sel.value;
  }

  function syncChapterBoxes() {
    document.querySelectorAll("[data-chapter]").forEach((box) => {
      const secs = [...document.querySelectorAll(`[data-section^="${box.dataset.chapter}."]`)];
      const on = secs.filter((s) => s.checked).length;
      box.checked = on === secs.length && on > 0;
      box.indeterminate = on > 0 && on < secs.length;
    });
  }

  function readFilters() {
    const f = state.f;
    f.text = $("#f-text").value.trim().toLowerCase();
    f.sections = new Set([...document.querySelectorAll("[data-section]:checked")].map((b) => b.dataset.section));
    for (const g of ["levels", "sessions", "types", "diffs"]) {
      f[g] = new Set([...document.querySelectorAll(`[data-group="${g}"]:checked`)].map((b) => b.value));
    }
    const yf = +$("#f-year-from").value, yt = +$("#f-year-to").value;
    f.yearFrom = Math.min(yf, yt);
    f.yearTo = Math.max(yf, yt);
    f.hidePaper = $("#f-hide-paper").checked;
    f.subpoint = $("#f-subpoint").value;
  }

  function wireFilters() {
    $("#filters").addEventListener("change", (e) => {
      const t = e.target;
      if (t.dataset.chapter) {
        document.querySelectorAll(`[data-section^="${t.dataset.chapter}."]`).forEach((s) => (s.checked = t.checked));
      }
      if (t.dataset.chapter || t.dataset.section) {
        syncChapterBoxes();
        readFilters();
        fillSubpoints();
      }
      update();
    });
    $("#f-sections").addEventListener("click", (e) => {
      if (e.target.matches("summary input")) e.stopPropagation();
    }, true);
    let timer;
    $("#f-text").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(update, 200); });
    $("#reset-filters").addEventListener("click", () => {
      document.querySelectorAll("#filters input[type=checkbox]").forEach((b) => { b.checked = false; b.indeterminate = false; });
      $("#f-text").value = "";
      $("#f-year-from").value = state.yearMin;
      $("#f-year-to").value = state.yearMax;
      readFilters();
      fillSubpoints();
      update();
    });
  }

  // ------------------------------------------------------------ matching
  function matches(q) {
    const f = state.f;
    if (f.sections.size && !f.sections.has(q.effSection)) return false;
    if (isMCQ() && f.levels.size && !q.allLevels.some((l) => f.levels.has(l))) return false;
    if (f.sessions.size && !q.sessions.some((s) => f.sessions.has(s))) return false;
    if (!q.years.some((y) => y >= f.yearFrom && y <= f.yearTo)) return false;
    if (f.types.size && !q.kinds.some((k) => f.types.has(k))) return false;
    if (f.diffs.size && !(q.tag && f.diffs.has(String(q.tag.difficulty)))) return false;
    if (f.subpoint && !(q.tag && q.tag.subpoints.includes(f.subpoint))) return false;
    if (f.hidePaper && state.paper.includes(q.id)) return false;
    if (f.text) {
      for (const word of f.text.split(/\s+/)) if (!q.search.includes(word)) return false;
    }
    return true;
  }

  function sortList(list) {
    const sn = (q) => q.refs[0].sn;
    if (state.sort === "new") return list.sort((a, b) => Math.max(...b.years) - Math.max(...a.years) || sn(a) - sn(b));
    if (state.sort === "old") return list.sort((a, b) => Math.min(...a.years) - Math.min(...b.years) || sn(a) - sn(b));
    return list.sort((a, b) => sn(a) - sn(b));
  }

  // ------------------------------------------------------------ results
  function refText(r) { return `${r.paper} Q${r.q}`; }

  function imgTag(p, alt) {
    return `<img src="${esc(p.image)}" alt="${esc(alt)}" loading="lazy"
      width="${Math.round(p.w_mm * 10)}" height="${Math.round(p.h_mm * 10)}" style="--w:${p.w_mm}">`;
  }

  function tagChips(q) {
    if (!q.tag) return "";
    const kindLabels = isMCQ() ? TYPE_LABEL : SKILL_LABEL;
    return q.tag.subpoints.map((c) => {
      const o = state.objectives.get(c);
      return `<span class="tag" title="${esc(o ? o.text : "")}">${esc(c)}</span>`;
    }).join("") +
      q.kinds.map((k) => `<span class="tag">${esc(kindLabels[k] || k)}</span>`).join("") +
      `<span class="tag">${esc(DIFF_LABEL[q.tag.difficulty] || "")}</span>`;
  }

  function msHTML(q) {
    if (q.ms_status === "not_applicable") return `<div class="ms"><p class="muted">No mark scheme is published for this question.</p></div>`;
    if (!q.ms.length) return `<div class="ms"><p class="muted">No mark scheme was found for this question.</p></div>`;
    return `<div class="ms"><h3>Mark scheme</h3>${q.ms.map((p) => imgTag(p, "Mark scheme")).join("")}</div>`;
  }

  function slipHTML(q) {
    const pos = state.paper.indexOf(q.id);
    const inPaper = pos >= 0;
    const refs = q.allRefs;
    const moved = q.effSection !== q.section
      ? `<span class="moved" title="Filed under ${esc(q.section)} in the book">moved from ${esc(q.section)}</span>` : "";
    const head = [
      `<span class="slip-sec">${esc(q.effSection)} ${esc(state.sectionTitle[q.effSection] || "")}</span>`,
      moved,
      `<span class="slip-refs" title="${esc(refs.map(refText).join("\n"))}">${esc(refText(refs[0]))}${refs.length > 1 ? ` and ${refs.length - 1} more` : ""}</span>`,
    ];
    if (isMCQ()) {
      head.push(`<span class="slip-refs">${q.allLevels.map((l) => (l === "core" ? "Core" : "Extended")).join(" + ")}</span>`);
    } else {
      if (q.tag?.marks) head.push(`<span class="slip-refs">${q.tag.marks} marks</span>`);
      head.push(`<span class="slip-refs">${q.pages.length} page${q.pages.length === 1 ? "" : "s"}</span>`);
    }

    const expanded = isMCQ() || state.expanded.has(q.id);
    const pages = expanded ? q.pages : q.pages.slice(0, 1);
    const showMS = !isMCQ() && (state.showAnswers || state.msOpen.has(q.id));
    const answer = isMCQ() && state.showAnswers
      ? `<span class="answer">Answer <b>${esc(q.answer || "?")}</b></span>` : "";

    const actions = [];
    if (!isMCQ() && q.pages.length > 1) {
      actions.push(`<button data-expand="${q.id}">${expanded ? "Show first page only" : `Show all ${q.pages.length} pages`}</button>`);
    }
    if (!isMCQ()) {
      actions.push(`<button data-ms="${q.id}">${showMS ? "Hide mark scheme" : "Mark scheme"}</button>`);
    }
    actions.push(`<button class="add-btn" data-toggle="${q.id}">${inPaper ? `Remove (question ${pos + 1})` : "Add to paper"}</button>`);

    return `<article class="slip${inPaper ? " in-paper" : ""}" data-id="${q.id}">
      <div class="slip-head">${head.join("")}</div>
      <div class="slip-body">${pages.map((p, i) => imgTag(p, `Question ${refText(refs[0])}, page ${i + 1}`)).join("")}</div>
      ${showMS ? msHTML(q) : ""}
      <div class="slip-foot">
        <div class="tags">${answer}${tagChips(q)}</div>
        <div class="slip-actions">${actions.join("")}</div>
      </div>
    </article>`;
  }

  function update(keepLimit = false) {
    readFilters();
    state.filtered = sortList(state.questions.filter(matches));
    if (!keepLimit) state.limit = PAGE_SIZE;
    render();
  }

  function render() {
    const list = state.filtered;
    const n = list.length;
    $("#result-count").textContent = `${n.toLocaleString()} question${n === 1 ? "" : "s"}`;
    $("#list").innerHTML = n
      ? list.slice(0, state.limit).map(slipHTML).join("")
      : `<div class="empty"><p>No questions match these filters.</p><p>Try clearing a filter on the left.</p></div>`;
    $("#more").hidden = n <= state.limit;
    $("#more").textContent = `Show more (${(n - state.limit).toLocaleString()} left)`;

    const f = state.f;
    const usesTags = f.types.size || f.diffs.size || f.subpoint;
    const untagged = state.questions.length - state.questions.filter((q) => q.tag).length;
    const note = $("#tag-note");
    note.hidden = !(usesTags && untagged > 0);
    note.textContent = `${isMCQ() ? "Type" : "Skill"}, difficulty and learning-objective filters only include tagged questions. ${untagged.toLocaleString()} are still waiting to be tagged.`;
  }

  function rerenderSlip(id) {
    const el = document.querySelector(`.slip[data-id="${CSS.escape(id)}"]`);
    if (el) el.outerHTML = slipHTML(state.byId.get(id));
  }

  function wireResults() {
    $("#list").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.toggle) togglePaper(b.dataset.toggle);
      if (b.dataset.expand) {
        const id = b.dataset.expand;
        state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id);
        rerenderSlip(id);
      }
      if (b.dataset.ms) {
        const id = b.dataset.ms;
        if (state.showAnswers) { state.showAnswers = false; $("#show-answers").checked = false; render(); return; }
        state.msOpen.has(id) ? state.msOpen.delete(id) : state.msOpen.add(id);
        rerenderSlip(id);
      }
    });
    $("#more").addEventListener("click", () => { state.limit += PAGE_SIZE; render(); });
    $("#show-answers").addEventListener("change", (e) => { state.showAnswers = e.target.checked; render(); });
    $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; update(); });
    $("#add-random").addEventListener("click", () => {
      const n = Math.max(1, Math.min(100, +$("#random-n").value || 10));
      const pool = state.filtered.filter((q) => !state.paper.includes(q.id));
      for (let i = pool.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [pool[i], pool[j]] = [pool[j], pool[i]];
      }
      pool.slice(0, n).forEach((q) => state.paper.push(q.id));
      paperChanged();
    });
    $("#banks").addEventListener("click", (e) => {
      const b = e.target.closest("[data-bank]");
      if (b && b.dataset.bank !== state.bank.key) switchBank(b.dataset.bank);
    });
  }

  // ------------------------------------------------------------ paper
  function togglePaper(id) {
    const i = state.paper.indexOf(id);
    if (i >= 0) state.paper.splice(i, 1); else state.paper.push(id);
    paperChanged();
  }

  function paperChanged() {
    store.save();
    renderPaper();
    update(true);
  }

  function paperQuestions() { return state.paper.map((id) => state.byId.get(id)); }

  function renderPaper() {
    const ids = state.paper;
    const qs = paperQuestions();
    $("#paper-count").textContent = ids.length;
    $("#paper-count-top").textContent = ids.length;
    $("#paper-empty").hidden = ids.length > 0;
    $("#download").disabled = ids.length === 0;
    $("#clear-paper").disabled = ids.length === 0;
    $("#paper-list").innerHTML = ids.map((id, i) => {
      const q = state.byId.get(id);
      return `<li>
        <span class="ref" title="${esc(q.effSection + " " + (state.sectionTitle[q.effSection] || ""))}">${esc(refText(q.refs[0]))}</span>
        <button data-move="-1" data-i="${i}" aria-label="Move up" ${i === 0 ? "disabled" : ""}>↑</button>
        <button data-move="1" data-i="${i}" aria-label="Move down" ${i === ids.length - 1 ? "disabled" : ""}>↓</button>
        <button data-remove="${i}" aria-label="Remove">✕</button>
      </li>`;
    }).join("");
    const auto = PaperPDF.suggestMinutes(qs, state.bank.kind);
    const mins = +$("#p-time").value || auto;
    $("#p-time").placeholder = `auto (${auto})`;
    $("#p-time-wrap").hidden = !$("#p-cover").checked;
    const marks = PaperPDF.totalMarks(qs, state.bank.kind);
    const parts = [];
    if (ids.length) {
      parts.push(`${ids.length} question${ids.length === 1 ? "" : "s"}`);
      if (marks) parts.push(`${marks} marks`);
      parts.push(`about ${PaperPDF.estimatePages(qs, state.bank.kind)} pages of questions`);
      if ($("#p-cover").checked) parts.push(`${mins} minutes`);
    }
    $("#p-summary").textContent = parts.length ? parts.join(", ") + "." : "";
  }

  function wirePaper() {
    $("#paper-list").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.remove !== undefined) state.paper.splice(+b.dataset.remove, 1);
      if (b.dataset.move) {
        const i = +b.dataset.i, j = i + +b.dataset.move;
        [state.paper[i], state.paper[j]] = [state.paper[j], state.paper[i]];
      }
      paperChanged();
    });
    ["#p-title", "#p-cover", "#p-key", "#p-refs", "#p-time"].forEach((s) =>
      $(s).addEventListener("input", () => { store.save(); renderPaper(); }));
    $("#clear-paper").addEventListener("click", () => {
      if (confirm("Remove all questions from your paper?")) { state.paper = []; paperChanged(); }
    });
    $("#download").addEventListener("click", async () => {
      const btn = $("#download");
      btn.disabled = true;
      btn.textContent = "Building PDF…";
      try {
        const qs = paperQuestions();
        await PaperPDF.build(qs, {
          kind: state.bank.kind,
          title: $("#p-title").value.trim() || "Physics practice paper",
          cover: $("#p-cover").checked,
          key: $("#p-key").checked,
          refs: $("#p-refs").checked,
          minutes: +$("#p-time").value || PaperPDF.suggestMinutes(qs, state.bank.kind),
        });
      } catch (err) {
        console.error(err);
        alert("The PDF couldn't be built: " + err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "Download PDF";
      }
    });
  }

  // ------------------------------------------------------------ drawers
  function wireDrawers() {
    const scrim = $("#scrim");
    const open = (id, btn) => {
      $(id).classList.add("open");
      scrim.hidden = false;
      btn?.setAttribute("aria-expanded", "true");
    };
    const closeAll = () => {
      document.querySelectorAll(".panel.open").forEach((p) => p.classList.remove("open"));
      scrim.hidden = true;
      document.querySelectorAll("[aria-expanded]").forEach((b) => b.setAttribute("aria-expanded", "false"));
    };
    $("#open-filters").addEventListener("click", (e) => open("#filters", e.currentTarget));
    $("#open-paper").addEventListener("click", (e) => open("#paper", e.currentTarget));
    document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeAll));
    scrim.addEventListener("click", closeAll);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAll(); });
  }

  // ------------------------------------------------------------ start
  async function start() {
    wireDrawers();
    const [syllabus] = await Promise.all([fetchJSON("data/syllabus.json", null)]);
    (syllabus?.objectives || []).forEach((o) => state.objectives.set(o.code, o));

    for (const b of BANKS) if (await loadBank(b)) state.available.push(b);
    if (!state.available.length) {
      $("#list").innerHTML = `<div class="empty"><p><strong>No questions yet.</strong></p>
        <p>Run the “1. Extract questions” workflow in the repository's Actions tab, then reload this page.</p></div>`;
      $("#result-count").textContent = "";
      return;
    }
    wireFilters();
    wireResults();
    wirePaper();
    let first = location.hash.slice(1);
    try { first = first || localStorage.getItem("qb0625.bank"); } catch { /* ignore */ }
    await switchBank(first);
    window.addEventListener("hashchange", () => {
      const key = location.hash.slice(1);
      if (key && key !== state.bank.key) switchBank(key);
    });
  }

  start();
})();
