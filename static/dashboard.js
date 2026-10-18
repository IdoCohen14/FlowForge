"use strict";

// All server-provided labels are inserted as text, never HTML.
const byId = (id) => document.getElementById(id);
const statusLabels = {pending: "Queued", running: "Processing", completed: "Completed", failed: "Failed"};
const numberFormat = new Intl.NumberFormat();
const state = {
  page: 1, pageSize: 6, query: "", status: "", selected: null,
  selectedJob: null, selectedFile: null, result: null, resultKey: null,
  columnQuery: "", columnSort: "original",
  previewing: false, previewReady: false, previewToken: 0, details: null,
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
  byId("upload-button").disabled = busy || state.previewing || !state.previewReady;
  byId("sample-button").disabled = busy;
  byId("upload-button").textContent = busy ? "Saving your file…" : "Create analysis ↗";
}

async function chooseFile(file) {
  if (!file) return false;
  const token = ++state.previewToken;
  state.previewReady = false;
  state.previewing = false;
  state.selectedFile = null;
  setBusy(state.uploading);
  byId("preview-panel").hidden = false;
  byId("preview-panel").classList.remove("invalid");
  byId("preview-head").replaceChildren();
  byId("preview-body").replaceChildren();
  byId("preview-caption").textContent = "";
  byId("file-title").textContent = file.name;
  byId("file-description").textContent = `${numberFormat.format(file.size)} bytes`;
  if (!file.name.toLowerCase().endsWith(".csv") || file.size > 10 * 1024 * 1024) {
    byId("preview-panel").classList.add("invalid");
    byId("preview-message").textContent = "Choose a .csv file no larger than 10 MiB.";
    return false;
  }
  state.selectedFile = file;
  state.previewing = true;
  byId("preview-message").textContent = "Validating your CSV and preparing a preview…";
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const preview = await request("/jobs/csv/preview", {method: "POST", body: form});
    if (token !== state.previewToken) return false;
    const header = element("tr");
    for (const label of preview.headers) {
      const cell = element("th", label);
      cell.scope = "col";
      header.append(cell);
    }
    byId("preview-head").replaceChildren(header);
    for (const values of preview.rows) {
      const row = element("tr");
      for (const value of values) row.append(element("td", value || "—"));
      byId("preview-body").append(row);
    }
    byId("preview-caption").textContent = `${preview.column_count} columns`;
    const notes = [`${preview.rows.length} preview rows${preview.has_more_rows ? " · more rows in file" : ""}`];
    if (preview.column_count > preview.headers.length) notes.push(`first ${preview.headers.length} columns shown`);
    if (preview.truncated_cells) notes.push("long cells shortened for display");
    if (!preview.rows.length) notes.push("header-only CSV");
    byId("preview-message").textContent = notes.join(" · ");
    state.previewReady = true;
    notify("");
    return true;
  } catch (error) {
    if (token !== state.previewToken) return false;
    byId("preview-panel").classList.add("invalid");
    byId("preview-message").textContent = `CSV validation failed: ${error.message}`;
    return false;
  } finally {
    if (token === state.previewToken) { state.previewing = false; setBusy(state.uploading); }
  }
}

async function upload(file, name) {
  if (state.uploading) return;
  if (!file) {
    notify("Choose a CSV file first, or try the sample data.", true);
    byId("file").focus();
    return;
  }
  if (state.previewing || !state.previewReady || file !== state.selectedFile) {
    notify("Wait for a valid preview before creating the analysis.", true);
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
  state.columnQuery = "";
  state.columnSort = "original";
  byId("column-search").value = "";
  byId("column-sort").value = "original";
  state.selected = job.id;
  state.selectedJob = job;
  state.resultKey = null;
  state.result = null;
  state.details = null;
  byId("recovery-panel").hidden = true;
  for (const id of ["time-submitted", "time-started", "time-finished", "time-duration"]) byId(id).textContent = "—";
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
  const signature = JSON.stringify([data.items, state.selected, Math.floor(Date.now() / 60000)]);
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
  const columns = FlowForgeColumns.selectColumns(data.columns, state.columnQuery, state.columnSort);
  byId("column-count").textContent = `${columns.length} of ${data.columns.length} columns`;
  byId("columns-empty").hidden = columns.length > 0;
  for (const column of columns) {
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
  renderFindings(data);
}

function renderFindings(data) {
  const findings = [];
  if (!data.row_count) findings.push("This file contains column headers but no data rows. Add rows to calculate statistics.");
  else if (!data.missing_values_total) findings.push(`All ${numberFormat.format(data.row_count * data.column_count)} cells contain a value. This measures completeness, not accuracy.`);
  else findings.push(`${numberFormat.format(data.missing_values_total)} cells are missing across ${numberFormat.format(data.row_count)} rows. Empty and whitespace-only cells count as missing.`);
  const incomplete = data.columns.filter((column) => column.missing_values > 0).sort((a, b) => b.missing_values - a.missing_values);
  if (incomplete.length) findings.push(`${incomplete[0].name} has the most missing values: ${numberFormat.format(incomplete[0].missing_values)} of ${numberFormat.format(data.row_count)} rows.`);
  for (const column of data.columns.filter((column) => column.numeric).slice(0, 3)) {
    const stats = column.numeric;
    findings.push(`${column.name}: average ${stats.mean}, ranging from ${stats.minimum} to ${stats.maximum}, across ${numberFormat.format(stats.count)} nonmissing numeric values.`);
  }
  if (data.row_count && !data.columns.some((column) => column.numeric)) findings.push("No column qualifies for numeric statistics. Numeric columns require every nonmissing value to be a supported finite number.");
  byId("findings").replaceChildren(...findings.map((text) => element("li", text)));
}

function renderDetails(details) {
  state.details = details;
  showJobMetadata(details);
  const date = (value) => value ? new Date(value).toLocaleString(undefined, {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "Not recorded";
  byId("time-submitted").textContent = date(details.created_at);
  byId("time-started").textContent = date(details.started_at);
  byId("time-finished").textContent = date(details.finished_at);
  byId("time-duration").textContent = details.processing_seconds === null ? "Not recorded" : `${details.processing_seconds.toFixed(3)} s`;
  byId("recovery-panel").hidden = !details.recovery_action;
  byId("recovery-title").textContent = ({delivery: "Delivery needs attention", validation: "The file needs correction", storage: "A new source file is needed", interrupted: "Processing was interrupted", processing: "Processing failed"})[details.failure_stage] || "Analysis needs attention";
  byId("recovery-message").textContent = details.message || "";
  byId("recovery-button").textContent = details.retry_job_id ? "Open retry" : details.recovery_action === "upload" ? "Choose a new file" : details.recovery_action === "retry_delivery" ? "Retry delivery" : "Retry analysis";
}

async function refreshSelection() {
  if (!state.selectedJob) return;
  const job = state.selectedJob;
  const details = await request(`/jobs/${job.id}/details`);
  if (state.selected !== job.id) return;
  renderDetails(details);
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
    byId("waiting-title").textContent = details.failure_stage === "delivery" ? "Delivery is paused" : title;
    byId("waiting-description").textContent = details.failure_stage === "delivery" ? "This job has not reached a worker. Use Retry delivery to resume." : description;
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
    const valid = await chooseFile(sample);
    setBusy(false);
    if (valid) await upload(sample, "Sales sample analysis");
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
byId("recovery-button").onclick = async () => {
  const details = state.details;
  if (!details) return;
  if (details.recovery_action === "upload" && !details.retry_job_id) {
    byId("upload-form").scrollIntoView({block: "center"});
    byId("file").focus();
    byId("file").click();
    return;
  }
  byId("recovery-button").disabled = true;
  try {
    const job = details.retry_job_id
      ? await request(`/jobs/${details.retry_job_id}/details`)
      : await request(`/jobs/${details.id}/retry`, {method: "POST"});
    selectJob({...job, job_type: "csv"}, false);
    state.page = 1;
    state.query = "";
    state.status = "";
    byId("search").value = "";
    byId("status-filter").value = "";
    notify(details.recovery_action === "retry_delivery" ? "Delivery has been queued again." : "Retry opened. The original analysis has been preserved.");
    await refresh();
  } catch (error) { notify(error.message, true); }
  finally { byId("recovery-button").disabled = false; }
};
function syncThemeButton() {
  const light = document.documentElement.dataset.theme === "light";
  byId("theme-toggle").textContent = light ? "☾ Dark mode" : "☀ Light mode";
  byId("theme-toggle").setAttribute("aria-label", light ? "Use dark theme" : "Use light theme");
  byId("theme-toggle").setAttribute("aria-pressed", String(light));
  document.querySelector('meta[name="theme-color"]').content = light ? "#f4f7fb" : "#10141c";
}
byId("theme-toggle").onclick = () => {
  const theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("flowforge-theme", theme); } catch (_) {}
  syncThemeButton();
};
window.addEventListener("storage", (event) => {
  if (event.key === "flowforge-theme" || event.key === null) {
    document.documentElement.dataset.theme = event.newValue === "light" ? "light" : "dark";
    syncThemeButton();
  }
});
byId("column-search").addEventListener("input", () => {
  state.columnQuery = byId("column-search").value;
  if (state.result) renderResults(state.result);
});
byId("column-sort").addEventListener("change", () => {
  state.columnSort = byId("column-sort").value;
  if (state.result) renderResults(state.result);
});
syncThemeButton();
setBusy(false);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden) refresh(); }, 3000);
refresh();
