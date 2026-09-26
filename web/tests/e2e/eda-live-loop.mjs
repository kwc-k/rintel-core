// Real Chrome + isolated SQLite + actual MCP write, not a jsdom component test.
import { chromium } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createServer } from 'node:net'

const root = resolve(import.meta.dirname, '../../..')
const fixture = join(root, 'tests/helpers/eda_browser_fixture.py')
const isolated = mkdtempSync(join(tmpdir(), 'rintel-eda-browser-'))
const multiport = process.env.RINTEL_EDA_MULTIPORT === '1'
const screenshot = resolve(root, multiport
  ? 'analysis_tournament/rintel_core_loop0/eda-multiport-browser.png'
  : 'analysis_tournament/rintel_core_loop0/eda-live-browser.png')
const popoverScreenshot = resolve(root, 'analysis_tournament/rintel_core_loop0/eda-port-type-popover.png')
mkdirSync(resolve(root, 'analysis_tournament/rintel_core_loop0'), { recursive: true })

function python(...args) {
  const run = spawnSync('uv', ['run', '--group', 'dev', 'python', fixture, ...args], {
    cwd: root, encoding: 'utf8', timeout: 30000,
  })
  if (run.status !== 0) throw new Error(`fixture failed: ${run.stderr || run.stdout}`)
  return JSON.parse(run.stdout.trim().split('\n').at(-1))
}

async function freePort() {
  const server = createServer()
  await new Promise((done) => server.listen(0, '127.0.0.1', done))
  const port = server.address().port
  await new Promise((done) => server.close(done))
  return port
}

const initial = python('setup', isolated)
const port = await freePort()
const url = `http://127.0.0.1:${port}`
const backend = spawn('uv', ['run', '--group', 'dev', 'python', '-m', 'uvicorn',
  'rintel.server.app:app', '--host', '127.0.0.1', '--port', String(port)], {
  cwd: root,
  env: { ...process.env, RINTEL_SQLITE_PATH: join(isolated, 'rintel.db'),
    RINTEL_WEB_DIST_PATH: join(root, 'web/dist'),
    RINTEL_BUILD_ARTIFACTS_PATH: join(isolated, 'build'),
    RINTEL_EXECUTION_ARTIFACTS_PATH: join(isolated, 'execution'),
    RINTEL_GIT_WORKSPACE_STATE_PATH: join(isolated, 'git') },
  stdio: 'pipe',
})
let backendLog = ''
backend.stderr.on('data', (chunk) => { backendLog += chunk.toString() })
let browser
try {
  let ready = false
  for (let n = 0; n < 60; n++) {
    try { if ((await fetch(`${url}/api/v1/docs`)).ok) { ready = true; break } }
    catch { /* startup */ }
    await new Promise((done) => setTimeout(done, 200))
  }
  if (!ready) throw new Error(`backend unavailable: ${backendLog}`)
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await page.goto(`${url}/flows/${initial.flow_id}`)
  await page.locator('.flow-block-node').filter({ hasText: 'Source' }).waitFor()
  await page.locator('.flow-block-node').filter({ hasText: 'Source' }).click()
  await page.locator('.flow-inspector').getByText(`node ${initial.source_id}`, { exact: false }).waitFor()
  await page.locator('.flow-inspector .fi-port-contract summary').first().click()
  await page.locator('.flow-inspector').getByText('ACTUAL: UNKNOWN', { exact: false }).waitFor()
  const initialFlow = await (await fetch(`${url}/api/v1/flows/${initial.flow_id}`)).json()

  const changed = python('mutate', isolated, JSON.stringify(initial))
  const expanded = multiport ? python('expand', isolated, JSON.stringify(changed)) : null
  const finalState = expanded ?? changed
  await page.locator('.flow-block-node').filter({ hasText: 'Sink' }).waitFor({ timeout: 12000 })
  await page.getByTestId('harness-design-operation')
    .filter({ hasText: 'Harness last recorded: add_net' }).waitFor()
  await page.locator('.flow-block-node').filter({ hasText: 'Sink' }).click()
  await page.locator('.flow-inspector').getByText(`node ${changed.sink_id}`, { exact: false }).waitFor()
  const sinkDetails = page.locator('.flow-inspector .fi-port-contract')
    .filter({ hasText: changed.sink_port_id })
  if (!(await sinkDetails.evaluate((element) => element.open))) {
    await sinkDetails.locator('summary').click()
  }
  await page.locator('.flow-inspector').getByText(`port_id: ${changed.sink_port_id}`, { exact: false }).waitFor()
  const live = await (await fetch(`${url}/api/v1/flows/${initial.flow_id}`)).json()
  if (live.eda.revision === initialFlow.eda.revision ||
      !live.eda.nets.some((net) => net.id === changed.net_id)) {
    throw new Error('browser backend did not expose the external net revision')
  }
  if (live.design_activity?.operation !== 'add_net' ||
      live.design_activity?.actor !== 'agent:rintel-mcp' ||
      live.design_activity?.design_revision !== finalState.revision ||
      !live.design_activity?.receipt_id) {
    throw new Error('harness operation lacks exact DesignLifecycle receipt binding')
  }
  await page.locator(`[data-cell-id="${changed.net_id}"]`).waitFor({ timeout: 5000 })
  const geometry = await page.evaluate(({ sourcePortId, targetPortId, netId }) => {
    const info = (element) => {
      if (!element) return null
      const rect = element.getBoundingClientRect()
      return { html: element.outerHTML.slice(0, 280),
        x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    }
    const sourceCircle = document.querySelector(`circle[port="${sourcePortId}"]`)
    const targetCircle = document.querySelector(`circle[port="${targetPortId}"]`)
    const path = document.querySelector(`[data-cell-id="${netId}"] path`)
    const edge = window.__flowGraph?.getCellById(netId)
    const screenPoint = (length) => path.getPointAtLength(length).matrixTransform(path.getScreenCTM())
    const center = (element) => {
      const rect = element.getBoundingClientRect()
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }
    }
    const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
    return { sourceLabel: info(document.querySelector(`[data-port-id="${sourcePortId}"]`)),
      targetLabel: info(document.querySelector(`[data-port-id="${targetPortId}"]`)),
      x6Ports: [...document.querySelectorAll('.x6-port')].map(info),
      edge: info(document.querySelector(`[data-cell-id="${netId}"]`)),
      edgePath: path?.getAttribute('d'),
      sourceGroup: sourceCircle?.getAttribute('port-group'),
      targetGroup: targetCircle?.getAttribute('port-group'),
      sourcePortId: edge?.getSourcePortId(), targetPortId: edge?.getTargetPortId(),
      sourceEndpointDistance: distance(screenPoint(0), center(sourceCircle)),
      targetEndpointDistance: distance(screenPoint(path.getTotalLength()), center(targetCircle)) }
  }, { sourcePortId: initial.source_port_id, targetPortId: changed.sink_port_id,
    netId: changed.net_id })
  if (geometry.sourcePortId !== initial.source_port_id ||
      geometry.targetPortId !== changed.sink_port_id ||
      geometry.sourceGroup !== 'data-out' || geometry.targetGroup !== 'data-in' ||
      geometry.sourceEndpointDistance > 12 || geometry.targetEndpointDistance > 12) {
    throw new Error(`net is not anchored OUT→IN at actual port handles: ${JSON.stringify(geometry)}`)
  }
  let multiportGeometry = null
  if (expanded) {
    await page.getByTestId(`port-name-${expanded.matrix_out_id}`).waitFor()
    await page.getByTestId(`port-name-${expanded.matrix_in_id}`).waitFor()
    await page.getByTestId(`port-name-${expanded.gate_out_id}`).waitFor()
    await page.getByTestId(`port-name-${expanded.gate_in_id}`).waitFor()
    await page.locator(`[data-cell-id="${expanded.matrix_net_id}"]`).waitFor()
    await page.locator(`[data-cell-id="${expanded.gate_net_id}"]`).waitFor()
    multiportGeometry = await page.evaluate(({ pairs, firstSource, firstTarget }) => {
      const circle = (id) => document.querySelector(`circle[port="${id}"]`)
      const center = (id) => {
        const box = circle(id)?.getBoundingClientRect()
        return box && { x: box.x + box.width / 2, y: box.y + box.height / 2 }
      }
      const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
      const routes = pairs.map(({ source, target, net }) => {
        const edge = window.__flowGraph?.getCellById(net)
        const path = document.querySelector(`[data-cell-id="${net}"] path`)
        const at = (length) => path.getPointAtLength(length).matrixTransform(path.getScreenCTM())
        return { source, target, net, sourcePortId: edge?.getSourcePortId(),
          targetPortId: edge?.getTargetPortId(),
          sourceGroup: circle(source)?.getAttribute('port-group'),
          targetGroup: circle(target)?.getAttribute('port-group'),
          sourceDistance: distance(at(0), center(source)),
          targetDistance: distance(at(path.getTotalLength()), center(target)) }
      })
      return { routes,
        sourceRows: [firstSource, ...pairs.map((pair) => pair.source)].map((id) => center(id)?.y),
        targetRows: [firstTarget, ...pairs.map((pair) => pair.target)].map((id) => center(id)?.y) }
    }, { pairs: [
      { source: expanded.matrix_out_id, target: expanded.matrix_in_id, net: expanded.matrix_net_id },
      { source: expanded.gate_out_id, target: expanded.gate_in_id, net: expanded.gate_net_id },
    ], firstSource: initial.source_port_id, firstTarget: changed.sink_port_id })
    if (expanded.mcp_source_outputs !== 3 || expanded.mcp_source_actual !== 'UNKNOWN' ||
        multiportGeometry.routes.some((route) => route.sourcePortId !== route.source ||
          route.targetPortId !== route.target || route.sourceDistance > 12 ||
          route.targetDistance > 12) ||
        [...multiportGeometry.sourceRows, ...multiportGeometry.targetRows].some((y) => y == null) ||
        new Set(multiportGeometry.sourceRows).size !== 3 ||
        new Set(multiportGeometry.targetRows).size !== 3) {
      throw new Error(`multiport handles or MCP node details are wrong: ${JSON.stringify(multiportGeometry)}`)
    }
  }
  await page.screenshot({ path: screenshot, fullPage: true })
  if (expanded) {
    await page.getByTestId(`port-name-${expanded.matrix_out_id}`).click()
    const card = page.getByTestId('port-type-popover')
    await card.getByText('Declared code type (Design): double[20][20]').waitFor()
    await card.getByText('Expected dtype (Design): float64').waitFor()
    await card.getByText('Actual: UNKNOWN').waitFor()
    await card.screenshot({ path: popoverScreenshot })
  }
  console.log(JSON.stringify({ status: 'PASS', browser: 'Google Chrome headless',
    backend: url, isolated_datastore: join(isolated, 'rintel.db'),
    change_id: initial.change_id, flow_id: initial.flow_id,
    initial_design_revision: initial.revision,
    final_design_revision: finalState.revision,
    operation_receipt_id: live.design_activity.receipt_id,
    initial_flow_digest: initialFlow.eda.revision,
    final_flow_digest: live.eda.revision,
    sink_node_id: changed.sink_id, sink_port_id: changed.sink_port_id,
    net_id: changed.net_id, screenshot,
    popover_screenshot: expanded ? popoverScreenshot : null,
    geometry, multiport_geometry: multiportGeometry }))
} finally {
  await browser?.close()
  backend.kill('SIGTERM')
}
