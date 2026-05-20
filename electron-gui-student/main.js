// electron-gui/main.js
const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');
const fs = require('fs');

let mainWindow = null;
let flaskProcess = null;
let latestStartupProgress = {
  percent: 0,
  message: 'Opening application...',
};

function sendStartupProgress(percent, message) {
  latestStartupProgress = {
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    message,
  };
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('startup-progress', latestStartupProgress);
}

ipcMain.on('startup-progress-ready', () => {
  sendStartupProgress(latestStartupProgress.percent, latestStartupProgress.message);
});

function copyDirectoryRecursive(source, target) {
  if (!fs.existsSync(source)) return;
  fs.mkdirSync(target, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const sourcePath = path.join(source, entry.name);
    const targetPath = path.join(target, entry.name);
    if (entry.isDirectory()) {
      copyDirectoryRecursive(sourcePath, targetPath);
    } else {
      fs.copyFileSync(sourcePath, targetPath);
      try {
        fs.chmodSync(targetPath, fs.statSync(sourcePath).mode);
      } catch (_) {
        // Best effort only; executable permission is set explicitly below.
      }
    }
  }
}

function preparePackagedBackend(sourceDir) {
  const backendName = process.platform === 'win32'
    ? 'conf-comparison-server.exe'
    : 'conf-comparison-server';
  const sourceBin = path.join(sourceDir, backendName);
  if (!fs.existsSync(sourceBin)) return null;

  if (process.platform !== 'linux') {
    return {
      bin: sourceBin,
      cwd: path.dirname(sourceBin),
    };
  }

  const targetDir = path.join(app.getPath('userData'), 'backend');
  const targetBin = path.join(targetDir, backendName);
  const sourceStat = fs.statSync(sourceBin);
  const needsCopy = !fs.existsSync(targetBin) || fs.statSync(targetBin).mtimeMs < sourceStat.mtimeMs;

  if (needsCopy) {
    fs.rmSync(targetDir, { recursive: true, force: true });
    copyDirectoryRecursive(sourceDir, targetDir);
  }
  fs.chmodSync(targetBin, 0o755);

  return {
    bin: targetBin,
    cwd: targetDir,
  };
}

function broadcastFlaskLog(line) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.webContents.send('flask-log', line);
}

ipcMain.handle('select-directory', async (event, defaultPath) => {
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
function waitForPort(host, port, timeout = 10000, onProgress = null) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    (function tryConnect() {
      const socket = net.createConnection(port, host);
      socket.on('connect', () => {
        socket.destroy();
        if (onProgress) onProgress(96, 'Backend is ready.');
        resolve();
      });
      socket.on('error', () => {
        socket.destroy();
        const elapsed = Date.now() - start;
        if (onProgress) {
          const percent = 62 + (Math.min(elapsed, timeout) / timeout) * 32;
          onProgress(percent, 'Waiting for local backend...');
        }
        if (elapsed > timeout)
          reject(new Error('Flask startup timeout'));
        else setTimeout(tryConnect, 250);
      });
    })();
  });
}

/**
 * Start Flask backend using fyp-venv's Python interpreter
 */
function startFlask(onProgress = null) {
  if (onProgress) onProgress(18, 'Preparing local backend...');
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
    if (onProgress) onProgress(34, 'Loading bundled backend...');
    const backend = preparePackagedBackend(path.dirname(packagedBackendExe));
    command = backend ? backend.bin : packagedBackendExe;
    args = [];
    cwd = backend ? backend.cwd : path.dirname(packagedBackendExe);
    console.log(`[INFO] Spawning bundled backend executable: ${command}`);
  } else if (
    app.isPackaged &&
    process.platform === 'linux' &&
    fs.existsSync(packagedBackendLinuxBin)
  ) {
    if (onProgress) onProgress(34, 'Preparing bundled backend...');
    const backend = preparePackagedBackend(path.dirname(packagedBackendLinuxBin));
    command = backend ? backend.bin : packagedBackendLinuxBin;
    args = [];
    cwd = backend ? backend.cwd : path.dirname(packagedBackendLinuxBin);
    console.log(`[INFO] Spawning bundled backend executable: ${command}`);
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
      if (onProgress) onProgress(34, 'Starting development backend...');
      command = pythonPath;
      args = [flaskScript];
      cwd = path.join(__dirname, '..');
      console.log(`[INFO] Spawning Flask using: ${pythonPath}`);
      console.log(`[INFO] Running server: ${flaskScript}`);
    } else {
      if (onProgress) onProgress(34, 'Starting installed backend...');
      command = 'conf-comparison-server';
      args = [];
      cwd = __dirname;
      useShell = process.platform === 'win32';
      console.log('[INFO] Repo-local fyp-venv not found; using installed conf-comparison-server command.');
    }
  }

  if (onProgress) onProgress(54, 'Launching local backend...');
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

  flaskProcess.on('error', (err) => {
    const line = `[ERROR] Failed to start Flask: ${err.message}`;
    console.error(line);
    broadcastFlaskLog(line);
  });

  return waitForPort('127.0.0.1', 5050, 15000, onProgress);
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

  const welcomeLoaded = new Promise((resolve) => {
    mainWindow.webContents.once('did-finish-load', () => {
      sendStartupProgress(
        Math.max(latestStartupProgress.percent, 8),
        latestStartupProgress.message,
      );
      resolve();
    });
  });

  mainWindow.webContents.on('did-finish-load', () => {
    const loadedFile = mainWindow.webContents.getURL();
    if (loadedFile.endsWith('/welcome.html') || loadedFile.endsWith('\\welcome.html')) {
      sendStartupProgress(
        Math.max(latestStartupProgress.percent, 8),
        latestStartupProgress.message,
      );
    }
  });
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

  return welcomeLoaded;
}

/**
 * App entry
 */
app.whenReady().then(async () => {
  await createWindow();
  try {
    sendStartupProgress(12, 'Starting local services...');
    await startFlask(sendStartupProgress);
    console.log('[READY] Flask server is live at http://127.0.0.1:5050');
    sendStartupProgress(100, 'Ready.');
  } catch (err) {
    console.warn('[WARN] Flask did not become ready in time:', err);
    sendStartupProgress(100, 'Backend took too long. Opening the app...');
  }
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadFile(path.join(__dirname, 'index.html'));
    }
  }, 350);
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
