<!-- i18n: source=docs/workflow.md source-sha256=c749365b6b33 -->
# 에이전트 개발 워크플로

원문(영어): [workflow.md](workflow.md)

이 저장소는 코딩 에이전트(Claude Code, Codex, Pi를 동등하게 1급 지원)가 개발하고, 사람 메인테이너가
결정을 승인하는 구조로 만들어져 있다. 이 문서는 각 구성 요소가 어떻게 맞물리는지 설명한다.

## 1. 하나의 원본, 세 개의 에이전트

```
.agents/                      도구 중립 원본 (여기를 수정한다)
├── rules/*.md                경로별 규칙
├── agents/*.md               서브 에이전트 정의
└── skills/*/SKILL.md         워크플로 (Agent Skills 표준)
        │
        │  make agents-sync  (tools/agentsync.py, 불일치하면 CI 실패)
        ▼
.claude/rules, .claude/agents, .claude/skills      Claude Code
.codex/agents/*.toml                               Codex   (skills는 .agents/skills에서 읽음)
.pi/agents/*.md                                    Pi      (skills는 .agents/skills에서 읽음)
AGENTS.md 인덱스 블록                               Codex, Pi (Claude는 CLAUDE.md 경유)
```

| 기능 | Claude Code | Codex | Pi |
|---|---|---|---|
| 프로젝트 지침 | `CLAUDE.md` -> `@AGENTS.md` | `AGENTS.md` | `AGENTS.md` |
| 규칙 | `.claude/rules/` (자동, 경로 기반) | `AGENTS.md`의 규칙 인덱스 | `AGENTS.md`의 규칙 인덱스 |
| 스킬 | `.claude/skills/` | `.agents/skills/` | `.agents/skills/` |
| 서브 에이전트 | `.claude/agents/` (Agent 도구) | `.codex/agents/*.toml` (agent role) | `.pi/extensions/subagent`를 통한 `.pi/agents/` (`agentScope: "project"`) |
| 편집 훅 | `.claude/settings.json` | `.codex/hooks.json` | `.pi/extensions/asyncopation-guard.ts` |

세 편집 훅은 모두 같은 스크립트 `tools/hooks/agent_hook.py`를 호출한다.
- **pre**: 생성 파일 편집을 막는다 (대신 `.agents/`를 수정).
- **post**: 편집한 파일에 `tools/guards`와 `ruff check`를 돌리고, 위반 사항을 즉시 에이전트에게
  돌려준다.

## 2. 기능 루프

모든 변경은 `feature-loop` 스킬을 따른다.

```
 frame ──► research ──► design check ──► red tests ──► implement ──► refactor
 (계획       (django-     (architect;      (test-writer)  (편집마다 훅
  항목)       researcher)  필요 시 ADR)                     피드백)
                                                                 │
   close ◄── perf (핫 패스) ◄── review-change ◄──────────────────┘
 (계획 체크,   (perf-analyst)   (arch-guardian + code-reviewer
  문서, ledger,                  + test-reviewer, 병렬)
  번역)
```

게이트: 가드 통과 -> 테스트가 올바른 이유로 실패(red) -> `make check` + `make test-int` 통과
-> 열린 BLOCKER/MAJOR 리뷰 지적 없음 -> 설명되지 않은 10% 초과 성능 저하 없음 -> `make check-all`.

문서는 코드와 함께 움직인다: `docs-sync` 규칙이 변경 종류마다 같은 변경에서 함께 고쳐야 할 문서를
정해 두고, 빠뜨리면 `arch-guardian`이 MAJOR로 보고한다.

작업은 GitHub에서 관리한다: Phase마다 마일스톤 하나, 로드맵 항목마다 이슈 하나, 라벨은 종류
(`feature`, `perf`, `test`, `ci`, ...), 레이어(`area:*`), 백엔드(`backend:*`), 진행 상태
(`needs-decision`, `blocked`)로 나눈다. PR은 `Closes #N`으로 이슈를 닫는다.

사람이 결정하는 것: ADR 승인, 가드/규칙 변경, 벤치마크 기준선 갱신, 릴리스.

브랜치: 작업은 `develop`에서 딴 `<prefix>/<kebab-summary>` 브랜치에서 하고 `develop`으로 PR을
보낸다. `main`은 릴리스 머지만 받는다. GitHub ruleset으로 강제된다: `develop` 머지에는 코드 오너
(@devgyurak)의 승인이 필요하고 메인테이너는 bypass 머지할 수 있다. `main`에는 누구도 직접 푸시할 수
없고 `develop`에서 온 PR만 받는다. 릴리스는 `main`의 `vX.Y.Z` 태그이고, 태그를 푸시하면 릴리스 CI가
돈다(아직 없음, #26). CI(`ci.yml`)는 모든 PR과 `develop`·`main` 푸시에서 돌기 때문에 back-merge와
bypass 머지도 검사된다. 커밋/PR 제목은 접두사 하나를 쓴다:
`ADD:` `FIX:` `REF:` `DEL:` `DOCS:` `UPT:` (`commit` 스킬).

## 3. 서브 에이전트

| 에이전트 | 권한 | 루프에서의 역할 |
|---|---|---|
| `architect` | read-only | 설계 검토, ADR 리뷰 |
| `django-researcher` | read-only | Django 호출 체인, 숨은 I/O, 포팅할 내부 API |
| `test-writer` | write | red 단계: 스펙으로부터 실패하는 테스트 작성 |
| `arch-guardian` | review | diff의 아키텍처 리뷰 |
| `code-reviewer` | review | 정합성 리뷰 (async, 트랜잭션, 드라이버) |
| `test-reviewer` | review | 테스트 규칙 준수와 충분성 |
| `perf-analyst` | review | 벤치마크 실행과 해석 |
| `translator` | write | `-XX` 번역본 동기화 |

`review` 에이전트는 명령(테스트, 벤치마크)은 실행할 수 있지만 파일은 절대 수정하지 않는다.

## 4. 기계적 강제

| 계층 | 도구 | 시점 |
|---|---|---|
| 포맷, 린트 | ruff | 편집 훅, `make lint`, pre-commit, CI |
| 타입 | ty | `make typecheck`, CI |
| 레이어 | import-linter | `make guard`, pre-commit, CI |
| 아키텍처 규칙 (A1xx/A2xx, A102 포함: `django.db.connections`는 `_django/connections.py`의 `swapped` 교체로만 쓰고 `compile/scope.py`만 import하고, 같은 모듈의 읽기 전용 `configured_databases()`는 `connection/lifecycle.py`만 import; A103: 동적 import와 introspection 속성; A101: `MySQLdb`를 import하는 Django 모듈(`mysql.base`, `mysql.introspection`)과 `django.utils.module_loading`은 `import M`, `from M import n`, `from P import M` 형태 모두 금지; A104: psycopg와 Django 모듈이 재노출하는 psycopg 이름은 `drivers/`에서만 (표: `tools/guards/reexports.py`; 어댑터 헬퍼는 `_django/postgresql_adapters.py` 경유이며 이 모듈은 `drivers/postgresql`만 import; Django 모듈의 `mogrify`는 어디서도 금지); A203: 비공개 Django import는 `asyncopation._django`에서만; ledger의 `[[dependency]]` 행(예: asgiref)은 guard가 형식만 검사하고 A202가 아니라 drift 테스트가 고정) | `tools/guards` | 편집 훅, `make guard`, pre-commit, CI |
| 테스트 규칙: GWT, 제어문 0개 (T1xx-T3xx) | `tools/guards` | 위와 동일 |
| 언어: 영어 + 번역본 (L1xx/L2xx) | `tools/guards` | 위와 동일 |
| 에이전트 설정 불일치 | `tools/agentsync.py --check` | `make agents-check`, pre-commit, CI |
| 정합성 | 3개 백엔드에서 pytest 차등 테스트, `compose_sql`은 순수 Python psycopg로도, drift 스위트(`tests/unit/django_drift`)와 MySQL wrapper 단위 테스트(`tests/unit/compile`, "`MySQLdb`가 로드되지 않음"을 확인하는 subprocess 검사 포함)는 mysqlclient가 설치된 integration job에서도 실행하며 `ASYNCOPATION_REQUIRE_MYSQLDB=1`이면 건너뛰던 MySQL wrapper drift 테스트가 실패로 바뀐다 | `make test-int`, `make test-compose-python`, CI 매트릭스 |
| 업스트림 drift | `tests/unit/django_drift` (ledger, asgiref 핀) | `make test-unit`, CI; `django-main` canary job이 `django_pin` 테스트를 제외하고 이를 `tests/unit/compile`(mysqlclient 설치, `ASYNCOPATION_REQUIRE_MYSQLDB=1`; `ci.yml`의 step `django-drift` / `integration-drift`는 `test_pin_marker.py`가 id로 찾아 `name`을 제외하고 통째로 비교)과 함께 실행하고, `tests/integration`도 Django main에서 실행 |
| E2E | uvicorn + httpx | `make test-e2e`, CI |
| 성능 | micro + k6 하네스 | `make bench-micro` / `make bench`, CI (수동 / 야간) |
| 브랜치 흐름 | GitHub ruleset + `branch-policy.yml` + `CODEOWNERS` | `develop`, `main`으로의 모든 푸시 / PR |

가드에는 인라인 예외 처리(suppression)가 없다. 가드가 아니라 코드를 고친다. 가드를 바꾸려면 사람이
결정해야 한다.

## 5. 테스트 규칙 요약

모든 테스트는 `# given` / `# when` / `# then` 마커를 갖고, `for`, `while`, `if`, `try`, `match`,
컴프리헨션, 조건 표현식을 포함하지 않는다. 변형은 `pytest.mark.parametrize`나 Hypothesis로 만들고,
헬퍼 로직은 `tests/support/`에 둔다. 읽기 경로는 sync ORM과의 차등 테스트로 검증하며, async
커넥션은 pytest-django의 테스트별 트랜잭션을 볼 수 없으므로 커밋된 시드 데이터
(`readonly_db` / `writable_db`)를 사용한다.

## 6. 언어

코드, 주석, 문서, 에이전트 파일의 원본 언어는 영어다. `docs/i18n.toml`에 등록된 사람용 문서는
`<stem>-<LANG>.md` 형식의 번역본(예: `README-KO.md`)을 함께 둔다. 번역본은 자신이 번역된 영어
리비전을 고정하므로, 영어 원문이 바뀌면 가드가 번역본을 오래된(stale) 것으로 표시한다.

## 7. 자주 쓰는 명령

```bash
make db-up && make check          # 세션 시작
make test-int ASYNCOPATION_DB=mysql
make agents-sync                  # .agents/ 수정 후
uv run python -m tools.guards --changed
make bench-micro                  # 핫 패스 변경 시
```
