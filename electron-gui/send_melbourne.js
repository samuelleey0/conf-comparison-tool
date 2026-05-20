const fs = require("fs");
const path = require("path");
const os = require("os");

const ENGINE_STUDENTS_ROOT = path.resolve(__dirname, "..", "comparison_engine", "students");
const DEVICE_TYPES = ["router", "switch", "asa"];

// Export Melbourne packages one mirrored session into Melbourne's expected zip
// layout. Temporary config files are generated during export but intentionally
// excluded from the final archive.
function readJsonFile(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch (_) {
    return {};
  }
}

function listDirectoryNames(targetPath) {
  if (!fs.existsSync(targetPath)) return [];
  return fs
    .readdirSync(targetPath, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !entry.name.startsWith("."))
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

function getSessionStudentNames(classroom, tutorName, timeSlot) {
  const metaPath = path.join(os.homedir(), "Documents", classroom, tutorName, timeSlot, "students.json");
  const data = readJsonFile(metaPath);
  return data && typeof data === "object" ? data : {};
}

function makeSessionKey(tutorName, timeSlot) {
  return `${tutorName} / ${timeSlot}`;
}

function parseCsv(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function deriveExamFields(examName) {
  const words = String(examName || "").trim().split(/\s+/).filter(Boolean);
  const unitcode = words[0] || "";
  const shortname = words[1] ? words[1].toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") : "";
  return { unitcode, shortname: shortname || "exam" };
}

function appendMelbourneLog(message) {
  const log = document.getElementById("melbourneLog");
  if (!log) return;
  log.textContent = message;
}

function buildSessionIndex() {
  // Melbourne export reads from the mirrored comparison_engine/students tree,
  // not directly from the user's Documents folder.
  const index = {};
  listDirectoryNames(ENGINE_STUDENTS_ROOT).forEach((classroom) => {
    const classroomPath = path.join(ENGINE_STUDENTS_ROOT, classroom);
    index[classroom] = {};
    listDirectoryNames(classroomPath).forEach((tutorName) => {
      const tutorPath = path.join(classroomPath, tutorName);
      listDirectoryNames(tutorPath).forEach((timeSlot) => {
        const sessionPath = path.join(tutorPath, timeSlot);
        const studentNames = getSessionStudentNames(classroom, tutorName, timeSlot);
        const students = listDirectoryNames(sessionPath).map((studentId) => ({
          student_id: studentId,
          student_name: studentNames[studentId] || "",
          path: path.join(sessionPath, studentId),
        }));
        index[classroom][makeSessionKey(tutorName, timeSlot)] = {
          classroom,
          tutor_name: tutorName,
          time_slot: timeSlot,
          label: makeSessionKey(tutorName, timeSlot),
          path: sessionPath,
          students,
        };
      });
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

function inferDeviceType(sessionPath, deviceName) {
  for (const studentId of listDirectoryNames(sessionPath)) {
    const devicePath = path.join(sessionPath, studentId, deviceName);
    if (!fs.existsSync(devicePath)) continue;
    for (const filename of fs.readdirSync(devicePath)) {
      const lower = filename.toLowerCase();
      if (lower.includes("vlan") || lower.includes("trunk") || lower.includes("spanning")) return "switch";
      if (lower.includes("route")) return "router";
    }
  }
  return "router";
}

function discoverDevices(sessionPath) {
  const devices = new Set();
  listDirectoryNames(sessionPath).forEach((studentId) => {
    listDirectoryNames(path.join(sessionPath, studentId))
      .filter((device) => device !== "results")
      .forEach((device) => devices.add(device));
  });
  return Array.from(devices).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

function setupExportMelbournePage() {
  const classroomSelect = document.getElementById("melbourneClassroomSelect");
  const sessionSelect = document.getElementById("melbourneSessionSelect");
  const sessionPathEl = document.getElementById("melbourneSessionPath");
  const examNameInput = document.getElementById("melbourneExamName");
  const semesterInput = document.getElementById("melbourneSemester");
  const unitcodeInput = document.getElementById("melbourneUnitcode");
  const shortnameInput = document.getElementById("melbourneShortname");
  const timeoutInput = document.getElementById("melbourneTimeout");
  const schemeValuesInput = document.getElementById("melbourneSchemeValues");
  const devicesBody = document.getElementById("melbourneDevicesBody");
  const studentsBody = document.getElementById("melbourneStudentsBody");
  const applySchemeSelect = document.getElementById("melbourneBulkScheme");
  const applySchemeBtn = document.getElementById("melbourneApplySchemeBtn");
  const refreshBtn = document.getElementById("refreshMelbourneBtn");
  const exportBtn = document.getElementById("exportMelbourneBtn");
  const selectedLabel = document.getElementById("melbourneSelectedLabel");
  const schemeValidationEl = document.getElementById("melbourneSchemeValidation");
  const maxMarksInput = document.getElementById("melbourneMaxMarks");
  const minorPenaltiesInput = document.getElementById("melbourneMinorPenalties");
  const rubricNameInput = document.getElementById("melbourneRubricName");
  const summaryEl = document.getElementById("melbourneSummary");

  let sessionIndex = {};
  const preferredClassroom = localStorage.getItem("classroom") || localStorage.getItem("examName") || "";
  const preferredTutor = localStorage.getItem("tutorName") || localStorage.getItem("sessionId") || "";
  const preferredTime = localStorage.getItem("timeSlot") || "";
  const preferredSession = preferredTutor && preferredTime ? makeSessionKey(preferredTutor, preferredTime) : "";

  const currentSession = () => {
    const classroom = classroomSelect?.value || "";
    const sessionKey = sessionSelect?.value || "";
    return sessionIndex?.[classroom]?.[sessionKey] || null;
  };

  const getAllowedSchemes = () => parseCsv(schemeValuesInput?.value || "A,B,C,D");

  const syncDerivedFields = () => {
    const { unitcode, shortname } = deriveExamFields(examNameInput?.value);
    if (unitcodeInput && !unitcodeInput.dataset.touched) unitcodeInput.value = unitcode;
    if (shortnameInput && !shortnameInput.dataset.touched) shortnameInput.value = shortname;
  };

  const renderDeviceRows = () => {
    const session = currentSession();
    if (!devicesBody) return;
    devicesBody.innerHTML = "";
    if (!session) {
      devicesBody.innerHTML = `<tr><td colspan="3" class="muted-cell">No session selected.</td></tr>`;
      return;
    }
    const devices = discoverDevices(session.path);
    if (!devices.length) {
      devicesBody.innerHTML = `<tr><td colspan="3" class="muted-cell">No device folders found.</td></tr>`;
      return;
    }
    devices.forEach((device) => {
      const inferredType = inferDeviceType(session.path, device);
      const typeOptions = DEVICE_TYPES.map(
        (type) => `<option value="${type}" ${type === inferredType ? "selected" : ""}>${type}</option>`
      ).join("");
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><span class="mono">${device}</span></td>
        <td><select class="melbourne-device-type" data-device="${device}">${typeOptions}</select></td>
        <td><input class="melbourne-device-name" data-device="${device}" type="text" value="${device}" /></td>
      `;
      devicesBody.appendChild(row);
    });
  };

  const renderSchemeOptions = () => {
    const schemes = getAllowedSchemes();
    const options = schemes.map((scheme) => `<option value="${scheme}">${scheme}</option>`).join("");
    if (applySchemeSelect) {
      applySchemeSelect.innerHTML = `<option value="">Bulk scheme</option>${options}`;
    }
    document.querySelectorAll(".melbourne-student-scheme").forEach((select) => {
      const current = select.value;
      select.innerHTML = `<option value="">Unassigned</option>${options}`;
      if (schemes.includes(current)) select.value = current;
    });
  };

  const validateSchemeAssignment = () => {
    const rows = Array.from(document.querySelectorAll(".melbourne-student-scheme"));
    const allowed = getAllowedSchemes();
    const missing = [];
    const invalid = [];

    rows.forEach((select) => {
      const studentId = select.dataset.studentId || "";
      const value = select.value;
      const isMissing = !value;
      const isInvalid = Boolean(value) && !allowed.includes(value);
      select.classList.toggle("is-invalid", isMissing || isInvalid);
      select.closest("tr")?.classList.toggle("has-validation-error", isMissing || isInvalid);
      if (isMissing) missing.push(studentId);
      if (isInvalid) invalid.push(studentId);
    });

    if (schemeValidationEl) {
      const messages = [];
      if (!allowed.length) messages.push("Enter at least one allowed scheme value before assigning students.");
      if (missing.length) messages.push(`Missing scheme: ${missing.join(", ")}`);
      if (invalid.length) messages.push(`Invalid scheme value: ${invalid.join(", ")}`);
      schemeValidationEl.hidden = !messages.length;
      schemeValidationEl.textContent = messages.join("\n");
    }

    return { rows, allowed, missing, invalid, valid: Boolean(rows.length && allowed.length && !missing.length && !invalid.length) };
  };

  const updateStudentState = () => {
    const { rows, missing, invalid, valid } = validateSchemeAssignment();
    const assigned = rows.filter((select) => select.value).length;
    if (selectedLabel) selectedLabel.textContent = `${assigned}/${rows.length} assigned`;
    if (exportBtn) exportBtn.disabled = !valid;
    return { missing, invalid, valid };
  };

  const renderStudentRows = () => {
    const session = currentSession();
    if (!studentsBody) return;
    studentsBody.innerHTML = "";
    if (!session || !session.students.length) {
      studentsBody.innerHTML = `<tr><td colspan="3" class="muted-cell">No student folders found.</td></tr>`;
      updateStudentState();
      return;
    }
    const schemes = getAllowedSchemes();
    const options = schemes.map((scheme) => `<option value="${scheme}">${scheme}</option>`).join("");
    session.students.forEach((student) => {
      const displayName = student.student_name || "";
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><strong>${student.student_id}</strong></td>
        <td>${displayName || "<span class=\"muted-cell\">No name found</span>"}</td>
        <td>
          <select class="melbourne-student-scheme" data-student-id="${student.student_id}">
            <option value="">Unassigned</option>
            ${options}
          </select>
        </td>
      `;
      studentsBody.appendChild(row);
    });
    studentsBody.querySelectorAll(".melbourne-student-scheme").forEach((select) => {
      select.addEventListener("change", updateStudentState);
    });
    updateStudentState();
  };

  const renderCurrentSession = () => {
    const session = currentSession();
    if (sessionPathEl) sessionPathEl.textContent = session ? session.path : "No mirrored session selected.";
    if (examNameInput && session && !examNameInput.value) {
      examNameInput.value = session.classroom;
      syncDerivedFields();
    }
    renderDeviceRows();
    renderStudentRows();
    renderSchemeOptions();
  };

  const refreshIndex = () => {
    sessionIndex = buildSessionIndex();
    const classroomNames = Object.keys(sessionIndex);
    const previousClassroom = classroomSelect?.value;
    const previousSession = sessionSelect?.value;
    setSelectOptions(classroomSelect, classroomNames, "No classrooms found");
    if (classroomSelect) {
      if (preferredClassroom && classroomNames.includes(preferredClassroom)) classroomSelect.value = preferredClassroom;
      else if (previousClassroom && classroomNames.includes(previousClassroom)) classroomSelect.value = previousClassroom;
    }
    const sessions = classroomSelect?.value ? Object.keys(sessionIndex[classroomSelect.value] || {}) : [];
    setSelectOptions(sessionSelect, sessions, "No sessions found");
    if (sessionSelect) {
      if (classroomSelect?.value === preferredClassroom && preferredSession && sessions.includes(preferredSession)) {
        sessionSelect.value = preferredSession;
      } else if (previousSession && sessions.includes(previousSession)) {
        sessionSelect.value = previousSession;
      }
    }
    renderCurrentSession();
    appendMelbourneLog(
      classroomNames.length
        ? `Loaded mirrored sessions from ${ENGINE_STUDENTS_ROOT}`
        : `No mirrored student sessions found under ${ENGINE_STUDENTS_ROOT}`
    );
  };

  const collectPayload = () => {
    const session = currentSession();
    if (!session) throw new Error("Select a session before exporting.");
    const schemes = getAllowedSchemes();
    if (!schemes.length) throw new Error("Enter at least one allowed scheme value.");
    const examName = String(examNameInput?.value || "").trim();
    if (!examName) throw new Error("Exam name is required.");
    const studentSchemes = {};
    document.querySelectorAll(".melbourne-student-scheme").forEach((select) => {
      studentSchemes[select.dataset.studentId] = select.value;
    });
    const schemeState = updateStudentState();
    if (schemeState.missing.length) {
      throw new Error(`Assign a scheme for every student before exporting. Missing: ${schemeState.missing.join(", ")}`);
    }
    if (schemeState.invalid.length) {
      throw new Error(`Some students have a scheme that is not in the allowed values: ${schemeState.invalid.join(", ")}`);
    }
    const devices = Array.from(document.querySelectorAll(".melbourne-device-type")).map((select) => {
      const device = select.dataset.device;
      const nameInput = Array.from(document.querySelectorAll(".melbourne-device-name"))
        .find((input) => input.dataset.device === device);
      return {
        folder: device,
        type: select.value,
        exam_name: nameInput?.value || device,
      };
    });
    return {
      classroom: session.classroom,
      tutor_name: session.tutor_name,
      time_slot: session.time_slot,
      session_path: session.path,
      exam_name: examName,
      semester: semesterInput?.value || "2026 S1",
      unitcode: unitcodeInput?.value || deriveExamFields(examName).unitcode,
      shortname: shortnameInput?.value || deriveExamFields(examName).shortname,
      timeout: timeoutInput?.value || 180,
      scheme_values: schemes.join(","),
      devices,
      student_schemes: studentSchemes,
      maximum_marks: maxMarksInput?.value || 100,
      minor_penalties: minorPenaltiesInput?.value || "10,20,30,40",
      rubric_name: rubricNameInput?.value || "Major_Minor",
    };
  };

  classroomSelect?.addEventListener("change", () => {
    const sessions = Object.keys(sessionIndex[classroomSelect.value] || {});
    setSelectOptions(sessionSelect, sessions, "No sessions found");
    renderCurrentSession();
  });
  sessionSelect?.addEventListener("change", renderCurrentSession);
  refreshBtn?.addEventListener("click", refreshIndex);
  examNameInput?.addEventListener("input", syncDerivedFields);
  unitcodeInput?.addEventListener("input", () => { unitcodeInput.dataset.touched = "1"; });
  shortnameInput?.addEventListener("input", () => { shortnameInput.dataset.touched = "1"; });
  schemeValuesInput?.addEventListener("input", () => {
    renderSchemeOptions();
    updateStudentState();
  });
  applySchemeBtn?.addEventListener("click", () => {
    const value = applySchemeSelect?.value || "";
    if (!value) return;
    document.querySelectorAll(".melbourne-student-scheme").forEach((select) => {
      select.value = value;
    });
    updateStudentState();
  });
  exportBtn?.addEventListener("click", async () => {
    try {
      exportBtn.disabled = true;
      exportBtn.textContent = "Exporting...";
      appendMelbourneLog("Preparing Melbourne export...");
      const data = await fetchJson("/api/melbourne/send", {
        method: "POST",
        body: JSON.stringify(collectPayload()),
      });
      const missing = data.missing_schemes || [];
      appendMelbourneLog(
        `Export complete.\nFinalised students: ${data.finalised_count}/${data.student_count}\nZip: ${data.zip_path}\nFolder: ${data.export_folder}`
      );
      if (summaryEl) {
        summaryEl.hidden = false;
        summaryEl.innerHTML = `
          <strong>Export complete</strong>
          <span>${data.finalised_count}/${data.student_count} student folders finalised.</span>
          <span>Zip saved to <span class="mono">${data.zip_path}</span></span>
          <span>Working folder saved to <span class="mono">${data.export_folder}</span></span>
          <span>${missing.length ? `Missing schemes: ${missing.join(", ")}` : "All students have assigned schemes."}</span>
        `;
      }
    } catch (err) {
      appendMelbourneLog(err.message || "Melbourne export failed.");
      alert(err.message || "Melbourne export failed.");
    } finally {
      exportBtn.textContent = "Export";
      updateStudentState();
    }
  });

  refreshIndex();
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("exportMelbournePage")) {
    if (typeof loadNavbar === "function") loadNavbar();
    setupExportMelbournePage();
  }
});
