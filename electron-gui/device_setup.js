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

    templateSelect.innerHTML = `
      <option value="">Select a template</option>
      ${availableTemplates.map(name => `<option value="${name}">${name}</option>`).join("")}
    `;
  }

  function createCommandRow(commandText = "", existingFileName = null) {
    const row = document.createElement("div");
    row.className = "command-item";
    row.dataset.command = commandText;
    
    row.innerHTML = `
      <input type="text" class="cmd-input" value="${commandText}" readonly />
      <input type="file" class="cmd-file" />
      <button type="button" class="remove-cmd-btn danger-text">X</button>
    `;

    const fileInput = row.querySelector(".cmd-file");
    if (existingFileName) {
      const fileLabel = document.createElement("small");
      fileLabel.textContent = `(Loaded: ${existingFileName})`;
      fileLabel.style.color = "var(--color-text-muted)";
      fileLabel.style.marginLeft = "8px";
      row.insertBefore(fileLabel, fileInput);
      fileInput.required = false;
    }

    // Removing from row should also uncheck the dropdown box
    row.querySelector(".remove-cmd-btn").addEventListener("click", () => {
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
      dropdownListHtml = `<div class="dropdown-item" style="color: grey;">No commands in System Admin.</div>`;
    }

    block.innerHTML = `
      <div class="device-block-header">
        <label>
          <strong>Hostname:</strong> 
          <input type="text" class="hostname-input" placeholder="e.g. R1 or S1" required />
        </label>
        
        <div class="custom-dropdown" id="dropdown-${deviceId}">
          <div class="dropdown-header">
            <span>Select Commands</span>
            <small>▼</small>
          </div>
          <div class="dropdown-list hidden">
            ${dropdownListHtml}
          </div>
        </div>

        <button type="button" class="remove-device-btn">Remove Device</button>
      </div>
      <div class="command-list" id="cmds-${deviceId}">
        <!-- Selected Commands Spawn Here -->
      </div>
    `;

    const cmdList = block.querySelector(`#cmds-${deviceId}`);
    const dropdownHeader = block.querySelector('.dropdown-header');
    const dropdownList = block.querySelector('.dropdown-list');
    
    // Toggle dropdown
    dropdownHeader.addEventListener("click", () => {
      dropdownList.classList.toggle("hidden");
    });

    // Close dropdown on click outside
    document.addEventListener("click", (e) => {
      if (!block.querySelector(`#dropdown-${deviceId}`).contains(e.target)) {
         dropdownList.classList.add("hidden");
      }
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
               fileInput.required = false; 
               
               const prevUploadedLabel = document.createElement("small");
               prevUploadedLabel.textContent = "(Previously Uploaded)";
               prevUploadedLabel.style.color = "var(--color-success, #28a745)";
               prevUploadedLabel.style.marginLeft = "8px";
               
               row.insertBefore(prevUploadedLabel, fileInput);
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
          fileInput.required = false;
          const prevUploadedLabel = document.createElement("small");
          prevUploadedLabel.textContent = "(Previously Uploaded)";
          prevUploadedLabel.style.color = "var(--color-success, #28a745)";
          prevUploadedLabel.style.marginLeft = "8px";
          row.insertBefore(prevUploadedLabel, fileInput);
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
    const selected = templateSelect?.value;
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

    // If user loaded an existing template and didn't upload anything new,
    // still save the edited device list to localStorage and continue.
    if (loadedFromServer) {
      const anyFileSelected = Array.from(document.querySelectorAll(".cmd-file")).some(
        (input) => input.files && input.files.length > 0
      );
      if (!anyFileSelected) {
        localStorage.setItem("templateName", templateName);
        localStorage.setItem("templateDevices", JSON.stringify(devicesMeta));
        alert("Template loaded. No new files uploaded, continuing.");
        goTo("index.html");
        saveBtn.disabled = false;
        saveBtn.textContent = "Save Template & Continue";
        return;
      }
    }

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
      
      alert("Template configuration saved! You may proceed.");
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
