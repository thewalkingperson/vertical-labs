const transcript = document.querySelector("#transcript");
const runList = document.querySelector("#run-list");
const fileList = document.querySelector("#file-list");
const statusEl = document.querySelector("#status");
const hintEl = document.querySelector("#hint");
const message = document.querySelector("#message");
const form = document.querySelector("#composer");
const reset = document.querySelector("#reset");
const viewerTitle = document.querySelector("#viewer-title");
const viewerMeta = document.querySelector("#viewer-meta");
const chips = document.querySelector("#chips");
const viewerBody = document.querySelector("#viewer-body");

let selectedRun = "";
let selectedFile = "";
let seenMessage = 0;
let fileBytes = -1;
let stick = true;

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
  return "System";
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

async function selectRun(id) {
  selectedRun = id;
  selectedFile = "";
  fileBytes = -1;
  viewerTitle.textContent = id;
  viewerMeta.textContent = "";
  chips.replaceChildren();
  viewerBody.textContent = "Choose a file to see its specs or results.";
  await loadFiles();
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
}

async function tick() {
  let state;
  try {
    state = await fetch("/api/state").then((response) => response.json());
  } catch (_error) {
    statusEl.textContent = "Offline";
    return;
  }
  const phase = state.phase || "idle";
  const active = state.active_run ? "  " + state.active_run : "";
  statusEl.textContent = phase === "asking" ? "Waiting for you" : phase === "running" ? "Running" + active : "Idle";
  hintEl.textContent = state.hint || "";
  message.placeholder = phase === "running"
    ? "Steer the agent. This reaches the next tool turn."
    : phase === "asking"
      ? "Answer the question above"
      : "NACA 2412 at 6 degrees, or a wing with low drag at 10 degrees";
  const runs = state.runs || [];
  if (selectedRun && !runs.some((run) => run.id === selectedRun)) {
    selectedRun = "";
    selectedFile = "";
    fileBytes = -1;
    viewerTitle.textContent = "Select a file";
    viewerMeta.textContent = "";
    chips.replaceChildren();
    viewerBody.textContent = "Specs, mesh checks, solver logs, and force coefficients open here.";
    fileList.replaceChildren();
  }
  renderMessages(state.messages || []);
  renderRuns(runs);
  if (selectedRun && phase === "running") {
    await loadFiles();
    if (selectedFile) await loadFile();
  }
}

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
  await fetch("/api/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  await tick();
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
