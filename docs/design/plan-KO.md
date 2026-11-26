<!-- i18n: source=docs/design/plan.md source-sha256=02836662b44d -->
# Asyncopation — 진짜 비동기 Django ORM 설계 및 로드맵

> 상태: 구상 / 설계 초안
> 작성일: 2026-11-24
> 형태: Django 코어 기여가 아닌 **서드파티 패키지**
>
> 원문(영어): [plan.md](plan.md)

| 항목 | 값 |
|---|---|
| 프로젝트 | **Asyncopation** |
| PyPI | `django-asyncopation` |
| import | `asyncopation` |
| 이름 유래 | syncopation(당김음, 엇박) + async. 정박(sync)에서 벗어난 박자라는 뜻이고, Django의 이름이 온 재즈 기타리스트 장고 라인하르트와도 이어진다 |

---

## 1. 배경: 왜 Django의 async ORM은 "진짜" 비동기가 아닌가

### 1.1 구현이 `sync_to_async` 래퍼

`QuerySet`의 async 메서드는 대부분 동기 메서드를 감싼 형태다.

```python
# django/db/models/query.py
async def aget(self, *args, **kwargs):
    return await sync_to_async(self.get)(*args, **kwargs)


async def acreate(self, **kwargs):
    return await sync_to_async(self.create)(**kwargs)
```

- 동기 `get()`을 asgiref의 `sync_to_async`로 스레드에 위임하고, 이벤트 루프는 결과만 await한다.
- 이벤트 루프가 소켓 I/O를 직접 다루는 구조가 아니다. "블로킹 작업을 스레드로 보내 루프를 막지 않는" 수준이다.
- `aiterator()`도 청크를 읽을 때마다 `sync_to_async`로 감싼 메서드를 호출한다.

### 1.2 `thread_sensitive=True` → 사실상 직렬 실행

- `sync_to_async`의 기본값은 `thread_sensitive=True`다.
- Django `ASGIHandler`는 요청마다 `ThreadSensitiveContext`를 씌운다. 그래서 **한 요청 안의 sync 호출은 전부 같은 스레드 하나에서 순서대로** 실행된다.

```python
# 기대: 쿼리 3개가 동시에 나감
# 실제: 같은 스레드에서 하나씩 순서대로 실행됨
a, b, c = await asyncio.gather(
    User.objects.aget(pk=1),
    Order.objects.acount(),
    Item.objects.afirst(),
)
```

### 1.3 근본 원인: DB 레이어 전체가 동기 전제

| 영역 | 동기 전제인 이유 |
|---|---|
| 커넥션 | `django.db.connections`가 스레드(컨텍스트) 로컬이다. 생성, 재사용, `CONN_MAX_AGE`, 트랜잭션 상태가 모두 스레드 기준으로 관리된다 |
| 안전 가드 | 백엔드 메서드에 `@async_unsafe`가 붙어 있어서, 이벤트 루프 스레드에서 직접 호출하면 `SynchronousOnlyOperation`이 발생한다 |
| 트랜잭션 | `transaction.atomic`은 sync 컨텍스트 매니저이고 async 버전이 없다 |
| 실행 경로 | SQLCompiler → backend → cursor, 시그널, lazy 로딩이 전부 sync 체인이다 |
| 드라이버 | psycopg3를 지원하지만 sync `Connection`만 쓴다. `AsyncConnection`은 통합되어 있지 않다 |

### 1.4 실무 영향

- 장점: 이벤트 루프가 막히지 않는다.
- 단점: 쿼리마다 스레드 홉 비용이 들고, 요청 안에서 쿼리를 병렬로 실행할 수 없다. 스레드 수만큼 커넥션이 생긴다.
- 결과적으로 DB 위주 엔드포인트에서는 ASGI + async ORM이 WSGI + sync보다 느린 경우가 흔하다.

### 1.5 코어 진행 상황

- **#35629 Implement async cursors**: 1단계로 저수준 API(`new_connection()` + `acursor()`)를 여는 방향이다. ORM 전체가 async로 바뀌는 건 그 이후다.
- **Django 6.1 (2026-08) fetch modes**: `FETCH_ONE` / `FETCH_PEERS` / `FETCH_RAISE` 모드가 도입됐다.
- **#37218**: lazy relation 접근이 sync 전용이라는 점을 문서화한 티켓이다. async에서 `FETCH_PEERS`를 쓰면 `SynchronousOnlyOperation`이 발생한다. 논의 중에 `AFETCH_PEERS` / `afetch_{one,many}()`가 필요하다는 의견이 나왔다. → **Asyncopation이 채울 수 있는 빈자리다.**

---

## 2. 기존 서드파티 현황

### 2.1 진짜 async I/O를 하는 쪽

| 프로젝트 | 내용 | 한계 |
|---|---|---|
| **django-async-backend** (Arfey) | psycopg3 async 커서, `async_atomic`(중첩 savepoint, async `on_commit`), a-메서드 대부분, `async for` | PostgreSQL 전용, ASGI 전용. `prefetch_related`, `aiterator`, `arefresh_from_db`, related manager 미지원 |
| **django-turbo-orm** | django-async-backend 위에서 Django `Query`/SQLCompiler로 SQL을 만들고, `AsyncConnectionPool`에서 직접 실행 | Alpha(0.2.0, 2026-01). PostgreSQL 전용. 성능 주장(2~4배)의 측정 방법이 공개되지 않음 |

### 2.2 이름만 async인 래퍼 계열 (코어와 같은 한계)

- **django-async-orm** (rednaks): 모델에 `async_*` 메서드를 패치하는 방식이고 내부는 스레드 위임이다. 2023년 이후 사실상 중단됐다.
- **asgimod**: 작성자 스스로 sync ORM 래퍼라고 명시했다.

### 2.3 Django ORM 밖의 대안

- **Tortoise ORM**: Django 스타일 API를 가진 네이티브 async ORM이다. Django 모델, admin, 마이그레이션과는 연동되지 않는다.

### 2.4 Asyncopation의 차별점

1. **MySQL 지원** (asyncmy). 기존 진짜 async 구현은 전부 PostgreSQL 전용이다.
2. **async fetch modes**: Django 6.1 fetch modes의 async 버전 (lazy + 자동 배치 로딩)
3. **async `prefetch_related`**: 기존 프로젝트들이 공통으로 비워둔 영역이다.

---

## 3. 확정된 방향

| # | 결정 사항 |
|---|---|
| 1 | 드라이버: **psycopg3 (PostgreSQL) + asyncmy (MySQL)** |
| 2 | API: 기존 `objects`는 그대로 두고 **`aobjects`** 접근자(`Manager`가 아닌 디스크립터 - ADR-0006)를 추가한다 |
| 3 | 목표 범위: Django ORM의 핵심 특징인 **lazy loading + eager loading**까지 지원한다 |
| 4 | 최종 목표: **SQL 생성 경로까지 비동기 친화적으로** 만든다 (정의는 §4.4 참고) |

전제 조건: **Django 6.1+**, **Python 3.12+**, **ASGI 전용**

---

## 4. 핵심 설계

### 4.1 기본 원칙: SQL 생성은 Django에, 실행은 async로

```python
compiler = qs.query.get_compiler(using=qs.db)
sql, params = compiler.as_sql()  # 순수 SQL 생성, DB I/O 없음
cur = await aconn.execute(sql, params)  # 비동기 드라이버로 실행
rows = await cur.fetchall()
# 이후 ModelIterable.__iter__ 로직(converters 적용 → Model.from_db)을 옮겨온다
```

- QuerySet 빌드(`filter`, `order_by`, `select_related` …)는 lazy하고 I/O가 없으므로 그대로 재사용한다.
- async 레이어는 **평가 시점**에만 개입한다.

### 4.2 커넥션 소유 모델 (프로젝트의 핵심 설계)

- thread-local 대신 **`contextvars`**로 요청/task 단위 커넥션을 관리한다.
- 풀은 `psycopg_pool.AsyncConnectionPool`과 asyncmy pool을 쓴다.
- **`asyncio.gather` 문제**: 자식 task는 contextvar를 복사해 가므로 부모 커넥션을 공유하게 된다. psycopg 커넥션 하나로는 쿼리를 동시에 실행할 수 없다.

| 전략 | 설명 |
|---|---|
| (a) lock | 커넥션 하나를 lock으로 직렬화한다. 안전하지만 병렬성이 없다 |
| (b) 쿼리 단위 대여 | 트랜잭션 밖에서는 쿼리마다 풀에서 빌리고 반납한다 |
| (c) 트랜잭션 고정 | 트랜잭션 안에서만 커넥션을 고정한다 |

→ **(b) + (c) 조합**을 기본으로 한다. 실제 병렬성을 얻을 수 있는 방식이다. ADR-0001로 채택됐다. 트랜잭션 안에서 다른 task가 고정된 커넥션을 쓰면 직렬화하지 않고 `ConcurrentQueryError`를 발생시킨다.

### 4.3 드라이버 추상화

인터페이스는 처음부터 두 구현체를 전제로 잡고, 구현은 PostgreSQL을 먼저 한다.

```
AsyncDriver (protocol)                       AsyncDbConnection (protocol)
├── capabilities: Capabilities (frozen)      ├── fetch_all / execute / execute_many
├── open / close                             ├── stream(...) -> async context manager
└── connection() -> borrow                   └── begin / commit / rollback / savepoints
```

드라이버는 Django의 sync 백엔드 커넥션과 똑같이 동작하고(값, rowcount 의미, 세션 설정) 예외를
`django.db` 예외로 변환한다. SQL 모양은 Django의 `features`가 결정한다(ADR-0003).

**드라이버별 함정**

| 항목 | PostgreSQL (psycopg3) | MySQL (asyncmy) |
|---|---|---|
| 바인딩 | `AsyncConnection` 기본 커서는 서버 사이드 바인딩이다. Django 호환성을 위해 **`AsyncClientCursor`**를 사용한다(기본값. `OPTIONS["server_side_binding"] = True`이면 Django sync 백엔드처럼 `AsyncCursor`) | `%s` 플레이스홀더라 호환된다 |
| `RETURNING` | 지원 | MySQL은 미지원이라 `lastrowid`로 처리한다. MariaDB는 지원하므로 분기가 필요하다. `bulk_create`의 pk 반환도 분기한다 |
| 서버 정보 | `pg_version`이 커넥션을 요구한다 | `mysql_server_data` 등이 실제 쿼리를 날린다 |
| 타입 변환 | Django 기준과 거의 같다 | Django는 mysqlclient 기준이다. asyncmy(PyMySQL 계열)와 datetime/Decimal/JSON 반환 타입이 다를 수 있다 → 차등 테스트로 검증한다 |

### 4.4 "SQL 생성 비동기화"의 재정의

**`as_sql()`을 `async def`로 바꾸지 않는다.**

- SQL 생성은 CPU 작업이라 await할 I/O가 없다. async로 바꾸면 코루틴 오버헤드만 생긴다.
- Expression/Lookup/Func 트리가 재귀적으로 `as_sql`을 호출한다. 서드파티 패키지와 사용자 코드의 커스텀 lookup이 전부 sync `as_sql`이므로 생태계 호환성이 깨진다.

**대신 아래 두 가지를 목표로 한다.**

1. **SQL 생성 경로에서 sync I/O 0개 보장**
   - 절대 연결하지 않는 **컴파일 전용 `DatabaseWrapper`**를 만든다.
   - 앱 시작 시 서버 버전/정보를 async로 조회해 features/ops 캐시에 미리 주입한다.
   - 결과적으로 SQL 생성이 sync 커넥션 객체에서 완전히 분리된다.
   - 일부 Django 컴파일러(aggregate, update)는 `connections[using]`을 다시 조회한다. 이들이 컴파일 전용
     wrapper에 닿는 방법은 ADR-0008에서 정한다(Accepted: compile 호출마다 동기적이고 범위가 한정된 `connections[alias]` 교체).
2. **I/O와 로직이 섞인 오케스트레이션을 async로 재작성**
   - `Collector.collect` (cascade 삭제 대상 수집 중 쿼리 실행)
   - `get_or_create`, `update_or_create`
   - `prefetch_related_objects`
   - `async def` 복사본이 아니라 하나의 async 실행기가 구동하는 sans-I/O 제너레이터 코어로 작성한다(ADR-0007).
   - 두 번째 코어가 `compile_scope` 밖에서 변환을 필요로 하면, `engine/runner.py`가 더 이상 `_django.aggregation`을 import하지 않도록 `Convert`가 자체 callable을 담도록 일반화한다(ADR-0007, Decision 1).

### 4.5 Lazy loading

파이썬 속성 접근은 await할 수 없으므로 **명시적 async 접근자 + 기본 `FETCH_RAISE`** 방식을 쓴다.

```python
books = await Book.aobjects.filter(...).alist()  # aobjects는 기본 FETCH_RAISE
books[0].author  # 로드 안 됐으면 FieldFetchBlocked (빠르게 실패)
await books[0].afetch("author")  # async FETCH_PEERS: peers 전체를 쿼리 1번으로 채움
books[1].author  # 이미 채워져 있으니 sync 접근 OK
```

- `await book.author`처럼 디스크립터가 awaitable을 반환하는 방식은 **채택하지 않는다**.
  - 로드 여부에 따라 반환 타입이 달라진다.
  - `select_related`로 이미 로드된 경우에도 `book.author.name`이 깨진다.
  - 참고: SQLAlchemy도 같은 이유로 `awaitable_attrs`와 `lazy="raise"`를 분리했다.
- `afetch`는 Django 6.1 fetch modes의 의미를 따른다 (`AFETCH_ONE` / `AFETCH_PEERS`).
- Django 6.1의 peers 추적(weakref 리스트) 같은 내부 API를 재사용할 경우, **지원 Django 버전을 고정**하고 버전별 테스트 매트릭스를 운영한다.

### 4.6 Eager loading

| 기능 | 난이도 | 방식 |
|---|---|---|
| `select_related` | 쉬움 | 컴파일러가 JOIN을 생성하고 `RelatedPopulator`(순수 파이썬)를 재사용한다. 실행 부분만 교체한다 |
| `prefetch_related` | 어려움 | `prefetch_one_level`을 async로 포팅한다. 디스크립터의 `get_prefetch_querysets`(숨은 I/O 점검 필요: GenericForeignKey 쪽은 캐시 미스 시 ContentType을 조회한다)로 쿼리셋을 만들고 → async로 평가하고 → 할당 로직을 재사용한다. `Prefetch` 객체, `to_attr`, 중첩 lookup, GenericForeignKey를 지원해야 한다 |
| related manager | 보통 | `author.book_set.filter(...)`는 lazy QuerySet이므로 **`aq()` 어댑터**로 평가 시점에만 개입한다 |

```python
from asyncopation import aq

books = await aq(author.book_set.filter(published=True)).alist()
```

---

## 5. 테스트 & 벤치마크 전략

### 5.1 차등 테스트 (정합성 검증의 핵심)

- 같은 QuerySet을 **sync ORM과 async ORM으로 각각 실행**하고 결과가 같은지 비교한다.
- Hypothesis로 쿼리셋 조합(filter/annotate/order_by/select_related/slicing …)을 생성한다.
- PostgreSQL과 MySQL 양쪽에서 실행한다. 드라이버 타입 변환 차이를 잡는 용도이기도 하다.

### 5.2 벤치마크

같은 엔드포인트를 세 가지 구성으로 비교한다.

1. WSGI + sync ORM
2. ASGI + Django 코어 async ORM (`sync_to_async`)
3. ASGI + Asyncopation

- 도구: k6 또는 locust
- 지표: p50/p95/p99 레이턴시, 처리량, **DB 커넥션 수**, 메모리
- 시나리오: 단건 조회, `gather` 병렬 조회, N+1 (`afetch` peers vs `prefetch_related`), 쓰기 + 트랜잭션

---

## 6. 로드맵

| Phase | 내용 | 난이도 | 산출물 |
|---|---|---|---|
| 0 | **테스트 하네스**: 차등 테스트 러너, 쿼리셋 생성 전략, async 쿼리 캡처, CI 매트릭스 | 중 | 하네스, CI |
| 1 | 드라이버 추상화 + 컴파일 전용 커넥션 + `aobjects` 읽기 API (`alist`/`aget`/`afirst`/`acount`/`aexists`/`async for`) — PG → MySQL. 하네스를 실제 구현에 붙여서 다듬는 단계 | 중 | 첫 동작 버전 |
| 2 | `select_related`, `values` / `values_list` | 중하 | |
| 3 | **`afetch` + 기본 `FETCH_RAISE` + async peers 로딩** (대표 기능) | 중상 | async fetch modes |
| 4 | `prefetch_related` async 포팅, `aq()` 어댑터 | 상 | |
| 5 | 쓰기 연산 (`acreate`/`asave`/`aupdate`/`adelete`), async `Collector`, `async_atomic`, `Signal.asend` 연동 | 상 | |
| 6 | 벤치마크, 문서, 첫 공개 릴리스 | 중 | README 비교표 |

진행 관리: 각 Phase는 GitHub 마일스톤, 각 로드맵 항목은 이슈로 관리한다 -
[마일스톤](https://github.com/devgyurak/asyncopation/milestones).

> Phase 0~1은 사실상 **테스트 하네스를 잡는 데 대부분의 시간**을 쓴다. 이후 Phase 2~5는 모두 "하네스에 케이스를 추가하고 통과시키는" 반복이 되므로, 여기에 들인 시간이 이후 속도를 결정한다.

### Phase 0 체크리스트 (테스트 하네스)

- [x] 테스트용 모델 세트 (FK, O2O, M2M, reverse FK, GenericForeignKey, composite pk, 다양한 필드 타입) - `harness/catalog`
- [x] 차등 테스트 러너: 같은 QuerySet을 sync / async로 실행하고 결과(인스턴스 필드, 순서, 타입)를 비교 - `tests/support/differential.py`
- [x] Hypothesis 쿼리셋 생성 전략 (filter / exclude / order_by / select_related / slicing / annotate / 역방향 FK editions / only()와 defer() 조합) - `tests/support/strategies.py`
- [ ] async 쪽 쿼리 캡처 유틸 (`assertNumQueries`의 async 버전, 실행 SQL 기록) - Phase 1의 드라이버 계측 훅이 필요 (#9, #4에 막혀 있음)
- [x] CI 매트릭스: PostgreSQL / MySQL / MariaDB × 지원 Django 버전 (+ Django main 카나리아)
- [x] E2E 하네스(uvicorn + 하네스 사이트)와 벤치마크 하네스(micro + k6)

### Phase 1 체크리스트 (첫 스파이크)

- [x] `as_sql()` + async 드라이버로 `alist` 동작 확인: PostgreSQL의 `aq(qs).alist()` (#6, PR #65; MySQL/MariaDB 차등 테스트는 MySQL wrapper와 함께, #6, PR #68 / #7)
- [x] `ModelIterable` 로직 이식 (converters → `Model.from_db`)과 values / values_list / flat / named 이터러블 (`engine.materialize`, #6, PR #65)
- [x] `as_sql()` 도중 sync 커넥션이 열리는 경로 탐지 (`SynchronousOnlyOperation` 발생 지점 기록) - 스파이크 결과는 ADR-0008에 있음 (#29, ADR-0008 Accepted)
- [x] 컴파일 전용 `DatabaseWrapper`(PostgreSQL) + 동기 `compile_scope` 교체 (#3, ADR-0008); MySQL/MariaDB 합성 wrapper(`compile.mysql`, #6, PR #68; asyncmy 드라이버·프로브·엔진 등록 → #7)
- [x] 서버 정보 async 프리로드 → 컴파일 전용 커넥션에 주입 (PostgreSQL; #5, ADR-0004; MySQL/MariaDB 프로브 → #7)
- [x] 실행기와 연결 소유권: `BorrowedExecutor` / `PinnedExecutor`, `ConcurrentQueryError`, 깨진 트랜잭션 상태, 열린 pin과 스트림을 기다리는 `close()` (#5, ADR-0001; `async_atomic` → #19, 풀 크기 → #8, 훅 → #4)
- [x] `django.contrib.postgres`가 설치된 경우 PostgreSQL 드라이버의 hstore/citext 타입 등록 (#43, ADR-0003 Decision 2); `harness.pgcatalog`
- [x] drift 스위트에서 asgiref 버전과 `compile_scope`가 의존하는 `Local(thread_critical=True)` 동작을 고정 (ledger `[[dependency]]` 행, `asgiref>=3.13`; #49, ADR-0008)
- [x] 첫 차등 테스트 1개 통과 (`tests/integration/test_harness_selfcheck.py::test_asyncopation_list_matches_sync`): PostgreSQL. `aget` / `afirst` / `acount` / `aexists` / `async for`, `aobjects` 디스크립터, `get_aggregation` / `has_results` 코어가 있는 sans-I/O 러너 포함 (#6, PR #67; MySQL/MariaDB는 MySQL wrapper와 함께, #6, PR #68 / #7)
- [ ] asyncmy로 같은 흐름 재현; 인수 조건(#7): 서버 정보 프로브는 열 순서가 Django의 `row[n]` 계약과 일치하는 정확한 `SELECT`에서 `MySQLServerInfo(*row)`를 만든다 (ADR-0008 결정 5; 소스 drift 테스트가 필드 순서를 고정한다)

---

## 7. 먼저 읽을 코드

| 파일 | 볼 부분 | 이유 |
|---|---|---|
| `django/db/models/sql/compiler.py` | `as_sql`, `execute_sql`, `results_iter`, `get_converters`, Insert/Update/Delete 컴파일러 | SQL 생성과 실행이 갈리는 지점 |
| `django/db/models/query.py` | `ModelIterable.__iter__`, `_fetch_all`, `RelatedPopulator`, `prefetch_related_objects`, `prefetch_one_level` | 인스턴스 변환, eager loading |
| `django/db/models/fetch_modes.py` | 전체 | Django 6.1 fetch modes 구조 |
| `django/db/models/fields/related_descriptors.py` | `fetch_one`, `fetch_many`, `get_prefetch_querysets` | lazy/eager 로딩 진입점 |
| `django/db/backends/base/base.py`, `postgresql/base.py`, `mysql/base.py` | `DatabaseWrapper`, `async_unsafe`, 서버 정보 조회 | 커넥션 생명주기, 숨은 I/O |
| `django/db/transaction.py` | `Atomic` | savepoint, `on_commit` |
| `django/db/models/deletion.py` | `Collector` | cascade 삭제 오케스트레이션 |

같이 참고할 것: django-async-backend 소스와 `UPSTREAMING.md`, 코어 티켓 #35629 패치

---

## 8. 참고 자료

- Django 티켓 #35629 — Implement async cursors: https://code.djangoproject.com/ticket/35629
- Django 티켓 #37218 — Document that lazy relation access is synchronous only: https://code.djangoproject.com/ticket/37218
- Django 6.1 fetch modes 문서: https://docs.djangoproject.com/en/6.1/topics/db/fetch-modes/
- Django 6.1 릴리스 노트: https://docs.djangoproject.com/en/dev/releases/6.1/
- django-async-backend: https://github.com/Arfey/django-async-backend
- django-turbo-orm: https://pypi.org/project/django-turbo-orm/
- django-async-orm (래퍼 계열): https://github.com/rednaks/django-async-orm
- Django 포럼 — Asynchronous ORM 논의: https://forum.djangoproject.com/t/asynchronous-orm/5925
