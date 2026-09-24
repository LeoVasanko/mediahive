import type { TaskInfo } from "../types"

export type RootTaskInfo = TaskInfo & { root_id: string }

export interface ProgressRootState {
  rootId: string
  rootLabel: string
  scanTarget: string | null
  phaseLabel: string
  phaseDetail: string | null
  progressPercent: number
  progressLabel: string | null
  isDeterminate: boolean
  toneClass: string
}

function normalizePosixPath(value: string): string {
  return value.replace(/\\/g, "/")
}

function extractScanPath(detail: string): string | null {
  if (!detail.startsWith("Scanning:")) return null
  let value = detail.replace(/^Scanning:\s*/i, "").trim()
  value = value.replace(/\s*\(\d+\s+found\)\s*$/i, "").trim()
  return value || null
}

function buildScanTarget(rootPath: string | null, detail: string): string | null {
  const rawPath = extractScanPath(detail)
  if (!rawPath) return null

  const posixRaw = normalizePosixPath(rawPath)
  const posixRoot = rootPath ? normalizePosixPath(rootPath) : null

  if (posixRoot) {
    const lowRaw = posixRaw.toLowerCase()
    const lowRoot = posixRoot.toLowerCase()
    if (lowRaw === lowRoot) return posixRoot
    if (lowRaw.startsWith(`${lowRoot}/`)) {
      return posixRaw.slice(posixRoot.length).replace(/^\/+/, "")
    }
  }
  return posixRaw
}

function describeRootProgress(
  rootId: string,
  rootPath: string | null,
  tasksForRoot: RootTaskInfo[],
  isInitialScanMode: boolean,
): ProgressRootState | null {
  const running = tasksForRoot.filter((task) => task.status === "running")
  const latestError = [...tasksForRoot].reverse().find((task) => task.status === "error") || null

  if (running.length === 0 && !latestError) return null

  const scanTask = running.find((task) => task.id.startsWith("scan-")) || null
  const showreelCount = running.filter((task) => task.id.startsWith("showreel-")).length
  const otherRunningCount = running.length - (scanTask ? 1 : 0) - showreelCount

  let phaseLabel = "Processing media"
  let phaseDetail: string | null = null
  let scanTarget: string | null = null
  let isDeterminate = false
  let progressPercent = 0
  let progressLabel: string | null = null
  let toneClass = ""

  if (scanTask) {
    const detail = (scanTask.detail || "").trim()
    scanTarget = buildScanTarget(rootPath, detail)
    if (detail.startsWith("Scanning:")) {
      phaseLabel = isInitialScanMode ? "Scanning folders" : "Checking for updates"
    } else if (/^Processing\s+\d+\s+(items|movies|series)/i.test(detail)) {
      phaseLabel = "Preparing titles"
    } else if (/^(Starting scan|No new items|Done|Scan cancelled)/i.test(detail)) {
      phaseLabel = isInitialScanMode ? "Scanning folders" : "Checking for updates"
    } else {
      phaseLabel = "Fetching metadata"
      phaseDetail = detail || null
    }
    if (scanTask.progress > 0 && scanTask.progress <= 1) {
      isDeterminate = true
      progressPercent = Math.max(1, Math.round(scanTask.progress * 100))
      progressLabel = `${progressPercent}%`
    }
  } else if (showreelCount > 0) {
    phaseLabel = "Generating previews"
  } else if (otherRunningCount > 0) {
    phaseLabel = "Finalizing updates"
  } else if (latestError) {
    phaseLabel = "Needs attention"
    phaseDetail = latestError.detail || "A background task failed"
    toneClass = "activity-root-error"
  }

  return {
    rootId,
    rootLabel: rootId,
    scanTarget,
    phaseLabel,
    phaseDetail,
    progressPercent,
    progressLabel,
    isDeterminate,
    toneClass,
  }
}

export function computeProgressRoots(
  tasks: Iterable<RootTaskInfo>,
  getRootPath: (rootId: string) => string | null,
  isInitialScanMode: boolean,
): ProgressRootState[] {
  const byRoot = new Map<string, RootTaskInfo[]>()
  for (const task of tasks) {
    const list = byRoot.get(task.root_id) || []
    list.push(task)
    byRoot.set(task.root_id, list)
  }

  const rows: ProgressRootState[] = []
  for (const [rootId, rootTasks] of byRoot) {
    const row = describeRootProgress(rootId, getRootPath(rootId), rootTasks, isInitialScanMode)
    if (row) rows.push(row)
  }
  return rows.sort((a, b) => a.rootLabel.localeCompare(b.rootLabel))
}
