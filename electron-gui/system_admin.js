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
  const savePolicyBtn = document.getElementById("savePolicyBtn");
  const refreshRubricBtn = document.getElementById("refreshRubricBtn");
  const saveRubricBtn = document.getElementById("saveRubricBtn");
  const rubricRulesContainer = document.getElementById("rubricRulesContainer");
  const rubricFilterButtons = document.getElementById("rubricFilterButtons");

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
  let rubricRules = [];
  let selectedRubricCategory = "all";

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
  async function loadPolicy() {
    try {
      const data = await fetchJson("http://127.0.0.1:5050/api/grading_policy");
      const policy = data.policy || {};
      majorThresholdInput.value = policy.major_threshold || 1;
      minorThresholdInput.value = policy.minor_threshold || 5;
    } catch (err) {
      console.error(err);
      alert("Failed to load grading policy.");
    }
  }

  function renderRubricRules() {
    if (!rubricRulesContainer) return;
    let filtered = rubricRules;
    if (selectedRubricCategory !== "all") {
      filtered = rubricRules.filter(
        (rule) => (rule.section || rule.category || "").toLowerCase() === selectedRubricCategory.toLowerCase()
      );
    }

    if (!filtered.length) {
      rubricRulesContainer.innerHTML = `<p class="hint">No rubric rules found.</p>`;
      return;
    }

    // Group by section
    const groups = {};
    filtered.forEach((rule) => {
      const section = rule.section || rule.category || "Other";
      if (!groups[section]) groups[section] = [];
      groups[section].push(rule);
    });

    // Sort sections by their numeric prefix (e.g. "3.2.1", "3.2.10")
    const sortedSections = Object.keys(groups).sort((a, b) => {
      const numA = a.match(/[\d.]+/)?.[0] || "";
      const numB = b.match(/[\d.]+/)?.[0] || "";
      const partsA = numA.split(".").map(Number);
      const partsB = numB.split(".").map(Number);
      for (let i = 0; i < Math.max(partsA.length, partsB.length); i++) {
        const va = partsA[i] || 0;
        const vb = partsB[i] || 0;
        if (va !== vb) return va - vb;
      }
      return a.localeCompare(b);
    });

    rubricRulesContainer.innerHTML = "";

    sortedSections.forEach((section) => {
      const sectionRules = groups[section];

      // Section header
      const header = document.createElement("div");
      header.className = "rubric-section-header";
      header.innerHTML = `
        <div class="rubric-section-title">
          <span class="rubric-section-toggle">▼</span>
          ${section}
          <span class="rubric-section-count">${sectionRules.length} rules</span>
        </div>
      `;
      rubricRulesContainer.appendChild(header);

      // Section body (collapsible)
      const body = document.createElement("div");
      body.className = "rubric-section-body";

      sectionRules.forEach((rule) => {
        const index = rubricRules.indexOf(rule);
        const card = document.createElement("div");
        card.className = "rubric-rule-card";
        card.dataset.index = String(index);

        card.innerHTML = `
          <div class="rubric-rule-header">
            <div>
              <div class="rubric-rule-title">${rule.id || ""}</div>
              <div class="rubric-rule-sub">${rule.description || ""}</div>
            </div>
            <label class="choice-label">
              <input type="checkbox" class="rubric-enabled" ${rule.enabled ? "checked" : ""} />
              Enabled
            </label>
            <select class="rubric-severity">
              <option value="minor" ${rule.severity === "minor" ? "selected" : ""}>Minor</option>
              <option value="major" ${rule.severity === "major" ? "selected" : ""}>Major</option>
            </select>
          </div>
          <div class="rubric-rule-body">
            <textarea class="rubric-patterns" placeholder="One regex per line">${(rule.patterns || []).join("\n")}</textarea>
            <input class="rubric-statuses" type="text" placeholder="Statuses (optional): missing, extra, mismatch" value="${Array.isArray(rule.statuses) ? rule.statuses.join(", ") : (rule.statuses || "")}">
          </div>
        `;

        body.appendChild(card);
      });

      rubricRulesContainer.appendChild(body);

      // Toggle collapse
      header.addEventListener("click", () => {
        const isCollapsed = body.classList.toggle("collapsed");
        header.querySelector(".rubric-section-toggle").textContent = isCollapsed ? "▶" : "▼";
      });
    });
  }

  function renderRubricFilters() {
    if (!rubricFilterButtons) return;

    // Use sections for filter buttons
    const sections = Array.from(
      new Set(
        rubricRules
          .map((rule) => rule.section || rule.category || "")
          .filter(Boolean)
      )
    ).sort((a, b) => {
      const numA = a.match(/[\d.]+/)?.[0] || "";
      const numB = b.match(/[\d.]+/)?.[0] || "";
      const partsA = numA.split(".").map(Number);
      const partsB = numB.split(".").map(Number);
      for (let i = 0; i < Math.max(partsA.length, partsB.length); i++) {
        const va = partsA[i] || 0;
        const vb = partsB[i] || 0;
        if (va !== vb) return va - vb;
      }
      return a.localeCompare(b);
    });

    rubricFilterButtons.innerHTML = "";

    const allBtn = document.createElement("button");
    allBtn.type = "button";
    allBtn.className = "rubric-filter-btn";
    allBtn.dataset.category = "all";
    allBtn.textContent = "All";
    rubricFilterButtons.appendChild(allBtn);

    sections.forEach((sec) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rubric-filter-btn";
      btn.dataset.category = sec;
      // Display just the descriptive part (after the number)
      const label = sec.replace(/^\d+\.\d+\.\d+\s*/, "");
      btn.textContent = label || sec;
      btn.title = sec;
      rubricFilterButtons.appendChild(btn);
    });

    const updateActive = () => {
      rubricFilterButtons.querySelectorAll(".rubric-filter-btn").forEach((btn) => {
        btn.classList.toggle(
          "active",
          btn.dataset.category === selectedRubricCategory
        );
      });
    };

    rubricFilterButtons.querySelectorAll(".rubric-filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedRubricCategory = btn.dataset.category || "all";
        updateActive();
        renderRubricRules();
      });
    });

    updateActive();
  }

  async function loadRubricRules() {
    try {
      const data = await fetchJson("http://127.0.0.1:5050/api/rubric_rules");
      rubricRules = data.rules || [];
      renderRubricFilters();
      renderRubricRules();
    } catch (err) {
      console.error(err);
      alert("Failed to load rubric rules.");
    }
  }

  async function saveRubricRules() {
    if (!rubricRulesContainer) return;
    // Collect all cards from all section bodies
    const cards = rubricRulesContainer.querySelectorAll(".rubric-rule-card");
    const updatedByIndex = {};
    cards.forEach((card) => {
      const index = parseInt(card.dataset.index, 10);
      const base = rubricRules[index] || {};
      const enabled = card.querySelector(".rubric-enabled")?.checked || false;
      const severity = card.querySelector(".rubric-severity")?.value || "minor";
      const patterns = (card.querySelector(".rubric-patterns")?.value || "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      const statusesRaw = card.querySelector(".rubric-statuses")?.value || "";
      const statuses = statusesRaw
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean);
      updatedByIndex[index] = {
        ...base,
        enabled,
        severity,
        patterns,
        statuses,
      };
    });

    // Build full list: for items not visible (filtered out), keep original
    const updated = rubricRules.map((rule, i) =>
      updatedByIndex[i] !== undefined ? updatedByIndex[i] : rule
    );

    try {
      await fetchJson("http://127.0.0.1:5050/api/rubric_rules", {
        method: "POST",
        body: JSON.stringify({ rules: updated }),
      });
      rubricRules = updated;
      renderRubricFilters();
      renderRubricRules();
      alert("Rubric rules saved.");
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to save rubric rules.");
    }
  }

  async function savePolicy() {
    try {
      const payload = {
        major_threshold: parseInt(majorThresholdInput.value, 10),
        minor_threshold: parseInt(minorThresholdInput.value, 10),
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
  refreshRubricBtn?.addEventListener("click", loadRubricRules);
  saveRubricBtn?.addEventListener("click", saveRubricRules);
  deleteTemplateBtn?.addEventListener("click", deleteTemplate);
  deleteAllTemplatesBtn?.addEventListener("click", deleteAllTemplates);
  deleteResultBtn?.addEventListener("click", deleteResult);
  deleteAllResultsBtn?.addEventListener("click", deleteAllResults);
  deleteSessionBtn?.addEventListener("click", deleteSession);
  deleteStudentBtn?.addEventListener("click", deleteStudent);

  // Init
  fetchCommands();
  loadPolicy();
  loadRubricRules();
  loadCleanupLists();
});
