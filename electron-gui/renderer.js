// electron-gui/renderer.js

const API_ROOT = "http://127.0.0.1:5050";
const SIDEBAR_COLLAPSE_KEY = "sidebarCollapsed";
const SERIAL_PRESETS = {
  linux_usb: "/dev/ttyUSB0",
  linux_rs232: "/dev/ttyS0",
  windows: "COM3",
  mac: "/dev/cu.usbserial-10",
};

let currentAbortController = null;

let ipcRenderer = null;
let shell = null;
let pathModule = null;

try {
  const electron = require("electron");
  ipcRenderer = electron.ipcRenderer;
  shell = electron.shell;
  pathModule = require("path");
} catch (err) {
  console.debug("Electron modules not available:", err);
}

try {
  pathModule = require("path");
} catch (err) {
  pathModule = null;
}

if (typeof window !== "undefined") {
  window.ipcRenderer = ipcRenderer;
  window.pathModule = pathModule;
  window.API_ROOT = API_ROOT;
}

let XLSX = null;
try {
  XLSX = require("xlsx");
} catch (err) {
  console.debug("XLSX module not available:", err);
}

let selectedExistingPath = null;
let selectedExistingDisplay = null;
let statusModalOverlay = null;
let statusModalMessageEl = null;
let statusModalCloseBtn = null;
let statusModalHideTimeout = null;
let autoRunAfterConnect = false;

restoreSidebarPreference();

function nowTimestamp() {
  return new Date().toLocaleTimeString();
}

function appendLogLine(message) {
  const log = document.getElementById("log");
  if (!log) return;
  log.innerText += `${message}\n`;
  log.scrollTop = log.scrollHeight;
}

function setProgressValue(value) {
  const progressEl = document.getElementById("progress");
  const labelEl = document.getElementById("progressValue");
  const clamped = Math.min(100, Math.max(0, Math.round(value)));
  if (progressEl) progressEl.value = clamped;
  if (labelEl) labelEl.textContent = `${clamped}%`;
}

function goTo(page) {
  window.location.href = page;
}
window.goTo = goTo;

function loadNavbar() {
  const container = document.getElementById("navbarContainer");
  if (!container) return;
  fetch("navbar.html")
    .then((res) => res.text())
    .then((html) => {
      container.outerHTML = html;
      initNavbarInteractions();
      highlightActiveNavLink();
    })
    .catch((err) => console.error("Failed to load navbar:", err));
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

function highlightActiveNavLink() {
  const links = document.querySelectorAll(".app-navbar__links a");
  if (!links.length) return;
  let current = window.location.pathname.split("/").pop() || "index.html";
  current = current.toLowerCase();
  links.forEach((link) => {
    const target =
      (link.getAttribute("href") || "")
        .split("/")
        .pop()
        .toLowerCase() || "index.html";
    const isActive = target === current;
    link.classList.toggle("active", isActive);
  });
}

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

async function fetchJson(path, options = {}) {
  const res = await fetch(`${API_ROOT}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    // ignore parse errors; handled below.
  }
  if (!res.ok || data.status === "error") {
    const message =
      data.message || res.statusText || "Request failed. Please try again.";
    throw new Error(message);
  }
  return data;
}

function setDirectoryInfo({
  exam_name,
  session_id,
  student_id,
  path,
  mode,
  display,
}) {
  const prevSessionKey = localStorage.getItem("sessionKey");
  const nextSessionKey = `${exam_name || ""}::${session_id || ""}`;
  if (prevSessionKey !== nextSessionKey) {
    localStorage.setItem("sessionKey", nextSessionKey);
    localStorage.removeItem("completedStudents");
    localStorage.removeItem("sessionStudents");
    localStorage.removeItem("sessionStudentsCount");
  }
  localStorage.setItem("examName", exam_name);
  localStorage.setItem("sessionId", session_id);
  localStorage.setItem("studentId", student_id);
  if (path) localStorage.setItem("basePath", path);
  else localStorage.removeItem("basePath");
  localStorage.setItem("directoryMode", mode || "create");
  if (display) localStorage.setItem("directoryDisplay", display);
  else localStorage.removeItem("directoryDisplay");
}

function ensureDirectoryConfigured() {
  const exam = localStorage.getItem("examName");
  const session = localStorage.getItem("sessionId");
  const student = localStorage.getItem("studentId");
  if (!exam || !session || !student) {
    alert("Please set up a directory before continuing.");
    goTo("index.html");
    return false;
  }
  return true;
}

function getDirectoryInfo() {
  const exam_name = localStorage.getItem("examName");
  const session_id = localStorage.getItem("sessionId");
  const student_id = localStorage.getItem("studentId");
  const base_path = localStorage.getItem("basePath");
  const mode = localStorage.getItem("directoryMode");
  const display = localStorage.getItem("directoryDisplay");

  if (!exam_name || !session_id) return null;

  return {
    exam_name,
    session_id,
    student_id,
    base_path,
    mode,
    display
  };
}

function ensureStatusModalElements() {
  if (statusModalOverlay) return statusModalOverlay;

  statusModalOverlay = document.createElement("div");
  statusModalOverlay.id = "statusModalOverlay";
  statusModalOverlay.className = "status-modal-overlay";

  const box = document.createElement("div");
  box.className = "status-modal-box";

  const spinner = document.createElement("div");
  spinner.className = "status-modal-spinner";

  statusModalMessageEl = document.createElement("p");
  statusModalMessageEl.className = "status-modal-message";

  statusModalCloseBtn = document.createElement("button");
  statusModalCloseBtn.type = "button";
  statusModalCloseBtn.className = "status-modal-close secondary";
  statusModalCloseBtn.textContent = "Close";
  statusModalCloseBtn.addEventListener("click", () => hideStatusModal());

  box.append(spinner, statusModalMessageEl, statusModalCloseBtn);
  statusModalOverlay.appendChild(box);
  document.body.appendChild(statusModalOverlay);
  return statusModalOverlay;
}

function showStatusModal(message, state = "info") {
  const overlay = ensureStatusModalElements();
  if (statusModalHideTimeout) {
    clearTimeout(statusModalHideTimeout);
    statusModalHideTimeout = null;
  }
  overlay.dataset.state = state;
  statusModalMessageEl.textContent = message;
  statusModalCloseBtn.style.display = state === "pending" ? "none" : "inline-block";
  overlay.classList.add("visible");
  return overlay;
}

function updateStatusModal(overlay, message, state = "info", autoHide = false) {
  const modal = overlay || ensureStatusModalElements();
  modal.dataset.state = state;
  statusModalMessageEl.textContent = message;
  statusModalCloseBtn.style.display = state === "pending" ? "none" : "inline-block";
  if (autoHide) {
    statusModalHideTimeout = setTimeout(() => hideStatusModal(), 1500);
  }
}

function hideStatusModal() {
  if (statusModalHideTimeout) {
    clearTimeout(statusModalHideTimeout);
    statusModalHideTimeout = null;
  }
  if (statusModalOverlay) {
    statusModalOverlay.classList.remove("visible");
  }
}

// -----------------------------
// Directory setup page
// -----------------------------

function setupWelcomePage() {
  // Clear previous session setup when the app is freshly opened
  const keysToClear = [
     "templateName", 
     "templateDevices", 
     "hasRubrics",
     "directoryMode", 
     "basePath", 
     "directoryDisplay", 
     "examName", 
     "sessionId", 
     "selectedStudent",
     "sessionPath"
  ];
  keysToClear.forEach(k => localStorage.removeItem(k));

  loadNavbar();
  const startBtn = document.getElementById("startSetupBtn");
  if (startBtn) {
    startBtn.addEventListener("click", () => goTo("device_setup.html"));
  }
}

function deriveDirectoryDisplay(dirPath) {
  if (!dirPath) return null;
  if (!pathModule) return dirPath;
  const parts = dirPath.split(pathModule.sep).filter(Boolean);
  if (parts.length >= 3) {
    return parts.slice(-3).join("/");
  }
  return dirPath;
}

function setSelectedExistingDirectory(pathValue, displayValue) {
  selectedExistingPath = pathValue || null;
  const display = displayValue || (pathValue ? deriveDirectoryDisplay(pathValue) : null);
  const label = document.getElementById("selectedDirectoryLabel");
  if (label) {
    if (selectedExistingPath) {
      label.textContent = display || selectedExistingPath;
      label.classList.add("has-value");
    } else {
      label.textContent = "No directory selected";
      label.classList.remove("has-value");
    }
  }
  const gridContainer = document.getElementById("mainStudentGridContainer");
  if (gridContainer && !selectedExistingPath) {
    gridContainer.innerHTML = "";
  }
  const infoBox = document.getElementById("existingInfoBox");
  if (infoBox && !selectedExistingPath) {
    infoBox.classList.add("hidden");
    infoBox.innerHTML = "";
  }
}

// -----------------------------
// Custom Folder Picker
// -----------------------------

let currentFolderTreeData = [];
let pendingSelectedFolder = null;
let pendingSelectedStudent = null;

function openCustomDirectoryPicker() {
  const overlay = document.getElementById("folderPickerOverlay");
  const closeBtn = document.getElementById("closeFolderPickerBtn");
  const cancelBtn = document.getElementById("cancelFolderPickerBtn");
  const confirmBtn = document.getElementById("confirmFolderPickerBtn");

  if (!overlay) return;

  // Reset state
  pendingSelectedFolder = null;
  if (confirmBtn) confirmBtn.disabled = true;

  overlay.classList.remove("hidden");
  loadDirectory(null);

  // Event handlers
  const close = () => overlay.classList.add("hidden");

  closeBtn.onclick = close;
  cancelBtn.onclick = close;

  const backBtn = document.getElementById("pickerBackBtn");
  backBtn.onclick = () => {
    if (currentPickerPath && pathModule) {
      const parent = pathModule.dirname(currentPickerPath);
      loadDirectory(parent);
    }
  };

  const pathInput = document.getElementById("pickerCurrentPath");
  if (pathInput) {
    pathInput.onkeydown = (e) => {
      if (e.key === "Enter") {
        loadDirectory(pathInput.value);
      }
    };
  }

  confirmBtn.onclick = () => {
    if (pendingSelectedFolder && pendingSelectedFolder.type === 'session') {
      const { exam, session, students } = pendingSelectedFolder;

      const label = document.getElementById("selectedDirectoryLabel");
      if (label) {
        label.textContent = `Session: ${exam} / ${session}`;
        label.classList.add("has-value");
      }

      const infoBox = document.getElementById("existingInfoBox");
      if (infoBox) infoBox.classList.add("hidden");
      
      let sessionPath = "";
      if (students && students.length > 0 && typeof pathModule !== "undefined") {
         sessionPath = pathModule.dirname(students[0].path);
      }
      if (!sessionPath && currentPickerPath && typeof pathModule !== "undefined") {
         sessionPath = currentPickerPath;
      }
      if (!sessionPath && typeof pathModule !== "undefined") {
        try {
          const os = require("os");
          sessionPath = pathModule.join(os.homedir(), "Documents", exam, session);
        } catch (_) {
          sessionPath = "";
        }
      }

      setDirectoryInfo({
        exam_name: exam,
        session_id: session,
        student_id: "", 
        path: sessionPath,
        mode: "existing",
        display: `${exam}/${session}`
      });
      if (sessionPath) {
        localStorage.setItem("sessionPath", sessionPath);
      }

      renderMainStudentGrid(students);
      close();
    }
  };
}

let currentPickerPath = null;

async function loadDirectory(pathVal = null) {
  const container = document.getElementById("folderTreeContainer");
  const pathLabel = document.getElementById("pickerCurrentPath");
  if (!container) return;

  container.innerHTML = `<p class="loading-text">Loading...</p>`;

  // Optimistic update for better UX
  if (pathLabel && pathVal) {
    pathLabel.value = pathVal;
  }

  try {
    const url = pathVal
      ? `${API_ROOT}/api/directories?path=${encodeURIComponent(pathVal)}`
      : `${API_ROOT}/api/directories`;

    const res = await fetch(url);
    const data = await res.json();

    if (data.status === "ok") {
      currentPickerPath = data.current_path;
      if (pathLabel) {
        pathLabel.value = currentPickerPath;
      }

      // Strategy: If we find Exam/Session/Student structure, show Tree.
      // If not, fall back to "Subfolder List" (Browser Mode).
      if (data.directories && data.directories.length > 0) {
        renderTree(container, transformToHierarchy(data.directories));
      } else {
        // No exams found, let's load subfolders (Browsing Mode)
        loadSubfolders(currentPickerPath, container);
      }
    } else {
      container.innerHTML = `<p class="error-text">${data.message}</p>`;
    }

  } catch (err) {
    container.innerHTML = `<p class="error-text">Failed to load: ${err.message}</p>`;
  }
}

async function loadSubfolders(pathVal, container) {
  try {
    const res = await fetch(`${API_ROOT}/api/subfolders?path=${encodeURIComponent(pathVal)}`);
    const data = await res.json();

    if (data.status === "ok") {
      renderSubfolders(container, data.subfolders);
    } else {
      container.innerHTML = `<p class="empty-text">No folders found or access denied.</p>`;
    }
  } catch (e) {
    container.innerHTML = `<p class="error-text">${e.message}</p>`;
  }
}

function transformToHierarchy(flatDirs) {
  const hierarchy = {};
  flatDirs.forEach(d => {
    if (!hierarchy[d.exam_name]) hierarchy[d.exam_name] = {};
    if (!hierarchy[d.exam_name][d.session_id]) hierarchy[d.exam_name][d.session_id] = [];
    hierarchy[d.exam_name][d.session_id].push(d);
  });
  return hierarchy;
}

function renderSubfolders(container, subfolders) {
  container.innerHTML = "";
  if (!subfolders || subfolders.length === 0) {
    container.innerHTML = `<p class="empty-text">Empty folder.</p>`;
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "tree-root";

  subfolders.forEach(f => {
    const li = document.createElement("li");
    const div = document.createElement("div");
    div.className = "tree-item";
    div.textContent = `📁 ${f.name}`;
    div.onclick = () => {
      loadDirectory(f.path);
    };
    li.appendChild(div);
    ul.appendChild(li);
  });
  container.appendChild(ul);
}

// Kept for compatibility if needed
function fetchAndRenderFolderTree() {
  loadDirectory(null);
}

function renderTree(container, hierarchy) {
  container.innerHTML = "";
  const ul = document.createElement("ul");
  ul.className = "tree-root";

  // Iterate Exams
  Object.keys(hierarchy).sort().forEach(exam => {
    const examLi = document.createElement("li");
    const examLabel = document.createElement("div");
    examLabel.className = "tree-item exam-item";
    examLabel.textContent = `📂 ${exam}`;
    examLi.appendChild(examLabel);

    const sessionUl = document.createElement("ul");
    sessionUl.className = "tree-children hidden";

    // Iterate Sessions
    Object.keys(hierarchy[exam]).sort().forEach(session => {
      const sessionLi = document.createElement("li");
      const sessionLabel = document.createElement("div");
      sessionLabel.className = "tree-item session-item";
      sessionLabel.textContent = `📁 ${session}`;
      sessionLi.appendChild(sessionLabel);

      sessionLabel.onclick = (e) => {
        e.stopPropagation();

        // Highlight active session
        document.querySelectorAll(".session-item.selected").forEach(el => el.classList.remove("selected"));
        sessionLabel.classList.add("selected");

        const students = hierarchy[exam][session] || [];

        pendingSelectedFolder = { type: 'session', exam, session, students };
        document.getElementById("confirmFolderPickerBtn").disabled = false;
      };

      sessionLabel.ondblclick = () => {
        const students = hierarchy[exam][session] || [];
        pendingSelectedFolder = { type: 'session', exam, session, students };
        document.getElementById("confirmFolderPickerBtn").click();
      }

      sessionUl.appendChild(sessionLi);
    });

    examLabel.onclick = () => {
      // Accordion behavior: Close all other exams
      const allSessionUls = container.querySelectorAll(".tree-root > li > ul");
      allSessionUls.forEach(ul => {
        if (ul !== sessionUl) {
          ul.classList.add("hidden");
        }
      });
      sessionUl.classList.toggle("hidden");
    };

    examLi.appendChild(sessionUl);
    ul.appendChild(examLi);
  });

  container.appendChild(ul);
}

function renderMainStudentGrid(students) {
  const gridContainer = document.getElementById("mainStudentGridContainer");
  const useBtn = document.getElementById("use-existing-btn");
  pendingSelectedStudent = null;

  if (useBtn) useBtn.disabled = true;
  if (!gridContainer) return;

  gridContainer.innerHTML = "";

  if (Array.isArray(students)) {
    localStorage.setItem("sessionStudentsCount", String(students.length));
    const ids = students.map((s) => s.student_id).filter(Boolean);
    localStorage.setItem("sessionStudents", JSON.stringify(ids));
    try {
      const completed = JSON.parse(localStorage.getItem("completedStudents") || "[]");
      const filtered = completed.filter((id) => ids.includes(id));
      localStorage.setItem("completedStudents", JSON.stringify(filtered));
    } catch (_) {
      localStorage.removeItem("completedStudents");
    }
  }

  if (!students || students.length === 0) {
    gridContainer.innerHTML = `<p class="empty-text" style="grid-column: 1 / -1; text-align: center;">No students found in this session.</p>`;
    return;
  }

  students.forEach(student => {
    const studentCard = document.createElement("div");
    studentCard.className = "student-card";
    studentCard.style.cssText = `
        border: 1px solid var(--color-border);
        border-radius: 6px;
        padding: 5px 10px;
        text-align: left;
        cursor: pointer;
        transition: all 0.2s;
        background: var(--color-bg-card);
        display: flex;
        flex-direction: row;
        align-items: center;
        gap: 8px;
    `;

    // Check if the student's subfolder exists to mark as "completed"
    const studentPath = student.path || "";
    let isCompleted = false;
    let isPartial = false;
    if (studentPath && require("fs").existsSync(studentPath)) {
      try {
        const devicesStr = localStorage.getItem("templateDevices");
        const devicesMeta = devicesStr ? JSON.parse(devicesStr) : {};
        const hostnames = Object.keys(devicesMeta);
        if (hostnames.length > 0 && pathModule) {
          let totalExpected = 0;
          let totalFound = 0;
          hostnames.forEach((hostname) => {
            const commands = devicesMeta[hostname] || [];
            const hostDir = pathModule.join(studentPath, hostname);
            if (!require("fs").existsSync(hostDir)) return;
            const files = require("fs").readdirSync(hostDir);
            commands.forEach((cmd) => {
              totalExpected += 1;
              const safeCommand = cmd.replace(/\\s+/g, "_").replace(/\\//g, "_");
              const matched = files.some((name) => name.startsWith(safeCommand));
              if (matched) totalFound += 1;
            });
          });
          if (totalExpected > 0) {
            isCompleted = totalFound === totalExpected;
            isPartial = totalFound > 0 && totalFound < totalExpected;
          } else {
            isCompleted = true;
          }
        } else {
          isCompleted = true;
        }
      } catch (err) {
        isCompleted = true;
      }
    }

    if (isCompleted) {
      studentCard.style.border = "1.5px solid var(--color-success, #28a745)";
      studentCard.style.backgroundColor = "rgba(40, 167, 69, 0.05)";
    } else if (isPartial) {
      studentCard.style.border = "1.5px solid #ff9800";
      studentCard.style.backgroundColor = "rgba(255, 152, 0, 0.08)";
    }

    let completedList = [];
    try {
      completedList = JSON.parse(localStorage.getItem("completedStudents") || "[]");
    } catch (_) {
      completedList = [];
    }
    if (completedList.includes(student.student_id)) {
      studentCard.classList.add("executed-student");
      studentCard.style.borderColor = "var(--color-primary)";
      studentCard.style.backgroundColor = "rgba(31, 59, 115, 0.1)";
    }

    studentCard.innerHTML = `
        <div style="font-size: 1.2rem;">👤</div>
        <div style="min-width: 0;">
          <div style="font-weight: bold; font-size: 0.95rem; word-break: break-all;">${student.student_id}</div>
          ${student.student_name ? `<div style="font-size: 0.82rem; color: inherit; opacity: 0.85; word-break: break-word;">${student.student_name}</div>` : ""}
        </div>
    `;

    studentCard.onclick = () => {
      document.querySelectorAll("#mainStudentGridContainer .student-card.selected-student").forEach(el => {
        el.classList.remove("selected-student");
        // Reset to completed state or default depending
        if (el.dataset.completed === "true") {
           el.style.borderColor = "var(--color-success, #28a745)";
           el.style.backgroundColor = "rgba(40, 167, 69, 0.05)";
        } else if (el.dataset.completed === "partial") {
           el.style.borderColor = "#ff9800";
           el.style.backgroundColor = "rgba(255, 152, 0, 0.08)";
        } else if (el.classList.contains("executed-student")) {
           el.style.borderColor = "var(--color-primary)";
           el.style.backgroundColor = "rgba(31, 59, 115, 0.1)";
        } else {
           el.style.borderColor = "var(--color-border)";
           el.style.backgroundColor = "var(--color-bg-card)";
        }
        el.style.color = "inherit";
      });

      studentCard.classList.add("selected-student");
      studentCard.style.borderColor = "var(--color-primary)";
      studentCard.style.backgroundColor = "var(--color-primary)";
      studentCard.style.color = "#ffffff";

      pendingSelectedStudent = student;
      if (useBtn) useBtn.disabled = false;
    };

    studentCard.dataset.completed = isCompleted ? "true" : (isPartial ? "partial" : "false");

    studentCard.ondblclick = () => {
      pendingSelectedStudent = student;
      if (useBtn) {
        useBtn.disabled = false;
        useBtn.click();
      }
    }

    gridContainer.appendChild(studentCard);
  });


}

// Replaced original openExistingDirectoryDialog
// async function openExistingDirectoryDialog(startPath) {
//   if (ipcRenderer) {
//     try {
//       const selectedPath = await ipcRenderer.invoke("select-directory", startPath);
//       if (selectedPath) {
//         setSelectedExistingDirectory(selectedPath);
//         const infoBox = document.getElementById("existingInfoBox");
//         if (infoBox) {
//           infoBox.classList.remove("hidden");
//           infoBox.innerHTML = `
//             <strong>Selected:</strong> ${deriveDirectoryDisplay(selectedPath) || selectedPath
//             }<br/>
//             <span class="hint">Click "Use Selected" to continue.</span>
//           `;
//         }
//       }
//       return;
//     } catch (err) {
//       console.error(err);
//       alert(`Failed to open directory picker: ${err.message}`);
//       return;
//     }
//   }

//   const manual = prompt("Enter the directory path:");
//   if (manual) {
//     setSelectedExistingDirectory(manual);
//     const infoBox = document.getElementById("existingInfoBox");
//     if (infoBox) {
//       infoBox.classList.remove("hidden");
//       infoBox.innerHTML = `
//         <strong>Selected:</strong> ${deriveDirectoryDisplay(manual) || manual}<br/>
//         <span class="hint">Click "Use Selected" to continue.</span>
//       `;
//     }
//   }
// }

function parseStudentFile(content, options = {}) {
  const { hasTitleRow = false, hasHeader = false, hasNumberColumn = false } = options;
  const lines = content.split(/\r?\n/);
  const students = [];
  const rowsToSkip = (hasTitleRow ? 1 : 0) + (hasHeader ? 1 : 0);
  const studentIdIndex = hasNumberColumn ? 1 : 0;
  const studentNameIndex = studentIdIndex + 1;
  lines.forEach((line, index) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    if (index < rowsToSkip) return;

    const parts = trimmed.split(/[\t,]+/).map((p) => p.trim());
    if (!parts.length) return;

    const studentId = (parts[studentIdIndex] || "").trim();
    if (!studentId) return;

    const studentName = (parts[studentNameIndex] || "").trim();
    students.push({ id: studentId, name: studentName });
  });
  return students;
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Failed to read file."));
    reader.onload = () => resolve(reader.result);
    reader.readAsText(file);
  });
}

function readFileAsArrayBuffer(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Failed to read file as binary."));
    reader.onload = () => resolve(reader.result);
    reader.readAsArrayBuffer(file);
  });
}

async function handleCreateDirectory(event) {
  event.preventDefault();
  const exam = document.getElementById("createExamName").value.trim();
  const session = document.getElementById("createSessionId").value.trim();
  const student = document.getElementById("createStudentId").value.trim();
  const studentName = (document.getElementById("createStudentName")?.value || "").trim();
  if (!exam || !session || !student) {
    alert("Please complete all fields for the new directory.");
    return;
  }

  try {
    const data = await fetchJson("/api/create_directory", {
      method: "POST",
      body: JSON.stringify({
        examName: exam,
        sessionId: session,
        studentId: student,
        studentName: studentName,
      }),
    });
    setDirectoryInfo({
      exam_name: data.exam_name,
      session_id: data.session_id,
      student_id: data.student_id,
      path: data.path,
      mode: "create",
      display: `${data.exam_name}/${data.session_id}/${data.student_id}`,
    });
    if (pathModule) {
      localStorage.setItem("sessionPath", pathModule.dirname(data.path));
    }
    alert(data.message);
    goTo("connection.html");
  } catch (err) {
    console.error(err);
    alert(`Failed to create directory: ${err.message}`);
  }
}

async function handleUseExistingDirectory() {
  if (!pendingSelectedStudent) {
    alert("Please choose a session and select a student first.");
    return;
  }

  try {
    const data = await fetchJson("/api/select_directory", {
      method: "POST",
      body: JSON.stringify({
        existingPath: pendingSelectedStudent.path,
      }),
    });
    setDirectoryInfo({
      exam_name: data.exam_name,
      session_id: data.session_id,
      student_id: data.student_id,
      path: data.path,
      mode: "existing",
      display: `${data.exam_name}/${data.session_id}/${data.student_id}`,
    });
    if (pathModule) {
      localStorage.setItem("sessionPath", pathModule.dirname(data.path));
    }
    setSelectedExistingDirectory(
      data.path,
      `${data.exam_name}/${data.session_id}/${data.student_id}`
    );
    alert(data.message);
    goTo("connection.html");
  } catch (err) {
    console.error(err);
    alert(`Failed to use directory: ${err.message}`);
  }
}

async function handleBulkCreate(event) {
  event.preventDefault();
  const exam = document.getElementById("bulkExamName").value.trim();
  const session = document.getElementById("bulkSessionId").value.trim();
  const fileInput = document.getElementById("bulkFile");
  const resultsBox = document.getElementById("bulkResults");
  const hasTitleRow = document.getElementById("bulkHasTitleRow")?.checked || false;
  const hasHeader = document.getElementById("bulkHasHeader")?.checked || false;
  const hasNumberColumn = document.getElementById("bulkHasNumber")?.checked || false;

  if (!fileInput.files.length) {
    alert("Please select a file.");
    return;
  }
  if (!exam || !session) {
    alert("Please provide exam and session details for bulk creation.");
    return;
  }

  try {
    const file = fileInput.files[0];
    let students = [];

    // Check file extension
    const name = file.name.toLowerCase();
    if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
      if (!XLSX) {
        throw new Error("XLSX parsing library is missing.");
      }
      const arrayBuffer = await readFileAsArrayBuffer(file);
      const workbook = XLSX.read(arrayBuffer, { type: "array" });
      const firstSheetName = workbook.SheetNames[0];
      const worksheet = workbook.Sheets[firstSheetName];
      const json = XLSX.utils.sheet_to_json(worksheet, { header: 1 });
      const rowsToSkip = (hasTitleRow ? 1 : 0) + (hasHeader ? 1 : 0);
      const studentIdIndex = hasNumberColumn ? 1 : 0;
      const studentNameIndex = studentIdIndex + 1;

      students = json
        .filter((row, idx) => idx >= rowsToSkip)
        .map(row => {
          const cols = row.map(cell => (cell || "").toString().trim());
          const id = (cols[studentIdIndex] || "").toString().trim();
          const studentName = (cols[studentNameIndex] || "").toString().trim();
          return { id, name: studentName };
        })
        .filter(s => s.id);

    } else {
      // Assume text/csv
      const content = await readFileAsText(file);
      students = parseStudentFile(content, { hasTitleRow, hasHeader, hasNumberColumn });
    }

    if (!students.length) {
      alert("No student IDs detected in the file.");
      return;
    }

    const data = await fetchJson("/api/directories/bulk", {
      method: "POST",
      body: JSON.stringify({ examName: exam, sessionId: session, students }),
    });

    const created = data.created || [];
    if (!created.length) {
      resultsBox.classList.remove("hidden");
      resultsBox.innerHTML =
        "<strong>No directories were created. Check the file contents.</strong>";
      return;
    }

    resultsBox.classList.remove("hidden");
    resultsBox.innerHTML = `
      <strong>Created ${created.length} directories.</strong>
      <ul>${created
        .map((dir) => `<li>${dir.display}</li>`)
        .join("")}</ul>
      <p>Opening session folder...</p>
    `;

    // Let user pick via dialog
    const primary = created[0];
    let sessionPath = null;

    if (primary && pathModule) {
      sessionPath = pathModule.dirname(primary.path);
      // Removed shell.openPath to prevents double windows
    }

    // Switch to Existing tab so they can pick
    const showExistingBtn = document.getElementById("showExistingBtn");
    if (showExistingBtn) showExistingBtn.click();

    // Clear previous
    setSelectedExistingDirectory(null);

    // Suggest they pick one
    const infoBox = document.getElementById("existingInfoBox");
    if (infoBox) {
      infoBox.classList.remove("hidden");
      infoBox.innerHTML = `
       <strong>Directories Created!</strong><br/>
       <span class="hint">Please select the specific student folder you want to use.</span>
     `;
    }

    // Launch the picker rooted at sessionPath to help them
    // Note: sessionPath is for OS dialog, but for custom picker we just open it.
    // Ideally we could filter/expand to that exam/session, but for now just opening it is fine.
    setTimeout(() => openCustomDirectoryPicker(), 500);
  } catch (err) {
    console.error(err);
    alert(`Bulk creation failed: ${err.message}`);
  }
}

function setupDirectoryPage() {
  loadNavbar();
  const sections = {
    create: document.getElementById("createSection"),
    existing: document.getElementById("existingSection"),
    bulk: document.getElementById("bulkSection"),
  };

  const buttons = {
    create: document.getElementById("showCreateBtn"),
    existing: document.getElementById("showExistingBtn"),
    bulk: document.getElementById("showBulkBtn"),
  };

  const showSection = (name) => {
    Object.entries(sections).forEach(([key, section]) => {
      if (!section) return;
      if (key === name) section.classList.remove("hidden");
      else section.classList.add("hidden");
    });
    Object.entries(buttons).forEach(([key, btn]) => {
      if (!btn) return;
      if (key === name) btn.classList.add("active");
      else btn.classList.remove("active");
    });
  };

  Object.entries(buttons).forEach(([name, btn]) => {
    if (btn) btn.addEventListener("click", () => showSection(name));
  });

  const createForm = document.getElementById("createDirectoryForm");
  const bulkForm = document.getElementById("bulkCreateForm");
  const useBtn = document.getElementById("use-existing-btn");
  const chooseBtn = document.getElementById("chooseDirectoryBtn");
  const clearBtn = document.getElementById("clearExistingSelectionBtn");
  const addStudentBtn = document.getElementById("addStudentBtn");

  if (createForm) createForm.addEventListener("submit", handleCreateDirectory);
  if (bulkForm) bulkForm.addEventListener("submit", handleBulkCreate);
  if (useBtn) useBtn.addEventListener("click", handleUseExistingDirectory);
  


  if (chooseBtn) {
    chooseBtn.addEventListener("click", () => {
      // Pass the currently selected path if it exists, otherwise undefined (which defaults to Docs)
      // openExistingDirectoryDialog(selectedExistingPath);
      openCustomDirectoryPicker();
    });
  }
  if (clearBtn) clearBtn.addEventListener("click", () =>
    setSelectedExistingDirectory(null)
  );
  if (addStudentBtn) {
    addStudentBtn.addEventListener("click", () => {
      const exam = localStorage.getItem("examName");
      const session = localStorage.getItem("sessionId");
      if (!exam || !session) {
        alert("Please select a session first.");
        return;
      }

      openAddStudentModal(async (studentId, studentName) => {
        const exam = localStorage.getItem("examName");
        const session = localStorage.getItem("sessionId");
        if (!exam || !session) {
          alert("Please select a session first.");
          return;
        }

        let sessionPath = localStorage.getItem("sessionPath");
        if (!sessionPath && localStorage.getItem("basePath") && typeof pathModule !== "undefined") {
          sessionPath = pathModule.dirname(localStorage.getItem("basePath"));
        }
        if (!sessionPath && typeof pathModule !== "undefined") {
          try {
            const os = require("os");
            sessionPath = pathModule.join(os.homedir(), "Documents", exam, session);
          } catch (_) {
            sessionPath = null;
          }
        }

        if (!sessionPath) {
          alert("Session path not found. Please re-select the session.");
          return;
        }

        try {
          await fetchJson("/api/add_student", {
            method: "POST",
            body: JSON.stringify({
              session_path: sessionPath,
              student_id: studentId.trim(),
              student_name: (studentName || "").trim(),
            }),
          });
          const res = await fetch(`${API_ROOT}/api/directories`);
          const data = await res.json();
          if (data.status === "ok" && data.directories) {
            const hierarchy = transformToHierarchy(data.directories);
            if (hierarchy[exam] && hierarchy[exam][session]) {
              renderMainStudentGrid(hierarchy[exam][session]);
            }
          }
        } catch (err) {
          console.error(err);
          alert(`Failed to add student: ${err.message}`);
        }
      });
    });
  }

  const storedMode = localStorage.getItem("directoryMode");
  const storedBasePath = localStorage.getItem("basePath");
  
  if (storedMode === "existing") {
    setSelectedExistingDirectory(
      storedBasePath,
      localStorage.getItem("directoryDisplay")
    );
    
    // Auto-load grid if we have a path
    if (storedBasePath && storedBasePath !== "null") {
       fetch(`${API_ROOT}/api/directories`) // Wait, if we use the backend API, the default fetches subfolders. We can use loadDirectory natively.
         .then(res => res.json())
         .then(data => {
            if (data.status === "ok" && data.directories) {
               const hierarchy = transformToHierarchy(data.directories);
               const exam = localStorage.getItem("examName");
               const session = localStorage.getItem("sessionId");
               
               if (exam && session && hierarchy[exam] && hierarchy[exam][session]) {
                  const students = hierarchy[exam][session];
                  renderMainStudentGrid(students);
               }
            }
         }).catch(err => console.error("Auto-load failed:", err));
    }
  } else {
    setSelectedExistingDirectory(null);
  }

  const defaultSection = storedMode === "existing" ? "existing" : "create";
  showSection(defaultSection);
}

function openAddStudentModal(onConfirm) {
  const overlay = document.getElementById("addStudentOverlay");
  const closeBtn = document.getElementById("closeAddStudentBtn");
  const cancelBtn = document.getElementById("cancelAddStudentBtn");
  const confirmBtn = document.getElementById("confirmAddStudentBtn");
  const input = document.getElementById("newStudentIdInput");
  const nameInput = document.getElementById("newStudentNameInput");
  if (!overlay || !input || !confirmBtn) return;

  const close = () => {
    overlay.classList.add("hidden");
    input.value = "";
    if (nameInput) nameInput.value = "";
  };

  if (closeBtn) closeBtn.onclick = close;
  if (cancelBtn) cancelBtn.onclick = close;

  confirmBtn.onclick = () => {
    const value = (input.value || "").trim();
    if (!value) {
      alert("Please enter a student ID.");
      return;
    }
    overlay.classList.add("hidden");
    input.value = "";
    const nameValue = nameInput ? (nameInput.value || "").trim() : "";
    if (nameInput) nameInput.value = "";
    if (typeof onConfirm === "function") onConfirm(value, nameValue);
  };

  overlay.classList.remove("hidden");
  input.focus();
}

// -----------------------------
// Sample log collection
// -----------------------------

function setupSampleCollectPage() {
  loadNavbar();

  const statusBox = document.getElementById("sampleStatus");
  const list = document.getElementById("sampleCommandsList");
  const log = document.getElementById("sampleLog");
  const dirLabel = document.getElementById("sampleDirLabel");
  const sampleCommandSearch = document.getElementById("sampleCommandSearch");

  const setStatus = (msg) => {
    if (!statusBox) return;
    const msgEl = statusBox.querySelector('.status-banner__msg');
    if (msgEl) msgEl.textContent = msg;
    else statusBox.textContent = msg;
  };

  const appendSampleLog = (msg) => {
    if (!log) return;
    log.textContent += `${msg}\n`;
    log.scrollTop = log.scrollHeight;
  };

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
    const checkboxes = document.querySelectorAll('#sampleCommandsList input[type="checkbox"]');
    return Array.from(checkboxes)
      .filter((cb) => cb.checked)
      .map((cb) => cb.value);
  };

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

    const btn = document.getElementById("runSampleCollectBtn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Collecting...";
    }

    if (log) log.textContent = "";
    appendSampleLog("Starting collection...");

    try {
      const res = await fetch(`${API_ROOT}/api/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !res.body) {
        throw new Error("Failed to start collection.");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let hasError = false;

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
            } else if (payloadObj.msg) {
              appendSampleLog(payloadObj.msg);
            }
          } catch (_) {
            appendSampleLog(line.trim());
          }
        }
      }

      if (hasError) {
        alert("Collection finished with errors. Check the log.");
      } else {
        alert("Logs collected successfully.");
      }
    } catch (err) {
      console.error(err);
      alert(err.message || "Collection failed.");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Collect Logs";
      }
    }
  };

  setStatus("Sample Collect ready. Loading commands...");
  loadCommands();

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

  document.getElementById("runSampleCollectBtn")?.addEventListener("click", () => {
    setStatus("Starting collection...");
    runSampleCollection();
  });

  toggleSampleConnectionFields();
}

// -----------------------------
// Connection page
// -----------------------------

function applySerialPreset(preset) {
  const portInput = document.getElementById("serialPort");
  if (!portInput) return;
  if (preset === "custom") {
    portInput.removeAttribute("readonly");
  } else {
    portInput.value = SERIAL_PRESETS[preset] || "/dev/ttyUSB0";
    portInput.setAttribute("readonly", "readonly");
  }
}

function toggleConnectionFields() {
  const conn = document.querySelector('input[name="connType"]:checked')?.value || "serial";
  const sshFields = document.getElementById("sshFields");
  const serialFields = document.getElementById("serialFields");
  const resetBtn = document.getElementById("resetDeviceBtn");
  if (!sshFields || !serialFields) return;
  if (conn === "ssh") {
    sshFields.classList.remove("hidden");
    serialFields.classList.add("hidden");
    if (resetBtn) resetBtn.disabled = true;
  } else {
    sshFields.classList.add("hidden");
    serialFields.classList.remove("hidden");
    if (resetBtn) resetBtn.disabled = false;
  }
}

async function saveConnection({ autoRun = false, triggerButton = null } = {}) {
  autoRunAfterConnect = autoRun;
  const type = document.querySelector('input[name="connType"]:checked');
  if (!type) {
    alert("Please choose a connection type.");
    autoRunAfterConnect = false;
    return;
  }
  const conn = type.value;
  const payload = { connection: conn, mode: conn };
  const connectBtn = triggerButton || null;
  const originalBtnText = connectBtn ? connectBtn.textContent : null;
  setProgressValue(0);

  let connectionSucceeded = false;
  let finalHostname = null;
  let errorMessage = "";
  let errorLogged = false;

  if (conn === "ssh") {
    const storedHost = localStorage.getItem("sshHost") || "";
    const storedUser = localStorage.getItem("sshUser") || "";
    const storedPass = localStorage.getItem("sshPass") || "";

    const hostInput = document.getElementById("sshHost");
    const userInput = document.getElementById("sshUser");
    const passInput = document.getElementById("sshPass");

    let host = hostInput?.value.trim() || "";
    let user = userInput?.value.trim() || "";
    let pass = passInput?.value || "";

    if (autoRunAfterConnect) {
      host = storedHost || host;
      user = storedUser || user;
      pass = storedPass || pass;
    } else {
      if (!host && storedHost) host = storedHost;
      if (!user && storedUser) user = storedUser;
      if (!pass && storedPass) pass = storedPass;
    }

    if (!host || !user || !pass) {
      alert("Please provide SSH host, username and password.");
      autoRunAfterConnect = false;
      return;
    }

    if (hostInput && hostInput.value.trim() !== host) hostInput.value = host;
    if (userInput && userInput.value.trim() !== user) userInput.value = user;
    if (passInput && passInput.value !== pass) passInput.value = pass;

    const storedSshPort = localStorage.getItem("sshPort") || "22";
    const sshPortInput = document.getElementById("sshPort");
    let sshPortValue = sshPortInput ? sshPortInput.value.trim() : "";
    if (!sshPortValue) {
      sshPortValue = storedSshPort || "22";
    }
    payload.ssh = { host, username: user, password: pass, port: sshPortValue };
    payload.host = host;
    payload.username = user;
    payload.password = pass;
    payload.port = sshPortValue;
  } else {
    const storedPort = localStorage.getItem("serialPort") || "";
    const portInput = document.getElementById("serialPort");
    let port = portInput ? portInput.value.trim() : "";
    if (autoRunAfterConnect && storedPort) {
      port = storedPort;
    } else if (!port && storedPort) {
      port = storedPort;
    }
    if (!port) port = "/dev/ttyUSB0";
    if (portInput && portInput.value.trim() !== port) {
      portInput.value = port;
    }
    payload.serial = { port };
  }

  let timeoutId;
  try {
    if (connectBtn) {
      connectBtn.textContent = "Connecting...";
      connectBtn.disabled = true;
    }
    const modal = showStatusModal("Connecting... Please wait.", "pending");
    const controller = new AbortController();
    const connectionTimeoutMs = 15000;
    timeoutId = setTimeout(() => controller.abort(), connectionTimeoutMs);
    const response = await fetch(`${API_ROOT}/api/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!response.ok || !response.body) {
      const text = await response.text();
      throw new Error(text || response.statusText || "Connection failed");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let payloadObj = null;
        try {
          payloadObj = JSON.parse(trimmed);
        } catch (err) {
          appendLogLine(trimmed);
          continue;
        }

        const { type: evtType, msg, hostname, trace, success } = payloadObj;
        const pct = typeof payloadObj.progress_pct === "number" ? payloadObj.progress_pct : null;
        if (pct !== null) {
          setProgressValue(pct);
        }
        if (evtType === "progress") {
          appendLogLine(`[${nowTimestamp()}] ${msg}`);
        } else if (evtType === "success") {
          connectionSucceeded = true;
          finalHostname = hostname || null;
          const successMsg = msg || "Connection established.";
          appendLogLine(`[SUCCESS] ${successMsg}`);
          updateStatusModal(modal, successMsg, "success", true);
        } else if (evtType === "error") {
          errorMessage = msg || "Connection failed.";
          appendLogLine(`[ERROR] ${errorMessage}`);
          errorLogged = true;
          if (trace) {
            appendLogLine(trace);
          }
          updateStatusModal(modal, errorMessage, "error");
        }

        if (evtType === "done") {
          if (!success && !connectionSucceeded && !errorMessage) {
            errorMessage = "Connection failed.";
            updateStatusModal(modal, errorMessage, "error");
          }
        }
      }
    }

    if (buffer.trim()) {
      try {
        const payloadObj = JSON.parse(buffer.trim());
        const { type: evtType, msg, hostname, trace } = payloadObj;
        const pct = typeof payloadObj.progress_pct === "number" ? payloadObj.progress_pct : null;
        if (pct !== null) {
          setProgressValue(pct);
        }
        if (evtType === "progress") {
          appendLogLine(`[${nowTimestamp()}] ${msg}`);
        } else if (evtType === "success") {
          connectionSucceeded = true;
          finalHostname = hostname || null;
          const successMsg = msg || "Connection established.";
          appendLogLine(`[SUCCESS] ${successMsg}`);
          updateStatusModal(modal, successMsg, "success", true);
        } else if (evtType === "error") {
          errorMessage = msg || "Connection failed.";
          appendLogLine(`[ERROR] ${errorMessage}`);
          errorLogged = true;
          if (trace) appendLogLine(trace);
          updateStatusModal(modal, errorMessage, "error");
        }
      } catch (err) {
        appendLogLine(buffer.trim());
      }
    }
  } catch (err) {
    console.error(err);
    if (!connectionSucceeded) {
      if (err.name === "AbortError") {
        showStatusModal(
          "Connection timed out. Please check the device and try again.",
          "error"
        );
      } else {
        showStatusModal(`Connection failed: ${err.message}`, "error");
      }
    }
  } finally {
    if (typeof timeoutId !== "undefined") {
      clearTimeout(timeoutId);
      timeoutId = undefined;
    }
    if (connectBtn) {
      connectBtn.disabled = false;
      connectBtn.textContent = originalBtnText || "Connect";
    }
    autoRunAfterConnect = false;
  }
  
  return connectionSucceeded;
}

async function resetCiscoDevice({ triggerButton = null } = {}) {
  const type = document.querySelector('input[name="connType"]:checked');
  const conn = type?.value || "serial";
  if (conn !== "serial") {
    alert("Cisco reset is only supported in serial mode.");
    return false;
  }

  const deviceTypeSelect = document.getElementById("resetDeviceType");
  const deviceType = (deviceTypeSelect?.value || "switch").toLowerCase();
  const resetMessage = deviceType === "router"
    ? "This will reload the connected Cisco router without saving the running configuration. Continue?"
    : "This will delete vlan.dat and reload the connected Cisco switch without saving the running configuration. Continue?";

  const confirmed = confirm(resetMessage);
  if (!confirmed) return false;

  const portInput = document.getElementById("serialPort");
  const port =
    portInput?.value.trim() ||
    localStorage.getItem("serialPort") ||
    SERIAL_PRESETS.linux_usb;

  if (!port) {
    alert("Please configure the serial port first.");
    return false;
  }

  const resetBtn = triggerButton || null;
  const originalBtnText = resetBtn ? resetBtn.textContent : null;

  try {
    if (resetBtn) {
      resetBtn.disabled = true;
      resetBtn.textContent = "Resetting...";
    }

    appendLogLine(`[${nowTimestamp()}] Starting Cisco reset on ${port}...`);
    const modal = showStatusModal("Resetting device... Please wait.", "pending");
    const response = await fetch(`${API_ROOT}/api/reset_device`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        connection: "serial",
        mode: "serial",
        device_type: deviceType,
        serial: { port },
      }),
    });

    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }

    const logs = Array.isArray(data.logs) ? data.logs : [];
    logs.forEach((line) => appendLogLine(line));

    if (!response.ok || data.status === "error") {
      const message = data.message || "Cisco reset failed.";
      appendLogLine(`[ERROR] ${message}`);
      updateStatusModal(modal, message, "error");
      return false;
    }

    const message = data.message || "Reset command sent successfully.";
    appendLogLine(`[SUCCESS] ${message}`);
    updateStatusModal(modal, message, "success", true);
    return true;
  } catch (err) {
    console.error(err);
    const message = err.message || "Cisco reset failed.";
    appendLogLine(`[ERROR] ${message}`);
    showStatusModal(message, "error");
    return false;
  } finally {
    if (resetBtn) {
      resetBtn.disabled = false;
      resetBtn.textContent = originalBtnText || "Reset Cisco Device";
    }
  }
}

// Queue State
let executionQueue = [];
let currentQueueIndex = 0;
let isSequenceRunning = false;
let manualNextHostname = null;
let completedQueueHosts = new Set();
let pollingDisconnect = false;

function getStudentProgressState() {
  let total = parseInt(localStorage.getItem("sessionStudentsCount") || "0", 10);
  let completed = [];

  try {
    completed = JSON.parse(localStorage.getItem("completedStudents") || "[]");
  } catch (_) {
    completed = [];
  }

  let sessionPath = localStorage.getItem("sessionPath") || "";
  const basePath = localStorage.getItem("basePath");
  if (!sessionPath && basePath && pathModule) {
    sessionPath = pathModule.dirname(basePath);
    localStorage.setItem("sessionPath", sessionPath);
  }

  if ((!total || Number.isNaN(total)) && sessionPath) {
    try {
      if (require("fs").existsSync(sessionPath)) {
        const dirs = require("fs")
          .readdirSync(sessionPath, { withFileTypes: true })
          .filter((d) => d.isDirectory())
          .map((d) => d.name);
        total = dirs.length;
        localStorage.setItem("sessionStudentsCount", String(total));
        completed = completed.filter((id) => dirs.includes(id));
        localStorage.setItem("completedStudents", JSON.stringify(completed));
      }
    } catch (_) {
      total = 0;
    }
  }

  return {
    total,
    completed,
    completedCount: completed.length,
    allDone: total > 0 && completed.length >= total,
  };
}

function updateDoneStudentButtonLabel() {
  const doneStudentBtn = document.getElementById("doneStudentBtn");
  if (!doneStudentBtn) return;
  const { allDone } = getStudentProgressState();
  doneStudentBtn.textContent = allDone ? "Start Grading" : "Next Student";
}

function setupConnectionPage() {
  loadNavbar();
  if (!ensureDirectoryConfigured()) return;

  // Build Execution Queue UI
  const deviceQueueContainer = document.getElementById("deviceQueueContainer");
  const queueStatus = document.getElementById("queueStatus");
  const startSequenceBtn = document.getElementById("startSequenceBtn");
  const doneStudentBtn = document.getElementById("doneStudentBtn");
  updateDoneStudentButtonLabel();

  if (deviceQueueContainer) {
    deviceQueueContainer.innerHTML = "";
    try {
      const devicesStr = localStorage.getItem("templateDevices");
      if (devicesStr) {
        const devicesMeta = JSON.parse(devicesStr);
        executionQueue = Object.keys(devicesMeta).map(hostname => ({
          hostname,
          commands: devicesMeta[hostname],
          status: "pending" // pending, running, done
        }));
        
        executionQueue.forEach((device, index) => {
          const row = document.createElement("div");
          row.className = `queue-item ${index === 0 ? "active-queue-item" : ""}`;
          row.id = `q-${device.hostname}`;
          row.style.cssText = `
             background: var(--color-bg-card);
             border: 1px solid var(--color-border);
             padding: 10px 14px;
             border-radius: 6px;
             display: flex;
             justify-content: space-between;
             align-items: center;
          `;
          
          row.innerHTML = `
            <div>
              <strong>${device.hostname}</strong>
              <div style="font-size: 0.8rem; color: var(--color-muted);">${device.commands.length} commands</div>
            </div>
            <span class="q-badge" style="font-size: 0.85rem; font-weight: bold; color: var(--color-muted);">WAITING</span>
          `;
          
          if (index === 0) {
            row.style.borderColor = "var(--color-primary)";
            row.style.backgroundColor = "rgba(31, 59, 115, 0.05)";
          }

          row.addEventListener("click", () => {
            if (isSequenceRunning) {
              return;
            }
            const proceed = confirm(`Start with ${device.hostname} now?`);
            if (!proceed) return;
            manualNextHostname = device.hostname;
            document.getElementById("startSequenceBtn")?.click();
          });
          
          deviceQueueContainer.appendChild(row);
        });
      }
    } catch(err) {
      console.error(err);
    }
  }

  const form = document.getElementById("connectionForm");
  const radios = document.querySelectorAll('input[name="connType"]');
  const presetRadios = document.querySelectorAll('input[name="serialPreset"]');
  const backBtn = document.getElementById("backToDirectoryBtn");

  if (form) {
    const savedConn = localStorage.getItem("connection");
    const targetRadio = Array.from(radios).find((r) => r.value === savedConn);
    if (targetRadio) targetRadio.checked = true;
    toggleConnectionFields();

    form.addEventListener("submit", (evt) => evt.preventDefault());

    document
      .getElementById("reconnectRunBtn")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        saveConnection({ autoRun: false, triggerButton: evt.currentTarget });
      });

    document
      .getElementById("resetDeviceBtn")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        resetCiscoDevice({ triggerButton: evt.currentTarget });
      });

    document
      .getElementById("doneStudentBtn")
      ?.addEventListener("click", (evt) => {
        evt.preventDefault();
        const { allDone } = getStudentProgressState();
        if (allDone) {
          localStorage.setItem("autoRunResults", "true");
          goTo("results.html");
        } else {
          goTo("index.html");
        }
      });
  }

  radios.forEach((r) => r.addEventListener("change", toggleConnectionFields));

  presetRadios.forEach((r) =>
    r.addEventListener("change", () => applySerialPreset(r.value))
  );

  const savedPort = localStorage.getItem("serialPort");
  if (savedPort) {
    document.querySelector('input[name="serialPreset"][value="custom"]').checked = true;
    applySerialPreset("custom");
    document.getElementById("serialPort").value = savedPort;
  } else {
    applySerialPreset("linux_usb");
  }

  document.getElementById("sshHost").value = localStorage.getItem("sshHost") || "";
  document.getElementById("sshUser").value = localStorage.getItem("sshUser") || "";
  document.getElementById("sshPass").value = localStorage.getItem("sshPass") || "";
  const sshPortInput = document.getElementById("sshPort");
  if (sshPortInput) {
    sshPortInput.value = localStorage.getItem("sshPort") || "22";
  }

  if (backBtn) {
    backBtn.addEventListener("click", () => goTo("index.html"));
  }

  document
    .getElementById("clearLogBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      clearExecutionLog();
    });

  startSequenceBtn?.addEventListener("click", () => {
     if (isSequenceRunning) return;
     isSequenceRunning = true;
     currentQueueIndex = 0;
     completedQueueHosts = new Set();
     startSequenceBtn.disabled = true;
     startSequenceBtn.textContent = "Sequence Running...";
     doneStudentBtn.disabled = true;
     queueStatus.textContent = "Running";
     queueStatus.style.color = "var(--color-primary)";
     runNextDeviceInQueue();
  });
}

function getNextQueueIndex(preferredHostname) {
  if (preferredHostname) {
    const idx = executionQueue.findIndex(
      (d) => d.hostname === preferredHostname && !completedQueueHosts.has(d.hostname)
    );
    if (idx !== -1) return idx;
  }
  for (let i = 0; i < executionQueue.length; i++) {
    const hostname = executionQueue[i].hostname;
    if (!completedQueueHosts.has(hostname)) return i;
  }
  return -1;
}

async function runNextDeviceInQueue() {
  const preferred = manualNextHostname;
  manualNextHostname = null;
  const nextIndex = getNextQueueIndex(preferred);
  if (nextIndex === -1) {
    // All done!
    isSequenceRunning = false;
    const finishedStudent = localStorage.getItem("studentId") || "";
    localStorage.setItem("lastExecutedStudent", finishedStudent);
    if (finishedStudent) {
      let completed = [];
      try {
        completed = JSON.parse(localStorage.getItem("completedStudents") || "[]");
      } catch (_) {
        completed = [];
      }
      if (!completed.includes(finishedStudent)) {
        completed.push(finishedStudent);
        localStorage.setItem("completedStudents", JSON.stringify(completed));
      }
    }
    document.getElementById("startSequenceBtn").textContent = "Sequence Finished";
    document.getElementById("queueStatus").textContent = "Completed";
    document.getElementById("queueStatus").style.color = "var(--color-success, #28a745)";
    document.getElementById("doneStudentBtn").disabled = false;
    updateDoneStudentButtonLabel();
    alert("All devices completed. Please hit Done to proceed to the next student.");
    return;
  }

  currentQueueIndex = nextIndex;
  const currentDevice = executionQueue[currentQueueIndex];
  const row = document.getElementById(`q-${currentDevice.hostname}`);
  const badge = row.querySelector(".q-badge");
  
  badge.textContent = "PLUG IN NOW";
  badge.style.color = "#ff9800"; // Orange attention
  row.style.borderColor = "#ff9800";
  row.style.backgroundColor = "rgba(255, 152, 0, 0.05)";

  // Set the current target context so execution scripts use it
  localStorage.setItem("connectedHostname", currentDevice.hostname);
  setStoredCommandsFromDevice(currentDevice.hostname);

  // Require operator confirmation before running the next device
  const proceed = confirm(
    `Plug in ${currentDevice.hostname} and click OK to start collecting logs.`
  );
  if (!proceed) {
    badge.textContent = "WAITING";
    badge.style.color = "var(--color-muted)";
    document.getElementById("startSequenceBtn").disabled = false;
    isSequenceRunning = false;
    return;
  }

  // Run commands directly (do not pre-connect; /api/execute manages serial)
  const abortBtn = document.getElementById("abortExecutionBtn");

  const runExecute = async (forceSkipHostname = false) => {
    badge.textContent = "EXECUTING...";
    badge.style.color = "var(--color-primary)";

    // Create abort controller for this execution
    currentAbortController = new AbortController();
    if (abortBtn) abortBtn.style.display = "inline-block";

    try {
         const directoryMode = localStorage.getItem("directoryMode") || "create";
         const basePath = localStorage.getItem("basePath");
         const payload = {
           deviceId: currentDevice.hostname,
           commands: currentDevice.commands,
           student_id: localStorage.getItem("studentId") || localStorage.getItem("selectedStudent") || "unknown",
           exam_name: localStorage.getItem("examName") || "unknown",
           session_id: localStorage.getItem("sessionId") || "unknown",
           log_mode: directoryMode,
         };
         if (forceSkipHostname) {
           payload.skip_hostname_check = true;
         }
         if (directoryMode === "existing" && basePath) {
           payload.log_dir = basePath;
         }
         const portInput = document.getElementById("serialPort");
         let currentPort = portInput ? portInput.value.trim() : "";
         if (!currentPort) {
           currentPort = localStorage.getItem("serialPort") || SERIAL_PRESETS.linux_usb;
         }
         payload.serial = { port: currentPort };
         const res = await fetch(`${API_ROOT}/api/execute`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: currentAbortController.signal,
         });
         if (!res.ok) {
            const txt = await res.text();
            throw new Error(txt || "Execution failed");
         }
         if (!res.body) {
            throw new Error("Execution failed: no response body.");
         }

         const reader = res.body.getReader();
         const dec = new TextDecoder();
         let buffer = "";
         let hadError = false;
         let hostnameMismatchMsg = null;

         while (true) {
           const { done, value } = await reader.read();
           if (done) break;
           buffer += dec.decode(value, { stream: true });
           const lines = buffer.split("\n");
           buffer = lines.pop() || "";
           for (const line of lines) {
             if (!line.trim()) continue;
             try {
               const obj = JSON.parse(line);
               if (obj.type === "error") {
                 hadError = true;
                 appendLogLine(`[ERROR] ${obj.msg || "Execution error"}`);
                 if (obj.error_code === "HOSTNAME_MISMATCH") {
                   hostnameMismatchMsg = obj.msg;
                 }
               } else if (obj.type === "progress") {
                 appendLogLine(`[${nowTimestamp()}] ${obj.msg}`);
               } else if (obj.type === "result") {
                 appendLogLine(`[RESULT] ${obj.msg || "Done"}`);
               } else if (obj.type === "done") {
                 appendLogLine(`[DONE] ${obj.msg || "Finished"}`);
               }
             } catch (_) {
               appendLogLine(line.trim());
             }
           }
         }

         if (abortBtn) abortBtn.style.display = "none";
         currentAbortController = null;

         if (!hadError) {
            badge.textContent = "DONE";
            badge.style.color = "var(--color-success, #28a745)";
            row.style.borderColor = "var(--color-success, #28a745)";
            row.style.backgroundColor = "rgba(40, 167, 69, 0.05)";
            
            appendLogLine(`[SUCCESS] Finished ${currentDevice.hostname}`);
            completedQueueHosts.add(currentDevice.hostname);
            runNextDeviceInQueue(); // Recurse next
         } else if (hostnameMismatchMsg) {
            // Special handling: offer to continue despite mismatch
            badge.textContent = "MISMATCH";
            badge.style.color = "#ff9800";
            row.style.borderColor = "#ff9800";
            row.style.backgroundColor = "rgba(255, 152, 0, 0.05)";

            const continueAnyway = confirm(
              `${hostnameMismatchMsg}\n\nDo you want to continue anyway?\nLogs will be saved under the selected device name "${currentDevice.hostname}".`
            );
            if (continueAnyway) {
              appendLogLine(`[INFO] User chose to continue despite hostname mismatch.`);
              runExecute(true); // Retry with skip flag
            } else {
              appendLogLine(`[INFO] User chose to stop due to hostname mismatch.`);
              badge.textContent = "SKIPPED";
              badge.style.color = "var(--color-muted)";
              document.getElementById("startSequenceBtn").disabled = false;
              isSequenceRunning = false;
            }
         } else {
            throw new Error("Execution failed during command run.");
         }
      } catch (e) {
         if (abortBtn) abortBtn.style.display = "none";
         currentAbortController = null;

         if (e.name === "AbortError") {
           // User clicked Stop Execution
           badge.textContent = "STOPPED";
           badge.style.color = "var(--color-danger)";
           appendLogLine(`[STOPPED] Execution aborted by user.`);
           document.getElementById("startSequenceBtn").disabled = false;
           isSequenceRunning = false;
           // Tell backend to abort too
           try { fetch(`${API_ROOT}/api/abort`, { method: "POST" }); } catch (_) {}
           return;
         }

         badge.textContent = "ERROR";
         badge.style.color = "var(--color-danger)";
         console.error(e);
         alert(`Execution failed on ${currentDevice.hostname}. Stopping queue.`);
         document.getElementById("startSequenceBtn").disabled = false;
         isSequenceRunning = false;
      }
  };

  // Wire abort button
  if (abortBtn) {
    abortBtn.onclick = () => {
      if (currentAbortController) {
        currentAbortController.abort();
      }
    };
  }

  runExecute();
}

// Transparently try to connect without blocking the UI heavily
async function attemptTransparentConnection() {
  const type = document.querySelector('input[name="connType"]:checked');
  if (!type || type.value !== "serial") return false; // SSH polling unsupported for now
  
  const portInput = document.getElementById("serialPort");
  const port = portInput ? portInput.value.trim() : "/dev/ttyUSB0";

  try {
     const controller = new AbortController();
     setTimeout(() => controller.abort(), 2500); // Fast timeout for serial polling
     const res = await fetch(`${API_ROOT}/api/connect`, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ connection: "serial", mode: "serial", serial: { port } }),
       signal: controller.signal
     });
     
     if (res.ok) {
       // It stream-responses, but just knowing the socket opened is enough success for the queue.
       return true;
     }
  } catch (e) {
    // Ignore aborts and fails while polling
  }
  return false;
}

// -----------------------------
// Commands page
// -----------------------------

function updateCommandSelectionState() {
  const badge = document.getElementById("commandCount");
  if (!badge) return;
  const checkboxes = document.querySelectorAll('input[name="command"]');
  const selected = Array.from(checkboxes).filter((cb) => cb.checked).length;
  const total = checkboxes.length;
  badge.textContent = total
    ? `${selected} selected of ${total}`
    : "0 selected";
}

function toggleSelectAllCommands() {
  const checkboxes = Array.from(document.querySelectorAll('input[name="command"]'));
  if (!checkboxes.length) return;
  const allSelected = checkboxes.every((cb) => cb.checked);
  checkboxes.forEach((cb) => {
    cb.checked = !allSelected;
  });
  updateCommandSelectionState();
}

const ROUTER_COMMANDS = [
  "show ip route",
  "show ip interface brief",
  "show run",
  "show interfaces",
  "show version"
];

const SWITCH_COMMANDS = [
  "show vlan",
  "show mac address-table",
  "show interface trunk",
  "show spanning-tree",
  "show ip interface brief",
  "show run",
  "show version"
];

function autoSelectCommands(deviceType) {
  const checkboxes = Array.from(document.querySelectorAll('input[name="command"]'));
  if (!checkboxes.length) return;

  // Determine which list to check against
  const targetCommands = deviceType === 'router' ? ROUTER_COMMANDS : SWITCH_COMMANDS;
  const opposingCommands = deviceType === 'router' ? SWITCH_COMMANDS : ROUTER_COMMANDS;

  // Find checkboxes that match the target commands
  const matchingCheckboxes = checkboxes.filter((cb) => {
    const cmdName = cb.value.toLowerCase().trim();
    return targetCommands.some(tc => cmdName.includes(tc.toLowerCase()));
  });

  // Find checkboxes that match the opposing commands but NOT the target commands
  const opposingCheckboxes = checkboxes.filter((cb) => {
    const cmdName = cb.value.toLowerCase().trim();
    const matchesOpposing = opposingCommands.some(tc => cmdName.includes(tc.toLowerCase()));
    const matchesTarget = targetCommands.some(tc => cmdName.includes(tc.toLowerCase()));
    return matchesOpposing && !matchesTarget;
  });

  if (matchingCheckboxes.length === 0) return;

  // Determine toggle state: if ALL matching ones are checked, uncheck them. Else, check them all.
  const allMatchingChecked = matchingCheckboxes.every(cb => cb.checked);

  matchingCheckboxes.forEach((cb) => {
    cb.checked = !allMatchingChecked;
  });

  // If we are checking the target commands, make sure to uncheck the opposing ones
  if (!allMatchingChecked) {
    opposingCheckboxes.forEach((cb) => {
      cb.checked = false;
    });
  }

  updateCommandSelectionState();
}

function renderCommandList(commands = []) {
  const container = document.getElementById("commandsList");
  if (!container) return;
  container.innerHTML = "";

  if (!commands.length) {
    container.innerHTML =
      "<p class=\"hint\">No commands defined. Add a command using the form on the left.</p>";
    updateCommandSelectionState();
    return;
  }

  commands.forEach((cmd, idx) => {
    const tile = document.createElement("div");
    tile.className = "command-tile";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "command";
    checkbox.value = cmd;
    checkbox.id = `cmd_${idx}`;

    const nameLabel = document.createElement("label");
    nameLabel.className = "command-name";
    nameLabel.setAttribute("for", `cmd_${idx}`);
    nameLabel.textContent = cmd;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.dataset.command = cmd;
    removeBtn.textContent = "Remove";
    removeBtn.className = "remove-btn";

    tile.appendChild(checkbox);
    tile.appendChild(nameLabel);
    tile.appendChild(removeBtn);
    container.appendChild(tile);
  });

  updateCommandSelectionState();
}

async function loadCommandsList() {
  const container = document.getElementById("commandsList");
  if (!container) return;
  container.innerHTML = "<p>Loading commands...</p>";
  try {
    const data = await fetchJson("/api/commands", { method: "GET" });
    renderCommandList(data.commands || []);
  } catch (err) {
    console.error(err);
    container.innerHTML = `<p class="error">Failed to load commands: ${err.message}</p>`;
    updateCommandSelectionState();
  }
}

async function addCommand() {
  const input = document.getElementById("newCommandInput");
  if (!input) return;
  const command = input.value.trim();
  if (!command) {
    alert("Enter a command to add.");
    return;
  }
  try {
    const data = await fetchJson("/api/commands", {
      method: "POST",
      body: JSON.stringify({ command }),
    });
    renderCommandList(data.commands || []);
    input.value = "";
  } catch (err) {
    console.error(err);
    alert(`Failed to add command: ${err.message}`);
  }
}

async function removeCommand(command) {
  if (!command) return;
  const confirmed = confirm(`Remove command "${command}"?`);
  if (!confirmed) return;
  try {
    const data = await fetchJson("/api/commands", {
      method: "DELETE",
      body: JSON.stringify({ command }),
    });
    renderCommandList(data.commands || []);
  } catch (err) {
    console.error(err);
    alert(`Failed to remove command: ${err.message}`);
  }
}
function getStoredCommands() {
  try {
    const raw = localStorage.getItem("selectedCommands");
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    return [];
  }
}

function setStoredCommandsFromDevice(hostname) {
  try {
    const devicesStr = localStorage.getItem("templateDevices");
    if (devicesStr) {
      const devicesMeta = JSON.parse(devicesStr);
      if (devicesMeta[hostname]) {
        localStorage.setItem("selectedCommands", JSON.stringify(devicesMeta[hostname]));
        renderSelectedCommandsInfo();
      }
    }
  } catch(err) {
    console.error(err);
  }
}

function setStoredCommands(commands) {
  localStorage.setItem("selectedCommands", JSON.stringify(commands));
}

function clearPendingExecutionFlag() {
  localStorage.removeItem("pendingExecution");
}

function setPendingExecutionFlag() {
  localStorage.setItem("pendingExecution", "true");
}

function isPendingExecution() {
  return localStorage.getItem("pendingExecution") === "true";
}

function clearExecutionLog() {
  const log = document.getElementById("log");
  if (log) log.innerText = "";
  setProgressValue(0);
}

function renderSelectedCommandsInfo() {
  const infoBox = document.getElementById("selectedCommandsInfo");
  if (!infoBox) return;
  const commands = getStoredCommands();
  if (!commands.length) {
    infoBox.classList.add("hidden");
    infoBox.innerHTML = "";
    return;
  }
  infoBox.classList.remove("hidden");
  infoBox.innerHTML = "";
  const heading = document.createElement("strong");
  heading.textContent = `${commands.length} command(s) selected:`;
  infoBox.appendChild(heading);
  const list = document.createElement("ul");
  commands.forEach((cmd) => {
    const li = document.createElement("li");
    li.textContent = cmd;
    list.appendChild(li);
  });
  infoBox.appendChild(list);
}

async function startExecution({ commands, initiatedFromConnection = false } = {}) {
  if (!ensureDirectoryConfigured()) return;

  const targetDeviceSelect = document.getElementById("targetDevice");
  const targetDevice = targetDeviceSelect ? targetDeviceSelect.value : null;

  if (document.getElementById("connectionPage") && !targetDevice) {
    alert("Please select a Target Device first.");
    return;
  }

  const commandsToRun = getStoredCommands();

  if (!commandsToRun.length) {
    alert("No commands mapped to this device. Check device setup.");
    return;
  }

  renderSelectedCommandsInfo();

  const log = document.getElementById("log");
  const progress = document.getElementById("progress");
  const hasStatusUI = log && progress;
  let connectionType = localStorage.getItem("connection");
  const selectedConnRadio = document.querySelector('input[name="connType"]:checked');
  if (selectedConnRadio) {
    connectionType = selectedConnRadio.value;
  }

  if (!hasStatusUI || !connectionType) {
    setPendingExecutionFlag();
    if (!connectionType && initiatedFromConnection) {
      alert("Configure the connection before running commands.");
    }
    if (!initiatedFromConnection) {
      goTo("connection.html");
    }
    return;
  }

  clearPendingExecutionFlag();
  clearExecutionLog();

  const directoryMode = localStorage.getItem("directoryMode") || "create";
  const basePath = localStorage.getItem("basePath");

  // Get extension
  const extSelect = document.getElementById("fileExtensionSelect");
  const fileExtension = extSelect ? extSelect.value : ".txt";

  const payload = {
    exam_name: localStorage.getItem("examName"),
    session_id: localStorage.getItem("sessionId"),
    student_id: localStorage.getItem("studentId"),
    connection: connectionType,
    mode: connectionType,
    commands: commandsToRun,
    log_mode: directoryMode,
    file_extension: fileExtension,
  };

  if (directoryMode === "existing") {
    if (!basePath) {
      alert("Existing directory path not found. Please re-select the directory.");
      goTo("index.html");
      return;
    }
    payload.log_dir = basePath;
  }

  if (connectionType === "ssh") {
    const sshHostInput = document.getElementById("sshHost");
    const sshUserInput = document.getElementById("sshUser");
    const sshPassInput = document.getElementById("sshPass");
    let sshHost =
      sshHostInput?.value.trim() || localStorage.getItem("sshHost") || "";
    let sshUser =
      sshUserInput?.value.trim() || localStorage.getItem("sshUser") || "";
    let sshPass =
      sshPassInput?.value || localStorage.getItem("sshPass") || "";
    let sshPort =
      document.getElementById("sshPort")?.value.trim() ||
      localStorage.getItem("sshPort") ||
      "22";
    payload.ssh = {
      host: sshHost,
      username: sshUser,
      password: sshPass,
      port: sshPort,
    };
    payload.host = sshHost;
    payload.username = sshUser;
    payload.password = sshPass;
    payload.port = sshPort;
  } else {
    // Attempt to get the port directly from the input field first
    const portInput = document.getElementById("serialPort");
    let currentPort = portInput ? portInput.value.trim() : "";
    if (!currentPort) {
      currentPort = localStorage.getItem("serialPort") || SERIAL_PRESETS.linux_rs232;
    }
    payload.serial = { port: currentPort };
  }

  const startBtn = document.getElementById("startExecutionBtn");
  if (startBtn) startBtn.disabled = true;
  setProgressValue(0);

  const total = commandsToRun.length;
  let finished = 0;

  try {
    const res = await fetch(`${API_ROOT}/api/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const txt = await res.text();
      log.innerText += `[ERROR ${res.status}] ${txt}\n`;
      return;
    }

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += dec.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const obj = JSON.parse(line);
          const pct = typeof obj.progress_pct === "number" ? obj.progress_pct : null;
          if (obj.type === "progress") {
            log.innerText += `[${new Date().toLocaleTimeString()}] ${obj.msg}\n`;
            if (pct !== null) {
              setProgressValue(pct);
            }
            if (obj.cmd_done) {
              finished++;
              if (pct === null) {
                setProgressValue((finished / total) * 100);
              }
            }
          } else if (obj.type === "result") {
            log.innerText += `[RESULT] ${obj.msg}\n`;
            if (obj.files) {
              obj.files.forEach((f) => (log.innerText += `  ${f}\n`));
            }
            if (pct !== null) {
              setProgressValue(pct);
            }
          } else if (obj.type === "done") {
            log.innerText += `\n[FINISHED] ${obj.msg}\n`;
            if (pct !== null) {
              setProgressValue(pct);
            } else {
              setProgressValue(100);
            }
            // User requested to open the directory on completion
            if (basePath && shell && pathModule) {
              // Try to open the specific routerorswitch subfolder first as requested
              const subDir = pathModule.join(basePath, "routerorswitch");
              shell.openPath(subDir).then((err) => {
                if (err) {
                  // Fallback to base student directory if subfolder doesn't exist
                  shell.openPath(basePath);
                }
              });
            }
          } else if (obj.type === "error") {
            log.innerText += `[ERROR] ${obj.msg}\n`;
            if (obj.trace) log.innerText += `${obj.trace}\n`;
          } else {
            log.innerText += JSON.stringify(obj) + "\n";
          }
        } catch (err) {
          log.innerText += line + "\n";
        }
        log.scrollTop = log.scrollHeight;
      }
    }
  } catch (err) {
    log.innerText += `[ERROR] ${err}\n`;
  } finally {
    if (startBtn) startBtn.disabled = false;
    updateCommandSelectionState();
  }
}
window.startExecution = startExecution;

function setupCommandsPage() {
  loadNavbar();
  if (!ensureDirectoryConfigured()) return;

  loadCommandsList();
  updateCommandSelectionState();

  document
    .getElementById("addCommandBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      addCommand();
    });

  document
    .getElementById("refreshCommandsBtn")
    ?.addEventListener("click", loadCommandsList);

  document
    .getElementById("commandsList")
    ?.addEventListener("click", (evt) => {
      const target = evt.target;
      if (
        target instanceof HTMLElement &&
        target.dataset &&
        target.dataset.command
      ) {
        removeCommand(target.dataset.command);
      }
    });

  document
    .getElementById("commandsList")
    ?.addEventListener("change", () => updateCommandSelectionState());

  document
    .getElementById("selectAllBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      toggleSelectAllCommands();
    });

  document
    .getElementById("autoSelectRouterBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      autoSelectCommands('router');
    });

  document
    .getElementById("autoSelectSwitchBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      autoSelectCommands('switch');
    });

  document
    .getElementById("startExecutionBtn")
    ?.addEventListener("click", (evt) => {
      evt.preventDefault();
      startExecution();
    });
}

// -----------------------------
// Init
// -----------------------------

document.addEventListener("DOMContentLoaded", () => {
  loadNavbar();
  if (document.getElementById("welcomePage")) setupWelcomePage();
  if (document.getElementById("directoryPage")) setupDirectoryPage();
  if (document.getElementById("connectionForm")) setupConnectionPage();
  if (document.getElementById("commandsPage")) setupCommandsPage();
  if (document.getElementById("sampleCollectPage")) setupSampleCollectPage();
});
