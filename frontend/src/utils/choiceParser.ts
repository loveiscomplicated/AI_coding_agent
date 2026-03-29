const CHOICE_TAG_RE = /<choice>([\s\S]*?)<\/choice>/g

/**
 * assistant 메시지에서 <choice> 태그를 추출합니다.
 * 태그는 텍스트에서 제거되고, 선택지 목록으로 분리됩니다.
 */
export function parseChoices(content: string): { text: string; choices: string[] } {
  const choices: string[] = []
  const text = content.replace(CHOICE_TAG_RE, (_, inner: string) => {
    choices.push(inner.trim())
    return ''
  }).trim()
  return { text, choices }
}

/**
 * 스트리밍 도중 <choice> 태그를 제거하되 내용은 유지합니다.
 * 부분적으로 열린 태그(<choice>)도 제거합니다.
 */
export function stripChoiceTags(content: string): string {
  return content
    .replace(CHOICE_TAG_RE, '$1')   // 완전한 태그: 내용만 남김
    .replace(/<\/?choice>/g, '')    // 불완전한 태그: 완전 제거
}
