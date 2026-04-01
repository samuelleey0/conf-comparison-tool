const fs = require("fs");
const path = require("path");
const os = require("os");

const ENGINE_STUDENTS_ROOT = path.resolve(__dirname, "..", "comparsion_engine", "students");

function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch (_) {
    return {};
  }
}

function getSessionStudentNames(examName, sessionId) {
  const metaPath = path.join(os.homedir(), "Documents", examName, sessionId, "students.json");
  const data = readJsonFile(metaPath);
  return data && typeof data === "object" ? data : {};
}

function appendMelbourneLog(message) {
  const log = document.getElementById("melbourneLog");
  if (!log) return;
  log.textContent = `${message}`;
}

function listDirectoryNames(targetPath) {
  if (!fs.existsSync(targetPath)) return [];
  return fs
    .readdirSync(targetPath, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

function buildSessionIndex() {
  const examNames = listDirectoryNames(ENGINE_STUDENTS_ROOT);
  const index = {};
  examNames.forEach((examName) => {
    const examPath = path.join(ENGINE_STUDENTS_ROOT, examName);
    const sessions = listDirectoryNames(examPath);
    index[examName] = {};
    sessions.forEach((sessionId) => {
      const sessionPath = path.join(examPath, sessionId);
      const studentNames = getSessionStudentNames(examName, sessionId);
      const students = listDirectoryNames(sessionPath).map((studentId) => {
        const studentPath = path.join(sessionPath, studentId);
        return {
          student_id: studentId,
          student_name: studentNames[studentId] || "",
          path: studentPath,
        };
      });
      index[examName][sessionId] = {
        path: sessionPath,
        students,
      };
    });
  });
  return index;
}

function setSelectOptions(selectEl, options, placeholder) {
  if (!selectEl) return;
  selectEl.innerHTML = "";
  if (!options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = placeholder;
    selectEl.appendChild(option);
    selectEl.disabled = true;
    return;
  }
  selectEl.disabled = false;
  options.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    selectEl.appendChild(option);
  });
}

function setupSendMelbournePage() {
  const examSelect = document.getElementById("melbourneExamSelect");
  const sessionSelect = document.getElementById("melbourneSessionSelect");
  const studentList = document.getElementById("melbourneStudentList");
  const selectedLabel = document.getElementById("melbourneSelectedLabel");
  const sendBtn = document.getElementById("sendMelbourneBtn");
  const refreshBtn = document.getElementById("refreshMelbourneBtn");
  const selectAllBtn = document.getElementById("selectAllMelbourneBtn");
  const clearAllBtn = document.getElementById("clearAllMelbourneBtn");

  let sessionIndex = {};
  const preferredExam = localStorage.getItem("examName") || "";
  const preferredSession = localStorage.getItem("sessionId") || "";

  const getCurrentSession = () => {
    const examName = examSelect?.value || "";
    const sessionId = sessionSelect?.value || "";
    return sessionIndex?.[examName]?.[sessionId] || null;
  };

  const getSelectedStudents = () => {
    return Array.from(document.querySelectorAll('.melbourne-student-checkbox:checked')).map((input) => ({
      student_id: input.dataset.studentId,
      student_name: input.dataset.studentName || "",
      path: input.dataset.path,
    }));
  };

  const updateSelectionState = () => {
    const selected = getSelectedStudents();
    if (selectedLabel) {
      selectedLabel.textContent = `${selected.length} selected`;
    }
    if (sendBtn) {
      sendBtn.disabled = selected.length === 0;
    }
  };

  const renderStudents = () => {
    const session = getCurrentSession();
    if (!studentList) return;
    if (!session || !Array.isArray(session.students) || !session.students.length) {
      studentList.innerHTML = `<p class="hint">No student folders found for this session.</p>`;
      updateSelectionState();
      return;
    }

    studentList.innerHTML = "";

    session.students.forEach((student) => {
      const initials = (student.student_name || student.student_id || "?").substring(0, 2).toUpperCase();
      const displayName = student.student_name
        ? `${student.student_id} — ${student.student_name}`
        : student.student_id;

      const row = document.createElement("label");
      row.className = "melb-student-row";
      row.innerHTML = `
        <input
          type="checkbox"
          class="melbourne-student-checkbox"
          data-student-id="${student.student_id}"
          data-student-name="${student.student_name || ""}"
          data-path="${student.path}"
        />
        <div class="melb-student-avatar">${initials}</div>
        <div class="melb-student-info">
          <div class="melb-student-name">${displayName}</div>
          <div class="melb-student-path">${student.path}</div>
        </div>
      `;
      studentList.appendChild(row);
    });

    studentList.querySelectorAll(".melbourne-student-checkbox").forEach((input) => {
      input.addEventListener("change", () => {
        const row = input.closest(".melb-student-row");
        if (row) row.classList.toggle("checked", input.checked);
        updateSelectionState();
      });
    });
    updateSelectionState();
  };

  const refreshIndex = () => {
    sessionIndex = buildSessionIndex();
    const examNames = Object.keys(sessionIndex);
    const previousExam = examSelect?.value;
    const previousSession = sessionSelect?.value;

    setSelectOptions(examSelect, examNames, "No exams found");
    if (examSelect) {
      if (preferredExam && examNames.includes(preferredExam)) {
        examSelect.value = preferredExam;
      } else if (previousExam && examNames.includes(previousExam)) {
        examSelect.value = previousExam;
      }
    }

    const sessionNames = examSelect?.value ? Object.keys(sessionIndex[examSelect.value] || {}) : [];
    setSelectOptions(sessionSelect, sessionNames, "No sessions found");
    if (sessionSelect) {
      if (
        examSelect?.value === preferredExam &&
        preferredSession &&
        sessionNames.includes(preferredSession)
      ) {
        sessionSelect.value = preferredSession;
      } else if (previousSession && sessionNames.includes(previousSession)) {
        sessionSelect.value = previousSession;
      }
    }

    renderStudents();
    if (!examNames.length) {
      appendMelbourneLog(`No mirrored student sessions found under ${ENGINE_STUDENTS_ROOT}`);
    } else if (
      preferredExam &&
      preferredSession &&
      examSelect?.value === preferredExam &&
      sessionSelect?.value === preferredSession
    ) {
      appendMelbourneLog(`Auto-selected ${preferredExam}/${preferredSession} from the current working session.`);
    } else {
      appendMelbourneLog(`Loaded ${examNames.length} exam(s) from ${ENGINE_STUDENTS_ROOT}`);
    }
  };

  examSelect?.addEventListener("change", () => {
    const sessionNames = Object.keys(sessionIndex[examSelect.value] || {});
    setSelectOptions(sessionSelect, sessionNames, "No sessions found");
    renderStudents();
  });

  sessionSelect?.addEventListener("change", renderStudents);

  refreshBtn?.addEventListener("click", refreshIndex);

  selectAllBtn?.addEventListener("click", () => {
    document.querySelectorAll(".melbourne-student-checkbox").forEach((input) => {
      input.checked = true;
    });
    updateSelectionState();
  });

  clearAllBtn?.addEventListener("click", () => {
    document.querySelectorAll(".melbourne-student-checkbox").forEach((input) => {
      input.checked = false;
    });
    updateSelectionState();
  });

  sendBtn?.addEventListener("click", async () => {
    const session = getCurrentSession();
    const selected = getSelectedStudents();
    if (!session || !selected.length) {
      updateSelectionState();
      return;
    }

    try {
      await fetchJson("/api/melbourne/send", {
        method: "POST",
        body: JSON.stringify({
          exam_name: examSelect.value,
          session_id: sessionSelect.value,
          session_path: session.path,
          students: selected,
        }),
      });
      appendMelbourneLog(`Sent ${selected.length} student folder(s) to Melbourne.`);
    } catch (err) {
      appendMelbourneLog(
        `Send request prepared for ${selected.length} student(s), but backend send endpoint is not ready.\n\n${err.message}`
      );
      alert(err.message || "Send to Melbourne is not implemented yet.");
    }
  });

  refreshIndex();
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("sendMelbournePage")) {
    setupSendMelbournePage();
  }
});
