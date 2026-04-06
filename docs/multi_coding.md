터미널 1 — 백엔드:
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000

터미널 2 — 프론트엔드:
cd frontend && npm run dev

도커 리빌드:
docker build -f docker/Dockerfile.test -t ai-coding-agent-test-runner .                                




⏺ 현재 이 시스템에서 사용 가능한 provider는 claude, openai, glm, ollama 4가지입니다. 각 provider에서 코딩 에이전트로 쓸 만한 모델들 기준으로 추천합니다.                                      
                                                                                                                                                                                              
  ---                                                                                                                                                                                         
  역할별 특성 요약 (다시)                                                                                                                                                                     
  - 오케스트레이터: 실패 분석·힌트·재설계 → 추론력                                                                                                                                            
  - 테스트 작성: 요구사항 → 테스트 명세 → 정확한 이해력                                                                                                                                       
  - 구현: 반복 호출 多 → 속도·비용                                                                                                                                                            
  - 리뷰어: 코드 품질 판단 → 추론력                                                                                                                                                           
                                                                                                                                                                                              
  ---                                                                                                                                                                                         
  조합 1 — 균형형 (openai 기반)                                                                                                                                                               
  오케스트레이터:  openai / gpt-4.1                                                                                                                                                           
  테스트 작성:    openai / gpt-4.1                                                                                                                                                            
  구현:           openai / gpt-4.1-mini                                                                                                                                                       
  리뷰어:         openai / gpt-4.1                                                                                                                                                            
  gpt-4.1이 코딩 벤치마크에서 현재 최상위권이고, mini는 구현 루프 비용 절감에 효과적입니다.                                                                                                   
                                                                                                                                                                                              
  조합 2 — 크로스 프로바이더형                                                                                                                                                                
  오케스트레이터:  openai / gpt-4.1          ← 추론·분석                                                                                                                                      
  테스트 작성:    claude / Sonnet 4.6        ← 명세 해석 정확도                                                                                                                               
  구현:           glm   / glm-4-flash        ← 빠르고 저렴                                                                                                                                    
  리뷰어:         openai / gpt-4.1          ← 코드 품질 판단                                                                                                                                  
  각 provider의 강점을 역할에 매핑하는 방식입니다. task-014 같은 명세 혼동 문제는 테스트 작성을 Claude Sonnet에 할당하면 완화됩니다.                                                          
                                                                                                                                                                                              
  조합 3 — 로컬 전용 (ollama, 비용 0)                                                                                                                                                         
  오케스트레이터:  ollama / qwen2.5-coder:32b                                                                                                                                                 
  테스트 작성:    ollama / qwen2.5-coder:32b                                                                                                                                                  
  구현:           ollama / qwen2.5-coder:7b                                                                                                                                                   
  리뷰어:         ollama / qwen2.5-coder:14b                                                                                                                                                  
  API 비용 없이 돌리는 경우. 구현만 7b로 낮추고 나머지는 14b/32b 권장. 단 컨텍스트 길이와 추론 품질이 API 모델 대비 낮아 복잡한 태스크는 실패율이 올라갑니다.                                 
                                                                                                                                                                                              
  ---                                                                                                                                                                                         
  현재 보고된 실패 패턴(task-014) 관점의 추천                                                                                                                                                 
                                                                                                                                                                                              
  명세 해석 오류가 주 원인이었으므로, 테스트 작성에 가장 강한 모델을 배치하는 게 핵심입니다:                                                                                                  
                                                                                                                                                                                              
  오케스트레이터:  openai / gpt-4.1      (실패 분석·힌트 품질)                                                                                                                                
  테스트 작성:    claude / Sonnet 4.6   (요구사항 이해·명세 해석)                                                                                                                             
  구현:           openai / gpt-4.1-mini (반복 루프 비용)                                                                                                                                      
  리뷰어:         openai / gpt-4.1      (최종 품질 게이트)                                                                                                                                    
                                                                                                                                                                                              
  이 조합이 현재 프로젝트 실패 패턴에 가장 직접적으로 대응합니다.                                                                                                                             
                                                                                                                                                                                              
✻ Crunched for 39s                                                                                                                                                                            
                                                                                                                                                                                              
❯ GLM은 별로야?                                                                                                                                                                               
                                                                                                                                                                                              
  Read 1 file (ctrl+o to expand)                                                                                                                                                              
                                                                                                                                                                                              
⏺ 코드를 보면 GLM을 쓰면서 이미 몇 가지 제약을 workaround한 흔적이 있습니다:                                                                                                                  
                                                                                                                                                                                              
  - temperature=0.0 미지원 (1만 허용) → 코딩 에이전트에서 determinism이 필요한 경우 불리                                                                                                      
  - tool_calls와 content 공존 불가 → 메시지 구조 제한                                                                                                                                         
  - 1213/1214 에러코드별 특수 처리 필요                                                                                                                                                       
                                                                                                                                                                                              
  그리고 실제 이 리포트에서 glm-4.7이 오케스트레이터였을 때 task-014의 태스크 명세를 환경에 맞게 번역하지 못하고 그대로 넘겼습니다.                                                           
                                                                                                                                                                                              
  GLM이 별로인 이유를 정리하면:                                                                                                                                                               
                                                                                                                                                                                              
  1. 코딩 품질: GLM은 중국어 중심 학습 데이터 비중이 높아 영어/한국어 코딩 명세 해석 정확도가 GPT·Claude 대비 낮음                                                                            
  2. API 안정성: 이미 코드에 1213/1214 에러 핸들링, temperature 제한 등 다른 provider에는 없는 workaround가 있음 — 실제 운영 중 예상치 못한 오류가 더 자주 발생한다는 뜻                      
  3. 오케스트레이터로 쓰기엔 약함: 복잡한 실패 분석·태스크 재설계 같은 고추론 작업에서 GPT-4.1/Claude Sonnet보다 판단력이 떨어짐                                                              
                                                                                                                                                                                              
  그나마 GLM이 유리한 상황:                                                                                                                                                                   
                                                                                                                                                                                              
  - 비용이 매우 저렴하고 속도가 빠른 glm-4-flash 계열은 구현 루프처럼 단순 반복 작업에는 쓸 만함                                                                                              
  - 한국어 프롬프트 처리는 GPT보다 나쁘지 않음                                                                                                                                                
                                                                                                                                                                                              
  결론적으로 오케스트레이터·테스트 작성·리뷰어에는 비추천, 구현 역할의 저비용 대안으로만 고려하는 게 맞습니다.    