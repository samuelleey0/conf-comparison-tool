// windows-serial.js - Windows-specific serial connection handler

const WINDOWS_PRESETS = {
  COM1: { port: "COM1", baudrate: 9600, name: "COM1 - Serial" },
  COM2: { port: "COM2", baudrate: 9600, name: "COM2 - Serial" },
  COM3: { port: "COM3", baudrate: 9600, name: "COM3 - Serial (Default)" },
  COM4: { port: "COM4", baudrate: 9600, name: "COM4 - Serial" },
  COM5: { port: "COM5", baudrate: 9600, name: "COM5 - Serial" },
};

/**
 * Initialize Windows serial connection handling
 * Detects available COM ports and configures UI accordingly
 */
function initWindowsSerialHandler() {
  console.log("[Windows Serial] Initializing Windows serial handler");
  
  const serialPort = document.getElementById("serialPort");
  const presetContainer = document.querySelector(".inline-options");
  
  if (!serialPort) return;
  
  // Set default Windows port
  serialPort.placeholder = "COM3";
  serialPort.value = localStorage.getItem("serialPort") || "COM3";
  
  // Apply Windows-specific preset styling
  applyWindowsPresetStyles();
  
  // Handle preset changes specifically for Windows
  const presetRadios = document.querySelectorAll('input[name="serialPreset"]');
  presetRadios.forEach((radio) => {
    radio.addEventListener("change", () => handleWindowsPresetChange(radio.value));
  });
  
  // Validate Windows COM port format on input
  serialPort.addEventListener("blur", () => validateWindowsCOMPort(serialPort));
  
  // Suggest available ports
  suggestAvailableComPorts();
}

/**
 * Apply Windows-specific preset styles
 */
function applyWindowsPresetStyles() {
  const presetLabels = document.querySelectorAll('.inline-options label');
  presetLabels.forEach((label) => {
    if (label.textContent.includes("Windows")) {
      label.style.fontWeight = "bold";
      label.style.color = "#2c5aa0";
    }
  });
}

/**
 * Handle Windows-specific preset changes
 */
function handleWindowsPresetChange(preset) {
  const serialPort = document.getElementById("serialPort");
  if (!serialPort) return;
  
  if (preset === "windows") {
    serialPort.value = "COM3";
    serialPort.setAttribute("readonly", "readonly");
    localStorage.setItem("serialPort", "COM3");
  } else if (preset === "custom") {
    serialPort.removeAttribute("readonly");
    serialPort.value = "";
    serialPort.placeholder = "e.g., COM3, COM4";
  }
}

/**
 * Validate Windows COM port format (COMx where x is 1-9)
 */
function validateWindowsCOMPort(portInput) {
  const value = portInput.value.toUpperCase().trim();
  const comPortRegex = /^COM[0-9]$/;
  
  if (value && !comPortRegex.test(value)) {
    console.warn(`[Windows Serial] Invalid COM port format: ${value}. Expected format: COM1-COM9`);
    portInput.style.borderColor = "#e74c3c";
    portInput.title = "Invalid format. Use COM1 through COM9";
    return false;
  } else {
    portInput.style.borderColor = "";
    portInput.title = "";
    if (value) {
      localStorage.setItem("serialPort", value);
    }
    return true;
  }
}

/**
 * Suggest available COM ports to user
 * In Electron, this could detect actual COM ports via IPC
 */
function suggestAvailableComPorts() {
  try {
    // Try to get available COM ports from Electron IPC if available
    if (typeof require !== 'undefined') {
      try {
        const { ipcRenderer } = require("electron");
        if (ipcRenderer) {
          ipcRenderer
            .invoke("get-available-com-ports")
            .then((ports) => {
              if (ports && ports.length > 0) {
                console.log("[Windows Serial] Available COM ports:", ports);
                displayAvailableComPorts(ports);
              } else {
                displayNoPortsFoundMessage();
              }
            })
            .catch((err) => {
              console.debug("[Windows Serial] Could not detect COM ports via IPC:", err);
              displayNoPortsFoundMessage();
            });
        }
      } catch (err) {
        console.debug("[Windows Serial] IPC not available in require", err);
        displayNoPortsFoundMessage();
      }
    }
  } catch (err) {
    console.debug("[Windows Serial] IPC not available, skipping COM port detection");
    displayNoPortsFoundMessage();
  }
}

/**
 * Display available COM ports to user (UI feedback)
 */
function displayAvailableComPorts(ports) {
  const serialPort = document.getElementById("serialPort");
  if (!serialPort) return;
  
  // Remove any existing info box
  const existingInfo = serialPort.parentElement?.querySelector(".com-ports-info");
  if (existingInfo) existingInfo.remove();
  
  const availableInfo = document.createElement("div");
  availableInfo.className = "info-box com-ports-info";
  availableInfo.style.marginTop = "10px";
  availableInfo.style.fontSize = "0.9rem";
  availableInfo.style.backgroundColor = "#d4edda";
  availableInfo.style.borderColor = "#28a745";
  availableInfo.style.color = "#155724";
  availableInfo.innerHTML = `
    <strong>✓ Detected COM Ports:</strong> ${ports.join(", ")}
  `;
  
  const parent = serialPort.parentElement;
  if (parent) {
    parent.appendChild(availableInfo);
  }
}

/**
 * Display no ports found message
 */
function displayNoPortsFoundMessage() {
  const serialPort = document.getElementById("serialPort");
  if (!serialPort) return;
  
  // Remove any existing info box
  const existingInfo = serialPort.parentElement?.querySelector(".com-ports-info");
  if (existingInfo) existingInfo.remove();
  
  const warningInfo = document.createElement("div");
  warningInfo.className = "info-box com-ports-info";
  warningInfo.style.marginTop = "10px";
  warningInfo.style.fontSize = "0.9rem";
  warningInfo.style.backgroundColor = "#f8d7da";
  warningInfo.style.borderColor = "#f5c6cb";
  warningInfo.style.color = "#721c24";
  warningInfo.innerHTML = `
    <strong>⚠ No COM ports detected.</strong> Check Device Manager or ensure your device is connected.
  `;
  
  const parent = serialPort.parentElement;
  if (parent) {
    parent.appendChild(warningInfo);
  }
}

/**
 * Override the serial connection save function for Windows
 * Ensures Windows-specific validation and formatting
 */
function validateWindowsSerialConnection(port, baudrate) {
  const comPortRegex = /^COM[0-9]$/;
  
  if (!comPortRegex.test(port.toUpperCase())) {
    console.error(`[Windows Serial] Invalid COM port: ${port}`);
    alert(`Invalid COM port format. Please use COM1 through COM9`);
    return false;
  }
  
  if (!Number.isInteger(baudrate) || baudrate <= 0) {
    console.error(`[Windows Serial] Invalid baud rate: ${baudrate}`);
    alert("Invalid baud rate");
    return false;
  }
  
  console.log(`[Windows Serial] Validated connection: ${port} @ ${baudrate} baud`);
  return true;
}

/**
 * Format Windows COM port name for display
 */
function formatWindowsComPortName(port) {
  const upperPort = port.toUpperCase();
  return WINDOWS_PRESETS[upperPort]?.name || `${upperPort} - Serial`;
}

// Auto-initialize when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  // Only initialize if we detect Windows platform
  const platform = navigator.platform || navigator.userAgentData?.platform || "unknown";
  if (platform.toLowerCase().includes("win")) {
    initWindowsSerialHandler();
  }
});

// Export for use in other modules
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    initWindowsSerialHandler,
    validateWindowsSerialConnection,
    formatWindowsComPortName,
  };
}
