interface Props {
  completeness: number
  hint: string
}

function barColor(pct: number): string {
  if (pct < 30) return 'bg-red-500'
  if (pct < 60) return 'bg-yellow-500'
  if (pct < 80) return 'bg-orange-400'
  return 'bg-green-500'
}

export function CompletionGauge({ completeness, hint }: Props) {
  return (
    <div data-testid="completion-gauge" className="px-4 py-3 border-b border-gray-200 dark:border-zinc-700 bg-gray-50 dark:bg-zinc-900">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-gray-600 dark:text-zinc-400">완성도</span>
        <span className="text-xs font-bold text-gray-800 dark:text-zinc-200">{completeness}%</span>
      </div>
      <div className="w-full bg-gray-200 dark:bg-zinc-700 rounded-full h-2">
        <div
          data-testid="gauge-bar"
          className={`h-2 rounded-full transition-all duration-500 ${barColor(completeness)}`}
          style={{ width: `${completeness}%` }}
        />
      </div>
      <p data-testid="gauge-hint" className="mt-1 text-xs text-gray-500 dark:text-zinc-500 italic">
        {hint}
      </p>
    </div>
  )
}
