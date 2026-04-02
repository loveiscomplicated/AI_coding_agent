import { useState, KeyboardEvent, useRef, useCallback, forwardRef, useImperativeHandle, useEffect } from 'react'
import { ChatAttachment } from '../types/meeting'

export interface ModelOption {
  id: string
  name: string
  provider: string
}

interface Props {
  onSend: (text: string, attachments?: ChatAttachment[]) => void
  disabled: boolean
  models?: ModelOption[]
  selectedModel?: string
  onModelChange?: (model: string) => void
}

export interface MessageInputRef {
  addFiles: (files: File[]) => void
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve((reader.result as string).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

export const MessageInput = forwardRef<MessageInputRef, Props>(
  ({ onSend, disabled, models, selectedModel, onModelChange }, ref) => {
    const [value, setValue] = useState('')
    const [attachments, setAttachments] = useState<ChatAttachment[]>([])
    const [modelOpen, setModelOpen] = useState(false)
    const [dropUp, setDropUp] = useState(true)
    const fileInputRef = useRef<HTMLInputElement>(null)
    const modelDropdownRef = useRef<HTMLDivElement>(null)

    // 드롭다운 외부 클릭 시 닫기
    useEffect(() => {
      if (!modelOpen) return
      const handler = (e: MouseEvent) => {
        if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
          setModelOpen(false)
        }
      }
      document.addEventListener('mousedown', handler)
      return () => document.removeEventListener('mousedown', handler)
    }, [modelOpen])

    const processFiles = useCallback(async (files: File[]) => {
      const supported = files.filter(
        (f) => f.type.startsWith('image/') || f.type === 'application/pdf'
      )
      const results = await Promise.all(
        supported.map(async (file) => ({
          type: (file.type.startsWith('image/') ? 'image' : 'document') as ChatAttachment['type'],
          mediaType: file.type,
          data: await readFileAsBase64(file),
          name: file.name,
          size: file.size,
        }))
      )
      setAttachments((prev) => [...prev, ...results])
    }, [])

    useImperativeHandle(ref, () => ({ addFiles: processFiles }))

    const handleSend = () => {
      if (!value.trim() && attachments.length === 0) return
      onSend(value.trim(), attachments.length > 0 ? attachments : undefined)
      setValue('')
      setAttachments([])
    }

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    }

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
        processFiles(Array.from(e.target.files))
        e.target.value = ''
      }
    }

    const selectedModelName = models?.find((m) => m.id === selectedModel)?.name ?? selectedModel ?? ''

    return (
      <div className="border-t border-gray-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 pt-3 pb-2">
        {/* 첨부파일 프리뷰 */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {attachments.map((att, i) => (
              <div
                key={i}
                className="relative flex items-center gap-1.5 rounded-xl border border-gray-200 dark:border-zinc-700 bg-gray-50 dark:bg-zinc-800 overflow-hidden"
              >
                {att.type === 'image' ? (
                  <img
                    src={`data:${att.mediaType};base64,${att.data}`}
                    alt={att.name}
                    className="h-16 w-16 object-cover"
                  />
                ) : (
                  <div className="flex items-center gap-1.5 px-3 py-2">
                    <svg className="w-4 h-4 text-gray-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                      <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <div className="flex flex-col max-w-[120px]">
                      <span className="text-xs text-gray-700 dark:text-zinc-300 truncate">{att.name}</span>
                      <span className="text-xs text-gray-400 dark:text-zinc-500">{formatBytes(att.size)}</span>
                    </div>
                  </div>
                )}
                <button
                  className="absolute top-1 right-1 w-4 h-4 flex items-center justify-center rounded-full bg-black/50 text-white hover:bg-black/70 transition-colors"
                  onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                >
                  <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <path d="M18 6 6 18M6 6l12 12"/>
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 텍스트 입력 */}
        <textarea
          className="w-full resize-none rounded-xl border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 placeholder-gray-400 dark:placeholder-zinc-500 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          rows={2}
          placeholder="메시지를 입력하세요… (Enter: 전송, Shift+Enter: 줄바꿈)"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onDragOver={(e) => e.preventDefault()}
          disabled={disabled}
        />

        {/* 툴바 행 */}
        <div className="flex items-center justify-between mt-2">
          {/* 왼쪽: 파일 첨부 */}
          <div>
            <button
              type="button"
              className="w-8 h-8 flex items-center justify-center rounded-full border border-gray-300 dark:border-zinc-600 text-gray-500 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              title="이미지 또는 PDF 첨부"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M12 5v14M5 12h14"/>
              </svg>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*,application/pdf"
              className="hidden"
              onChange={handleFileChange}
            />
          </div>

          {/* 오른쪽: 모델 선택 + 전송 */}
          <div className="flex items-center gap-2">
            {/* 모델 드롭다운 */}
            {models && models.length > 0 && (
              <div ref={modelDropdownRef} className="relative">
                <button
                  type="button"
                  onClick={() => {
                    if (!modelOpen && modelDropdownRef.current) {
                      const rect = modelDropdownRef.current.getBoundingClientRect()
                      setDropUp(rect.top > 220)
                    }
                    setModelOpen((o) => !o)
                  }}
                  className="flex items-center gap-1 text-xs text-gray-500 dark:text-zinc-400 hover:text-gray-700 dark:hover:text-zinc-200 px-2 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-zinc-800 transition-colors"
                  title="모델 선택"
                >
                  <span className="font-medium">{selectedModelName}</span>
                  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="m6 9 6 6 6-6"/>
                  </svg>
                </button>

                {modelOpen && (
                  <div className={`absolute right-0 bg-white dark:bg-zinc-800 border border-gray-200 dark:border-zinc-700 rounded-xl shadow-lg py-1 min-w-[180px] z-50 max-h-64 overflow-y-auto ${dropUp ? 'bottom-full mb-1.5' : 'top-full mt-1.5'}`}>
                    {/* provider별 그룹핑 */}
                    {Array.from(new Set(models.map((m) => m.provider))).map((provider) => (
                      <div key={provider}>
                        <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-zinc-500">
                          {provider}
                        </div>
                        {models
                          .filter((m) => m.provider === provider)
                          .map((m) => (
                            <button
                              key={m.id}
                              type="button"
                              onClick={() => {
                                onModelChange?.(m.id)
                                setModelOpen(false)
                              }}
                              className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-zinc-700 transition-colors ${
                                m.id === selectedModel
                                  ? 'text-blue-600 dark:text-blue-400 font-semibold'
                                  : 'text-gray-700 dark:text-zinc-300'
                              }`}
                            >
                              {m.name}
                            </button>
                          ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 전송 버튼 */}
            <button
              className="rounded-xl bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              onClick={handleSend}
              disabled={disabled || (!value.trim() && attachments.length === 0)}
              aria-label="전송"
            >
              전송
            </button>
          </div>
        </div>
      </div>
    )
  }
)

MessageInput.displayName = 'MessageInput'
