/* PDF Watermark - UI logic.
 *
 * Runs inside a pywebview window, where window.pywebview.api is the Python
 * bridge (app.py). Opened directly in a browser there is no bridge, so a mock
 * API with sample data stands in - handy for working on the design alone.
 *
 * Three steps: Files, Design (who the copies are for, and how the mark looks
 * and sits), Export.
 */

const PASTELS = ["#8fb4ff", "#c4a8ff", "#8fe3c8", "#ffc29a", "#ff9fb8", "#e8d29a"];
const COLORS = { grey: "#999999", red: "#d93333", blue: "#2659d9", black: "#000000" };
const STEP_COUNT = 3;
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

const ICON = {
  folder: '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24"><path d="M6 3h12v18l-6-4.5L6 21z"/></svg>',
  tick: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
};

const state = {
  step: 0,
  sources: [],              // {path, name, kind, pdfs, ignored, ignored_files, files, problems}
  recipients: [],           // names as typed
  companies: [],            // saved on this computer, offered under Recipients
  format: "standard",       // standard | dated | custom - see FORMATS
  prefixText: "CONFIDENTIAL", // used by the custom format; empty means the name alone
  customDate: false,
  fontFamily: "Helvetica",
  bold: true,
  italic: false,
  outline: false,
  color: "grey",            // a swatch name or "#rrggbb"
  opacity: 0.4,
  layout: "diagonal",       // diagonal | tile | position
  size: 1,                  // diagonal and tile: 0.4-1 of the largest that fits
  stampSize: 1,             // position: 0.5-4, where 1 is about 12pt on A4
  position: [0.5, 1],       // stamp spot, as fractions across and down
  angle: "diagonal",        // "diagonal" (corner to corner) or degrees
  stack: false,
  flatten: false,
  out: { mode: "beside", dir: null },
  previewDoc: 0,
  previewRecipient: 0,
  outputDir: null,
  fonts: null,              // [{family, builtin, bold, italic}] once loaded
};

/* ---- Mock bridge --------------------------------------------------------- */

const SAMPLE_FOLDER = {
  path: "C:\\Users\\me\\Documents\\Tender pack", name: "Tender pack", kind: "folder",
  pdfs: 5, ignored: 2, ignored_files: ["Meeting notes.docx", "Budget.xlsx"],
  files: ["Cover letter.pdf", "Scope of works.pdf", "drawings\\GA-101 Site plan.pdf",
          "drawings\\GA-102 Elevations.pdf", "drawings\\details\\DT-210 Fixings.pdf"],
  problems: [{ rel: "HR\\Salaries.pdf", path: "C:\\Users\\me\\Documents\\Tender pack\\HR\\Salaries.pdf", reason: "password-protected" }],
};
const SAMPLE_FILE = {
  path: "C:\\Users\\me\\Desktop\\Pricing schedule.pdf", name: "Pricing schedule.pdf", kind: "file",
  pdfs: 1, ignored: 0, ignored_files: [], files: ["Pricing schedule.pdf"], problems: [],
};
const SAMPLE_FONTS = ["Helvetica", "Times", "Courier"].map(f => ({ family: f, builtin: true, bold: true, italic: true }))
  .concat(["Arial", "Calibri", "Cambria", "Consolas", "Georgia", "Segoe UI", "Tahoma", "Verdana"]
    .map(f => ({ family: f, builtin: false, bold: true, italic: f !== "Consolas" })));

const mockApi = {
  async pick_files() { return [SAMPLE_FILE]; },
  async pick_folder() { return [SAMPLE_FOLDER]; },
  async inspect(paths) {
    return paths.map(p => {
      const name = p.split(/[\\/]/).pop();
      return name.toLowerCase().endsWith(".pdf")
        ? { ...SAMPLE_FILE, path: p, name, files: [name] }
        : { ...SAMPLE_FOLDER, path: p, name };
    });
  },
  async pick_output() { return "D:\\Outgoing"; },
  async preview() { return null; },      // null = draw the stand-in page locally
  async plan() { return buildPlan(); },
  async list_fonts() { return SAMPLE_FONTS; },
  async run(settings) {
    mockCancelled = false;
    const jobs = buildPlan().flatMap(g => g.files.map(f => ({ text: g.text, label: f.label })));
    window.onRunStart(jobs.length);
    let done = 0;
    for (; done < jobs.length && !mockCancelled; done++) {
      await new Promise(r => setTimeout(r, 260));
      window.onRunProgress(done + 1, jobs[done].label, jobs[done].text, true, "");
    }
    window.onRunDone(done, 0, settings.out_dir || state.sources[0]?.path, mockCancelled);
  },
  async cancel() { mockCancelled = true; },
  async open_path() {},
  async open_help() {},
  async initial_sources() { return []; },
  async load_settings() { return {}; },
  async save_settings() {},
  async load_companies() { try { return JSON.parse(localStorage.getItem("companies") || "[]"); } catch { return []; } },
  async save_companies(names) { try { localStorage.setItem("companies", JSON.stringify(names)); } catch { /* preview only */ } },
  async log_js_error() {},
};
let mockCancelled = false;

let api = mockApi;
const inApp = () => api !== mockApi;

// Style choices persist between sessions; who the copies are for does not.
// The version number lets a new release ignore settings whose meaning changed.
const SETTINGS_VERSION = 3;
const REMEMBERED = ["format", "prefixText", "customDate", "fontFamily", "bold", "italic", "outline",
  "color", "opacity", "layout", "size", "stampSize", "position", "angle", "stack", "flatten"];
function remember() {
  api.save_settings({ v: SETTINGS_VERSION, ...Object.fromEntries(REMEMBERED.map(k => [k, state[k]])) });
}

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  const saved = await api.load_settings();
  if (saved.v === SETTINGS_VERSION) for (const k of REMEMBERED) if (k in saved) state[k] = saved[k];
  state.companies = await api.load_companies();
  const initial = await api.initial_sources();
  if (initial.length) addSources(initial); else render();
});

/* ---- Helpers ------------------------------------------------------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// The ready-made mark texts, plus "custom", which takes its wording and date
// setting from the Customise panel.
const FORMATS = {
  standard: { label: "CONFIDENTIAL – Name", prefix: "CONFIDENTIAL", date: false },
  dated: { label: "CONFIDENTIAL – Name – Date", prefix: "CONFIDENTIAL", date: true },
};
const format = () => FORMATS[state.format] || { prefix: state.prefixText.trim(), date: state.customDate };
const watermarkText = name => {
  const { prefix, date } = format();
  return (prefix ? `${prefix} - ${name}` : name) + (date ? " - {date}" : "");
};
const today = () => new Date().toISOString().slice(0, 10);
const shown = text => text.replaceAll("{date}", today());
const texts = () => state.recipients.map(watermarkText);
const pdfCount = () => state.sources.reduce((n, s) => n + s.pdfs, 0);
const allDocs = () => state.sources.flatMap(s => s.files.map(f => ({ source: s, rel: f })));
const isStamp = () => state.layout === "position";

// Mirrors slugify() in batch_watermark.py for the browser-only mock; inside
// the app the file list comes from Python itself.
const slugify = t => shown(t).replace(/\{(page|pages|file)\}/g, "").replace(/[<>:"/\\|?*\x00-\x1f]/g, "-")
  .replace(/\s+/g, " ").replace(/^[ .-]+|[ .-]+$/g, "").slice(0, 80).replace(/[ .-]+$/, "") || "Watermarked";

function settings() {
  return {
    texts: texts(),
    sources: state.sources.map(s => s.path),
    font: state.fontFamily,
    bold: state.bold,
    italic: state.italic,
    outline: state.outline,
    color: state.color,
    opacity: state.opacity,
    layout: state.layout,
    size: isStamp() ? state.stampSize : state.size,
    position: state.position,
    angle: state.angle,
    stack: state.stack,
    flatten: state.flatten,
    skip: state.sources.flatMap(s => (s.problems || []).map(p => p.path)),
    out_dir: state.out.mode === "custom" ? state.out.dir : null,
  };
}

function canEnter(step) {
  if (step >= 1 && pdfCount() === 0) return false;
  if (step >= 2 && state.recipients.length === 0) return false;
  return true;
}

/* ---- Step navigation ----------------------------------------------------- */

function go(step) {
  if (step < 0 || step >= STEP_COUNT || !canEnter(step)) return;
  state.step = step;
  render();
}

function placeSpot() {
  const btn = $(`.steps button[data-step="${state.step}"]`);
  const spot = $("#spot");
  spot.style.width = btn.offsetWidth + "px";
  spot.style.transform = `translateX(${btn.offsetLeft}px)`;
}

function render() {
  $$(".view").forEach(v => { v.hidden = Number(v.dataset.view) !== state.step; });
  $$(".steps button").forEach(b => {
    const n = Number(b.dataset.step);
    b.classList.toggle("active", n === state.step);
    b.classList.toggle("complete", n < state.step);
    b.disabled = !canEnter(n);
  });
  placeSpot();

  $("#back").style.visibility = state.step === 0 ? "hidden" : "visible";
  const next = $("#next");
  const last = state.step === STEP_COUNT - 1;
  const files = pdfCount() * state.recipients.length;
  next.textContent = last ? `Watermark ${plural(files, "copy", "copies")}` : "Continue";
  next.className = "btn " + (last ? "btn-accent" : "btn-light");
  next.disabled = last ? (state.out.mode === "custom" && !state.out.dir) : !canEnter(state.step + 1);

  renderNotice();
  [renderFiles, renderDesign, renderExport][state.step]();
}

function renderNotice() {
  let msg = "";
  if (state.step === 0 && state.sources.length && pdfCount() === 0) msg = "None of those contain any PDFs. Try a different folder.";
  if (state.step === 1 && state.recipients.length === 0) msg = "Add at least one company to continue.";
  $("#notice").hidden = !msg;
  $("#noticeText").textContent = msg;
}

/* ---- 1 · Files ----------------------------------------------------------- */

function addSources(list) {
  for (const s of list) {
    if (!state.sources.some(x => x.path === s.path)) state.sources.push(s);
  }
  render();
}

function renderFiles() {
  $("#dropzone").classList.toggle("compact", state.sources.length > 0);
  $("#dropzone h2").textContent = state.sources.length ? "Add more PDFs or folders" : "Drag and drop PDFs or folders here";

  $("#sources").innerHTML = state.sources.map((s, i) => {
    const tint = s.kind === "folder" ? "#8fb4ff" : "#ffc29a";
    const problems = s.problems || [];
    const meta = [
      s.pdfs ? `<span class="pill"><i></i>${s.kind === "folder" ? plural(s.pdfs, "PDF") : "PDF"}</span>` : "",
      problems.length ? `<span class="pill warn"><i></i>${problems.length} can't be marked</span>` : "",
      s.ignored ? `<span class="pill" style="--dot:#6b6b74"><i></i>${s.ignored} not ${s.ignored === 1 ? "a PDF" : "PDFs"}</span>` : "",
    ].join("");
    const skipped = s.ignored_files || [];
    const more = s.ignored - skipped.slice(0, 5).length;
    const list = (problems.length || skipped.length)
      ? `<ul class="src-problems">${problems.map(p =>
          `<li>${esc(p.rel)}<span>— ${esc(p.reason)}, will be left out</span></li>`).join("")}</ul>` +
        (skipped.length ? `<ul class="src-problems src-skipped">${skipped.slice(0, 5).map(f =>
          `<li>${esc(f)}<span>— not a PDF, left as it is</span></li>`).join("")}${more > 0
          ? `<li><span>and ${more} more ${more === 1 ? "file" : "files"} that aren't PDFs</span></li>` : ""}</ul>` : "")
      : "";
    return `<li class="source-wrap"><div class="source">
      <div class="src-icon" style="--tint:${tint}">${s.kind === "folder" ? ICON.folder : ICON.file}</div>
      <div class="src-main"><div class="src-name">${esc(s.name)}</div><div class="src-path">${esc(s.path)}</div></div>
      <div class="row gap-sm">${meta}</div>
      <button class="remove" data-remove-source="${i}" aria-label="Remove ${esc(s.name)}">${ICON.x}</button>
    </div>${list}</li>`;
  }).join("");
}

/* ---- 2 · Design ---------------------------------------------------------- */

function renderDesign() {
  renderRecipients();
  renderFormat();
  renderTextStyle();
  renderPlacement();
  $("#flattenToggle").checked = state.flatten;
  renderPreview();
}

/* Recipients */

function addRecipient() {
  const input = $("#recipientInput");
  // Allow pasting a comma or newline separated list in one go.
  const names = input.value.split(/[,\n;]/).map(s => s.trim()).filter(Boolean);
  for (const n of names) {
    if (!isAdded(n)) state.recipients.push(n);
  }
  if (names.length) state.previewRecipient = state.recipients.length - 1;
  input.value = "";
  render();
  input.focus();
}

const isSaved = name => state.companies.some(c => c.toLowerCase() === name.toLowerCase());
const isAdded = name => state.recipients.some(r => r.toLowerCase() === name.toLowerCase());
const saveCompanies = () => api.save_companies(state.companies);

function renderRecipients() {
  const unsaved = state.companies.filter(c => !isAdded(c));
  $("#saved").hidden = unsaved.length === 0;
  $("#savedChips").innerHTML = state.companies.map((c, i) => isAdded(c) ? "" : `
    <span class="chip" role="button" tabindex="0" data-company="${i}">
      ${esc(c)}<span class="chip-x" data-forget="${i}" title="Forget ${esc(c)}" aria-label="Forget ${esc(c)}">${ICON.x}</span>
    </span>`).join("");
  $("#recipientCount").textContent = state.recipients.length
    ? `${plural(state.recipients.length, "copy", "copies")} of each document` : "";
  state.previewRecipient = clamp(state.previewRecipient, 0, Math.max(state.recipients.length - 1, 0));
  $("#recipients").innerHTML = state.recipients.length
    ? state.recipients.map((r, i) => `
      <li class="rrow ${i === state.previewRecipient ? "sel" : ""}" style="--tint:${PASTELS[i % PASTELS.length]}"
          data-preview-recipient="${i}" title="Preview ${esc(r)}'s copy">
        <span class="dot"></span>
        <span class="rmain"><span class="rname">${esc(r)}</span><span class="rtext">${esc(shown(watermarkText(r)))}</span></span>
        <button class="save ${isSaved(r) ? "saved" : ""}" data-save-recipient="${i}"
          data-tip="${isSaved(r) ? "Saved on this computer" : "Save for next time"}"
          aria-label="${isSaved(r) ? "Saved" : "Save"} ${esc(r)}">${ICON.bookmark}</button>
        <button class="remove" data-remove-recipient="${i}" aria-label="Remove ${esc(r)}">${ICON.x}</button>
      </li>`).join("")
    : `<li class="rempty">Each company you add gets its own marked copy.</li>`;
}

/* Mark text dropdown */

const formatOptions = () => {
  const name = state.recipients[state.previewRecipient] || "Company name";
  const example = (prefix, date) => (prefix ? `${prefix} – ${name}` : name) + (date ? ` – ${today()}` : "");
  const custom = { prefix: state.prefixText.trim(), date: state.customDate };
  return [
    { value: "standard", title: FORMATS.standard.label, example: example("CONFIDENTIAL", false) },
    { value: "dated", title: FORMATS.dated.label, example: example("CONFIDENTIAL", true) },
    { value: "custom", title: "Customise…", example: state.format === "custom"
        ? example(custom.prefix, custom.date) : "Your own wording, with or without the date" },
  ];
};
let menuIndex = 0;

function renderFormat() {
  const opts = formatOptions();
  const current = opts.find(o => o.value === state.format) || opts[0];
  $("#formatValue").innerHTML = state.format === "custom"
    ? `<b>${esc(current.example)}</b><span class="tag">Custom</span>`
    : `<b>${esc(current.title)}</b>`;
  const menu = $("#formatMenu");
  menu.innerHTML = opts.map((o, i) => `${i === 2 ? '<li class="sep" role="presentation"></li>' : ""}
    <li role="option" id="fmt-${o.value}" data-value="${o.value}" data-index="${i}"
        aria-selected="${o.value === state.format}" class="${i === menuIndex ? "active" : ""}">
      <span class="opt"><b>${esc(o.title)}</b><small>${esc(o.example)}</small></span>
      <span class="tick">${ICON.tick}</span>
    </li>`).join("");
  menu.setAttribute("aria-activedescendant", `fmt-${opts[menuIndex].value}`);
  $("#customise").hidden = state.format !== "custom";
  if (document.activeElement !== $("#prefixText")) $("#prefixText").value = state.prefixText;
  $("#dateToggle").checked = state.customDate;
  $$("#prefixPresets .chip").forEach(c => c.classList.toggle("on", c.dataset.preset === state.prefixText.trim()));
}

function openFormatMenu(open) {
  const menu = $("#formatMenu");
  if (open === !menu.hidden) return;
  if (open) closePopovers();
  menu.hidden = !open;
  $("#formatSelect").classList.toggle("open", open);
  $("#formatTrigger").setAttribute("aria-expanded", String(open));
  if (open) {
    menuIndex = Math.max(0, formatOptions().findIndex(o => o.value === state.format));
    renderFormat();
    menu.focus();
  }
}

function chooseFormat(value) {
  state.format = value;
  openFormatMenu(false);
  remember();
  render();
  $("#formatTrigger").focus();
  // Selected, so typing replaces the wording instead of adding to it.
  if (value === "custom") $("#prefixText").select();
}

/* Text style toolbar */

const fontInfo = family => (state.fonts || SAMPLE_FONTS).find(f => f.family === family)
  || { family, builtin: false, bold: true, italic: true };

function renderTextStyle() {
  const info = fontInfo(state.fontFamily);
  $("#fontValue").textContent = state.fontFamily;
  $("#fontValue").style.fontFamily = cssFont(state.fontFamily);
  const bold = $("#boldBtn"), italic = $("#italicBtn");
  bold.disabled = !info.bold;
  italic.disabled = !info.italic;
  bold.setAttribute("aria-pressed", String(state.bold && info.bold));
  italic.setAttribute("aria-pressed", String(state.italic && info.italic));
  bold.dataset.tip = info.bold ? "Bold" : `${state.fontFamily} has no bold`;
  italic.dataset.tip = info.italic ? "Italic" : `${state.fontFamily} has no italic`;
  $("#outlineBtn").setAttribute("aria-pressed", String(state.outline));
  $("#colorDot").style.setProperty("--dot", COLORS[state.color] || state.color);
  $$("#swatches button").forEach(b => b.classList.toggle("selected", b.dataset.color === state.color));
  const custom = state.color.startsWith("#");
  $(".custom-swatch").classList.toggle("selected", custom);
  if (custom) { $(".custom-swatch").style.setProperty("--sw", state.color); $("#customColor").value = state.color; }

  const size = $("#size");
  if (isStamp()) { size.min = 50; size.max = 400; size.step = 10; }
  else { size.min = 40; size.max = 100; size.step = 5; }
  setRange("#size", Math.round((isStamp() ? state.stampSize : state.size) * 100), "#sizeValue");
  setRange("#opacity", Math.round(state.opacity * 100), "#opacityValue");
}

// The UI can show installed fonts by name; the built-in PDF fonts map onto
// their everyday equivalents.
const cssFont = family => ({ Helvetica: "Helvetica, Arial, sans-serif", Times: "'Times New Roman', Times, serif",
  Courier: "'Courier New', Courier, monospace" }[family] || `"${family}", system-ui, sans-serif`);

function setRange(sel, value, labelSel) {
  const el = $(sel), min = Number(el.min), max = Number(el.max);
  el.value = value;
  el.style.setProperty("--fill", ((value - min) / (max - min)) * 100 + "%");
  $(labelSel).textContent = value + "%";
}

let fontIndex = 0;
let fontMatches = [];

async function openFontMenu(open) {
  const menu = $("#fontMenu");
  if (open === !menu.hidden) return;
  if (open) closePopovers();
  menu.hidden = !open;
  $("#fontTrigger").setAttribute("aria-expanded", String(open));
  if (!open) return;
  if (!state.fonts) {
    $("#fontList").innerHTML = '<li class="none">Loading fonts…</li>';
    state.fonts = await api.list_fonts();
  }
  $("#fontSearch").value = "";
  renderFontList(true);
  $("#fontSearch").focus();
}

function renderFontList(scrollToCurrent = false) {
  const q = $("#fontSearch").value.trim().toLowerCase();
  fontMatches = state.fonts.filter(f => !q || f.family.toLowerCase().includes(q));
  if (scrollToCurrent) fontIndex = Math.max(0, fontMatches.findIndex(f => f.family === state.fontFamily));
  fontIndex = clamp(fontIndex, 0, Math.max(fontMatches.length - 1, 0));
  const builtins = fontMatches.filter(f => f.builtin), installed = fontMatches.filter(f => !f.builtin);
  const item = f => {
    const i = fontMatches.indexOf(f);
    return `<li role="option" data-family="${esc(f.family)}" data-index="${i}" class="${i === fontIndex ? "active" : ""}"
      aria-selected="${f.family === state.fontFamily}"><span style="font-family:${esc(cssFont(f.family))}">${esc(f.family)}</span></li>`;
  };
  $("#fontList").innerHTML = fontMatches.length
    ? (builtins.length ? `<li class="group">Built into every PDF reader</li>${builtins.map(item).join("")}` : "")
      + (installed.length ? `<li class="group">On this computer</li>${installed.map(item).join("")}` : "")
    : `<li class="none">No font called “${esc(q)}”</li>`;
  const active = $("#fontList li.active");
  if (active) active.scrollIntoView({ block: scrollToCurrent ? "center" : "nearest" });
}

function chooseFont(family) {
  state.fontFamily = family;
  const info = fontInfo(family);
  if (!info.bold) state.bold = false;
  if (!info.italic) state.italic = false;
  openFontMenu(false);
  remember();
  render();
  $("#fontTrigger").focus();
}

function toggleBold() {
  if ($("#boldBtn").disabled) return;
  state.bold = !state.bold; remember(); render();
}
function toggleItalic() {
  if ($("#italicBtn").disabled) return;
  state.italic = !state.italic; remember(); render();
}
function toggleOutline() { state.outline = !state.outline; remember(); render(); }

function openColorPop(open) {
  const pop = $("#colorPop");
  if (open === !pop.hidden) return;
  if (open) closePopovers();
  pop.hidden = !open;
  $("#colorBtn").setAttribute("aria-expanded", String(open));
}

function closePopovers() {
  openFormatMenu(false);
  openFontMenu(false);
  openColorPop(false);
}

/* Placement */

const LAYOUT_HINTS = {
  diagonal: "One large mark across the middle of every page.",
  tile: "Repeated across the whole page. The hardest to crop out.",
  position: "A small stamp in one spot - a corner, an edge or the centre.",
};
// Grid cells in reading order, and the number key for each, laid out like a
// numeric keypad: 7 8 9 along the top, 1 2 3 along the bottom.
const GRID = [[0, 0], [0.5, 0], [1, 0], [0, 0.5], [0.5, 0.5], [1, 0.5], [0, 1], [0.5, 1], [1, 1]];
const GRID_KEYS = ["7", "8", "9", "4", "5", "6", "1", "2", "3"];
const GRID_NAMES = ["Top left", "Top centre", "Top right", "Middle left", "Centre", "Middle right",
  "Bottom left", "Bottom centre", "Bottom right"];

function positionName() {
  const i = GRID.findIndex(([x, y]) => Math.abs(x - state.position[0]) < 0.01 && Math.abs(y - state.position[1]) < 0.01);
  return i >= 0 ? GRID_NAMES[i] : "Custom spot";
}

function renderPlacement() {
  $$("#layoutSeg button").forEach(b => b.classList.toggle("selected", b.dataset.layout === state.layout));
  $("#layoutHint").textContent = LAYOUT_HINTS[state.layout];
  $("#positionControls").hidden = !isStamp();
  $("#angleControl").hidden = isStamp();
  $$("#angleSeg button").forEach(b => b.classList.toggle("selected", String(b.dataset.angle) === String(state.angle)));
  $("#stackToggle").checked = state.stack;
  $("#posGrid").innerHTML = GRID.map(([x, y], i) => {
    const on = Math.abs(x - state.position[0]) < 0.01 && Math.abs(y - state.position[1]) < 0.01;
    return `<button type="button" role="radio" aria-checked="${on}" data-cell="${i}" aria-label="${GRID_NAMES[i]}"
      data-tip="${GRID_NAMES[i]}" data-keys="${GRID_KEYS[i]}" tabindex="${on ? 0 : -1}"><i></i></button>`;
  }).join("");
  $("#positionName").textContent = positionName();
}

function setLayout(layout) {
  if (state.layout === layout) return;
  state.layout = layout;
  remember(); render();
}

function setPosition(x, y, save = true) {
  state.position = [clamp(x, 0, 1), clamp(y, 0, 1)];
  if (state.layout !== "position") state.layout = "position";
  if (save) remember();
  render();
}

/* Preview */

let previewToken = 0;
let previewTimer;
let stampBox = null;       // where the stamp landed, as page fractions (from Python or the mock)

function renderPreview() {
  const docs = allDocs();
  state.previewDoc = clamp(state.previewDoc, 0, docs.length - 1);
  const doc = docs[state.previewDoc];
  $("#previewLabel").textContent = `${doc.rel.split(/[\\/]/).pop()}  ·  ${state.previewDoc + 1} of ${docs.length}`;
  $("#prevPage").disabled = state.previewDoc === 0;
  $("#nextPage").disabled = state.previewDoc === docs.length - 1;

  const token = ++previewToken;
  const name = state.recipients[state.previewRecipient] || "Company name";
  const text = watermarkText(name);
  // Debounced, so dragging a slider does not queue up a render per pixel.
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const result = await api.preview({ ...settings(), text, source: doc.source.path, rel: doc.rel, page: 0, want_box: true });
    if (token !== previewToken) return;
    const png = result && (result.png || result);
    $("#previewImg").hidden = !png;
    $("#previewCanvas").hidden = !!png;
    if (png) {
      stampBox = result.box || null;
      const img = $("#previewImg");
      img.onload = placeHandle;
      img.src = png;
    } else if (inApp()) {
      stampBox = null;
      drawUnavailable();
    } else {
      stampBox = drawStandIn(shown(text));
    }
    placeHandle();
  }, inApp() ? 120 : 0);
}

function drawUnavailable() {
  const c = $("#previewCanvas"), g = c.getContext("2d");
  c.width = 595; c.height = 842;
  g.fillStyle = "#1b1b20"; g.fillRect(0, 0, 595, 842);
  g.fillStyle = "#a1a1aa"; g.font = "600 22px system-ui, sans-serif"; g.textAlign = "center";
  g.fillText("This document can't be previewed", 297, 410);
  $("#previewImg").hidden = true; c.hidden = false;
}

/* For the browser-only mock: a sample A4 page with the watermark laid out
   the way layout_page() in watermark.py does it. Inside the app the preview
   is a real render from Python instead. Returns the stamp box, if any. */
function drawStandIn(text) {
  const c = $("#previewCanvas");
  const W = 595, H = 842, S = 2;
  c.width = W * S; c.height = H * S;
  const g = c.getContext("2d");
  g.scale(S, S);

  g.fillStyle = "#fff"; g.fillRect(0, 0, W, H);
  g.fillStyle = "#1f2937"; g.fillRect(56, 60, 210, 16);
  g.fillStyle = "#9ca3af"; g.fillRect(56, 86, 140, 8);
  g.fillStyle = "#e5e7eb";
  for (let y = 130; y < 380; y += 18) g.fillRect(56, y, y % 72 === 58 ? 300 : 483, 6);
  g.strokeStyle = "#d1d5db"; g.lineWidth = 1.5; g.strokeRect(56, 410, 483, 230);
  g.beginPath(); g.moveTo(56, 640); g.lineTo(220, 520); g.lineTo(330, 580); g.lineTo(539, 440); g.stroke();
  for (let y = 680; y < 790; y += 18) g.fillRect(56, y, 483, 6);

  const FONT = fs => `${state.italic ? "italic " : ""}${state.bold ? 700 : 400} ${fs}px ${cssFont(state.fontFamily)}`;
  const REF = 72, TILE_GAP = 3, FONT_H = 1.374, LEADING = 1.25, BANDS = 4;
  const mode = isStamp() ? "stamp" : state.layout === "tile" ? "tile" : "single";
  let lines = [text];
  if (state.stack && text.includes(" - ")) {
    const i = text.indexOf(" - ");
    lines = [text.slice(0, i), text.slice(i + 3)];
  }
  const deg = mode === "stamp" ? 0 : state.angle === "diagonal" ? Math.atan2(H, W) * 180 / Math.PI : Number(state.angle);
  const a = (deg * Math.PI) / 180, cos = Math.cos(a), sin = Math.sin(a);
  g.font = FONT(REF);
  const twRef = Math.max(...lines.map(l => g.measureText(l).width));
  const blockH = FONT_H + (lines.length - 1) * LEADING;
  const perpSpan = (() => {
    const px = sin, py = -cos, p = [0, W * px, H * py, W * px + H * py];
    return Math.max(...p) - Math.min(...p);
  })();
  let fs;
  if (mode === "stamp") {
    fs = Math.min(Math.min(W, H) * 0.02 * state.stampSize, REF * W * 0.9 / twRef);
  } else if (mode === "single") {
    const th = blockH * REF;
    const bw = Math.abs(twRef * cos) + Math.abs(th * sin);
    const bh = Math.abs(twRef * sin) + Math.abs(th * cos);
    fs = REF * Math.min((W * 0.9) / bw, (H * 0.9) / bh) * state.size;
  } else {
    fs = Math.min((perpSpan / BANDS) * 0.45 / blockH, REF * (Math.hypot(W, H) * 0.55) / twRef) * state.size;
  }

  g.font = FONT(fs);
  const color = COLORS[state.color] || state.color;
  g.fillStyle = color; g.strokeStyle = color; g.lineWidth = fs * 0.05;
  g.globalAlpha = state.opacity;
  g.textBaseline = "middle";
  const widths = lines.map(l => g.measureText(l).width), blockW = Math.max(...widths);
  const down = { x: sin, y: cos }, lead = LEADING * fs;
  const align = mode === "stamp" ? state.position[0] : 0.5;
  const drawBlock = (x, y) => lines.forEach((line, i) => {
    const off = (i - (lines.length - 1) / 2) * lead;
    const shift = (blockW - widths[i]) * (align - 0.5);
    g.save(); g.translate(x + off * down.x, y + off * down.y); g.rotate(-a);
    g.textAlign = "center";
    if (state.outline) g.strokeText(line, shift, 0); else g.fillText(line, shift, 0);
    g.restore();
  });

  let box = null;
  if (mode === "stamp") {
    const m = Math.min(W, H) * 0.035, bh = (lines.length - 1) * lead + fs;
    const left = m + state.position[0] * Math.max(W - 2 * m - blockW, 0);
    const top = m + state.position[1] * Math.max(H - 2 * m - bh, 0);
    drawBlock(left + blockW / 2, top + bh / 2);
    box = { x0: left / W, y0: top / H, x1: (left + blockW) / W, y1: (top + bh) / H, mx: m / W, my: m / H };
  } else if (mode === "single") {
    drawBlock(W / 2, H / 2);
  } else {
    // Bands across the page, copies along each band, alternate bands
    // shifted half a step like brickwork.
    const step = blockW + TILE_GAP * fs, spacing = perpSpan / BANDS;
    const reach = Math.hypot(W, H) + step;
    for (let i = 0; i < BANDS; i++) {
      const off = (i - (BANDS - 1) / 2) * spacing;
      const bx = W / 2 - off * down.x, by = H / 2 - off * down.y;
      const phase = (step / 2) * (i % 2);
      for (let t = phase - Math.ceil(reach / step) * step; t <= reach; t += step) drawBlock(bx + t * cos, by - t * sin);
    }
  }
  g.globalAlpha = 1;
  return box;
}

/* Drag handle: a dashed box over the stamp on the preview. Dragging it, or
   nudging it with the arrow keys, moves the stamp; the new spot is turned
   back into the same "fractions across and down" that the grid uses, so it
   lands in the same place on every page size. */

const pageEl = () => ($("#previewImg").hidden ? $("#previewCanvas") : $("#previewImg"));

function placeHandle() {
  const handle = $("#dragHandle");
  if (!isStamp() || !stampBox || state.step !== 1) { handle.hidden = true; return; }
  const page = pageEl().getBoundingClientRect(), stage = $("#previewStage").getBoundingClientRect();
  if (!page.width) { handle.hidden = true; return; }
  const pad = 4;
  handle.hidden = false;
  handle.style.left = page.left - stage.left + stampBox.x0 * page.width - pad + "px";
  handle.style.top = page.top - stage.top + stampBox.y0 * page.height - pad + "px";
  handle.style.width = (stampBox.x1 - stampBox.x0) * page.width + pad * 2 + "px";
  handle.style.height = (stampBox.y1 - stampBox.y0) * page.height + pad * 2 + "px";
  handle.setAttribute("aria-valuetext", positionName());
}

function boxToPosition(box) {
  const bw = box.x1 - box.x0, bh = box.y1 - box.y0;
  const spanX = 1 - 2 * box.mx - bw, spanY = 1 - 2 * box.my - bh;
  return [spanX > 0 ? (box.x0 - box.mx) / spanX : 0.5, spanY > 0 ? (box.y0 - box.my) / spanY : 0.5];
}

let drag = null;

$("#dragHandle").addEventListener("pointerdown", e => {
  if (!stampBox) return;
  e.preventDefault();
  const page = pageEl().getBoundingClientRect();
  drag = { x: e.clientX, y: e.clientY, box: { ...stampBox }, w: page.width, h: page.height };
  e.currentTarget.setPointerCapture(e.pointerId);
  e.currentTarget.classList.add("dragging");
  hideTip();
});
$("#dragHandle").addEventListener("pointermove", e => {
  if (!drag) return;
  const { box } = drag;
  const bw = box.x1 - box.x0, bh = box.y1 - box.y0;
  const x0 = clamp(box.x0 + (e.clientX - drag.x) / drag.w, box.mx, 1 - box.mx - bw);
  const y0 = clamp(box.y0 + (e.clientY - drag.y) / drag.h, box.my, 1 - box.my - bh);
  stampBox = { ...box, x0, y0, x1: x0 + bw, y1: y0 + bh };
  placeHandle();
});
const endDrag = e => {
  if (!drag) return;
  e.currentTarget.classList.remove("dragging");
  const moved = Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 2;
  drag = null;
  if (moved) {
    const [x, y] = boxToPosition(stampBox);
    // Snap to a grid spot when released close to one.
    const near = GRID.find(([gx, gy]) => Math.abs(gx - x) < 0.04 && Math.abs(gy - y) < 0.04);
    setPosition(...(near || [x, y]));
  }
};
$("#dragHandle").addEventListener("pointerup", endDrag);
$("#dragHandle").addEventListener("pointercancel", endDrag);
$("#dragHandle").addEventListener("keydown", e => {
  const step = e.shiftKey ? 0.1 : 0.02;
  const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
  if (!d) return;
  e.preventDefault();
  e.stopPropagation();
  setPosition(state.position[0] + d[0], state.position[1] + d[1]);
  $("#dragHandle").focus();
});
new ResizeObserver(placeHandle).observe($("#previewStage"));

/* ---- 3 · Export ---------------------------------------------------------- */

// Mirrors build_jobs() in batch_watermark.py.
function buildPlan() {
  const multi = state.recipients.length > 1;
  const outDir = state.out.mode === "custom" ? state.out.dir : null;
  return texts().map(text => {
    const slug = slugify(text);
    const files = [];
    for (const s of state.sources) {
      const parent = s.path.replace(/[\\/][^\\/]+$/, "");
      if (s.kind === "folder") {
        const root = `${outDir || parent}\\${s.name} _${slug}_Watermarked`;
        for (const rel of s.files) files.push({ root, rel: rel.replace(/\.pdf$/i, "_watermarked.pdf"), label: rel });
      } else {
        const stem = s.name.replace(/\.pdf$/i, "") + (multi ? ` _${slug}` : "");
        files.push({ root: outDir || parent, rel: stem + "_watermarked.pdf", label: s.name });
      }
    }
    return { text, files };
  });
}

// Recipient > output folder > subfolders > files, in the sidebar-tree style.
function renderTree(plan) {
  const node = () => ({ dirs: new Map(), files: [] });
  const insert = (tree, parts) => {
    let n = tree;
    for (const p of parts.slice(0, -1)) {
      if (!n.dirs.has(p)) n.dirs.set(p, node());
      n = n.dirs.get(p);
    }
    n.files.push(parts.at(-1));
  };
  const branch = (label, icon, body, depth, open = true, count = "") =>
    `<details class="tree-node" ${open ? "open" : ""} style="--d:${depth}">
       <summary class="tree-row">${icon}<span class="tree-label">${esc(label)}</span>${count ? `<span class="tree-count">${count}</span>` : ""}<span class="tree-chev">${ICON.chev}</span></summary>
       ${body}
     </details>`;
  const leaf = (label, depth) => `<div class="tree-row leaf" style="--d:${depth}">${ICON.file}<span class="tree-label">${esc(label)}</span></div>`;
  const walk = (n, depth) =>
    [...n.dirs].map(([name, child]) => branch(name, ICON.folder, walk(child, depth + 1), depth, depth < 2)).join("") +
    n.files.map(f => leaf(f, depth)).join("");

  return plan.map((g, gi) => {
    const roots = new Map();
    for (const f of g.files) {
      if (!roots.has(f.root)) roots.set(f.root, node());
      insert(roots.get(f.root), f.rel.split(/[\\/]/));
    }
    const body = [...roots].map(([root, tree]) =>
      branch(root.split(/[\\/]/).pop(), ICON.folder, walk(tree, 2), 1, true)).join("");
    return branch(state.recipients[gi], ICON.user, body, 0, gi === 0, g.files.length);
  }).join("");
}

let planToken = 0;

async function renderExport() {
  $("#statPdfs").textContent = pdfCount();
  $("#statRecipients").textContent = state.recipients.length;
  $("#statFiles").textContent = pdfCount() * state.recipients.length;
  $$(".choice[data-out]").forEach(b => b.classList.toggle("selected", b.dataset.out === state.out.mode));
  $("#outDirLabel").textContent = state.out.dir || "Choose a folder";
  const token = ++planToken;
  const plan = await api.plan(settings());
  if (token === planToken) $("#plan").innerHTML = `<div class="tree">${renderTree(plan)}</div>`;
}

let runFailures = [];

window.onRunStart = total => {
  runFailures = [];
  document.body.classList.add("busy");
  $("#exportReady").hidden = true;
  $("#exportDone").hidden = true;
  $("#exportRunning").hidden = false;
  $("#log").innerHTML = "";
  $("#progressFill").style.width = "0";
  $("#progressCount").textContent = `0/${total} completed`;
  $("#runTitle").dataset.total = total;
  $("#runTitle").textContent = "Creating your copies…";
  $(".card-foot").hidden = true;
};

window.onRunProgress = (done, label, text, ok, error) => {
  const total = Number($("#runTitle").dataset.total);
  $("#progressFill").style.width = (done / total) * 100 + "%";
  $("#progressCount").textContent = `${done}/${total} completed`;
  if (!ok) runFailures.push(state.recipients.length > 1 ? `${label} (${shown(text)}): ${error}` : `${label}: ${error}`);
  const li = document.createElement("li");
  li.innerHTML = `<span class="${ok ? "ok" : "err"}">${ok ? "✓" : "✕"}</span>${esc(label)}<span class="who">${esc(shown(text))}</span>`;
  const log = $("#log");
  log.prepend(li);
  while (log.children.length > 8) log.lastChild.remove();
};

window.onRunDone = (succeeded, failed, outputDir, cancelled) => {
  state.outputDir = outputDir;
  document.body.classList.remove("busy");
  $("#exportRunning").hidden = true;
  $("#exportDone").hidden = false;
  $("#doneTitle").textContent = cancelled ? "Stopped" : failed ? "Finished, with some problems" : "All done";
  $("#doneSub").textContent = `${plural(succeeded, "copy", "copies")} created${failed ? `, ${failed} could not be marked` : ""}. Your originals were not changed.`;
  $("#openOutput").hidden = !outputDir;
  const errBox = $("#doneErrors");
  errBox.hidden = !failed;
  errBox.innerHTML = runFailures.map(f => `<div>${esc(f)}</div>`).join("");
};
// addSources is a top-level function, so Python can already call it as
// window.addSources after a drop.

/* ---- Tooltips ------------------------------------------------------------ */

const keyLabel = k => ({ mod: IS_MAC ? "⌘" : "Ctrl", shift: IS_MAC ? "⇧" : "Shift", alt: IS_MAC ? "⌥" : "Alt",
  enter: IS_MAC ? "↩" : "Enter" }[k.toLowerCase()] || k);
const keyCaps = combo => combo.split("+").map(k => `<span class="kbd">${esc(keyLabel(k))}</span>`).join("");

let tipTimer;
function showTip(el) {
  const tip = $("#tooltip");
  tip.innerHTML = `<span>${esc(el.dataset.tip)}</span>${el.dataset.keys ? `<span class="keys">${keyCaps(el.dataset.keys)}</span>` : ""}`;
  tip.hidden = false;
  const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect();
  const below = r.top - t.height - 10 < 0;
  tip.style.left = clamp(r.left + r.width / 2 - t.width / 2, 8, innerWidth - t.width - 8) + "px";
  tip.style.top = (below ? r.bottom + 8 : r.top - t.height - 8) + "px";
}
function hideTip() { clearTimeout(tipTimer); $("#tooltip").hidden = true; }

document.addEventListener("mouseover", e => {
  const el = e.target.closest("[data-tip]");
  clearTimeout(tipTimer);
  if (!el || drag) { $("#tooltip").hidden = true; return; }
  tipTimer = setTimeout(() => showTip(el), 450);
});
document.addEventListener("mousedown", hideTip);
document.addEventListener("scroll", hideTip, true);

/* ---- Keyboard shortcuts -------------------------------------------------- */

const SHORTCUTS = [
  ["Anywhere", [
    ["mod+O", "Add PDFs"],
    ["mod+shift+O", "Add a folder"],
    ["mod+enter", "Continue, or start the export"],
    ["?", "Show this list"],
    ["Esc", "Close a menu or this list"],
  ]],
  ["Design", [
    ["mod+B", "Bold"],
    ["mod+I", "Italic"],
    ["mod+shift+L", "Outline letters"],
    ["mod+shift+F", "Choose a font"],
    ["D", "Diagonal across the page"],
    ["T", "Tiled all over"],
    ["S", "Stamp in one spot"],
    ["1-9", "Put the stamp in that spot (laid out like a number pad)"],
    ["←↑↓→", "Nudge the stamp (hold Shift for bigger steps)"],
    ["[", "Previous document in the preview"],
    ["]", "Next document in the preview"],
  ]],
];

function renderShortcuts() {
  $("#shortcutList").innerHTML = SHORTCUTS.map(([group, rows]) => `<h3>${esc(group)}</h3>` + rows.map(([keys, what]) =>
    `<div class="row-s"><span>${esc(what)}</span><span class="keys">${keys === "1-9" || keys === "←↑↓→" || keys === "Esc"
      ? `<span class="kbd">${esc(keys)}</span>` : keyCaps(keys)}</span></div>`).join("")).join("");
}

function showShortcuts(open) {
  $("#shortcutsModal").hidden = !open;
  if (open) { renderShortcuts(); $("#closeShortcuts").focus(); }
}

const typing = el => el && (el.matches("input[type=text], input:not([type]), textarea") || el.isContentEditable);

document.addEventListener("keydown", e => {
  if (document.body.classList.contains("busy")) return;
  const mod = IS_MAC ? e.metaKey : e.ctrlKey;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;

  if (e.key === "Escape") {
    if (!$("#shortcutsModal").hidden) { showShortcuts(false); return; }
    closePopovers(); hideTip();
    return;
  }
  if (!$("#shortcutsModal").hidden) return;

  if (mod && key === "o") { e.preventDefault(); (e.shiftKey ? $("#pickFolder") : $("#pickFiles")).click(); return; }
  if (mod && e.key === "Enter") { e.preventDefault(); if (!$("#next").disabled) $("#next").click(); return; }

  if (state.step === 1) {
    if (mod && !e.shiftKey && key === "b") { e.preventDefault(); toggleBold(); return; }
    if (mod && !e.shiftKey && key === "i") { e.preventDefault(); toggleItalic(); return; }
    if (mod && e.shiftKey && key === "l") { e.preventDefault(); toggleOutline(); return; }
    if (mod && e.shiftKey && key === "f") { e.preventDefault(); openFontMenu(true); return; }
  }
  if (mod || e.altKey || typing(document.activeElement)) return;
  if (!$("#fontMenu").hidden || !$("#formatMenu").hidden) return;

  if (e.key === "?") { e.preventDefault(); showShortcuts(true); return; }
  if (state.step !== 1) return;
  if (key === "d") setLayout("diagonal");
  else if (key === "t") setLayout("tile");
  else if (key === "s") setLayout("position");
  else if (GRID_KEYS.includes(e.key)) setPosition(...GRID[GRID_KEYS.indexOf(e.key)]);
  else if (e.key === "[" && !$("#prevPage").disabled) $("#prevPage").click();
  else if (e.key === "]" && !$("#nextPage").disabled) $("#nextPage").click();
  else if (isStamp() && e.key.startsWith("Arrow") && !e.target.closest(".segmented, .pos-grid, input[type=range]")) {
    const step = e.shiftKey ? 0.1 : 0.02;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    setPosition(state.position[0] + d[0], state.position[1] + d[1]);
  } else return;
  e.preventDefault();
});

/* ---- Wiring -------------------------------------------------------------- */

$("#steps").addEventListener("click", e => {
  const b = e.target.closest("button[data-step]");
  if (b) go(Number(b.dataset.step));
});
$("#back").addEventListener("click", () => go(state.step - 1));
$("#next").addEventListener("click", () => {
  if (state.step < STEP_COUNT - 1) return go(state.step + 1);
  api.run(settings());
});
$("#help").addEventListener("click", () => api.open_help());
$("#showShortcuts").addEventListener("click", () => showShortcuts(true));
$("#closeShortcuts").addEventListener("click", () => showShortcuts(false));
$("#shortcutsModal").addEventListener("mousedown", e => { if (e.target === e.currentTarget) showShortcuts(false); });

$("#pickFiles").addEventListener("click", async () => addSources(await api.pick_files() || []));
$("#pickFolder").addEventListener("click", async () => addSources(await api.pick_folder() || []));

const dz = $("#dropzone");
["dragenter", "dragover"].forEach(t => document.addEventListener(t, e => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach(t => document.addEventListener(t, e => {
  e.preventDefault();
  if (t === "dragleave" && e.relatedTarget) return;
  dz.classList.remove("over");
}));
document.addEventListener("drop", async e => {
  if (document.body.classList.contains("busy")) return;
  if (state.step !== 0) go(0);
  // Inside the app, Python receives the drop with the real paths (app.py
  // on_drop) and calls addSources itself. A plain browser only knows names.
  if (inApp()) return;
  const paths = [...(e.dataTransfer?.files || [])].map(f => f.name);
  if (paths.length) addSources(await api.inspect(paths));
});

$("#sources").addEventListener("click", e => {
  const b = e.target.closest("[data-remove-source]");
  if (b) { state.sources.splice(Number(b.dataset.removeSource), 1); render(); }
});

// Recipients
$("#recipientInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !(IS_MAC ? e.metaKey : e.ctrlKey)) { e.preventDefault(); addRecipient(); }
});
$("#addRecipient").addEventListener("click", e => { e.preventDefault(); addRecipient(); });
$("#recipients").addEventListener("click", e => {
  const save = e.target.closest("[data-save-recipient]");
  if (save) {
    const name = state.recipients[Number(save.dataset.saveRecipient)];
    state.companies = isSaved(name)
      ? state.companies.filter(c => c.toLowerCase() !== name.toLowerCase())
      : [...state.companies, name].sort((a, b) => a.localeCompare(b));
    saveCompanies(); hideTip(); render();
    return;
  }
  const remove = e.target.closest("[data-remove-recipient]");
  if (remove) { state.recipients.splice(Number(remove.dataset.removeRecipient), 1); render(); return; }
  const row = e.target.closest("[data-preview-recipient]");
  if (row) { state.previewRecipient = Number(row.dataset.previewRecipient); render(); }
});
$("#savedChips").addEventListener("click", e => {
  const forget = e.target.closest("[data-forget]");
  if (forget) {
    state.companies.splice(Number(forget.dataset.forget), 1);
    saveCompanies(); render();
    return;
  }
  const chip = e.target.closest("[data-company]");
  if (!chip) return;
  state.recipients.push(state.companies[Number(chip.dataset.company)]);
  state.previewRecipient = state.recipients.length - 1;
  render();
});
$("#savedChips").addEventListener("keydown", e => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-company]")) { e.preventDefault(); e.target.click(); }
});

// Mark text
$("#prefixText").addEventListener("input", e => { state.prefixText = e.target.value; render(); });
$("#prefixText").addEventListener("change", remember);
$("#prefixPresets").addEventListener("click", e => {
  const b = e.target.closest("[data-preset]");
  if (b) { state.prefixText = b.dataset.preset; remember(); render(); }
});
$("#dateToggle").addEventListener("change", e => { state.customDate = e.target.checked; remember(); render(); });
$("#formatTrigger").addEventListener("click", () => openFormatMenu($("#formatMenu").hidden));
$("#formatTrigger").addEventListener("keydown", e => {
  if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); openFormatMenu(true); }
});
$("#formatMenu").addEventListener("click", e => {
  const li = e.target.closest("li[data-value]");
  if (li) chooseFormat(li.dataset.value);
});
$("#formatMenu").addEventListener("mousemove", e => {
  const li = e.target.closest("li[data-value]");
  if (li && Number(li.dataset.index) !== menuIndex) { menuIndex = Number(li.dataset.index); renderFormat(); }
});
$("#formatMenu").addEventListener("keydown", e => {
  const count = formatOptions().length;
  if (e.key === "ArrowDown") menuIndex = (menuIndex + 1) % count;
  else if (e.key === "ArrowUp") menuIndex = (menuIndex + count - 1) % count;
  else if (e.key === "Home") menuIndex = 0;
  else if (e.key === "End") menuIndex = count - 1;
  else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); chooseFormat(formatOptions()[menuIndex].value); return; }
  else if (e.key === "Escape" || e.key === "Tab") { openFormatMenu(false); $("#formatTrigger").focus(); return; }
  else return;
  e.preventDefault();
  renderFormat();
});

// Text style
$("#fontTrigger").addEventListener("click", () => openFontMenu($("#fontMenu").hidden));
$("#fontSearch").addEventListener("input", () => { fontIndex = 0; renderFontList(); });
$("#fontSearch").addEventListener("keydown", e => {
  if (e.key === "ArrowDown") fontIndex = Math.min(fontIndex + 1, fontMatches.length - 1);
  else if (e.key === "ArrowUp") fontIndex = Math.max(fontIndex - 1, 0);
  else if (e.key === "Enter") { e.preventDefault(); if (fontMatches[fontIndex]) chooseFont(fontMatches[fontIndex].family); return; }
  else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); openFontMenu(false); $("#fontTrigger").focus(); return; }
  else return;
  e.preventDefault();
  renderFontList();
});
$("#fontList").addEventListener("click", e => {
  const li = e.target.closest("li[data-family]");
  if (li) chooseFont(li.dataset.family);
});
$("#fontList").addEventListener("mousemove", e => {
  const li = e.target.closest("li[data-family]");
  if (li && Number(li.dataset.index) !== fontIndex) {
    $$("#fontList li.active").forEach(x => x.classList.remove("active"));
    li.classList.add("active");
    fontIndex = Number(li.dataset.index);
  }
});
$("#boldBtn").addEventListener("click", toggleBold);
$("#italicBtn").addEventListener("click", toggleItalic);
$("#outlineBtn").addEventListener("click", toggleOutline);
$("#colorBtn").addEventListener("click", () => openColorPop($("#colorPop").hidden));
$("#swatches").addEventListener("click", e => {
  const b = e.target.closest("button"); if (b) { state.color = b.dataset.color; remember(); render(); openColorPop(false); }
});
$("#customColor").addEventListener("input", e => { state.color = e.target.value; render(); });
$("#customColor").addEventListener("change", remember);
$("#size").addEventListener("input", e => {
  state[isStamp() ? "stampSize" : "size"] = Number(e.target.value) / 100; render();
});
$("#opacity").addEventListener("input", e => { state.opacity = Number(e.target.value) / 100; render(); });
["#opacity", "#size"].forEach(sel => $(sel).addEventListener("change", remember));
document.addEventListener("mousedown", e => {
  if (!e.target.closest("#formatSelect")) openFormatMenu(false);
  if (!e.target.closest("#fontPicker")) openFontMenu(false);
  if (!e.target.closest("#colorPicker")) openColorPop(false);
});

// Placement
$("#layoutSeg").addEventListener("click", e => {
  const b = e.target.closest("button"); if (b) setLayout(b.dataset.layout);
});
$("#posGrid").addEventListener("click", e => {
  const b = e.target.closest("[data-cell]"); if (b) setPosition(...GRID[Number(b.dataset.cell)]);
});
$("#posGrid").addEventListener("keydown", e => {
  const cell = e.target.closest("[data-cell]"); if (!cell) return;
  const i = Number(cell.dataset.cell);
  const move = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3 }[e.key];
  if (move === undefined) return;
  e.preventDefault();
  e.stopPropagation();
  const next = clamp(i + move, 0, 8);
  setPosition(...GRID[next]);
  $(`#posGrid [data-cell="${next}"]`).focus();
});
$("#angleSeg").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.angle = b.dataset.angle === "diagonal" ? "diagonal" : Number(b.dataset.angle);
  remember(); render();
});
$("#stackToggle").addEventListener("change", e => { state.stack = e.target.checked; remember(); render(); });
$("#flattenToggle").addEventListener("change", e => { state.flatten = e.target.checked; remember(); });
$("#prevPage").addEventListener("click", () => { state.previewDoc--; render(); });
$("#nextPage").addEventListener("click", () => { state.previewDoc++; render(); });

// Export
$("#cancelRun").addEventListener("click", () => {
  api.cancel();
  $("#runTitle").textContent = "Stopping after the current file…";
});
$$(".choice[data-out]").forEach(b => b.addEventListener("click", async () => {
  if (b.dataset.out === "custom") {
    const dir = await api.pick_output();
    if (!dir) return;
    state.out = { mode: "custom", dir };
  } else {
    state.out = { mode: "beside", dir: state.out.dir };
  }
  render();
}));
$("#openOutput").addEventListener("click", () => api.open_path(state.outputDir));
$("#startOver").addEventListener("click", () => {
  state.sources = [];
  state.recipients = [];
  state.step = 0;
  $("#exportDone").hidden = true;
  $("#exportReady").hidden = false;
  $(".card-foot").hidden = false;
  render();
});

window.addEventListener("error", e => api.log_js_error(`${e.message} @ ${e.filename}:${e.lineno}`));
window.addEventListener("unhandledrejection", e => api.log_js_error(`promise: ${e.reason?.stack || e.reason}`));
window.addEventListener("resize", () => { placeSpot(); placeHandle(); });
api.load_companies().then(list => {
  state.companies = list;
  // ?demo=files|design|export opens the browser-only mock with sample data,
  // for screenshots and design work. It never runs inside the app.
  const demo = !inApp() && new URLSearchParams(location.search).get("demo");
  if (demo) {
    state.sources = [SAMPLE_FOLDER];
    state.recipients = ["Acme Construction", "Globex Engineering", "Initech"];
    state.companies = ["Acme Construction", "Initech", "Wayne Enterprises"];
    state.step = { files: 0, design: 1, export: 2 }[demo] ?? 1;
  }
  render();
});
