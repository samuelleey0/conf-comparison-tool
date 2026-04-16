// electron-gui/device_setup.js

document.addEventListener("DOMContentLoaded", async () => {
  loadNavbar();
  
  const addDeviceBtn = document.getElementById("addDeviceBtn");
  const clearSetupBtn = document.getElementById("clearSetupBtn");
  const saveBtn = document.getElementById("saveTemplateBtn");
  const container = document.getElementById("devicesContainer");
  const templateSelect = document.getElementById("templateSelect");
  const loadTemplateBtn = document.getElementById("loadTemplateBtn");
  
  let deviceCount = 0;
  let systemCommands = [];
  let availableTemplates = [];
  let loadedFromServer = false;
  let selectedTemplateName = "";

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

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".app-select")) {
      closeOpenSelects();
      document.querySelectorAll(".app-select-menu").forEach((menu) => menu.classList.add("hidden"));
    }
  });

  // Fetch all commands from backend
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
  }

  function createCommandRow(commandText = "", existingFileName = null) {
    const row = document.createElement("div");
    row.className = "command-item";
    row.dataset.command = commandText;
    
    row.innerHTML = `
      <div class="command-meta">
        <input type="text" class="cmd-input" value="${commandText}" readonly />
        <div class="command-badges"></div>
      </div>
      <div class="command-actions">
        <input type="file" class="cmd-file" />
        <button type="button" class="command-remove-btn" title="Remove">✕</button>
      </div>
    `;

    const fileInput = row.querySelector(".cmd-file");
    const badges = row.querySelector(".command-badges");
    if (existingFileName) {
      const fileLabel = document.createElement("small");
      fileLabel.className = "command-badge command-badge--loaded";
      fileLabel.textContent = `Loaded: ${existingFileName}`;
      badges?.appendChild(fileLabel);
      fileInput.required = false;
    }

    // Removing from row should also uncheck the dropdown box
    row.querySelector(".command-remove-btn").addEventListener("click", () => {
      row.remove();
      const deviceBlock = row.closest(".device-block");
      if (deviceBlock) {
        const checkbox = deviceBlock.querySelector(`input[type="checkbox"][value="${commandText}"]`);
        if (checkbox) checkbox.checked = false;
        updateDropdownCount(deviceBlock);
      }
    });

    return row;
  }

  function updateDropdownCount(block) {
    const checkboxes = block.querySelectorAll('.dropdown-item input[type="checkbox"]:checked');
    const label = block.querySelector('.dropdown-header span');
    if (checkboxes.length === 0) {
      label.textContent = "Select Commands";
    } else {
      label.textContent = `${checkboxes.length} Commands Selected`;
    }
  }

  function normalizeCommandSearch(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[_./-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function closeCommandDropdowns(exceptDropdown = null) {
    document.querySelectorAll(".custom-dropdown").forEach((dropdown) => {
      if (dropdown !== exceptDropdown) {
        dropdown.classList.remove("dropdown-open");
        dropdown.querySelector(".dropdown-list")?.classList.add("hidden");
        const searchInput = dropdown.querySelector(".dropdown-search-input");
        if (searchInput) searchInput.value = "";
        dropdown.querySelectorAll(".dropdown-item.hidden").forEach((item) => {
          item.classList.remove("hidden");
        });
        dropdown.closest(".device-block")?.classList.remove("dropdown-open");
      }
    });
  }

  function addDeviceBlock() {
    deviceCount++;
    const deviceId = `device-${deviceCount}`;
    
    const block = document.createElement("div");
    block.className = "device-block";
    block.id = deviceId;
    
    // Build the dropdown checklist HTML dynamically
    let dropdownListHtml = systemCommands.map(cmd => `
      <label class="dropdown-item">
        <input type="checkbox" value="${cmd}" />
        ${cmd}
      </label>
    `).join("");

    if (systemCommands.length === 0) {
      dropdownListHtml = `<div class="dropdown-empty">No commands in System Admin.</div>`;
    }

    block.innerHTML = `
      <div class="device-block-header">
        <div class="device-block-title">Device ${deviceCount}</div>
        
        <div style="display: flex; gap: 16px; flex: 1; align-items: flex-end;">
          <div style="flex: 1;">
            <label style="display: block; font-weight: 600; color: var(--color-heading); margin-bottom: 6px; font-size: 0.9rem;">Hostname</label>
            <input type="text" class="hostname-input" placeholder="e.g. R1 or S1" required />
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
      
      <div class="command-list" id="cmds-${deviceId}">
        <!-- Selected Commands Spawn Here -->
      </div>
    `;

    const cmdList = block.querySelector(`#cmds-${deviceId}`);
    const dropdownRoot = block.querySelector(`#dropdown-${deviceId}`);
    const dropdownHeader = block.querySelector('.dropdown-header');
    const dropdownList = block.querySelector('.dropdown-list');
    const dropdownSearch = block.querySelector('.dropdown-search-input');

    function positionDropdown() {
      const rect = dropdownHeader.getBoundingClientRect();
      dropdownList.style.top = `${rect.bottom}px`;
      dropdownList.style.left = `${rect.left}px`;
      dropdownList.style.width = `${rect.width}px`;
    }
    
    window.addEventListener('scroll', () => {
      if (!dropdownList.classList.contains("hidden")) {
        positionDropdown();
      }
    });
    
    window.addEventListener('resize', () => {
      if (!dropdownList.classList.contains("hidden")) {
        positionDropdown();
      }
    });
    
    dropdownHeader.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = dropdownList.classList.contains("hidden");
      closeCommandDropdowns(willOpen ? dropdownRoot : null);
      dropdownList.classList.toggle("hidden", !willOpen);
      dropdownRoot?.classList.toggle("dropdown-open", willOpen);
      block.classList.toggle("dropdown-open", willOpen);
      if (willOpen) {
        positionDropdown();
        dropdownSearch?.focus();
      }
    });

    dropdownSearch?.addEventListener("click", (e) => {
      e.stopPropagation();
    });

    dropdownSearch?.addEventListener("input", () => {
      const term = normalizeCommandSearch(dropdownSearch.value);
      const items = block.querySelectorAll('.dropdown-item');
      items.forEach((item) => {
        const text = normalizeCommandSearch(item.textContent);
        item.classList.toggle("hidden", term && !text.includes(term));
      });
    });

    // Handle Checkbox changes
    const checkboxes = block.querySelectorAll('.dropdown-item input[type="checkbox"]');
    checkboxes.forEach(chk => {
      chk.addEventListener("change", (e) => {
         const cmdVal = e.target.value;
         if (e.target.checked) {
            // Add row
            cmdList.appendChild(createCommandRow(cmdVal));
         } else {
            // Remove row
            const existingRow = cmdList.querySelector(`.command-item[data-command="${cmdVal}"]`);
            if (existingRow) existingRow.remove();
         }
         updateDropdownCount(block);
      });
    });

    // Prevent dropdown list clicks from closing the dropdown
    dropdownList.addEventListener("click", (e) => {
      e.stopPropagation();
    });

    block.querySelector(".remove-device-btn").addEventListener("click", () => {
      block.remove();
    });

    container.appendChild(block);
    return block;
  }

  addDeviceBtn.addEventListener("click", () => addDeviceBlock());
  clearSetupBtn?.addEventListener("click", () => {
    if (!confirm("Clear all device setup data? This will remove saved template settings from this app.")) {
      return;
    }
    localStorage.removeItem("templateName");
    localStorage.removeItem("templateDevices");
    loadedFromServer = false;
    deviceCount = 0;
    container.innerHTML = "";
    const nameInput = document.getElementById("templateName");
    if (nameInput) nameInput.value = "";
    addDeviceBlock();
  });

  // Load from local storage
  function loadTemplateState() {
    const savedName = localStorage.getItem("templateName");
    const savedDevicesStr = localStorage.getItem("templateDevices");

    if (savedName) {
      document.getElementById("templateName").value = savedName;
    }

    if (savedDevicesStr) {
      try {
        const devicesMeta = JSON.parse(savedDevicesStr);
        const hostnames = Object.keys(devicesMeta);
        
        if (hostnames.length > 0) {
          hostnames.forEach(hostname => {
            const block = addDeviceBlock();
            block.querySelector(".hostname-input").value = hostname;
            
            const commands = devicesMeta[hostname];
            const checkboxes = block.querySelectorAll('.dropdown-item input[type="checkbox"]');
            
            // Re-check boxes and fire change event to trigger row creation
            checkboxes.forEach(chk => {
              if (commands.includes(chk.value)) {
                chk.checked = true;
                chk.dispatchEvent(new Event("change"));
              }
            });

            // Mark as previously uploaded
            const cmdRows = block.querySelectorAll(".command-item");
            cmdRows.forEach(row => {
               const fileInput = row.querySelector(".cmd-file");
               const badges = row.querySelector(".command-badges");
               fileInput.required = false; 
               
               const prevUploadedLabel = document.createElement("small");
               prevUploadedLabel.className = "command-badge command-badge--uploaded";
               prevUploadedLabel.textContent = "Previously Uploaded";
               if (badges) {
                 badges.appendChild(prevUploadedLabel);
               } else {
                 row.insertBefore(prevUploadedLabel, fileInput);
               }
            });
          });
          return;
        }
      } catch (err) {
        console.warn("Failed to parse saved devices:", err);
      }
    }
    
    // Only add an empty one if we didn't restore any
    addDeviceBlock();
  }

  loadTemplateState();

  // Global click listener to close dropdowns when clicking outside
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".custom-dropdown") && !e.target.closest(".dropdown-list")) {
      closeCommandDropdowns();
    }
  });

  async function loadTemplateFromServer(templateName) {
    if (!templateName) return;
    try {
      const res = await fetch(`http://127.0.0.1:5050/api/templates/${encodeURIComponent(templateName)}`);
      const data = await res.json();
      if (data.status !== "ok") {
        throw new Error(data.message || "Failed to load template.");
      }

      const devicesMeta = data.devices_meta || {};
      const logsByCommand = data.logs_by_command || {};
      localStorage.setItem("templateName", templateName);
      localStorage.setItem("templateDevices", JSON.stringify(devicesMeta));
      loadedFromServer = true;
      const nameInput = document.getElementById("templateName");
      if (nameInput) nameInput.value = templateName;

      // Reset UI
      container.innerHTML = "";
      deviceCount = 0;

      const hostnames = Object.keys(devicesMeta);
      if (hostnames.length === 0) {
        addDeviceBlock();
        return;
      }

      hostnames.forEach(hostname => {
        const block = addDeviceBlock();
        block.querySelector(".hostname-input").value = hostname;
        const commands = devicesMeta[hostname] || [];
        const cmdList = block.querySelector(".command-list");
        const checkboxes = block.querySelectorAll('.dropdown-item input[type="checkbox"]');
        commands.forEach(cmd => {
          const checkbox = Array.from(checkboxes).find(chk => chk.value === cmd);
          const existingFile = (logsByCommand[hostname] || {})[cmd] || null;
          if (checkbox) {
            checkbox.checked = true;
          }
          cmdList.appendChild(createCommandRow(cmd, existingFile));
        });
        updateDropdownCount(block);

        const cmdRows = block.querySelectorAll(".command-item");
        cmdRows.forEach(row => {
          const fileInput = row.querySelector(".cmd-file");
          const badges = row.querySelector(".command-badges");
          fileInput.required = false;
          const prevUploadedLabel = document.createElement("small");
          prevUploadedLabel.className = "command-badge command-badge--uploaded";
          prevUploadedLabel.textContent = "Previously Uploaded";
          badges?.appendChild(prevUploadedLabel);
        });
      });

      saveBtn.disabled = false;
      saveBtn.textContent = "Save Template & Continue";
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to load template.");
    }
  }

  loadTemplateBtn?.addEventListener("click", () => {
    const selected = templateSelect?.dataset.value || selectedTemplateName;
    if (!selected) {
      alert("Please select a template to load.");
      return;
    }
    loadTemplateFromServer(selected);
  });

  saveBtn.addEventListener("click", async () => {
    const templateName = document.getElementById("templateName").value.trim() || "default";
    
    const deviceBlocks = document.querySelectorAll(".device-block");
    if (deviceBlocks.length === 0) {
      alert("Please add at least one device.");
      return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = "Saving...";

    const formData = new FormData();
    formData.append("template_name", templateName);
    if (loadedFromServer && selectedTemplateName) {
      formData.append("source_template_name", selectedTemplateName);
    }

    let hasError = false;
    let deviceIndex = 0;

    // Collect data
    const devicesMeta = {}; // hostname -> array of commands

    deviceBlocks.forEach(block => {
      const hostname = block.querySelector(".hostname-input").value.trim();
      if (!hostname) {
        alert("All devices must have a hostname.");
        hasError = true;
        return;
      }
      
      const commands = [];
      const cmdRows = block.querySelectorAll(".command-item");
      cmdRows.forEach(row => {
        const cmdVal = row.querySelector(".cmd-input").value.trim();
        const fileInput = row.querySelector(".cmd-file");
        if (cmdVal) {
          commands.push(cmdVal);
          if (fileInput.files.length > 0) {
            // Field name format: file_HOSTNAME_COMMAND
            const fieldName = `file_${hostname}_${cmdVal}`;
            formData.append(fieldName, fileInput.files[0]);
          }
        }
      });

      // Optional Upload logic: if file exists, append it. If not, bypass because it's cached.
      if (commands.length === 0) {
        alert(`Device ${hostname} must have at least one selected command.`);
        hasError = true;
        return;
      }

      devicesMeta[hostname] = commands;
    });

    if (hasError) {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Template & Continue";
      return;
    }

    formData.append("devices_meta", JSON.stringify(devicesMeta));

    try {
      const res = await fetch("http://127.0.0.1:5050/api/templates/upload", {
        method: "POST",
        body: formData
      });
      const data = await res.json();
      
      if (!res.ok || data.status === "error") {
        throw new Error(data.message || "Failed to upload templates.");
      }

      localStorage.setItem("templateName", templateName);
      localStorage.setItem("templateDevices", JSON.stringify(devicesMeta));

      const anyFileSelected = Array.from(document.querySelectorAll(".cmd-file")).some(
        (input) => input.files && input.files.length > 0
      );
      if (anyFileSelected) {
        alert("Template baseline saved. You may proceed.");
      } else {
        alert("Device and command setup saved. No template baseline uploaded yet.");
      }
      goTo("index.html");

    } catch (err) {
      console.error(err);
      alert(err.message);
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Template & Continue";
    }
  });

  loadTemplateList();
});
