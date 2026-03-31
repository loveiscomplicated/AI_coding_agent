const PROJECTS_KEY = 'projects_v3'

export interface Project {
  id: string
  name: string
  rootDir: string    // 프로젝트 루트 = repo_path = git root
  createdAt: string
}

/** rootDir/data/tasks.yaml */
export function projectTasksPath(p: Pick<Project, 'rootDir'>): string {
  return p.rootDir.replace(/\/+$/, '') + '/data/tasks.yaml'
}

/** rootDir/data/reports */
export function projectReportsDir(p: Pick<Project, 'rootDir'>): string {
  return p.rootDir.replace(/\/+$/, '') + '/data/reports'
}

export function loadProjects(): Project[] {
  try {
    return JSON.parse(localStorage.getItem(PROJECTS_KEY) ?? '[]')
  } catch {
    return []
  }
}

export function saveProjects(projects: Project[]) {
  localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects))
}
