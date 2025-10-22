// electron-gui/main.js
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;
let flaskProcess;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 700,
    webPreferences: {
      preload: path.join(__dirname, 'renderer.js'),
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  mainWindow.loadFile('index.html');
  mainWindow.on('closed', () => {
    mainWindow = null;
    if (flaskProcess) flaskProcess.kill('SIGINT');
  });
}

// Launch Flask backend
function startFlask() {
  const flaskPath = path.join(__dirname, '..', 'server.py');
  flaskProcess = spawn('python', [flaskPath], { shell: true });
  flaskProcess.stdout.on('data', (data) => console.log(`[Flask]: ${data}`));
  flaskProcess.stderr.on('data', (data) => console.error(`[Flask Error]: ${data}`));
}

app.whenReady().then(() => {
  startFlask();
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});