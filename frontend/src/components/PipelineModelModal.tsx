/**
 * PipelineModelModal.tsx
 *
 * 파이프라인 시작/재개 시 역할별 기본 모델과 override를 선택하는 팝업.
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
export type ResolvedRoleModel = { provider: string; model: string }
export type DefaultRoleModels = Record<string, ResolvedRoleModel>

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

export type ComplexityMap = Record<'simple' | 'standard' | 'complex', Record<string, ResolvedRoleModel>>

const ROLES: Array<{ key: string; label: string; prefersLastModel?: boolean }> = [
  { key: 'test_writer', label: '테스트 작성기' },
  { key: 'implementer', label: '구현기' },
  { key: 'reviewer', label: '리뷰어' },
  { key: 'merge_agent', label: '머지 에이전트' },
  { key: 'orchestrator', label: '오케스트레이터', prefersLastModel: true },
  { key: 'intervention', label: '개입 분석기', prefersLastModel: true },
]

export const DEFAULT_COMPLEXITY_MAP: ComplexityMap = {
  simple: {
    test_writer: { provider: 'openai', model: 'gpt-4.1-mini' },
    implementer: { provider: 'openai', model: 'gpt-4.1-mini' },
    reviewer: { provider: 'openai', model: 'gpt-4.1-mini' },
    merge_agent: { provider: 'openai', model: 'gpt-4.1-mini' },
    orchestrator: { provider: 'gemini', model: 'gemini-2.5-flash-lite' },
    intervention: { provider: 'gemini', model: 'gemini-2.5-flash-lite' },
  },
  standard: {
    test_writer: { provider: 'openai', model: 'gpt-5-mini' },
    implementer: { provider: 'openai', model: 'gpt-5-mini' },
    reviewer: { provider: 'openai', model: 'gpt-5-mini' },
    merge_agent: { provider: 'openai', model: 'gpt-5-mini' },
    orchestrator: { provider: 'gemini', model: 'gemini-2.5-flash' },
    intervention: { provider: 'gemini', model: 'gemini-2.5-flash' },
  },
  complex: {
    test_writer: { provider: 'openai', model: 'gpt-5' },
    implementer: { provider: 'openai', model: 'gpt-5' },
    reviewer: { provider: 'openai', model: 'gpt-5' },
    merge_agent: { provider: 'openai', model: 'gpt-5' },
    orchestrator: { provider: 'gemini', model: 'gemini-3-pro-preview' },
    intervention: { provider: 'gemini', model: 'gemini-3-pro-preview' },
  },
}

export interface PipelineTaskSummary {
  id: string
  complexity?: string | null
}

const SELECT_CLS =
  'rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 ' +
  'text-xs text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 ' +
  'focus:ring-blue-500 disabled:opacity-40'

function modelsForProvider(models: AvailableModel[], provider: string) {
  return models.filter(model => model.provider === provider)
}

function buildInitialDefaultRoleModels(models: AvailableModel[]): DefaultRoleModels {
  const providers = Array.from(new Set(models.map(model => model.provider)))
  const defaultProvider = providers[0] ?? ''
  const providerModels = modelsForProvider(models, defaultProvider)
  const firstModel = providerModels[0]?.id ?? ''
  const lastModel = providerModels[providerModels.length - 1]?.id ?? firstModel

  return Object.fromEntries(
    ROLES.map(({ key, prefersLastModel }) => [
      key,
      {
        provider: defaultProvider,
        model: prefersLastModel ? lastModel : firstModel,
      },
    ]),
  )
}

interface RoleModelRowProps {
  label: string
  models: AvailableModel[]
  value: RoleOverride
  onChange: (cfg: RoleOverride) => void
  onReset?: () => void
  allowEmpty?: boolean
  disabled?: boolean
}

function RoleModelRow({
  label,
  models,
  value,
  onChange,
  onReset,
  allowEmpty = false,
  disabled = false,
}: RoleModelRowProps) {
  const providers = Array.from(new Set(models.map(model => model.provider)))
  const filtered = value.provider ? modelsForProvider(models, value.provider) : []

  function handleProviderChange(provider: string) {
    if (!provider && allowEmpty) {
      onChange({})
      return
    }
    const nextModels = modelsForProvider(models, provider)
    onChange({
      provider,
      model: nextModels[0]?.id ?? '',
    })
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-600 dark:text-zinc-400 w-28 shrink-0">{label}</span>
      <select
        className={`${SELECT_CLS} w-28 shrink-0`}
        value={value.provider ?? ''}
        onChange={event => handleProviderChange(event.target.value)}
        disabled={disabled}
      >
        {allowEmpty && <option value="">기본값</option>}
        {providers.map(provider => (
          <option key={provider} value={provider}>{provider}</option>
        ))}
      </select>
      <select
        className={`${SELECT_CLS} flex-1`}
        value={value.model ?? ''}
        disabled={disabled || !value.provider}
        onChange={event => onChange({ ...value, model: event.target.value })}
      >
        {allowEmpty && !value.provider && <option value="">기본 모델 사용</option>}
        {filtered.map(model => (
          <option key={model.id} value={model.id}>{model.name}</option>
        ))}
      </select>
      {onReset && (
        <button
          className="text-xs text-gray-400 dark:text-zinc-500 hover:text-red-500 dark:hover:text-red-400 shrink-0 transition-colors"
          onClick={onReset}
          title="기본값으로 초기화"
          disabled={disabled}
        >
          기본값
        </button>
      )}
    </div>
  )
}

interface PipelineModelModalProps {
  models: AvailableModel[]
  onConfirm: (
    defaultRoleModels: DefaultRoleModels,
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
  const [defaultRoleModels, setDefaultRoleModels] = useState<DefaultRoleModels>(() => buildInitialDefaultRoleModels(models))
  const [agentCount, setAgentCount] = useState(1)
  const [noPush, setNoPush] = useState(false)
  const [interventionAutoSplit, setInterventionAutoSplit] = useState(false)
  const [roleOverrides, setRoleOverrides] = useState<RoleOverrides>({})
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [autoByComplexity, setAutoByComplexity] = useState(false)
  const [complexityMap, setComplexityMap] = useState<ComplexityMap>(DEFAULT_COMPLEXITY_MAP)

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/chat/complexity-map`)
      .then(response => response.ok ? response.json() : null)
      .then(data => {
        if (!cancelled && data?.map) setComplexityMap(data.map as ComplexityMap)
      })
      .catch(() => { /* fallback 유지 */ })
    return () => { cancelled = true }
  }, [])

  const tasksWithoutComplexity = (tasks ?? []).filter(task => !task.complexity)

  function updateDefaultRoleModel(role: string, cfg: RoleOverride) {
    setDefaultRoleModels(prev => ({
      ...prev,
      [role]: {
        provider: cfg.provider ?? '',
        model: cfg.model ?? '',
      },
    }))
  }

  function handleConfirm() {
    const cleanedDefaults = Object.entries(defaultRoleModels).reduce<DefaultRoleModels>((acc, [role, cfg]) => {
      if (cfg.provider && cfg.model) {
        acc[role] = { provider: cfg.provider, model: cfg.model }
      }
      return acc
    }, {})
    const cleanedOverrides = Object.entries(roleOverrides)
      .filter(([, cfg]) => cfg.provider || cfg.model)
      .reduce<RoleOverrides>((acc, [role, cfg]) => ({ ...acc, [role]: cfg }), {})

    onConfirm(
      cleanedDefaults,
      agentCount,
      Object.keys(cleanedOverrides).length > 0 ? cleanedOverrides : undefined,
      noPush,
      autoByComplexity,
      interventionAutoSplit,
    )
  }

  const hasMissingDefaultRoleModel = ROLES.some(({ key }) => {
    const cfg = defaultRoleModels[key]
    return !cfg?.provider || !cfg?.model
  })

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onCancel}
    >
      <div
        className="bg-white dark:bg-zinc-900 rounded-2xl w-full max-w-2xl shadow-2xl p-6 space-y-4"
        onClick={event => event.stopPropagation()}
      >
        <h2 className="text-base font-bold text-gray-800 dark:text-zinc-100">파이프라인 모델 설정</h2>

        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 space-y-2">
          <div>
            <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">기본 역할 모델</p>
            <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">
              {autoByComplexity
                ? '복잡도 자동 선택이 켜져 있으면 이 기본값 대신 tier별 role 모델이 사용됩니다.'
                : '모든 역할은 아래 기본 모델을 사용합니다.'}
            </p>
          </div>
          <div className="space-y-2">
            {ROLES.map(({ key, label }) => (
              <RoleModelRow
                key={key}
                label={label}
                models={models}
                value={defaultRoleModels[key] ?? { provider: '', model: '' }}
                onChange={cfg => updateDefaultRoleModel(key, cfg)}
                disabled={autoByComplexity}
              />
            ))}
          </div>
        </div>

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
            onChange={event => setAgentCount(Math.max(1, Math.min(8, parseInt(event.target.value, 10) || 1)))}
          />
        </div>

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
            onClick={() => setNoPush(value => !value)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 dark:focus:ring-offset-zinc-900 ${
              noPush ? 'bg-gray-300 dark:bg-zinc-600' : 'bg-blue-600'
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                noPush ? 'translate-x-0' : 'translate-x-5'
              }`}
            />
          </button>
        </div>

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
            onClick={() => setInterventionAutoSplit(value => !value)}
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

        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">복잡도 기반 자동 선택</p>
              <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">
                {autoByComplexity
                  ? '각 태스크의 complexity 라벨에 따라 tier별 role 모델이 자동 선택됩니다.'
                  : '각 태스크의 complexity 값을 무시하고 위 기본 역할 모델을 사용합니다.'}
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={autoByComplexity}
              aria-label="복잡도 기반 자동 선택"
              onClick={() => setAutoByComplexity(value => !value)}
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
              className="pt-2 border-t border-gray-100 dark:border-zinc-800 space-y-2"
            >
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                복잡도: <span className="font-mono">simple</span> / <span className="font-mono">non-simple</span> (자동 계산).
                실패 시 다음 tier로 escalation (트리거: LOGIC_ERROR / MAX_ITER / CHANGES_REQUESTED).
              </p>
              {(['simple', 'standard', 'complex'] as const).map(tier => (
                <div
                  key={tier}
                  className="rounded-lg bg-gray-50 dark:bg-zinc-800/50 px-3 py-2 space-y-1"
                >
                  <div className="font-mono text-[11px] text-gray-600 dark:text-zinc-300">{tier}</div>
                  {ROLES.map(({ key, label }) => {
                    const entry = complexityMap[tier][key]
                    return (
                      <div
                        key={`${tier}-${key}`}
                        className="grid grid-cols-[96px_1fr] gap-2 text-[11px] text-gray-500 dark:text-zinc-400"
                      >
                        <span>{label}</span>
                        <span className="text-gray-600 dark:text-zinc-300">
                          {entry?.provider} · {entry?.model}
                        </span>
                      </div>
                    )
                  })}
                </div>
              ))}
              {tasksWithoutComplexity.length > 0 && (
                <p
                  data-testid="complexity-missing-warning"
                  className="pt-1 text-[11px] text-amber-600 dark:text-amber-400"
                >
                  complexity 라벨이 없는 태스크 {tasksWithoutComplexity.length}개는 'standard'로 실행됩니다
                </p>
              )}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-3 py-2.5 text-xs font-semibold text-gray-600 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={() => setShowAdvanced(value => !value)}
          >
            <span>역할별 override 설정</span>
            <span className="text-gray-400 dark:text-zinc-600">{showAdvanced ? '▲' : '▼'}</span>
          </button>

          {showAdvanced && (
            <div className="px-3 pb-3 space-y-2 border-t border-gray-100 dark:border-zinc-800 pt-2">
              <p className="text-xs text-gray-400 dark:text-zinc-500">
                비워두면 기본 역할 모델 또는 complexity routing 결과를 사용합니다.
              </p>
              {ROLES.map(({ key, label }) => (
                <RoleModelRow
                  key={key}
                  label={label}
                  models={models}
                  value={roleOverrides[key] ?? {}}
                  onChange={cfg => setRoleOverrides(prev => ({ ...prev, [key]: cfg }))}
                  onReset={() => setRoleOverrides(prev => {
                    const next = { ...prev }
                    delete next[key]
                    return next
                  })}
                  allowEmpty
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
            disabled={hasMissingDefaultRoleModel}
          >
            파이프라인 시작
          </button>
        </div>
      </div>
    </div>
  )
}
