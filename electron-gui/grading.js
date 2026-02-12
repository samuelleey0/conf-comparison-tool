const API_ROOT = "http://127.0.0.1:5050";
const { ipcRenderer } = require("electron");
const SIDEBAR_COLLAPSE_KEY = "sidebarCollapsed";

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

function addCriteriaRow(data = {}) {
    const container = document.getElementById("rubricCriteriaList");
    const div = document.createElement("div");
    div.className = "criteria-item";
    div.innerHTML = `
        <input type="text" placeholder="Name" class="crit-name" value="${data.name || ''}" style="flex: 1;">
        <input type="text" placeholder="Pattern (Regex)" class="crit-pattern" value="${(data.pattern || '').replace(/"/g, '&quot;')}" style="flex: 2; font-family: monospace;">
        <input type="number" placeholder="Pts" class="crit-points" value="${data.points || 0}" style="width: 60px;">
        <button class="delete-btn">x</button>
    `;
    div.querySelector(".delete-btn").onclick = () => div.remove();
    container.appendChild(div);
}

async function saveRubric() {
    const id = document.getElementById("rubricId").value;
    const name = document.getElementById("rubricName").value;
    const description = document.getElementById("rubricDesc").value;

    const criteria = [];
    document.querySelectorAll(".criteria-item").forEach(row => {
        criteria.push({
            name: row.querySelector(".crit-name").value,
            pattern: row.querySelector(".crit-pattern").value,
            points: parseInt(row.querySelector(".crit-points").value) || 0
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
