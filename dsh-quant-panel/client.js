window.__ModuleLoader__.load({
  id: '@local/dsh-quant-panel',
  factory(require) {
    const React = require('react')
    const h = React.createElement

    /** Kept in step with the Host half's mount; both are part of this bundle. */
    const MOUNT = '/quant'
    const HEALTH_URL = `${MOUNT}/api/health`
    const START_URL = `${MOUNT}/control/start`
    const APP_URL = `${MOUNT}/`
    const NAMESPACE = 'quantPanel'
    const START_COMMAND = 'python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000'
    const PROBE_INTERVAL = 30000
    const PROBE_TIMEOUT = 4000

    const ZH = {
      panelTitle: '选股',
      panelHeading: '0925 选股',
      panelSubtitle: '集合竞价 · 弱转强',
      statusOnline: '已连接',
      statusOffline: '未连接',
      statusChecking: '连接中',
      refresh: '刷新',
      openExternal: '新窗口打开',
      frameTitle: '0925 集合竞价选股界面',
      offlineTitle: '选股后端未启动',
      offlineBody: '选股界面读取本机 8000 端口的选股服务。点下面的一键启动即可，不需要开命令行。',
      startBackend: '一键启动',
      startingBackend: '正在启动…',
      startHint: '正在后台启动选股服务（Python · uvicorn），首次启动约 5—15 秒，成功后自动进入界面。',
      startFailed: '启动失败',
      retryStart: '重试启动',
      manualHint: '自动启动失败时，也可以在项目目录运行 start.bat：',
      retry: '重新连接',
      copy: '复制命令',
      copied: '已复制',
      disconnected: '与选股后端的连接中断，行情与图表暂时无法刷新。',
      reconnect: '重新连接',
      closePanel: '返回对话',
    }

    const EN = {
      panelTitle: 'Screener',
      panelHeading: '0925 Screener',
      panelSubtitle: 'Call auction · reversal',
      statusOnline: 'Connected',
      statusOffline: 'Offline',
      statusChecking: 'Connecting',
      refresh: 'Refresh',
      openExternal: 'Open in tab',
      frameTitle: '0925 call-auction stock screener',
      offlineTitle: 'Screener backend is not running',
      offlineBody: 'The panel reads the screener service on local port 8000. Start it here with one click; no terminal needed.',
      startBackend: 'Start backend',
      startingBackend: 'Starting…',
      startHint: 'Starting the local screener (Python · uvicorn) in the background. First start takes 5–15 seconds.',
      startFailed: 'Start failed',
      retryStart: 'Retry start',
      manualHint: 'If automatic start fails, run start.bat in the project folder:',
      retry: 'Reconnect',
      copy: 'Copy command',
      copied: 'Copied',
      disconnected: 'Lost the connection to the screener backend; quotes and charts cannot refresh.',
      reconnect: 'Reconnect',
      closePanel: 'Back to chat',
    }

    const STYLES = `
.dsh-quant-panel{display:flex;flex-direction:column;height:100%;min-height:360px;box-sizing:border-box;padding-top:var(--dsh-frame-top-clearance,0px);background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary)}
.dsh-quant-bar{display:flex;flex:0 0 auto;align-items:center;gap:10px;min-height:44px;padding:0 12px 0 16px;border-bottom:1px solid var(--dsw-alias-border-l1)}
.dsh-quant-mark{display:inline-flex;flex:0 0 auto;align-items:center;justify-content:center;width:26px;height:26px;border-radius:8px;background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-brand-primary)}
.dsh-quant-headings{display:flex;flex-direction:column;gap:1px;min-width:0}
.dsh-quant-title{font-size:13px;font-weight:600;line-height:1.25;white-space:nowrap}
.dsh-quant-subtitle{overflow:hidden;font-size:11px;line-height:1.25;color:var(--dsw-alias-label-secondary);text-overflow:ellipsis;white-space:nowrap}
.dsh-quant-spacer{flex:1 1 auto}
.dsh-quant-status{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 9px;border-radius:999px;background:var(--dsw-alias-bg-layer-2);font-size:11px;color:var(--dsw-alias-label-secondary);white-space:nowrap}
.dsh-quant-dot{width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-state-idle-primary)}
.dsh-quant-dot[data-state='online']{background:var(--dsw-alias-state-success-primary)}
.dsh-quant-dot[data-state='offline']{background:var(--dsw-alias-state-error-primary)}
.dsh-quant-action{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px;border:1px solid var(--dsw-alias-border-l1);border-radius:6px;background:transparent;color:var(--dsw-alias-label-secondary);font:inherit;font-size:12px;white-space:nowrap;cursor:pointer}
.dsh-quant-action:hover{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}
.dsh-quant-action:focus-visible{outline:2px solid var(--dsw-alias-brand-primary);outline-offset:1px}
.dsh-quant-action:disabled{opacity:.55;cursor:default}
.dsh-quant-action[data-tone='primary']{border-color:transparent;background:var(--dsw-alias-brand-primary);color:#fff}
.dsh-quant-icon-action{width:28px;padding:0;justify-content:center}
.dsh-quant-body{position:relative;display:flex;flex:1 1 auto;flex-direction:column;min-height:0}
.dsh-quant-frame{display:block;flex:1 1 auto;width:100%;height:100%;min-height:0;border:0;background:#fff}
.dsh-quant-banner{display:flex;flex:0 0 auto;align-items:center;gap:10px;padding:6px 16px;border-bottom:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-2);font-size:12px;color:var(--dsw-alias-label-secondary)}
.dsh-quant-banner .dsh-quant-dot{flex:0 0 auto}
.dsh-quant-empty{display:flex;flex:1 1 auto;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:32px 24px;overflow:auto;text-align:center}
.dsh-quant-empty-icon{display:flex;align-items:center;justify-content:center;width:54px;height:54px;border-radius:15px;background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-state-warn-primary)}
.dsh-quant-empty h2{margin:2px 0 0;font-size:15px;font-weight:600;line-height:1.4}
.dsh-quant-empty p{max-width:460px;margin:0;font-size:12.5px;line-height:1.7;color:var(--dsw-alias-label-secondary)}
.dsh-quant-starting{margin:0;font-size:12px;line-height:1.6;color:var(--dsw-alias-label-secondary)}
.dsh-quant-error{margin:0;font-size:12px;font-weight:600;color:var(--dsw-alias-state-error-primary)}
.dsh-quant-log{max-width:min(560px,100%);max-height:150px;overflow:auto;margin:0;padding:8px 10px;border:1px solid var(--dsw-alias-border-l1);border-radius:8px;background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);font-family:var(--dsh-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:10.5px;line-height:1.55;text-align:left;white-space:pre-wrap;overflow-wrap:anywhere}
.dsh-quant-command{display:flex;align-items:center;gap:10px;max-width:min(560px,100%);padding:8px 10px;border:1px solid var(--dsw-alias-border-l1);border-radius:8px;background:var(--dsw-alias-bg-layer-1)}
.dsh-quant-command code{overflow-wrap:anywhere;text-align:left;font-family:var(--dsh-font-family-code,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:11.5px;line-height:1.6;color:var(--dsw-alias-label-primary)}
.dsh-quant-actions{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:6px}
@media (max-width:720px){.dsh-quant-subtitle{display:none}.dsh-quant-status{display:none}}
`

    let translate = null
    let layout = null

    function t(key) {
      if (translate) {
        const value = translate(key)
        if (typeof value === 'string' && value.length > 0 && value !== key) return value
      }
      return ZH[key] ?? key
    }

    /** 16px sidebar glyph: two candles, drawn for the rail's icon cell. */
    function QuantIcon(props) {
      const size = props && typeof props.size === 'number' ? props.size : 16
      return h(
        'svg',
        {
          width: size,
          height: size,
          viewBox: '0 0 16 16',
          fill: 'none',
          'aria-hidden': 'true',
          focusable: 'false',
          style: { display: 'block', color: 'currentColor' },
        },
        h('path', {
          d: 'M4.5 1.4v13.2',
          stroke: 'currentColor',
          strokeWidth: 1.25,
          strokeLinecap: 'round',
        }),
        h('rect', {
          x: 2.35,
          y: 4.4,
          width: 4.3,
          height: 5.8,
          rx: 0.9,
          stroke: 'currentColor',
          strokeWidth: 1.25,
        }),
        h('path', {
          d: 'M11.5 2.2v11.5',
          stroke: 'currentColor',
          strokeWidth: 1.25,
          strokeLinecap: 'round',
        }),
        h('rect', {
          x: 9.35,
          y: 6.5,
          width: 4.3,
          height: 4.3,
          rx: 0.9,
          stroke: 'currentColor',
          strokeWidth: 1.25,
        }),
      )
    }

    function CandleMark() {
      return h(
        'svg',
        {
          width: 18,
          height: 18,
          viewBox: '0 0 24 24',
          fill: 'none',
          'aria-hidden': 'true',
          focusable: 'false',
        },
        h('path', {
          d: 'M7 3.5v17',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
        }),
        h('rect', {
          x: 4,
          y: 7.5,
          width: 6,
          height: 8,
          rx: 1.3,
          stroke: 'currentColor',
          strokeWidth: 1.6,
        }),
        h('path', {
          d: 'M17 5v15',
          stroke: 'currentColor',
          strokeWidth: 1.6,
          strokeLinecap: 'round',
        }),
        h('rect', {
          x: 14,
          y: 10,
          width: 6,
          height: 6,
          rx: 1.3,
          stroke: 'currentColor',
          strokeWidth: 1.6,
        }),
      )
    }

    function RefreshGlyph() {
      return h(
        'svg',
        { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': 'true', focusable: 'false' },
        h('path', {
          d: 'M13.2 8a5.2 5.2 0 1 1-1.6-3.75',
          stroke: 'currentColor',
          strokeWidth: 1.35,
          strokeLinecap: 'round',
        }),
        h('path', {
          d: 'M13.4 2.4v3.2h-3.2',
          stroke: 'currentColor',
          strokeWidth: 1.35,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
        }),
      )
    }

    function ExternalGlyph() {
      return h(
        'svg',
        { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': 'true', focusable: 'false' },
        h('path', {
          d: 'M9.2 2.6h4.2v4.2',
          stroke: 'currentColor',
          strokeWidth: 1.35,
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
        }),
        h('path', {
          d: 'M13.4 2.6 7.9 8.1',
          stroke: 'currentColor',
          strokeWidth: 1.35,
          strokeLinecap: 'round',
        }),
        h('path', {
          d: 'M12.1 9.6v2.8a1 1 0 0 1-1 1H3.6a1 1 0 0 1-1-1V4.9a1 1 0 0 1 1-1h2.8',
          stroke: 'currentColor',
          strokeWidth: 1.35,
          strokeLinecap: 'round',
        }),
      )
    }

    function CopyGlyph() {
      return h(
        'svg',
        { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': 'true', focusable: 'false' },
        h('rect', {
          x: 5.4,
          y: 5.4,
          width: 7.2,
          height: 7.2,
          rx: 1.2,
          stroke: 'currentColor',
          strokeWidth: 1.3,
        }),
        h('path', {
          d: 'M10.6 5.4V4.4a1 1 0 0 0-1-1H4.4a1 1 0 0 0-1 1v5.2a1 1 0 0 0 1 1h1',
          stroke: 'currentColor',
          strokeWidth: 1.3,
          strokeLinecap: 'round',
        }),
      )
    }

    function CloseGlyph() {
      return h(
        'svg',
        { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': 'true', focusable: 'false' },
        h('path', {
          d: 'M4.2 4.2 11.8 11.8M11.8 4.2 4.2 11.8',
          stroke: 'currentColor',
          strokeWidth: 1.4,
          strokeLinecap: 'round',
        }),
      )
    }

    function PowerGlyph() {
      return h(
        'svg',
        { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', 'aria-hidden': 'true', focusable: 'false' },
        h('path', {
          d: 'M8 2.4v5.4',
          stroke: 'currentColor',
          strokeWidth: 1.4,
          strokeLinecap: 'round',
        }),
        h('path', {
          d: 'M4.7 4.6a4.7 4.7 0 1 0 6.6 0',
          stroke: 'currentColor',
          strokeWidth: 1.4,
          strokeLinecap: 'round',
        }),
      )
    }

    function ActionButton(props) {
      return h(
        'button',
        {
          type: 'button',
          className: 'dsh-quant-action',
          'data-tone': props.tone,
          onClick: props.onClick,
          title: props.title,
          disabled: props.disabled === true,
        },
        props.icon ?? null,
        props.label ? h('span', null, props.label) : null,
      )
    }

    function QuantPanel() {
      const [phase, setPhase] = React.useState('checking')
      const [booted, setBooted] = React.useState(false)
      const [generation, setGeneration] = React.useState(0)
      const [copied, setCopied] = React.useState(false)
      const [launch, setLaunch] = React.useState('idle')
      const [launchDetail, setLaunchDetail] = React.useState('')
      const alive = React.useRef(true)
      const copyTimer = React.useRef(null)

      const probe = React.useCallback(async () => {
        const controller = new AbortController()
        const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT)
        try {
          const response = await fetch(HEALTH_URL, { cache: 'no-store', signal: controller.signal })
          if (!alive.current) return response.ok
          setPhase(response.ok ? 'online' : 'offline')
          if (response.ok) setBooted(true)
          return response.ok
        } catch (_) {
          if (alive.current) setPhase('offline')
          return false
        } finally {
          clearTimeout(timeout)
        }
      }, [])

      React.useEffect(() => {
        alive.current = true
        probe()
        const interval = setInterval(probe, PROBE_INTERVAL)
        return () => {
          alive.current = false
          clearInterval(interval)
          if (copyTimer.current) clearTimeout(copyTimer.current)
        }
      }, [probe])

      const reload = React.useCallback(() => {
        setGeneration(value => value + 1)
        probe()
      }, [probe])

      const openExternal = React.useCallback(() => {
        window.open(APP_URL, '_blank', 'noopener,noreferrer')
      }, [])

      const closePanel = React.useCallback(() => {
        if (layout && typeof layout.selectPanel === 'function') layout.selectPanel(null)
      }, [])

      const startBackend = React.useCallback(async () => {
        if (launch === 'starting') return
        setLaunch('starting')
        setLaunchDetail('')
        try {
          const response = await fetch(START_URL, { method: 'POST' })
          const data = await response.json().catch(() => ({}))
          if (!alive.current) return
          if (response.ok && (data.status === 'online' || data.already)) {
            setLaunch('idle')
            setPhase('online')
            setBooted(true)
            setGeneration(value => value + 1)
            return
          }
          setLaunch('error')
          setLaunchDetail(
            response.status === 404
              ? '启动接口未加载：请刷新 Harness 页面后再试。'
              : data.message || data.detail || `启动失败（HTTP ${response.status}）`,
          )
        } catch (error) {
          if (!alive.current) return
          setLaunch('error')
          setLaunchDetail(String(error?.message || error))
        }
      }, [launch])

      const copyCommand = React.useCallback(async () => {
        try {
          await navigator.clipboard.writeText(START_COMMAND)
          setCopied(true)
          if (copyTimer.current) clearTimeout(copyTimer.current)
          copyTimer.current = setTimeout(() => {
            if (alive.current) setCopied(false)
          }, 1800)
        } catch (_) {
          /* Clipboard access is optional; the command stays selectable. */
        }
      }, [])

      const statusKey = phase === 'online' ? 'statusOnline' : phase === 'offline' ? 'statusOffline' : 'statusChecking'

      const offline = phase === 'offline'
      const banner =
        booted && offline
          ? h(
              'div',
              { className: 'dsh-quant-banner', role: 'status' },
              h('span', { className: 'dsh-quant-dot', 'data-state': 'offline' }),
              h('span', null, t('disconnected')),
              h('span', { className: 'dsh-quant-spacer' }),
              h(ActionButton, {
                label: launch === 'starting' ? t('startingBackend') : t('startBackend'),
                onClick: startBackend,
                icon: h(PowerGlyph, null),
                tone: 'primary',
                disabled: launch === 'starting',
              }),
              h(ActionButton, { label: t('reconnect'), onClick: reload, icon: h(RefreshGlyph, null) }),
            )
          : null

      return h(
        'div',
        { className: 'dsh-quant-panel' },
        h('style', null, STYLES),
        h(
          'header',
          { className: 'dsh-quant-bar' },
          h('span', { className: 'dsh-quant-mark' }, h(CandleMark, null)),
          h(
            'div',
            { className: 'dsh-quant-headings' },
            h('span', { className: 'dsh-quant-title' }, t('panelHeading')),
            h('span', { className: 'dsh-quant-subtitle' }, t('panelSubtitle')),
          ),
          h('span', { className: 'dsh-quant-spacer' }),
          h(
            'span',
            { className: 'dsh-quant-status', role: 'status' },
            h('span', { className: 'dsh-quant-dot', 'data-state': phase }),
            t(statusKey),
          ),
          h(ActionButton, { label: t('refresh'), onClick: reload, icon: h(RefreshGlyph, null) }),
          h(ActionButton, { label: t('openExternal'), onClick: openExternal, icon: h(ExternalGlyph, null) }),
          layout
            ? h(
                'button',
                {
                  type: 'button',
                  className: 'dsh-quant-action dsh-quant-icon-action',
                  onClick: closePanel,
                  title: t('closePanel'),
                  'aria-label': t('closePanel'),
                },
                h(CloseGlyph, null),
              )
            : null,
        ),
        h(
          'div',
          { className: 'dsh-quant-body' },
          booted
            ? h(
                React.Fragment,
                null,
                banner,
                h('iframe', {
                  key: generation,
                  className: 'dsh-quant-frame',
                  src: APP_URL,
                  title: t('frameTitle'),
                  allow: 'clipboard-write',
                }),
              )
            : h(
                'div',
                { className: 'dsh-quant-empty' },
                h('span', { className: 'dsh-quant-empty-icon' }, h(CandleMark, null)),
                h('h2', null, t('offlineTitle')),
                h('p', null, t('offlineBody')),
                launch === 'starting' ? h('p', { className: 'dsh-quant-starting', role: 'status' }, t('startHint')) : null,
                launch === 'error'
                  ? h(
                      React.Fragment,
                      null,
                      h('p', { className: 'dsh-quant-error' }, t('startFailed')),
                      h('pre', { className: 'dsh-quant-log' }, launchDetail || t('startFailed')),
                    )
                  : null,
                h(
                  'div',
                  { className: 'dsh-quant-actions' },
                  h(ActionButton, {
                    label:
                      launch === 'starting'
                        ? t('startingBackend')
                        : launch === 'error'
                          ? t('retryStart')
                          : t('startBackend'),
                    onClick: startBackend,
                    icon: h(PowerGlyph, null),
                    tone: 'primary',
                    disabled: launch === 'starting',
                  }),
                  h(ActionButton, { label: t('retry'), onClick: reload, icon: h(RefreshGlyph, null) }),
                  h(ActionButton, { label: t('openExternal'), onClick: openExternal, icon: h(ExternalGlyph, null) }),
                ),
                h('p', { className: 'dsh-quant-starting' }, t('manualHint')),
                h(
                  'div',
                  { className: 'dsh-quant-command' },
                  h('code', null, START_COMMAND),
                  h(ActionButton, { label: t(copied ? 'copied' : 'copy'), onClick: copyCommand, icon: h(CopyGlyph, null) }),
                ),
              ),
        ),
      )
    }

    return {
      inject: ['slots', 'locale', 'layout'],
      apply(ctx) {
        const locale = ctx.get('locale')
        layout = ctx.get('layout') ?? null
        if (locale && typeof locale.register === 'function') {
          ctx.effect(() => {
            const dispose = locale.register(NAMESPACE, { zh: ZH, en: EN })
            return typeof dispose === 'function' ? dispose : () => {}
          }, 'quant-panel: locale dictionary')
        }
        if (locale && typeof locale.bind === 'function') {
          try {
            translate = locale.bind(NAMESPACE)
          } catch (_) {
            translate = null
          }
        }

        ctx.slots.inject('sidebar.panellist', () =>
          ctx.slots.register(
            { name: 'sidebar.panellist', id: 'quant', order: 20, label: () => t('panelTitle') },
            QuantIcon,
          ),
        )
        ctx.slots.inject('main', () =>
          ctx.slots.register({ name: 'main', key: 'quant' }, QuantPanel),
        )
      },
    }
  },
})
