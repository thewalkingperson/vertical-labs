const transcript = document.querySelector("#transcript");
const thinking = document.querySelector("#thinking");
const thinkingWho = thinking.querySelector(".who");
const thinkingLabel = thinking.querySelector(".label");
const runList = document.querySelector("#run-list");
const fileList = document.querySelector("#file-list");
const statusEl = document.querySelector("#status");
const exploreToggle = document.querySelector("#explore-toggle");
const hintEl = document.querySelector("#hint");
const message = document.querySelector("#message");
const form = document.querySelector("#composer");
const reset = document.querySelector("#reset");
const viewerTitle = document.querySelector("#viewer-title");
const viewerMeta = document.querySelector("#viewer-meta");
const chips = document.querySelector("#chips");
const viewerBody = document.querySelector("#viewer-body");
const stage = document.querySelector("#stage");
const stageTitle = document.querySelector("#stage-title");
const flowCanvas = document.querySelector("#flow");
const flowNote = document.querySelector("#flow-note");
const forcesCanvas = document.querySelector("#forces");
const forcesTitle = document.querySelector("#forces-title");
const fieldP = document.querySelector("#field-p");
const fieldU = document.querySelector("#field-u");
const studyActions = document.querySelector("#study-actions");
const refineButton = document.querySelector("#refine");
const paraButton = document.querySelector("#paraview");
const deltaEl = document.querySelector("#delta");
const board = document.querySelector("#board");
const boardBody = document.querySelector("#board-body");
const boardNote = document.querySelector("#board-note");

let selectedRun = "";
let selectedFile = "";
let seenMessage = -1;
let fileBytes = -1;
let stick = true;
let optimistic = false;
let exploreLock = false;
let picture = null;
let pictureField = "p";
let pictureAt = 0;
let pictureRun = "";
let phaseNow = "idle";
let runsById = {};
let boardStamp = "";
let wasBusy = false;
let followedRun = "";

transcript.addEventListener("scroll", () => {
  const near = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40;
  stick = near;
});

function roleName(role) {
  if (role === "you") return "You";
  if (role === "meta") return "Meta";
  if (role === "steer") return "Steer";
  if (role === "outer") return "Outer";
  if (role === "result") return "Result";
  if (role === "engineer") return "Engineer";
  return "System";
}

function setStatus(text, phase) {
  statusEl.textContent = text;
  statusEl.dataset.phase = phase || "idle";
}

function showThinking(on, label, who) {
  thinking.hidden = !on;
  thinkingWho.textContent = who || "Meta";
  thinkingLabel.textContent = label || "Reading the request.";
  if (on && stick) transcript.scrollTop = transcript.scrollHeight;
}

function gateClass(gate) {
  if (gate === "screening" || gate === "design" || gate === "running") return "gate-ok";
  if (!gate) return "";
  return "gate-bad";
}

function renderMessages(rows) {
  const last = rows.length ? rows[rows.length - 1].id : 0;
  if (last === seenMessage && transcript.childElementCount === rows.length) return;
  seenMessage = last;
  transcript.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No messages yet. Ask for a NACA section, or describe the wing you want.";
    transcript.appendChild(empty);
    return;
  }
  for (const row of rows) {
    const item = document.createElement("article");
    item.className = "bubble " + (row.role || "system");
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = roleName(row.role);
    const body = document.createElement("p");
    body.textContent = row.text || "";
    item.append(who, body);
    transcript.appendChild(item);
  }
  if (stick) transcript.scrollTop = transcript.scrollHeight;
}

function renderRuns(runs) {
  runList.replaceChildren();
  if (!runs.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No OpenFOAM runs in this store yet.";
    runList.appendChild(empty);
    return;
  }
  for (const run of runs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "run" + (run.id === selectedRun ? " active" : "");
    const title = document.createElement("strong");
    const wing = run.naca ? "NACA " + run.naca : run.id;
    const angle = run.alpha_deg === "" || run.alpha_deg === undefined ? "" : " at " + run.alpha_deg + "°";
    title.textContent = wing + angle;
    const detail = document.createElement("span");
    const coeff = run.cl === null || run.cl === undefined
      ? ""
      : "  Cl " + Number(run.cl).toFixed(3) + "  Cd " + Number(run.cd).toFixed(3);
    detail.textContent = (run.gate || "open") + coeff;
    detail.className = gateClass(run.gate);
    button.append(title, detail);
    button.addEventListener("click", () => selectRun(run.id));
    runList.appendChild(button);
  }
}

function paint(value, lo, hi) {
  const span = hi - lo || 1;
  const t = Math.min(1, Math.max(0, (value - lo) / span));
  const stops = [
    [37, 78, 143],
    [90, 164, 184],
    [232, 214, 122],
    [176, 58, 46],
  ];
  const scaled = t * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const mix = scaled - index;
  const color = stops[index].map((channel, offset) => Math.round(channel + (stops[index + 1][offset] - channel) * mix));
  return "rgb(" + color.join(",") + ")";
}

function fitCanvas(canvas, height) {
  const width = Math.max(320, canvas.clientWidth || canvas.parentElement.clientWidth || 640);
  const ratio = window.devicePixelRatio || 1;
  canvas.style.height = height + "px";
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function drawFlow() {
  if (!picture || !picture.quads || !picture.quads.length) {
    flowCanvas.hidden = true;
    return;
  }
  flowCanvas.hidden = false;
  const { ctx, width, height } = fitCanvas(flowCanvas, 340);
  ctx.fillStyle = "#0e141b";
  ctx.fillRect(0, 0, width, height);
  const view = picture.view || [0, 0, 1, 1];
  const pad = 18;
  const scale = Math.min((width - pad * 2) / (view[2] - view[0] || 1), (height - pad * 2) / (view[3] - view[1] || 1));
  const xOf = (x) => pad + (x - view[0]) * scale;
  const yOf = (y) => height - pad - (y - view[1]) * scale;
  const index = pictureField === "U" ? 9 : 8;
  let lo = Infinity;
  let hi = -Infinity;
  for (const quad of picture.quads) {
    lo = Math.min(lo, quad[index]);
    hi = Math.max(hi, quad[index]);
  }
  for (const quad of picture.quads) {
    ctx.beginPath();
    ctx.moveTo(xOf(quad[0]), yOf(quad[1]));
    ctx.lineTo(xOf(quad[2]), yOf(quad[3]));
    ctx.lineTo(xOf(quad[4]), yOf(quad[5]));
    ctx.lineTo(xOf(quad[6]), yOf(quad[7]));
    ctx.closePath();
    ctx.fillStyle = paint(quad[index], lo, hi);
    ctx.fill();
  }
  ctx.strokeStyle = "#f4f8fb";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (const edge of picture.airfoil || []) {
    ctx.moveTo(xOf(edge[0]), yOf(edge[1]));
    ctx.lineTo(xOf(edge[2]), yOf(edge[3]));
  }
  ctx.stroke();
  drawWind(ctx, xOf, yOf, view);
  const label = pictureField === "U" ? "speed" : "pressure";
  ctx.fillStyle = "#d5dde4";
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillText(label + "  " + lo.toFixed(2) + "  to  " + hi.toFixed(2), pad, height - 8);
}

function drawWind(ctx, xOf, yOf, view) {
  const alpha = Number(picture.alpha_deg);
  if (!Number.isFinite(alpha)) return;
  const rad = (alpha * Math.PI) / 180;
  const width = view[2] - view[0] || 1;
  const height = view[3] - view[1] || 1;
  const x0 = view[0] + 0.08 * width;
  const y0 = view[1] + 0.78 * height;
  const length = 0.28 * width;
  const x1 = x0 + length * Math.cos(rad);
  const y1 = y0 + length * Math.sin(rad);
  ctx.strokeStyle = "#f4f8fb";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(xOf(x0), yOf(y0));
  ctx.lineTo(xOf(x1), yOf(y1));
  ctx.stroke();
  const sx0 = xOf(x0);
  const sy0 = yOf(y0);
  const sx1 = xOf(x1);
  const sy1 = yOf(y1);
  const ang = Math.atan2(sy1 - sy0, sx1 - sx0);
  const head = Math.max(8, 0.04 * (xOf(view[2]) - xOf(view[0])));
  ctx.beginPath();
  ctx.moveTo(sx1, sy1);
  ctx.lineTo(sx1 - head * Math.cos(ang - 0.45), sy1 - head * Math.sin(ang - 0.45));
  ctx.lineTo(sx1 - head * Math.cos(ang + 0.45), sy1 - head * Math.sin(ang + 0.45));
  ctx.closePath();
  ctx.fillStyle = "#f4f8fb";
  ctx.fill();
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillText(alpha + "° wind", sx0, sy0 - 8);
}

function drawForces() {
  const series = picture && picture.forces;
  const times = series && series.time ? series.time : [];
  if (!times.length) {
    forcesCanvas.hidden = true;
    forcesTitle.hidden = true;
    return;
  }
  forcesCanvas.hidden = false;
  forcesTitle.hidden = false;
  const { ctx, width, height } = fitCanvas(forcesCanvas, 168);
  if (times.length === 1) {
    ctx.fillStyle = "#0e141b";
    ctx.fillRect(0, 0, width, height);
    ctx.font = "20px sans-serif";
    ctx.fillStyle = "#8ecae6";
    ctx.fillText("Cl  " + series.cl[0].toFixed(4), 24, 70);
    ctx.fillStyle = "#e9c46a";
    ctx.fillText("Cd  " + series.cd[0].toFixed(4), 24, 104);
    ctx.fillStyle = "#d5dde4";
    ctx.font = "12px sans-serif";
    ctx.fillText("One sample at time " + times[0], 24, 136);
    return;
  }
  ctx.fillStyle = "#0e141b";
  ctx.fillRect(0, 0, width, height);
  const pad = { left: 46, right: 12, top: 16, bottom: 26 };
  const values = series.cl.concat(series.cd);
  let lo = Math.min.apply(null, values);
  let hi = Math.max.apply(null, values);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const x0 = times[0];
  const x1 = times[times.length - 1];
  const xOf = (x) => pad.left + ((x - x0) / (x1 - x0 || 1)) * (width - pad.left - pad.right);
  const yOf = (y) => pad.top + ((hi - y) / (hi - lo)) * (height - pad.top - pad.bottom);
  ctx.strokeStyle = "#31414f";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, height - pad.bottom);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();
  function line(rows, color) {
    ctx.beginPath();
    rows.forEach((value, index) => {
      const x = xOf(times[index]);
      const y = yOf(value);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.stroke();
  }
  line(series.cl, "#8ecae6");
  line(series.cd, "#e9c46a");
  ctx.fillStyle = "#d5dde4";
  ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillText("Cl", width - 78, 18);
  ctx.fillStyle = "#8ecae6";
  ctx.fillRect(width - 96, 9, 14, 3);
  ctx.fillStyle = "#e9c46a";
  ctx.fillRect(width - 52, 9, 14, 3);
  ctx.fillStyle = "#d5dde4";
  ctx.fillText("Cd", width - 34, 18);
  ctx.fillText(hi.toFixed(2), 4, pad.top + 4);
  ctx.fillText(lo.toFixed(2), 4, height - pad.bottom);
  ctx.fillText("time", width - 46, height - 8);
}

function coeff(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toFixed(4);
}

function sectionLabel(row) {
  const code = row.naca ? "NACA " + row.naca : "section";
  const angle = row.alpha_deg === "" || row.alpha_deg === undefined || row.alpha_deg === null
    ? ""
    : " at " + row.alpha_deg + "°";
  return code + angle;
}

function renderBoard(payload) {
  const rows = payload && payload.rows ? payload.rows : [];
  if (!payload || !payload.ready || !rows.length) {
    board.hidden = true;
    boardBody.replaceChildren();
    return;
  }
  board.hidden = false;
  boardNote.textContent = payload.sentence || "";
  boardBody.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (row.best) tr.className = "best";
    const section = document.createElement("td");
    section.textContent = sectionLabel(row);
    if (row.best || row.direct) {
      const mark = document.createElement("span");
      mark.className = "mark";
      mark.textContent = row.best ? "best neighbor" : "direct";
      section.appendChild(mark);
    }
    const cl = document.createElement("td");
    cl.textContent = coeff(row.cl);
    const cd = document.createElement("td");
    cd.textContent = coeff(row.cd);
    const gate = document.createElement("td");
    gate.textContent = row.gate || "—";
    gate.className = gateClass(row.gate);
    tr.append(section, cl, cd, gate);
    boardBody.appendChild(tr);
  }
}

async function loadBoard(force) {
  if (!selectedRun) {
    board.hidden = true;
    boardStamp = "";
    return;
  }
  if (!force && boardStamp === selectedRun) return;
  const requested = selectedRun;
  boardStamp = requested;
  try {
    const payload = await fetch("/api/runs/" + encodeURIComponent(requested) + "/board").then((response) => response.json());
    if (requested !== selectedRun) return;
    renderBoard(payload);
  } catch (_error) {
    if (requested === selectedRun) board.hidden = true;
  }
}

function syncStudy() {
  const run = runsById[selectedRun];
  if (!selectedRun) {
    studyActions.hidden = true;
    deltaEl.hidden = true;
    return;
  }
  studyActions.hidden = false;
  const busy = phaseNow === "running" || phaseNow === "asking" || optimistic;
  refineButton.disabled = busy || !run || !run.worth_finer;
  paraButton.disabled = !selectedRun;
  const delta = run && run.delta;
  if (!delta) {
    deltaEl.hidden = true;
    deltaEl.textContent = "";
    return;
  }
  deltaEl.hidden = false;
  deltaEl.textContent = "dCl " + coeff(delta.delta_cl)
    + "  dCd " + coeff(delta.delta_cd)
    + ". " + (delta.sentence || "");
}

function showPicture() {
  if (!picture || !picture.ready) {
    stage.hidden = true;
    return;
  }
  stage.hidden = false;
  stageTitle.textContent = picture.title || "Flow";
  const cells = picture.cells ? picture.cells + " cells. " : "";
  flowNote.textContent = cells + (picture.note || "");
  fieldP.classList.toggle("active", pictureField === "p");
  fieldU.classList.toggle("active", pictureField === "U");
  drawFlow();
  drawForces();
}

async function loadPicture(force) {
  if (!selectedRun) {
    picture = null;
    stage.hidden = true;
    return;
  }
  const now = Date.now();
  if (!force && pictureRun === selectedRun && now - pictureAt < 4000) return;
  pictureRun = selectedRun;
  pictureAt = now;
  try {
    const next = await fetch("/api/runs/" + encodeURIComponent(selectedRun) + "/picture").then((response) => response.json());
    if (pictureRun !== selectedRun) return;
    picture = next;
    if (!selectedFile) viewerTitle.textContent = next.title || selectedRun;
    showPicture();
  } catch (_error) {
    flowNote.textContent = "The picture did not load.";
  }
}

function focusRun(id) {
  if (selectedRun === id) return;
  selectedRun = id;
  selectedFile = "";
  fileBytes = -1;
  picture = null;
  pictureAt = 0;
  pictureRun = "";
  boardStamp = "";
  viewerTitle.textContent = id;
  viewerMeta.textContent = "";
  chips.replaceChildren();
  viewerBody.textContent = "Pick a file below the picture for the raw text.";
  viewerBody.classList.add("is-placeholder");
  document.querySelector("#raw-title").hidden = true;
}

async function selectRun(id) {
  followedRun = id;
  focusRun(id);
  viewerTitle.textContent = id;
  await loadFiles();
  await loadPicture(true);
  await loadBoard(true);
  syncStudy();
}

async function loadFiles() {
  if (!selectedRun) {
    fileList.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Select a run.";
    fileList.appendChild(empty);
    return;
  }
  const payload = await fetch("/api/runs/" + encodeURIComponent(selectedRun)).then((response) => response.json());
  const files = payload.files || [];
  fileList.replaceChildren();
  if (!files.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "This run has no case files yet.";
    fileList.appendChild(empty);
    return;
  }
  for (const file of files) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "file" + (file.path === selectedFile ? " active" : "");
    const title = document.createElement("strong");
    title.textContent = file.path;
    const detail = document.createElement("span");
    detail.textContent = file.kind + "  " + file.bytes + " B";
    button.append(title, detail);
    button.addEventListener("click", () => selectFile(file.path));
    fileList.appendChild(button);
  }
}

async function selectFile(path) {
  selectedFile = path;
  fileBytes = -1;
  await loadFile();
  await loadFiles();
}

async function loadFile() {
  if (!selectedRun || !selectedFile) return;
  const query = "/api/runs/" + encodeURIComponent(selectedRun) + "/file?path=" + encodeURIComponent(selectedFile);
  const view = await fetch(query).then((response) => response.json());
  if (view.bytes === fileBytes && viewerTitle.textContent === view.path) return;
  fileBytes = view.bytes;
  viewerTitle.textContent = view.path || selectedFile;
  viewerMeta.textContent = (view.kind || "") + (view.truncated ? "  truncated to the end" : "") + "  " + (view.bytes || 0) + " B";
  chips.replaceChildren();
  for (const chip of view.highlights || []) {
    const item = document.createElement("div");
    item.className = "chip";
    const label = document.createElement("b");
    label.textContent = chip.label;
    const value = document.createElement("span");
    value.textContent = chip.value;
    item.append(label, value);
    chips.appendChild(item);
  }
  viewerBody.textContent = view.text || "";
  viewerBody.classList.remove("is-placeholder");
  document.querySelector("#raw-title").hidden = false;
}

async function tick() {
  let state;
  try {
    state = await fetch("/api/state").then((response) => response.json());
  } catch (_error) {
    setStatus("Offline", "offline");
    showThinking(false);
    return;
  }
  const phase = state.phase || "idle";
  const activity = state.activity || phase;
  phaseNow = phase;
  const busy = phase === "running" || phase === "asking" || optimistic;
  const exploring = activity === "exploring";
  const detail = state.detail || (busy ? "Reading the request. If the section or the angle is missing, the next line asks for it before OpenFOAM starts." : "");
  const shownPhase = exploring ? "exploring" : (busy ? phase : "idle");
  setStatus(state.status || (phase === "idle" && !exploring ? "Idle" : "Working"), shownPhase);
  showThinking(busy, detail, state.who || (exploring ? "Outer" : "Meta"));
  if (!exploreLock && typeof state.explore === "boolean") {
    exploreToggle.checked = state.explore;
  }
  hintEl.textContent = state.hint || "";
  message.placeholder = phase === "running"
    ? "Steer the agent. This reaches the next tool turn."
    : phase === "asking"
      ? "Answer the question above"
      : "NACA 2412 at 6 degrees";
  const runs = state.runs || [];
  if (selectedRun && !runs.some((run) => run.id === selectedRun)) {
    selectedRun = "";
    selectedFile = "";
    fileBytes = -1;
    viewerTitle.textContent = "Select a file";
    viewerMeta.textContent = "";
    chips.replaceChildren();
    viewerBody.textContent = "Select a run to see the mesh, the pressure, and the force history.";
    viewerBody.classList.add("is-placeholder");
    document.querySelector("#raw-title").hidden = true;
    stage.hidden = true;
    picture = null;
    pictureRun = "";
    board.hidden = true;
    boardStamp = "";
    fileList.replaceChildren();
  }
  if (state.active_run && activity !== "exploring" && state.active_run !== followedRun) {
    followedRun = state.active_run;
    focusRun(state.active_run);
  }
  if (!selectedRun && phase === "idle" && runs.length) selectedRun = runs[0].id;
  const busyNow = phase === "running" || activity === "exploring";
  if (wasBusy && !busyNow) boardStamp = "";
  wasBusy = busyNow;
  if (activity === "exploring") boardStamp = "";
  renderMessages(state.messages || []);
  renderRuns(runs);
  runsById = {};
  for (const run of runs) runsById[run.id] = run;
  phaseNow = phase;
  syncStudy();
  if (selectedRun && phase === "idle" && pictureRun !== selectedRun) {
    await loadFiles();
    await loadPicture(true);
  }
  if (selectedRun && phase === "idle") await loadBoard(false);
  if (selectedRun && phase === "running") {
    await loadFiles();
    if (selectedFile) await loadFile();
    await loadPicture(false);
  }
}

fieldP.addEventListener("click", () => {
  pictureField = "p";
  showPicture();
});

fieldU.addEventListener("click", () => {
  pictureField = "U";
  showPicture();
});

window.addEventListener("resize", () => {
  if (picture && picture.ready) showPicture();
});

exploreToggle.addEventListener("change", async () => {
  exploreLock = true;
  try {
    await fetch("/api/explore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: exploreToggle.checked }),
    });
  } finally {
    exploreLock = false;
    await tick();
  }
});

paraButton.addEventListener("click", async () => {
  if (!selectedRun || paraButton.disabled) return;
  paraButton.disabled = true;
  try {
    const response = await fetch("/api/paraview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: selectedRun }),
    });
    const payload = await response.json();
    if (!payload.ok) setStatus("paraFoam did not open", "error");
  } finally {
    paraButton.disabled = !selectedRun;
  }
});

refineButton.addEventListener("click", async () => {
  if (!selectedRun || refineButton.disabled) return;
  optimistic = true;
  phaseNow = "running";
  refineButton.disabled = true;
  setStatus("Running the finer mesh", "running");
  showThinking(true, "Running the finer mesh on this same wing. The coarse result stays, and this pass uses more cells.", "Inner");
  try {
    await fetch("/api/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: selectedRun }),
    });
  } finally {
    optimistic = false;
    await tick();
  }
});

reset.addEventListener("click", async () => {
  message.value = "";
  seenMessage = -1;
  await fetch("/api/reset", { method: "POST" });
  await tick();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = message.value.trim();
  if (!text) return;
  message.value = "";
  optimistic = true;
  setStatus("Reading the request", "running");
  showThinking(true, "Reading the request. If the section or the angle is missing, the next line asks for it before OpenFOAM starts.", "Meta");
  try {
    await fetch("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } finally {
    optimistic = false;
    await tick();
  }
});

message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

loadFiles();
tick();
setInterval(tick, 1000);
