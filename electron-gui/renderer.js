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

function goTo(page) {
  window.location.href = page;
}
window.goTo = goTo;

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

// -----------------------------
// Directory setup page
// -----------------------------

function setupWelcomePage() {
  const startBtn = document.getElementById("startSetupBtn");
  const viewCommandsBtn = document.getElementById("viewCommandsBtn");
  if (startBtn) {
    startBtn.addEventListener("click", () => goTo("index.html"));
  }
  if (viewCommandsBtn) {
    viewCommandsBtn.addEventListener("click", () => goTo("commands.html"));
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
    goTo("connection.html");
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
    goTo("connection.html");
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
  const skipBtn = document.getElementById("goToCommandsBtn");

  if (createForm) createForm.addEventListener("submit", handleCreateDirectory);
  if (bulkForm) bulkForm.addEventListener("submit", handleBulkCreate);
  if (useBtn) useBtn.addEventListener("click", handleUseExistingDirectory);
  if (chooseBtn) chooseBtn.addEventListener("click", openExistingDirectoryDialog);
  if (clearBtn) clearBtn.addEventListener("click", () =>
    setSelectedExistingDirectory(null)
  );

  if (skipBtn) {
    skipBtn.addEventListener("click", () => {
      if (!ensureDirectoryConfigured()) return;
      goTo("commands.html");
    });
  }

  const storedMode = localStorage.getItem("directoryMode");
  if (storedMode === "existing") {
    setSelectedExistingDirectory(
      localStorage.getItem("basePath"),
      localStorage.getItem("directoryDisplay")
    );
  } else {
    setSelectedExistingDirectory(null);
  }
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

async function saveConnection() {
  const type = document.querySelector('input[name="connType"]:checked');
  if (!type) {
    alert("Please choose a connection type.");
    return;
  }
  const conn = type.value;
  const payload = { connection: conn };

  if (conn === "ssh") {
    const host = document.getElementById("sshHost").value.trim();
    const user = document.getElementById("sshUser").value.trim();
    const pass = document.getElementById("sshPass").value;
    if (!host || !user || !pass) {
      alert("Please provide SSH host, username and password.");
      return;
    }
    payload.ssh = { host, username: user, password: pass };
  } else {
    const port = document.getElementById("serialPort").value.trim() || "/dev/ttyUSB0";
    payload.serial = { port };
  }

  try {
    const data = await fetchJson("/api/connect", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    localStorage.setItem("connection", conn);
    if (conn === "ssh") {
      localStorage.setItem("sshHost", payload.ssh.host);
      localStorage.setItem("sshUser", payload.ssh.username);
      localStorage.setItem("sshPass", payload.ssh.password);
    } else {
      localStorage.setItem("serialPort", payload.serial.port);
    }
    alert(data.message);
    goTo("commands.html");
  } catch (err) {
    console.error(err);
    alert(`Connection failed: ${err.message}`);
  }
}

function setupConnectionPage() {
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
      .getElementById("connection-next")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        saveConnection();
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
}

// -----------------------------
// Commands page
// -----------------------------

function renderCommandList(commands = []) {
  const container = document.getElementById("commandsList");
  if (!container) return;
  container.innerHTML = "";

  if (!commands.length) {
    container.innerHTML =
      "<p>No commands defined. Add a command using the form above.</p>";
    return;
  }

  commands.forEach((cmd, idx) => {
    const wrapper = document.createElement("div");
    wrapper.className = "command-item";

    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" name="command" value="${cmd}" id="cmd_${idx}"> ${cmd}`;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.dataset.command = cmd;
    removeBtn.textContent = "Remove";

    wrapper.appendChild(label);
    wrapper.appendChild(removeBtn);
    container.appendChild(wrapper);
  });
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

async function startExecution() {
  if (!ensureDirectoryConfigured()) return;

  const commands = Array.from(
    document.querySelectorAll('input[name="command"]:checked')
  ).map((i) => i.value);

  if (!commands.length) {
    alert("Select at least one command to execute.");
    return;
  }

  const directoryMode = localStorage.getItem("directoryMode") || "create";
  const basePath = localStorage.getItem("basePath");
  const payload = {
    exam_name: localStorage.getItem("examName"),
    session_id: localStorage.getItem("sessionId"),
    student_id: localStorage.getItem("studentId"),
    connection: localStorage.getItem("connection"),
    commands,
    log_mode: directoryMode,
  };

  if (!payload.connection) {
    alert("Connection settings missing. Please configure the connection first.");
    goTo("connection.html");
    return;
  }

  if (directoryMode === "existing") {
    if (!basePath) {
      alert("Existing directory path not found. Please re-select the directory.");
      goTo("index.html");
      return;
    }
    payload.log_dir = basePath;
  }

  if (payload.connection === "ssh") {
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

  const log = document.getElementById("log");
  const progress = document.getElementById("progress");
  const startBtn = document.getElementById("startExecutionBtn");
  log.innerText = "";
  progress.value = 0;
  const total = commands.length;
  let finished = 0;
  if (startBtn) startBtn.disabled = true;

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
          if (obj.type === "progress") {
            log.innerText += `[${new Date().toLocaleTimeString()}] ${obj.msg}\n`;
            if (obj.cmd_done) {
              finished++;
              progress.value = Math.round((finished / total) * 100);
            }
          } else if (obj.type === "result") {
            log.innerText += `[RESULT] ${obj.msg}\n`;
            if (obj.files) {
              obj.files.forEach((f) => (log.innerText += `  ${f}\n`));
            }
          } else if (obj.type === "done") {
            log.innerText += `\n[FINISHED] ${obj.msg}\n`;
            progress.value = 100;
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
  }
}
window.startExecution = startExecution;

function setupCommandsPage() {
  if (!ensureDirectoryConfigured()) return;

  loadCommandsList();

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
    .getElementById("startExecutionBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      startExecution();
    });

  document
    .getElementById("backToConnectionBtn")
    ?.addEventListener("click", () => goTo("connection.html"));
}

// -----------------------------
// Init
// -----------------------------

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("welcomePage")) setupWelcomePage();
  if (document.getElementById("directoryPage")) setupDirectoryPage();
  if (document.getElementById("connectionForm")) setupConnectionPage();
  if (document.getElementById("commandsPage")) setupCommandsPage();
});
