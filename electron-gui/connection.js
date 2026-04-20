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

  try {
    if (resetBtn) {
      resetBtn.disabled = true;
      resetBtn.textContent = "Resetting...";
    }

    appendLogLine(`[${nowTimestamp()}] Starting Cisco reset on ${port}...`);
    const modal = showStatusModal("Resetting device... Please wait.", "pending");
    const response = await fetch(`${API_ROOT}/api/reset_device`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        connection: "serial",
        mode: "serial",
        device_type: deviceType,
        serial: { port },
      }),
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
    console.error(err);
    const message = err.message || "Cisco reset failed.";
    appendLogLine(`[ERROR] ${message}`);
    showStatusModal(message, "error");
    return false;
  } finally {
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
    `;

    row.innerHTML = `
      <div>
        <strong>${device.hostname}</strong>
        <div style="font-size: 0.8rem; color: var(--color-muted);">${device.commands.length} commands</div>
      </div>
      <span class="q-badge" style="font-size: 0.85rem; font-weight: bold; color: var(--color-muted);">WAITING</span>
    `;

    if (index === 0) {
      row.style.borderColor = "var(--color-primary)";
      row.style.backgroundColor = "rgba(31, 59, 115, 0.05)";
    }

    row.addEventListener("click", () => {
      if (isSequenceRunning) {
        return;
      }
      const proceed = confirm(`Start with ${device.hostname} now?`);
      if (!proceed) return;
      manualNextHostname = device.hostname;
      document.getElementById("startSequenceBtn")?.click();
    });

    deviceQueueContainer.appendChild(row);
  });
}

async function setupConnectionPage() {
  loadNavbar();

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
          goTo("index.html");
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
    backBtn.addEventListener("click", () => goTo("index.html"));
  }

  document
    .getElementById("clearLogBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      clearExecutionLog();
    });

  startSequenceBtn?.addEventListener("click", () => {
     if (isSequenceRunning) return;
     if (!ensureDirectoryConfigured()) return;
     if (!executionQueue.length) {
       alert("No devices found. Return to Device Setup and save the template setup first.");
       return;
     }
     isSequenceRunning = true;
     currentQueueIndex = 0;
     completedQueueHosts = new Set();
     startSequenceBtn.disabled = true;
     startSequenceBtn.textContent = "Sequence Running...";
     doneStudentBtn.disabled = true;
     queueStatus.textContent = "Running";
     queueStatus.style.color = "var(--color-primary)";
     runNextDeviceInQueue();
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
    alert("All devices completed. Please hit Done to proceed to the next student.");
    return;
  }

  currentQueueIndex = nextIndex;
  const currentDevice = executionQueue[currentQueueIndex];
  const row = document.getElementById(`q-${currentDevice.hostname}`);
  const badge = row.querySelector(".q-badge");
  
  badge.textContent = "PLUG IN NOW";
  badge.style.color = "#ff9800"; // Orange attention
  row.style.borderColor = "#ff9800";
  row.style.backgroundColor = "rgba(255, 152, 0, 0.05)";

  // Set the current target context so execution scripts use it
  localStorage.setItem("connectedHostname", currentDevice.hostname);
  setStoredCommandsFromDevice(currentDevice.hostname);

  // Require operator confirmation before running the next device
  const proceed = confirm(
    `Plug in ${currentDevice.hostname} and click OK to start collecting logs.`
  );
  if (!proceed) {
    badge.textContent = "WAITING";
    badge.style.color = "var(--color-muted)";
    document.getElementById("startSequenceBtn").disabled = false;
    isSequenceRunning = false;
    return;
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
         const payload = {
           deviceId: currentDevice.hostname,
           commands: currentDevice.commands,
           student_id: localStorage.getItem("studentId") || localStorage.getItem("selectedStudent") || "unknown",
           exam_name: localStorage.getItem("examName") || "unknown",
           session_id: localStorage.getItem("sessionId") || "unknown",
           log_mode: directoryMode,
         };
         if (forceSkipHostname) {
           payload.skip_hostname_check = true;
         }
         if (directoryMode === "existing" && basePath) {
           payload.log_dir = basePath;
         }
         const portInput = document.getElementById("serialPort");
         let currentPort = portInput ? portInput.value.trim() : "";
         if (!currentPort) {
           currentPort = localStorage.getItem("serialPort") || SERIAL_PRESETS.linux_usb;
         }
         payload.serial = { port: currentPort };
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

            const continueAnyway = confirm(
              `${hostnameMismatchMsg}\n\nDo you want to continue anyway?\nLogs will be saved under the selected device name "${currentDevice.hostname}".`
            );
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
