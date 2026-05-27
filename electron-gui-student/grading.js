const API_ROOT = "http://127.0.0.1:5050";
const { ipcRenderer } = require("electron");
const SIDEBAR_COLLAPSE_KEY = "sidebarCollapsed";

// Older grading admin page for schemes/rubrics. The active pass/fail policy and
// outcome-rule editor now live in System Admin, but these CRUD screens are kept
// for compatibility with existing saved YAML files.

// Navigate
function goTo(page) {
    window.location.href = page;
}

// Load Navbar
function loadNavbar() {
    const container = document.getElementById("navbarContainer");
    if (!container) return;
    fetch("navbar.html")
        .then((res) => res.text())
        .then((html) => {
            container.outerHTML = html;
            initNavbarInteractions();
            highlightActiveNavLink();
        });
}

function highlightActiveNavLink() {
    const links = document.querySelectorAll(".app-navbar__links a");
    const current = "grading.html";
    links.forEach((link) => {
        if (link.getAttribute("href") === current) {
            link.classList.add("active");
        }
    });
}

// --- State ---
let schemes = [];
let rubrics = [];

// --- Initialization ---
document.addEventListener("DOMContentLoaded", () => {
    restoreSidebarPreference();
    loadNavbar();
    setupTabs();
    loadSchemes();
    loadRubrics();
    setupEventHandlers();
});

function setupTabs() {
    const tabs = {
        navSchemes: "viewSchemes",
        navRubrics: "viewRubrics",
        navGrading: "viewGrading"
    };

    Object.keys(tabs).forEach(id => {
        document.getElementById(id).addEventListener("click", () => {
            // Update buttons
            Object.keys(tabs).forEach(tid => document.getElementById(tid).classList.remove("active"));
            document.getElementById(id).classList.add("active");

            // Update sections
            Object.values(tabs).forEach(sid => document.getElementById(sid).style.display = "none");
            document.getElementById(tabs[id]).style.display = "block";

            // Refresh data if needed
            if (id === "navGrading") updateGradingDropdowns();
        });
    });

    // Default visibility is set in CSS/HTML (active class) but explicit setting helps
    document.getElementById("viewSchemes").style.display = "block";
    document.getElementById("viewRubrics").style.display = "none";
    document.getElementById("viewGrading").style.display = "none";
}

// --- Data Fetching ---

async function fetchJson(endpoint, options = {}) {
    try {
        const res = await fetch(`${API_ROOT}${endpoint}`, {
            headers: { "Content-Type": "application/json" },
            ...options
        });
        const data = await res.json();
        if (data.status === "error") throw new Error(data.message);
        return data;
    } catch (err) {
        alert("Error: " + err.message);
        throw err;
    }
}

async function loadSchemes() {
    try {
        const data = await fetchJson("/api/schemes");
        schemes = data.schemes || [];
        renderSchemesList();
    } catch (e) { console.error(e); }
}

async function loadRubrics() {
    try {
        const data = await fetchJson("/api/rubrics");
        rubrics = data.rubrics || [];
        renderRubricsList();
    } catch (e) { console.error(e); }
}

// --- Schemes UI ---

function renderSchemesList() {
    const list = document.getElementById("schemesList");
    list.innerHTML = "";
    schemes.forEach(s => {
        const el = document.createElement("div");
        el.className = "list-item";
        el.innerHTML = `<span>${s.name || s.id}</span> <button class="delete-btn" data-id="${s.id}">🗑️</button>`;
        el.querySelector("span").onclick = () => editScheme(s);
        el.querySelector(".delete-btn").onclick = (e) => {
            e.stopPropagation();
            deleteScheme(s.id);
        };
        list.appendChild(el);
    });
}

function editScheme(scheme) {
    document.getElementById("schemeEditor").classList.remove("hidden");
    document.getElementById("schemeId").value = scheme ? scheme.id : "";
    document.getElementById("schemeName").value = scheme ? scheme.name : "";
    document.getElementById("schemeVariables").value = scheme ? JSON.stringify(scheme.variables || {}, null, 2) : "{}";
}

async function saveScheme() {
    const id = document.getElementById("schemeId").value;
    const name = document.getElementById("schemeName").value;
    let variables = {};
    try {
        variables = JSON.parse(document.getElementById("schemeVariables").value);
    } catch (e) {
        alert("Invalid JSON for variables");
        return;
    }

    await fetchJson("/api/schemes", {
        method: "POST",
        body: JSON.stringify({ id, name, variables })
    });

    document.getElementById("schemeEditor").classList.add("hidden");
    loadSchemes();
}

async function deleteScheme(id) {
    if (!confirm("Delete this scheme?")) return;
    await fetchJson(`/api/schemes/${id}`, { method: "DELETE" });
    loadSchemes();
}

// --- Rubrics UI ---

function renderRubricsList() {
    const list = document.getElementById("rubricsList");
    list.innerHTML = "";
    rubrics.forEach(r => {
        const el = document.createElement("div");
        el.className = "list-item";
        el.innerHTML = `<span>${r.name || r.id}</span> <button class="delete-btn" data-id="${r.id}">🗑️</button>`;
        el.querySelector("span").onclick = () => editRubric(r);
        el.querySelector(".delete-btn").onclick = (e) => {
            e.stopPropagation();
            deleteRubric(r.id);
        };
        list.appendChild(el);
    });
}

function editRubric(rubric) {
    document.getElementById("rubricEditor").classList.remove("hidden");
    document.getElementById("rubricId").value = rubric ? rubric.id : "";
    document.getElementById("rubricName").value = rubric ? rubric.name : "";
    document.getElementById("rubricDesc").value = rubric ? rubric.description : "";

    const criteriaList = document.getElementById("rubricCriteriaList");
    criteriaList.innerHTML = "";
    (rubric ? (rubric.criteria || []) : []).forEach(c => addCriteriaRow(c));
}

const CRITERIA_TEMPLATES = {
    "hostname": {
        label: "Hostname",
        generate: (data) => `hostname\\s+${data.host}`,
        extract: (pattern) => {
            const m = pattern.match(/^hostname\\s\+([^ ]+)$/);
            return m ? { host: m[1] } : null;
        },
        render: (data = {}) => `
            <input type="text" placeholder="Expected Hostname (e.g., S1 or {{hostname}})" class="tpl-hostname" value="${(data.host || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
        `,
        getValues: (row) => ({ host: row.querySelector('.tpl-hostname').value })
    },
    "interface_ip": {
        label: "Interface IP Address",
        generate: (data) => {
            const ip = data.ip.startsWith("{{") ? data.ip : data.ip.replace(/\./g, "\\.");
            return `interface\\s+${data.intf}[\\s\\S]*?ip\\s+address\\s+${ip}`;
        },
        extract: (pattern) => {
            const m = pattern.match(/^interface\\s\+(.+?)\[\\s\\S\]\*\?ip\\s\+address\\s\+(.+)$/);
            if (m) {
                const ip = m[2].startsWith("{{") ? m[2] : m[2].replace(/\\\./g, ".");
                return { intf: m[1], ip: ip };
            }
            return null;
        },
        render: (data = {}) => `
            <input type="text" placeholder="Interface (e.g., GigabitEthernet0/0)" class="tpl-intf" value="${(data.intf || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
            <input type="text" placeholder="IP (e.g., 192.168.1.1 or {{ip}})" class="tpl-ip" value="${(data.ip || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
        `,
        getValues: (row) => ({
            intf: row.querySelector('.tpl-intf').value,
            ip: row.querySelector('.tpl-ip').value
        })
    },
    "interface_config": {
        label: "Interface Configuration",
        generate: (data) => `interface\\s+${data.intf}[\\s\\S]*?${data.config}`,
        extract: (pattern) => {
            // Avoid matching the IP check which also uses this structure
            if (pattern.includes("ip\\s+address")) return null;
            const m = pattern.match(/^interface\\s\+(.+?)\[\\s\\S\]\*\?(.+)$/);
            return m ? { intf: m[1], config: m[2] } : null;
        },
        render: (data = {}) => `
            <input type="text" placeholder="Interface (e.g., FastEthernet0/1)" class="tpl-intf-cfg" value="${(data.intf || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
            <input type="text" placeholder="Config (e.g., description WAN)" class="tpl-cfg" value="${(data.config || '').replace(/"/g, '&quot;')}" style="flex: 2; padding: 4px;">
        `,
        getValues: (row) => ({
            intf: row.querySelector('.tpl-intf-cfg').value,
            config: row.querySelector('.tpl-cfg').value
        })
    },
    "vlan_dynamic": {
        label: "Dynamic VLAN List",
        generate: (data) => `VLAN_LOOP:{{${data.varname}}}`,
        extract: (pattern) => {
            const m = pattern.match(/^VLAN_LOOP:\{\{(.+?)\}\}$/);
            return m ? { varname: m[1] } : null;
        },
        render: (data = {}) => `
            <input type="text" placeholder="Scheme Variable Name (e.g., vlans)" class="tpl-vlan-var" value="${(data.varname || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
            <span style="font-size: 0.8em; align-self: center; color: var(--color-text-muted);">Points divide evenly amongst VLANs</span>
        `,
        getValues: (row) => ({
            varname: row.querySelector('.tpl-vlan-var').value
        })
    },
    "banner_motd": {
        label: "Banner MOTD",
        generate: (data) => data.text ? `banner\\s+motd.*${data.text}` : `banner\\s+motd`,
        extract: (pattern) => {
            if (pattern === "banner\\s+motd") return { text: "" };
            const m = pattern.match(/^banner\\s\+motd\.\*(.+)$/);
            return m ? { text: m[1] } : null;
        },
        render: (data = {}) => `
            <input type="text" placeholder="Banner text to check (leave empty for any banner)" class="tpl-banner" value="${(data.text || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
        `,
        getValues: (row) => ({
            text: row.querySelector('.tpl-banner').value
        })
    },
    "text_contains": {
        label: "Text Contains",
        generate: (data) => data.text,
        extract: (pattern) => {
            // If pattern has no regex special chars, it's just plain text
            const isRegex = /[\^\\$\[\]\{\}\(\)\*\+\?\\|]/.test(pattern);
            if (!isRegex && !pattern.startsWith("VLAN_LOOP")) return { text: pattern };
            return null;
        },
        render: (data = {}) => `
            <input type="text" placeholder="Text to find (e.g., OSPF enabled)" class="tpl-contains" value="${(data.text || '').replace(/"/g, '&quot;')}" style="flex: 1; padding: 4px;">
        `,
        getValues: (row) => ({
            text: row.querySelector('.tpl-contains').value
        })
    },
    "custom": {
        label: "Custom (Regex)",
        generate: (data) => data.pattern,
        extract: (pattern) => ({ pattern }),
        render: (data = {}) => `
            <input type="text" placeholder="Pattern (Regex)" class="tpl-pattern" value="${(data.pattern || '').replace(/"/g, '&quot;')}" style="flex: 1; font-family: monospace; padding: 4px;">
        `,
        getValues: (row) => ({ pattern: row.querySelector('.tpl-pattern').value })
    }
};

function addCriteriaRow(data = {}) {
    const container = document.getElementById("rubricCriteriaList");
    const div = document.createElement("div");
    div.className = "criteria-item";
    div.style.flexDirection = "column";
    div.style.alignItems = "stretch";

    let type = "custom";
    let typeData = { pattern: data.pattern || "" };

    if (data.pattern) {
        for (const [key, tpl] of Object.entries(CRITERIA_TEMPLATES)) {
            if (key === "custom") continue;
            const extracted = tpl.extract(data.pattern);
            if (extracted) {
                type = key;
                typeData = extracted;
                break;
            }
        }
    }

    let optionsHtml = Object.entries(CRITERIA_TEMPLATES).map(([k, v]) =>
        `<option value="${k}" ${k === type ? 'selected' : ''}>${v.label}</option>`
    ).join('');

    div.innerHTML = `
        <div style="display: flex; gap: 10px; width: 100%; align-items: center; margin-bottom: 5px;">
            <input type="text" placeholder="Criteria Name" class="crit-name" value="${data.name || ''}" style="flex: 1; padding: 4px;">
            <select class="crit-type" style="width: 140px; padding: 4px;">
                ${optionsHtml}
            </select>
            <input type="number" placeholder="Pts" class="crit-points" value="${data.points || 0}" style="width: 60px; padding: 4px;">
            <button class="delete-btn" style="padding: 4px 8px;">x</button>
        </div>
        <div class="crit-dynamic-inputs" style="display: flex; gap: 10px; width: 100%;">
            ${CRITERIA_TEMPLATES[type].render(typeData)}
        </div>
    `;

    div.querySelector(".delete-btn").onclick = () => div.remove();

    const select = div.querySelector(".crit-type");
    const dynamicContainer = div.querySelector(".crit-dynamic-inputs");

    select.addEventListener("change", (e) => {
        const selectedType = e.target.value;
        dynamicContainer.innerHTML = CRITERIA_TEMPLATES[selectedType].render({});
    });

    container.appendChild(div);
}

async function saveRubric() {
    const id = document.getElementById("rubricId").value;
    const name = document.getElementById("rubricName").value;
    const description = document.getElementById("rubricDesc").value;

    const criteria = [];
    document.querySelectorAll(".criteria-item").forEach(row => {
        const type = row.querySelector(".crit-type").value;
        const pts = parseInt(row.querySelector(".crit-points").value) || 0;
        const critName = row.querySelector(".crit-name").value;

        let pattern = "";
        try {
            const values = CRITERIA_TEMPLATES[type].getValues(row);
            pattern = CRITERIA_TEMPLATES[type].generate(values);
        } catch (e) {
            console.error("Failed to generate pattern", e);
        }

        criteria.push({
            name: critName,
            pattern: pattern,
            points: pts
        });
    });

    await fetchJson("/api/rubrics", {
        method: "POST",
        body: JSON.stringify({ id, name, description, criteria })
    });

    document.getElementById("rubricEditor").classList.add("hidden");
    loadRubrics();
}

async function deleteRubric(id) {
    if (!confirm("Delete this rubric?")) return;
    await fetchJson(`/api/rubrics/${id}`, { method: "DELETE" });
    loadRubrics();
}

// --- Grading Execution ---

function updateGradingDropdowns() {
    const sSelect = document.getElementById("selectScheme");
    const rSelect = document.getElementById("selectRubric");

    sSelect.innerHTML = "";
    schemes.forEach(s => sSelect.add(new Option(s.name || s.id, s.id)));

    rSelect.innerHTML = "";
    rubrics.forEach(r => rSelect.add(new Option(r.name || r.id, r.id)));
}

async function runGrading() {
    const scheme_id = document.getElementById("selectScheme").value;
    const rubric_id = document.getElementById("selectRubric").value;
    const target_path = document.getElementById("targetInfo").value;

    if (!scheme_id || !rubric_id || !target_path) {
        alert("Please select Scheme, Rubric, and Target Directory.");
        return;
    }

    document.getElementById("btnStartGrading").disabled = true;
    document.getElementById("btnStartGrading").textContent = "Grading...";

    try {
        const res = await fetchJson("/api/grade", {
            method: "POST",
            body: JSON.stringify({ scheme_id, rubric_id, target_path })
        });

        displayResults(res);
    } catch (e) {
        // error handled in fetchJson
    } finally {
        document.getElementById("btnStartGrading").disabled = false;
        document.getElementById("btnStartGrading").textContent = "Grade Now";
    }
}

function displayResults(data) {
    const container = document.getElementById("gradingResults");
    container.classList.remove("hidden");

    document.getElementById("resultSummary").innerHTML = `
        File: ${data.file || "N/A"}<br/>
        Score: ${data.total_score} / ${data.max_score}
    `;

    const tbody = document.getElementById("resultDetails");
    tbody.innerHTML = "";

    (data.details || []).forEach(d => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td style="padding: 8px; border-bottom: 1px solid var(--color-border);">${d.name}</td>
            <td style="padding: 8px; border-bottom: 1px solid var(--color-border);"><code style="font-size: 0.9em;">${d.pattern}</code></td>
            <td style="padding: 8px; border-bottom: 1px solid var(--color-border); text-align: right;">${d.score} / ${d.points}</td>
            <td style="padding: 8px; border-bottom: 1px solid var(--color-border); text-align: center;" class="${d.score > 0 ? 'result-pass' : 'result-fail'}">
                ${d.score > 0 ? "PASS" : "FAIL"}
            </td>
        `;
        tbody.appendChild(tr);
    });
}

// --- Event Handlers ---

function setupEventHandlers() {
    // Schemes
    document.getElementById("btnNewScheme").onclick = () => editScheme(null);
    document.getElementById("btnSaveScheme").onclick = saveScheme;
    document.getElementById("btnCancelScheme").onclick = () => document.getElementById("schemeEditor").classList.add("hidden");

    // Rubrics
    document.getElementById("btnNewRubric").onclick = () => editRubric(null);
    document.getElementById("btnAddCriteria").onclick = () => addCriteriaRow();
    document.getElementById("btnSaveRubric").onclick = saveRubric;
    document.getElementById("btnCancelRubric").onclick = () => document.getElementById("rubricEditor").classList.add("hidden");

    // Grading
    document.getElementById("btnSelectTarget").onclick = async () => {
        try {
            const path = await ipcRenderer.invoke("select-directory");
            if (path) document.getElementById("targetInfo").value = path;
        } catch (e) {
            alert("Failed to select directory: " + e.message);
        }
    };
    document.getElementById("btnStartGrading").onclick = runGrading;
}

// --- Helper Functions (Sidebar) ---

function restoreSidebarPreference() {
    if (typeof document === "undefined") return;
    const applyState = () => {
        if (typeof localStorage === "undefined") return;
        const collapsed = localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === "1";
        document.body.classList.toggle("sidebar-collapsed", collapsed);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", applyState, { once: true });
    } else {
        applyState();
    }
}

function initNavbarInteractions() {
    const toggleBtn = document.getElementById("sidebarToggle");
    if (!toggleBtn || !document.body) return;

    const syncButtonState = () => {
        const collapsed = document.body.classList.contains("sidebar-collapsed");
        toggleBtn.classList.toggle("collapsed", collapsed);
        toggleBtn.setAttribute("aria-expanded", (!collapsed).toString());
    };

    toggleBtn.addEventListener("click", () => {
        const nextCollapsed = !document.body.classList.contains("sidebar-collapsed");
        document.body.classList.toggle("sidebar-collapsed", nextCollapsed);
        if (typeof localStorage !== "undefined") {
            localStorage.setItem(SIDEBAR_COLLAPSE_KEY, nextCollapsed ? "1" : "0");
        }
        syncButtonState();
    });

    syncButtonState();
}
