// electron-gui/renderer.js

const API_ROOT = "http://127.0.0.1:5050";
const SIDEBAR_COLLAPSE_KEY = "sidebarCollapsed";
const SERIAL_PRESETS = {
  linux_usb: "/dev/ttyUSB0",
  linux_rs232: "/dev/ttyS0",
  windows: "COM3",
  mac: "/dev/cu.usbserial-10",
};

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
let statusModalActionBtn = null;
let statusModalHideTimeout = null;

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
      updateGlobalTemplateBadge();
    })
    .catch((err) => console.error("Failed to load navbar:", err));
}

function updateGlobalTemplateBadge() {
  const badge = document.getElementById("globalTemplateBadge");
  const nameEl = document.getElementById("globalTemplateName");
  if (!badge || !nameEl) return;
  const templateName = localStorage.getItem("templateName");
  if (templateName) {
    nameEl.textContent = templateName;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }
}
if (typeof window !== "undefined") {
  window.updateGlobalTemplateBadge = updateGlobalTemplateBadge;
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
  let current = window.location.pathname.split("/").pop() || "directory.html";
  current = current.toLowerCase();
  links.forEach((link) => {
    const target =
      (link.getAttribute("href") || "")
        .split("/")
        .pop()
        .toLowerCase() || "directory.html";
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
  return parseJsonResponse(res);
}

async function parseJsonResponse(res) {
  const rawText = await res.text();
  let data = {};
  try {
    data = rawText ? JSON.parse(rawText) : {};
  } catch (_) {
    const trimmed = String(rawText || "").trim();
    const message = trimmed.startsWith("<")
      ? `Server returned HTML instead of JSON (${res.status} ${res.statusText}).`
      : trimmed || res.statusText || "Request failed. Please try again.";
    throw new Error(message);
  }
  if (!res.ok || data.status === "error") {
    const message =
      data.message || res.statusText || "Request failed. Please try again.";
    throw new Error(message);
  }
  return data;
}

function setDirectoryInfo({
  classroom,
  tutor_name,
  time_slot,
  exam_name,
  session_id,
  student_id,
  path,
  mode,
  display,
}) {
  const resolvedClassroom = classroom || exam_name || "";
  const resolvedTutor = tutor_name || session_id || "";
  const resolvedTime = time_slot || localStorage.getItem("timeSlot") || "";

  const prevSessionKey = localStorage.getItem("sessionKey");
  const nextSessionKey = `${resolvedClassroom}::${resolvedTutor}::${resolvedTime}`;
  if (prevSessionKey !== nextSessionKey) {
    localStorage.setItem("sessionKey", nextSessionKey);
    localStorage.removeItem("completedStudents");
    localStorage.removeItem("sessionStudents");
    localStorage.removeItem("sessionStudentsCount");
  }
  localStorage.setItem("classroom", resolvedClassroom);
  localStorage.setItem("tutorName", resolvedTutor);
  localStorage.setItem("timeSlot", resolvedTime);

  // Legacy keys remain for pages that still consume exam/session.
  localStorage.setItem("examName", resolvedClassroom);
  localStorage.setItem("sessionId", resolvedTutor);

  localStorage.setItem("studentId", student_id);
  if (path) localStorage.setItem("basePath", path);
  else localStorage.removeItem("basePath");
  localStorage.setItem("directoryMode", mode || "create");
  if (display) localStorage.setItem("directoryDisplay", display);
  else localStorage.removeItem("directoryDisplay");
}

function ensureDirectoryConfigured() {
  const classroom = localStorage.getItem("classroom") || localStorage.getItem("examName");
  const tutorName = localStorage.getItem("tutorName") || localStorage.getItem("sessionId");
  const timeSlot = localStorage.getItem("timeSlot");
  const student = localStorage.getItem("studentId");
  if (!classroom || !tutorName || !timeSlot || !student) {
    alert("Please set up a directory before continuing.");
    goTo("directory.html");
    return false;
  }
  return true;
}

function getDirectoryInfo() {
  const classroom = localStorage.getItem("classroom") || localStorage.getItem("examName");
  const tutor_name = localStorage.getItem("tutorName") || localStorage.getItem("sessionId");
  const time_slot = localStorage.getItem("timeSlot");
  const student_id = localStorage.getItem("studentId");
  const base_path = localStorage.getItem("basePath");
  const mode = localStorage.getItem("directoryMode");
  const display = localStorage.getItem("directoryDisplay");

  if (!classroom || !tutor_name || !time_slot) return null;

  return {
    classroom,
    tutor_name,
    time_slot,
    exam_name: classroom,
    session_id: tutor_name,
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

  statusModalActionBtn = document.createElement("button");
  statusModalActionBtn.type = "button";
  statusModalActionBtn.className = "status-modal-action danger";
  statusModalActionBtn.style.display = "none";

  box.append(spinner, statusModalMessageEl, statusModalActionBtn, statusModalCloseBtn);
  statusModalOverlay.appendChild(box);
  document.body.appendChild(statusModalOverlay);
  return statusModalOverlay;
}

function setStatusModalAction(action = null) {
  ensureStatusModalElements();
  if (!statusModalActionBtn) return;
  statusModalActionBtn.onclick = null;

  if (!action || typeof action !== "object") {
    statusModalActionBtn.style.display = "none";
    statusModalActionBtn.textContent = "";
    return;
  }

  statusModalActionBtn.textContent = action.label || "Stop";
  statusModalActionBtn.className = `status-modal-action ${action.className || "danger"}`;
  statusModalActionBtn.onclick = action.onClick || null;
  statusModalActionBtn.style.display = "inline-block";
}

function showStatusModal(message, state = "info", action = null) {
  const overlay = ensureStatusModalElements();
  if (statusModalHideTimeout) {
    clearTimeout(statusModalHideTimeout);
    statusModalHideTimeout = null;
  }
  overlay.dataset.state = state;
  statusModalMessageEl.textContent = message;
  statusModalCloseBtn.style.display = state === "pending" ? "none" : "inline-block";
  setStatusModalAction(action);
  overlay.classList.add("visible");
  return overlay;
}

function updateStatusModal(overlay, message, state = "info", autoHide = false) {
  const modal = overlay || ensureStatusModalElements();
  modal.dataset.state = state;
  statusModalMessageEl.textContent = message;
  statusModalCloseBtn.style.display = state === "pending" ? "none" : "inline-block";
  if (state !== "pending") {
    setStatusModalAction(null);
  }
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
  setStatusModalAction(null);
}

// -----------------------------
// Directory setup page
// -----------------------------

function setupWelcomePage() {
  // Clear previous session setup when the app is freshly opened
  const keysToClear = [
    "templateName",
    "templateDevices",
    "activeTemplateName",
    "activeTemplateDevices",
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
  window.setTimeout(() => goTo("index.html"), 2200);
}

// -----------------------------
// Init
// -----------------------------

document.addEventListener("DOMContentLoaded", () => {
  loadNavbar();
  if (document.getElementById("welcomePage")) setupWelcomePage();
});
