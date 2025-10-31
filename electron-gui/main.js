// electron-gui/main.js
const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');
const fs = require('fs');

let mainWindow = null;
let flaskProcess = null;

ipcMain.handle('select-directory', async () => {
  const options = {
    properties: ['openDirectory'],
  };
  const result = await dialog.showOpenDialog(mainWindow || undefined, options);
  if (result.canceled || !result.filePaths || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
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
  const flaskScript = path.join(__dirname, '..', 'server.py');

  // Path to your venv python
  // Adjust if needed based on your OS
  let pythonPath;
  if (process.platform === 'win32') {
    pythonPath = path.join(__dirname, '..', 'fyp-venv', 'Scripts', 'python.exe');
  } else {
    pythonPath = path.join(__dirname, '..', 'fyp-venv', 'bin', 'python');
  }

  // Verify interpreter exists
  if (!fs.existsSync(pythonPath)) {
    console.error(`[ERROR] Python interpreter not found at ${pythonPath}`);
    console.error('Please verify your fyp-venv is set up correctly.');
    return Promise.reject('Missing Python interpreter');
  }

  console.log(`[INFO] Spawning Flask using: ${pythonPath}`);
  console.log(`[INFO] Running server: ${flaskScript}`);

  flaskProcess = spawn(pythonPath, [flaskScript], {
    cwd: path.join(__dirname, '..'),
    shell: false,
  });

  flaskProcess.stdout.on('data', (data) => {
    console.log(`[Flask stdout] ${data.toString().trim()}`);
  });

  flaskProcess.stderr.on('data', (data) => {
    console.error(`[Flask stderr] ${data.toString().trim()}`);
  });

  flaskProcess.on('close', (code) => {
    console.log(`[INFO] Flask exited with code ${code}`);
  });

  return waitForPort('127.0.0.1', 5050, 15000);
}

/**
 * Create Electron window
 */
function createWindow() {
  mainWindow = new BrowserWindow({
    fullscreen: true,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
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
