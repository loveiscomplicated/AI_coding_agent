/**
 * DependencyGraphModal.tsx
 *
 * ReactFlow 기반 태스크 의존성 DAG 편집기
 * - 엣지 드래그로 의존성 추가
 * - 엣지 선택 후 Delete 키로 제거
 * - 실시간 순환 참조 감지 (Kahn's algorithm)
 * - AI 자동 수정 버튼 → POST /api/tasks/fix-dependencies
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  addEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type Edge,
  type Node,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { DraftTask } from './TaskDraftPanel'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

// ── 순환 감지 (Kahn's algorithm) ──────────────────────────────────────────────

export function hasDuplicateIds(tasks: { id: string }[]): boolean {
  return tasks.length !== new Set(tasks.map(t => t.id)).size
}

function detectCycles(tasks: DraftTask[]): boolean {
  const validIds = new Set(tasks.map(t => t.id))
  const inDegree: Record<string, number> = {}
  const adj: Record<string, string[]> = {}

  for (const t of tasks) {
    inDegree[t.id] = 0
    adj[t.id] = []
  }
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (!validIds.has(dep)) continue
      adj[dep].push(t.id)
      inDegree[t.id]++
    }
  }

  const queue = Object.keys(inDegree).filter(id => inDegree[id] === 0)
  let processed = 0
  while (queue.length > 0) {
    const node = queue.shift()!
    processed++
    for (const neighbor of adj[node]) {
      inDegree[neighbor]--
      if (inDegree[neighbor] === 0) queue.push(neighbor)
    }
  }
  // tasks.length가 아닌 유니크 ID 수와 비교 (중복 ID 오탐 방지)
  return processed !== validIds.size
}

// DFS로 back-edge(순환을 만드는 엣지) ID 집합 반환
function getCycleEdgeIds(tasks: DraftTask[]): Set<string> {
  const validIds = new Set(tasks.map(t => t.id))
  const adj: Record<string, string[]> = {}
  for (const t of tasks) adj[t.id] = []
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (validIds.has(dep)) adj[dep].push(t.id)
    }
  }

  const WHITE = 0, GRAY = 1, BLACK = 2
  const color: Record<string, number> = {}
  for (const t of tasks) color[t.id] = WHITE
  const cycleEdges = new Set<string>()

  function dfs(id: string) {
    color[id] = GRAY
    for (const neighbor of adj[id] ?? []) {
      if (color[neighbor] === GRAY) cycleEdges.add(`${id}->${neighbor}`)
      else if (color[neighbor] === WHITE) dfs(neighbor)
    }
    color[id] = BLACK
  }
  for (const t of tasks) if (color[t.id] === WHITE) dfs(t.id)
  return cycleEdges
}

// ── 레이아웃: 위상 정렬 기반 레벨 배치 ──────────────────────────────────────

function computeLayout(tasks: DraftTask[]): Record<string, { x: number; y: number }> {
  const validIds = new Set(tasks.map(t => t.id))
  const inDegree: Record<string, number> = {}
  const adj: Record<string, string[]> = {}
  for (const t of tasks) { inDegree[t.id] = 0; adj[t.id] = [] }
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (!validIds.has(dep)) continue
      adj[dep].push(t.id)
      inDegree[t.id]++
    }
  }

  const levels: Record<string, number> = {}
  const queue = Object.keys(inDegree).filter(id => inDegree[id] === 0)
  queue.forEach(id => { levels[id] = 0 })

  while (queue.length > 0) {
    const node = queue.shift()!
    for (const neighbor of adj[node]) {
      levels[neighbor] = Math.max(levels[neighbor] ?? 0, (levels[node] ?? 0) + 1)
      inDegree[neighbor]--
      if (inDegree[neighbor] === 0) queue.push(neighbor)
    }
  }

  // 순환 노드는 가장 오른쪽 컬럼에 배치
  const maxLevel = Math.max(0, ...Object.values(levels))
  for (const t of tasks) if (levels[t.id] === undefined) levels[t.id] = maxLevel + 1

  const byLevel: Record<number, string[]> = {}
  for (const [id, lv] of Object.entries(levels)) {
    byLevel[lv] = byLevel[lv] ?? []
    byLevel[lv].push(id)
  }

  const NODE_W = 210, NODE_H = 130
  const positions: Record<string, { x: number; y: number }> = {}
  for (const [lvStr, ids] of Object.entries(byLevel)) {
    const lv = Number(lvStr)
    const totalH = ids.length * NODE_H
    ids.forEach((id, i) => {
      positions[id] = { x: lv * NODE_W, y: i * NODE_H - totalH / 2 }
    })
  }
  return positions
}

// ── ReactFlow 헬퍼 ────────────────────────────────────────────────────────────

function makeNodeLabel(task: DraftTask) {
  return (
    <div className="text-center px-1">
      <div className="font-mono text-[9px] text-gray-400 dark:text-zinc-500 leading-tight">{task.id}</div>
      <div
        className="text-[11px] font-medium text-gray-800 dark:text-zinc-100 leading-tight mt-0.5 break-words"
        style={{ maxWidth: 160 }}
      >
        {task.title || '(제목 없음)'}
      </div>
    </div>
  )
}

function buildNodes(tasks: DraftTask[], hasCycle: boolean, existingNodes?: Node[]): Node[] {
  const posMap = existingNodes
    ? Object.fromEntries(existingNodes.map(n => [n.id, n.position]))
    : computeLayout(tasks)

  return tasks.map(task => ({
    id: task.id,
    position: posMap[task.id] ?? { x: 0, y: 0 },
    data: { label: makeNodeLabel(task) },
    style: {
      background: hasCycle ? '#fef2f2' : '#f0fdf4',
      border: `1.5px solid ${hasCycle ? '#ef4444' : '#22c55e'}`,
      borderRadius: 10,
      width: 180,
      padding: '8px 6px',
    } as React.CSSProperties,
  }))
}

function buildEdges(tasks: DraftTask[], cycleEdgeIds: Set<string>): Edge[] {
  const edges: Edge[] = []
  for (const task of tasks) {
    for (const dep of task.depends_on) {
      const id = `${dep}->${task.id}`
      const isCycle = cycleEdgeIds.has(id)
      edges.push({
        id,
        source: dep,
        target: task.id,
        markerEnd: { type: MarkerType.ArrowClosed, color: isCycle ? '#ef4444' : '#6b7280' },
        style: { stroke: isCycle ? '#ef4444' : '#6b7280', strokeWidth: isCycle ? 2.5 : 1.5 },
        animated: isCycle,
      })
    }
  }
  return edges
}

// ── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  tasks: DraftTask[]
  onClose: () => void
  onApply: (tasks: DraftTask[]) => void
}

// ── 컴포넌트 ──────────────────────────────────────────────────────────────────

export function DependencyGraphModal({ tasks: initialTasks, onClose, onApply }: Props) {
  const [tasks, setTasks] = useState<DraftTask[]>(initialTasks)
  const [isFixing, setIsFixing] = useState(false)
  const [fixError, setFixError] = useState<string | null>(null)
  const [fixExplanation, setFixExplanation] = useState<string | null>(null)

  const hasCycle = useMemo(() => detectCycles(tasks), [tasks])
  const cycleEdgeIds = useMemo(() => getCycleEdgeIds(tasks), [tasks])
  const dupIds = useMemo(() => {
    const seen = new Set<string>()
    const dups = new Set<string>()
    for (const t of tasks) { if (seen.has(t.id)) dups.add(t.id); seen.add(t.id) }
    return dups
  }, [tasks])

  const [nodes, setNodes, onNodesChange] = useNodesState(
    buildNodes(initialTasks, detectCycles(initialTasks))
  )
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    buildEdges(initialTasks, getCycleEdgeIds(initialTasks))
  )

  // tasks 변경 시 → 노드 스타일 + 엣지 재구성 (위치는 유지)
  useEffect(() => {
    setNodes(prev => buildNodes(tasks, hasCycle, prev))
    setEdges(buildEdges(tasks, cycleEdgeIds))
  }, [tasks, hasCycle, cycleEdgeIds])

  // 엣지 연결 → depends_on 추가
  const onConnect = useCallback((connection: Connection) => {
    const { source, target } = connection
    if (!source || !target || source === target) return
    setTasks(prev =>
      prev.map(t =>
        t.id === target && !t.depends_on.includes(source)
          ? { ...t, depends_on: [...t.depends_on, source] }
          : t
      )
    )
    setEdges(prev =>
      addEdge(
        {
          ...connection,
          id: `${source}->${target}`,
          markerEnd: { type: MarkerType.ArrowClosed },
        },
        prev
      )
    )
  }, [])

  // 엣지 삭제 → depends_on 제거
  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    const removals = new Map<string, Set<string>>()
    for (const e of deleted) {
      if (!removals.has(e.target)) removals.set(e.target, new Set())
      removals.get(e.target)!.add(e.source)
    }
    setTasks(prev =>
      prev.map(t => {
        const toRemove = removals.get(t.id)
        if (!toRemove) return t
        return { ...t, depends_on: t.depends_on.filter(d => !toRemove.has(d)) }
      })
    )
  }, [])

  // AI 자동 수정
  async function handleAiFix() {
    setIsFixing(true)
    setFixError(null)
    setFixExplanation(null)
    try {
      const res = await fetch(`${API_BASE}/api/tasks/fix-dependencies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? 'AI 수정 실패')
      }
      const data = await res.json()
      const fixedTasks: DraftTask[] = data.tasks
      if (data.explanation) setFixExplanation(data.explanation)
      setNodes(prev => buildNodes(fixedTasks, detectCycles(fixedTasks), prev))
      setEdges(buildEdges(fixedTasks, getCycleEdgeIds(fixedTasks)))
      setTasks(fixedTasks)
    } catch (e: unknown) {
      setFixError(e instanceof Error ? e.message : String(e))
    } finally {
      setIsFixing(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-white dark:bg-zinc-900 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        style={{ width: '92vw', height: '88vh' }}>

        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 dark:border-zinc-700 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-800 dark:text-zinc-100">의존성 그래프</span>
            {dupIds.size > 0 && (
              <span className="text-xs font-medium bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400 px-2 py-0.5 rounded-full">
                ⚠ 중복 ID: {[...dupIds].join(', ')}
              </span>
            )}
            {hasCycle ? (
              <span className="text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400 px-2 py-0.5 rounded-full">
                ⚠ 순환 참조 감지됨
              </span>
            ) : (
              <span className="text-xs font-medium bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400 px-2 py-0.5 rounded-full">
                ✓ DAG 유효
              </span>
            )}
            <span className="text-xs text-gray-400 dark:text-zinc-500">{tasks.length}개 태스크</span>
          </div>

          <div className="flex items-center gap-2">
            {fixError && (
              <span className="text-xs text-red-600 dark:text-red-400 max-w-xs truncate">{fixError}</span>
            )}
            <button
              onClick={handleAiFix}
              disabled={isFixing || !hasCycle}
              title={hasCycle ? 'AI가 순환 참조를 자동으로 수정합니다' : '순환 참조가 없습니다'}
              className="rounded-lg bg-purple-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-purple-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {isFixing ? (
                <span className="flex items-center gap-1.5">
                  <span className="w-3 h-3 border border-white border-t-transparent rounded-full animate-spin inline-block" />
                  수정 중…
                </span>
              ) : '✨ AI 자동 수정'}
            </button>
            <button
              onClick={() => onApply(tasks)}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 transition-colors"
            >
              적용
            </button>
            <button
              onClick={onClose}
              className="rounded-lg border border-gray-300 dark:border-zinc-600 px-3 py-1.5 text-xs text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            >
              닫기
            </button>
          </div>
        </div>

        {/* AI 수정 설명 */}
        {fixExplanation && (
          <div className="px-5 py-2 bg-purple-50 dark:bg-purple-900/20 border-b border-purple-200 dark:border-purple-800 shrink-0">
            <p className="text-xs text-purple-700 dark:text-purple-300">✨ {fixExplanation}</p>
          </div>
        )}

        {/* 힌트 */}
        <div className="px-5 py-1.5 bg-gray-50 dark:bg-zinc-800 border-b border-gray-200 dark:border-zinc-700 shrink-0">
          <p className="text-[11px] text-gray-400 dark:text-zinc-500">
            노드 핸들에서 드래그 → 의존성 추가 &nbsp;·&nbsp; 엣지 클릭 후 Delete → 의존성 제거 &nbsp;·&nbsp; 빨간 엣지 = 순환 참조
          </p>
        </div>

        {/* ReactFlow */}
        <div className="flex-1 min-h-0">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onEdgesDelete={onEdgesDelete}
            deleteKeyCode="Delete"
            fitView
            fitViewOptions={{ padding: 0.2 }}
          >
            <Background color="#e5e7eb" />
            <Controls />
          </ReactFlow>
        </div>
      </div>
    </div>
  )
}