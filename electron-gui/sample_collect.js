// -----------------------------
// Sample log collection
// -----------------------------

function setupSampleCollectPage() {
  loadNavbar();

  const statusBox = document.getElementById("sampleStatus");
  const list = document.getElementById("sampleCommandsList");
  const log = document.getElementById("sampleLog");
  const dirLabel = document.getElementById("sampleDirLabel");
  const sampleCommandSearch = document.getElementById("sampleCommandSearch");

  const setStatus = (msg) => {
    if (!statusBox) return;
    const msgEl = statusBox.querySelector('.status-banner__msg');
    if (msgEl) msgEl.textContent = msg;
    else statusBox.textContent = msg;
  };

  const appendSampleLog = (msg) => {
    if (!log) return;
    log.textContent += `${msg}\n`;
    log.scrollTop = log.scrollHeight;
  };

  const normalizeCommandSearch = (text) =>
    String(text || "")
      .toLowerCase()
      .replace(/[_./-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const filterSampleCommands = () => {
    if (!list) return;
    const term = normalizeCommandSearch(sampleCommandSearch?.value || "");
    list.querySelectorAll(".sample-cmd-row").forEach((row) => {
      const text = normalizeCommandSearch(row.textContent);
      row.classList.toggle("hidden", !!term && !text.includes(term));
    });
  };

  const toggleSampleConnectionFields = () => {
    const conn = document.querySelector('input[name="sampleConnType"]:checked')?.value || "serial";
    const sshFields = document.getElementById("sampleSshFields");
    const serialFields = document.getElementById("sampleSerialFields");
    if (!sshFields || !serialFields) return;
    if (conn === "ssh") {
      sshFields.classList.remove("hidden");
      serialFields.classList.add("hidden");
    } else {
      sshFields.classList.add("hidden");
      serialFields.classList.remove("hidden");
    }
  };

  const applySampleSerialPreset = (preset) => {
    const portInput = document.getElementById("sampleSerialPort");
    if (!portInput) return;
    if (preset === "custom") {
      portInput.removeAttribute("readonly");
    } else {
      portInput.value = SERIAL_PRESETS[preset] || "/dev/ttyS0";
      portInput.setAttribute("readonly", "readonly");
    }
  };

  const loadCommands = async () => {
    if (!list) return;
    list.innerHTML = "<p class=\"loading-text\">Loading commands...</p>";
    try {
      const data = await fetchJson("/api/commands");
      const commands = data.commands || [];
      if (!commands.length) {
        list.innerHTML = "<p class=\"hint\">No commands found. Add them in System Admin.</p>";
        return;
      }

      list.innerHTML = "";
      commands.forEach((cmd) => {
        const row = document.createElement("label");
        row.className = "choice-label sample-cmd-row";
        row.innerHTML = `<input type="checkbox" value="${cmd}"> ${cmd}`;
        list.appendChild(row);
      });
      filterSampleCommands();
    } catch (err) {
      console.error(err);
      list.innerHTML = `<p class="hint">Failed to load commands: ${err.message || "unknown error"}</p>`;
    }
  };

  const getSelectedCommands = () => {
    const checkboxes = document.querySelectorAll('#sampleCommandsList input[type="checkbox"]');
    return Array.from(checkboxes)
      .filter((cb) => cb.checked)
      .map((cb) => cb.value);
  };

  const chooseSampleDirectory = async () => {
    if (ipcRenderer && typeof ipcRenderer.invoke === "function") {
      const selected = await ipcRenderer.invoke("select-directory");
      return selected || null;
    }
    alert("Folder picker not available. Please enter a path manually.");
    const manual = prompt("Enter directory path to save logs:");
    return manual || null;
  };

  const runSampleCollection = async () => {
    const dirPath = dirLabel?.dataset?.path;
    if (!dirPath) {
      alert("Please choose a folder first.");
      return;
    }

    const commands = getSelectedCommands();
    if (!commands.length) {
      alert("Please select at least one command.");
      return;
    }

    const conn = document.querySelector('input[name="sampleConnType"]:checked')?.value || "serial";
    const payload = {
      exam_name: "sample",
      session_id: "sample",
      student_id: "sample",
      commands,
      connection: conn,
      mode: conn,
      log_mode: "existing",
      log_dir: dirPath,
      skip_config: true,
    };

    if (conn === "ssh") {
      payload.ssh = {
        host: document.getElementById("sampleSshHost").value.trim(),
        username: document.getElementById("sampleSshUser").value.trim(),
        password: document.getElementById("sampleSshPass").value || "",
        port: document.getElementById("sampleSshPort").value.trim() || "22",
      };
    } else {
      payload.serial = {
        port: document.getElementById("sampleSerialPort").value.trim() || "/dev/ttyUSB0",
      };
    }

    const btn = document.getElementById("runSampleCollectBtn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Collecting...";
    }

    if (log) log.textContent = "";
    appendSampleLog("Starting collection...");

    try {
      const res = await fetch(`${API_ROOT}/api/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !res.body) {
        throw new Error("Failed to start collection.");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let hasError = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const payloadObj = JSON.parse(line);
            if (payloadObj.type === "error") {
              hasError = true;
              appendSampleLog(`[ERROR] ${payloadObj.msg || "Error"}`);
            } else if (payloadObj.msg) {
              appendSampleLog(payloadObj.msg);
            }
          } catch (_) {
            appendSampleLog(line.trim());
          }
        }
      }

      if (hasError) {
        alert("Collection finished with errors. Check the log.");
      } else {
        alert("Logs collected successfully.");
      }
    } catch (err) {
      console.error(err);
      alert(err.message || "Collection failed.");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Collect Logs";
      }
    }
  };

  setStatus("Sample Collect ready. Loading commands...");
  loadCommands();

  document.querySelectorAll('input[name="sampleConnType"]').forEach((r) =>
    r.addEventListener("change", toggleSampleConnectionFields)
  );

  document.querySelectorAll('input[name="sampleSerialPreset"]').forEach((r) =>
    r.addEventListener("change", () => applySampleSerialPreset(r.value))
  );

  const savedPort = localStorage.getItem("serialPort");
  if (savedPort) {
    const customRadio = document.querySelector('input[name="sampleSerialPreset"][value="custom"]');
    if (customRadio) customRadio.checked = true;
    applySampleSerialPreset("custom");
    const portInput = document.getElementById("sampleSerialPort");
    if (portInput) portInput.value = savedPort;
  } else {
    applySampleSerialPreset("linux_usb");
  }

  document.getElementById("sampleSshHost").value = localStorage.getItem("sshHost") || "";
  document.getElementById("sampleSshUser").value = localStorage.getItem("sshUser") || "";
  document.getElementById("sampleSshPass").value = localStorage.getItem("sshPass") || "";
  const sshPortInput = document.getElementById("sampleSshPort");
  if (sshPortInput) {
    sshPortInput.value = localStorage.getItem("sshPort") || "22";
  }

  sampleCommandSearch?.addEventListener("input", filterSampleCommands);

  document.getElementById("selectAllSampleCmds")?.addEventListener("click", () => {
    document.querySelectorAll('#sampleCommandsList input[type="checkbox"]').forEach((cb) => (cb.checked = true));
  });

  document.getElementById("clearSampleCmds")?.addEventListener("click", () => {
    document.querySelectorAll('#sampleCommandsList input[type="checkbox"]').forEach((cb) => (cb.checked = false));
  });

  document.getElementById("chooseSampleDirBtn")?.addEventListener("click", async () => {
    setStatus("Opening folder picker...");
    const selected = await chooseSampleDirectory();
    if (selected && dirLabel) {
      dirLabel.textContent = selected;
      dirLabel.classList.add("has-value");
      dirLabel.dataset.path = selected;
      setStatus("Folder selected.");
    } else if (!selected) {
      appendSampleLog("No folder selected.");
      setStatus("No folder selected.");
    }
  });

  document.getElementById("sampleCreateFolderBtn")?.addEventListener("click", () => {
    const nameInput = document.getElementById("sampleNewFolderName");
    const folderName = nameInput ? nameInput.value.trim() : "";
    if (!folderName) {
      alert("Enter a folder name first.");
      return;
    }
    if (!pathModule) {
      alert("Folder creation not available in this environment.");
      return;
    }

    let parentPath = dirLabel?.dataset?.path || "";
    if (!parentPath) {
      try {
        const os = require("os");
        parentPath = pathModule.join(os.homedir(), "Documents");
      } catch (_) {
        alert("Unable to resolve Documents path.");
        return;
      }
    }

    const newPath = pathModule.join(parentPath, folderName);
    try {
      require("fs").mkdirSync(newPath, { recursive: true });
      if (dirLabel) {
        dirLabel.textContent = newPath;
        dirLabel.classList.add("has-value");
        dirLabel.dataset.path = newPath;
      }
      setStatus("Folder created.");
      if (nameInput) nameInput.value = "";
    } catch (err) {
      console.error(err);
      alert(`Failed to create folder: ${err.message || err}`);
    }
  });

  document.getElementById("runSampleCollectBtn")?.addEventListener("click", () => {
    setStatus("Starting collection...");
    runSampleCollection();
  });

  toggleSampleConnectionFields();
}



document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("sampleCollectPage")) setupSampleCollectPage();
});
