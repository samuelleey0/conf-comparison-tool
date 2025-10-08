const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
    width: 1000,
    height: 700,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });
  win.loadFile('index.html');
}

app.whenReady().then(() => {
  const flask = spawn('python', ['../server.py']);
  flask.stdout.on('data', data => console.log(`🐍 [Flask]: ${data}`));
  flask.stderr.on('data', data => console.error(`🐍 [Flask Error]: ${data}`));
  flask.on('close', code => console.log(`Flask server exited with code ${code}`));

  createWindow();
});