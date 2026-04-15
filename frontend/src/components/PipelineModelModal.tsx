/**
 * PipelineModelModal.tsx
 *
 * 파이프라인 시작/재개 시 코딩 에이전트·오케스트레이터 모델을 선택하는 팝업.
 * TaskDraftPanel, DashboardPage 등 여러 곳에서 공유한다.
 */

import { useState } from 'react'

export interface AvailableModel {
  id: string
  name: string
  provider: string
}

export type RoleOverride = { provider?: string; model?: string }
export type RoleOverrides = Record<string, RoleOverride>

// ── ModelSelect ───────────────────────────────────────────────────────────────

interface ModelSelectProps {
  label: string
  hint: string
  models: AvailableModel[]
  selectedProvider: string
  selectedModel: string
  onProviderChange: (p: string) => void
  onModelChange: (m: string) => void
}

export function ModelSelect({
  label,
  hint,
  models,
  selectedProvider,
  selectedModel,
  onProviderChange,
  onModelChange,
}: ModelSelectProps) {
  const providers = Array.from(new Set(models.map(m => m.provider)))
  const filtered = models.filter(m => m.provider === selectedProvider)

  return (
    <div className="rounded-xl border border-gray-200 dark:border-zinc-700 p-3 space-y-2">
      <div>
        <p className="text-xs font-semibold text-gray-700 dark:text-zinc-200">{label}</p>
        <p className="text-xs text-gray-400 dark:text-zinc-500 mt-0.5">{hint}</p>
      </div>
      <div className="flex gap-2">
        <select
          className="rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-xs text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500 w-28 shrink-0"
          value={selectedProvider}
          onChange={e => onProviderChange(e.target.value)}
        >
          {providers.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          className="flex-1 rounded-lg border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-xs text-gray-700 dark:text-zinc-200 px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={selectedModel}
          onChange={e => onModelChange(e.target.value)}
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
  ) => void
  onCancel: () => void
}

export function PipelineModelModal({ models, onConfirm, onCancel }: PipelineModelModalProps) {
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
  const [roleOverrides, setRoleOverrides] = useState<RoleOverrides>({})
  const [showAdvanced, setShowAdvanced] = useState(false)

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
    onConfirm(
      fastProvider, fastModel,
      capableProvider, capableModel,
      agentCount,
      Object.keys(roleModels).length > 0 ? roleModels : undefined,
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

        {/* 역할별 모델 설정 */}
        <div className="rounded-xl border border-gray-200 dark:border-zinc-700 overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-3 py-2.5 text-xs font-semibold text-gray-600 dark:text-zinc-400 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={() => setShowAdvanced(v => !v)}
          >
            <span>역할별 모델 설정</span>
            <span className="text-gray-400 dark:text-zinc-600">{showAdvanced ? '▲' : '▼'}</span>
          </button>

          {showAdvanced && (
            <div className="px-3 pb-3 space-y-2 border-t border-gray-100 dark:border-zinc-800 pt-2">
              <p className="text-xs text-gray-400 dark:text-zinc-500">
                비워두면 위의 코딩 에이전트 모델이 사용됩니다.
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
