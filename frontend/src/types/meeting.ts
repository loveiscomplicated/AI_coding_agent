// 프로젝트 컨텍스트 JSON 구조
// Opus가 프로젝트 성격에 맞게 필드를 자유롭게 결정하므로,
// MeetingContext는 최소 고정 필드(version, meeting_meta)만 강제하고
// 나머지는 index signature로 허용한다.

export interface MeetingTask {
  id: string
  title: string
  description: string
  priority: 'high' | 'medium' | 'low'
  estimated_hours: number
}

export interface Milestone {
  id: number
  title: string
  tasks: MeetingTask[]
  deadline: string
}

export interface TechStack {
  languages: string[]
  frameworks: string[]
  infra: string[]
  ai_models: string[]
}

export interface SandboxSpec {
  cpu_limit: string
  memory_limit: string
  timeout_minutes: number
}

export interface AgentConfig {
  orchestrator_model: string
  worker_models: string[]
  max_concurrent_agents: number
  sandbox_spec: SandboxSpec
}

export interface MeetingMeta {
  date: string
  duration_min: number
  completeness: number
  version: number
  hint?: string  // Opus가 현재 상태 또는 다음 필요 정보를 설명
}

export interface MeetingContext {
  version: number
  meeting_meta: MeetingMeta
  // 알려진 선택적 필드 (소프트웨어 프로젝트에서 자주 등장)
  project?: {
    name?: string
    overview?: string
    goals?: string[]
    non_goals?: string[]
    [key: string]: unknown
  }
  tech_stack?: TechStack
  constraints?: string[]
  milestones?: Milestone[]
  agent_config?: AgentConfig
  // Opus가 프로젝트 성격에 따라 추가하는 임의 필드
  [key: string]: unknown
}

// 최소 빈 컨텍스트 — Opus가 채울 모든 필드는 고정하지 않는다
export function emptyMeetingContext(): MeetingContext {
  return {
    version: 1,
    meeting_meta: {
      date: new Date().toISOString().split('T')[0],
      duration_min: 0,
      completeness: 0,
      version: 1,
    },
  }
}

// 대화 메시지
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  /** 응답에서 추출된 컨텍스트 (assistant 메시지만) */
  context?: Partial<MeetingContext>
}

// 저장된 회의 기록
export interface MeetingRecord {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messages: ChatMessage[]
  context: MeetingContext
  /** Haiku가 생성한 마크다운 컨텍스트 문서 (frontmatter + 본문) */
  contextDoc?: string
  isFinished: boolean
}
