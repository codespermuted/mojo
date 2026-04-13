# Mojo — Development Guide

## Project Overview
도메인 전문가의 암묵지를 Claude Code가 소비 가능한 형태로 자동 축적하는 시스템.
Claude Code의 네이티브 Hooks → JSONL 파싱 → LLM 추출 → CLAUDE.md/Skills 주입.

## Architecture
- **Hooks** (Python scripts): SessionEnd, Stop → 세션 등록 + 정정 시그널 감지
- **Extract** (Python): parser → signals → filter(Haiku) → structure(Sonnet) → dedup
- **Serve** (Python): packer(토큰 예산) → sync(CLAUDE.md/SKILL.md 생성)
- **Storage**: SQLite (knowledge, raw_sessions, injections, extraction_costs)

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
