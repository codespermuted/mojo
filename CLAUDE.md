# Mojo — Development Guide

## Project Overview
도메인 전문가의 암묵지를 Claude/Codex 세션에서 자동 축적하는 시스템.
Hooks(자동 추출) → JSONL 파싱(Claude+Codex) → LLM 추출(headless CLI, $0)
→ SQLite + Obsidian vault(md, 리뷰 UI) → MOJO.md advisory.

## Architecture
- **Hooks** (stdlib-only Python): SessionEnd → 세션 등록 + detached 자동 추출
  스폰. MOJO_EXTRACTION env로 재귀 차단. Stop → 정정 시그널 감지.
- **Extract**: parser(Claude/Codex 자동 감지) → signals → filter → structure
  → dedup. LLM 호출은 `extract/llm_backend.py`의 백엔드 추상화 경유:
  claude-cli(기본, 구독, API 키 strip) | codex-cli | api.
- **지식 모델**: knowledge 행 = 주장(claim) 1개. observations 테이블이
  프로젝트별 supports/refutes 누적 → 서로 다른 프로젝트 ≥2 supports +
  refutes 0 이면 generalization_suggested. 승격은 항상 사람이.
- **Vault** (`serve/vault.py`): 주장 1개 = md 노트 1개. 소유권 분리 —
  인간 편집 필드는 vault가 진실, 기계 필드(observations·grade·related)는
  DB가 진실 (export마다 재생성). REVIEW-QUEUE.md = 리뷰 인박스.
- **Serve**: packer(토큰 예산) → sync(CLAUDE.md/SKILL.md, 명시적 legacy)
- **Storage**: SQLite (knowledge, observations, raw_sessions, injections,
  extraction_costs) + Obsidian vault (기본 ~/mojo-vault)

## Code Conventions
- Python 3.10+, type hints 사용
- `db_ops.py`가 모든 DB 접근 중앙화. 직접 SQL 쓰지 않기.
- LLM 프롬프트는 `extract/prompts/` 디렉토리에 XML 파일로 관리
- rich 라이브러리로 CLI 출력. print 대신 console.print 사용.
- 에러 시 silent fail (hooks는 Claude Code를 절대 block하면 안 됨)

## Key Files
- `db/schema.sql`: SQLite 스키마 정의
- `extract/pipeline.py`: 추출 오케스트레이터
- `extract/signals.py`: 정정 시그널 감지 (rule-based, 무료)
- `scan.py`: git 히스토리 + 폴더 스캔 (rule-based, 무료)
- `serve/sync.py`: CLAUDE.md / SKILL.md 생성
- `serve/packer.py`: 토큰 예산 관리
- `seeds/seed_knowledge.json`: 초기 시드 데이터

## Testing
- `pytest tests/` 로 실행
- fixtures/에 샘플 JSONL 준비
- LLM 호출이 필요한 테스트는 `@pytest.mark.integration` 으로 분리

## Design Principles
1. Zero-friction: 사용자 워크플로우 변경 없음
2. Signal over noise: 도메인 특이 지식만 추출
3. Token-efficient: 주입 시 토큰 예산 관리
4. 가성비: Haiku 필터 → Sonnet 구조화 (2단계)
