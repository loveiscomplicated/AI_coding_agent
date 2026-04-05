import { useState, useEffect } from 'react'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000') as string

const PROVIDER_LABELS: Record<string, string> = {
  claude: 'Anthropic Claude',
  openai: 'OpenAI',
  glm: 'GLM (Zai)',
  ollama: 'Ollama (로컬)',
}

interface ModelOption {
  id: string
  name: string
  provider: string
}

interface Props {
  onClose: () => void
}

function ModelSelector({
  models,
  selectedModel,
  selectedProvider,
  radioName,
  onSelect,
}: {
  models: ModelOption[]
  selectedModel: string
  selectedProvider: string
  radioName: string
  onSelect: (model: ModelOption) => void
}) {
  const grouped = models.reduce<Record<string, ModelOption[]>>((acc, m) => {
    const p = m.provider ?? 'unknown'
    if (!acc[p]) acc[p] = []
    acc[p].push(m)
    return acc
  }, {})
  const providerOrder = ['claude', 'openai', 'glm', 'ollama']
  const sortedProviders = [
    ...providerOrder.filter(p => grouped[p]),
    ...Object.keys(grouped).filter(p => !providerOrder.includes(p)),
  ]

  return (
    <div className="space-y-4">
      {sortedProviders.map(provider => (
        <div key={provider}>
          <p className="text-xs text-zinc-400 dark:text-zinc-500 mb-1.5 px-1">
            {PROVIDER_LABELS[provider] ?? provider}
          </p>
          <div className="space-y-1.5">
            {grouped[provider].map(m => {
              const selected = selectedModel === m.id && selectedProvider === m.provider
              return (
                <label
                  key={`${m.provider}:${m.id}`}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors ${
                    selected
                      ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/40 dark:border-blue-400'
                      : 'border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600'
                  }`}
                >
                  <input
                    type="radio"
                    name={radioName}
                    checked={selected}
                    onChange={() => onSelect(m)}
                    className="accent-blue-500 flex-shrink-0"
                  />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-zinc-800 dark:text-zinc-100 truncate">
                      {m.name}
                    </div>
                    <div className="text-xs text-zinc-400 dark:text-zinc-500 truncate font-mono">
                      {m.id}
                    </div>
                  </div>
                </label>
              )
            })}
          </div>
        </div>
      ))}

      {selectedModel && !models.some(m => m.id === selectedModel) && (
        <div className="px-3 py-2 rounded-lg border border-amber-400 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-500 text-xs text-zinc-700 dark:text-zinc-300">
          현재 설정: <span className="font-mono">{selectedProvider}/{selectedModel}</span>
          <span className="ml-1 text-amber-500">(목록에 없음)</span>
        </div>
      )}
    </div>
  )
}

export function SettingsModal({ onClose }: Props) {
  const [models, setModels] = useState<ModelOption[]>([])
  const [convModel, setConvModel] = useState('')
  const [convProvider, setConvProvider] = useState('')
  const [redesignModel, setRedesignModel] = useState('')
  const [redesignProvider, setRedesignProvider] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/api/chat/models`).then(r => r.json()),
      fetch(`${API_BASE}/api/config/llm`).then(r => r.json()),
    ])
      .then(([modelsData, llmData]) => {
        setModels(modelsData.models ?? [])
        setConvModel(llmData.hotline_conv_model ?? '')
        setConvProvider(llmData.hotline_conv_provider ?? '')
        setRedesignModel(llmData.redesign_model ?? '')
        setRedesignProvider(llmData.redesign_provider ?? '')
        setLoading(false)
      })
      .catch(() => {
        setError('설정을 불러오지 못했습니다.')
        setLoading(false)
      })
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setError('')
    setSaved(false)
    try {
      const res = await fetch(`${API_BASE}/api/config/llm`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          hotline_conv_model: convModel,
          hotline_conv_provider: convProvider,
          redesign_model: redesignModel,
          redesign_provider: redesignProvider,
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail ?? '저장 실패')
      }
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '저장 중 오류가 발생했습니다.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white dark:bg-zinc-900 rounded-xl shadow-xl w-[860px] max-w-[95vw] max-h-[80vh] flex flex-col border border-zinc-200 dark:border-zinc-700">
        {/* 헤더 */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200 dark:border-zinc-700 flex-shrink-0">
          <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">설정</h2>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors"
          >
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12"/>
            </svg>
          </button>
        </div>

        {/* 본문 (스크롤 가능) */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="px-5 py-5 text-xs text-zinc-400 dark:text-zinc-500">불러오는 중…</div>
          ) : models.length === 0 ? (
            <div className="px-5 py-5 text-xs text-zinc-400 dark:text-zinc-500">사용 가능한 모델이 없습니다.</div>
          ) : (
            <div className="grid grid-cols-2 divide-x divide-zinc-200 dark:divide-zinc-700">
              {/* 왼쪽 컬럼: Discord 핫라인 */}
              <div className="px-5 py-5">
                <p className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-3">
                  Discord 대화 LLM
                </p>
                <ModelSelector
                  models={models}
                  selectedModel={convModel}
                  selectedProvider={convProvider}
                  radioName="conv_model"
                  onSelect={m => { setConvModel(m.id); setConvProvider(m.provider) }}
                />
              </div>

              {/* 오른쪽 컬럼: AI 재설계 */}
              <div className="px-5 py-5">
                <p className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-3">
                  AI 재설계 LLM
                </p>
                <ModelSelector
                  models={models}
                  selectedModel={redesignModel}
                  selectedProvider={redesignProvider}
                  radioName="redesign_model"
                  onSelect={m => { setRedesignModel(m.id); setRedesignProvider(m.provider) }}
                />
              </div>
            </div>
          )}
        </div>

        {/* 푸터 */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-zinc-200 dark:border-zinc-700 flex-shrink-0">
          {error ? (
            <span className="text-xs text-red-500">{error}</span>
          ) : saved ? (
            <span className="text-xs text-green-500">✓ 저장되었습니다</span>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-xs rounded-lg text-zinc-500 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
            >
              닫기
            </button>
            <button
              onClick={handleSave}
              disabled={saving || loading || !convModel || !redesignModel}
              className="px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {saving ? '저장 중…' : '저장'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
