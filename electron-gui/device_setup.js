const fs = require("fs");
const path = require("path");

document.addEventListener("DOMContentLoaded", async () => {
  loadNavbar();

  const addDeviceBtn = document.getElementById("addDeviceBtn");
  const clearSetupBtn = document.getElementById("clearSetupBtn");
  const saveBtn = document.getElementById("saveTemplateBtn");
  const saveBar = saveBtn?.closest(".ds-save-bar");
  const container = document.getElementById("devicesContainer");
  const templateSelect = document.getElementById("templateSelect");
  const loadTemplateBtn = document.getElementById("loadTemplateBtn");
  const templateNameInput = document.getElementById("templateName");
  const templateConfigCard = document.getElementById("templateConfigCard");
  const templateConfigSubtitle = document.getElementById("templateConfigSubtitle");
  const templateNameHelp = document.getElementById("templateNameHelp");
  const loadExistingGroup = document.getElementById("loadExistingGroup");
  const manualToolbar = document.getElementById("manualToolbar");
  const logsFirstPanel = document.getElementById("logsFirstPanel");
  const fullManualPanel = document.getElementById("fullManualPanel");
  const structureOnlyPanel = document.getElementById("structureOnlyPanel");
  const strictFolderPanel = document.getElementById("strictFolderPanel");
  const chooseLogsFolderBtn = document.getElementById("chooseLogsFolderBtn");
  const logsFolderLabel = document.getElementById("logsFolderLabel");
  const chooseStrictFolderBtn = document.getElementById("chooseStrictFolderBtn");
  const strictFolderLabel = document.getElementById("strictFolderLabel");
  const devicesSubtitle = document.getElementById("devicesSubtitle");
  const deviceConfigCard = document.getElementById("deviceConfigCard");
  const setupModeSelector = document.getElementById("setupModeSelector");
  const clearSetupModeBtn = document.getElementById("clearSetupModeBtn");

  const MODE_FULL_MANUAL = "full_manual";
  const MODE_STRUCTURE_ONLY = "structure_only";
  const MODE_LOGS_FIRST = "logs_first";
  const VALID_MODES = new Set([MODE_FULL_MANUAL, MODE_STRUCTURE_ONLY, MODE_LOGS_FIRST]);

  let deviceCount = 0;
  let systemCommands = [];
  let availableTemplates = [];
  let loadedFromServer = false;
  let selectedTemplateName = "";
  let templateNameProgrammaticUpdate = false;
  let templateNameEditedManually = false;
  let currentMode = "";
  let importedLogsFolder = "";
  let strictLogsFolder = "";
  let loadedTemplateHasBaseline = false;

  function getModeRadio(value) {
    return document.querySelector(`input[name="templateSetupMode"][value="${value}"]`);
  }

  function normalizeMode(value) {
    if (value === "manual") return MODE_STRUCTURE_ONLY;
    if (value === "logs") return MODE_LOGS_FIRST;
    return VALID_MODES.has(value) ? value : "";
  }

  function closeOpenSelects(except = null) {
    document.querySelectorAll(".app-select.open").forEach((node) => {
      if (node !== except) node.classList.remove("open");
    });
  }

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
      if (root.classList.contains("app-select-disabled")) return;
      const isOpen = root.classList.contains("open");
      closeOpenSelects(root);
      root.classList.toggle("open", !isOpen);
      menu?.classList.toggle("hidden", isOpen);
    });

    menu?.querySelectorAll(".app-select-option").forEach((optionNode) => {
      optionNode.addEventListener("click", () => {
        const nextValue = optionNode.dataset.value || "";
        root.dataset.value = nextValue;
        const labelNode = root.querySelector(".app-select-label");
        if (labelNode) labelNode.textContent = optionNode.textContent || placeholder;
        menu.querySelectorAll(".app-select-option").forEach((item) => {
          item.classList.toggle("selected", item === optionNode);
        });
        root.classList.remove("open");
        menu.classList.add("hidden");
        selectedTemplateName = nextValue;
      });
    });
  }

  function normalizeCommandText(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[_./-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function commandLabelFromFilename(filename) {
    const base = path.parse(filename).name;
    return String(base || "")
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function getKnownCommandForFilename(filename) {
    const inferred = normalizeCommandText(commandLabelFromFilename(filename));
    const exact = systemCommands.find((cmd) => normalizeCommandText(cmd) === inferred);
    return exact || commandLabelFromFilename(filename);
  }

  function setExistingTemplateControlsDisabled(disabled, title = "") {
    if (templateSelect) {
      templateSelect.classList.toggle("app-select-disabled", disabled);
      templateSelect.querySelector(".app-select-trigger")?.toggleAttribute("disabled", disabled);
      if (disabled) {
        templateSelect.classList.remove("open");
        templateSelect.querySelector(".app-select-menu")?.classList.add("hidden");
      }
    }
    if (loadTemplateBtn) {
      loadTemplateBtn.disabled = disabled;
      loadTemplateBtn.title = title;
    }
  }

  function syncTemplateControls() {
    const hasManualName = templateNameEditedManually && Boolean(templateNameInput?.value.trim());
    const disableLoad =
      currentMode === MODE_LOGS_FIRST ||
      hasManualName ||
      !currentMode;
    const title = currentMode === MODE_LOGS_FIRST
      ? "Logs First creates a new template from the selected folder."
      : hasManualName
        ? "Clear the Template Name field to load an existing template."
        : "";
    setExistingTemplateControlsDisabled(disableLoad, title);
    if (loadExistingGroup) {
      loadExistingGroup.style.display = currentMode === MODE_LOGS_FIRST ? "none" : "";
    }
  }

  function setFolderLabel(label, folderPath) {
    if (!label) return;
    label.textContent = folderPath || "No folder selected";
    label.classList.toggle("has-value", Boolean(folderPath));
  }

  function setLogsFolder(folderPath) {
    importedLogsFolder = folderPath || "";
    setFolderLabel(logsFolderLabel, importedLogsFolder);
    if (importedLogsFolder) sessionStorage.setItem("deviceSetupLogsFolder", importedLogsFolder);
    else sessionStorage.removeItem("deviceSetupLogsFolder");
  }

  function setStrictFolder(folderPath) {
    strictLogsFolder = folderPath || "";
    setFolderLabel(strictFolderLabel, strictLogsFolder);
    if (strictLogsFolder) sessionStorage.setItem("deviceSetupStrictLogsFolder", strictLogsFolder);
    else sessionStorage.removeItem("deviceSetupStrictLogsFolder");
  }

  function saveModeToStorage() {
    localStorage.removeItem("deviceSetupMode");
    if (currentMode) sessionStorage.setItem("deviceSetupMode", currentMode);
    else sessionStorage.removeItem("deviceSetupMode");
  }

  function updateSaveButtonText() {
    if (!saveBtn) return;
    if (currentMode === MODE_FULL_MANUAL) {
      saveBtn.textContent = "Save Complete Template";
    } else if (currentMode === MODE_STRUCTURE_ONLY) {
      saveBtn.textContent = "Save Device List for Later Collection";
    } else if (currentMode === MODE_LOGS_FIRST) {
      saveBtn.textContent = "Create Template From Logs Folder";
    } else {
      saveBtn.textContent = "";
    }
    saveBtn.disabled = !currentMode;
  }

  function updateModeUI() {
    document.querySelectorAll('input[name="templateSetupMode"]').forEach((radio) => {
      radio.checked = radio.value === currentMode;
    });

    if (templateConfigCard) templateConfigCard.classList.toggle("hidden", !currentMode);
    if (deviceConfigCard) deviceConfigCard.classList.toggle("hidden", !currentMode);
    if (saveBar) saveBar.style.display = currentMode ? "" : "none";
    if (clearSetupModeBtn) clearSetupModeBtn.classList.toggle("is-visible", Boolean(currentMode));

    if (fullManualPanel) fullManualPanel.classList.toggle("hidden", !(currentMode === MODE_FULL_MANUAL && loadedFromServer));
    if (structureOnlyPanel) structureOnlyPanel.classList.add("hidden");
    if (logsFirstPanel) logsFirstPanel.classList.toggle("hidden", currentMode !== MODE_LOGS_FIRST);
    if (strictFolderPanel) strictFolderPanel.classList.toggle("hidden", !(currentMode === MODE_FULL_MANUAL && loadedFromServer));

    if (manualToolbar) manualToolbar.style.display = currentMode === MODE_LOGS_FIRST ? "none" : "";
    if (templateConfigSubtitle) {
      templateConfigSubtitle.textContent = currentMode === MODE_LOGS_FIRST
        ? "Name the complete template created from a collected logs folder"
        : "Create a new template or load an existing one";
    }
    if (templateNameHelp) {
      templateNameHelp.textContent = currentMode === MODE_LOGS_FIRST
        ? "Enter the template name that will be created from the selected logs folder."
        : "Type a new name to create a template, or leave this blank and load an existing template.";
    }
    if (devicesSubtitle) {
      const labels = {
        [MODE_FULL_MANUAL]: loadedFromServer
          ? "Loaded template devices; add missing logs one by one or from a matching folder"
          : "Add devices, select commands, and attach raw logs",
        [MODE_STRUCTURE_ONLY]: "Add devices and commands only; logs will be collected later",
        [MODE_LOGS_FIRST]: "Devices and commands detected from the selected logs folder",
      };
      devicesSubtitle.textContent = labels[currentMode] || "Add and configure network devices";
    }

    container.querySelectorAll(".remove-device-btn").forEach((button) => {
      button.style.display = currentMode === MODE_LOGS_FIRST ? "none" : "";
    });
    container.querySelectorAll(".hostname-input").forEach((input) => {
      input.readOnly = currentMode === MODE_LOGS_FIRST;
    });
    container.querySelectorAll(".custom-dropdown").forEach((dropdown) => {
      const locked = currentMode === MODE_LOGS_FIRST;
      dropdown.style.pointerEvents = locked ? "none" : "";
      dropdown.style.opacity = locked ? "0.65" : "";
    });
    container.querySelectorAll(".cmd-file-input").forEach((input) => {
      const showUpload = currentMode === MODE_FULL_MANUAL;
      input.disabled = !showUpload;
      input.style.display = showUpload ? "" : "none";
    });
    container.querySelectorAll(".command-badge--uploaded").forEach((badge) => {
      badge.style.display = currentMode === MODE_FULL_MANUAL ? "" : "none";
    });

    syncTemplateControls();
    updateSaveButtonText();
  }

  function setMode(mode) {
    currentMode = normalizeMode(mode);
    saveModeToStorage();
    if (currentMode === MODE_LOGS_FIRST) {
      loadedFromServer = false;
      loadedTemplateHasBaseline = false;
      selectedTemplateName = "";
      setStrictFolder("");
      templateNameEditedManually = true;
    }
    if ((currentMode === MODE_FULL_MANUAL || currentMode === MODE_STRUCTURE_ONLY) && !container.children.length) {
      addDeviceBlock();
    }
    updateModeUI();
  }

  function clearDeviceSetup({ clearTemplateName = true } = {}) {
    localStorage.removeItem("templateName");
    localStorage.removeItem("templateDevices");
    localStorage.removeItem("activeTemplateName");
    localStorage.removeItem("activeTemplateDevices");
    localStorage.removeItem("deviceSetupMode");
    localStorage.removeItem("deviceSetupLogsFolder");
    localStorage.removeItem("deviceSetupStrictLogsFolder");
    sessionStorage.removeItem("deviceSetupMode");
    sessionStorage.removeItem("deviceSetupLogsFolder");
    sessionStorage.removeItem("deviceSetupStrictLogsFolder");
    loadedFromServer = false;
    loadedTemplateHasBaseline = false;
    selectedTemplateName = "";
    templateNameEditedManually = !clearTemplateName && Boolean(templateNameInput?.value.trim());
    currentMode = "";
    deviceCount = 0;
    container.innerHTML = "";
    setLogsFolder("");
    setStrictFolder("");
    if (clearTemplateName && templateNameInput) templateNameInput.value = "";
    renderSingleSelect(templateSelect, {
      options: availableTemplates,
      value: "",
      placeholder: "Select a template",
    });
    updateModeUI();
  }

  templateNameInput?.addEventListener("input", () => {
    if (templateNameProgrammaticUpdate) return;
    templateNameEditedManually = Boolean(templateNameInput.value.trim());
    if (templateNameEditedManually && loadedFromServer) {
      loadedFromServer = false;
      loadedTemplateHasBaseline = false;
      selectedTemplateName = "";
      setStrictFolder("");
      localStorage.removeItem("activeTemplateName");
    }
    updateModeUI();
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".app-select")) {
      closeOpenSelects();
      document.querySelectorAll(".app-select-menu").forEach((menu) => menu.classList.add("hidden"));
    }
  });

  try {
    const res = await fetch("http://127.0.0.1:5050/api/commands");
    const data = await res.json();
    if (data.status === "ok") {
      systemCommands = data.commands || [];
    }
  } catch (err) {
    console.warn("Could not fetch commands:", err);
  }

  async function loadTemplateList() {
    if (!templateSelect) return;
    try {
      const res = await fetch("http://127.0.0.1:5050/api/admin/templates");
      const data = await res.json();
      if (data.status === "ok") {
        availableTemplates = data.templates || [];
      }
    } catch (err) {
      console.warn("Could not fetch templates:", err);
    }
    if (!availableTemplates.includes(selectedTemplateName)) {
      selectedTemplateName = "";
    }
    renderSingleSelect(templateSelect, {
      options: availableTemplates,
      value: selectedTemplateName,
      placeholder: "Select a template",
    });
    syncTemplateControls();
  }

  function createCommandBadge(commandText) {
    const row = document.createElement("div");
    row.className = "command-item";
    row.dataset.command = commandText;
    row.innerHTML = `
      <div class="command-meta">
        <input type="text" class="cmd-input" value="${commandText}" readonly />
        <div class="command-badges">
          <small class="command-badge command-badge--uploaded">Mapped</small>
        </div>
      </div>
      <div class="command-actions">
        <input type="file" class="cmd-file-input" accept=".txt,.log,.docx" />
        <button type="button" class="command-remove-btn" title="Remove">✕</button>
      </div>
    `;
    const fileInput = row.querySelector(".cmd-file-input");
    const uploadedBadge = row.querySelector(".command-badge--uploaded");
    fileInput?.addEventListener("change", () => {
      if (!uploadedBadge) return;
      uploadedBadge.textContent = fileInput.files?.length ? "File attached" : "Mapped";
    });
    row.querySelector(".command-remove-btn").addEventListener("click", () => {
      row.remove();
      const block = row.closest(".device-block");
      if (!block) return;
      const checkbox = block.querySelector(`input[type="checkbox"][value="${commandText}"]`);
      if (checkbox) checkbox.checked = false;
      updateDropdownCount(block);
    });
    return row;
  }

  function updateDropdownCount(block) {
    const checkboxes = block.querySelectorAll('.dropdown-item input[type="checkbox"]:checked');
    const label = block.querySelector(".dropdown-header span");
    if (!label) return;
    label.textContent = checkboxes.length ? `${checkboxes.length} Commands Selected` : "Select Commands";
  }

  function closeCommandDropdowns(exceptDropdown = null) {
    document.querySelectorAll(".custom-dropdown").forEach((dropdown) => {
      if (dropdown !== exceptDropdown) {
        dropdown.classList.remove("dropdown-open");
        dropdown.querySelector(".dropdown-list")?.classList.add("hidden");
        const searchInput = dropdown.querySelector(".dropdown-search-input");
        if (searchInput) searchInput.value = "";
        dropdown.querySelectorAll(".dropdown-item.hidden").forEach((item) => item.classList.remove("hidden"));
        dropdown.closest(".device-block")?.classList.remove("dropdown-open");
      }
    });
  }

  function addDeviceBlock({ hostname = "", commands = [] } = {}) {
    deviceCount += 1;
    const deviceId = `device-${deviceCount}`;
    const selectedCommands = new Set(commands);

    const dropdownListHtml = systemCommands.length
      ? systemCommands.map((cmd) => `
          <label class="dropdown-item">
            <input type="checkbox" value="${cmd}" ${selectedCommands.has(cmd) ? "checked" : ""} />
            ${cmd}
          </label>
        `).join("")
      : `<div class="dropdown-empty">No commands in System Admin.</div>`;

    const block = document.createElement("div");
    block.className = "device-block";
    block.id = deviceId;
    block.innerHTML = `
      <div class="device-block-header">
        <div class="device-block-title">Device ${deviceCount}</div>
        <div style="display: flex; gap: 16px; flex: 1; align-items: flex-end;">
          <div style="flex: 1;">
            <label style="display: block; font-weight: 600; color: var(--color-heading); margin-bottom: 6px; font-size: 0.9rem;">Hostname</label>
            <input type="text" class="hostname-input" placeholder="e.g. R1 or S1" value="${hostname}" required />
          </div>
          <div style="flex: 1;">
            <label style="display: block; font-weight: 600; color: var(--color-heading); margin-bottom: 6px; font-size: 0.9rem;">Commands</label>
            <div class="custom-dropdown" id="dropdown-${deviceId}">
              <div class="dropdown-header">
                <span>Select Commands</span>
                <small>▼</small>
              </div>
              <div class="dropdown-list hidden">
                <div class="dropdown-search">
                  <input type="text" class="dropdown-search-input" placeholder="Search commands..." />
                </div>
                ${dropdownListHtml}
              </div>
            </div>
          </div>
        </div>
        <button type="button" class="remove-device-btn">Remove Device</button>
      </div>
      <div class="command-list" id="cmds-${deviceId}"></div>
    `;

    const cmdList = block.querySelector(`#cmds-${deviceId}`);
    const dropdownRoot = block.querySelector(`#dropdown-${deviceId}`);
    const dropdownHeader = block.querySelector(".dropdown-header");
    const dropdownList = block.querySelector(".dropdown-list");
    const dropdownSearch = block.querySelector(".dropdown-search-input");

    dropdownHeader.addEventListener("click", (event) => {
      event.stopPropagation();
      if (currentMode === MODE_LOGS_FIRST) return;
      const willOpen = dropdownList.classList.contains("hidden");
      closeCommandDropdowns(willOpen ? dropdownRoot : null);
      dropdownList.classList.toggle("hidden", !willOpen);
      dropdownRoot.classList.toggle("dropdown-open", willOpen);
      block.classList.toggle("dropdown-open", willOpen);
      if (willOpen) dropdownSearch?.focus();
    });

    dropdownSearch?.addEventListener("click", (event) => event.stopPropagation());
    dropdownSearch?.addEventListener("input", () => {
      const term = normalizeCommandText(dropdownSearch.value);
      block.querySelectorAll(".dropdown-item").forEach((item) => {
        const text = normalizeCommandText(item.textContent);
        item.classList.toggle("hidden", Boolean(term) && !text.includes(term));
      });
    });

    block.querySelectorAll('.dropdown-item input[type="checkbox"]').forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const cmdVal = checkbox.value;
        const existingRow = cmdList.querySelector(`.command-item[data-command="${cmdVal}"]`);
        if (checkbox.checked && !existingRow) {
          cmdList.appendChild(createCommandBadge(cmdVal));
        }
        if (!checkbox.checked && existingRow) {
          existingRow.remove();
        }
        updateDropdownCount(block);
        updateModeUI();
      });
    });

    dropdownList?.addEventListener("click", (event) => event.stopPropagation());
    block.querySelector(".remove-device-btn").addEventListener("click", () => block.remove());

    container.appendChild(block);

    commands.forEach((cmd) => {
      const existingRow = cmdList.querySelector(`.command-item[data-command="${cmd}"]`);
      if (!existingRow) cmdList.appendChild(createCommandBadge(cmd));
    });
    updateDropdownCount(block);
    updateModeUI();

    return block;
  }

  function renderImportedDevices(devicesMeta) {
    container.innerHTML = "";
    deviceCount = 0;
    Object.entries(devicesMeta).forEach(([hostname, commands]) => {
      addDeviceBlock({ hostname, commands });
    });
    if (!Object.keys(devicesMeta).length && currentMode !== MODE_LOGS_FIRST) addDeviceBlock();
    updateModeUI();
  }

  function collectDevicesMeta() {
    const devicesMeta = {};
    const seen = new Set();
    let error = null;

    container.querySelectorAll(".device-block").forEach((block) => {
      if (error) return;
      const hostname = block.querySelector(".hostname-input")?.value.trim() || "";
      if (!hostname) {
        error = "All devices must have a hostname.";
        return;
      }
      if (seen.has(hostname.toLowerCase())) {
        error = `Duplicate hostname "${hostname}" found.`;
        return;
      }
      seen.add(hostname.toLowerCase());
      const commands = Array.from(block.querySelectorAll(".command-item .cmd-input"))
        .map((input) => input.value.trim())
        .filter(Boolean);
      if (!commands.length) {
        error = `Device ${hostname} must have at least one command.`;
        return;
      }
      devicesMeta[hostname] = commands;
    });

    if (error) throw new Error(error);
    if (!Object.keys(devicesMeta).length) throw new Error("Please add at least one device.");
    return devicesMeta;
  }

  function collectManualFiles() {
    const manualFiles = [];
    container.querySelectorAll(".device-block").forEach((block) => {
      const hostname = block.querySelector(".hostname-input")?.value.trim() || "";
      if (!hostname) return;
      block.querySelectorAll(".command-item").forEach((row) => {
        const command = row.querySelector(".cmd-input")?.value.trim() || "";
        const file = row.querySelector(".cmd-file-input")?.files?.[0];
        if (command && file) {
          manualFiles.push({ hostname, command, file });
        }
      });
    });
    return manualFiles;
  }

  function templateDetailsHaveBaseline(data) {
    if (data?.has_baseline) return true;
    const logsByCommand = data?.logs_by_command || {};
    return Object.values(logsByCommand).some((commands) => {
      return commands && typeof commands === "object" && Object.keys(commands).length > 0;
    });
  }

  function scanLogsFolder(folderPath) {
    const devicesMeta = {};
    const entries = fs.readdirSync(folderPath, { withFileTypes: true });
    entries
      .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))
      .forEach((entry) => {
        const hostDir = path.join(folderPath, entry.name);
        const commands = fs.readdirSync(hostDir, { withFileTypes: true })
          .filter((child) => child.isFile() && !child.name.startsWith("."))
          .map((child) => getKnownCommandForFilename(child.name))
          .filter(Boolean);
        if (commands.length) {
          devicesMeta[entry.name] = Array.from(new Set(commands));
        }
      });
    return devicesMeta;
  }

  async function selectFolder() {
    if (!ipcRenderer?.invoke) {
      alert("Folder picker is not available.");
      return "";
    }
    return (await ipcRenderer.invoke("select-directory")) || "";
  }

  async function chooseLogsFolder() {
    const selected = await selectFolder();
    if (!selected) return;
    setLogsFolder(selected);
    try {
      const devicesMeta = scanLogsFolder(selected);
      renderImportedDevices(devicesMeta);
      if (!Object.keys(devicesMeta).length) {
        alert("No device folders with log files were found in the selected folder.");
      }
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to scan the selected logs folder.");
    }
  }

  async function chooseStrictFolder() {
    if (!loadedFromServer) {
      alert("Load an existing template before using strict folder fill.");
      return;
    }
    const selected = await selectFolder();
    if (selected) setStrictFolder(selected);
  }

  async function loadTemplateFromServer(templateName) {
    if (!templateName) return;
    try {
      const res = await fetch(`http://127.0.0.1:5050/api/templates/${encodeURIComponent(templateName)}`);
      const data = await res.json();
      if (data.status !== "ok") {
        throw new Error(data.message || "Failed to load template.");
      }

      const devicesMeta = data.devices_meta || {};
      localStorage.setItem("templateName", templateName);
      if (typeof window.updateGlobalTemplateBadge === "function") window.updateGlobalTemplateBadge();
      localStorage.setItem("templateDevices", JSON.stringify(devicesMeta));
      localStorage.setItem("activeTemplateName", templateName);
      localStorage.setItem("activeTemplateDevices", JSON.stringify(devicesMeta));
      loadedFromServer = true;
      loadedTemplateHasBaseline = templateDetailsHaveBaseline(data);
      selectedTemplateName = templateName;
      if (templateNameInput) {
        templateNameProgrammaticUpdate = true;
        templateNameInput.value = templateName;
        templateNameProgrammaticUpdate = false;
      }
      templateNameEditedManually = false;
      renderImportedDevices(devicesMeta);
      updateModeUI();
      saveBtn.disabled = false;
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to load template.");
    }
  }

  function restoreState() {
    localStorage.removeItem("deviceSetupMode");
    localStorage.removeItem("deviceSetupLogsFolder");
    localStorage.removeItem("deviceSetupStrictLogsFolder");
    const savedName = localStorage.getItem("templateName") || "";
    const savedDevicesStr = localStorage.getItem("templateDevices") || "";
    const savedMode = normalizeMode(sessionStorage.getItem("deviceSetupMode") || "");
    const savedLogsFolder = sessionStorage.getItem("deviceSetupLogsFolder") || "";
    const savedStrictFolder = sessionStorage.getItem("deviceSetupStrictLogsFolder") || "";

    currentMode = "";
    loadedFromServer = false;
    loadedTemplateHasBaseline = false;
    selectedTemplateName = "";
    templateNameEditedManually = false;
    container.innerHTML = "";

    if (savedMode) setMode(savedMode);
    else updateModeUI();

    if (savedName && templateNameInput) {
      templateNameProgrammaticUpdate = true;
      templateNameInput.value = savedName;
      templateNameProgrammaticUpdate = false;
      selectedTemplateName = savedName;
    } else if (templateNameInput) {
      templateNameInput.value = "";
    }
    setLogsFolder(savedLogsFolder);
    setStrictFolder(savedStrictFolder);

    if (savedName && savedMode && savedMode !== MODE_LOGS_FIRST) {
      loadTemplateFromServer(savedName);
      return;
    }

    if (savedDevicesStr) {
      try {
        const devicesMeta = JSON.parse(savedDevicesStr);
        if (devicesMeta && Object.keys(devicesMeta).length) {
          renderImportedDevices(devicesMeta);
          return;
        }
      } catch (err) {
        console.warn("Failed to parse saved devices:", err);
      }
    }

    updateModeUI();
  }

  function persistTemplateState(templateName, devicesMeta) {
    localStorage.setItem("templateName", templateName);
    if (typeof window.updateGlobalTemplateBadge === "function") window.updateGlobalTemplateBadge();
    localStorage.setItem("templateDevices", JSON.stringify(devicesMeta));
    localStorage.setItem("activeTemplateName", templateName);
    localStorage.setItem("activeTemplateDevices", JSON.stringify(devicesMeta));
    localStorage.removeItem("deviceSetupMode");
    if (currentMode) sessionStorage.setItem("deviceSetupMode", currentMode);
  }

  document.querySelectorAll('input[name="templateSetupMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const selectedMode = normalizeMode(radio.value);
      if (selectedMode === currentMode) return;
      clearDeviceSetup({ clearTemplateName: false });
      setMode(selectedMode);
    });
  });

  setupModeSelector?.querySelectorAll(".radio-card").forEach((card) => {
    card.addEventListener("click", () => {
      const radio = card.querySelector('input[name="templateSetupMode"]');
      if (!radio) return;
      const selectedMode = normalizeMode(radio.value);
      if (selectedMode === currentMode) return;
      clearDeviceSetup({ clearTemplateName: false });
      setMode(selectedMode);
    });
  });

  clearSetupModeBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    clearDeviceSetup();
  });

  chooseLogsFolderBtn?.addEventListener("click", chooseLogsFolder);
  chooseStrictFolderBtn?.addEventListener("click", chooseStrictFolder);
  addDeviceBtn?.addEventListener("click", () => addDeviceBlock());
  clearSetupBtn?.addEventListener("click", () => {
    if (!confirm("Clear all device setup data? This will remove saved template settings from this app.")) return;
    clearDeviceSetup();
  });
  loadTemplateBtn?.addEventListener("click", () => {
    if (loadTemplateBtn.disabled) return;
    const selected = templateSelect?.dataset.value || selectedTemplateName;
    if (!selected) {
      alert("Please select a template to load.");
      return;
    }
    loadTemplateFromServer(selected);
  });

  saveBtn?.addEventListener("click", async () => {
    const templateName = templateNameInput?.value.trim() || "";
    const sourceTemplateName = loadedFromServer && selectedTemplateName ? selectedTemplateName : "";
    if (!currentMode) {
      alert("Choose a setup mode first.");
      return;
    }
    if (!templateName) {
      alert("Enter a template name first.");
      return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = "Saving...";

    try {
      let response;
      if (currentMode === MODE_LOGS_FIRST) {
        if (!importedLogsFolder) throw new Error("Choose a collected logs folder first.");
        response = await fetchJson("/api/templates/import_logs_folder", {
          method: "POST",
          body: JSON.stringify({
            template_name: templateName,
            source_dir: importedLogsFolder,
          }),
        });
        sessionStorage.setItem("deviceSetupLogsFolder", importedLogsFolder);
      } else {
        const devicesMeta = collectDevicesMeta();

        if (currentMode === MODE_STRUCTURE_ONLY) {
          response = await fetchJson("/api/templates/save_setup", {
            method: "POST",
            body: JSON.stringify({
              template_name: templateName,
              source_template_name: sourceTemplateName,
              devices_meta: devicesMeta,
            }),
          });
        } else if (strictLogsFolder && loadedFromServer) {
          response = await fetchJson("/api/templates/import_logs_folder", {
            method: "POST",
            body: JSON.stringify({
              template_name: templateName,
              source_template_name: sourceTemplateName,
              source_dir: strictLogsFolder,
              devices_meta: devicesMeta,
              strict: true,
            }),
          });
          sessionStorage.setItem("deviceSetupStrictLogsFolder", strictLogsFolder);
        } else {
          const manualFiles = collectManualFiles();
          if (!manualFiles.length) {
            if (!loadedFromServer || !loadedTemplateHasBaseline) {
              throw new Error("Attach log files for at least one selected command, or load an existing complete template and choose a folder fill.");
            }
            response = await fetchJson("/api/templates/save_setup", {
              method: "POST",
              body: JSON.stringify({
                template_name: templateName,
                source_template_name: sourceTemplateName,
                devices_meta: devicesMeta,
              }),
            });
          } else {
            const formData = new FormData();
            formData.append("template_name", templateName);
            formData.append("source_template_name", sourceTemplateName);
            formData.append("devices_meta", JSON.stringify(devicesMeta));
            manualFiles.forEach(({ hostname, command, file }) => {
              formData.append(`file_${hostname}_${command}`, file);
            });

            const res = await fetch(`${window.API_ROOT}/api/templates/upload`, {
              method: "POST",
              body: formData,
            });
            response = await parseJsonResponse(res);
            response.devices_meta = response.results?.devices_meta || devicesMeta;
          }
        }
      }

      const devicesMeta =
        response.devices_meta ||
        response.results?.devices_meta ||
        collectDevicesMeta();
      persistTemplateState(templateName, devicesMeta);

      if (currentMode === MODE_LOGS_FIRST) {
        alert("Template baseline imported from the collected logs folder.");
      } else if (currentMode === MODE_STRUCTURE_ONLY) {
        alert("Template structure saved without baseline logs. You can collect logs later from Sample Collect.");
      } else if (response.ignored || response.missing) {
        const ignoredDevices = response.ignored?.devices?.length || 0;
        const ignoredCommands = response.ignored?.commands?.length || 0;
        const missingDevices = Object.keys(response.missing || {}).length;
        alert(`Template filled from folder. Ignored devices: ${ignoredDevices}. Ignored command files: ${ignoredCommands}. Devices with missing logs: ${missingDevices}.`);
      } else {
        alert("Template setup saved and uploaded baseline logs were processed.");
      }
      goTo("directory.html");
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to save template.");
    } finally {
      updateSaveButtonText();
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".custom-dropdown") && !event.target.closest(".dropdown-list")) {
      closeCommandDropdowns();
    }
  });

  await loadTemplateList();
  restoreState();
});
