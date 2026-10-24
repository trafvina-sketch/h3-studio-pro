const { app, BrowserWindow, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const { spawn } = require('child_process');

let mainWindow = null;
let pythonProcess = null;
let isQuitting = false;
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

function resolveBackendDir() {
  // 1. Khi đã đóng gói bản cài đặt Setup (NSIS), ưu tiên dùng thư mục app-backend nằm ngay trong bộ cài của khách
  if (app.isPackaged && process.resourcesPath) {
    const bundledBackend = path.join(process.resourcesPath, 'app-backend');
    if (fs.existsSync(path.join(bundledBackend, 'dashboard.py'))) {
      return bundledBackend;
    }
  }

  // 2. Khi chạy ở chế độ Portable hoặc Dev
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR || '';
  const exeDir = path.dirname(process.execPath || '');
  const searchDirs = [
    portableDir,
    path.resolve(__dirname, '..'),
    exeDir,
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

function findPythonExecutable(rootDir) {
  // Ưu tiên số 1: Bộ chạy Python Portable (python-embed) được đóng gói sẵn bên trong phần mềm (khách không cần cài Python)
  const embeddedCandidates = [
    rootDir ? path.join(rootDir, 'python-embed', 'python.exe') : '',
    process.resourcesPath ? path.join(process.resourcesPath, 'app-backend', 'python-embed', 'python.exe') : '',
    path.resolve(__dirname, '..', 'python-embed', 'python.exe')
  ].filter(Boolean);

  for (const emb of embeddedCandidates) {
    if (fs.existsSync(emb)) {
      return emb;
    }
  }

  // Fallback: Tìm Python cài trên máy nếu đang chạy ở môi trường Dev chưa có python-embed
  const sysCandidates = [
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python312', 'python.exe') : '',
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python311', 'python.exe') : '',
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python310', 'python.exe') : '',
    'python',
    'py'
  ].filter(Boolean);

  for (const cand of sysCandidates) {
    if (cand.includes('\\') && fs.existsSync(cand)) {
      return cand;
    }
  }
  return 'python';
}

function startPythonBackend() {
  if (isQuitting) return;
  const rootDir = resolveBackendDir();
  const dashboardPy = path.join(rootDir, 'dashboard.py');
  const pyExe = findPythonExecutable(rootDir);

  // Đưa thư mục rootDir (chứa sẵn ffmpeg.exe và python-embed) lên đầu PATH để mọi lệnh gọi ffmpeg đều hoạt động ngay không cần cài
  const env = Object.assign({}, process.env);
  const extraPaths = [
    rootDir,
    path.join(rootDir, 'python-embed')
  ].filter(p => fs.existsSync(p));
  env.PATH = [...extraPaths, env.PATH || ''].join(path.delimiter);
  env.PYTHONUTF8 = '1';

  try {
    pythonProcess = spawn(pyExe, ['-X', 'utf8', '-u', dashboardPy], {
      cwd: rootDir,
      env,
      stdio: 'ignore',
      detached: false,
      shell: false,
      windowsHide: true
    });
    pythonProcess.on('exit', () => {
      pythonProcess = null;
      // Tự động khởi động lại backend sau khi người dùng bấm "Cập Nhật Từ GitHub"
      if (!isQuitting && mainWindow && !mainWindow.isDestroyed()) {
        setTimeout(() => {
          if (!isQuitting) startPythonBackend();
        }, 700);
      }
    });
  } catch (err) {
    console.error('Failed to spawn Python backend:', err);
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
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 350));
      alive = await checkServerAlive();
      if (alive) break;
    }
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(SERVER_URL);
  }
}

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(createWindow);

  app.on('before-quit', () => {
    isQuitting = true;
  });

  app.on('window-all-closed', () => {
    isQuitting = true;
    if (pythonProcess) {
      try {
        spawn('taskkill', ['/pid', String(pythonProcess.pid), '/f', '/t'], { windowsHide: true });
      } catch (e) {}
      pythonProcess = null;
    }
    app.quit();
  });
}
