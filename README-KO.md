<!-- i18n: source=README.md source-sha256=887f2762505d -->
# Asyncopation

> **상태: pre-alpha.** PostgreSQL 라이프사이클(`INSTALLED_APPS`, `setup()` / `close()`, `LifespanApp`, 드라이버 레지스트리)과 `asyncopation.testing`을 사용할 수 있다. 아래 쿼리 API는 설계 목표이며 아직 구현되지 않았다. [계획](docs/design/plan-KO.md) 참고.

Django ORM을 위한 진짜 비동기 실행 계층. SQL은 여전히 Django가 만들고, Asyncopation은
`sync_to_async`로 스레드를 오가는 대신 네이티브 async 드라이버(PostgreSQL은 psycopg3,
MySQL/MariaDB는 asyncmy)로 실행한다.

| | |
|---|---|
| PyPI | `django-asyncopation` |
| import | `asyncopation` |
| 요구 사항 | Python 3.12+, Django 6.1, ASGI |
| 이름 | *syncopation*(당김음, 엇박) + *async*. 장고 라인하르트에 대한 오마주 |

```python
books = await Book.aobjects.filter(published=True).alist()  # native async I/O
await books[0].afetch("author")  # loads authors for all peers in one query
```

원문(영어): [README.md](README.md)

## 설정 (PostgreSQL)

앱을 등록하면 `AppConfig.ready()`가 `DATABASES`로부터 async 드라이버를 구성한다. 그리고 ASGI 애플리케이션을 감싸서 시작할 때 풀을 열고 종료할 때 닫는다.

```python
# settings.py
INSTALLED_APPS = ["asyncopation", ...]

# asgi.py
from asyncopation.connection.asgi import LifespanApp
from django.core.asgi import get_asgi_application

application = LifespanApp(get_asgi_application())
```

스크립트와 테스트는 `await asyncopation.setup()`과 `await asyncopation.close(timeout=10)`을 직접 호출한다. `setup()` 이전에 쿼리를 실행해도 동작한다(풀이 지연 방식으로 한 번 열리며, lifespan 래퍼 사용을 권장하는 경고가 로그에 남는다). `setup()`은 엔진이 지원되는 모든 데이터베이스를 열고 나머지는 건너뛴다. 대상을 직접 고르려면 `LifespanApp(app, aliases=[...])` 또는 `setup(aliases=[...])`를 전달한다. MySQL/MariaDB는 아직 지원하지 않는다.

**테스트에서는** 테스트 러너가 `ready()` 이후에 테스트 데이터베이스를 만들고 `NAME`을 바꾼다. 테스트 데이터베이스가 만들어진 뒤(예를 들어 세션 픽스처에서 `setup_databases` 직후) `asyncopation.configure()`를 한 번 호출한다. 그 전에 드라이버를 열면 원래 데이터베이스에 연결하는 대신 `ImproperlyConfigured`로 실패한다.

## 데이터베이스 없이 상위 레이어 테스트하기

`asyncopation.testing.RecordingDriver`는 `AsyncDriver` 프로토콜을 메모리에서 구현한다. 테스트에
필요한 결과를 큐에 넣고, 테스트 대상 코드를 실행한 뒤, 실행된 구문 로그를 확인하면 된다.
`Capabilities`와 `ExecResult`도 `asyncopation.testing`에서 다시 내보낸다.

```python
import asyncio

from asyncopation.testing import ExecResult, RecordingDriver


async def main() -> None:
    driver = RecordingDriver()
    await driver.open()
    driver.queue([(1, "a"), (2, "b")], ExecResult(rowcount=1))  # FIFO, one per statement
    async with driver.connection() as conn:
        rows = await conn.fetch_all("SELECT id, name FROM t WHERE id > %s", [0])
        await conn.execute("DELETE FROM t WHERE id = %s", [1])
    assert rows == [(1, "a"), (2, "b")]
    assert [s.kind for s in driver.statements] == ["fetch_all", "execute"]
    await driver.close()


asyncio.run(main())
```

## 개발

필요 도구: [uv](https://docs.astral.sh/uv/), Docker. MySQL 차등 테스트에는 `mysqlclient` 빌드
의존성(`brew install mysql-client pkg-config` 또는 `apt install libmysqlclient-dev`)과
`uv sync --group mysql-sync`도 필요하다.

```bash
uv sync                     # dev + e2e 의존성 그룹 설치
make db-up                  # docker compose로 PostgreSQL, MySQL, MariaDB 실행 (tmpfs)
make check                  # ruff + ty + import-linter + guards + 에이전트 설정 동기화 + 단위 테스트
make test-int               # 통합 테스트 (ASYNCOPATION_DB=postgresql|mysql|mariadb)
make test-e2e               # 하네스 사이트를 uvicorn으로 띄워 테스트
make bench-micro            # 프로세스 내 마이크로 벤치마크
make bench                  # k6 부하 테스트: WSGI vs ASGI 코어 vs Asyncopation
make help                   # 나머지 전부
```

도구: uv, pytest (+ pytest-asyncio, pytest-django, Hypothesis), ruff (포맷 + 린트),
ty (타입), import-linter (레이어), `tools/guards`의 프로젝트 가드, k6 (부하).

## 코딩 에이전트와 함께 작업하기

이 저장소는 Claude Code, Codex, Pi를 1급으로 지원하도록 구성되어 있다.
[docs/workflow-KO.md](docs/workflow-KO.md)부터 읽자. 에이전트는 [AGENTS.md](AGENTS.md)를 읽는다
(Claude Code는 [CLAUDE.md](CLAUDE.md) 경유).

## 문서

- [docs/design/plan-KO.md](docs/design/plan-KO.md) - 배경, 설계, 로드맵
- [docs/architecture.md](docs/architecture.md) - 레이어와 불변 조건
- [docs/adr/](docs/adr/) - 아키텍처 결정 기록
- [docs/workflow-KO.md](docs/workflow-KO.md) - 에이전트 개발 워크플로
- 원본 언어는 영어이고, 번역 문서는 `-<LANG>` 접미사를 쓴다(`README-KO.md`).
  [docs/i18n.toml](docs/i18n.toml) 참고.
