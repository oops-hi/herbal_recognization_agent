import { app, shell, BrowserWindow } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { startBackend, killBackend, type BackendHandle } from './backend'

let backend: BackendHandle | null = null

function createWindow(port: number): void {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 1000,
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      nodeIntegration: false,
      contextIsolation: true
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // dev：vite dev server（等后端握手成功再加载，避免徽章先闪"服务异常"）
  // prod：Flask 托管的 Vue dist
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadURL('http://127.0.0.1:' + port + '/')
  }
}

// 单实例锁：防双击重复启动（否则端口/会话双实例打架）
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const wins = BrowserWindow.getAllWindows()
    if (wins.length) {
      if (wins[0].isMinimized()) wins[0].restore()
      wins[0].focus()
    }
  })

  app.whenReady().then(async () => {
    electronApp.setAppUserModelId('com.oopsherbal.agent')

    // 默认打开/关闭 DevTools by F12 in development；生产忽略 CommandOrControl + R
    app.on('browser-window-created', (_, window) => {
      optimizer.watchWindowShortcuts(window)
    })

    try {
      // 启动后端并等待握手（dev 固定端口 / prod 动态端口）
      backend = await startBackend()
    } catch (e) {
      console.error('[main] 后端启动失败：', e)
      app.quit()
      return
    }
    createWindow(backend.port)

    app.on('activate', function () {
      if (BrowserWindow.getAllWindows().length === 0) createWindow(backend!.port)
    })
  })

  // 退出时清理后端子进程（整树杀）
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
  app.on('before-quit', () => {
    killBackend(backend?.proc)
    backend = null
  })
  process.on('exit', () => killBackend(backend?.proc))
}
