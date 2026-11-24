# ADR-0003: Driver contract - protocol, capabilities, normalization and errors

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-24 (#2, PR #46) - Decision 1 (cursor selection mirrors Django's `server_side_binding`), Decision 2 (hstore/citext registration deferred to #43), Decision 4 (configuration-error exception), the Decision code block (`capabilities` property, `is_disconnect`), Option D (PostgreSQL capabilities come from the driver's `open()` connection); approved by: maintainer (decided to handle all review items in PR #46; to be confirmed in the PR review before merge); architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)

## Context
Plan §4.3 lists the driver operations and the per-driver pitfalls (psycopg needs
`AsyncClientCursor`; MySQL has no `RETURNING`, MariaDB does; asyncmy may return different types
than mysqlclient). It does not say how capabilities are expressed, where differences are
absorbed, or how driver exceptions reach users.

Django's sync backends do more than pick a driver: PostgreSQL `get_connection_params` passes
`context=get_adapters_template(use_tz, timezone)` (non-public `psycopg_any`), `prepare_threshold`,
`client_encoding`, the cursor factory / `server_side_binding`; `_configure_connection` sets
`TimeZone` and `assume_role`. MySQL connects with `client_flag=CLIENT.FOUND_ROWS`, `utf8mb4`,
`init_command`, the isolation level and `SET SQL_AUTO_IS_NULL = 0`. `FOUND_ROWS` decides whether
`rowcount` counts matched or changed rows, which `Model.save()` uses. MySQL's `CursorWrapper`
remaps `OperationalError` codes 1048, 1690, 3819, 4025 to `IntegrityError`. `contrib.postgres`
registers hstore/citext handlers on the `connection_created` signal with a sync `TypeInfo.fetch`.
SQL shape (e.g. whether `RETURNING` is emitted) is decided by the compiler from
`connection.features` (`can_return_columns_from_insert`, `can_return_rows_from_bulk_insert`).

Prior art: SQLAlchemy per-instance dialect flags set in `initialize()` and MRO-based
`DBAPIError.instance`; Prisma ADR 031 (static + probed capabilities, no vendor checks); Tortoise
frozen but static `Capabilities`; ormar `DriverAdapter` + `raise_translated`; SeaORM `MockDatabase`.
encode/databases leaked raw driver exceptions and hard-coded capabilities.

## Options
### A - Normalize in the materializer (upper layers know backend quirks)
+ thin drivers. - backend knowledge leaks into `engine`; Django converters would need patching.

### B - Normalize in the driver (driver behaves like Django's sync driver connection)
+ Django's converters run unchanged; one place per backend. - drivers must replicate Django's
connection setup exactly.

### C - Vendor checks in core (`if vendor == "mariadb"`)
+ quick. - scattered branching, version-dependent behaviour handled inconsistently.

### D - Frozen capabilities refined by a server probe; SQL shape stays with Django features
+ core code is vendor-agnostic; no second source of truth for SQL shape. - needs a probe at
startup (ADR-0004).
*Amended 2026-11-24 (#2, PR #46): for PostgreSQL the capabilities come from the driver's `open()`
connection (startup parameters, no probe query); ADR-0004 Decision 2.*

## Decision
**B + D**, with a typed protocol in `asyncopation.drivers`:

```python
class AsyncDriver(Protocol):
    # Amended 2026-11-24 (#2, PR #46): a property that raises RuntimeError unless the driver is open
    @property
    def capabilities(self) -> Capabilities: ...

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def connection(self) -> AbstractAsyncContextManager[AsyncDbConnection]: ...
    def is_disconnect(self, exc: BaseException) -> bool: ...


class AsyncDbConnection(Protocol):
    async def fetch_all(self, sql: str, params: Params) -> list[tuple]: ...
    async def execute(
        self, sql: str, params: Params
    ) -> ExecResult: ...  # rowcount, lastrowid, rows
    async def execute_many(self, sql: str, batch: Sequence[Params]) -> ExecResult: ...
    def stream(
        self, sql: str, params: Params, chunk: int
    ) -> AbstractAsyncContextManager[AsyncIterator[list[tuple]]]: ...
    # also: commit, rollback, savepoint, release_savepoint, rollback_to_savepoint
    async def begin(self) -> None: ...


@dataclass(frozen=True)
class Capabilities:  # driver/runtime facts only - never SQL shape
    server_version: tuple[int, ...]
    server_side_cursors: bool  # false under poolers / DISABLE_SERVER_SIDE_CURSORS
    atomic_execute_many: bool
```

1. **Normalization contract:** a driver connection returns the same values, the same
   `rowcount`/`lastrowid` semantics and the same session state as Django's sync backend
   connection for that alias - including the connection setup listed in Context. psycopg uses
   Django's adapters template and mirrors Django's cursor selection: by default
   `AsyncClientCursor` for plain statements and Django's server-side cursor mix
   (`ClientCursorMixin` + `AsyncServerCursor`) for streams; with `OPTIONS["server_side_binding"]
   = True` psycopg's `AsyncCursor` / `AsyncServerCursor`. Named cursors are `scrollable=False` and
   `withhold` while in autocommit (outside a transaction), as in Django's `create_cursor`. SQL
   shape differences stay in the wrapper's `features.uses_server_side_binding`; the driver never
   branches SQL on binding or vendor. asyncmy gets a decoder shim matching mysqlclient and
   `CLIENT.FOUND_ROWS`. *Amended 2026-11-24 (#2, PR #46): the cursor selection (default
   `AsyncClientCursor`, `AsyncCursor` with `OPTIONS["server_side_binding"] = True`) mirrors
   Django's sync backend; the former text named `AsyncClientCursor` only.*
2. **contrib.postgres:** the PostgreSQL driver's connection-configure step registers hstore/citext
   handlers asynchronously (**deferred to #43**: the harness has no `contrib.postgres` yet).
   *Amended 2026-11-24 (#2, PR #46): this registration is NOT implemented yet - the sentence above
   is a plan, not current behaviour; tracked in #43.*
   `connection_created` is **not** sent for our connections (documented).
3. **Single source of truth for SQL shape:** the compile-only wrapper's `features`. `Capabilities`
   holds only driver/runtime facts. Core code never reads vendor strings; it reads `Capabilities`
   or the wrapper's `features`.
4. **Errors:** each driver reproduces `DatabaseErrorWrapper` semantics - MRO-based mapping to the
   `django.db` hierarchy with subclasses first and `__cause__` chaining - for `execute` and
   `execute_many`, including MySQL's integrity-code remap. It exposes `is_disconnect(exc)`; broken
   connections are discarded. No driver exception type crosses layer `drivers`.
   *Amended 2026-11-24 (#2, PR #46): this is about database errors. A pool configuration error
   (`min_size > max_size`) surfaces as `psycopg_pool`'s builtin `ValueError` from `open()`, see
   ADR-0001 Decision 8.*
5. **Streams** are async context managers (async-correctness rule 7, ADR-0001 rule 7).
6. A **recording driver** (queued results + statement log) implements the protocol for unit tests
   of upper layers. It lives in `asyncopation.testing`. Query counting is built on observers
   (ADR-0005), not on the recording driver.

## Consequences
+ `engine`/`query` stay backend-agnostic; a new backend is one driver module.
- Drivers must track Django's connection setup; every non-public helper they reuse
(`get_adapters_template`, server-side cursor classes) is ledgered and drift-tested.
- Exception mapping tables need maintenance when drivers add exception classes.

## Validation
- differential: `FieldZoo` round-trip on PostgreSQL, MySQL, MariaDB matches the sync ORM types;
  `TIME_ZONE` / `USE_TZ` matrix (UTC and non-UTC).
- integration: MySQL `update()` rowcount when rows already hold the value (FOUND_ROWS) equals sync.
- integration: unique, NOT NULL and CHECK violations raise `IntegrityError` on every backend;
  a killed connection raises `OperationalError` and is not returned to the pool.
- integration: `server_side_binding=True` statements and streams behave like the sync backend (values, cursors, withhold)
- integration: `HStoreField` round-trip once `contrib.postgres` is in the harness.
