document.addEventListener("DOMContentLoaded", () => {
  loadNavbar();
  
  const commandsTableBody = document.getElementById("commandsTableBody");
  const commentsTable = document.getElementById("commentsTable");
  const loadingIndicator = document.getElementById("loadingIndicator");
  const newCommandInput = document.getElementById("newCommandInput");
  const addCommandBtn = document.getElementById("addCommandBtn");
  const countBadge = document.getElementById("commandCountBadge");
  const majorThresholdInput = document.getElementById("majorThreshold");
  const minorThresholdInput = document.getElementById("minorThreshold");
  const majorPatternsInput = document.getElementById("majorPatterns");
  const savePolicyBtn = document.getElementById("savePolicyBtn");
  const majorPatternChecklist = document.getElementById("majorPatternChecklist");

  const templateList = document.getElementById("templateList");
  const resultList = document.getElementById("resultList");
  const sessionList = document.getElementById("sessionList");
  const studentList = document.getElementById("studentList");
  const deleteTemplateBtn = document.getElementById("deleteTemplateBtn");
  const deleteAllTemplatesBtn = document.getElementById("deleteAllTemplatesBtn");
  const deleteResultBtn = document.getElementById("deleteResultBtn");
  const deleteAllResultsBtn = document.getElementById("deleteAllResultsBtn");
  const deleteSessionBtn = document.getElementById("deleteSessionBtn");
  const deleteStudentBtn = document.getElementById("deleteStudentBtn");

  let globalCommands = [];
  const majorPatternPresets = [
    {
      label: "Hostname",
      pattern: "^show_running_config\\.hostname$",
      note: "Device name must match",
    },
    {
      label: "VTY Transport",
      pattern: "^show_running_config\\.vty\\.transport$",
      note: "SSH vs telnet",
    },
    {
      label: "Interface IP/Mask (any)",
      pattern: "^show_running_config\\.interfaces\\.[^.]+\\.(ip|mask)$",
      note: "Layer3 interface IPs",
    },
    {
      label: "SVI IP/Mask (Vlan)",
      pattern: "^show_running_config\\.interfaces\\.Vlan\\d+\\.(ip|mask)$",
      note: "Switch management SVI",
    },
    {
      label: "Enable Secret",
      pattern: "^show_running_config\\.enable_secret$",
      note: "Privilege access password",
    },
    {
      label: "Default Gateway",
      pattern: "^show_running_config\\.switching\\.default_gateway$",
      note: "Layer2 default gateway",
    },
  ];

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await res.json();
    if (!res.ok || data.status === "error") {
      throw new Error(data.message || "Request failed.");
    }
    return data;
  }

  async function fetchCommands() {
    try {
      loadingIndicator.style.display = "block";
      commentsTable.style.display = "none";
      
      const res = await fetch("http://127.0.0.1:5050/api/commands");
      const data = await res.json();
      
      if (data.status === "ok") {
        globalCommands = data.commands || [];
        renderCommands();
      } else {
        alert("Failed to load commands: " + data.message);
      }
    } catch (err) {
      console.error(err);
      alert("Error fetching commands. Is the backend running?");
    } finally {
      loadingIndicator.style.display = "none";
      commentsTable.style.display = "table";
    }
  }

  function renderCommands() {
    commandsTableBody.innerHTML = "";
    countBadge.textContent = `${globalCommands.length} commands`;
    
    if (globalCommands.length === 0) {
      commandsTableBody.innerHTML = `
        <tr>
          <td colspan="2" style="text-align: center; color: var(--color-text-muted);">
            No commands configured. Add one below.
          </td>
        </tr>
      `;
      return;
    }

    globalCommands.forEach((cmd) => {
      const tr = document.createElement("tr");
      
      tr.innerHTML = `
        <td><code style="font-size: 1rem; background: var(--color-bg); padding: 4px 8px; border-radius: 4px;">${cmd}</code></td>
        <td class="command-actions">
          <button type="button" class="remove-btn" title="Delete Command">Remove</button>
        </td>
      `;
      
      const deleteBtn = tr.querySelector("button");
      deleteBtn.addEventListener("click", () => deleteCommand(cmd));
      
      commandsTableBody.appendChild(tr);
    });
  }

  function getMajorPatternLines() {
    return majorPatternsInput.value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  }

  function setMajorPatternLines(lines) {
    majorPatternsInput.value = Array.from(new Set(lines)).join("\n");
  }

  function syncChecklistFromTextarea() {
    if (!majorPatternChecklist) return;
    const lines = new Set(getMajorPatternLines());
    majorPatternChecklist
      .querySelectorAll("input[type='checkbox']")
      .forEach((input) => {
        const pattern = input.dataset.pattern;
        input.checked = lines.has(pattern);
      });
  }

  function renderMajorChecklist() {
    if (!majorPatternChecklist) return;
    majorPatternChecklist.innerHTML = "";
    majorPatternPresets.forEach((preset) => {
      const label = document.createElement("label");
      label.className = "major-check-item";
      label.innerHTML = `
        <input type="checkbox" data-pattern="${preset.pattern}" />
        <span class="major-check-text">${preset.label}</span>
        <span class="major-check-note">${preset.note}</span>
      `;
      const input = label.querySelector("input");
      input.addEventListener("change", () => {
        const lines = new Set(getMajorPatternLines());
        if (input.checked) {
          lines.add(preset.pattern);
        } else {
          lines.delete(preset.pattern);
        }
        setMajorPatternLines(Array.from(lines));
      });
      majorPatternChecklist.appendChild(label);
    });
  }

  async function addCommand() {
    const cmdVal = newCommandInput.value.trim();
    if (!cmdVal) return;
    
    if (globalCommands.includes(cmdVal)) {
      alert("This command already exists in the repository!");
      return;
    }

    addCommandBtn.disabled = true;
    addCommandBtn.textContent = "Adding...";

    try {
      const res = await fetch("http://127.0.0.1:5050/api/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmdVal })
      });
      
      const data = await res.json();
      if (data.status === "ok") {
        newCommandInput.value = "";
        await fetchCommands();
      } else {
        alert("Failed to add command: " + data.message);
      }
    } catch (err) {
      console.error(err);
      alert("Error saving command.");
    } finally {
      addCommandBtn.disabled = false;
      addCommandBtn.textContent = "+ Add Command";
    }
  }

  async function deleteCommand(cmd) {
    if (!confirm(`Are you sure you want to delete '${cmd}' from the system repository? This might break existing templates relying on it.`)) {
      return;
    }
    
    try {
      const res = await fetch("http://127.0.0.1:5050/api/commands", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd })
      });
      
      const data = await res.json();
      if (data.status === "ok") {
        await fetchCommands();
      } else {
        alert("Failed to delete command: " + data.message);
      }
    } catch (err) {
      console.error(err);
      alert("Error deleting command.");
    }
  }

  addCommandBtn.addEventListener("click", addCommand);
  newCommandInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") addCommand();
  });
  majorPatternsInput.addEventListener("input", syncChecklistFromTextarea);

  async function loadPolicy() {
    try {
      const data = await fetchJson("http://127.0.0.1:5050/api/grading_policy");
      const policy = data.policy || {};
      majorThresholdInput.value = policy.major_threshold || 1;
      minorThresholdInput.value = policy.minor_threshold || 5;
      majorPatternsInput.value = (policy.major_patterns || []).join("\n");
      syncChecklistFromTextarea();
    } catch (err) {
      console.error(err);
      alert("Failed to load grading policy.");
    }
  }

  async function savePolicy() {
    try {
      const major_patterns = majorPatternsInput.value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      const payload = {
        major_threshold: parseInt(majorThresholdInput.value, 10),
        minor_threshold: parseInt(minorThresholdInput.value, 10),
        major_patterns,
      };
      await fetchJson("http://127.0.0.1:5050/api/grading_policy", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      alert("Grading policy saved.");
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to save grading policy.");
    }
  }

  async function loadCleanupLists() {
    try {
      const [templates, results, students] = await Promise.all([
        fetchJson("http://127.0.0.1:5050/api/admin/templates"),
        fetchJson("http://127.0.0.1:5050/api/admin/results"),
        fetchJson("http://127.0.0.1:5050/api/admin/students"),
      ]);

      templateList.innerHTML = (templates.templates || [])
        .map((name) => `<option value="${name}">${name}</option>`)
        .join("");

      resultList.innerHTML = (results.results || [])
        .map((entry) => `<option value="${entry.path}">${entry.display}</option>`)
        .join("");

      sessionList.innerHTML = (students.sessions || [])
        .map(
          (s) =>
            `<option value="${s.path}">${s.exam_name}/${s.session_id}</option>`
        )
        .join("");

      studentList.innerHTML = (students.students || [])
        .map(
          (s) =>
            `<option value="${s.path}">${s.exam_name}/${s.session_id}/${s.student_id}</option>`
        )
        .join("");
    } catch (err) {
      console.error(err);
      alert("Failed to load cleanup lists.");
    }
  }

  async function deleteTemplate() {
    const name = templateList.value;
    if (!name) return;
    if (!confirm(`Delete template '${name}'?`)) return;
    await fetchJson("http://127.0.0.1:5050/api/admin/templates", {
      method: "DELETE",
      body: JSON.stringify({ name }),
    });
    loadCleanupLists();
  }

  async function deleteAllTemplates() {
    if (!confirm("Delete ALL templates? This cannot be undone.")) return;
    await fetchJson("http://127.0.0.1:5050/api/admin/templates", {
      method: "DELETE",
      body: JSON.stringify({ all: true }),
    });
    loadCleanupLists();
  }

  async function deleteResult() {
    const path = resultList.value;
    if (!path) return;
    if (!confirm(`Delete results at:\n${path}?`)) return;
    await fetchJson("http://127.0.0.1:5050/api/admin/results", {
      method: "DELETE",
      body: JSON.stringify({ path }),
    });
    loadCleanupLists();
  }

  async function deleteAllResults() {
    if (!confirm("Delete ALL results? This cannot be undone.")) return;
    await fetchJson("http://127.0.0.1:5050/api/admin/results", {
      method: "DELETE",
      body: JSON.stringify({ all: true }),
    });
    loadCleanupLists();
  }

  async function deleteStudent() {
    const path = studentList.value;
    if (!path) return;
    if (!confirm(`Delete student folder:\n${path}\nThis cannot be undone.`)) return;
    await fetchJson("http://127.0.0.1:5050/api/admin/students", {
      method: "DELETE",
      body: JSON.stringify({ path }),
    });
    loadCleanupLists();
  }

  async function deleteSession() {
    const path = sessionList.value;
    if (!path) return;
    if (!confirm(`Delete session folder:\n${path}\nThis will remove all students in the session.`)) return;
    await fetchJson("http://127.0.0.1:5050/api/admin/students", {
      method: "DELETE",
      body: JSON.stringify({ path }),
    });
    loadCleanupLists();
  }

  savePolicyBtn?.addEventListener("click", savePolicy);
  deleteTemplateBtn?.addEventListener("click", deleteTemplate);
  deleteAllTemplatesBtn?.addEventListener("click", deleteAllTemplates);
  deleteResultBtn?.addEventListener("click", deleteResult);
  deleteAllResultsBtn?.addEventListener("click", deleteAllResults);
  deleteSessionBtn?.addEventListener("click", deleteSession);
  deleteStudentBtn?.addEventListener("click", deleteStudent);

  // Init
  renderMajorChecklist();
  fetchCommands();
  loadPolicy();
  loadCleanupLists();
});
