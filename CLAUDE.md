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

## Hook 배포 — 단일 진실은 레포 (⚠️ 재발 방지)
실행되는 hook은 `~/.claude/settings.json`이 가리키는 `~/.mojo/hooks/`다.
레포 `hooks/` → (uv 빌드) 설치본 site-packages → (`mojo init` 복사) `~/.mojo/hooks/`,
이 3계층은 **항상 일치해야 한다**. 어긋나면 재귀 가드 같은 fix가 조용히 소실된다.
- **배포본(`~/.mojo/hooks/`)을 손으로 고치지 않는다.** `mojo init`이 설치본으로
  덮어써서 손수정이 날아간다 (실제로 겪음). 고칠 건 레포에서만, 아래로 흘려보낸다.
- **hook 수정은 설치본에 자동 반영되지 않는다.** `uv tool install --force`는 버전이
  같으면 빌드를 캐시 재사용한다 → `pyproject.toml` 버전을 올리거나 hook을 직접 복사해야
  설치본·배포본에 반영된다. 수정 후 3계층 md5 일치를 확인한다.

## 재귀 차단은 hook 경계에서 (왜)
SessionEnd가 spawn하는 extract 서브트리에 `MOJO_EXTRACTION=1`을 **hook이 직접** 건다.
llm_backend가 거는 것에만 의존하면, 설치 mojo 버전·백엔드가 바뀔 때 headless `claude -p`가
플래그 없이 떠서 추출→세션종료→추출 무한 폭주(토큰 전소)가 난다. hook은 항상 도는
단일 관문이므로 거기서 거는 것이 버전·백엔드와 무관하게 안전하다.

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
