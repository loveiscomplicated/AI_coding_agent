/**
 * PipelineModelModal.tsx
 *
 * 파이프라인 시작/재개 시 코딩 에이전트·오케스트레이터 모델을 선택하는 팝업.
 * TaskDraftPanel, DashboardPage 등 여러 곳에서 공유한다.
 */

import { useEffect, useState } from 'react'

export interface AvailableModel {
  id: string
  name: string
  provider: string
}

export type RoleOverride = { provider?: string; model?: string }
export type RoleOverrides = Record<string, RoleOverride>

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

// 백엔드 /api/chat/complexity-map 응답 shape.
// env override가 적용된 런타임 실매핑을 받아 UI에 그대로 표시한다.
export interface ComplexityMapEntry {
  provider_fast: string
  model_fast: string
  provider_capable: string
  model_capable: string
}
export type ComplexityMap = Record<'simple' | 'standard' | 'complex', ComplexityMapEntry>

// 백엔드 조회 실패 시 fallback. backend/config.py의 기본값과 동일.
export const DEFAULT_COMPLEXITY_MAP: ComplexityMap = {
  simple:   { provider_fast: 'openai', model_fast: 'gpt-4.1-mini',
              provider_capable: 'gemini', model_capable: 'gemini-2.5-flash-lite' },
  standard: { provider_fast: 'openai', model_fast: 'gpt-5-mini',
              provider_capable: 'gemini', model_capable: 'gemini-2.5-flash' },
  complex:  { provider_fast: 'openai', model_fast: 'gpt-5',
              provider_capable: 'gemini', model_capable: 'gemini-3-pro-preview' },
}

export interface PipelineTaskSummary {
  id: string
  complexity?: 'simple' | 'standard' | 'complex' | null
}
// ── ModelSelect ───────────────────────────────────────────────────────────────

interface ModelSelectProps {
  label: string
  hint: string
  models: AvailableModel[]
  selectedProvider: string
  selectedModel: string
  onProviderChange: (p: string) => void
  onModelChange: (m: string) => void
  disabled?: boolean
}

export function ModelSelect({
  label,
  hint,
  models,
  selectedProvider,
  selectedModel,
  onProviderChange,
  onModelChange,
  disabled = false,
}: ModelSelectProps) {
  const providers = Array.from(new Set(models.map(m => m.provider)))
  const filtered = models.filter(m => m.provider === selectedProvider)

  return (
    <div className={`rounded-xl border border-gray-200 dark:border-zinc-700 p-3 space-y-2 ${disabled ? 'opacity-40' : ''}`}>
      <div>
        <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">{label}</p>
        <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">{hint}</p>
      </div>
      <div className="flex gap-2">
        <select
          className="rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-xs text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500 w-28 shrink-0 disabled:cursor-not-allowed"
          value={selectedProvider}
          onChange={e => onProviderChange(e.target.value)}
          disabled={disabled}
        >
          {providers.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          className="flex-1 rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-xs text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed"
          value={selectedModel}
          onChange={e => onModelChange(e.target.value)}
          disabled={disabled}
        >
          {filtered.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </div>
    </div>
  )
}

// ── RoleOverrideRow ───────────────────────────────────────────────────────────

const SELECT_CLS =
  'rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 ' +
  'text-xs text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 ' +
  'focus:ring-blue-500 disabled:opacity-40'

interface RoleOverrideRowProps {
  label: string
  models: AvailableModel[]
  override: RoleOverride
  onChange: (cfg: RoleOverride) => void
  onReset: () => void
}

function RoleOverrideRow({ label, models, override, onChange, onReset }: RoleOverrideRowProps) {
  const providers = Array.from(new Set(models.map(m => m.provider)))
  const filtered = override.provider ? models.filter(m => m.provider === override.provider) : []

  function handleProviderChange(p: string) {
    if (!p) {
      onChange({})
    } else {
      const firstModel = models.find(m => m.provider === p)?.id
      onChange({ provider: p, model: firstModel })
    }
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-600 dark:text-zinc-400 w-20 shrink-0">{label}</span>
      <select
        className={`${SELECT_CLS} w-24 shrink-0`}
        value={override.provider ?? ''}
        onChange={e => handleProviderChange(e.target.value)}
      >
        <option value="">기본값</option>
        {providers.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      <select
        className={`${SELECT_CLS} flex-1`}
        value={override.model ?? ''}
        disabled={!override.provider}
        onChange={e => onChange({ ...override, model: e.target.value })}
      >
        {!override.provider && <option value="">기본 모델 사용</option>}
        {filtered.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
      </select>
      <button
        className="text-xs text-gray-400 dark:text-zinc-500 hover:text-red-500 dark:hover:text-red-400 shrink-0 transition-colors"
        onClick={onReset}
        title="기본값으로 초기화"
      >
        기본값
      </button>
    </div>
  )
}

// ── PipelineModelModal ────────────────────────────────────────────────────────

const ROLES: Array<{ key: string; label: string }> = [
  { key: 'test_writer', label: '테스트 작성기' },
  { key: 'implementer', label: '구현기' },
  { key: 'reviewer', label: '리뷰어' },
]

interface PipelineModelModalProps {
  models: AvailableModel[]
  onConfirm: (
    providerFast: string,
    modelFast: string,
    providerCapable: string,
    modelCapable: string,
    agentCount: number,
    roleModels?: RoleOverrides,
    noPush?: boolean,
    autoSelectByComplexity?: boolean,
    interventionAutoSplit?: boolean,
  ) => void
  onCancel: () => void
  tasks?: PipelineTaskSummary[]
}

export function PipelineModelModal({ models, onConfirm, onCancel, tasks }: PipelineModelModalProps) {
  const providers = Array.from(new Set(models.map(m => m.provider)))
  const defaultProvider = providers[0] ?? ''
  const modelsForProvider = (p: string) => models.filter(m => m.provider === p)

  const [fastProvider, setFastProvider] = useState(defaultProvider)
  const [fastModel, setFastModel] = useState(modelsForProvider(defaultProvider)[0]?.id ?? '')

  const [capableProvider, setCapableProvider] = useState(defaultProvider)
  const [capableModel, setCapableModel] = useState(() => {
    const list = modelsForProvider(defaultProvider)
    return list[list.length - 1]?.id ?? ''
  })

  const [agentCount, setAgentCount] = useState(1)
  const [noPush, setNoPush] = useState(false)
  const [interventionAutoSplit, setInterventionAutoSplit] = useState(false)
  const [roleOverrides, setRoleOverrides] = useState<RoleOverrides>({})
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [autoByComplexity, setAutoByComplexity] = useState(false)
  const [complexityMap, setComplexityMap] = useState<ComplexityMap>(DEFAULT_COMPLEXITY_MAP)

  // 백엔드 env override가 적용된 실매핑을 가져온다. 실패 시 DEFAULT_COMPLEXITY_MAP 유지.
  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/chat/complexity-map`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!cancelled && data?.map) setComplexityMap(data.map as ComplexityMap)
      })
      .catch(() => { /* fallback 유지 */ })
    return () => { cancelled = true }
  }, [])

  const tasksWithoutComplexity = (tasks ?? []).filter(t => !t.complexity)

  const handleFastProviderChange = (p: string) => {
    setFastProvider(p)
    setFastModel(modelsForProvider(p)[0]?.id ?? '')
  }

  const handleCapableProviderChange = (p: string) => {
    setCapableProvider(p)
    const list = modelsForProvider(p)
    setCapableModel(list[list.length - 1]?.id ?? '')
  }

  function handleConfirm() {
    const roleModels = Object.entries(roleOverrides)
      .filter(([, cfg]) => cfg.provider || cfg.model)
      .reduce<RoleOverrides>((acc, [role, cfg]) => ({ ...acc, [role]: cfg }), {})
    // role_models 는 auto_select 여부와 무관하게 그대로 전송한다.
    // 백엔드에서 role_models → complexity mapping → 기본값 순으로 해석한다.
    const effectiveRoleModels = Object.keys(roleModels).length > 0 ? roleModels : undefined
    onConfirm(
      fastProvider, fastModel,
      capableProvider, capableModel,
      agentCount,
      effectiveRoleModels,
      noPush,
      autoByComplexity,
      interventionAutoSplit,
    )
  }

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-white dark:bg-zinc-900 rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-bold text-gray-800 dark:text-zinc-100">파이프라인 모델 설정</h2>

        <ModelSelect
          label="코딩 에이전트 모델"
          hint="테스트 작성, 구현, 코드 리뷰 담당 — 속도가 빠른 모델 권장"
          models={models}
          selectedProvider={fastProvider}
          selectedModel={fastModel}
          onProviderChange={handleFastProviderChange}
          onModelChange={setFastModel}
          disabled={autoByComplexity}
        />

        <ModelSelect
          label="오케스트레이터 모델"
          hint="태스크 조율, 핫라인 대화, 개입 분석 담당 — 성능이 좋은 모델 권장"
          models={models}
          selectedProvider={capableProvider}
          selectedModel={capableModel}
          onProviderChange={handleCapableProviderChange}
          onModelChange={setCapableModel}
        />

        {/* 병렬 에이전트 수 */}
        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">병렬 에이전트 수</p>
            <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">동시에 실행할 태스크 수 (1 = 순차)</p>
          </div>
          <input
            type="number"
            min={1}
            max={8}
            className="w-16 rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-sm text-center text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={agentCount}
            onChange={e => setAgentCount(Math.max(1, Math.min(8, parseInt(e.target.value) || 1)))}
          />
        </div>

        {/* 푸쉬 온오프 */}
        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">Git 푸쉬</p>
            <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">
              {noPush ? '브랜치를 원격에 푸쉬하지 않습니다' : '각 태스크 완료 후 브랜치를 원격에 푸쉬합니다'}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={!noPush}
            onClick={() => setNoPush(v => !v)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-zinc-900 ${
              noPush
                ? 'bg-gray-300 dark:bg-zinc-600'
                : 'bg-blue-600'
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                noPush ? 'translate-x-0' : 'translate-x-5'
              }`}
            />
          </button>
        </div>

        {/* 최종 실패 시 태스크 자동 분해 (intervention_auto_split) */}
        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 flex items-center justify-between">
          <div>
            <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">최종 실패 시 태스크 자동 분해</p>
            <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">
              {interventionAutoSplit
                ? '실행 중 태스크 구조가 변경됩니다. 재시도 소진 시 LLM이 태스크를 2~3개 하위 태스크로 분해합니다 (재실행 필요)'
                : '비활성화 — 재시도 소진 시 태스크를 FAILED로 종료합니다'}
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={interventionAutoSplit}
            aria-label="최종 실패 시 태스크 자동 분해"
            onClick={() => setInterventionAutoSplit(v => !v)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-zinc-900 ${
              interventionAutoSplit ? 'bg-blue-600' : 'bg-gray-300 dark:bg-zinc-600'
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                interventionAutoSplit ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
          </button>
        </div>

        {/* 복잡도 기반 자동 선택 */}
        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">복잡도 기반 자동 선택</p>
              <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">
                {autoByComplexity
                  ? '각 태스크의 complexity 라벨에 따라 모델이 자동 선택됩니다 (위 설정 무시)'
                  : '각 태스크의 complexity 값을 무시하고 위 설정을 모든 태스크에 적용합니다'}
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={autoByComplexity}
              aria-label="복잡도 기반 자동 선택"
              onClick={() => setAutoByComplexity(v => !v)}
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-zinc-900 ${
                autoByComplexity ? 'bg-blue-600' : 'bg-gray-300 dark:bg-zinc-600'
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                  autoByComplexity ? 'translate-x-5' : 'translate-x-0'
                }`}
              />
            </button>
          </div>

          {autoByComplexity && (
            <div
              data-testid="complexity-mapping-table"
              className="pt-2 border-t border-gray-100 dark:border-zinc-800 space-y-1"
            >
              {(['simple', 'standard', 'complex'] as const).map(tier => {
                const entry = complexityMap[tier]
                const fastLabel = `${entry.provider_fast} · ${entry.model_fast}`
                const capableLabel = `${entry.provider_capable} · ${entry.model_capable}`
                return (
                  <div
                    key={tier}
                    className="grid grid-cols-[70px_1fr] gap-2 text-[11px] text-gray-500 dark:text-zinc-400"
                  >
                    <span className="font-mono text-gray-600 dark:text-zinc-300">{tier}</span>
                    <span>
                      fast: <span className="text-gray-600 dark:text-zinc-300">{fastLabel}</span>
                      <span className="mx-1 text-gray-400 dark:text-zinc-600">·</span>
                      capable: <span className="text-gray-600 dark:text-zinc-300">{capableLabel}</span>
                    </span>
                  </div>
                )
              })}
              {tasksWithoutComplexity.length > 0 && (
                <p
                  data-testid="complexity-missing-warning"
                  className="pt-1 text-[11px] text-amber-600 dark:text-amber-400"
                >
                  ⚠ complexity 라벨이 없는 태스크 {tasksWithoutComplexity.length}개는 'standard'로 실행됩니다
                </p>
              )}
            </div>
          )}
        </div>

        {/* 역할별 모델 설정 — auto_select ON 이어도 여기서 특정 역할을 override 가능 */}
        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-3 py-2.5 text-xs font-semibold text-gray-600 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={() => setShowAdvanced(v => !v)}
          >
            <span>역할별 모델 설정{autoByComplexity ? ' (선택한 역할만 복잡도 매핑을 덮어씀)' : ''}</span>
            <span className="text-gray-400 dark:text-zinc-600">{showAdvanced ? '▲' : '▼'}</span>
          </button>

          {showAdvanced && (
            <div className="px-3 pb-3 space-y-2 border-t border-gray-100 dark:border-zinc-800 pt-2">
              <p className="text-xs text-gray-400 dark:text-zinc-500">
                {autoByComplexity
                  ? '비워두면 각 태스크의 complexity에 해당하는 모델이 사용됩니다.'
                  : '비워두면 위의 코딩 에이전트 모델이 사용됩니다.'}
              </p>
              {ROLES.map(({ key, label }) => (
                <RoleOverrideRow
                  key={key}
                  label={label}
                  models={models}
                  override={roleOverrides[key] ?? {}}
                  onChange={cfg => setRoleOverrides(prev => ({ ...prev, [key]: cfg }))}
                  onReset={() => setRoleOverrides(prev => {
                    const next = { ...prev }
                    delete next[key]
                    return next
                  })}
                />
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <button
            className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={onCancel}
          >
            취소
          </button>
          <button
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            onClick={handleConfirm}
            disabled={!fastModel || !capableModel}
          >
            파이프라인 시작 🚀
          </button>
        </div>
      </div>
    </div>
  )
}
