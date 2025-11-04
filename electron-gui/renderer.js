// electron-gui/renderer.js

const API_ROOT = "http://127.0.0.1:5050";
const SERIAL_PRESETS = {
  linux: "/dev/ttyUSB0",
  windows: "COM3",
  mac: "/dev/cu.usbserial-10",
};

let ipcRenderer = null;
let pathModule = null;

try {
  ipcRenderer = require("electron").ipcRenderer;
} catch (err) {
  console.debug("ipcRenderer not available:", err);
}

try {
  pathModule = require("path");
} catch (err) {
  pathModule = null;
}

let selectedExistingPath = null;
let selectedExistingDisplay = null;
let statusModalOverlay = null;
let statusModalMessageEl = null;
let statusModalCloseBtn = null;
let statusModalHideTimeout = null;
let autoRunAfterConnect = false;

function nowTimestamp() {
  return new Date().toLocaleTimeString();
}

function appendLogLine(message) {
  const log = document.getElementById("log");
  if (!log) return;
  log.innerText += `${message}\n`;
  log.scrollTop = log.scrollHeight;
}

function goTo(page) {
  window.location.href = page;
}
window.goTo = goTo;

function loadNavbar() {
  const container = document.getElementById("navbarContainer");
  if (!container) return;
  fetch("navbar.html")
    .then((res) => res.text())
    .then((html) => {
      container.outerHTML = html;
    })
    .catch((err) => console.error("Failed to load navbar:", err));
}

async function fetchJson(path, options = {}) {
  const res = await fetch(`${API_ROOT}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    // ignore parse errors; handled below.
  }
  if (!res.ok || data.status === "error") {
    const message =
      data.message || res.statusText || "Request failed. Please try again.";
    throw new Error(message);
  }
  return data;
}

function setDirectoryInfo({
  exam_name,
  session_id,
  student_id,
  path,
  mode,
  display,
}) {
  localStorage.setItem("examName", exam_name);
  localStorage.setItem("sessionId", session_id);
  localStorage.setItem("studentId", student_id);
  if (path) localStorage.setItem("basePath", path);
  else localStorage.removeItem("basePath");
  localStorage.setItem("directoryMode", mode || "create");
  if (display) localStorage.setItem("directoryDisplay", display);
  else localStorage.removeItem("directoryDisplay");
}

function ensureDirectoryConfigured() {
  const exam = localStorage.getItem("examName");
  const session = localStorage.getItem("sessionId");
  const student = localStorage.getItem("studentId");
  if (!exam || !session || !student) {
    alert("Please set up a directory before continuing.");
    goTo("index.html");
    return false;
  }
  return true;
}

function ensureStatusModalElements() {
  if (statusModalOverlay) return statusModalOverlay;

  statusModalOverlay = document.createElement("div");
  statusModalOverlay.id = "statusModalOverlay";
  statusModalOverlay.className = "status-modal-overlay";

  const box = document.createElement("div");
  box.className = "status-modal-box";

  const spinner = document.createElement("div");
  spinner.className = "status-modal-spinner";

  statusModalMessageEl = document.createElement("p");
  statusModalMessageEl.className = "status-modal-message";

  statusModalCloseBtn = document.createElement("button");
  statusModalCloseBtn.type = "button";
  statusModalCloseBtn.className = "status-modal-close secondary";
  statusModalCloseBtn.textContent = "Close";
  statusModalCloseBtn.addEventListener("click", () => hideStatusModal());

  box.append(spinner, statusModalMessageEl, statusModalCloseBtn);
  statusModalOverlay.appendChild(box);
  document.body.appendChild(statusModalOverlay);
  return statusModalOverlay;
}

function showStatusModal(message, state = "info") {
  const overlay = ensureStatusModalElements();
  if (statusModalHideTimeout) {
    clearTimeout(statusModalHideTimeout);
    statusModalHideTimeout = null;
  }
  overlay.dataset.state = state;
  statusModalMessageEl.textContent = message;
  statusModalCloseBtn.style.display = state === "pending" ? "none" : "inline-block";
  overlay.classList.add("visible");
  return overlay;
}

function updateStatusModal(overlay, message, state = "info", autoHide = false) {
  const modal = overlay || ensureStatusModalElements();
  modal.dataset.state = state;
  statusModalMessageEl.textContent = message;
  statusModalCloseBtn.style.display = state === "pending" ? "none" : "inline-block";
  if (autoHide) {
    statusModalHideTimeout = setTimeout(() => hideStatusModal(), 1500);
  }
}

function hideStatusModal() {
  if (statusModalHideTimeout) {
    clearTimeout(statusModalHideTimeout);
    statusModalHideTimeout = null;
  }
  if (statusModalOverlay) {
    statusModalOverlay.classList.remove("visible");
  }
}

// -----------------------------
// Directory setup page
// -----------------------------

function setupWelcomePage() {
  loadNavbar();
  const startBtn = document.getElementById("startSetupBtn");
  if (startBtn) {
    startBtn.addEventListener("click", () => goTo("index.html"));
  }
}

function deriveDirectoryDisplay(dirPath) {
  if (!dirPath) return null;
  if (!pathModule) return dirPath;
  const parts = dirPath.split(pathModule.sep).filter(Boolean);
  if (parts.length >= 3) {
    return parts.slice(-3).join("/");
  }
  return dirPath;
}

function setSelectedExistingDirectory(pathValue, displayValue) {
  selectedExistingPath = pathValue || null;
  const display = displayValue || (pathValue ? deriveDirectoryDisplay(pathValue) : null);
  const label = document.getElementById("selectedDirectoryLabel");
  if (label) {
    if (selectedExistingPath) {
      label.textContent = display || selectedExistingPath;
      label.classList.add("has-value");
    } else {
      label.textContent = "No directory selected";
      label.classList.remove("has-value");
    }
  }
  const infoBox = document.getElementById("existingInfoBox");
  if (infoBox && !selectedExistingPath) {
    infoBox.classList.add("hidden");
    infoBox.innerHTML = "";
  }
}

async function openExistingDirectoryDialog() {
  if (ipcRenderer) {
    try {
      const selectedPath = await ipcRenderer.invoke("select-directory");
      if (selectedPath) {
        setSelectedExistingDirectory(selectedPath);
        const infoBox = document.getElementById("existingInfoBox");
        if (infoBox) {
          infoBox.classList.remove("hidden");
          infoBox.innerHTML = `
            <strong>Selected:</strong> ${
              deriveDirectoryDisplay(selectedPath) || selectedPath
            }<br/>
            <span class="hint">Click "Use Selected" to continue.</span>
          `;
        }
      }
      return;
    } catch (err) {
      console.error(err);
      alert(`Failed to open directory picker: ${err.message}`);
      return;
    }
  }

  const manual = prompt("Enter the directory path:");
  if (manual) {
    setSelectedExistingDirectory(manual);
    const infoBox = document.getElementById("existingInfoBox");
    if (infoBox) {
      infoBox.classList.remove("hidden");
      infoBox.innerHTML = `
        <strong>Selected:</strong> ${deriveDirectoryDisplay(manual) || manual}<br/>
        <span class="hint">Click "Use Selected" to continue.</span>
      `;
    }
  }
}

function parseStudentFile(content) {
  const lines = content.split(/\r?\n/);
  const students = [];
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const [id, name] = trimmed.split(",", 2);
    const studentId = (id || "").trim();
    if (!studentId) return;
    students.push({ id: studentId, name: (name || "").trim() });
  });
  return students;
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Failed to read file."));
    reader.onload = () => resolve(reader.result);
    reader.readAsText(file);
  });
}

async function handleCreateDirectory(event) {
  event.preventDefault();
  const exam = document.getElementById("createExamName").value.trim();
  const session = document.getElementById("createSessionId").value.trim();
  const student = document.getElementById("createStudentId").value.trim();
  if (!exam || !session || !student) {
    alert("Please complete all fields for the new directory.");
    return;
  }

  try {
    const data = await fetchJson("/api/create_directory", {
      method: "POST",
      body: JSON.stringify({
        examName: exam,
        sessionId: session,
        studentId: student,
      }),
    });
    setDirectoryInfo({
      exam_name: data.exam_name,
      session_id: data.session_id,
      student_id: data.student_id,
      path: data.path,
      mode: "create",
      display: `${data.exam_name}/${data.session_id}/${data.student_id}`,
    });
    alert(data.message);
    goTo("commands.html");
  } catch (err) {
    console.error(err);
    alert(`Failed to create directory: ${err.message}`);
  }
}

async function handleUseExistingDirectory() {
  if (!selectedExistingPath) {
    alert("Please choose a directory first.");
    return;
  }

  try {
    const data = await fetchJson("/api/select_directory", {
      method: "POST",
      body: JSON.stringify({
        existingPath: selectedExistingPath,
      }),
    });
    setDirectoryInfo({
      exam_name: data.exam_name,
      session_id: data.session_id,
      student_id: data.student_id,
      path: data.path,
      mode: "existing",
      display: `${data.exam_name}/${data.session_id}/${data.student_id}`,
    });
    setSelectedExistingDirectory(
      data.path,
      `${data.exam_name}/${data.session_id}/${data.student_id}`
    );
    alert(data.message);
    goTo("commands.html");
  } catch (err) {
    console.error(err);
    alert(`Failed to use directory: ${err.message}`);
  }
}

async function handleBulkCreate(event) {
  event.preventDefault();
  const exam = document.getElementById("bulkExamName").value.trim();
  const session = document.getElementById("bulkSessionId").value.trim();
  const fileInput = document.getElementById("bulkFile");
  const resultsBox = document.getElementById("bulkResults");

  if (!fileInput.files.length) {
    alert("Please select a CSV or TXT file.");
    return;
  }
  if (!exam || !session) {
    alert("Please provide exam and session details for bulk creation.");
    return;
  }

  try {
    const content = await readFileAsText(fileInput.files[0]);
    const students = parseStudentFile(content);
    if (!students.length) {
      alert("No student IDs detected in the file.");
      return;
    }

    const data = await fetchJson("/api/directories/bulk", {
      method: "POST",
      body: JSON.stringify({ examName: exam, sessionId: session, students }),
    });

    const created = data.created || [];
    if (!created.length) {
    resultsBox.classList.remove("hidden");
    resultsBox.innerHTML =
      "<strong>No directories were created. Check the file contents.</strong>";
    return;
  }

  resultsBox.classList.remove("hidden");
  resultsBox.innerHTML = `
      <strong>Created ${created.length} directories:</strong>
      <ul>${created
        .map((dir) => `<li>${dir.display}</li>`)
        .join("")}</ul>
      <p>The first directory is pre-selected for you below.</p>
    `;
    const primary = created[0];
    setSelectedExistingDirectory(primary.path, primary.display);
    const infoBox = document.getElementById("existingInfoBox");
    if (infoBox) {
      infoBox.classList.remove("hidden");
      infoBox.innerHTML = `
        <strong>Ready to use:</strong> ${primary.display}<br/>
        <span class="hint">Click "Use Selected" or choose another folder.</span>
      `;
    }
    const showExistingBtn = document.getElementById("showExistingBtn");
    if (showExistingBtn) showExistingBtn.click();
  } catch (err) {
    console.error(err);
    alert(`Bulk creation failed: ${err.message}`);
  }
}

function setupDirectoryPage() {
  loadNavbar();
  const sections = {
    create: document.getElementById("createSection"),
    existing: document.getElementById("existingSection"),
    bulk: document.getElementById("bulkSection"),
  };

  const buttons = {
    create: document.getElementById("showCreateBtn"),
    existing: document.getElementById("showExistingBtn"),
    bulk: document.getElementById("showBulkBtn"),
  };

  const showSection = (name) => {
    Object.entries(sections).forEach(([key, section]) => {
      if (!section) return;
      if (key === name) section.classList.remove("hidden");
      else section.classList.add("hidden");
    });
    Object.entries(buttons).forEach(([key, btn]) => {
      if (!btn) return;
      if (key === name) btn.classList.add("active");
      else btn.classList.remove("active");
    });
  };

  Object.entries(buttons).forEach(([name, btn]) => {
    if (btn) btn.addEventListener("click", () => showSection(name));
  });

  const createForm = document.getElementById("createDirectoryForm");
  const bulkForm = document.getElementById("bulkCreateForm");
  const useBtn = document.getElementById("use-existing-btn");
  const chooseBtn = document.getElementById("chooseDirectoryBtn");
  const clearBtn = document.getElementById("clearExistingSelectionBtn");

  if (createForm) createForm.addEventListener("submit", handleCreateDirectory);
  if (bulkForm) bulkForm.addEventListener("submit", handleBulkCreate);
  if (useBtn) useBtn.addEventListener("click", handleUseExistingDirectory);
  if (chooseBtn) chooseBtn.addEventListener("click", openExistingDirectoryDialog);
  if (clearBtn) clearBtn.addEventListener("click", () =>
    setSelectedExistingDirectory(null)
  );

  const storedMode = localStorage.getItem("directoryMode");
  if (storedMode === "existing") {
    setSelectedExistingDirectory(
      localStorage.getItem("basePath"),
      localStorage.getItem("directoryDisplay")
    );
  } else {
    setSelectedExistingDirectory(null);
  }

  const defaultSection = storedMode === "existing" ? "existing" : "create";
  showSection(defaultSection);
}

// -----------------------------
// Connection page
// -----------------------------

function applySerialPreset(preset) {
  const portInput = document.getElementById("serialPort");
  if (!portInput) return;
  if (preset === "custom") {
    portInput.value = "";
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
  if (!sshFields || !serialFields) return;
  if (conn === "ssh") {
    sshFields.classList.remove("hidden");
    serialFields.classList.add("hidden");
  } else {
    sshFields.classList.add("hidden");
    serialFields.classList.remove("hidden");
  }
}

async function saveConnection({ autoRun = false, triggerButton = null } = {}) {
  autoRunAfterConnect = autoRun;
  const type = document.querySelector('input[name="connType"]:checked');
  if (!type) {
    alert("Please choose a connection type.");
    autoRunAfterConnect = false;
    return;
  }
  const conn = type.value;
  const payload = { connection: conn };
  const connectBtn = triggerButton || null;
  const originalBtnText = connectBtn ? connectBtn.textContent : null;
  const progress = document.getElementById("progress");
  if (progress) progress.value = 0;

  let connectionSucceeded = false;
  let finalHostname = null;
  let errorMessage = "";
  let errorLogged = false;

  if (conn === "ssh") {
    const storedHost = localStorage.getItem("sshHost") || "";
    const storedUser = localStorage.getItem("sshUser") || "";
    const storedPass = localStorage.getItem("sshPass") || "";

    const hostInput = document.getElementById("sshHost");
    const userInput = document.getElementById("sshUser");
    const passInput = document.getElementById("sshPass");

    let host = hostInput?.value.trim() || "";
    let user = userInput?.value.trim() || "";
    let pass = passInput?.value || "";

    if (autoRunAfterConnect) {
      host = storedHost || host;
      user = storedUser || user;
      pass = storedPass || pass;
    } else {
      if (!host && storedHost) host = storedHost;
      if (!user && storedUser) user = storedUser;
      if (!pass && storedPass) pass = storedPass;
    }

    if (!host || !user || !pass) {
      alert("Please provide SSH host, username and password.");
      autoRunAfterConnect = false;
      return;
    }

    if (hostInput && hostInput.value.trim() !== host) hostInput.value = host;
    if (userInput && userInput.value.trim() !== user) userInput.value = user;
    if (passInput && passInput.value !== pass) passInput.value = pass;

    payload.ssh = { host, username: user, password: pass };
  } else {
    const storedPort = localStorage.getItem("serialPort") || "";
    const portInput = document.getElementById("serialPort");
    let port = portInput ? portInput.value.trim() : "";
    if (autoRunAfterConnect && storedPort) {
      port = storedPort;
    } else if (!port && storedPort) {
      port = storedPort;
    }
    if (!port) port = "/dev/ttyUSB0";
    if (portInput && portInput.value.trim() !== port) {
      portInput.value = port;
    }
    payload.serial = { port };
  }

  let timeoutId;
  try {
    if (connectBtn) {
      connectBtn.textContent = "Connecting...";
      connectBtn.disabled = true;
    }
    const modal = showStatusModal("Connecting... Please wait.", "pending");
    const controller = new AbortController();
    const connectionTimeoutMs = 15000;
    timeoutId = setTimeout(() => controller.abort(), connectionTimeoutMs);
    const response = await fetch(`${API_ROOT}/api/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new Error(text || response.statusText || "Connection failed");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let payloadObj = null;
        try {
          payloadObj = JSON.parse(trimmed);
        } catch (err) {
          appendLogLine(trimmed);
          continue;
        }

        const { type: evtType, msg, hostname, trace, success } = payloadObj;
        const pct = typeof payloadObj.progress_pct === "number" ? payloadObj.progress_pct : null;
        if (pct !== null && progress) {
          progress.value = Math.min(100, Math.max(0, pct));
        }
        if (evtType === "progress") {
          appendLogLine(`[${nowTimestamp()}] ${msg}`);
        } else if (evtType === "success") {
          connectionSucceeded = true;
          finalHostname = hostname || null;
          const successMsg = msg || "Connection established.";
          appendLogLine(`[SUCCESS] ${successMsg}`);
          updateStatusModal(modal, successMsg, "success", true);
        } else if (evtType === "error") {
          errorMessage = msg || "Connection failed.";
          appendLogLine(`[ERROR] ${errorMessage}`);
          errorLogged = true;
          if (trace) {
            appendLogLine(trace);
          }
          updateStatusModal(modal, errorMessage, "error");
        }

        if (evtType === "done") {
          if (!success && !connectionSucceeded && !errorMessage) {
            errorMessage = "Connection failed.";
            updateStatusModal(modal, errorMessage, "error");
          }
        }
      }
    }

    if (buffer.trim()) {
      try {
        const payloadObj = JSON.parse(buffer.trim());
        const { type: evtType, msg, hostname, trace } = payloadObj;
        const pct = typeof payloadObj.progress_pct === "number" ? payloadObj.progress_pct : null;
        if (pct !== null && progress) {
          progress.value = Math.min(100, Math.max(0, pct));
        }
        if (evtType === "progress") {
          appendLogLine(`[${nowTimestamp()}] ${msg}`);
        } else if (evtType === "success") {
          connectionSucceeded = true;
          finalHostname = hostname || null;
          const successMsg = msg || "Connection established.";
          appendLogLine(`[SUCCESS] ${successMsg}`);
          updateStatusModal(modal, successMsg, "success", true);
        } else if (evtType === "error") {
          errorMessage = msg || "Connection failed.";
          appendLogLine(`[ERROR] ${errorMessage}`);
          errorLogged = true;
          if (trace) appendLogLine(trace);
          updateStatusModal(modal, errorMessage, "error");
        }
      } catch (err) {
        appendLogLine(buffer.trim());
      }
    }

    if (!connectionSucceeded) {
      if (!errorMessage) errorMessage = "Connection failed.";
      if (!errorLogged) {
        appendLogLine(`[ERROR] ${errorMessage}`);
      }
      updateStatusModal(modal, errorMessage, "error");
      autoRunAfterConnect = false;
      return;
    }

    if (progress) progress.value = 100;

    localStorage.setItem("connection", conn);
    if (conn === "ssh") {
      localStorage.setItem("sshHost", payload.ssh.host);
      localStorage.setItem("sshUser", payload.ssh.username);
      localStorage.setItem("sshPass", payload.ssh.password);
    } else {
      localStorage.setItem("serialPort", payload.serial.port);
    }
    if (finalHostname) {
      localStorage.setItem("connectedHostname", finalHostname);
    }
    renderSelectedCommandsInfo();
    const storedCommands = getStoredCommands();
    if (autoRunAfterConnect && storedCommands.length) {
      startExecution({ commands: storedCommands, initiatedFromConnection: true });
    } else if (autoRunAfterConnect && !storedCommands.length) {
      alert("Select commands on the Commands page before running.");
      goTo("commands.html");
    }
    autoRunAfterConnect = false;
    if (isPendingExecution()) {
      const commands = getStoredCommands();
      if (commands.length) {
        startExecution({ commands, initiatedFromConnection: true });
      }
    }
  } catch (err) {
    console.error(err);
    if (!connectionSucceeded) {
      if (err.name === "AbortError") {
        showStatusModal(
          "Connection timed out. Please check the device and try again.",
          "error"
        );
      } else {
        showStatusModal(`Connection failed: ${err.message}`, "error");
      }
    }
  } finally {
    if (typeof timeoutId !== "undefined") {
      clearTimeout(timeoutId);
      timeoutId = undefined;
    }
    if (connectBtn) {
      connectBtn.disabled = false;
      connectBtn.textContent = originalBtnText || "Connect";
    }
    autoRunAfterConnect = false;
  }
}

function setupConnectionPage() {
  loadNavbar();
  if (!ensureDirectoryConfigured()) return;

  const form = document.getElementById("connectionForm");
  const radios = document.querySelectorAll('input[name="connType"]');
  const presetRadios = document.querySelectorAll('input[name="serialPreset"]');
  const backBtn = document.getElementById("backToDirectoryBtn");

  if (form) {
    const savedConn = localStorage.getItem("connection") || "serial";
    const targetRadio = Array.from(radios).find((r) => r.value === savedConn);
    if (targetRadio) targetRadio.checked = true;
    toggleConnectionFields();

    form.addEventListener("submit", (evt) => evt.preventDefault());

    document
      .getElementById("reconnectRunBtn")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        const commands = getStoredCommands();
        if (!commands.length) {
          alert("Select commands on the Commands page before running.");
          goTo("commands.html");
          return;
        }
        saveConnection({ autoRun: true, triggerButton: evt.currentTarget });
      });
  }

  radios.forEach((r) => r.addEventListener("change", toggleConnectionFields));

  presetRadios.forEach((r) =>
    r.addEventListener("change", () => applySerialPreset(r.value))
  );

  const savedPort = localStorage.getItem("serialPort");
  if (savedPort) {
    document.getElementById("serialPort").value = savedPort;
    document.querySelector('input[name="serialPreset"][value="custom"]').checked = true;
    applySerialPreset("custom");
  } else {
    applySerialPreset("linux");
  }

  document.getElementById("sshHost").value = localStorage.getItem("sshHost") || "";
  document.getElementById("sshUser").value = localStorage.getItem("sshUser") || "";
  document.getElementById("sshPass").value = localStorage.getItem("sshPass") || "";

  if (backBtn) {
    backBtn.addEventListener("click", () => goTo("index.html"));
  }

  document
    .getElementById("clearLogBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      clearExecutionLog();
    });

  document
    .getElementById("startExecutionBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      const commands = getStoredCommands();
      if (!commands.length) {
        alert("Select commands on the Commands page before running.");
        goTo("commands.html");
        return;
      }
      startExecution({ commands, initiatedFromConnection: true });
    });

  renderSelectedCommandsInfo();

  if (isPendingExecution()) {
    const pendingCommands = getStoredCommands();
    if (pendingCommands.length && localStorage.getItem("connection")) {
      startExecution({ commands: pendingCommands, initiatedFromConnection: true });
    }
  }
}

// -----------------------------
// Commands page
// -----------------------------

function updateCommandSelectionState() {
  const badge = document.getElementById("commandCount");
  if (!badge) return;
  const checkboxes = document.querySelectorAll('input[name="command"]');
  const selected = Array.from(checkboxes).filter((cb) => cb.checked).length;
  const total = checkboxes.length;
  badge.textContent = total
    ? `${selected} selected of ${total}`
    : "0 selected";
}

function toggleSelectAllCommands() {
  const checkboxes = Array.from(document.querySelectorAll('input[name="command"]'));
  if (!checkboxes.length) return;
  const allSelected = checkboxes.every((cb) => cb.checked);
  checkboxes.forEach((cb) => {
    cb.checked = !allSelected;
  });
  updateCommandSelectionState();
}

function renderCommandList(commands = []) {
  const container = document.getElementById("commandsList");
  if (!container) return;
  container.innerHTML = "";

  if (!commands.length) {
    container.innerHTML =
      "<p class=\"hint\">No commands defined. Add a command using the form on the left.</p>";
    updateCommandSelectionState();
    return;
  }

  commands.forEach((cmd, idx) => {
    const tile = document.createElement("div");
    tile.className = "command-tile";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "command";
    checkbox.value = cmd;
    checkbox.id = `cmd_${idx}`;

    const nameLabel = document.createElement("label");
    nameLabel.className = "command-name";
    nameLabel.setAttribute("for", `cmd_${idx}`);
    nameLabel.textContent = cmd;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.dataset.command = cmd;
    removeBtn.textContent = "Remove";
    removeBtn.className = "remove-btn";

    tile.appendChild(checkbox);
    tile.appendChild(nameLabel);
    tile.appendChild(removeBtn);
    container.appendChild(tile);
  });

  updateCommandSelectionState();
}

async function loadCommandsList() {
  const container = document.getElementById("commandsList");
  if (!container) return;
  container.innerHTML = "<p>Loading commands...</p>";
  try {
    const data = await fetchJson("/api/commands", { method: "GET" });
    renderCommandList(data.commands || []);
  } catch (err) {
    console.error(err);
    container.innerHTML = `<p class="error">Failed to load commands: ${err.message}</p>`;
    updateCommandSelectionState();
  }
}

async function addCommand() {
  const input = document.getElementById("newCommandInput");
  if (!input) return;
  const command = input.value.trim();
  if (!command) {
    alert("Enter a command to add.");
    return;
  }
  try {
    const data = await fetchJson("/api/commands", {
      method: "POST",
      body: JSON.stringify({ command }),
    });
    renderCommandList(data.commands || []);
    input.value = "";
  } catch (err) {
    console.error(err);
    alert(`Failed to add command: ${err.message}`);
  }
}

async function removeCommand(command) {
  if (!command) return;
  const confirmed = confirm(`Remove command "${command}"?`);
  if (!confirmed) return;
  try {
    const data = await fetchJson("/api/commands", {
      method: "DELETE",
      body: JSON.stringify({ command }),
    });
    renderCommandList(data.commands || []);
  } catch (err) {
    console.error(err);
    alert(`Failed to remove command: ${err.message}`);
  }
}
function getStoredCommands() {
  try {
    const raw = localStorage.getItem("selectedCommands");
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    return [];
  }
}

function setStoredCommands(commands) {
  localStorage.setItem("selectedCommands", JSON.stringify(commands));
}

function clearPendingExecutionFlag() {
  localStorage.removeItem("pendingExecution");
}

function setPendingExecutionFlag() {
  localStorage.setItem("pendingExecution", "true");
}

function isPendingExecution() {
  return localStorage.getItem("pendingExecution") === "true";
}

function clearExecutionLog() {
  const log = document.getElementById("log");
  if (log) log.innerText = "";
  const progress = document.getElementById("progress");
  if (progress) progress.value = 0;
}

function renderSelectedCommandsInfo() {
  const infoBox = document.getElementById("selectedCommandsInfo");
  if (!infoBox) return;
  const commands = getStoredCommands();
  if (!commands.length) {
    infoBox.classList.add("hidden");
    infoBox.innerHTML = "";
    return;
  }
  infoBox.classList.remove("hidden");
  infoBox.innerHTML = "";
  const heading = document.createElement("strong");
  heading.textContent = `${commands.length} command(s) selected:`;
  infoBox.appendChild(heading);
  const list = document.createElement("ul");
  commands.forEach((cmd) => {
    const li = document.createElement("li");
    li.textContent = cmd;
    list.appendChild(li);
  });
  infoBox.appendChild(list);
}

async function startExecution({ commands, initiatedFromConnection = false } = {}) {
  if (!ensureDirectoryConfigured()) return;

  let commandsToRun = Array.isArray(commands) ? commands : null;
  if (!commandsToRun) {
    commandsToRun = Array.from(
      document.querySelectorAll('input[name="command"]:checked')
    ).map((i) => i.value);
  }

  if (!commandsToRun.length) {
    alert("Select at least one command to execute.");
    return;
  }

  setStoredCommands(commandsToRun);
  renderSelectedCommandsInfo();

  const log = document.getElementById("log");
  const progress = document.getElementById("progress");
  const hasStatusUI = log && progress;
  const connectionType = localStorage.getItem("connection");

  if (!hasStatusUI || !connectionType) {
    setPendingExecutionFlag();
    if (!connectionType && initiatedFromConnection) {
      alert("Configure the connection before running commands.");
    }
    if (!initiatedFromConnection) {
      goTo("connection.html");
    }
    return;
  }

  clearPendingExecutionFlag();
  clearExecutionLog();

  const directoryMode = localStorage.getItem("directoryMode") || "create";
  const basePath = localStorage.getItem("basePath");
  const payload = {
    exam_name: localStorage.getItem("examName"),
    session_id: localStorage.getItem("sessionId"),
    student_id: localStorage.getItem("studentId"),
    connection: connectionType,
    commands: commandsToRun,
    log_mode: directoryMode,
  };

  if (directoryMode === "existing") {
    if (!basePath) {
      alert("Existing directory path not found. Please re-select the directory.");
      goTo("index.html");
      return;
    }
    payload.log_dir = basePath;
  }

  if (connectionType === "ssh") {
    payload.ssh = {
      host: localStorage.getItem("sshHost"),
      username: localStorage.getItem("sshUser"),
      password: localStorage.getItem("sshPass"),
    };
  } else {
    payload.serial = {
      port: localStorage.getItem("serialPort") || SERIAL_PRESETS.linux,
    };
  }

  const startBtn = document.getElementById("startExecutionBtn");
  if (startBtn) startBtn.disabled = true;

  const total = commandsToRun.length;
  let finished = 0;

  try {
    const res = await fetch(`${API_ROOT}/api/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const txt = await res.text();
      log.innerText += `[ERROR ${res.status}] ${txt}\n`;
      return;
    }

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buffer = "";
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
          const pct = typeof obj.progress_pct === "number" ? obj.progress_pct : null;
          if (obj.type === "progress") {
            log.innerText += `[${new Date().toLocaleTimeString()}] ${obj.msg}\n`;
            if (pct !== null && progress) {
              progress.value = Math.min(100, Math.max(0, pct));
            }
            if (obj.cmd_done && progress) {
              finished++;
              if (pct === null) {
                progress.value = Math.round((finished / total) * 100);
              }
            }
          } else if (obj.type === "result") {
            log.innerText += `[RESULT] ${obj.msg}\n`;
            if (obj.files) {
              obj.files.forEach((f) => (log.innerText += `  ${f}\n`));
            }
            if (pct !== null && progress) {
              progress.value = Math.min(100, Math.max(0, pct));
            }
          } else if (obj.type === "done") {
            log.innerText += `\n[FINISHED] ${obj.msg}\n`;
            if (pct !== null && progress) {
              progress.value = Math.min(100, Math.max(0, pct));
            } else {
              if (progress) progress.value = 100;
            }
          } else if (obj.type === "error") {
            log.innerText += `[ERROR] ${obj.msg}\n`;
            if (obj.trace) log.innerText += `${obj.trace}\n`;
          } else {
            log.innerText += JSON.stringify(obj) + "\n";
          }
        } catch (err) {
          log.innerText += line + "\n";
        }
        log.scrollTop = log.scrollHeight;
      }
    }
  } catch (err) {
    log.innerText += `[ERROR] ${err}\n`;
  } finally {
    if (startBtn) startBtn.disabled = false;
    updateCommandSelectionState();
  }
}
window.startExecution = startExecution;

function setupCommandsPage() {
  loadNavbar();
  if (!ensureDirectoryConfigured()) return;

  loadCommandsList();
  updateCommandSelectionState();

  document
    .getElementById("addCommandBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      addCommand();
    });

  document
    .getElementById("refreshCommandsBtn")
    ?.addEventListener("click", loadCommandsList);

  document
    .getElementById("commandsList")
    ?.addEventListener("click", (evt) => {
      const target = evt.target;
      if (
        target instanceof HTMLElement &&
        target.dataset &&
        target.dataset.command
      ) {
        removeCommand(target.dataset.command);
      }
    });

  document
    .getElementById("commandsList")
    ?.addEventListener("change", () => updateCommandSelectionState());

  document
    .getElementById("selectAllBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      toggleSelectAllCommands();
    });

  document
    .getElementById("startExecutionBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      startExecution();
    });
}

// -----------------------------
// Init
// -----------------------------

document.addEventListener("DOMContentLoaded", () => {
  loadNavbar();
  if (document.getElementById("welcomePage")) setupWelcomePage();
  if (document.getElementById("directoryPage")) setupDirectoryPage();
  if (document.getElementById("connectionForm")) setupConnectionPage();
  if (document.getElementById("commandsPage")) setupCommandsPage();
});
