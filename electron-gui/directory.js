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
const WINDOWS_DRIVES_ROOT = "__WINDOWS_DRIVES__";

function displayPickerPath(pathValue) {
  return pathValue === WINDOWS_DRIVES_ROOT ? "This PC" : (pathValue || "");
}

function inferSessionFromPath(sessionPath) {
  if (!sessionPath || !pathModule) {
    return {
      classroom: "Selected Session",
      tutor_name: "Selected Folder",
      time_slot: "",
    };
  }
  const parts = String(sessionPath).split(pathModule.sep).filter(Boolean);
  return {
    classroom: parts.at(-3) || "Selected Session",
    tutor_name: parts.at(-2) || "Selected Folder",
    time_slot: parts.at(-1) || "",
  };
}

async function fetchPickerJson(url) {
  let res;
  try {
    res = await fetch(url);
  } catch (err) {
    throw new Error(
      "Cannot reach the local backend. Make sure the app server is running on 127.0.0.1:5050."
    );
  }

  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    data = {};
  }

  if (!res.ok) {
    throw new Error(data.message || `Request failed (${res.status})`);
  }
  return data;
}

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
    if (currentPickerParentPath) {
      loadDirectory(currentPickerParentPath);
    } else if (currentPickerPath && pathModule) {
      const parent = pathModule.dirname(currentPickerPath);
      if (parent && parent !== currentPickerPath) {
        loadDirectory(parent);
      } else {
        loadDirectory(WINDOWS_DRIVES_ROOT);
      }
    }
  };

  const pathInput = document.getElementById("pickerCurrentPath");
  if (pathInput) {
    pathInput.onkeydown = (e) => {
      if (e.key === "Enter") {
        const requestedPath = pathInput.value === "This PC"
          ? WINDOWS_DRIVES_ROOT
          : pathInput.value;
        loadDirectory(requestedPath);
      }
    };
  }

  confirmBtn.onclick = () => {
    if (pendingSelectedFolder && (pendingSelectedFolder.type === 'session' || pendingSelectedFolder.type === 'browser-session')) {
      const { classroom, tutor_name, time_slot, students } = pendingSelectedFolder;

      const label = document.getElementById("selectedDirectoryLabel");
      if (label) {
        label.textContent = `Session: ${classroom} / ${tutor_name} / ${time_slot}`;
        label.classList.add("has-value");
      }

      const infoBox = document.getElementById("existingInfoBox");
      if (infoBox) infoBox.classList.add("hidden");

      let sessionPath = "";
      if (pendingSelectedFolder.path) {
        sessionPath = pendingSelectedFolder.path;
      } else if (students && students.length > 0 && typeof pathModule !== "undefined") {
         sessionPath = pathModule.dirname(students[0].path);
      }
      if (!sessionPath && currentPickerPath && typeof pathModule !== "undefined") {
         sessionPath = currentPickerPath;
      }
      if (!sessionPath && typeof pathModule !== "undefined") {
        try {
          const os = require("os");
          sessionPath = pathModule.join(os.homedir(), "Documents", classroom, tutor_name, time_slot);
        } catch (_) {
          sessionPath = "";
        }
      }

      setDirectoryInfo({
        classroom,
        tutor_name,
        time_slot,
        student_id: "",
        path: sessionPath,
        mode: "existing",
        display: `${classroom}/${tutor_name}/${time_slot}`
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
let currentPickerParentPath = null;

async function loadDirectory(pathVal = null) {
  const container = document.getElementById("folderTreeContainer");
  const pathLabel = document.getElementById("pickerCurrentPath");
  if (!container) return;

  container.innerHTML = `<p class="loading-text">Loading...</p>`;

  // Optimistic update for better UX
  if (pathLabel && pathVal) {
    pathLabel.value = displayPickerPath(pathVal);
  }

  try {
    const url = pathVal
      ? `${API_ROOT}/api/directories?path=${encodeURIComponent(pathVal)}`
      : `${API_ROOT}/api/directories`;

    const data = await fetchPickerJson(url);

    if (data.status === "ok") {
      currentPickerPath = data.current_path;
      currentPickerParentPath = data.parent_path || null;
      if (pathLabel) {
        pathLabel.value = displayPickerPath(currentPickerPath);
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
    const data = await fetchPickerJson(`${API_ROOT}/api/subfolders?path=${encodeURIComponent(pathVal)}`);

    if (data.status === "ok") {
      currentPickerPath = data.current_path || pathVal;
      currentPickerParentPath = data.parent_path || null;
      const pathLabel = document.getElementById("pickerCurrentPath");
      if (pathLabel) {
        pathLabel.value = displayPickerPath(currentPickerPath);
      }
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
    const classroom = d.classroom || d.exam_name || "";
    const tutor = d.tutor_name || d.session_id || "";
    const time = d.time_slot || "";
    if (!classroom || !tutor || !time) return;
    if (!hierarchy[classroom]) hierarchy[classroom] = {};
    if (!hierarchy[classroom][tutor]) hierarchy[classroom][tutor] = {};
    if (!hierarchy[classroom][tutor][time]) hierarchy[classroom][tutor][time] = [];
    hierarchy[classroom][tutor][time].push(d);
  });
  return hierarchy;
}

function renderSubfolders(container, subfolders) {
  container.innerHTML = "";
  const currentSessionPath = currentPickerPath;
  if (currentSessionPath && currentSessionPath !== WINDOWS_DRIVES_ROOT) {
    const sessionInfo = inferSessionFromPath(currentSessionPath);
    pendingSelectedFolder = {
      type: "browser-session",
      path: currentSessionPath,
      classroom: sessionInfo.classroom,
      tutor_name: sessionInfo.tutor_name,
      time_slot: sessionInfo.time_slot,
      students: Array.isArray(subfolders)
        ? subfolders.map((folder) => ({
            student_id: folder.name,
            student_name: "",
            path: folder.path,
          }))
        : [],
    };
    const confirmBtn = document.getElementById("confirmFolderPickerBtn");
    if (confirmBtn) confirmBtn.disabled = false;
  }
  if (!subfolders || subfolders.length === 0) {
    container.innerHTML = `<p class="empty-text">Empty folder. Click Select Session to use this folder anyway.</p>`;
    return;
  }
  const ul = document.createElement("ul");
  ul.className = "tree-root";

  subfolders.forEach(f => {
    const li = document.createElement("li");
    const div = document.createElement("div");
    div.className = "tree-item";
    div.textContent = `${f.is_drive ? "💽" : "📁"} ${f.name}`;
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

  // Iterate classrooms
  Object.keys(hierarchy).sort().forEach(classroom => {
    const examLi = document.createElement("li");
    const examLabel = document.createElement("div");
    examLabel.className = "tree-item exam-item";
    examLabel.textContent = `📂 ${classroom}`;
    examLi.appendChild(examLabel);

    const tutorUl = document.createElement("ul");
    tutorUl.className = "tree-children hidden";

    Object.keys(hierarchy[classroom]).sort().forEach(tutor_name => {
      const tutorLi = document.createElement("li");
      const tutorLabel = document.createElement("div");
      tutorLabel.className = "tree-item tutor-item";
      tutorLabel.textContent = `📁 ${tutor_name}`;
      tutorLi.appendChild(tutorLabel);

      const timeUl = document.createElement("ul");
      timeUl.className = "tree-children hidden";

      Object.keys(hierarchy[classroom][tutor_name]).sort().forEach(time_slot => {
        const timeLi = document.createElement("li");
        const timeLabel = document.createElement("div");
        timeLabel.className = "tree-item session-item";
        timeLabel.textContent = `🕒 ${time_slot}`;
        timeLi.appendChild(timeLabel);

        timeLabel.onclick = (e) => {
          e.stopPropagation();
          document.querySelectorAll(".session-item.selected").forEach(el => el.classList.remove("selected"));
          timeLabel.classList.add("selected");
          const students = hierarchy[classroom][tutor_name][time_slot] || [];
          pendingSelectedFolder = { type: 'session', classroom, tutor_name, time_slot, students };
          document.getElementById("confirmFolderPickerBtn").disabled = false;
        };

        timeLabel.ondblclick = () => {
          const students = hierarchy[classroom][tutor_name][time_slot] || [];
          pendingSelectedFolder = { type: 'session', classroom, tutor_name, time_slot, students };
          document.getElementById("confirmFolderPickerBtn").click();
        };

        timeUl.appendChild(timeLi);
      });

      tutorLabel.onclick = (e) => {
        e.stopPropagation();
        timeUl.classList.toggle("hidden");
      };

      tutorLi.appendChild(timeUl);
      tutorUl.appendChild(tutorLi);
    });

    examLabel.onclick = () => {
      const allSessionUls = container.querySelectorAll(".tree-root > li > ul");
      allSessionUls.forEach(ul => {
        if (ul !== tutorUl) {
          ul.classList.add("hidden");
        }
      });
      tutorUl.classList.toggle("hidden");
    };

    examLi.appendChild(tutorUl);
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
        border-radius: 8px;
        padding: 12px 14px;
        text-align: left;
        cursor: pointer;
        background: var(--color-surface);
        display: flex;
        flex-direction: row;
        align-items: center;
        gap: 12px;
        box-shadow: 0 2px 6px rgba(15, 23, 46, 0.05);
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
        if (el.classList.contains("executed-student")) {
           el.style.borderColor = "var(--color-primary)";
           el.style.backgroundColor = "rgba(31, 59, 115, 0.1)";
        } else if (el.dataset.completed === "true") {
           el.style.borderColor = "var(--color-success, #28a745)";
           el.style.backgroundColor = "rgba(40, 167, 69, 0.05)";
        } else if (el.dataset.completed === "partial") {
           el.style.borderColor = "#ff9800";
           el.style.backgroundColor = "rgba(255, 152, 0, 0.08)";
        } else {
           el.style.borderColor = "var(--color-border)";
           el.style.backgroundColor = "var(--color-bg-card)";
        }
        el.style.color = "inherit";
      });

      studentCard.classList.add("selected-student");
      studentCard.style.borderColor = "var(--color-primary)";
      studentCard.style.borderWidth = "2px";
      studentCard.style.backgroundColor = "rgba(31, 59, 115, 0.08)";
      studentCard.style.color = "var(--color-heading)";

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
  const classroom = document.getElementById("createClassroom").value.trim();
  const tutorName = document.getElementById("createTutorName").value.trim();
  const timeSlot = document.getElementById("createTimeSlot").value.trim();
  const student = document.getElementById("createStudentId").value.trim();
  const studentName = (document.getElementById("createStudentName")?.value || "").trim();
  if (!classroom || !tutorName || !timeSlot || !student) {
    alert("Please complete all fields for the new directory.");
    return;
  }

  try {
    const data = await fetchJson("/api/create_directory", {
      method: "POST",
      body: JSON.stringify({
        classroom,
        tutor_name: tutorName,
        time_slot: timeSlot,
        studentId: student,
        studentName: studentName,
      }),
    });
    setDirectoryInfo({
      classroom: data.classroom,
      tutor_name: data.tutor_name,
      time_slot: data.time_slot,
      student_id: data.student_id,
      path: data.path,
      mode: "create",
      display: `${data.classroom}/${data.tutor_name}/${data.time_slot}/${data.student_id}`,
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
      classroom: data.classroom,
      tutor_name: data.tutor_name,
      time_slot: data.time_slot,
      student_id: data.student_id,
      path: data.path,
      mode: "existing",
      display: `${data.classroom}/${data.tutor_name}/${data.time_slot}/${data.student_id}`,
    });
    if (pathModule) {
      localStorage.setItem("sessionPath", pathModule.dirname(data.path));
    }
    setSelectedExistingDirectory(
      data.path,
      `${data.classroom}/${data.tutor_name}/${data.time_slot}/${data.student_id}`
    );
    goTo("connection.html");
  } catch (err) {
    console.error(err);
    alert(`Failed to use directory: ${err.message}`);
  }
}

async function handleBulkCreate(event) {
  event.preventDefault();
  const classroom = document.getElementById("bulkClassroom").value.trim();
  const tutorName = document.getElementById("bulkTutorName").value.trim();
  const timeSlot = document.getElementById("bulkTimeSlot").value.trim();
  const fileInput = document.getElementById("bulkFile");
  const resultsBox = document.getElementById("bulkResults");
  const hasTitleRow = document.getElementById("bulkHasTitle")?.checked || false;
  const hasHeader = document.getElementById("bulkHasHeader")?.checked || false;
  const hasNumberColumn = document.getElementById("bulkHasNumber")?.checked || false;

  if (!fileInput.files.length) {
    alert("Please select a file.");
    return;
  }
  if (!classroom || !tutorName || !timeSlot) {
    alert("Please provide classroom, tutor name, and time for bulk creation.");
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
      body: JSON.stringify({ classroom, tutor_name: tutorName, time_slot: timeSlot, students }),
    });

    const created = data.created || [];
    if (!created.length) {
      resultsBox.classList.remove("hidden");
      resultsBox.innerHTML =
        "<strong>No directories were created. Check the file contents.</strong>";
      return;
    }

    resultsBox.classList.remove("hidden");
    resultsBox.innerHTML = `<strong>Created ${created.length} student director${created.length === 1 ? "y" : "ies"}.</strong>`;

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
      const classroom = localStorage.getItem("classroom") || localStorage.getItem("examName");
      const tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId");
      const timeSlot = localStorage.getItem("timeSlot");
      if (!classroom || !tutorName || !timeSlot) {
        alert("Please select a session first.");
        return;
      }

      openAddStudentModal(async (studentId, studentName) => {
        const classroom = localStorage.getItem("classroom") || localStorage.getItem("examName");
        const tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId");
        const timeSlot = localStorage.getItem("timeSlot");
        if (!classroom || !tutorName || !timeSlot) {
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
            sessionPath = pathModule.join(os.homedir(), "Documents", classroom, tutorName, timeSlot);
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
            const students = hierarchy[classroom]?.[tutorName]?.[timeSlot];
            if (students) {
              renderMainStudentGrid(students);
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
               const classroom = localStorage.getItem("classroom") || localStorage.getItem("examName");
               const tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId");
               const timeSlot = localStorage.getItem("timeSlot");

               const students = hierarchy[classroom]?.[tutorName]?.[timeSlot];
               if (students) {
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



document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("directoryPage")) setupDirectoryPage();
});
