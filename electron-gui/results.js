// electron-gui/results.js

const API_BASE = (typeof window !== "undefined" && window.API_ROOT)
  ? window.API_ROOT
  : "http://127.0.0.1:5050";
let reportsCache = [];
let policyCache = null;
let currentFilter = "all";
let currentReport = null;

function getSessionPath() {
  const storedSession = localStorage.getItem("sessionPath");
  if (storedSession) {
    const basePath = localStorage.getItem("basePath");
    if (basePath && storedSession === basePath && typeof pathModule !== "undefined") {
      return pathModule.dirname(basePath);
    }
    return storedSession;
  }

  const basePath = localStorage.getItem("basePath");
  if (basePath && typeof pathModule !== "undefined") {
    return pathModule.dirname(basePath);
  }

  const exam = localStorage.getItem("examName");
  const session = localStorage.getItem("sessionId");
  if (exam && session) {
    try {
      const os = require("os");
      return pathModule.join(os.homedir(), "Documents", exam, session);
    } catch (_) {
      return null;
    }
  }

  return null;
}

async function fetchJson(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json();
  if (!res.ok || data.status === "error") {
    throw new Error(data.message || "Request failed.");
  }
  return data;
}

function setSessionLabel() {
  const label = document.getElementById("sessionLabel");
  const exam = localStorage.getItem("examName");
  const session = localStorage.getItem("sessionId");
  if (!label) return;
  if (exam && session) {
    label.textContent = `Session: ${exam} / ${session}`;
  } else {
    label.textContent = "Session not set";
  }
}

function statusClass(report) {
  if (report.status !== "graded") return "status-missing";
  return report.pass ? "status-pass" : "status-fail";
}

function renderResultsList(reports) {
  const list = document.getElementById("resultsList");
  if (!list) return;
  list.innerHTML = "";

  if (!reports.length) {
    list.innerHTML = `<p class="hint" style="padding: 12px;">No students found.</p>`;
    return;
  }

  reports.forEach((report, index) => {
    const item = document.createElement("div");
    item.className = `student-result-item ${statusClass(report)}`;
    item.dataset.studentId = report.student_id;

    const summary = report.summary || {};
    const errors = (summary.major || 0) + (summary.minor || 0);
    const badge = report.status === "graded"
      ? (report.pass ? "PASS" : "FAIL")
      : "NO RESULTS";

    item.innerHTML = `
      <strong>${report.student_id}</strong>
      <div class="meta">${badge} • ${errors} errors</div>
    `;

    item.addEventListener("click", () => {
      document.querySelectorAll(".student-result-item").forEach((el) => {
        el.classList.remove("selected");
      });
      item.classList.add("selected");
      renderReport(report);
    });

    list.appendChild(item);

    if (index === 0) {
      item.classList.add("selected");
      renderReport(report);
    }
  });
}

function formatValue(value) {
  if (value === null || value === undefined) return "(none)";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (_) {
    return String(value);
  }
}

function groupByHostname(items) {
  const grouped = {};
  items.forEach((item) => {
    const hostname = item.hostname || "unknown";
    if (!grouped[hostname]) grouped[hostname] = [];
    grouped[hostname].push(item);
  });
  return grouped;
}

function renderReport(report) {
  const panel = document.getElementById("reportPanel");
  if (!panel) return;
  currentReport = report;

  if (report.status !== "graded") {
    panel.innerHTML = `<p class="hint">No results found for ${report.student_id}. Run comparison first.</p>`;
    return;
  }

  const summary = report.summary || {};
  const items = report.items || [];
  const grouped = groupByHostname(items);

  panel.innerHTML = `
    <h2>${report.student_id}</h2>
    <p class="hint">Template: ${report.template_name || "Unknown"} • Mode: ${report.grading_mode || "strict"}</p>
    <div class="report-summary">
      <div class="summary-box" data-filter="all"><strong>Correct</strong><div>${summary.correct || 0}</div></div>
      <div class="summary-box" data-filter="major"><strong>Major Errors</strong><div>${summary.major || 0}</div></div>
      <div class="summary-box" data-filter="minor"><strong>Minor Errors</strong><div>${summary.minor || 0}</div></div>
    </div>
  `;

  panel.querySelectorAll(".summary-box").forEach((box) => {
    const filter = box.dataset.filter || "all";
    if (filter === currentFilter) {
      box.style.borderColor = "var(--color-primary)";
      box.style.boxShadow = "0 0 0 2px rgba(31, 59, 115, 0.15)";
    }
    box.addEventListener("click", () => {
      currentFilter = filter;
      if (currentReport) {
        renderReport(currentReport);
      }
    });
  });

  Object.keys(grouped).sort().forEach((hostname) => {
    const hostItems = grouped[hostname];
    let errors = hostItems.filter((i) => i.status !== "correct");
    if (currentFilter === "major") {
      errors = errors.filter((i) => i.severity === "major");
    } else if (currentFilter === "minor") {
      errors = errors.filter((i) => (i.severity || "minor") === "minor");
    }
    const corrects = hostItems.filter((i) => i.status === "correct");

    const section = document.createElement("div");
    section.className = "report-section";
    section.innerHTML = `<h3>${hostname}</h3>`;

    const errorDetails = document.createElement("details");
    errorDetails.open = true;
    errorDetails.innerHTML = `<summary>Errors (${errors.length})</summary>`;
    errors.forEach((item) => {
      const div = document.createElement("div");
      const severity = item.severity ? item.severity : "minor";
      const severityClass = severity === "major" ? "severity-major" : "severity-minor";
      div.className = "result-item";
      div.innerHTML = `
        <div class="meta">${item.feature || "(unknown)"}</div>
        <div class="status ${severityClass}">${item.status || "mismatch"} • ${severity.toUpperCase()}</div>
        <div class="result-values">
          <div><strong>Expected</strong><pre>${formatValue(item.expected)}</pre></div>
          <div><strong>Actual</strong><pre>${formatValue(item.actual)}</pre></div>
        </div>
      `;
      errorDetails.appendChild(div);
    });

    const correctDetails = document.createElement("details");
    correctDetails.innerHTML = `<summary>Correct (${corrects.length})</summary>`;
    corrects.forEach((item) => {
      const div = document.createElement("div");
      div.className = "result-item";
      div.innerHTML = `
        <div class="meta">${item.feature || "(unknown)"}</div>
        <div class="status">correct</div>
      `;
      correctDetails.appendChild(div);
    });

    section.appendChild(errorDetails);
    if (currentFilter === "all") {
      section.appendChild(correctDetails);
    }
    panel.appendChild(section);
  });
}

async function runComparison() {
  const sessionPath = getSessionPath();
  if (!sessionPath) {
    alert("Session path not found. Please select a session first.");
    return;
  }

  const exam = localStorage.getItem("examName");
  const session = localStorage.getItem("sessionId");

  const btn = document.getElementById("runComparisonBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Running...";
  }

  try {
    const data = await fetchJson("/api/grade", {
      method: "POST",
      body: JSON.stringify({
        exam_name: exam,
        session_id: session,
        target_path: sessionPath,
        template_name: localStorage.getItem("templateName") || null,
        include_reports: true,
      }),
    });
    reportsCache = data.reports || [];
    policyCache = data.policy || null;
    renderResultsList(reportsCache);
  } catch (err) {
    console.error(err);
    alert(err.message || "Comparison failed.");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Run Comparison";
    }
  }
}

async function refreshResults() {
  const sessionPath = getSessionPath();
  if (!sessionPath) {
    alert("Session path not found. Please select a session first.");
    return;
  }

  const btn = document.getElementById("refreshResultsBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Refreshing...";
  }

  try {
    const data = await fetchJson(`/api/results?target_path=${encodeURIComponent(sessionPath)}`);
    reportsCache = data.reports || [];
    policyCache = data.policy || null;
    renderResultsList(reportsCache);
  } catch (err) {
    console.error(err);
    alert(err.message || "Failed to load results.");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Refresh Results";
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (typeof loadNavbar === "function") loadNavbar();
  setSessionLabel();
  document.getElementById("runComparisonBtn")?.addEventListener("click", runComparison);
  document.getElementById("refreshResultsBtn")?.addEventListener("click", refreshResults);

  const autoRun = localStorage.getItem("autoRunResults");
  if (autoRun === "true") {
    localStorage.removeItem("autoRunResults");
    runComparison();
  }
});
