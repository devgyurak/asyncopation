<!-- i18n: source=README.md source-sha256=19c00c2c5da8 -->
# Asyncopation

> **상태: pre-alpha.** PostgreSQL 라이프사이클(`INSTALLED_APPS`, `setup()` / `close()`, `LifespanApp`, 드라이버 레지스트리)과 `asyncopation.testing`을 사용할 수 있고, PostgreSQL에서는 `aq(qs)`와 `aobjects`로 데이터를 읽을 수 있다(`alist` / `aget` / `afirst` / `acount` / `aexists` / `async for`). 나머지 쿼리 API는 설계 목표이며 아직 구현되지 않았다. [계획](docs/design/plan-KO.md) 참고.

Django ORM을 위한 진짜 비동기 실행 계층. SQL은 여전히 Django가 만들고, Asyncopation은
`sync_to_async`로 스레드를 오가는 대신 네이티브 async 드라이버(PostgreSQL은 psycopg3,
MySQL/MariaDB는 asyncmy)로 실행한다.

| | |
|---|---|
| PyPI | `django-asyncopation` |
| import | `asyncopation` |
| 요구 사항 | Python 3.12+, Django 6.1, asgiref 3.13+, ASGI |
| 이름 | *syncopation*(당김음, 엇박) + *async*. 장고 라인하르트에 대한 오마주 |

```python
books = await Book.aobjects.filter(published=True).alist()  # native async I/O
await books[0].afetch("author")  # loads authors for all peers in one query
```

원문(영어): [README.md](README.md)

## 설정 (PostgreSQL)

앱을 등록하면 `AppConfig.ready()`가 `DATABASES`로부터 async 드라이버를 구성한다. 그리고 ASGI 애플리케이션을 감싸서 시작할 때 풀을 열고 종료할 때 닫는다
(`LifespanApp`은 `lifespan` 스코프를 직접 처리하며 안쪽 앱으로 전달하지 않는다. Django의 ASGI 핸들러는 이를 처리할 수 없다).

```python
# settings.py
INSTALLED_APPS = ["asyncopation", ...]

# asgi.py
from asyncopation.connection.asgi import LifespanApp
from django.core.asgi import get_asgi_application

application = LifespanApp(get_asgi_application())
```

스크립트와 테스트는 `await asyncopation.setup()`과 `await asyncopation.close(timeout=10)`을 직접 호출한다. `setup()` 이전에 데이터베이스를 사용하면(쿼리 경로, 예: `alist()`) 풀이 지연 방식으로 한 번 열리며, lifespan 래퍼 사용을 권장하는 경고가 로그에 남는다. `setup()`은 엔진이 지원되는 모든 데이터베이스를 열고 나머지는 건너뛴다. MySQL/MariaDB는 아직 지원하지 않는다.

풀을 다른 시작/종료 작업과 함께 사용하려면 앱을 감싸는 대신 직접 작성한 lifespan 핸들러에서 `setup()` / `close()`를 호출한다.

```python
from contextlib import asynccontextmanager

import asyncopation


@asynccontextmanager
async def lifespan(app):  # e.g. Starlette(lifespan=lifespan)
    await asyncopation.setup()
    try:
        yield
    finally:
        await asyncopation.close()
```

**테스트에서는** 테스트 러너가 `ready()` 이후에 테스트 데이터베이스를 만들고 `NAME`을 바꾼다. 테스트 데이터베이스가 만들어진 뒤(예를 들어 세션 픽스처에서 `setup_databases` 직후) `asyncopation.configure()`를 한 번 호출한다. 그 전에 드라이버를 열면 원래 데이터베이스에 연결하는 대신 `ImproperlyConfigured`로 실패한다.

## 데이터 읽기 (PostgreSQL)

`aq()`는 모든 QuerySet 또는 Manager를 감싼다. 평소처럼 체이닝한 뒤 평가를 `await`한다. 평가는
처음부터 끝까지 비동기다. SQL은 Django가 만들고 네이티브 async 드라이버가 실행한다.

```python
from asyncopation import aq

books = await aq(Book.objects.filter(published=True).order_by("title")).alist()
book = await Book.aobjects.aget(pk=1)  # 모델에 `aobjects = AsyncObjects()`를 선언한 경우
how_many = await Book.aobjects.filter(published=True).acount()
titles = await aq(Book.objects.values_list("title", flat=True)).alist()
```

`aq()`는 넘겨받은 QuerySet의 복제본을 감싼다(예외는 결과 캐시가 이미 채워진 QuerySet이며, `alist()`는 그 캐시를 읽기만 한다. prefetch된 관계 매니저나 사용자 정의 메서드가 돌려주는 보관된 QuerySet이 여기에 해당한다). 따라서 `alist()`는 래퍼 자신의 캐시를 채울 뿐 사용자의 QuerySet은 건드리지 않는다. 클래스 속성에 보관한 QuerySet이 이후 요청에 오래된 행을 돌려주는 일이 없다. 조합에도 같은 규칙이 적용된다(`aq(Book.objects.none()) | qs`, `.union(qs)`는 Django가 `qs` 자체를 돌려주지만 결과는 복제본이다). 사용자 정의 체이닝 메서드는 새 QuerySet을 돌려줘야 한다. 인자 안에 중첩된 래퍼(`Prefetch(queryset=aq(...))`, 리스트, `Q`)는 풀리지 않으며 지원하지 않는다. 대신 `.queryset`을 넘긴다. `|`, `&`, `^`에서는 래퍼를 왼쪽에 두어야 한다. `QuerySet | aq(...)`는 래퍼에 위임하지 않는 Django 연산자를 그대로 실행하므로 `aq()`가 `FETCH_RAISE`를 적용하지 않은 일반 QuerySet을 돌려준다. 결과 캐시가 이미 채워진 QuerySet(prefetch된 관계 매니저, 동기로 평가된 QuerySet)을 감싸면 그 캐시의 인스턴스는 처음 로드될 때의 fetch 모드를 유지하며, 이는 `FETCH_RAISE`가 아니다. Manager가 막 만든 QuerySet은 그대로 사용한다(`Manager.get_queryset()`은 Django의 매니저와 관계 매니저처럼 새 QuerySet을 돌려준다고 가정한다). `aq()`와 `aobjects`는 `FETCH_RAISE`를 적용한다. 로드되지 않은 관계나 deferred 필드에 접근하면 동기 쿼리를
실행하는 대신 `FieldFetchBlocked`가 발생한다. 동기 평가(`list(aq(qs))`, `aq(qs).get()`,
`len(...)`, `aq(qs).iterator()`, `aq(qs)[::2]` 같은 step 슬라이스)는
`asyncopation.SyncEvaluationError`를 내고, 아직 구현되지 않은 `a*` 메서드(`acreate`, `aiterator` 등)와
`raw()`(래핑되지 않은 `RawQuerySet`을 돌려주게 된다)는 `NotImplementedError`를 낸다. 일치할 수 없는
`QuerySet`(`.none()`, `pk__in=[]`)은 SQL을 실행하지 않지만, 읽기 API는 먼저 해당 alias의 풀을 연다(ADR-0004).
PostgreSQL의 읽기 API는 `alist()`, `aget()`, `afirst()`, `acount()`, `aexists()`, `async for`이다
(`prefetch_related`는 아직 없고 MySQL/MariaDB는 뒤따른다). `aobjects = AsyncObjects()`를 선언한
모델(서드파티 모델은 `AsyncObjects.attach(Model)`)은 `await Book.aobjects.filter(...).alist()`처럼
`aobjects`로 읽는다. `aobjects`는 교체되어 쓰이지 않는 모델을 거부하지 않으므로, 실제로 사용하는 모델에 붙여야 한다.

## 동시성과 트랜잭션

`async_atomic`(#19)은 아직 없다. 아래는 그것이 기반으로 삼을 소유권 모델(ADR-0001)이다.
트랜잭션 밖에서는 구문마다 풀에서 연결을 따로 빌리므로 `asyncio.gather` 아래의 구문이 병렬로
실행된다. `async_atomic` 블록 안에서는 연결 하나가 그 블록을 연 태스크에 고정된다. 다른
태스크(`gather`나 `TaskGroup`의 자식, 블록 안에서 만든 태스크)에서 쓰거나, 구문이나 스트림이 진행
중일 때 다시 쓰거나, 블록이 끝난 뒤에 쓰면 `asyncopation.ConcurrentQueryError`가 발생한다
(`DatabaseError`가 아니라 `RuntimeError`이므로 재시도 루프가 삼키지 않는다). 동시에 실행할 작업은
트랜잭션 밖에서 하라.

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

도구: uv, pytest (+ pytest-asyncio, pytest-django, pytest-timeout, Hypothesis), ruff (포맷 + 린트),
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
