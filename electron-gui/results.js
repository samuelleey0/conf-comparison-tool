// Results page controller. It reads saved comparison outputs, applies the active
// grading policy/rubric rules, and lets users inspect raw/parsed evidence.

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

function getPathParts(pathValue) {
  if (!pathValue || typeof pathModule === "undefined") return [];
  return String(pathValue).split(pathModule.sep).filter(Boolean);
}

function getSessionInfo() {
  // Recover session identity from localStorage first, then from the selected
  // path. This makes Results resilient when users navigate back from other pages.
  const sessionPath = getSessionPath();
  const basePath = localStorage.getItem("basePath");
  let classroom = localStorage.getItem("classroom") || localStorage.getItem("examName") || "";
  let tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId") || "";
  let timeSlot = localStorage.getItem("timeSlot") || "";

  if ((!classroom || !tutorName || !timeSlot) && sessionPath) {
    const parts = getPathParts(sessionPath);
    if (parts.length >= 3) {
      classroom = classroom || parts[parts.length - 3];
      tutorName = tutorName || parts[parts.length - 2];
      timeSlot = timeSlot || parts[parts.length - 1];
    }
  }

  if ((!classroom || !tutorName || !timeSlot) && basePath) {
    const parts = getPathParts(basePath);
    if (parts.length >= 4) {
      classroom = classroom || parts[parts.length - 4];
      tutorName = tutorName || parts[parts.length - 3];
      timeSlot = timeSlot || parts[parts.length - 2];
    }
  }

  return { classroom, tutorName, timeSlot, sessionPath };
}

function getSessionPath() {
  const storedSession = localStorage.getItem("sessionPath");
  if (storedSession) {
    const basePath = localStorage.getItem("basePath");
    if (basePath && storedSession === basePath && typeof pathModule !== "undefined") {
      // Older flows stored the selected student path as sessionPath; correct it here.
      return pathModule.dirname(basePath);
    }
    return storedSession;
  }

  const basePath = localStorage.getItem("basePath");
  if (basePath && typeof pathModule !== "undefined") {
    return pathModule.dirname(basePath);
  }

  const classroom = localStorage.getItem("classroom") || localStorage.getItem("examName");
  const tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId");
  const timeSlot = localStorage.getItem("timeSlot");
  if (classroom && tutorName && timeSlot) {
    try {
      const os = require("os");
      return pathModule.join(os.homedir(), "Documents", classroom, tutorName, timeSlot);
    } catch (_) {
      return null;
    }
  }

  if (classroom && tutorName) {
    try {
      const os = require("os");
      return pathModule.join(os.homedir(), "Documents", classroom, tutorName);
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

function canDisableRuleFromResult(item) {
  // Only scored findings can disable a rule. Deduplicated/skipped rows are
  // already evidence-only, so showing the button there would be misleading.
  const ruleCode = item?.rule_code || item?.rule_id;
  if (!ruleCode) return false;
  if (!["missing", "extra", "mismatch"].includes(item.status)) return false;
  if (item.counts_toward_marking === false) return false;
  if (item.deduplicated || item.rule_deduplicated || item.verification_rule_deduplicated) return false;
  return true;
}

async function disableRubricRule(ruleCode, studentId = "") {
  // Rule changes are global, not per-student. Results refresh immediately so the
  // user can see the same finding become skipped/unscored.
  const code = String(ruleCode || "").trim();
  if (!code) return;
  const message = [
    `Disable rubric rule ${code}?`,
    "",
    "This is a global grading rule. Matching findings for every student will become unscored until you re-enable it in System Admin.",
  ].join("\n");
  if (!confirm(message)) return false;

  await fetchJson("/api/rubric_rules/disable", {
    method: "POST",
    body: JSON.stringify({ rule_code: code }),
  });
  // Refresh instead of re-running comparison; classification uses the current rules.
  await refreshResults(studentId || currentReport?.student_id || null);
  alert(`${code} is now disabled. Matching findings are still visible, but no longer count toward marking.`);
  return true;
}

async function enableRubricRule(ruleCode, studentId = "") {
  const code = String(ruleCode || "").trim();
  if (!code) return false;
  if (!confirm(`Re-enable rubric rule ${code} for marking?`)) return false;

  await fetchJson("/api/rubric_rules/enable", {
    method: "POST",
    body: JSON.stringify({ rule_code: code }),
  });
  // Reclassify existing result files with the re-enabled rule.
  await refreshResults(studentId || currentReport?.student_id || null);
  alert(`${code} is now re-enabled and matching findings will count again.`);
  return true;
}

function setSessionLabel() {
  const label = document.getElementById("sessionLabel");
  const { classroom, tutorName, timeSlot } = getSessionInfo();
  if (!label) return;
  if (classroom && tutorName && timeSlot) {
    label.textContent = `Session: ${classroom} / ${tutorName} / ${timeSlot}`;
  } else if (classroom && tutorName) {
    label.textContent = `Session: ${classroom} / ${tutorName}`;
  } else {
    label.textContent = "Session not set";
  }
}

function statusClass(report) {
  if (report.status !== "graded") return "status-missing";
  return report.pass ? "status-pass" : "status-fail";
}

function renderResultsList(reports, preferredStudentId = null) {
  const list = document.getElementById("resultsList");
  if (!list) return;
  list.innerHTML = "";

  if (!reports.length) {
    list.innerHTML = `<p class="hint" style="padding: 12px;">No students found.</p>`;
    return;
  }

  const selectedIndex = Math.max(
    0,
    reports.findIndex((report) => report.student_id === preferredStudentId)
  );

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

    if (index === selectedIndex) {
      item.classList.add("selected");
      renderReport(report);
    }
  });
}

function formatValue(value) {
  if (value === null) return "configured as null";
  if (value === undefined) return "not present";
  if (value === "__ACCMS_NOT_PRESENT__") return "not present";
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

function rawLogAvailability(entry) {
  const states = [];
  states.push(entry && entry.template ? "Template log" : "No template log");
  states.push(entry && entry.student ? "Student log" : "No student log");
  return states.join(" • ");
}

function renderRawLogCommandPreview(entry) {
  if (!entry) {
    return `
      <div class="raw-command-empty">
        Choose a command to preview the matching template and student raw logs.
      </div>
    `;
  }

  const templateItem = entry.template || {};
  const studentItem = entry.student || {};
  return `
    <div class="context-tab-panel active" data-tab="raw">
      ${renderContextPane(
        "Template",
        templateItem.path || "Template raw log not found",
        templateItem.content || "(none)"
      )}
      ${renderContextPane(
        "Student",
        studentItem.path || "Student raw log not found",
        studentItem.content || "(none)"
      )}
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

async function openRawLogPreview(report, hostname) {
  const modal = document.getElementById("errorContextModal");
  const title = document.getElementById("errorContextTitle");
  const meta = document.getElementById("errorContextMeta");
  const body = document.getElementById("errorContextBody");
  if (!modal || !title || !meta || !body) return;

  title.textContent = `Raw Logs: ${hostname}`;
  meta.textContent = "Loading full template and student raw logs...";
  body.innerHTML = `
    <div class="context-tab-panel active" data-tab="raw">
      <div class="context-pane"><pre>Loading template raw logs...</pre></div>
      <div class="context-pane"><pre>Loading student raw logs...</pre></div>
    </div>
  `;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");

  try {
    const payload = await fetchJson("/api/raw_log_preview", {
      method: "POST",
      body: JSON.stringify({
        target_path: getSessionPath(),
        student_id: report.student_id,
        template_name: report.template_name,
        hostname,
      }),
    });

    title.textContent = `Raw Logs: ${hostname}`;
    const logs = Array.isArray(payload.logs) ? payload.logs : [];
    const baseMeta = [
      `Student: ${payload.student_id}`,
      `Template: ${payload.template_name}`,
      `Commands: ${logs.length}`,
    ];
    meta.textContent = baseMeta.join(" • ");

    if (!logs.length) {
      body.innerHTML = `
        <div class="raw-command-empty">
          No raw log files were found for this device.
        </div>
      `;
      return;
    }

    body.innerHTML = `
      <div class="raw-command-layout">
        <div class="raw-command-toolbar" aria-label="Raw log commands">
          <div class="raw-command-label">Choose Command</div>
          <div class="raw-command-list">
            ${logs.map((entry, index) => `
              <button type="button" class="raw-command-btn" data-index="${index}">
                <span>${escapeHtml(entry.command || `Command ${index + 1}`)}</span>
                <small>${escapeHtml(rawLogAvailability(entry))}</small>
              </button>
            `).join("")}
          </div>
        </div>
        <div id="rawCommandPreview" class="raw-command-preview">
          ${renderRawLogCommandPreview(null)}
        </div>
      </div>
    `;

    const preview = body.querySelector("#rawCommandPreview");
    body.querySelectorAll(".raw-command-btn").forEach((button) => {
      button.addEventListener("click", () => {
        const index = Number(button.dataset.index);
        const entry = logs[index];
        body.querySelectorAll(".raw-command-btn").forEach((item) => {
          item.classList.toggle("active", item === button);
        });
        if (preview) {
          preview.innerHTML = renderRawLogCommandPreview(entry);
        }
        meta.textContent = [...baseMeta, `Selected: ${entry.command || `Command ${index + 1}`}`].join(" • ");
      });
    });
  } catch (err) {
    meta.textContent = "Failed to load raw logs.";
    body.innerHTML = `
      <div class="context-tab-panel active" data-tab="raw">
        <div class="context-pane" style="grid-column: 1 / -1;">
          <pre>${escapeHtml(err.message || "Unable to load raw logs.")}</pre>
        </div>
      </div>
    `;
  }
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
  const hostnames = new Set([
    ...Object.keys(report.hostnames || {}),
    ...Object.keys(grouped || {}),
  ]);

  panel.innerHTML = `
    <h2>${report.student_id}</h2>
    <p class="hint">Template: ${report.template_name || "Unknown"} • Mode: ${report.grading_mode || "strict"}</p>
    <div class="report-summary">
      <div class="summary-box" data-filter="all"><strong>All Findings</strong><div>${(summary.major || 0) + (summary.minor || 0)}</div></div>
      <div class="summary-box" data-filter="major"><strong>Major Errors</strong><div>${summary.major || 0}</div></div>
      <div class="summary-box" data-filter="minor"><strong>Minor Errors</strong><div>${summary.minor || 0}</div></div>
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

  Array.from(hostnames).sort().forEach((hostname) => {
    const hostItems = grouped[hostname] || [];
    let errors = hostItems.filter((i) => i.status !== "correct");
    if (currentFilter === "major") {
      errors = errors.filter((i) => i.severity === "major");
    } else if (currentFilter === "minor") {
      errors = errors.filter((i) => (i.severity || "minor") === "minor");
    }

    const section = document.createElement("div");
    section.className = "report-section";
    section.innerHTML = `
      <div class="report-section-header">
        <h3>${escapeHtml(hostname)}</h3>
        <button type="button" class="raw-preview-btn" aria-label="Preview raw logs" title="Preview raw logs">
          <span aria-hidden="true">&gt;_</span>
        </button>
      </div>
    `;
    section.querySelector(".raw-preview-btn")?.addEventListener("click", (event) => {
      event.stopPropagation();
      openRawLogPreview(report, hostname);
    });

    const errorDetails = document.createElement("details");
    errorDetails.open = true;
    errorDetails.innerHTML = `<summary>Errors (${errors.length})</summary>`;
    errors.forEach((item) => {
      const isVerification =
        item.layer === "verification" || String(item.feature || "").startsWith("verification.");
      const div = _renderErrorItem(report, item, isVerification);
      errorDetails.appendChild(div);
    });

    section.appendChild(errorDetails);
    details.appendChild(section);
  });
}

function _renderErrorItem(report, item, isVerification) {
  const div = document.createElement("div");
  const severity = item.severity ? item.severity : "minor";
  const isDeduplicated = item.deduplicated === true;
  const isVerificationRuleDedup = item.verification_rule_deduplicated === true;
  const isRuleDedup = item.rule_deduplicated === true;
  const isSkipped = item.status === "skipped";
  const disableRuleCode = item.rule_code || item.rule_id || "";
  const disableAction = canDisableRuleFromResult(item)
    ? `
      <div class="result-item-actions">
        <button type="button" class="result-rule-disable-btn" data-rule-code="${escapeHtml(disableRuleCode)}">
          Disable Rule
        </button>
      </div>
    `
    : "";
  const enableAction = isSkipped && disableRuleCode
    ? `
      <div class="result-item-actions">
        <button type="button" class="result-rule-enable-btn" data-rule-code="${escapeHtml(disableRuleCode)}">
          Re-enable Rule
        </button>
      </div>
    `
    : "";
  const codeLine = item.rule_code
    ? `<div class="result-code">Code: ${item.rule_code}</div>`
    : "";

  const shortDedupRef = (ref) => String(ref || "").replace(/^show_running_config\./, "");

  const resolveVlanSchemeParent = () => {
    const vlanTokens = [
      ".access_vlan",
      ".switchport_mode",
      ".trunk_native_vlan",
      ".trunk_allowed_vlans",
      ".Vlan.interface",
      ".subinterface",
    ];
    const items = Array.isArray(report?.items) ? report.items : [];
    const hostname = item.hostname || "";
    const candidates = items.filter((candidate) => {
      if (!candidate || candidate === item) return false;
      if (candidate.status === "correct" || candidate.status === "skipped") return false;
      if (candidate.counts_toward_marking === false) return false;
      const feature = String(candidate.feature || "");
      return vlanTokens.some((token) => feature.includes(token));
    });

    const sameHost = candidates.find((candidate) => hostname && candidate.hostname === hostname);
    const parent = sameHost || candidates[0];
    if (parent?.feature) {
      return shortDedupRef(parent.feature);
    }
    return "related VLAN or switchport configuration error";
  };

  const displayDedupRef = (ref, fallback = "config error") => {
    if (ref === "show_running_config.__vlan_scheme__") {
      return resolveVlanSchemeParent();
    }
    return shortDedupRef(ref) || fallback;
  };

  let severityClass, statusLabel;
  if (isSkipped) {
    severityClass = "severity-skipped";
    statusLabel = "SKIPPED • RULE DISABLED";
    div.className = "result-item result-item--skipped";
  } else if (isVerification && isVerificationRuleDedup) {
    severityClass = "severity-verified";
    statusLabel = `${item.status || "mismatch"} • ALREADY COUNTED`;
    div.className = "result-item result-item--dedup";
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
  if (isVerification && isVerificationRuleDedup) {
    const ref = item.block_name || item.layer1_ref || "";
    dedupInfo = `<div class="dedup-ref">↳ Same verification rule already scored for <strong>${escapeHtml(displayDedupRef(ref, "this block"))}</strong></div>`;
  } else if (isVerification && isDeduplicated) {
    const ref = item.layer1_ref || item.block_name || "";
    dedupInfo = `<div class="dedup-ref">↳ Counted under: <strong>${escapeHtml(displayDedupRef(ref))}</strong></div>`;
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
    ${disableAction}
    ${enableAction}
  `;
  div.querySelector(".result-rule-disable-btn")?.addEventListener("click", async (event) => {
    event.stopPropagation();
    const ruleCode = event.currentTarget.dataset.ruleCode || disableRuleCode;
    try {
      event.currentTarget.disabled = true;
      event.currentTarget.textContent = "Disabling...";
      const disabled = await disableRubricRule(ruleCode, report?.student_id || "");
      if (!disabled) {
        event.currentTarget.disabled = false;
        event.currentTarget.textContent = "Disable Rule";
      }
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to disable rubric rule.");
      event.currentTarget.disabled = false;
      event.currentTarget.textContent = "Disable Rule";
    }
  });
  div.querySelector(".result-rule-enable-btn")?.addEventListener("click", async (event) => {
    event.stopPropagation();
    const ruleCode = event.currentTarget.dataset.ruleCode || disableRuleCode;
    try {
      event.currentTarget.disabled = true;
      event.currentTarget.textContent = "Re-enabling...";
      const enabled = await enableRubricRule(ruleCode, report?.student_id || "");
      if (!enabled) {
        event.currentTarget.disabled = false;
        event.currentTarget.textContent = "Re-enable Rule";
      }
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to re-enable rubric rule.");
      event.currentTarget.disabled = false;
      event.currentTarget.textContent = "Re-enable Rule";
    }
  });
  div.addEventListener("click", () => openErrorContext(report, item));
  return div;
}
async function runComparison() {
  const sessionPath = getSessionPath();
  if (!sessionPath) {
    alert("Session path not found. Please select a session first.");
    return;
  }

  const { classroom, tutorName, timeSlot } = getSessionInfo();

  const btn = document.getElementById("runComparisonBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Running...";
  }

  try {
    const data = await fetchJson("/api/grade", {
      method: "POST",
      body: JSON.stringify({
        classroom,
        tutor_name: tutorName,
        time_slot: timeSlot,
        exam_name: classroom,
        session_id: tutorName,
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

async function refreshResults(preferredStudentId = null) {
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
    renderResultsList(reportsCache, preferredStudentId || currentReport?.student_id || null);
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
