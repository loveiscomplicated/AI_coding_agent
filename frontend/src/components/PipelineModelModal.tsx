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

// ── PipelineModelModal ────────────────────────────────────────────────────────

interface PipelineModelModalProps {
  models: AvailableModel[]
  onConfirm: (providerFast: string, modelFast: string, providerCapable: string, modelCapable: string) => void
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

  const handleFastProviderChange = (p: string) => {
    setFastProvider(p)
    setFastModel(modelsForProvider(p)[0]?.id ?? '')
  }

  const handleCapableProviderChange = (p: string) => {
    setCapableProvider(p)
    const list = modelsForProvider(p)
    setCapableModel(list[list.length - 1]?.id ?? '')
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

        <div className="flex justify-end gap-2 pt-1">
          <button
            className="rounded-lg border border-gray-300 dark:border-zinc-600 px-4 py-2 text-sm text-gray-600 dark:text-zinc-300 hover:bg-gray-50 dark:hover:bg-zinc-800 transition-colors"
            onClick={onCancel}
          >
            취소
          </button>
          <button
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            onClick={() => onConfirm(fastProvider, fastModel, capableProvider, capableModel)}
            disabled={!fastModel || !capableModel}
          >
            파이프라인 시작 🚀
          </button>
        </div>
      </div>
    </div>
  )
}
