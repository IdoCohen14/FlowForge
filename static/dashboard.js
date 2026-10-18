"use strict";

// All server-provided labels are inserted as text, never HTML.
const byId = (id) => document.getElementById(id);
const statusLabels = {pending: "Queued", running: "Processing", completed: "Completed", failed: "Failed"};
const numberFormat = new Intl.NumberFormat();
const state = {
  page: 1, pageSize: 6, query: "", status: "", selected: null,
  selectedJob: null, selectedFile: null, result: null, resultKey: null,
  refreshing: false, refreshAgain: false, uploading: false, jobsSignature: "",
};

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function notify(message, error = false) {
  const notice = byId("notice");
  notice.textContent = message;
  notice.className = error ? "notice error" : "notice";
  notice.hidden = !message;
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, {...options, signal: controller.signal});
    const body = await response.json();
    if (!response.ok) {
      throw new Error(typeof body.detail === "string" ? body.detail : "The request could not be completed.");
    }
    return body;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The server took too long to respond. Please refresh and check the job before uploading again.");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function setConnection(online) {
  byId("connection").classList.toggle("offline", !online);
  byId("connection-text").textContent = online ? "Live · updates every 3s" : "Connection interrupted";
}

function setBusy(busy) {
  state.uploading = busy;
  byId("upload-button").disabled = busy;
  byId("sample-button").disabled = busy;
  byId("upload-button").textContent = busy ? "Saving your file…" : "Create analysis ↗";
}

function chooseFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".csv")) {
    notify("Choose a file with a .csv extension.", true);
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    notify("This file exceeds the 10 MiB limit. Choose a smaller CSV.", true);
    return;
  }
  state.selectedFile = file;
  byId("file-title").textContent = file.name;
  byId("file-description").textContent = `${numberFormat.format(file.size)} bytes · ready to analyze`;
  notify("");
}

async function upload(file, name) {
  if (state.uploading) return;
  if (!file) {
    notify("Choose a CSV file first, or try the sample data.", true);
    byId("file").focus();
    return;
  }
  setBusy(true);
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    if (name.trim()) form.append("name", name.trim());
    const job = await request("/jobs/csv", {method: "POST", body: form});
    state.page = 1;
    state.query = "";
    state.status = "";
    byId("search").value = "";
    byId("status-filter").value = "";
    selectJob({...job, job_type: "csv", attempt_count: 0}, false);
    notify("File saved. Your analysis is queued and will update here automatically.");
    await refresh();
  } catch (error) {
    notify(error.message, true);
  } finally {
    setBusy(false);
  }
}

function submittedLabel(timestamp) {
  if (!timestamp) return "—";
  const date = new Date(timestamp);
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "Just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
  return date.toLocaleDateString(undefined, {month: "short", day: "numeric"});
}

function selectJob(job, scroll = true) {
  state.selected = job.id;
  state.selectedJob = job;
  state.resultKey = null;
  state.result = null;
  state.jobsSignature = "";
  byId("export-button").disabled = true;
  byId("analysis-title").textContent = job.name;
  byId("analysis-subtitle").textContent = job.job_type === "csv" ? "Your dataset, a little better understood." : "Background demonstration job";
  byId("analysis-placeholder").hidden = true;
  byId("analysis-content").hidden = false;
  byId("analysis-completed").hidden = true;
  byId("analysis-waiting").hidden = false;
  byId("waiting-title").textContent = "Loading job details…";
  byId("waiting-description").textContent = "";
  showJobMetadata(job);
  if (scroll) byId("analysis-panel").scrollIntoView({behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start"});
  refresh();
}

function showJobMetadata(job) {
  byId("analysis-status").textContent = statusLabels[job.status] || job.status;
  byId("analysis-status").className = `badge ${job.status}`;
  byId("analysis-id").textContent = `Job ${job.id.slice(0, 8)}`;
  byId("analysis-id").title = job.id;
  byId("analysis-attempts").textContent = `${job.attempt_count ?? 0} processing attempt${job.attempt_count === 1 ? "" : "s"}`;
}

function renderJobs(data) {
  const counts = data.counts;
  byId("count-total").textContent = numberFormat.format(Object.values(counts).reduce((sum, value) => sum + value, 0));
  byId("count-active").textContent = numberFormat.format(counts.pending + counts.running);
  byId("count-completed").textContent = numberFormat.format(counts.completed);
  byId("count-failed").textContent = numberFormat.format(counts.failed);
  const start = data.total ? (state.page - 1) * state.pageSize + 1 : 0;
  byId("page-description").textContent = `${start}–${Math.min(state.page * state.pageSize, data.total)} of ${numberFormat.format(data.total)} jobs`;
  byId("previous-page").disabled = state.page <= 1;
  byId("next-page").disabled = state.page * state.pageSize >= data.total;
  byId("jobs-empty").hidden = data.items.length > 0;
  byId("jobs-empty").querySelector("strong").textContent = state.query || state.status ? "No matching jobs" : "No jobs here yet";
  byId("jobs-empty").querySelector("p").textContent = state.query || state.status ? "Try another name or a different status filter." : "Upload a CSV or try the sample to get started.";

  // Preserve focused controls when a polling response has not changed.
  const signature = JSON.stringify([data.items, state.selected]);
  if (signature === state.jobsSignature) return;
  state.jobsSignature = signature;
  const fragment = document.createDocumentFragment();
  for (const job of data.items) {
    const row = element("tr");
    if (job.id === state.selected) row.className = "selected";
    const nameCell = element("td");
    const name = element("button", job.name || "Untitled analysis", "job-name");
    name.title = job.name;
    name.onclick = () => selectJob(job);
    nameCell.append(name, element("span", job.job_type === "csv" ? "CSV analysis" : "Demo job", "job-type"));
    const status = element("td");
    status.append(element("span", statusLabels[job.status] || job.status, `badge ${job.status}`));
    const submitted = element("td", submittedLabel(job.created_at), "submitted-column");
    if (job.created_at) submitted.title = new Date(job.created_at).toLocaleString();
    const action = element("td");
    const button = element("button", "↗", "row-arrow");
    button.setAttribute("aria-label", `Open ${job.name || "untitled job"}`);
    button.onclick = () => selectJob(job);
    action.append(button);
    row.append(nameCell, status, submitted, action);
    fragment.append(row);
  }
  byId("jobs-body").replaceChildren(fragment);
}

function setView(view) {
  for (const tab of document.querySelectorAll("[data-view]")) {
    const active = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    byId(`view-${tab.dataset.view}`).hidden = !active;
  }
}

function renderResults(result) {
  const data = result.results;
  state.result = result;
  byId("analysis-waiting").hidden = true;
  byId("analysis-completed").hidden = false;
  byId("export-button").disabled = false;
  byId("result-rows").textContent = numberFormat.format(data.row_count);
  byId("result-columns").textContent = numberFormat.format(data.column_count);
  byId("result-missing").textContent = numberFormat.format(data.missing_values_total);
  const cells = data.row_count * data.column_count;
  byId("result-completeness").textContent = cells ? `${(100 * (1 - data.missing_values_total / cells)).toFixed(1)}%` : "—";
  const rows = document.createDocumentFragment();
  const bars = document.createDocumentFragment();
  for (const column of data.columns) {
    const row = element("tr");
    row.append(element("td", column.name));
    const type = element("td");
    type.append(element("span", column.numeric ? "Numeric" : "Non-numeric", "type-label"));
    row.append(type, element("td", numberFormat.format(column.missing_values)));
    for (const key of ["minimum", "maximum", "mean"]) {
      row.append(element("td", column.numeric?.[key] ?? "—"));
    }
    rows.append(row);
    const bar = element("div");
    const label = element("div", undefined, "quality-label");
    const present = data.row_count - column.missing_values;
    const percentage = data.row_count ? present / data.row_count * 100 : 0;
    label.append(element("strong", column.name), element("span", data.row_count ? `${percentage.toFixed(1)}% present` : "No rows"));
    const track = element("div", undefined, "quality-track");
    const fill = element("div", undefined, "quality-fill");
    fill.style.width = `${percentage}%`;
    track.append(fill);
    bar.append(label, track);
    bars.append(bar);
  }
  byId("statistics-body").replaceChildren(rows);
  byId("quality-bars").replaceChildren(bars);
  byId("json-results").textContent = JSON.stringify(result, null, 2);
}

async function refreshSelection() {
  if (!state.selectedJob) return;
  const job = state.selectedJob;
  showJobMetadata(job);
  if (job.job_type !== "csv") {
    byId("analysis-waiting").hidden = false;
    byId("waiting-title").textContent = `${statusLabels[job.status] || job.status} demo job`;
    byId("waiting-description").textContent = "Demonstration jobs exercise background processing. Upload a CSV for dataset statistics.";
    return;
  }
  const key = `${job.id}:${job.status}`;
  if (key === state.resultKey && state.result) return;
  const result = await request(`/jobs/${job.id}/analysis`);
  if (state.selected !== job.id) return;
  byId("analysis-status").textContent = statusLabels[result.status] || result.status;
  byId("analysis-status").className = `badge ${result.status}`;
  if (result.status === "completed" && result.results) {
    renderResults(result);
    state.resultKey = key;
  } else {
    byId("analysis-completed").hidden = true;
    byId("analysis-waiting").hidden = false;
    byId("export-button").disabled = true;
    const messages = {
      pending: ["In the queue", "Your file is saved and waiting for a worker. This view updates automatically."],
      running: ["Finding the details in your data", "Your CSV is being analyzed. You can explore other jobs while it finishes."],
      failed: ["This analysis couldn’t be completed", "Check the CSV format and try a new upload. The original job remains available for inspection."],
    };
    const [title, description] = messages[result.status] || ["Results unavailable", "Please refresh to check this job again."];
    byId("waiting-title").textContent = title;
    byId("waiting-description").textContent = description;
  }
}

async function refresh() {
  if (state.refreshing) { state.refreshAgain = true; return; }
  state.refreshing = true;
  const parameters = new URLSearchParams({page: state.page, page_size: state.pageSize, q: state.query});
  if (state.status) parameters.set("status", state.status);
  const selection = `${state.page}:${state.query}:${state.status}`;
  try {
    const data = await request(`/dashboard/data?${parameters}`);
    if (selection !== `${state.page}:${state.query}:${state.status}`) return;
    renderJobs(data);
    if (!state.selected && data.items.length) {
      const first = data.items.find((job) => job.job_type === "csv");
      if (first) selectJob(first, false);
    }
    if (state.selectedJob) {
      const visible = data.items.find((job) => job.id === state.selected);
      if (visible) state.selectedJob = visible;
      else {
        const latest = await request(`/jobs/${state.selected}`);
        if (state.selectedJob.id === latest.id) state.selectedJob = {...state.selectedJob, ...latest};
      }
    }
    setConnection(true);
    await refreshSelection();
  } catch (error) {
    setConnection(false);
    notify(error.message || "Couldn’t reach the application. Check that FlowForge is running.", true);
  } finally {
    state.refreshing = false;
    if (state.refreshAgain) {
      state.refreshAgain = false;
      refresh();
    }
  }
}

byId("upload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  upload(state.selectedFile, byId("name").value);
});
byId("file").addEventListener("change", (event) => chooseFile(event.target.files[0]));
const dropzone = byId("dropzone");
for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); });
}
dropzone.addEventListener("drop", (event) => {
  if (event.dataTransfer.files.length !== 1) return notify("Choose one CSV at a time.", true);
  chooseFile(event.dataTransfer.files[0]);
});
byId("sample-button").addEventListener("click", async () => {
  if (state.uploading) return;
  setBusy(true);
  try {
    const response = await fetch("/samples/sales.csv");
    if (!response.ok) throw new Error("The sample file could not be loaded.");
    const sample = new File([await response.blob()], "sales.csv", {type: "text/csv"});
    setBusy(false);
    await upload(sample, "Sales sample analysis");
  } catch (error) { notify(error.message, true); setBusy(false); }
});
let searchTimer;
byId("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = byId("search").value; state.page = 1; refresh(); }, 250);
});
byId("status-filter").addEventListener("change", () => { state.status = byId("status-filter").value; state.page = 1; refresh(); });
byId("previous-page").onclick = () => { if (state.page > 1) { state.page--; refresh(); } };
byId("next-page").onclick = () => { state.page++; refresh(); };
byId("refresh-button").onclick = () => { state.resultKey = null; refresh(); };
const tabs = Array.from(document.querySelectorAll("[data-view]"));
for (const [index, tab] of tabs.entries()) {
  tab.onclick = () => setView(tab.dataset.view);
  tab.onkeydown = (event) => {
    let target;
    if (event.key === "ArrowRight") target = tabs[(index + 1) % tabs.length];
    if (event.key === "ArrowLeft") target = tabs[(index + tabs.length - 1) % tabs.length];
    if (event.key === "Home") target = tabs[0];
    if (event.key === "End") target = tabs[tabs.length - 1];
    if (target) { event.preventDefault(); setView(target.dataset.view); target.focus(); }
  };
}
byId("export-button").onclick = () => {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result, null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = element("a");
  link.href = url;
  link.download = `flowforge-${state.result.id}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden) refresh(); }, 3000);
refresh();
