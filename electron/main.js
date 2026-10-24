const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');

let mainWindow = null;
let pythonProcess = null;
const PORT = 8030;
const SERVER_URL = `http://127.0.0.1:${PORT}`;

function checkServerAlive() {
  return new Promise((resolve) => {
    const req = http.get(SERVER_URL + '/api/status', (res) => {
      resolve(res.statusCode >= 200 && res.statusCode < 400);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function findPythonExecutable() {
  const candidates = [
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python312', 'python.exe') : '',
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python311', 'python.exe') : '',
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python310', 'python.exe') : '',
    'C:\\Users\\Admin\\AppData\\Local\\Programs\\Python\\Python310\\python.exe',
    'python',
    'py'
  ].filter(Boolean);

  for (const cand of candidates) {
    if (cand.includes('\\') && fs.existsSync(cand)) {
      return cand;
    }
  }
  return 'python';
}

function resolveBackendDir() {
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR || '';
  const exeDir = path.dirname(process.execPath || '');
  const searchDirs = [
    portableDir,
    portableDir ? path.resolve(portableDir, '..') : '',
    portableDir ? path.resolve(portableDir, '..', '..') : '',
    path.resolve(__dirname, '..'),
    exeDir,
    path.resolve(exeDir, '..'),
    path.resolve(exeDir, '..', '..'),
    process.resourcesPath ? path.join(process.resourcesPath, 'app-backend') : '',
    'D:\\H3 Studio Pro',
    'd:\\H3 video\\minimax-h3-colab-skill-main'
  ].filter(Boolean);

  for (const dir of searchDirs) {
    try {
      if (fs.existsSync(path.join(dir, 'dashboard.py'))) {
        return dir;
      }
    } catch (e) {}
  }
  return path.resolve(__dirname, '..');
}

function startPythonBackend() {
  const rootDir = resolveBackendDir();
  const dashboardPy = path.join(rootDir, 'dashboard.py');
  const pyExe = findPythonExecutable();

  try {
    pythonProcess = spawn(pyExe, ['-X', 'utf8', '-u', dashboardPy], {
      cwd: rootDir,
      stdio: 'ignore',
      detached: false,
      shell: false,
      windowsHide: true
    });
  } catch (err) {
    try {
      pythonProcess = spawn('python', ['-X', 'utf8', '-u', dashboardPy], {
        cwd: rootDir,
        stdio: 'ignore',
        detached: false,
        shell: true,
        windowsHide: true
      });
    } catch (e) {
      console.error('Failed to spawn Python backend:', e);
    }
  }
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: '#0c0c0e',
    title: 'H3 Studio Pro - AI Video Generator',
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  mainWindow.loadFile(path.join(__dirname, 'splash.html'));

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http:') || url.startsWith('https:')) {
      if (!url.includes(`127.0.0.1:${PORT}`) && !url.includes(`localhost:${PORT}`)) {
        shell.openExternal(url);
        return { action: 'deny' };
      }
    }
    return { action: 'allow' };
  });

  let alive = await checkServerAlive();
  if (!alive) {
    startPythonBackend();
  }

  let retries = 0;
  const maxRetries = 60;
  const interval = setInterval(async () => {
    retries++;
    alive = await checkServerAlive();
    if (alive) {
      clearInterval(interval);
      if (mainWindow) mainWindow.loadURL(SERVER_URL);
    } else if (retries >= maxRetries) {
      clearInterval(interval);
      if (mainWindow) mainWindow.loadURL(SERVER_URL);
    }
  }, 500);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (pythonProcess && pythonProcess.pid) {
    try {
      spawn('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t']);
    } catch (e) {}
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  if (pythonProcess && pythonProcess.pid) {
    try {
      spawn('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t']);
    } catch (e) {}
  }
});
