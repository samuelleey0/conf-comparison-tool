
// grading.js
const GRADING_API_ROOT = "http://127.0.0.1:5050/api";

// -----------------------------------------------------
// Utility: Fetch Wrapper
// -----------------------------------------------------
async function apiCall(endpoint, method = "GET", body = null) {
    const options = {
        method: method,
        headers: { "Content-Type": "application/json" }
    };
    if (body) options.body = JSON.stringify(body);

    const res = await fetch(`${GRADING_API_ROOT}${endpoint}`, options);
    const data = await res.json();
    if (data.status === "error") throw new Error(data.message);
    return data;
}

// -----------------------------------------------------
// Tab Switching
// -----------------------------------------------------
function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    document.querySelector(`button[onclick="switchTab('${tabName}')"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).classList.add('active');

    if (tabName === 'schemes') loadSchemes();
    if (tabName === 'rubrics') loadRubrics();
    if (tabName === 'grading') loadGradingOptions();
}

// -----------------------------------------------------
// Schemes Management
// -----------------------------------------------------
async function loadSchemes() {
    try {
        const data = await apiCall('/schemes');
        const list = document.getElementById('schemesList');
        list.innerHTML = '';
        data.schemes.forEach(s => {
            const li = document.createElement('li');
            li.className = 'list-item clickable';
            li.innerHTML = `<span>${s.name}</span> <small style="color:var(--color-text-muted)">(${Object.keys(s.variables || {}).length} vars)</small>`;
            li.onclick = () => loadSchemeDetails(s.id);
            list.appendChild(li);
        });
    } catch (e) {
        console.error("Failed to load schemes", e);
    }
}

async function loadSchemeDetails(id) {
    try {
        const data = await apiCall(`/schemes/${id}`);
        const scheme = data.scheme;

        document.getElementById('schemeEditor').style.display = 'block';
        document.getElementById('schemeEditorTitle').textContent = 'Edit Scheme';
        document.getElementById('schemeId').value = scheme.id;
        document.getElementById('schemeName').value = scheme.name;
        document.getElementById('schemeVars').value = JSON.stringify(scheme.variables, null, 2);
    } catch (e) {
        alert(e.message);
    }
}

function showCreateScheme() {
    document.getElementById('schemeEditor').style.display = 'block';
    document.getElementById('schemeEditorTitle').textContent = 'Create New Scheme';
    document.getElementById('schemeId').value = '';
    document.getElementById('schemeName').value = '';
    document.getElementById('schemeVars').value = '{\n  "hostname": "Router1"\n}';
}

document.getElementById('schemeForm').onsubmit = async (e) => {
    e.preventDefault();
    const id = document.getElementById('schemeId').value;
    const name = document.getElementById('schemeName').value;
    let variables = {};

    try {
        variables = JSON.parse(document.getElementById('schemeVars').value);
    } catch (err) {
        alert("Invalid JSON in Variables field");
        return;
    }

    try {
        if (id) {
            await apiCall(`/schemes/${id}`, 'PUT', { name, variables });
        } else {
            await apiCall('/schemes', 'POST', { name, variables });
        }
        loadSchemes();
        showStatusModal("Scheme saved!");
        setTimeout(hideStatusModal, 1000);
    } catch (e) {
        alert(e.message);
    }
};

async function deleteScheme() {
    const id = document.getElementById('schemeId').value;
    if (!id) return;
    if (!confirm("Delete this scheme?")) return;

    try {
        await apiCall(`/schemes/${id}`, 'DELETE');
        loadSchemes();
        document.getElementById('schemeEditor').style.display = 'none';
    } catch (e) {
        alert(e.message);
    }
}

// -----------------------------------------------------
// Rubrics Management
// -----------------------------------------------------
let currentRubricCriteria = [];

async function loadRubrics() {
    try {
        const data = await apiCall('/rubrics');
        const list = document.getElementById('rubricsList');
        list.innerHTML = '';
        data.rubrics.forEach(r => {
            const li = document.createElement('li');
            li.className = 'list-item clickable';
            li.innerHTML = `<span>${r.name}</span>`;
            li.onclick = () => loadRubricDetails(r.id);
            list.appendChild(li);
        });
    } catch (e) {
        console.error(e);
    }
}

async function loadRubricDetails(id) {
    try {
        const data = await apiCall(`/rubrics/${id}`);
        const rubric = data.rubric;

        document.getElementById('rubricEditor').style.display = 'block';
        document.getElementById('rubricEditorTitle').textContent = 'Edit Rubric';
        document.getElementById('rubricId').value = rubric.id;
        document.getElementById('rubricName').value = rubric.name;
        document.getElementById('rubricDesc').value = rubric.description || "";

        currentRubricCriteria = rubric.criteria || [];
        renderCriteriaList();
    } catch (e) {
        alert(e.message);
    }
}

function showCreateRubric() {
    document.getElementById('rubricEditor').style.display = 'block';
    document.getElementById('rubricEditorTitle').textContent = 'Create New Rubric';
    document.getElementById('rubricId').value = '';
    document.getElementById('rubricName').value = '';
    document.getElementById('rubricDesc').value = '';
    currentRubricCriteria = [];
    renderCriteriaList();
}

function renderCriteriaList() {
    const container = document.getElementById('rubricCriteriaList');
    container.innerHTML = '';

    currentRubricCriteria.forEach((crit, idx) => {
        const div = document.createElement('div');
        div.className = 'criteria-item';
        div.innerHTML = `
            <div style="display:flex; justify-content:space-between;">
                <strong>${crit.name}</strong>
                <button type="button" class="icon-btn danger" onclick="removeCriterion(${idx})">&times;</button>
            </div>
            <div style="font-family:monospace; font-size:0.85em; color:var(--color-text-muted); margin-top:4px;">
                Pattern: ${crit.pattern} <br/>
                Points: ${crit.points}
            </div>
        `;
        div.onclick = (e) => {
            if (e.target.tagName !== 'BUTTON') editCriterion(idx);
        }
        container.appendChild(div);
    });
}

function removeCriterion(idx) {
    currentRubricCriteria.splice(idx, 1);
    renderCriteriaList();
}

function editCriterion(idx) {
    const crit = currentRubricCriteria[idx];
    // Simple prompt for now, could be a modal
    const name = prompt("Criterion Name:", crit.name);
    if (name === null) return;
    const pattern = prompt("Regex Pattern:", crit.pattern);
    if (pattern === null) return;
    const points = prompt("Points:", crit.points);
    if (points === null) return;

    currentRubricCriteria[idx] = { ...crit, name, pattern, points: parseInt(points) };
    renderCriteriaList();
}

function addCriterionUI() {
    const name = prompt("Criterion Name:");
    if (!name) return;
    const pattern = prompt("Regex Pattern (use {{var}} for schemes):");
    const points = prompt("Points:", "10");

    currentRubricCriteria.push({
        name,
        pattern: pattern || "",
        points: parseInt(points) || 0
    });
    renderCriteriaList();
}


document.getElementById('rubricForm').onsubmit = async (e) => {
    e.preventDefault();
    const id = document.getElementById('rubricId').value;
    const name = document.getElementById('rubricName').value;
    const description = document.getElementById('rubricDesc').value;

    const payload = { name, description, criteria: currentRubricCriteria };

    try {
        if (id) {
            await apiCall(`/rubrics/${id}`, 'PUT', payload);
        } else {
            await apiCall('/rubrics', 'POST', payload);
        }
        loadRubrics();
        showStatusModal("Rubric saved!");
        setTimeout(hideStatusModal, 1000);
    } catch (e) {
        alert(e.message);
    }
};

async function deleteRubric() {
    const id = document.getElementById('rubricId').value;
    if (!id) return;
    if (!confirm("Delete this rubric?")) return;

    try {
        await apiCall(`/rubrics/${id}`, 'DELETE');
        loadRubrics();
        document.getElementById('rubricEditor').style.display = 'none';
    } catch (e) {
        alert(e.message);
    }
}

// -----------------------------------------------------
// Grading Execution flows
// -----------------------------------------------------
async function loadGradingOptions() {
    const schemesData = await apiCall('/schemes');
    const rubricsData = await apiCall('/rubrics');

    const schemeSel = document.getElementById('gradingSchemeSelect');
    const rubricSel = document.getElementById('gradingRubricSelect');

    schemeSel.innerHTML = '<option value="">-- Select Scheme --</option>';
    schemesData.schemes.forEach(s => {
        schemeSel.innerHTML += `<option value="${s.id}">${s.name}</option>`;
    });

    rubricSel.innerHTML = '<option value="">-- Select Rubric --</option>';
    rubricsData.rubrics.forEach(r => {
        rubricSel.innerHTML += `<option value="${r.id}">${r.name}</option>`;
    });
}

async function pickConfigFile() {
    let path = null;

    // Check if we are in Electron with nodeIntegration
    if (typeof require !== 'undefined') {
        try {
            const { ipcRenderer } = require('electron');
            path = await ipcRenderer.invoke('select-file');
        } catch (e) {
            console.warn("IPC invoke failed, falling back to prompt", e);
        }
    }

    if (!path) {
        path = prompt("Enter full path to config file:");
    }

    if (path) {
        document.getElementById('gradingConfigPath').value = path;
    }
}

async function runGrading() {
    const configPath = document.getElementById('gradingConfigPath').value;
    const schemeId = document.getElementById('gradingSchemeSelect').value;
    const rubricId = document.getElementById('gradingRubricSelect').value;

    if (!configPath || !schemeId || !rubricId) {
        alert("Please select all fields.");
        return;
    }

    showStatusModal("Grading...", "pending");
    try {
        const data = await apiCall('/grade', 'POST', {
            config_path: configPath,
            scheme_id: schemeId,
            rubric_id: rubricId
        });

        hideStatusModal();
        displayResults(data);
    } catch (e) {
        hideStatusModal();
        alert("Grading failed: " + e.message);
    }
}

function displayResults(data) {
    document.getElementById('gradingResults').style.display = 'block';
    const summary = document.getElementById('gradingSummary');
    const details = document.getElementById('gradingDetails');

    const { total_earned, total_possible, percentage } = data.summary;
    summary.innerHTML = `Total Score: ${total_earned} / ${total_possible} (${percentage.toFixed(1)}%)`;

    details.innerHTML = '';
    data.results.forEach(r => {
        const div = document.createElement('div');
        div.className = 'grading-result-item';
        div.innerHTML = `
            <span>${r.criterion}</span>
            <div style="text-align:right">
                <span class="status-badge status-${r.status}">${r.status}</span>
                <span style="margin-left:8px; font-weight:bold;">${r.points} pts</span>
            </div>
        `;
        details.appendChild(div);
    });
}

// Initial Load
document.addEventListener('DOMContentLoaded', () => {
    loadNavbar();
    switchTab('schemes');
});
