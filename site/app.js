/* 0625 question bank - browsing, filtering and paper building. */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const SESSION_LABEL = { m: "Feb/Mar", s: "May/Jun", w: "Oct/Nov" };
  const TYPE_LABEL = {
    calculation: "Calculation", graph_or_diagram: "Graph or diagram",
    practical: "Practical", conceptual: "Conceptual",
  };
  const DIFF_LABEL = { 1: "Easier", 2: "Standard", 3: "Harder" };
  const PAGE_SIZE = 40;
  const STORE_KEY = "qb0625.paper.v1";

  const state = {
    questions: [], byId: new Map(), sections: [], chapters: [],
    sectionTitle: {}, objectives: new Map(), taggedCount: 0,
    filtered: [], limit: PAGE_SIZE,
    paper: [],
    f: { text: "", sections: new Set(), levels: new Set(), sessions: new Set(),
         types: new Set(), diffs: new Set(), yearFrom: null, yearTo: null,
         subpoint: "", hidePaper: false },
    showAnswers: false, sort: "book",
  };

  // ------------------------------------------------------------ storage
  const store = {
    load() {
      try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; }
    },
    save() {
      try {
        localStorage.setItem(STORE_KEY, JSON.stringify({
          ids: state.paper, title: $("#p-title").value, cover: $("#p-cover").checked,
          key: $("#p-key").checked, refs: $("#p-refs").checked, time: $("#p-time").value,
        }));
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
  async function load() {
    const [qdata, tags, syllabus] = await Promise.all([
      fetchJSON("data/questions.json", null),
      fetchJSON("data/tags.json", {}),
      fetchJSON("data/syllabus.json", null),
    ]);
    if (!qdata || !qdata.questions?.length) {
      $("#list").innerHTML = `<div class="empty"><p><strong>No questions yet.</strong></p>
        <p>Run the “1. Extract questions” workflow in the repository's Actions tab, then reload this page.</p></div>`;
      $("#result-count").textContent = "";
      return false;
    }
    state.sections = qdata.sections;
    state.chapters = qdata.chapters;
    state.sections.forEach((s) => (state.sectionTitle[s.code] = s.title));
    (syllabus?.objectives || []).forEach((o) => state.objectives.set(o.code, o));

    const repeatsOf = new Map();
    for (const q of qdata.questions) {
      const t = tags[q.id];
      q.tag = t && t.status === "done" ? t : null;
      if (q.tag) state.taggedCount++;
      q.repeatOf = q.tag?.repeat_of || null;
      if (q.repeatOf) {
        if (!repeatsOf.has(q.repeatOf)) repeatsOf.set(q.repeatOf, []);
        repeatsOf.get(q.repeatOf).push(q);
      }
      q.effSection = q.tag && q.tag.section_ok === false && q.tag.suggested_section
        ? q.tag.suggested_section : q.section;
      state.byId.set(q.id, q);
    }
    for (const q of qdata.questions) {
      q.allRefs = [...q.refs, ...(repeatsOf.get(q.id) || []).flatMap((r) => r.refs)];
      q.allLevels = [...new Set([...q.levels, ...(repeatsOf.get(q.id) || []).flatMap((r) => r.levels)])];
      q.years = [...new Set(q.allRefs.map((r) => r.year))];
      q.sessions = [...new Set(q.allRefs.map((r) => r.session))];
      q.search = ((q.tag?.text || "") + " " + q.allRefs.map((r) => r.paper).join(" ")).toLowerCase();
    }
    state.questions = qdata.questions.filter((q) => !q.repeatOf);

    const total = state.questions.length;
    const tagged = state.questions.filter((q) => q.tag).length;
    $("#progress").textContent = tagged >= total
      ? `${total.toLocaleString()} questions, all tagged`
      : `${total.toLocaleString()} questions, ${tagged.toLocaleString()} tagged so far`;
    return true;
  }

  // ------------------------------------------------------------ filters UI
  function chip(group, value, label) {
    return `<label class="chip"><input type="checkbox" data-group="${group}" value="${esc(value)}"><span>${esc(label)}</span></label>`;
  }

  function buildFilters() {
    const counts = {};
    state.questions.forEach((q) => (counts[q.effSection] = (counts[q.effSection] || 0) + 1));
    $("#f-sections").innerHTML = state.chapters.map((ch) => {
      const secs = state.sections.filter((s) => s.chapter === ch.num);
      const n = secs.reduce((a, s) => a + (counts[s.code] || 0), 0);
      return `<details>
        <summary><input type="checkbox" data-chapter="${ch.num}" aria-label="All of ${esc(ch.title)}">
          <span>${ch.num} ${esc(ch.title)}</span><span class="n">${n}</span></summary>
        ${secs.map((s) => `<label class="sec"><input type="checkbox" data-section="${s.code}">
          <span>${s.code} ${esc(s.title)}</span><span class="n">${counts[s.code] || 0}</span></label>`).join("")}
      </details>`;
    }).join("");

    $("#f-levels").innerHTML = chip("levels", "core", "Core") + chip("levels", "extended", "Extended");
    $("#f-sessions").innerHTML = Object.entries(SESSION_LABEL).map(([k, v]) => chip("sessions", k, v)).join("");
    $("#f-types").innerHTML = Object.entries(TYPE_LABEL).map(([k, v]) => chip("types", k, v)).join("");
    $("#f-diffs").innerHTML = Object.entries(DIFF_LABEL).map(([k, v]) => chip("diffs", k, v)).join("");

    const years = [...new Set(state.questions.flatMap((q) => q.years))].sort();
    const opts = years.map((y) => `<option value="${y}">${y}</option>`).join("");
    $("#f-year-from").innerHTML = opts;
    $("#f-year-to").innerHTML = opts;
    $("#f-year-from").value = years[0];
    $("#f-year-to").value = years[years.length - 1];
    state.yearMin = years[0];
    state.yearMax = years[years.length - 1];

    if (!state.objectives.size) $("#f-subpoint-wrap").hidden = true;
    fillSubpoints();
  }

  function fillSubpoints() {
    const sel = $("#f-subpoint");
    const keep = sel.value;
    const secs = state.f.sections;
    const objs = [...state.objectives.values()].filter((o) => !secs.size || secs.has(o.section));
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
      if (t.id === "f-subpoint") state.f.subpoint = t.value;
      update();
    });
    // stop the chapter checkbox from toggling the <details> open state
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
    if (f.levels.size && !q.allLevels.some((l) => f.levels.has(l))) return false;
    if (f.sessions.size && !q.sessions.some((s) => f.sessions.has(s))) return false;
    if (!q.years.some((y) => y >= f.yearFrom && y <= f.yearTo)) return false;
    if (f.types.size && !(q.tag && f.types.has(q.tag.type))) return false;
    if (f.diffs.size && !(q.tag && f.diffs.has(String(q.tag.difficulty)))) return false;
    if (f.subpoint && !(q.tag && q.tag.subpoints.includes(f.subpoint))) return false;
    if (f.hidePaper && state.paper.includes(q.id)) return false;
    if (f.text) {
      for (const word of f.text.split(/\s+/)) if (!q.search.includes(word)) return false;
    }
    return true;
  }

  function sortList(list) {
    if (state.sort === "new") return list.sort((a, b) => Math.max(...b.years) - Math.max(...a.years) || a.refs[0].sn - b.refs[0].sn);
    if (state.sort === "old") return list.sort((a, b) => Math.min(...a.years) - Math.min(...b.years) || a.refs[0].sn - b.refs[0].sn);
    return list.sort((a, b) => a.refs[0].sn - b.refs[0].sn);
  }

  // ------------------------------------------------------------ results
  function refText(r) { return `${r.paper} Q${r.q}`; }

  function slipHTML(q) {
    const pos = state.paper.indexOf(q.id);
    const inPaper = pos >= 0;
    const refs = q.allRefs;
    const levels = q.allLevels.map((l) => (l === "core" ? "Core" : "Extended")).join(" + ");
    const moved = q.effSection !== q.section
      ? `<span class="moved" title="Filed under ${esc(q.section)} in the book">moved from ${esc(q.section)}</span>` : "";
    let tags = "";
    if (q.tag) {
      tags = q.tag.subpoints.map((c) => {
        const o = state.objectives.get(c);
        return `<span class="tag" title="${esc(o ? o.text : "")}">${esc(c)}</span>`;
      }).join("") +
        `<span class="tag">${esc(TYPE_LABEL[q.tag.type] || q.tag.type)}</span>` +
        `<span class="tag">${esc(DIFF_LABEL[q.tag.difficulty] || "")}</span>`;
    }
    const answer = state.showAnswers
      ? `<span class="answer">Answer <b>${esc(q.answer || "?")}</b></span>` : "";
    return `<article class="slip${inPaper ? " in-paper" : ""}" data-id="${q.id}">
      <div class="slip-head">
        <span class="slip-sec">${esc(q.effSection)} ${esc(state.sectionTitle[q.effSection] || "")}</span>
        ${moved}
        <span class="slip-refs" title="${esc(refs.map(refText).join("\n"))}">${esc(refText(refs[0]))}${refs.length > 1 ? ` and ${refs.length - 1} more` : ""}</span>
        <span class="slip-refs">${levels}</span>
      </div>
      <div class="slip-body">
        <img src="${esc(q.image)}" alt="Question ${esc(refText(refs[0]))}" loading="lazy"
          width="${Math.round(q.w_mm * 10)}" height="${Math.round(q.h_mm * 10)}" style="--w:${q.w_mm}">
      </div>
      <div class="slip-foot">
        <div class="tags">${answer}${tags}</div>
        <button class="add-btn" data-toggle="${q.id}">${inPaper ? `Remove (question ${pos + 1})` : "Add to paper"}</button>
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
    note.textContent = `Type, difficulty and learning-objective filters only include tagged questions. ${untagged.toLocaleString()} are still waiting to be tagged.`;
  }

  function wireResults() {
    $("#list").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-toggle]");
      if (btn) togglePaper(btn.dataset.toggle);
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

  function renderPaper() {
    const ids = state.paper;
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
    const mins = +$("#p-time").value || PaperPDF.suggestMinutes(ids.length);
    $("#p-time").placeholder = `auto (${PaperPDF.suggestMinutes(ids.length)})`;
    $("#p-time-wrap").hidden = !$("#p-cover").checked;
    $("#p-summary").textContent = ids.length
      ? `${ids.length} question${ids.length === 1 ? "" : "s"}, about ${PaperPDF.estimatePages(ids.map((x) => state.byId.get(x)))} pages of questions${$("#p-cover").checked ? `, ${mins} minutes` : ""}.`
      : "";
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
        await PaperPDF.build(state.paper.map((id) => state.byId.get(id)), {
          title: $("#p-title").value.trim() || "Physics practice paper",
          cover: $("#p-cover").checked,
          key: $("#p-key").checked,
          refs: $("#p-refs").checked,
          minutes: +$("#p-time").value || PaperPDF.suggestMinutes(state.paper.length),
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
    if (!(await load())) return;
    const saved = store.load();
    state.paper = (saved.ids || []).filter((id) => state.byId.has(id));
    if (saved.title) $("#p-title").value = saved.title;
    if (saved.cover !== undefined) $("#p-cover").checked = saved.cover;
    if (saved.key !== undefined) $("#p-key").checked = saved.key;
    if (saved.refs !== undefined) $("#p-refs").checked = saved.refs;
    if (saved.time) $("#p-time").value = saved.time;
    buildFilters();
    wireFilters();
    wireResults();
    wirePaper();
    renderPaper();
    update();
  }

  start();
})();
