// Electron main process. It owns the desktop window, native folder dialogs, and
// the Flask backend child process that the renderer pages call through HTTP.
const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');
const fs = require('fs');

let mainWindow = null;
let flaskProcess = null;

function broadcastFlaskLog(line) {
  // Renderer pages subscribe to this channel for the raw Terminal Output tabs.
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('flask-log', line);
}

ipcMain.handle('select-directory', async (event, defaultPath) => {
  // Native directory picker fallback used by pages that need a real filesystem
  // folder instead of the custom in-app session browser.
  let targetPath = defaultPath;
  if (!targetPath || typeof targetPath !== 'string') {
    targetPath = app.getPath('documents');
  }
  const options = {
    properties: ['openDirectory'],
    defaultPath: targetPath,
  };
  const result = await dialog.showOpenDialog(mainWindow || undefined, options);
  if (result.canceled || !result.filePaths || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
});

/**
 * Get available COM ports on Windows
 */
ipcMain.handle('get-available-com-ports', async () => {
  if (process.platform !== 'win32') {
    return [];
  }
  
  try {
    const { execSync } = require('child_process');
    try {
      // Method 1: Use PowerShell to list COM ports from registry
      const command = `Get-ItemProperty -Path "HKLM:\\HARDWARE\\DEVICEMAP\\SERIALCOMM" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty PSObject.Properties | Where-Object {$_.Name -like "\\Device\\*"} | Select-Object -ExpandProperty Value`;
      const output = execSync(`powershell -Command "${command}"`, { 
        encoding: 'utf8',
        timeout: 5000
      });
      
      const ports = output
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line.match(/^COM\d+$/));
      
      if (ports.length > 0) {
        console.log('[Windows Serial] Detected COM ports:', ports);
        return [...new Set(ports)]; // Remove duplicates
      }
    } catch (err) {
      // Fallback to simpler method
      console.debug('[Windows Serial] PowerShell registry method failed, trying WMI');
    }
    
    // Method 2: Use WMI to list COM ports
    const wmiCommand = `Get-WmiObject Win32_SerialPort | Select-Object -ExpandProperty DeviceID`;
    const wmiOutput = execSync(`powershell -Command "${wmiCommand}"`, { 
      encoding: 'utf8',
      timeout: 5000
    });
    
    const ports = wmiOutput
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.match(/^COM\d+$/));
    
    if (ports.length > 0) {
      console.log('[Windows Serial] Detected COM ports via WMI:', ports);
      return [...new Set(ports)];
    }
    
    // If still nothing found, return empty
    console.log('[Windows Serial] No COM ports found');
    return [];
  } catch (err) {
    console.debug('[Windows Serial] Error detecting COM ports:', err.message);
    // Return empty array - user will need to manually enter port
    return [];
  }
});

/**
 * Wait until Flask port is reachable (default: 5050)
 */
function waitForPort(host, port, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    (function tryConnect() {
      const socket = net.createConnection(port, host);
      socket.on('connect', () => {
        socket.destroy();
        resolve();
      });
      socket.on('error', () => {
        socket.destroy();
        if (Date.now() - start > timeout)
          reject(new Error('Flask startup timeout'));
        else setTimeout(tryConnect, 250);
      });
    })();
  });
}

/**
 * Start Flask backend using fyp-venv's Python interpreter
 */
function startFlask() {
  const packagedBackendExe = path.join(
    process.resourcesPath,
    'backend',
    'conf-comparison-server.exe',
  );
  const packagedBackendLinuxBin = path.join(
    process.resourcesPath,
    'backend',
    'conf-comparison-server',
  );
  const flaskScript = path.join(__dirname, '..', 'server.py');

  let command;
  let args;
  let cwd;
  let useShell = false;
  let pythonPath;

  if (app.isPackaged && process.platform === 'win32' && fs.existsSync(packagedBackendExe)) {
    command = packagedBackendExe;
    args = [];
    cwd = path.dirname(packagedBackendExe);
    console.log(`[INFO] Spawning bundled backend executable: ${packagedBackendExe}`);
  } else if (
    app.isPackaged &&
    process.platform === 'linux' &&
    fs.existsSync(packagedBackendLinuxBin)
  ) {
    command = packagedBackendLinuxBin;
    args = [];
    cwd = path.dirname(packagedBackendLinuxBin);
    console.log(`[INFO] Spawning bundled backend executable: ${packagedBackendLinuxBin}`);
  } else {
    // Prefer the repo-local virtual environment for development. If this app is
    // running on a machine where the Python backend was installed with pip, fall
    // back to the console command exposed by pyproject.toml.
    if (process.platform === 'win32') {
      pythonPath = path.join(__dirname, '..', 'fyp-venv', 'Scripts', 'python.exe');
    } else {
      pythonPath = path.join(__dirname, '..', 'fyp-venv', 'bin', 'python');
    }

    if (fs.existsSync(pythonPath) && fs.existsSync(flaskScript)) {
      command = pythonPath;
      args = [flaskScript];
      cwd = path.join(__dirname, '..');
      console.log(`[INFO] Spawning Flask using: ${pythonPath}`);
      console.log(`[INFO] Running server: ${flaskScript}`);
    } else {
      command = 'conf-comparison-server';
      args = [];
      cwd = __dirname;
      useShell = process.platform === 'win32';
      console.log('[INFO] Repo-local fyp-venv not found; using installed conf-comparison-server command.');
    }
  }

  flaskProcess = spawn(command, args, {
    cwd,
    shell: useShell,
  });

  flaskProcess.stdout.on('data', (data) => {
    const text = data.toString().trim();
    if (!text) return;
    const line = `[Flask stdout] ${text}`;
    console.log(line);
    broadcastFlaskLog(line);
  });

  flaskProcess.stderr.on('data', (data) => {
    const text = data.toString().trim();
    if (!text) return;
    const line = `[Flask stderr] ${text}`;
    console.error(line);
    broadcastFlaskLog(line);
  });

  flaskProcess.on('close', (code) => {
    const line = `[INFO] Flask exited with code ${code}`;
    console.log(line);
    broadcastFlaskLog(line);
  });

  return waitForPort('127.0.0.1', 5050, 15000);
}

/**
 * Create Electron window
 */
function createWindow() {
  mainWindow = new BrowserWindow({
    // fullscreen: true, // Remove fullscreen
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  mainWindow.maximize(); // Add this line to maximize the window

  mainWindow.loadFile(path.join(__dirname, 'welcome.html'));

  mainWindow.on('closed', () => {
    mainWindow = null;
    if (flaskProcess) {
      try {
        console.log('[INFO] Terminating Flask process...');
        flaskProcess.kill('SIGINT');
      } catch (err) {
        console.warn('[WARN] Failed to kill Flask process:', err);
      }
    }
  });
}

/**
 * App entry
 */
app.whenReady().then(async () => {
  try {
    await startFlask();
    console.log('[READY] Flask server is live at http://127.0.0.1:5050');
  } catch (err) {
    console.warn('[WARN] Flask did not become ready in time:', err);
  }
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
