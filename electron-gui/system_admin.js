document.addEventListener("DOMContentLoaded", () => {
  loadNavbar();
  
  const commandsGrid = document.getElementById("commandsGrid");
  const loadingIndicator = document.getElementById("loadingIndicator");
  const newCommandInput = document.getElementById("newCommandInput");
  const addCommandBtn = document.getElementById("addCommandBtn");
  const countBadge = document.getElementById("commandCountBadge");
  const adminTabsRoot = document.getElementById("systemAdminTabs");
  const majorThresholdInput = document.getElementById("majorThreshold");
  const minorThresholdInput = document.getElementById("minorThreshold");
  const savePolicyBtn = document.getElementById("savePolicyBtn");
  const refreshRubricBtn = document.getElementById("refreshRubricBtn");
  const saveRubricBtn = document.getElementById("saveRubricBtn");
  const addRubricRuleBtn = document.getElementById("addRubricRuleBtn");
  const resetRubricBtn = document.getElementById("resetRubricBtn");
  const rubricRulesContainer = document.getElementById("rubricRulesContainer");
  const rubricFilterButtons = document.getElementById("rubricFilterButtons");
  const rubricSearchInput = document.getElementById("rubricSearchInput");
  const DEFAULT_RUBRIC_SECTION = "all";

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
  const examList = document.getElementById("examList");
  const deleteExamBtn = document.getElementById("deleteExamBtn");
  const syncMirrorBtn = document.getElementById("syncMirrorBtn");

  let globalCommands = [];
  let rubricRules = [];
  let selectedRubricCategory = DEFAULT_RUBRIC_SECTION;
  let rubricSearchTerm = "";
  let deletedIndices = new Set();

  function initAdminTabs() {
    if (!adminTabsRoot) return;
    const tabs = Array.from(adminTabsRoot.querySelectorAll(".admin-tab-btn"));
    const panels = tabs
      .map((tab) => document.getElementById(tab.dataset.target || ""))
      .filter(Boolean);

    const activateTab = (targetId) => {
      tabs.forEach((tab) => {
        const isActive = tab.dataset.target === targetId;
        tab.classList.toggle("active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      panels.forEach((panel) => {
        panel.classList.toggle("active", panel.id === targetId);
      });
    };

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.target || ""));
    });
  }

  function closeOpenSelects(except = null) {
    document.querySelectorAll(".app-select.open").forEach((node) => {
      if (node !== except) {
        node.classList.remove("open");
        node.querySelector(".app-select-menu")?.classList.add("hidden");
      }
    });
  }

  function initSingleSelect(root, { options = [], value = "", placeholder = "Select" } = {}) {
    if (!root) return;
    const normalizedOptions = options.map((option) =>
      typeof option === "string" ? { value: option, label: option } : option
    );
    const selected = normalizedOptions.find((option) => option.value === value) || null;
    root.classList.add("app-select");
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
      });
    });
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".app-select")) {
      closeOpenSelects();
    }
  });

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
      commandsGrid.style.display = "none";
      
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
      commandsGrid.style.display = "grid";
    }
  }

  function renderCommands() {
    commandsGrid.innerHTML = "";
    countBadge.textContent = `${globalCommands.length} commands`;
    
    if (globalCommands.length === 0) {
      commandsGrid.innerHTML = `
        <div class="commands-container-empty">
          <p>No commands configured. Add one below.</p>
        </div>
      `;
      return;
    }

    globalCommands.forEach((cmd) => {
      const card = document.createElement("div");
      card.className = "command-card";
      
      card.innerHTML = `
        <div class="command-card-content">
          <code>${cmd}</code>
        </div>
        <div class="command-card-actions">
          <button type="button" class="remove-btn" title="Delete Command">Remove</button>
        </div>
      `;
      
      const deleteBtn = card.querySelector("button");
      deleteBtn.addEventListener("click", () => deleteCommand(cmd));
      
      commandsGrid.appendChild(card);
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
  rubricSearchInput?.addEventListener("input", (e) => {
    rubricSearchTerm = (e.target.value || "").trim();
    renderRubricRules();
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
    if (rubricSearchTerm) {
      const needle = rubricSearchTerm.toLowerCase();
      filtered = filtered.filter((rule) => {
        const haystack = [
          rule.id,
          rule.code,
          rule.description,
          rule.section,
          rule.category,
          rule.subcategory,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        return haystack.includes(needle);
      });
    }

    if (!filtered.length) {
      rubricRulesContainer.innerHTML = `<p class="hint">No rubric rules found${rubricSearchTerm ? ` for "${rubricSearchTerm}"` : ""}.</p>`;
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
        if (deletedIndices.has(index)) return;
        const card = document.createElement("div");
        card.className = "rubric-rule-card";
        card.dataset.index = String(index);
        const isDefault = rule.is_default !== false;
        // Determine status filter: single-element array → that status; multi or null → "any"
        const statusArr = Array.isArray(rule.statuses) ? rule.statuses : [];
        const selectedStatus = statusArr.length === 1 ? statusArr[0] : "";

        card.innerHTML = `
          <div class="rubric-rule-header">
            <div class="rubric-rule-info">
              <input type="text" class="rubric-rule-id" value="${rule.id || ""}" ${isDefault ? "readonly title=\"Default rule — ID cannot be changed\"" : "title=\"Edit rule ID\""} />
              <input type="text" class="rubric-rule-description" value="${(rule.description || "").replace(/"/g, "&quot;")}" placeholder="Rule description" />
            </div>
            <label class="choice-label">
              <input type="checkbox" class="rubric-enabled" ${rule.enabled ? "checked" : ""} />
              Enabled
            </label>
            <select class="rubric-severity">
              <option value="minor" ${rule.severity === "minor" ? "selected" : ""}>Minor</option>
              <option value="major" ${rule.severity === "major" ? "selected" : ""}>Major</option>
            </select>
            <button type="button" class="rubric-delete-btn" title="${isDefault ? "Disable this rule" : "Delete this rule"}">✕</button>
          </div>
          <div class="rubric-rule-body">
            <textarea class="rubric-patterns" placeholder="One regex per line">${(rule.patterns || []).join("\n")}</textarea>
            <div class="rubric-statuses"></div>
          </div>
        `;

        // Wire up delete button
        card.querySelector(".rubric-delete-btn").addEventListener("click", (e) => {
          e.stopPropagation();
          if (isDefault) {
            // Default rules: just disable (uncheck enabled)
            card.querySelector(".rubric-enabled").checked = false;
            card.style.opacity = "0.45";
          } else {
            // Custom rules: mark for deletion and hide
            if (!confirm(`Delete custom rule "${rule.id}"?`)) return;
            deletedIndices.add(index);
            card.remove();
          }
        });

        if (!rule.enabled) card.style.opacity = "0.45";
        card.querySelector(".rubric-enabled").addEventListener("change", (e) => {
          card.style.opacity = e.target.checked ? "1" : "0.45";
        });
        initSingleSelect(card.querySelector(".rubric-statuses"), {
          value: selectedStatus,
          placeholder: "Any Status",
          options: [
            { value: "", label: "Any Status" },
            { value: "mismatch", label: "Mismatch" },
            { value: "missing", label: "Missing" },
            { value: "extra", label: "Extra" },
          ],
        });

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
      deletedIndices = new Set();
      const availableSections = new Set(
        rubricRules.map((rule) => rule.section || rule.category || "").filter(Boolean)
      );
      if (!availableSections.has(selectedRubricCategory)) {
        selectedRubricCategory = availableSections.has(DEFAULT_RUBRIC_SECTION)
          ? DEFAULT_RUBRIC_SECTION
          : "all";
      }
      renderRubricFilters();
      renderRubricRules();
    } catch (err) {
      console.error(err);
      alert("Failed to load rubric rules.");
    }
  }

  async function saveRubricRules() {
    if (!rubricRulesContainer) return;
    // Collect all visible cards
    const cards = rubricRulesContainer.querySelectorAll(".rubric-rule-card");
    const updatedByIndex = {};
    cards.forEach((card) => {
      const index = parseInt(card.dataset.index, 10);
      const base = rubricRules[index] || {};
      const ruleId = card.querySelector(".rubric-rule-id")?.value?.trim() || base.id;
      const description = card.querySelector(".rubric-rule-description")?.value?.trim() || base.description;
      const enabled = card.querySelector(".rubric-enabled")?.checked || false;
      const severity = card.querySelector(".rubric-severity")?.value || "minor";
      const patterns = (card.querySelector(".rubric-patterns")?.value || "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      const selectedStatus = (card.querySelector(".rubric-statuses")?.dataset.value || "").trim().toLowerCase();
      const statuses = selectedStatus ? [selectedStatus] : [];
      updatedByIndex[index] = {
        ...base,
        id: ruleId,
        code: base.is_default !== false ? base.code : ruleId,
        description,
        enabled,
        severity,
        patterns,
        statuses,
      };
    });

    // Build full list: skip deleted, for items not visible (filtered out) keep original
    const updated = rubricRules
      .map((rule, i) => {
        if (deletedIndices.has(i)) return null;
        return updatedByIndex[i] !== undefined ? updatedByIndex[i] : rule;
      })
      .filter(Boolean);

    try {
      await fetchJson("http://127.0.0.1:5050/api/rubric_rules", {
        method: "POST",
        body: JSON.stringify({ rules: updated }),
      });
      rubricRules = updated;
      deletedIndices = new Set();
      renderRubricFilters();
      renderRubricRules();
      alert("Rubric rules saved.");
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to save rubric rules.");
    }
  }

  function addRubricRule() {
    // Gather existing sections for the dropdown
    const sections = Array.from(
      new Set(
        rubricRules
          .map((r) => r.section || r.category || "")
          .filter(Boolean)
      )
    ).sort();

    // Build a modal overlay
    const overlay = document.createElement("div");
    overlay.className = "rubric-modal-overlay";
    overlay.innerHTML = `
      <div class="rubric-modal">
        <h3>Add New Rubric Rule</h3>
        <label for="newRuleSection">Select or type a section:</label>
        <select id="newRuleSectionSelect">
          ${sections.map((s) => `<option value="${s}">${s}</option>`).join("")}
          <option value="__custom__">— Custom section —</option>
        </select>
        <input type="text" id="newRuleSectionInput" placeholder="Enter custom section name" style="display:none;" />
        <div class="rubric-modal-actions">
          <button type="button" id="newRuleCancelBtn" class="secondary">Cancel</button>
          <button type="button" id="newRuleConfirmBtn" class="primary">Create Rule</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const selectEl = overlay.querySelector("#newRuleSectionSelect");
    const inputEl = overlay.querySelector("#newRuleSectionInput");
    selectEl.addEventListener("change", () => {
      inputEl.style.display = selectEl.value === "__custom__" ? "block" : "none";
    });

    overlay.querySelector("#newRuleCancelBtn").addEventListener("click", () => {
      overlay.remove();
    });
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });

    overlay.querySelector("#newRuleConfirmBtn").addEventListener("click", () => {
      let resolvedSection = selectEl.value === "__custom__"
        ? inputEl.value.trim()
        : selectEl.value;
      if (!resolvedSection) {
        resolvedSection = "Custom";
      }

      // Generate unique ID
      let counter = 1;
      while (rubricRules.some((r) => r.id === `CUSTOM_RULE_${counter}`)) counter++;
      const newId = `CUSTOM_RULE_${counter}`;

      const newRule = {
        id: newId,
        code: newId,
        category: "",
        subcategory: "",
        section: resolvedSection,
        description: "New custom rule",
        severity: "minor",
        enabled: true,
        statuses: [],
        patterns: [],
        is_default: false,
      };

      rubricRules.push(newRule);
      selectedRubricCategory = resolvedSection;
      renderRubricFilters();
      renderRubricRules();
      overlay.remove();
    });
  }

  async function resetRubricRules() {
    if (!confirm("Reset ALL rubric rules to defaults?\nThis will remove all customizations and custom rules.")) return;
    try {
      const data = await fetchJson("http://127.0.0.1:5050/api/rubric_rules/reset", {
        method: "POST",
      });
      rubricRules = data.rules || [];
      deletedIndices = new Set();
      const availableSections = new Set(
        rubricRules.map((r) => r.section || r.category || "").filter(Boolean)
      );
      if (!availableSections.has(selectedRubricCategory)) {
        selectedRubricCategory = availableSections.has(DEFAULT_RUBRIC_SECTION)
          ? DEFAULT_RUBRIC_SECTION
          : "all";
      }
      renderRubricFilters();
      renderRubricRules();
      alert("Rubric rules reset to defaults.");
    } catch (err) {
      console.error(err);
      alert(err.message || "Failed to reset rubric rules.");
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

      fillSelect(
        templateList,
        (templates.templates || []).map((name) => ({ value: name, label: name })),
        "No templates found"
      );

      fillSelect(
        resultList,
        (results.results || []).map((entry) => ({
          value: entry.path,
          label: formatCleanupDisplay(entry, "Result"),
        })),
        "No results found"
      );

      fillSelect(
        examList,
        (students.exams || []).map((entry) => ({
          value: entry.path,
          label: formatCleanupDisplay(entry, "Exam"),
        })),
        "No exams found"
      );

      fillSelect(
        sessionList,
        (students.sessions || []).map((entry) => ({
          value: entry.path,
          label: formatCleanupDisplay(entry, "Session"),
        })),
        "No sessions found"
      );

      fillSelect(
        studentList,
        (students.students || []).map((entry) => ({
          value: entry.path,
          label: formatCleanupDisplay(entry, "Student"),
        })),
        "No students found"
      );
    } catch (err) {
      console.error(err);
      alert("Failed to load cleanup lists.");
    }
  }

  function fillSelect(selectEl, options, emptyLabel) {
    if (!selectEl) return;
    selectEl.innerHTML = "";

    if (!options.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = emptyLabel;
      selectEl.appendChild(option);
      return;
    }

    options.forEach(({ value, label }) => {
      const option = document.createElement("option");
      option.value = value || "";
      option.textContent = label || value || "";
      selectEl.appendChild(option);
    });
  }

  function formatCleanupDisplay(entry, fallbackLabel) {
    if (!entry || typeof entry !== "object") return fallbackLabel;
    if (entry.display) return entry.display;

    const parts = [
      entry.classroom || entry.exam_name,
      entry.tutor_name || entry.session_id,
      entry.time_slot,
      entry.student_id,
    ].filter(Boolean);

    if (parts.length) return parts.join("/");
    return entry.path || fallbackLabel;
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

  async function deleteExam() {
    const path = examList.value;
    if (!path) return;
    if (!confirm(`Delete ENTIRE exam folder:\n${path}\nThis will remove ALL sessions and students inside. This cannot be undone.`)) return;
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

  async function syncMirror() {
    if (!confirm("This will remove any comparison_engine/students folders that no longer exist in Documents. Continue?")) return;
    try {
      const data = await fetchJson("http://127.0.0.1:5050/api/admin/sync_mirror", { method: "POST" });
      alert(data.message || "Mirror sync complete.");
    } catch (err) {
      console.error(err);
      alert("Failed to sync mirror: " + (err.message || err));
    }
  }

  savePolicyBtn?.addEventListener("click", savePolicy);
  refreshRubricBtn?.addEventListener("click", loadRubricRules);
  saveRubricBtn?.addEventListener("click", saveRubricRules);
  addRubricRuleBtn?.addEventListener("click", addRubricRule);
  resetRubricBtn?.addEventListener("click", resetRubricRules);
  deleteTemplateBtn?.addEventListener("click", deleteTemplate);
  deleteAllTemplatesBtn?.addEventListener("click", deleteAllTemplates);
  deleteResultBtn?.addEventListener("click", deleteResult);
  deleteAllResultsBtn?.addEventListener("click", deleteAllResults);
  deleteExamBtn?.addEventListener("click", deleteExam);
  deleteSessionBtn?.addEventListener("click", deleteSession);
  deleteStudentBtn?.addEventListener("click", deleteStudent);
  syncMirrorBtn?.addEventListener("click", syncMirror);

  // Init
  initAdminTabs();
  fetchCommands();
  loadPolicy();
  loadRubricRules();
  loadCleanupLists();
});
