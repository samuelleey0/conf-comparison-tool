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

const ROUTER_COMMANDS = [
  "show ip route",
  "show ip interface brief",
  "show run",
  "show interfaces",
  "show version"
];

const SWITCH_COMMANDS = [
  "show vlan",
  "show mac address-table",
  "show interface trunk",
  "show spanning-tree",
  "show ip interface brief",
  "show run",
  "show version"
];

function autoSelectCommands(deviceType) {
  const checkboxes = Array.from(document.querySelectorAll('input[name="command"]'));
  if (!checkboxes.length) return;

  // Determine which list to check against
  const targetCommands = deviceType === 'router' ? ROUTER_COMMANDS : SWITCH_COMMANDS;
  const opposingCommands = deviceType === 'router' ? SWITCH_COMMANDS : ROUTER_COMMANDS;

  // Find checkboxes that match the target commands
  const matchingCheckboxes = checkboxes.filter((cb) => {
    const cmdName = cb.value.toLowerCase().trim();
    return targetCommands.some(tc => cmdName.includes(tc.toLowerCase()));
  });

  // Find checkboxes that match the opposing commands but NOT the target commands
  const opposingCheckboxes = checkboxes.filter((cb) => {
    const cmdName = cb.value.toLowerCase().trim();
    const matchesOpposing = opposingCommands.some(tc => cmdName.includes(tc.toLowerCase()));
    const matchesTarget = targetCommands.some(tc => cmdName.includes(tc.toLowerCase()));
    return matchesOpposing && !matchesTarget;
  });

  if (matchingCheckboxes.length === 0) return;

  // Determine toggle state: if ALL matching ones are checked, uncheck them. Else, check them all.
  const allMatchingChecked = matchingCheckboxes.every(cb => cb.checked);

  matchingCheckboxes.forEach((cb) => {
    cb.checked = !allMatchingChecked;
  });

  // If we are checking the target commands, make sure to uncheck the opposing ones
  if (!allMatchingChecked) {
    opposingCheckboxes.forEach((cb) => {
      cb.checked = false;
    });
  }

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

function setStoredCommandsFromDevice(hostname) {
  try {
    const devicesStr = localStorage.getItem("templateDevices");
    if (devicesStr) {
      const devicesMeta = JSON.parse(devicesStr);
      if (devicesMeta[hostname]) {
        localStorage.setItem("selectedCommands", JSON.stringify(devicesMeta[hostname]));
        renderSelectedCommandsInfo();
      }
    }
  } catch(err) {
    console.error(err);
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
  setProgressValue(0);
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

  const targetDeviceSelect = document.getElementById("targetDevice");
  const targetDevice = targetDeviceSelect ? targetDeviceSelect.value : null;

  if (document.getElementById("connectionPage") && !targetDevice) {
    alert("Please select a Target Device first.");
    return;
  }

  const commandsToRun = getStoredCommands();

  if (!commandsToRun.length) {
    alert("No commands mapped to this device. Check device setup.");
    return;
  }

  renderSelectedCommandsInfo();

  const log = document.getElementById("log");
  const progress = document.getElementById("progress");
  const hasStatusUI = log && progress;
  let connectionType = localStorage.getItem("connection");
  const selectedConnRadio = document.querySelector('input[name="connType"]:checked');
  if (selectedConnRadio) {
    connectionType = selectedConnRadio.value;
  }

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

  // Get extension
  const extSelect = document.getElementById("fileExtensionSelect");
  const fileExtension = extSelect ? extSelect.value : ".txt";

  const payload = {
    exam_name: localStorage.getItem("examName"),
    session_id: localStorage.getItem("sessionId"),
    student_id: localStorage.getItem("studentId"),
    connection: connectionType,
    mode: connectionType,
    commands: commandsToRun,
    log_mode: directoryMode,
    file_extension: fileExtension,
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
    const sshHostInput = document.getElementById("sshHost");
    const sshUserInput = document.getElementById("sshUser");
    const sshPassInput = document.getElementById("sshPass");
    let sshHost =
      sshHostInput?.value.trim() || localStorage.getItem("sshHost") || "";
    let sshUser =
      sshUserInput?.value.trim() || localStorage.getItem("sshUser") || "";
    let sshPass =
      sshPassInput?.value || localStorage.getItem("sshPass") || "";
    let sshPort =
      document.getElementById("sshPort")?.value.trim() ||
      localStorage.getItem("sshPort") ||
      "22";
    payload.ssh = {
      host: sshHost,
      username: sshUser,
      password: sshPass,
      port: sshPort,
    };
    payload.host = sshHost;
    payload.username = sshUser;
    payload.password = sshPass;
    payload.port = sshPort;
  } else {
    // Attempt to get the port directly from the input field first
    const portInput = document.getElementById("serialPort");
    let currentPort = portInput ? portInput.value.trim() : "";
    if (!currentPort) {
      currentPort = localStorage.getItem("serialPort") || SERIAL_PRESETS.linux_rs232;
    }
    payload.serial = { port: currentPort };
  }

  const startBtn = document.getElementById("startExecutionBtn");
  if (startBtn) startBtn.disabled = true;
  setProgressValue(0);

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
            if (pct !== null) {
              setProgressValue(pct);
            }
            if (obj.cmd_done) {
              finished++;
              if (pct === null) {
                setProgressValue((finished / total) * 100);
              }
            }
          } else if (obj.type === "result") {
            log.innerText += `[RESULT] ${obj.msg}\n`;
            if (obj.files) {
              obj.files.forEach((f) => (log.innerText += `  ${f}\n`));
            }
            if (pct !== null) {
              setProgressValue(pct);
            }
          } else if (obj.type === "done") {
            log.innerText += `\n[FINISHED] ${obj.msg}\n`;
            if (pct !== null) {
              setProgressValue(pct);
            } else {
              setProgressValue(100);
            }
            // User requested to open the directory on completion
            if (basePath && shell && pathModule) {
              // Try to open the specific routerorswitch subfolder first as requested
              const subDir = pathModule.join(basePath, "routerorswitch");
              shell.openPath(subDir).then((err) => {
                if (err) {
                  // Fallback to base student directory if subfolder doesn't exist
                  shell.openPath(basePath);
                }
              });
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
    .getElementById("autoSelectRouterBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      autoSelectCommands('router');
    });

  document
    .getElementById("autoSelectSwitchBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      autoSelectCommands('switch');
    });

  document
    .getElementById("startExecutionBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      startExecution();
    });
}



document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("commandsPage")) setupCommandsPage();
});
