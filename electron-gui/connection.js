// -----------------------------
// Connection page
// -----------------------------

let currentAbortController = null;

function applySerialPreset(preset) {
  const portInput = document.getElementById("serialPort");
  if (!portInput) return;
  if (preset === "custom") {
    portInput.removeAttribute("readonly");
  } else {
    portInput.value = SERIAL_PRESETS[preset] || "/dev/ttyUSB0";
    portInput.setAttribute("readonly", "readonly");
  }
}

function toggleConnectionFields() {
  const conn = document.querySelector('input[name="connType"]:checked')?.value || "serial";
  const sshFields = document.getElementById("sshFields");
  const serialFields = document.getElementById("serialFields");
  const resetBtn = document.getElementById("resetDeviceBtn");
  if (!sshFields || !serialFields) return;
  if (conn === "ssh") {
    sshFields.classList.remove("hidden");
    serialFields.classList.add("hidden");
    if (resetBtn) resetBtn.disabled = true;
  } else {
    sshFields.classList.add("hidden");
    serialFields.classList.remove("hidden");
    if (resetBtn) resetBtn.disabled = false;
  }
  localStorage.setItem("connection", conn);
}

async function resetCiscoDevice({ triggerButton = null } = {}) {
  const type = document.querySelector('input[name="connType"]:checked');
  const conn = type?.value || "serial";
  if (conn !== "serial") {
    alert("Cisco reset is only supported in serial mode.");
    return false;
  }

  const deviceTypeSelect = document.getElementById("resetDeviceType");
  const deviceType = (deviceTypeSelect?.value || "switch").toLowerCase();
  const resetMessage = deviceType === "router"
    ? "This will reload the connected Cisco router without saving the running configuration. Continue?"
    : "This will delete vlan.dat and reload the connected Cisco switch without saving the running configuration. Continue?";

  const confirmed = confirm(resetMessage);
  if (!confirmed) return false;

  const portInput = document.getElementById("serialPort");
  const port =
    portInput?.value.trim() ||
    localStorage.getItem("serialPort") ||
    SERIAL_PRESETS.linux_usb;

  if (!port) {
    alert("Please configure the serial port first.");
    return false;
  }

  const resetBtn = triggerButton || null;
  const originalBtnText = resetBtn ? resetBtn.textContent : null;
  const abortBtn = document.getElementById("abortExecutionBtn");
  const originalAbortText = abortBtn ? abortBtn.textContent : null;
  const stopReset = () => {
    if (currentAbortController) {
      currentAbortController.abort();
    }
    try {
      fetch(`${API_ROOT}/api/abort`, { method: "POST" });
    } catch (_) {}
  };

  try {
    if (resetBtn) {
      resetBtn.disabled = true;
      resetBtn.textContent = "Resetting...";
    }
    currentAbortController = new AbortController();
    if (abortBtn) {
      abortBtn.textContent = "Stop Reset";
      abortBtn.style.display = "inline-block";
      abortBtn.onclick = stopReset;
    }

    appendLogLine(`[${nowTimestamp()}] Starting Cisco reset on ${port}...`);
    const modal = showStatusModal("Resetting device... Please wait.", "pending", {
      label: "Stop Reset",
      className: "danger",
      onClick: stopReset,
    });
    const response = await fetch(`${API_ROOT}/api/reset_device`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        connection: "serial",
        mode: "serial",
        device_type: deviceType,
        serial: { port },
      }),
      signal: currentAbortController.signal,
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }

    const logs = Array.isArray(data.logs) ? data.logs : [];
    logs.forEach((line) => appendLogLine(line));

    if (!response.ok || data.status === "error") {
      const message = data.message || "Cisco reset failed.";
      appendLogLine(`[ERROR] ${message}`);
      updateStatusModal(modal, message, "error");
      return false;
    }

    const message = data.message || "Reset command sent successfully.";
    appendLogLine(`[SUCCESS] ${message}`);
    updateStatusModal(modal, message, "success", true);
    return true;
  } catch (err) {
    if (err.name === "AbortError") {
      const message = "Cisco reset stopped by user.";
      appendLogLine(`[STOPPED] ${message}`);
      showStatusModal(message, "error");
      return false;
    }
    console.error(err);
    const message = err.message || "Cisco reset failed.";
    appendLogLine(`[ERROR] ${message}`);
    showStatusModal(message, "error");
    return false;
  } finally {
    currentAbortController = null;
    if (abortBtn) {
      abortBtn.style.display = "none";
      abortBtn.onclick = null;
      abortBtn.textContent = originalAbortText || "Stop Execution";
    }
    if (resetBtn) {
      resetBtn.disabled = false;
      resetBtn.textContent = originalBtnText || "Reset Cisco Device";
    }
  }
}

// Queue State
let executionQueue = [];
let currentQueueIndex = 0;
let isSequenceRunning = false;
let manualNextHostname = null;
let completedQueueHosts = new Set();
let pollingDisconnect = false;

function escapeConnectionPromptText(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showConnectionPrompt({
  title = "Continue",
  message = "",
  confirmText = "OK",
  cancelText = "Cancel",
  confirmClass = "primary",
} = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay connection-confirm-modal";
    overlay.innerHTML = `
      <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="connectionPromptTitle">
        <div class="modal-header">
          <h3 id="connectionPromptTitle">${escapeConnectionPromptText(title)}</h3>
        </div>
        <div class="modal-body">
          <p style="white-space: pre-wrap;">${escapeConnectionPromptText(message)}</p>
          <p class="hint" style="margin-top: 12px;">Press Enter to continue, or Esc to cancel.</p>
        </div>
        <div class="modal-footer">
          <button type="button" class="secondary" data-action="cancel">${escapeConnectionPromptText(cancelText)}</button>
          <button type="button" class="${escapeConnectionPromptText(confirmClass)}" data-action="confirm">${escapeConnectionPromptText(confirmText)}</button>
        </div>
      </div>
    `;

    const finish = (value) => {
      document.removeEventListener("keydown", handleKeyDown, true);
      overlay.remove();
      resolve(value);
    };

    const handleKeyDown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    };

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish(false);
    });
    overlay.querySelector('[data-action="cancel"]')?.addEventListener("click", () => finish(false));
    overlay.querySelector('[data-action="confirm"]')?.addEventListener("click", () => finish(true));

    document.body.appendChild(overlay);
    document.addEventListener("keydown", handleKeyDown, true);
    setTimeout(() => overlay.querySelector('[data-action="confirm"]')?.focus(), 0);
  });
}

function getSelectedConnectionMode() {
  return document.querySelector('input[name="connType"]:checked')?.value || "serial";
}

function readSshCredentialCache() {
  try {
    const parsed = JSON.parse(localStorage.getItem("sshDeviceCredentials") || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function showSshDevicePrompt(hostname) {
  return new Promise((resolve) => {
    const cache = readSshCredentialCache();
    const deviceCreds = cache[hostname] || {};
    const hostValue = deviceCreds.host || localStorage.getItem("sshHost") || "";
    const userValue = deviceCreds.username || localStorage.getItem("sshUser") || "";
    const portValue = deviceCreds.port || localStorage.getItem("sshPort") || "22";

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay connection-confirm-modal";
    overlay.innerHTML = `
      <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="sshPromptTitle">
        <div class="modal-header">
          <h3 id="sshPromptTitle">SSH Details For ${escapeConnectionPromptText(hostname)}</h3>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label for="sshPromptHost">IP Address / Host</label>
            <input type="text" id="sshPromptHost" value="${escapeConnectionPromptText(hostValue)}" placeholder="192.168.1.1" />
          </div>
          <div class="form-row">
            <div class="form-group">
              <label for="sshPromptUser">Username</label>
              <input type="text" id="sshPromptUser" value="${escapeConnectionPromptText(userValue)}" placeholder="admin" />
            </div>
            <div class="form-group">
              <label for="sshPromptPort">Port</label>
              <input type="text" id="sshPromptPort" value="${escapeConnectionPromptText(portValue)}" placeholder="22" />
            </div>
          </div>
          <div class="form-group">
            <label for="sshPromptPass">Password</label>
            <input type="password" id="sshPromptPass" placeholder="Password" />
          </div>
          <p class="hint" id="sshPromptError" style="display:none;color:var(--color-danger);margin:0;"></p>
        </div>
        <div class="modal-footer">
          <button type="button" class="secondary" data-action="cancel">Cancel</button>
          <button type="button" class="primary" data-action="confirm">Start Collection</button>
        </div>
      </div>
    `;

    const finish = (value) => {
      document.removeEventListener("keydown", handleKeyDown, true);
      overlay.remove();
      resolve(value);
    };

    const confirm = () => {
      const host = overlay.querySelector("#sshPromptHost")?.value.trim() || "";
      const username = overlay.querySelector("#sshPromptUser")?.value.trim() || "";
      const password = overlay.querySelector("#sshPromptPass")?.value || "";
      const port = overlay.querySelector("#sshPromptPort")?.value.trim() || "22";
      const error = overlay.querySelector("#sshPromptError");
      if (!host || !username || !password) {
        if (error) {
          error.textContent = "IP address, username, and password are required.";
          error.style.display = "block";
        }
        return;
      }
      cache[hostname] = { host, username, port };
      localStorage.setItem("sshDeviceCredentials", JSON.stringify(cache));
      localStorage.setItem("sshHost", host);
      localStorage.setItem("sshUser", username);
      localStorage.removeItem("sshPass");
      localStorage.setItem("sshPort", port);
      finish({ host, username, password, port });
    };

    const handleKeyDown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        confirm();
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish(null);
      }
    };

    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish(null);
    });
    overlay.querySelector('[data-action="cancel"]')?.addEventListener("click", () => finish(null));
    overlay.querySelector('[data-action="confirm"]')?.addEventListener("click", confirm);

    document.body.appendChild(overlay);
    document.addEventListener("keydown", handleKeyDown, true);
    setTimeout(() => overlay.querySelector("#sshPromptHost")?.focus(), 0);
  });
}

function setupConnectionKeyboardShortcuts() {
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    if (document.querySelector(".connection-confirm-modal")) return;

    if (event.key === "Escape") {
      const abortBtn = document.getElementById("abortExecutionBtn");
      const abortVisible =
        abortBtn &&
        !abortBtn.disabled &&
        abortBtn.style.display !== "none" &&
        abortBtn.offsetParent !== null;

      if (abortVisible) {
        event.preventDefault();
        abortBtn.click();
      }
      return;
    }

    if (event.key !== "Enter") return;

    const tag = (event.target?.tagName || "").toUpperCase();
    if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag)) return;

    const doneStudentBtn = document.getElementById("doneStudentBtn");
    if (doneStudentBtn && !doneStudentBtn.disabled) {
      event.preventDefault();
      doneStudentBtn.click();
    }
  });
}

function getStudentProgressState() {
  let total = parseInt(localStorage.getItem("sessionStudentsCount") || "0", 10);
  let completed = [];

  try {
    completed = JSON.parse(localStorage.getItem("completedStudents") || "[]");
  } catch (_) {
    completed = [];
  }

  let sessionPath = localStorage.getItem("sessionPath") || "";
  const basePath = localStorage.getItem("basePath");
  if (!sessionPath && basePath && pathModule) {
    sessionPath = pathModule.dirname(basePath);
    localStorage.setItem("sessionPath", sessionPath);
  }

  if ((!total || Number.isNaN(total)) && sessionPath) {
    try {
      if (require("fs").existsSync(sessionPath)) {
        const dirs = require("fs")
          .readdirSync(sessionPath, { withFileTypes: true })
          .filter((d) => d.isDirectory())
          .map((d) => d.name);
        total = dirs.length;
        localStorage.setItem("sessionStudentsCount", String(total));
        completed = completed.filter((id) => dirs.includes(id));
        localStorage.setItem("completedStudents", JSON.stringify(completed));
      }
    } catch (_) {
      total = 0;
    }
  }

  return {
    total,
    completed,
    completedCount: completed.length,
    allDone: total > 0 && completed.length >= total,
  };
}

function updateDoneStudentButtonLabel() {
  const doneStudentBtn = document.getElementById("doneStudentBtn");
  if (!doneStudentBtn) return;
  const { allDone } = getStudentProgressState();
  doneStudentBtn.textContent = allDone ? "Start Grading" : "Next Student";
}

async function loadTemplateDevicesForConnection() {
  const parseDevices = (raw) => {
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? parsed
        : null;
    } catch (_) {
      return null;
    }
  };

  const templateName =
    localStorage.getItem("templateName") ||
    localStorage.getItem("activeTemplateName");

  if (templateName) {
    try {
      const res = await fetch(`${API_ROOT}/api/templates/${encodeURIComponent(templateName)}`);
      const data = await res.json();
      if (res.ok && data.status === "ok" && data.devices_meta && Object.keys(data.devices_meta).length) {
        localStorage.setItem("templateDevices", JSON.stringify(data.devices_meta));
        localStorage.setItem("activeTemplateName", templateName);
        localStorage.setItem("activeTemplateDevices", JSON.stringify(data.devices_meta));
        return data.devices_meta;
      }
    } catch (err) {
      console.warn("Could not reload template devices:", err);
    }
  }

  const activeCached = parseDevices(localStorage.getItem("activeTemplateDevices"));
  if (activeCached && Object.keys(activeCached).length) {
    localStorage.setItem("templateDevices", JSON.stringify(activeCached));
    return activeCached;
  }

  const cached = parseDevices(localStorage.getItem("templateDevices"));
  if (cached && Object.keys(cached).length) return cached;

  return {};
}

function renderDeviceQueue(devicesMeta, deviceQueueContainer) {
  deviceQueueContainer.innerHTML = "";
  executionQueue = Object.keys(devicesMeta || {}).map(hostname => ({
    hostname,
    commands: devicesMeta[hostname] || [],
    status: "pending" // pending, running, done
  }));

  if (!executionQueue.length) {
    deviceQueueContainer.innerHTML = `<div class="hint">No devices found. Return to Device Setup and save the template setup.</div>`;
    const startBtn = document.getElementById("startSequenceBtn");
    if (startBtn) startBtn.disabled = true;
    return;
  }

  const startBtn = document.getElementById("startSequenceBtn");
  if (startBtn) startBtn.disabled = false;

  executionQueue.forEach((device, index) => {
    // Wrapper holds row + collapsible dropdown together
    const wrapper = document.createElement("div");
    wrapper.className = "queue-item-wrapper";

    const row = document.createElement("div");
    row.className = `queue-item ${index === 0 ? "active-queue-item" : ""}`;
    row.id = `q-${device.hostname}`;
    row.style.cssText = `
       background: var(--color-bg-card);
       border: 1px solid var(--color-border);
       padding: 10px 14px;
       border-radius: 6px;
       display: flex;
       justify-content: space-between;
       align-items: center;
       cursor: pointer;
       user-select: none;
    `;

    row.innerHTML = `
      <div>
        <strong>${device.hostname}</strong>
        <div style="font-size: 0.8rem; color: var(--color-muted);">
          ${device.commands.length} command${device.commands.length !== 1 ? "s" : ""}
          <span class="queue-preview-hint" style="color:var(--color-primary);">- click to preview</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;">
        <span class="q-chevron" style="font-size:0.7rem;color:var(--color-muted);transition:transform 0.2s;">▼</span>
        <span class="q-badge" style="font-size:0.85rem;font-weight:bold;color:var(--color-muted);">WAITING</span>
        <button type="button" class="queue-start-btn" data-hostname="${device.hostname}">Collect this</button>
      </div>
    `;

    if (index === 0) {
      row.style.borderColor = "var(--color-primary)";
      row.style.backgroundColor = "rgba(31, 59, 115, 0.05)";
    }

    // Build command dropdown (hidden by default)
    const dropdown = document.createElement("div");
    dropdown.className = "queue-cmd-dropdown";
    dropdown.style.display = "none";

    if (!device.commands.length) {
      dropdown.innerHTML = `<span class="queue-cmd-dropdown__empty">No commands configured for this device.</span>`;
    } else {
      dropdown.innerHTML = device.commands.map((cmd, i) =>
        `<div class="queue-cmd-dropdown__item">
           <span class="queue-cmd-dropdown__num">${i + 1}</span>
           <code class="queue-cmd-dropdown__cmd">${cmd}</code>
         </div>`
      ).join("");
    }

    wrapper.appendChild(row);
    wrapper.appendChild(dropdown);
    deviceQueueContainer.appendChild(wrapper);

    row.querySelector(".queue-start-btn")?.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (isSequenceRunning) {
        appendLogLine(`[INFO] A sequence is already running. Stop it before starting from ${device.hostname}.`);
        return;
      }

      const proceed = await showConnectionPrompt({
        title: "Start From Device",
        message: `Start collection from ${device.hostname} and skip earlier queued devices?`,
        confirmText: "Collect this",
      });
      if (!proceed) return;

      manualNextHostname = device.hostname;
      startQueueFromSelectedDevice();
    });

    row.addEventListener("click", async () => {
      if (isSequenceRunning) {
        if (device.hostname !== executionQueue[currentQueueIndex]?.hostname) {
          appendLogLine(`[INFO] Preview only. ${executionQueue[currentQueueIndex]?.hostname || "A device"} is currently running.`);
        }
      }

      if (!isSequenceRunning) {
        manualNextHostname = device.hostname;
      }

      // Toggle dropdown
      const isOpen = dropdown.style.display !== "none";
      const chevron = row.querySelector(".q-chevron");
      const hint = row.querySelector(".queue-preview-hint");

      if (isOpen) {
        dropdown.style.display = "none";
        if (chevron) chevron.style.transform = "";
        if (hint) hint.textContent = "- click to preview";
        row.style.borderBottomLeftRadius = "6px";
        row.style.borderBottomRightRadius = "6px";
      } else {
        dropdown.style.display = "block";
        if (chevron) chevron.style.transform = "rotate(180deg)";
        if (hint) hint.textContent = "- click to close";
        row.style.borderBottomLeftRadius = "0";
        row.style.borderBottomRightRadius = "0";
      }
    });
  });
}

function startQueueFromSelectedDevice() {
  const startSequenceBtn = document.getElementById("startSequenceBtn");
  const doneStudentBtn = document.getElementById("doneStudentBtn");
  const queueStatus = document.getElementById("queueStatus");

  if (isSequenceRunning) return;
  if (!ensureDirectoryConfigured()) return;
  if (!executionQueue.length) {
    alert("No devices found. Return to Device Setup and save the template setup first.");
    return;
  }

  isSequenceRunning = true;
  currentQueueIndex = 0;
  completedQueueHosts = new Set();
  if (startSequenceBtn) {
    startSequenceBtn.disabled = true;
    startSequenceBtn.textContent = "Sequence Running...";
  }
  if (doneStudentBtn) doneStudentBtn.disabled = true;
  if (queueStatus) {
    queueStatus.textContent = "Running";
    queueStatus.style.color = "var(--color-primary)";
  }
  runNextDeviceInQueue();
}

async function setupConnectionPage() {
  loadNavbar();
  setupConnectionKeyboardShortcuts();

  // Build Execution Queue UI
  const deviceQueueContainer = document.getElementById("deviceQueueContainer");
  const queueStatus = document.getElementById("queueStatus");
  const startSequenceBtn = document.getElementById("startSequenceBtn");
  const doneStudentBtn = document.getElementById("doneStudentBtn");
  updateDoneStudentButtonLabel();

  const form = document.getElementById("connectionForm");
  const radios = document.querySelectorAll('input[name="connType"]');
  const presetRadios = document.querySelectorAll('input[name="serialPreset"]');
  const backBtn = document.getElementById("backToDirectoryBtn");

  if (form) {
    const savedConn = localStorage.getItem("connection");
    const targetRadio = Array.from(radios).find((r) => r.value === savedConn);
    if (targetRadio) targetRadio.checked = true;
    else {
      const serialRadio = Array.from(radios).find((r) => r.value === "serial");
      if (serialRadio) serialRadio.checked = true;
    }
    toggleConnectionFields();

    form.addEventListener("submit", (evt) => evt.preventDefault());

    document
      .getElementById("resetDeviceBtn")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        resetCiscoDevice({ triggerButton: evt.currentTarget });
      });

    document
      .getElementById("doneStudentBtn")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        const { allDone } = getStudentProgressState();
        if (allDone) {
          localStorage.setItem("autoRunResults", "true");
          goTo("results.html");
        } else {
          goTo("directory.html");
        }
      });
  }

  radios.forEach((r) => r.addEventListener("change", toggleConnectionFields));

  presetRadios.forEach((r) =>
    r.addEventListener("change", () => applySerialPreset(r.value))
  );

  const savedPort = localStorage.getItem("serialPort");
  if (savedPort) {
    document.querySelector('input[name="serialPreset"][value="custom"]').checked = true;
    applySerialPreset("custom");
    document.getElementById("serialPort").value = savedPort;
  } else {
    applySerialPreset("linux_usb");
  }

  document.getElementById("sshHost").value = localStorage.getItem("sshHost") || "";
  document.getElementById("sshUser").value = localStorage.getItem("sshUser") || "";
  document.getElementById("sshPass").value = localStorage.getItem("sshPass") || "";
  const sshPortInput = document.getElementById("sshPort");
  if (sshPortInput) {
    sshPortInput.value = localStorage.getItem("sshPort") || "22";
  }

  if (backBtn) {
    backBtn.addEventListener("click", () => goTo("directory.html"));
  }

  document
    .getElementById("clearLogBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      clearExecutionLog();
    });

  startSequenceBtn?.addEventListener("click", () => {
     manualNextHostname = null;
     startQueueFromSelectedDevice();
  });

  if (deviceQueueContainer) {
    deviceQueueContainer.innerHTML = `<div class="hint">Loading devices...</div>`;
    if (startSequenceBtn) startSequenceBtn.disabled = true;
    const devicesMeta = await loadTemplateDevicesForConnection();
    renderDeviceQueue(devicesMeta, deviceQueueContainer);
  }
}

function getNextQueueIndex(preferredHostname) {
  if (preferredHostname) {
    const idx = executionQueue.findIndex(
      (d) => d.hostname === preferredHostname && !completedQueueHosts.has(d.hostname)
    );
    if (idx !== -1) return idx;
  }
  for (let i = 0; i < executionQueue.length; i++) {
    const hostname = executionQueue[i].hostname;
    if (!completedQueueHosts.has(hostname)) return i;
  }
  return -1;
}

async function runNextDeviceInQueue() {
  const preferred = manualNextHostname;
  manualNextHostname = null;
  const nextIndex = getNextQueueIndex(preferred);
  if (nextIndex === -1) {
    // All done!
    isSequenceRunning = false;
    const finishedStudent = localStorage.getItem("studentId") || "";
    localStorage.setItem("lastExecutedStudent", finishedStudent);
    if (finishedStudent) {
      let completed = [];
      try {
        completed = JSON.parse(localStorage.getItem("completedStudents") || "[]");
      } catch (_) {
        completed = [];
      }
      if (!completed.includes(finishedStudent)) {
        completed.push(finishedStudent);
        localStorage.setItem("completedStudents", JSON.stringify(completed));
      }
    }
    document.getElementById("startSequenceBtn").textContent = "Sequence Finished";
    document.getElementById("queueStatus").textContent = "Completed";
    document.getElementById("queueStatus").style.color = "var(--color-success, #28a745)";
    document.getElementById("doneStudentBtn").disabled = false;
    updateDoneStudentButtonLabel();
    const doneStudentBtn = document.getElementById("doneStudentBtn");
    const proceed = await showConnectionPrompt({
      title: "All Devices Completed",
      message: "All devices completed for this student.",
      confirmText: doneStudentBtn?.textContent || "Continue",
      cancelText: "Stay Here",
    });
    if (proceed) {
      doneStudentBtn?.click();
    }
    return;
  }

  currentQueueIndex = nextIndex;
  const currentDevice = executionQueue[currentQueueIndex];
  const row = document.getElementById(`q-${currentDevice.hostname}`);
  const badge = row.querySelector(".q-badge");
  const connectionMode = getSelectedConnectionMode();

  badge.textContent = connectionMode === "ssh" ? "SSH DETAILS" : "PLUG IN NOW";
  badge.style.color = "#ff9800"; // Orange attention
  row.style.borderColor = "#ff9800";
  row.style.backgroundColor = "rgba(255, 152, 0, 0.05)";

  // Set the current target context so execution scripts use it
  localStorage.setItem("connectedHostname", currentDevice.hostname);
  setStoredCommandsFromDevice(currentDevice.hostname);

  let sshDetailsForCurrentRun = null;
  if (connectionMode === "ssh") {
    sshDetailsForCurrentRun = await showSshDevicePrompt(currentDevice.hostname);
    if (!sshDetailsForCurrentRun) {
      badge.textContent = "WAITING";
      badge.style.color = "var(--color-muted)";
      document.getElementById("startSequenceBtn").disabled = false;
      isSequenceRunning = false;
      return;
    }
  } else {
    // Require operator confirmation before running the next serial device
    const proceed = await showConnectionPrompt({
      title: "Ready For Next Device",
      message: `Plug in ${currentDevice.hostname}, then press Enter to start collecting logs.`,
      confirmText: "Start Collection",
    });
    if (!proceed) {
      badge.textContent = "WAITING";
      badge.style.color = "var(--color-muted)";
      document.getElementById("startSequenceBtn").disabled = false;
      isSequenceRunning = false;
      return;
    }
  }

  // Run commands directly (do not pre-connect; /api/execute manages serial)
  const abortBtn = document.getElementById("abortExecutionBtn");

  const runExecute = async (forceSkipHostname = false) => {
    badge.textContent = "EXECUTING...";
    badge.style.color = "var(--color-primary)";

    // Create abort controller for this execution
    currentAbortController = new AbortController();
    if (abortBtn) abortBtn.style.display = "inline-block";

    try {
         const directoryMode = localStorage.getItem("directoryMode") || "create";
         const basePath = localStorage.getItem("basePath");
         let classroom = localStorage.getItem("classroom") || localStorage.getItem("examName") || "";
         let tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId") || "";
         let timeSlot = localStorage.getItem("timeSlot") || "";
         let studentId = localStorage.getItem("studentId") || localStorage.getItem("selectedStudent") || "";
         if (basePath && pathModule && (!classroom || !tutorName || !timeSlot || !studentId)) {
           const parts = basePath.split(pathModule.sep).filter(Boolean);
           if (parts.length >= 4) {
             classroom = classroom || parts[parts.length - 4];
             tutorName = tutorName || parts[parts.length - 3];
             timeSlot = timeSlot || parts[parts.length - 2];
             studentId = studentId || parts[parts.length - 1];
           }
         }
         const payload = {
           mode: connectionMode,
           connection: connectionMode,
           deviceId: currentDevice.hostname,
           commands: currentDevice.commands,
           classroom: classroom || "unknown",
           tutor_name: tutorName || "unknown",
           time_slot: timeSlot || "unknown",
           student_id: studentId || "unknown",
           exam_name: classroom || "unknown",
           session_id: tutorName || "unknown",
           log_mode: directoryMode,
         };
         if (forceSkipHostname) {
           payload.skip_hostname_check = true;
         }
         if (directoryMode === "existing" && basePath) {
           payload.log_dir = basePath;
         }
         if (payload.mode === "ssh") {
           payload.ssh = {
             host: sshDetailsForCurrentRun.host,
             username: sshDetailsForCurrentRun.username,
             password: sshDetailsForCurrentRun.password,
             port: sshDetailsForCurrentRun.port,
           };
         } else {
           const portInput = document.getElementById("serialPort");
           let currentPort = portInput ? portInput.value.trim() : "";
           if (!currentPort) {
             currentPort = localStorage.getItem("serialPort") || SERIAL_PRESETS.linux_usb;
           }
           localStorage.setItem("serialPort", currentPort);
           payload.serial = { port: currentPort };
         }
         const res = await fetch(`${API_ROOT}/api/execute`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: currentAbortController.signal,
         });
         if (!res.ok) {
            const txt = await res.text();
            throw new Error(txt || "Execution failed");
         }
         if (!res.body) {
            throw new Error("Execution failed: no response body.");
         }

         const reader = res.body.getReader();
         const dec = new TextDecoder();
         let buffer = "";
         let hadError = false;
         let hostnameMismatchMsg = null;

         while (true) {
           const { done, value } = await reader.read();
           if (done) break;
           buffer += dec.decode(value, { stream: true });
           const lines = buffer.split("\n");
           buffer = lines.pop() || "";
           for (const line of lines) {
             if (!line.trim()) continue;
             try {
               const obj = JSON.parse(line);
               if (obj.type === "error") {
                 hadError = true;
                 appendLogLine(`[ERROR] ${obj.msg || "Execution error"}`);
                 if (obj.error_code === "HOSTNAME_MISMATCH") {
                   hostnameMismatchMsg = obj.msg;
                 }
               } else if (obj.type === "progress") {
                 appendLogLine(`[${nowTimestamp()}] ${obj.msg}`);
               } else if (obj.type === "result") {
                 appendLogLine(`[RESULT] ${obj.msg || "Done"}`);
               } else if (obj.type === "done") {
                 appendLogLine(`[DONE] ${obj.msg || "Finished"}`);
               }
             } catch (_) {
               appendLogLine(line.trim());
             }
           }
         }

         if (abortBtn) abortBtn.style.display = "none";
         currentAbortController = null;

         if (!hadError) {
            badge.textContent = "DONE";
            badge.style.color = "var(--color-success, #28a745)";
            row.style.borderColor = "var(--color-success, #28a745)";
            row.style.backgroundColor = "rgba(40, 167, 69, 0.05)";

            appendLogLine(`[SUCCESS] Finished ${currentDevice.hostname}`);
            completedQueueHosts.add(currentDevice.hostname);
            runNextDeviceInQueue(); // Recurse next
         } else if (hostnameMismatchMsg) {
            // Special handling: offer to continue despite mismatch
            badge.textContent = "MISMATCH";
            badge.style.color = "#ff9800";
            row.style.borderColor = "#ff9800";
            row.style.backgroundColor = "rgba(255, 152, 0, 0.05)";

            const continueAnyway = await showConnectionPrompt({
              title: "Hostname Mismatch",
              message: `${hostnameMismatchMsg}\n\nDo you want to continue anyway?\nLogs will be saved under the selected device name "${currentDevice.hostname}".`,
              confirmText: "Continue Anyway",
              cancelText: "Stop",
            });
            if (continueAnyway) {
              appendLogLine(`[INFO] User chose to continue despite hostname mismatch.`);
              runExecute(true); // Retry with skip flag
            } else {
              appendLogLine(`[INFO] User chose to stop due to hostname mismatch.`);
              badge.textContent = "SKIPPED";
              badge.style.color = "var(--color-muted)";
              document.getElementById("startSequenceBtn").disabled = false;
              isSequenceRunning = false;
            }
         } else {
            throw new Error("Execution failed during command run.");
         }
      } catch (e) {
         if (abortBtn) abortBtn.style.display = "none";
         currentAbortController = null;

         if (e.name === "AbortError") {
           // User clicked Stop Execution
           badge.textContent = "STOPPED";
           badge.style.color = "var(--color-danger)";
           appendLogLine(`[STOPPED] Execution aborted by user.`);
           document.getElementById("startSequenceBtn").disabled = false;
           isSequenceRunning = false;
           // Tell backend to abort too
           try { fetch(`${API_ROOT}/api/abort`, { method: "POST" }); } catch (_) {}
           return;
         }

         badge.textContent = "ERROR";
         badge.style.color = "var(--color-danger)";
         console.error(e);
         alert(`Execution failed on ${currentDevice.hostname}. Stopping queue.`);
         document.getElementById("startSequenceBtn").disabled = false;
         isSequenceRunning = false;
      }
  };

  // Wire abort button
  if (abortBtn) {
    abortBtn.onclick = () => {
      if (currentAbortController) {
        currentAbortController.abort();
      }
    };
  }

  runExecute();
}

// Transparently try to connect without blocking the UI heavily
async function attemptTransparentConnection() {
  const type = document.querySelector('input[name="connType"]:checked');
  if (!type || type.value !== "serial") return false; // SSH polling unsupported for now

  const portInput = document.getElementById("serialPort");
  const port = portInput ? portInput.value.trim() : "/dev/ttyUSB0";

  try {
     const controller = new AbortController();
     setTimeout(() => controller.abort(), 2500); // Fast timeout for serial polling
     const res = await fetch(`${API_ROOT}/api/connect`, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ connection: "serial", mode: "serial", serial: { port } }),
       signal: controller.signal
     });

     if (res.ok) {
       // It stream-responses, but just knowing the socket opened is enough success for the queue.
       return true;
     }
  } catch (e) {
    // Ignore aborts and fails while polling
  }
  return false;
}



document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("connectionForm")) {
    setupConnectionPage().catch((err) => {
      console.error("Connection page setup failed:", err);
      const queue = document.getElementById("deviceQueueContainer");
      if (queue) {
        queue.innerHTML = `<div class="hint">Connection page setup failed: ${err.message || err}</div>`;
      }
    });
  }
});
