import { useState, KeyboardEvent, useRef, useCallback, forwardRef, useImperativeHandle } from 'react'
import { ChatAttachment } from '../types/meeting'

interface Props {
  onSend: (text: string, attachments?: ChatAttachment[]) => void
  disabled: boolean
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

export const MessageInput = forwardRef<MessageInputRef, Props>(({ onSend, disabled }, ref) => {
  const [value, setValue] = useState('')
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

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

  return (
    <div className="border-t border-gray-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-4 py-3">
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

      {/* 입력 행 */}
      <div className="flex items-end gap-2">
        <button
          type="button"
          className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full border border-gray-300 dark:border-zinc-600 text-gray-500 dark:text-zinc-400 hover:bg-gray-100 dark:hover:bg-zinc-800 transition-colors disabled:opacity-40 disabled:cursor-not-allowed mb-0.5"
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

        <textarea
          className="flex-1 resize-none rounded-xl border border-gray-300 dark:border-zinc-600 bg-white dark:bg-zinc-800 text-gray-900 dark:text-zinc-100 placeholder-gray-400 dark:placeholder-zinc-500 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          rows={2}
          placeholder="메시지를 입력하세요… (Enter: 전송, Shift+Enter: 줄바꿈)"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onDragOver={(e) => e.preventDefault()}
          disabled={disabled}
        />

        <button
          className="shrink-0 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors mb-0.5"
          onClick={handleSend}
          disabled={disabled || (!value.trim() && attachments.length === 0)}
          aria-label="전송"
        >
          전송
        </button>
      </div>
    </div>
  )
})

MessageInput.displayName = 'MessageInput'
