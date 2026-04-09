// electron-gui/results.js

const API_BASE = (typeof window !== "undefined" && window.API_ROOT)
  ? window.API_ROOT
  : "http://127.0.0.1:5050";
let reportsCache = [];
let policyCache = null;
let currentFilter = "all";
let currentReport = null;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

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
    const badge = report.status === "graded"
      ? (report.pass ? "PASS" : "FAIL")
      : "NO RESULTS";

    item.innerHTML = `
      <div class="student-result-top">
        <strong>${report.student_id}</strong>
      </div>
      <div class="meta">${badge}</div>
      <div class="student-breakdown">${summary.major || 0} major • ${summary.minor || 0} minor</div>
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
  if (value === null) return "configured as null";
  if (value === undefined) return "not present";
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

function formatScalarJsonLine(key, value) {
  const renderedValue = value === undefined ? null : value;
  return `"${key}": ${JSON.stringify(renderedValue)},`;
}

function formatJsonSnippet(value, contextPath = "", highlightKey = null) {
  if (value === null || value === undefined) {
    if (highlightKey) {
      return formatScalarJsonLine(highlightKey, null);
    }
    return "(none)";
  }
  if (
    highlightKey &&
    (typeof value === "string" || typeof value === "number" || typeof value === "boolean" || value === null)
  ) {
    return formatScalarJsonLine(highlightKey, value);
  }
  if (typeof value === "string") {
    if (highlightKey) {
      return formatScalarJsonLine(highlightKey, value);
    }
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch (_) {
    return String(value);
  }
}

function renderContextPane(title, pathText, content) {
  return `
    <div class="context-pane">
      <div class="context-pane-header">
        <strong>${escapeHtml(title)}</strong>
        <div class="context-pane-path">${escapeHtml(pathText || "(none)")}</div>
      </div>
      <pre>${escapeHtml(content)}</pre>
    </div>
  `;
}

function activateContextTab(name) {
  const modal = document.getElementById("errorContextModal");
  if (!modal) return;
  modal.querySelectorAll(".context-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  modal.querySelectorAll(".context-tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.tab === name);
  });
}

function closeErrorContextModal() {
  const modal = document.getElementById("errorContextModal");
  if (!modal) return;
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

async function openErrorContext(report, item) {
  const modal = document.getElementById("errorContextModal");
  const title = document.getElementById("errorContextTitle");
  const meta = document.getElementById("errorContextMeta");
  const body = document.getElementById("errorContextBody");
  if (!modal || !title || !meta || !body) return;

  title.textContent = item.feature || "Config Context";
  meta.textContent = "Loading config context...";
  body.innerHTML = `
    <div class="context-tabs">
      <button type="button" class="context-tab active" data-tab="raw">Raw CLI</button>
      <button type="button" class="context-tab" data-tab="json">Parsed JSON</button>
    </div>
    <div class="context-tab-panel active" data-tab="raw">
      <div class="context-pane"><pre>Loading template CLI excerpt...</pre></div>
      <div class="context-pane"><pre>Loading student CLI excerpt...</pre></div>
    </div>
    <div class="context-tab-panel" data-tab="json">
      <div class="context-pane"><pre>Loading template config...</pre></div>
      <div class="context-pane"><pre>Loading student config...</pre></div>
    </div>
  `;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");

  try {
    const payload = await fetchJson("/api/error_context", {
      method: "POST",
      body: JSON.stringify({
        target_path: getSessionPath(),
        student_id: report.student_id,
        template_name: report.template_name,
        hostname: item.hostname,
        feature: item.feature,
        expected: item.expected,
        actual: item.actual,
      }),
    });

    title.textContent = item.feature || "Config Context";
    meta.textContent = [
      payload.context_path ? `Context: ${payload.context_path}` : null,
      payload.highlight_key ? `Field: ${payload.highlight_key}` : null,
      item.rule_code ? `Code: ${item.rule_code}` : null,
      payload.command_hint ? `Source: ${payload.command_hint}` : null,
    ].filter(Boolean).join(" • ");

    body.innerHTML = `
      <div class="context-tabs">
        <button type="button" class="context-tab active" data-tab="raw">Raw CLI</button>
        <button type="button" class="context-tab" data-tab="json">Parsed JSON</button>
      </div>
      <div class="context-tab-panel active" data-tab="raw">
        ${renderContextPane(
          "Template",
          payload.template_raw_path || "Template raw log not found",
          payload.template_raw_excerpt || "(none)"
        )}
        ${renderContextPane(
          "Student",
          payload.student_raw_path || "Student raw log not found",
          payload.student_raw_excerpt || "(none)"
        )}
      </div>
      <div class="context-tab-panel" data-tab="json">
        ${renderContextPane(
          "Template",
          payload.template_config_path || "Template config not found",
          formatJsonSnippet(payload.template_context, payload.context_path, payload.highlight_key)
        )}
        ${renderContextPane(
          "Student",
          payload.student_config_path || "Student config not found",
          formatJsonSnippet(payload.student_context, payload.context_path, payload.highlight_key)
        )}
      </div>
    `;
    body.querySelectorAll(".context-tab").forEach((button) => {
      button.addEventListener("click", () => activateContextTab(button.dataset.tab));
    });
    activateContextTab("raw");
  } catch (err) {
    meta.textContent = "Failed to load config context.";
    body.innerHTML = `
      <div class="context-tab-panel active" data-tab="raw">
        <div class="context-pane" style="grid-column: 1 / -1;">
          <pre>${escapeHtml(err.message || "Unable to load config context.")}</pre>
        </div>
      </div>
      <div class="context-tab-panel" data-tab="json">
        <div class="context-pane" style="grid-column: 1 / -1;">
          <pre>${escapeHtml(err.message || "Unable to load config context.")}</pre>
        </div>
      </div>
    `;
  }
}

function bindContextModalEvents() {
  const modal = document.getElementById("errorContextModal");
  if (!modal) return;
  modal.querySelectorAll(".context-tab").forEach((button) => {
    button.addEventListener("click", () => activateContextTab(button.dataset.tab));
  });
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

  const configErrorCount = (summary.major || 0) + (summary.minor || 0);
  const verifyDedupCount = summary.verify_deduplicated || 0;
  const verifyFailedCount = summary.verify_failed || 0;

  panel.innerHTML = `
    <h2>${report.student_id}</h2>
    <p class="hint">Template: ${report.template_name || "Unknown"} • Mode: ${report.grading_mode || "strict"}</p>
    <div class="report-summary">
      <div class="summary-box" data-filter="all"><strong>Scored Findings</strong><div>${configErrorCount}</div></div>
      <div class="summary-box" data-filter="major"><strong>Major</strong><div>${summary.major || 0}</div></div>
      <div class="summary-box" data-filter="minor"><strong>Minor</strong><div>${summary.minor || 0}</div></div>
      <div class="summary-box" data-filter="verification"><strong>Verification</strong><div>${verifyDedupCount + verifyFailedCount}</div></div>
    </div>
    <div class="report-details" id="reportDetails"></div>
  `;

  const details = panel.querySelector("#reportDetails");
  if (!details) return;

  panel.querySelectorAll(".summary-box").forEach((box) => {
    const filter = box.dataset.filter || "all";
    if (!box.dataset.filter) return;
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

    // Separate config and verification errors
    let configErrors = hostItems.filter((i) => i.status !== "correct" && i.layer !== "verification");
    let verificationErrors = hostItems.filter((i) => i.status !== "correct" && i.layer === "verification");

    // Apply filters
    if (currentFilter === "major") {
      configErrors = configErrors.filter((i) => i.severity === "major");
      verificationErrors = [];
    } else if (currentFilter === "minor") {
      configErrors = configErrors.filter((i) => (i.severity || "minor") === "minor");
      verificationErrors = [];
    } else if (currentFilter === "verification") {
      configErrors = [];
      // show all verification
    }

    if (configErrors.length === 0 && verificationErrors.length === 0) return;

    const section = document.createElement("div");
    section.className = "report-section";
    section.innerHTML = `<h3>${hostname}</h3>`;

    // --- Config Errors ---
    if (configErrors.length > 0) {
      const configDetails = document.createElement("details");
      configDetails.open = true;
      configDetails.innerHTML = `<summary>Config Errors (${configErrors.length})</summary>`;
      configErrors.forEach((item) => {
        configDetails.appendChild(_renderErrorItem(report, item, false));
      });
      section.appendChild(configDetails);
    }

    // --- Verification ---
    if (verificationErrors.length > 0) {
      const verifyDetails = document.createElement("details");
      verifyDetails.open = currentFilter === "verification";
      verifyDetails.innerHTML = `<summary>Verification (${verificationErrors.length})</summary>`;
      verificationErrors.forEach((item) => {
        verifyDetails.appendChild(_renderErrorItem(report, item, true));
      });
      section.appendChild(verifyDetails);
    }

    section.appendChild(document.createElement("hr"));
    details.appendChild(section);
  });
}

function _renderErrorItem(report, item, isVerification) {
  const div = document.createElement("div");
  const severity = item.severity ? item.severity : "minor";
  const isDeduplicated = item.deduplicated === true;
  const isRuleDedup = item.rule_deduplicated === true;
  const isSkipped = item.status === "skipped";
  const codeLine = item.rule_code
    ? `<div class="result-code">Code: ${item.rule_code}</div>`
    : "";

  let severityClass, statusLabel;
  if (isSkipped) {
    severityClass = "severity-skipped";
    statusLabel = "SKIPPED • RULE DISABLED";
    div.className = "result-item result-item--skipped";
  } else if (isVerification && isDeduplicated) {
    severityClass = "severity-verified";
    statusLabel = `${item.status || "mismatch"} • ALREADY COUNTED`;
    div.className = "result-item result-item--dedup";
  } else if (!isVerification && isRuleDedup) {
    severityClass = "severity-verified";
    statusLabel = `${item.status || "mismatch"} • ${severity.toUpperCase()} (NOT SCORED — same rule already counted)`;
    div.className = "result-item result-item--dedup";
  } else {
    severityClass = severity === "major" ? "severity-major" : "severity-minor";
    statusLabel = `${item.status || "mismatch"} • ${severity.toUpperCase()}`;
    div.className = "result-item";
  }

  // Build the dedup info line
  let dedupInfo = "";
  if (isVerification && isDeduplicated) {
    const ref = item.layer1_ref || item.block_name || "";
    const shortRef = ref.replace(/^show_running_config\./, "");
    dedupInfo = `<div class="dedup-ref">↳ Counted under: <strong>${escapeHtml(shortRef || "config error")}</strong></div>`;
  } else if (!isVerification && isRuleDedup) {
    const ruleCode = item.rule_code || item.rule_id || "";
    dedupInfo = `<div class="dedup-ref">↳ Same rule <strong>${escapeHtml(ruleCode)}</strong> already scored on another device</div>`;
  } else if (isSkipped) {
    const ruleCode = item.rule_code || item.rule_id || "matched rule";
    dedupInfo = `<div class="dedup-ref">↳ Hidden from scoring because <strong>${escapeHtml(ruleCode)}</strong> is disabled in Rubric Rules</div>`;
  }

  div.innerHTML = `
    <div class="meta">${item.feature || "(unknown)"}</div>
    ${codeLine}
    <div class="status ${severityClass}">${statusLabel}</div>
    ${dedupInfo}
    <div class="result-values">
      <div><strong>Expected</strong><pre>${formatValue(item.expected)}</pre></div>
      <div><strong>Actual</strong><pre>${formatValue(item.actual)}</pre></div>
    </div>
  `;
  div.addEventListener("click", () => openErrorContext(report, item));
  return div;
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
  document.getElementById("closeErrorContextBtn")?.addEventListener("click", closeErrorContextModal);
  bindContextModalEvents();
  document.getElementById("errorContextModal")?.addEventListener("click", (event) => {
    if (event.target?.id === "errorContextModal") {
      closeErrorContextModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeErrorContextModal();
    }
  });

  const autoRun = localStorage.getItem("autoRunResults");
  if (autoRun === "true") {
    localStorage.removeItem("autoRunResults");
    runComparison();
  } else {
    refreshResults();
  }
});
