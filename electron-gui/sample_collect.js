// Sample Collect lets lecturers collect baseline logs after a structure-only
// template exists, or manually collect logs without a saved template. It shares
// the same live terminal stream as Connection but writes into template folders.
function setupSampleCollectPage() {
  loadNavbar();

  const statusBox = document.getElementById("sampleStatus");
  const list = document.getElementById("sampleCommandsList");
  const log = document.getElementById("sampleLog");
  const dirLabel = document.getElementById("sampleDirLabel");
  const sampleCommandSearch = document.getElementById("sampleCommandSearch");
  const sampleTemplateSelect = document.getElementById("sampleTemplateSelect");
  const sampleTemplateInfo = document.getElementById("sampleTemplateInfo");
  const sampleTemplateDevices = document.getElementById("sampleTemplateDevices");
  const sampleActiveDeviceInfo = document.getElementById("sampleActiveDeviceInfo");
  const runSampleCollectBtn = document.getElementById("runSampleCollectBtn");
  const sampleTerminalLog = document.getElementById("sampleTerminalLog");
  const stopSampleCollectBtn = document.getElementById("stopSampleCollectBtn");
  const sampleTemplatePanel = document.getElementById("sampleTemplatePanel");
  const sampleManualPanel = document.getElementById("sampleManualPanel");

  let availableTemplates = [];
  let selectedTemplateName = "";
  let activeTemplateName = "";
  let templateDevicesMeta = {};
  let activeTemplateDevice = "";
  let currentSampleMode = "";
  let collectedDevices = [];
  let sampleAbortController = null;
  let flaskLogListenerAttached = false;

  const setStatus = (msg) => {
    if (!statusBox) return;
    const msgEl = statusBox.querySelector(".status-banner__msg");
    if (msgEl) msgEl.textContent = msg;
    else statusBox.textContent = msg;
  };

  const appendSampleLog = (msg) => {
    if (!log) return;
    log.textContent += `${msg}\n`;
    log.scrollTop = log.scrollHeight;
  };

  const appendTerminalLine = (msg) => {
    if (!sampleTerminalLog) return;
    // This panel mirrors backend stdout/stderr, not the cleaned activity log.
    sampleTerminalLog.textContent += `${msg}\n`;
    sampleTerminalLog.scrollTop = sampleTerminalLog.scrollHeight;
  };

  const clearTerminalLog = () => {
    if (sampleTerminalLog) sampleTerminalLog.textContent = "";
  };

  function setupSampleCollectKeyboardShortcuts() {
    // Match Connection page shortcuts: Escape stops a live collection and Enter
    // starts only when focus is not inside a form control.
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented) return;

      if (event.key === "Escape") {
        const stopVisible =
          stopSampleCollectBtn &&
          !stopSampleCollectBtn.disabled &&
          stopSampleCollectBtn.style.display !== "none" &&
          stopSampleCollectBtn.offsetParent !== null;

        if (stopVisible) {
          event.preventDefault();
          stopSampleCollectBtn.click();
        }
        return;
      }

      if (event.key !== "Enter") return;

      const tag = (event.target?.tagName || "").toUpperCase();
      if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag)) return;

      if (runSampleCollectBtn && !runSampleCollectBtn.disabled) {
        event.preventDefault();
        runSampleCollectBtn.click();
      }
    });
  }

  function stopSampleCollection() {
    if (sampleAbortController) {
      sampleAbortController.abort();
    }
    fetch(`${API_ROOT}/api/abort`, { method: "POST" }).catch(() => {});
  }

  function setupSampleLogTabs() {
    const tabsRoot = document.getElementById("sampleLogTabs");
    if (!tabsRoot) return;
    const tabs = Array.from(tabsRoot.querySelectorAll(".log-tab"));
    const panels = tabs
      .map((tab) => document.getElementById(tab.dataset.target || ""))
      .filter(Boolean);

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        const targetId = tab.dataset.target || "";
        tabs.forEach((item) => item.classList.toggle("active", item === tab));
        panels.forEach((panel) => panel.classList.toggle("hidden", panel.id !== targetId));
      });
    });
  }

  function attachFlaskTerminalListener() {
    if (!window.ipcRenderer || flaskLogListenerAttached) return;
    window.ipcRenderer.on("flask-log", (_event, line) => {
      appendTerminalLine(line);
    });
    flaskLogListenerAttached = true;
  }

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

  function renderSingleSelect(root, { options = [], value = "", placeholder = "Select" } = {}) {
    if (!root) return;
    const normalizedOptions = options.map((option) =>
      typeof option === "string" ? { value: option, label: option } : option
    );
    const selected = normalizedOptions.find((option) => option.value === value) || null;
    root.dataset.value = selected ? selected.value : "";
    root.innerHTML = `
      <button type="button" class="app-select-trigger">
        <span class="app-select-label">${selected ? selected.label : placeholder}</span>
        <span class="app-select-caret">▼</span>
      </button>
      <div class="app-select-menu hidden">
        ${normalizedOptions.map((option) => `
          <div class="app-select-option ${option.value === root.dataset.value ? "selected" : ""}" data-value="${option.value}">
            ${option.label}
          </div>
        `).join("")}
      </div>
    `;

    const trigger = root.querySelector(".app-select-trigger");
    const menu = root.querySelector(".app-select-menu");
    trigger?.addEventListener("click", (event) => {
      event.stopPropagation();
      const isOpen = root.classList.contains("open");
      document.querySelectorAll(".app-select.open").forEach((node) => {
        if (node !== root) {
          node.classList.remove("open");
          node.querySelector(".app-select-menu")?.classList.add("hidden");
        }
      });
      root.classList.toggle("open", !isOpen);
      menu?.classList.toggle("hidden", isOpen);
    });

    menu?.querySelectorAll(".app-select-option").forEach((optionNode) => {
      optionNode.addEventListener("click", () => {
        const nextValue = optionNode.dataset.value || "";
        root.dataset.value = nextValue;
        root.querySelector(".app-select-label").textContent = optionNode.textContent || placeholder;
        menu.querySelectorAll(".app-select-option").forEach((item) => {
          item.classList.toggle("selected", item === optionNode);
        });
        root.classList.remove("open");
        menu.classList.add("hidden");
        selectedTemplateName = nextValue;
      });
    });
  }

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

  async function resetCiscoDevice() {
    const conn = document.querySelector('input[name="sampleConnType"]:checked')?.value || "serial";
    if (conn !== "serial") {
      alert("Cisco reset is only supported in serial mode.");
      return false;
    }

    const resetType = document.getElementById("sampleResetDeviceType")?.value || "switch";
    const message = resetType === "router"
      ? "This will reload the connected Cisco router without saving the running configuration. Continue?"
      : "This will delete vlan.dat and reload the connected Cisco switch without saving the running configuration. Continue?";
    if (!confirm(message)) return false;

    const port = document.getElementById("sampleSerialPort")?.value.trim() || localStorage.getItem("serialPort") || SERIAL_PRESETS.linux_usb;
    if (!port) {
      alert("Please configure the serial port first.");
      return false;
    }

    const resetBtn = document.getElementById("sampleResetDeviceBtn");
    const originalText = resetBtn?.textContent || "Reset Cisco Device";
    if (resetBtn) {
      resetBtn.disabled = true;
      resetBtn.textContent = "Resetting...";
    }

    appendSampleLog(`[${nowTimestamp()}] Starting Cisco reset on ${port}...`);
    try {
      sampleAbortController = new AbortController();
      const response = await fetch(`${API_ROOT}/api/reset_device`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          connection: "serial",
          mode: "serial",
          device_type: resetType,
          serial: { port },
        }),
        signal: sampleAbortController.signal,
      });

      let data = {};
      try {
        data = await response.json();
      } catch (_) {
        data = {};
      }

      const logs = Array.isArray(data.logs) ? data.logs : [];
      logs.forEach((line) => appendSampleLog(line));

      if (!response.ok || data.status === "error") {
        const errMsg = data.message || "Cisco reset failed.";
        appendSampleLog(`[ERROR] ${errMsg}`);
        alert(errMsg);
        return false;
      }

      const okMsg = data.message || "Reset command sent successfully.";
      appendSampleLog(`[SUCCESS] ${okMsg}`);
      alert(okMsg);
      return true;
    } catch (err) {
      if (err.name === "AbortError") {
        appendSampleLog("[STOPPED] Cisco reset stopped by user.");
        return false;
      }
      appendSampleLog(`[ERROR] ${err.message || err}`);
      alert(err.message || "Cisco reset failed.");
      return false;
    } finally {
      sampleAbortController = null;
      if (resetBtn) {
        resetBtn.disabled = false;
        resetBtn.textContent = originalText;
      }
    }
  }

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
    if (activeTemplateName && activeTemplateDevice) {
      return templateDevicesMeta[activeTemplateDevice] || [];
    }
    const checkboxes = document.querySelectorAll('#sampleCommandsList input[type="checkbox"]');
    return Array.from(checkboxes)
      .filter((cb) => cb.checked)
      .map((cb) => cb.value);
  };

  function setSampleMode(mode) {
    currentSampleMode = mode || "";
    localStorage.setItem("sampleCollectMode", currentSampleMode);

    document.querySelectorAll('input[name="sampleMode"]').forEach((radio) => {
      radio.checked = radio.value === currentSampleMode;
    });

    if (sampleTemplatePanel) sampleTemplatePanel.classList.toggle("hidden", currentSampleMode !== "template");
    if (sampleManualPanel) sampleManualPanel.classList.toggle("hidden", currentSampleMode !== "manual");

    const selector = document.getElementById("sampleModeSelector");
    if (selector) selector.classList.toggle("sample-blocked", Boolean(currentSampleMode));

    if (runSampleCollectBtn) {
      runSampleCollectBtn.disabled = !currentSampleMode;
    }

    updateTemplateModeUI();
  }

  function resetSampleMode() {
    currentSampleMode = "";
    localStorage.removeItem("sampleCollectMode");
    document.querySelectorAll('input[name="sampleMode"]').forEach((radio) => {
      radio.checked = false;
    });
    if (sampleTemplatePanel) sampleTemplatePanel.classList.add("hidden");
    if (sampleManualPanel) sampleManualPanel.classList.add("hidden");
    const selector = document.getElementById("sampleModeSelector");
    if (selector) selector.classList.remove("sample-blocked");
    clearTemplateMode({ preserveMode: true });
    if (runSampleCollectBtn) {
      runSampleCollectBtn.textContent = "Collect Logs";
      runSampleCollectBtn.disabled = true;
    }
    setStatus("Choose a collection mode.");
  }

  function updateTemplateModeUI() {
    const inTemplateMode = currentSampleMode === "template";
    const hasLoadedTemplate = inTemplateMode && Boolean(activeTemplateName);
    
    const tplView = document.getElementById("sampleTemplateView");
    if (tplView) tplView.classList.toggle("hidden", !hasLoadedTemplate);

    if (!inTemplateMode) {
      if (runSampleCollectBtn) runSampleCollectBtn.textContent = "Collect Logs";
    } else if (hasLoadedTemplate) {
      const commands = templateDevicesMeta[activeTemplateDevice] || [];
      if (sampleTemplateInfo) {
        sampleTemplateInfo.textContent = `Template loaded: ${activeTemplateName}`;
      }
      if (sampleActiveDeviceInfo) {
        sampleActiveDeviceInfo.textContent = activeTemplateDevice
          ? `Active device: ${activeTemplateDevice} (${commands.length} commands)`
          : "Select a device from the template list.";
      }
      if (runSampleCollectBtn) {
        runSampleCollectBtn.textContent = activeTemplateDevice ? `Collect ${activeTemplateDevice}` : "Collect Logs";
      }
    } else if (runSampleCollectBtn) {
      runSampleCollectBtn.textContent = "Collect Logs";
    }
  }

  function renderTemplateDevices() {
    sampleTemplateDevices.innerHTML = "";
    const hostnames = Object.keys(templateDevicesMeta);
    if (!hostnames.length) {
      sampleTemplateDevices.innerHTML = `<div class="hint">This template has no devices configured.</div>`;
      updateTemplateModeUI();
      return;
    }

    hostnames.forEach((hostname) => {
      const commands = templateDevicesMeta[hostname] || [];
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.style.textAlign = "left";
      button.style.display = "block";
      button.style.width = "100%";
      button.innerHTML = `
        <strong>${hostname}</strong><br>
        <span style="font-size:0.85rem;color:var(--color-muted);">${commands.length} command${commands.length === 1 ? "" : "s"}</span>
        <div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:6px;">
          ${commands.length
            ? commands
                .map(
                  (cmd) => `<code style="font-size:0.78rem; background:rgba(0,0,0,0.05); padding:3px 6px; border-radius:4px;">${cmd}</code>`
                )
                .join("") 
            : `<span style="font-size:0.8rem;color:var(--color-muted);">No commands</span>`}
        </div>
      `;
      if (hostname === activeTemplateDevice) {
        button.style.borderColor = "var(--color-primary)";
        button.style.background = "rgba(31,59,115,0.08)";
      }
      button.addEventListener("click", () => {
        activeTemplateDevice = hostname;
        renderTemplateDevices();
        updateTemplateModeUI();
      });
      sampleTemplateDevices.appendChild(button);
    });
    updateTemplateModeUI();
  }

  async function loadTemplateList() {
    try {
      const res = await fetchJson("/api/admin/templates");
      availableTemplates = res.templates || [];
    } catch (err) {
      console.warn("Could not fetch templates:", err);
      availableTemplates = [];
    }
    renderSingleSelect(sampleTemplateSelect, {
      options: availableTemplates,
      value: selectedTemplateName,
      placeholder: "Select a template",
    });
  }

  async function loadTemplateIntoSampleCollect(templateName) {
    if (!templateName) {
      alert("Please select a template first.");
      return;
    }
    const data = await fetchJson(`/api/templates/${encodeURIComponent(templateName)}`);
    templateDevicesMeta = data.devices_meta || {};
    activeTemplateName = templateName;
    activeTemplateDevice = Object.keys(templateDevicesMeta)[0] || "";
    localStorage.setItem("sampleCollectTemplate", templateName);
    setSampleMode("template");
    renderTemplateDevices();
    updateTemplateModeUI();
  }

  function renderCollectedDevices() {
    const listEl = document.getElementById("collectedDevicesList");
    const hint = document.getElementById("noDevicesHint");
    if (!listEl) return;
    
    if (collectedDevices.length > 0) {
      if (hint) hint.style.display = "none";
      Array.from(listEl.children).forEach(child => {
        if (child.id !== "noDevicesHint") child.remove();
      });
      collectedDevices.forEach(dev => {
        const badge = document.createElement("span");
        badge.className = "collected-badge";
        badge.innerHTML = `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>${dev}`;
        listEl.appendChild(badge);
      });
    } else {
      if (hint) hint.style.display = "block";
      Array.from(listEl.children).forEach(child => {
        if (child.id !== "noDevicesHint") child.remove();
      });
    }
  }

  function clearTemplateMode({ preserveMode = false } = {}) {
    activeTemplateName = "";
    templateDevicesMeta = {};
    activeTemplateDevice = "";
    localStorage.removeItem("sampleCollectTemplate");
    if (sampleTemplateDevices) sampleTemplateDevices.innerHTML = "";
    updateTemplateModeUI();
  }

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
    if (!currentSampleMode) {
      alert("Choose a collection mode first.");
      return;
    }
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

    if (activeTemplateName && activeTemplateDevice) {
      payload.target_device = activeTemplateDevice;
      payload.deviceId = activeTemplateDevice;
      payload.skip_hostname_check = true;
    }

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

    if (runSampleCollectBtn) {
      runSampleCollectBtn.disabled = true;
    }
    if (stopSampleCollectBtn) {
      stopSampleCollectBtn.style.display = "inline-block";
      stopSampleCollectBtn.disabled = false;
    }

    if (log) log.textContent = "";
    appendSampleLog(`[${nowTimestamp()}] Starting collection${activeTemplateDevice ? ` for ${activeTemplateDevice}` : ""}...`);

    try {
      sampleAbortController = new AbortController();
      const res = await fetch(`${API_ROOT}/api/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: sampleAbortController.signal,
      });

      if (!res.ok || !res.body) {
        throw new Error("Failed to start collection.");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let hasError = false;
      let lastDiscoveredHostname = "";

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
            } else if (payloadObj.type === "progress") {
              appendSampleLog(`[${nowTimestamp()}] ${payloadObj.msg || "Working..."}`);
            } else if (payloadObj.type === "raw_output") {
              appendTerminalLine(payloadObj.msg || "");
            } else if (payloadObj.type === "result") {
              if (payloadObj.hostname) lastDiscoveredHostname = payloadObj.hostname;
              if (payloadObj.msg) appendSampleLog(`[RESULT] ${payloadObj.msg}`);
            } else if (payloadObj.type === "done") {
              appendSampleLog(`[DONE] ${payloadObj.msg || "Finished"}`);
            } else if (payloadObj.msg) {
              appendSampleLog(JSON.stringify(payloadObj));
            }
          } catch (_) {
            appendTerminalLine(line.trim());
          }
        }
      }

      if (hasError) {
        alert("Collection finished with errors. Check the log.");
      } else {
        alert("Logs collected successfully.");
        
        const collectedName = (activeTemplateName && activeTemplateDevice) ? activeTemplateDevice : (lastDiscoveredHostname || "Unknown Device");
        if (!collectedDevices.includes(collectedName)) {
          collectedDevices.push(collectedName);
          renderCollectedDevices();
        }

        if (activeTemplateName && activeTemplateDevice) {
          const hostnames = Object.keys(templateDevicesMeta);
          const currentIndex = hostnames.indexOf(activeTemplateDevice);
          if (currentIndex >= 0 && currentIndex < hostnames.length - 1) {
            activeTemplateDevice = hostnames[currentIndex + 1];
            renderTemplateDevices();
          }
        }
      }
    } catch (err) {
      if (err.name === "AbortError") {
        appendSampleLog("[STOPPED] Collection stopped by user.");
        setStatus("Collection stopped.");
        return;
      }
      console.error(err);
      alert(err.message || "Collection failed.");
    } finally {
      sampleAbortController = null;
      if (runSampleCollectBtn) {
        runSampleCollectBtn.disabled = false;
      }
      if (stopSampleCollectBtn) {
        stopSampleCollectBtn.style.display = "none";
        stopSampleCollectBtn.disabled = false;
      }
    }
  };

  setStatus("Loading commands...");
  attachFlaskTerminalListener();
  setupSampleLogTabs();
  setupSampleCollectKeyboardShortcuts();

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

  document.querySelectorAll('input[name="sampleMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      setSampleMode(radio.value);
      if (radio.value === "manual") {
        clearTemplateMode({ preserveMode: true });
      }
      setStatus(radio.value === "template" ? "Template mode selected." : "Manual mode selected.");
    });
  });

  document.getElementById("loadSampleTemplateBtn")?.addEventListener("click", async () => {
    try {
      await loadTemplateIntoSampleCollect(sampleTemplateSelect?.dataset.value || selectedTemplateName);
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to load template.");
    }
  });

  document.getElementById("cancelTemplateModeBtn")?.addEventListener("click", resetSampleMode);
  document.getElementById("cancelManualModeBtn")?.addEventListener("click", resetSampleMode);
  document.getElementById("sampleResetDeviceBtn")?.addEventListener("click", resetCiscoDevice);
  stopSampleCollectBtn?.addEventListener("click", stopSampleCollection);

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

  runSampleCollectBtn?.addEventListener("click", () => {
    setStatus("Starting collection...");
    runSampleCollection();
  });

  toggleSampleConnectionFields();
  resetSampleMode();

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".app-select")) {
      document.querySelectorAll(".app-select.open").forEach((node) => {
        node.classList.remove("open");
        node.querySelector(".app-select-menu")?.classList.add("hidden");
      });
    }
  });

  (async () => {
    await loadCommands();
    await loadTemplateList();
    const savedMode = localStorage.getItem("sampleCollectMode") || "";
    const savedTemplate = localStorage.getItem("sampleCollectTemplate") || localStorage.getItem("templateName");
    if (savedTemplate && availableTemplates.includes(savedTemplate)) {
      selectedTemplateName = savedTemplate;
      renderSingleSelect(sampleTemplateSelect, {
        options: availableTemplates,
        value: selectedTemplateName,
        placeholder: "Select a template",
      });
      try {
        await loadTemplateIntoSampleCollect(savedTemplate);
      } catch (err) {
        console.warn("Could not auto-load sample template:", err);
      }
    } else if (savedMode === "manual") {
      setSampleMode("manual");
      setStatus("Manual mode selected.");
    } else {
      setStatus("Choose a collection mode.");
    }
  })();
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("sampleCollectPage")) setupSampleCollectPage();
});
