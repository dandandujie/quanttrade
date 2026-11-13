/**
 * Host half of the 0925 stock-screening panel.
 *
 * The stock program is a local FastAPI app on 127.0.0.1:8000 that serves its own
 * frontend with absolute `/static` and `/api` paths. The panel embeds it from the
 * Web server's own origin, so this plugin publishes the app under `/quant` and
 * rewrites those two prefixes. Same-origin embedding keeps the document free of
 * CORS and cross-origin frame policy, and gives it one stable entry URL.
 *
 * The same mount also exposes `/quant/control/*`, which starts the local app on
 * demand so the panel's "一键启动" button does not need a terminal.
 */
import http from 'node:http'
import { spawn } from 'node:child_process'
import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  statSync,
  writeFileSync,
  writeSync,
} from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/** Panel prefix on the Web server's origin; the Client mounts the same value. */
const MOUNT = '/quant'
/** Control routes are answered by this plugin, not forwarded upstream. */
const CONTROL_PREFIX = `${MOUNT}/control/`
/** Local stock program. `start.bat` starts it on this port too. */
const UPSTREAM = new URL('http://127.0.0.1:8000')
/** Frame policy belongs to this plugin's mount, not to the upstream app. */
const STRIPPED_RESPONSE_HEADERS = new Set([
  'connection',
  'content-encoding',
  'content-length',
  'content-security-policy',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'x-frame-options',
])
/** Only these documents carry root-relative paths that need the mount prefix. */
const REWRITABLE = /(?:text\/html|(?:java|ecma)script)/i
const START_TIMEOUT = 30_000
const PROBE_TIMEOUT = 1_500
const PREFLIGHT_TIMEOUT = 10_000
const LOG_LIMIT = 2_000_000

const PLUGIN_DIR = path.dirname(realpathSync(fileURLToPath(import.meta.url)))
const APP_ROOT = findAppRoot()
const LOG_PATH = path.join(APP_ROOT, 'data', 'backend.log')

let backendChild = null
let starting = false
let startPromise = null
let lastError = ''
let lastExit = null
let pythonCache = null

export const inject = ['webServer']

export function apply(ctx) {
  ctx.effect(
    () => ctx.webServer.register({ kind: 'prefix', path: MOUNT, handler: handle }),
    'quant-panel: /quant proxy',
  )
}

async function handle(req, res) {
  const url = req.url ?? '/'
  const queryAt = url.indexOf('?')
  const pathname = queryAt === -1 ? url : url.slice(0, queryAt)
  const search = queryAt === -1 ? '' : url.slice(queryAt)

  if (pathname === MOUNT) {
    res.writeHead(308, { location: `${MOUNT}/${search}` })
    res.end()
    return
  }
  if (pathname.startsWith(CONTROL_PREFIX)) {
    await handleControl(req, res, pathname.slice(CONTROL_PREFIX.length))
    return
  }
  if (!pathname.startsWith(`${MOUNT}/`)) {
    respond(res, 404, 'text/plain; charset=utf-8', 'Not found')
    return
  }

  const headers = { ...req.headers }
  delete headers.connection
  delete headers.upgrade
  headers.host = UPSTREAM.host
  headers['accept-encoding'] = 'identity'

  const upstream = http.request(
    {
      protocol: UPSTREAM.protocol,
      hostname: UPSTREAM.hostname,
      port: UPSTREAM.port || 80,
      method: req.method,
      path: pathname.slice(MOUNT.length) + search,
      headers,
    },
    upstreamResponse => relay(req, res, upstreamResponse),
  )
  upstream.on('error', error => {
    respond(
      res,
      502,
      'application/json; charset=utf-8',
      JSON.stringify({ detail: `选股后端未响应（${error.message}）。可在面板点「一键启动」。` }),
    )
  })
  req.on('aborted', () => upstream.destroy())
  req.pipe(upstream)
}

/**
 * Forward one upstream response. HTML and JavaScript get the mount prefix added
 * to their root-relative app paths; every other payload streams untouched.
 */
function relay(req, res, upstreamResponse) {
  const status = upstreamResponse.statusCode ?? 502
  const headers = {}
  for (const [name, value] of Object.entries(upstreamResponse.headers)) {
    if (value === undefined || STRIPPED_RESPONSE_HEADERS.has(name)) continue
    headers[name] = value
  }

  const type = String(upstreamResponse.headers['content-type'] ?? '')
  if (!REWRITABLE.test(type)) {
    res.writeHead(status, headers)
    upstreamResponse.pipe(res)
    return
  }

  const chunks = []
  upstreamResponse.on('data', chunk => chunks.push(chunk))
  upstreamResponse.on('end', () => {
    if (res.writableEnded) return
    const text = Buffer.concat(chunks)
      .toString('utf8')
      .replaceAll('/static/', `${MOUNT}/static/`)
      .replaceAll('/api/', `${MOUNT}/api/`)
    const body = Buffer.from(text, 'utf8')
    headers['content-length'] = String(body.byteLength)
    res.writeHead(status, headers)
    if (req.method === 'HEAD') {
      res.end()
      return
    }
    res.end(body)
  })
  upstreamResponse.on('error', () => {
    if (!res.headersSent) respond(res, 502, 'application/json; charset=utf-8', '{"detail":"选股后端中断"}')
    else res.destroy()
  })
}

async function handleControl(req, res, action) {
  if (action === 'start' && req.method === 'POST') {
    const result = await startBackend()
    respondJson(res, result.status === 'error' ? 500 : 200, result)
    return
  }
  if (action === 'status' && (req.method === 'GET' || req.method === 'HEAD')) {
    respondJson(res, 200, await controlStatus())
    return
  }
  if (action === 'stop' && req.method === 'POST') {
    respondJson(res, 200, stopBackend())
    return
  }
  respondJson(res, 404, { detail: '未知的控制操作' })
}

async function startBackend() {
  if (await probeHealth()) return { status: 'online', already: true, message: '选股后端已在运行' }
  if (startPromise) return startPromise
  startPromise = runStart()
  try {
    return await startPromise
  } finally {
    startPromise = null
  }
}

async function runStart() {
  starting = true
  lastError = ''
  let logFd = null
  try {
    const python = await resolvePython()
    if (!python) {
      lastError =
        '没有找到装好 uvicorn/fastapi 的 Python。可先运行一次 start.bat 建 .venv，或设置环境变量 DSH_QUANT_PYTHON 指向解释器。'
      return { status: 'error', message: lastError, appRoot: APP_ROOT }
    }

    mkdirSync(path.dirname(LOG_PATH), { recursive: true })
    try {
      if (statSync(LOG_PATH).size > LOG_LIMIT) writeFileSync(LOG_PATH, '')
    } catch {
      /* 首次启动时日志文件还不存在 */
    }
    logFd = openSync(LOG_PATH, 'a')
    writeSync(logFd, `\n===== ${new Date().toISOString()} start ${describe(python)} =====\n`)

    const child = spawn(
      python.command,
      [
        ...python.args,
        '-m',
        'uvicorn',
        'backend.main:app',
        '--host',
        UPSTREAM.hostname,
        '--port',
        String(UPSTREAM.port || '8000'),
        '--log-level',
        'warning',
      ],
      {
        cwd: APP_ROOT,
        detached: true,
        windowsHide: true,
        stdio: ['ignore', logFd, logFd],
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
      },
    )
    backendChild = child

    let spawnError = null
    child.on('error', error => {
      spawnError = error
      lastError = `无法启动 Python：${error.message}`
    })
    child.on('exit', (code, signal) => {
      lastExit = { code, signal, at: new Date().toISOString() }
      if (backendChild === child) backendChild = null
    })
    child.unref()

    const deadline = Date.now() + START_TIMEOUT
    while (Date.now() < deadline) {
      if (await probeHealth(PROBE_TIMEOUT)) {
        return {
          status: 'online',
          pid: child.pid ?? null,
          python: describe(python),
          message: '选股后端已启动',
        }
      }
      if (spawnError || child.exitCode !== null) break
      await sleep(700)
    }

    const tail = readLogTail()
    lastError = spawnError
      ? `无法启动 Python：${spawnError.message}`
      : child.exitCode !== null
        ? `后端进程已退出（code=${child.exitCode}）。${tail}`
        : `启动超时（${START_TIMEOUT / 1000} 秒）。${tail}`
    return { status: 'error', message: lastError, log: tail, pid: child.pid ?? null }
  } catch (error) {
    lastError = error?.message ?? String(error)
    return { status: 'error', message: lastError, log: readLogTail() }
  } finally {
    starting = false
    if (logFd !== null) {
      try {
        closeSync(logFd)
      } catch {
        /* 父进程句柄已复制给子进程，关闭失败不影响后端 */
      }
    }
  }
}

async function controlStatus() {
  const online = await probeHealth()
  return {
    online,
    starting,
    managed: Boolean(backendChild && backendChild.exitCode === null),
    pid: backendChild?.pid ?? null,
    python: pythonCache?.value ? describe(pythonCache.value) : null,
    appRoot: APP_ROOT,
    logPath: LOG_PATH,
    logTail: readLogTail(800),
    lastExit,
    error: lastError,
    upstream: UPSTREAM.origin,
  }
}

function stopBackend() {
  if (!backendChild || backendChild.exitCode !== null) {
    return { status: 'idle', message: '没有由插件启动的后端进程；外部启动的进程不会被结束' }
  }
  const pid = backendChild.pid
  try {
    process.kill(pid)
  } catch (error) {
    return { status: 'error', message: error?.message ?? String(error) }
  }
  backendChild = null
  return { status: 'stopped', pid }
}

function probeHealth(timeout = PROBE_TIMEOUT) {
  return new Promise(resolve => {
    let settled = false
    const finish = value => {
      if (settled) return
      settled = true
      resolve(value)
    }
    const request = http.request(
      {
        hostname: UPSTREAM.hostname,
        port: UPSTREAM.port || 80,
        path: '/api/health',
        method: 'GET',
        timeout,
      },
      response => {
        response.resume()
        finish(response.statusCode === 200)
      },
    )
    request.on('timeout', () => {
      request.destroy()
      finish(false)
    })
    request.on('error', () => finish(false))
    request.end()
  })
}

function findAppRoot() {
  const explicit = process.env.DSH_QUANT_ROOT
  if (explicit && existsSync(path.join(explicit, 'backend', 'main.py'))) return path.resolve(explicit)
  let dir = PLUGIN_DIR
  for (let depth = 0; depth < 6; depth += 1) {
    if (existsSync(path.join(dir, 'backend', 'main.py'))) return dir
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return path.resolve(PLUGIN_DIR, '..')
}

function pythonCandidates() {
  const candidates = []
  const explicit = process.env.DSH_QUANT_PYTHON
  if (explicit) candidates.push({ command: explicit, args: [] })
  const venv = path.join(APP_ROOT, '.venv', 'Scripts', 'python.exe')
  if (existsSync(venv)) candidates.push({ command: venv, args: [] })
  const local = path.join(homedir(), 'python', 'python.exe')
  if (existsSync(local)) candidates.push({ command: local, args: [] })
  candidates.push({ command: 'python', args: [] })
  candidates.push({ command: 'py', args: ['-3'] })
  return candidates
}

async function resolvePython() {
  if (pythonCache && Date.now() - pythonCache.at < 60_000) return pythonCache.value
  for (const candidate of pythonCandidates()) {
    if (await probePython(candidate)) {
      pythonCache = { at: Date.now(), value: candidate }
      return candidate
    }
  }
  pythonCache = { at: Date.now(), value: null }
  return null
}

function probePython(candidate) {
  return new Promise(resolve => {
    let child
    try {
      child = spawn(candidate.command, [...candidate.args, '-c', 'import uvicorn, fastapi'], {
        cwd: APP_ROOT,
        windowsHide: true,
        stdio: 'ignore',
      })
    } catch {
      resolve(false)
      return
    }
    let settled = false
    const finish = value => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(value)
    }
    const timer = setTimeout(() => {
      try {
        child.kill()
      } catch {
        /* 已在退出 */
      }
      finish(false)
    }, PREFLIGHT_TIMEOUT)
    child.on('error', () => finish(false))
    child.on('exit', code => finish(code === 0))
  })
}

function describe(python) {
  return [python.command, ...python.args].join(' ')
}

function readLogTail(limit = 1200) {
  try {
    return readFileSync(LOG_PATH, 'utf8').slice(-limit)
  } catch {
    return ''
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function respond(res, status, type, body) {
  if (res.headersSent) {
    res.destroy()
    return
  }
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' })
  res.end(body)
}

function respondJson(res, status, payload) {
  respond(res, status, 'application/json; charset=utf-8', JSON.stringify(payload))
}
