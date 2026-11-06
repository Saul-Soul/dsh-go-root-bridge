// Root 桥接：权限状态 + 无线调试配对 + adb root 通道。
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const run = promisify(execFile)
const DIR = dirname(fileURLToPath(import.meta.url))
const PROBES = join(DIR, '..', 'probes.py')
const ADB = '/usr/bin/adb'

export const name = 'dsh-go-root-bridge'

let last = null
let st = {
  connAddr: '192.168.0.3:42785',
  pairAddr: '',
  pairCode: '',
  paired: false,
  root: null,
  uidOut: '',
  log: '',
}

const sh = (cmd, args, timeout = 15000) =>
  run(cmd, args, { timeout }).then(r => String(r.stdout || '') + String(r.stderr || '')).catch(e =>
    'ERR: ' + String((e && (e.stderr || e.message)) || e).slice(0, 160))

async function probe() {
  try {
    const { stdout } = await run('python3', [PROBES], { timeout: 20000 })
    last = JSON.parse(stdout)
  } catch (e) {
    last = { error: String((e && e.message) || e).slice(0, 80) }
  }
  return last
}

async function doPair(v) {
  const pairAddr = String(v.pairAddr || st.pairAddr || '').trim()
  const code = String(v.pairCode || st.pairCode || '').trim()
  const connAddr = String(v.connAddr || st.connAddr || '').trim()
  const ip = pairAddr.split(':')[0]
  const lines = []
  if (!pairAddr.includes(':') || !/^\d{6}$/.test(code)) {
    st.paired = false
    return { ok: false, log: '格式不对：配对地址要带端口，配对码是 6 位数字' }
  }
  lines.push('$ adb pair ' + pairAddr)
  lines.push(await sh(ADB, ['pair', pairAddr, code], 15000))
  if (!/success|paired/i.test(lines[lines.length - 1])) {
    st.paired = false
    return { ok: false, log: lines.join('\n') }
  }
  lines.push('$ adb connect ' + connAddr)
  lines.push(await sh(ADB, ['connect', connAddr], 10000))
  if (!/connected|already/i.test(lines[lines.length - 1])) {
    st.paired = false
    return { ok: false, log: lines.join('\n') }
  }
  st.paired = true
  lines.push('$ adb shell id')
  const idOut = await sh(ADB, ['-s', connAddr, 'shell', 'id'], 10000)
  lines.push(idOut.trim())
  st.uidOut = idOut.trim().slice(0, 80)
  st.root = /uid=0/.test(idOut)
  if (!st.root) {
    lines.push('$ adb root && 重连')
    lines.push(await sh(ADB, ['-s', connAddr, 'root'], 10000))
    await new Promise(r => setTimeout(r, 1200))
    lines.push(await sh(ADB, ['connect', connAddr], 10000))
    const id2 = await sh(ADB, ['-s', connAddr, 'shell', 'id'], 10000)
    lines.push(id2.trim())
    st.uidOut = id2.trim().slice(0, 80)
    st.root = /uid=0/.test(id2)
  }
  return { ok: st.root, log: lines.join('\n').slice(0, 1800) }
}

function decorate(d) {
  const bridgeOnline = d.bridge === 'online'
  const root = bridgeOnline || !!d.root
  return {
    rootLabel: bridgeOnline ? ('root ✓（桥接 uid ' + (d.bridge_uid || 0) + '）')
      : root ? '已提权' : '未提权',
    hookLabel: d.stat_hook ? '生效' : '未生效',
    uidLabel: String(d.uid ?? '?'),
    rebootLabel: d.reboot_errno === 0 ? '通' : ('errno ' + d.reboot_errno),
    fdLabel: d.driver_fd === null || d.driver_fd === undefined ? '无' : String(d.driver_fd),
    adbLabel: '不需要（内核通道已通）',
    note: bridgeOnline
      ? 'Root 桥在线：全盘文件可读。root 操作由 Agent 走 bc.py，仍经既有审批。'
      : (root
        ? '已提权。'
        : '桥未在线：点「重新探测」会自动拉起插件自带的守护进程，无需配对。'),
  }
}

function report(d) {
  const lines = []
  lines.push('uid: ' + (d.uid ?? '?') + (d.root ? ' (root)' : ''))
  lines.push('stat 钩子: ' + (d.stat_hook ? '生效' : '未生效'))
  lines.push('reboot: errno ' + (d.reboot_errno ?? '?'))
  lines.push('adb: ' + (st.root === null ? '未连接' : (st.root ? 'root' : '未提权')))
  if (st.uidOut) lines.push('adb id: ' + st.uidOut)
  if (d.error) lines.push('错误: ' + d.error)
  return lines.join('\n')
}

function zh(caps) { return !String(caps && caps.locale || 'zh').startsWith('en') }

export function apply(ctx) {
  ctx.inject(['dshGo'], (ctx) => {
    ctx.dshGo.panel('perms', {
      view: (scope, caps) => {
        const Z = zh(caps)
        const t = (a, b) => (Z ? a : b)
        return { v: 2, type: 'stack', title: t('权限状态', 'Permissions'), children: [
          { type: 'metric', label: t('提权状态', 'Root'), value: { $: 'data.rootLabel' } },
          { type: 'keyValue', items: [
            { label: t('内核钩子', 'hook'), value: { $: 'data.hookLabel' } },
            { label: 'adb', value: { $: 'data.adbLabel' } },
            { label: 'uid', value: { $: 'data.uidLabel' } },
            { label: t('reboot 通道', 'reboot'), value: { $: 'data.rebootLabel' } },
          ] },
          { type: 'callout', tone: 'info', text: { $: 'data.note' } },
          { type: 'form', label: t('无线调试配对', 'Wireless pairing'),
            submit: 'pair', submitLabel: t('配对并连接', 'Pair & connect'),
            children: [
              { type: 'textField', label: t('连接地址', 'Connect addr'),
                bind: 'data.connAddr', placeholder: '192.168.0.3:42785', required: true },
              { type: 'textField', label: t('配对地址', 'Pair addr'),
                bind: 'data.pairAddr', placeholder: '192.168.0.3:配对端口', required: true },
              { type: 'textField', label: t('配对码', 'Code'),
                bind: 'data.pairCode', placeholder: t('6 位数字', '6 digits'),
                required: true, maxLength: 6 },
            ] },
          { type: 'actions', items: [
            { label: t('重新探测', 'Re-probe'), action: 'reprobe', style: 'primary' },
            { label: t('复制报告', 'Copy'), action: 'copyreport' },
            { label: t('插入结论', 'Insert'), action: 'insert' },
          ] },
        ] }
      },
      data: (scope, emit, caps) => {
        emit(decorate({ ...(last || {}), ...{ root: st.root === true || (last && last.root) } }))
        emit({ ...decorate({ ...(last || {}), ...{ root: st.root === true || (last && last.root) } }),
          connAddr: st.connAddr, pairAddr: st.pairAddr, pairCode: st.pairCode })
        probe().then((d) => {
          emit(decorate({ ...d, root: st.root === true || d.root }))
        }).catch(() => {})
        return () => {}
      },
      actions: {
        pair: (scope, event) => {
          const v = (event && event.value) || {}
          return {
            pending: { label: '正在配对…', timeoutMs: 60000 },
            result: async (signal) => {
              const r = await doPair(v)
              return { toast: r.ok ? '配对成功，adb 已 root' : '配对失败，看日志', patch: {
                ...decorate(last || {}), adbLabel: st.root ? 'root ✓' : '失败', note: r.log } }
            },
          }
        },
        reprobe: () => ({ refresh: true, toast: '正在重新探测…' }),
        copyreport: () => last ? { copy: report(last), toast: '报告已复制' } : { toast: '没有结果' },
        insert: () => last ? { insert: '[Root 桥接]\n' + report(last) } : { toast: '没有结果' },
      },
    })
  })
}
