/*
 * src/main/backend.ts
 * Python 后端子进程管理：spawn → stdout 握手（HERB_READY_PORT）→ kill（整树）。
 *
 * dev ：spawn Conda task 环境 python 跑仓库根 app.py（cwd = 仓库根）
 * prod：spawn extraResources 拷入的后端 onedir exe（resources/herbal-backend/herbal-backend.exe）
 */
import { spawn, spawnSync, type ChildProcess } from 'child_process'
import path from 'path'
import { app } from 'electron'

/** dev 模式固定端口（vite proxy 静态 target 需要）；prod 用 0 = 系统分配动态端口 */
const DEV_PORT = Number(process.env.HERB_DEV_PORT || 5175)
const HANDSHAKE_RE = /HERB_READY_PORT=(\d+)/
const HANDSHAKE_TIMEOUT_MS = 60_000

export interface BackendHandle {
  proc: ChildProcess
  port: number
}

function backendCommand(): { cmd: string; args: string[]; cwd: string } {
  if (!app.isPackaged) {
    // dev：Conda task python + 仓库根 app.py（frontend/ 的上级）
    return {
      cmd: 'D:/CondaEnv/task/python.exe',
      args: ['app.py'],
      cwd: path.join(app.getAppPath(), '..')
    }
  }
  // prod：resources/herbal-backend/ 内的 PyInstaller onedir
  return {
    cmd: path.join(process.resourcesPath, 'herbal-backend', 'herbal-backend.exe'),
    args: [],
    cwd: path.join(process.resourcesPath, 'herbal-backend')
  }
}

export function startBackend(): Promise<BackendHandle> {
  const { cmd, args, cwd } = backendCommand()
  const proc = spawn(cmd, args, {
    cwd,
    env: {
      ...process.env,
      HERB_HOST: '127.0.0.1',
      HERB_PORT: app.isPackaged ? '0' : String(DEV_PORT),
      HERB_SERVE_DIST: app.isPackaged ? '1' : '0' // dev 走 vite，禁止 Flask 抢 dist
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: false // 保留控制台：答辩现场可见握手端口/启动日志
  })
  proc.stderr?.setEncoding('utf8')
  proc.stderr?.on('data', (d: string) => console.error('[backend]', d.trimEnd()))
  return waitHandshake(proc)
}

/** 正则解析 stdout 握手帧；超时抛错并杀进程 */
function waitHandshake(proc: ChildProcess): Promise<BackendHandle> {
  return new Promise((resolve, reject) => {
    let buf = ''
    const timer = setTimeout(() => {
      proc.kill()
      reject(new Error('后端启动超时（60s 内未收到 HERB_READY_PORT 握手）'))
    }, HANDSHAKE_TIMEOUT_MS)

    proc.stdout?.setEncoding('utf8')
    proc.stdout?.on('data', (d: string) => {
      console.log('[backend]', d.trimEnd())
      buf += d
      const m = buf.match(HANDSHAKE_RE)
      if (m) {
        clearTimeout(timer)
        resolve({ proc, port: Number(m[1]) })
      }
    })
    proc.on('error', (e) => {
      clearTimeout(timer)
      reject(new Error('后端进程启动失败：' + e.message))
    })
    proc.on('exit', (code) => {
      clearTimeout(timer)
      reject(new Error('后端进程提前退出，code=' + code))
    })
  })
}

/** Windows 整树杀（werkzeug 多线程 + 子进程全清）；非 Windows 走 SIGTERM 兜底 */
export function killBackend(proc: ChildProcess | null | undefined): void {
  if (!proc || !proc.pid) return
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { stdio: 'ignore' })
    } else {
      proc.kill('SIGTERM')
    }
  } catch (e) {
    console.warn('[backend] 结束进程失败：', e)
  }
}
