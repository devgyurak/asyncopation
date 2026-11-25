# ADR-0003: Driver contract - protocol, capabilities, normalization and errors

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-24 (#2, PR #46) - Decision 1 (cursor selection mirrors Django's `server_side_binding`), Decision 2 (hstore/citext registration deferred to #43), Decision 4 (configuration-error exception), the Decision code block (`capabilities` property, `is_disconnect`), new Validation item (`server_side_binding=True`), Option D (PostgreSQL capabilities come from the driver's `open()` connection); approved by: maintainer (@devgyurak), 2026-11-24 in the PR #46 review; architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Amended: 2026-11-24 (#3, PR #45) - Context (Django's `get_adapters_template` reaches the driver through the `_django/postgresql_adapters.py` boundary), Consequences (A104 confinement of psycopg re-exports, the adapter boundary module importable only by `drivers/postgresql`, Django's `mogrify` unused, drivers export no psycopg objects), new Validation items; approved by: maintainer (@devgyurak), 2026-11-25 in the PR #45 review; architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Amended: 2026-11-25 (#43) - Decision 2 (hstore/citext registration implemented: gated on `django.contrib.postgres` being installed at `configure()`, OIDs read once per `open()` on the first connection, registered per physical connection and never on the shared adapters template, not refreshed until the next `open()`), Option D note (the "no probe query" statement concerns capabilities), Validation (`HStoreField` / `ArrayField(HStoreField)` / `citext[]` round-trip); approved by: maintainer (@devgyurak), 2026-11-25 (issue #43 design session); architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-25, changes applied)

## Context
Plan §4.3 lists the driver operations and the per-driver pitfalls (psycopg needs
`AsyncClientCursor`; MySQL has no `RETURNING`, MariaDB does; asyncmy may return different types
than mysqlclient). It does not say how capabilities are expressed, where differences are
absorbed, or how driver exceptions reach users.

Django's sync backends do more than pick a driver: PostgreSQL `get_connection_params` passes
`context=get_adapters_template(use_tz, timezone)` (non-public `psycopg_any`; *Amended 2026-11-24 (#3, PR #45): the driver reaches it through `_django/postgresql_adapters.py`*), `prepare_threshold`,
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
connection (startup parameters, no probe query for capabilities; `open()` may run the two contrib.postgres catalog queries, Decision 2); ADR-0004 Decision 2.*

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
    # Amended 2026-11-24 (#2, PR #46): added to the protocol
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
2. **contrib.postgres:** when `django.contrib.postgres` is in `INSTALLED_APPS` at `configure()`
   time (Django registers its handlers only then; the flag is read with `apps.is_installed`
   through the `_django` boundary and handed to the driver as configuration), the PostgreSQL
   driver registers hstore and citext handlers on each physical connection, as Django's
   `register_type_handlers` does on `connection_created`. `open()` reads the type OIDs once on its
   first, direct connection with Django's query (`SELECT oid, typarray FROM pg_type WHERE typname
   = %s` for `hstore` and `citext`, every schema's type, not `TypeInfo.fetch`) and keeps them in
   an immutable value bound to that open's pool. Every connection's configure step, the first
   connection and each pooled one, then registers synchronously and without I/O:
   `register_hstore` for each hstore OID and the array loader only for each citext array OID (so
   `citext[]` loads as `list[str]` and scalar `citext` as `str`, as in Django). Registration goes
   on the connection's own adapters, never on the shared adapters template. A missing extension
   yields empty OIDs: nothing is registered and nothing fails. A pool configure step that finds
   the OIDs missing raises instead of skipping. Without `django.contrib.postgres` nothing is
   queried or registered (hstore reads as `str`, like the sync backend). The OIDs are not
   refreshed while the driver stays open: an extension created or re-created after `open()` takes
   effect on the next `close()` and `open()` (Django caches them per process until
   `CreateExtension` runs; likewise a database dropped and re-created while the driver is open
   gets new OIDs, so its hstore silently reads as `str` until the next `open()`, and with
   `server_side_binding` a dict parameter fails with `InternalError` because the dumper still
   names the stale OID), and the flag is fixed at `configure()` like `USE_TZ`. The catalog
   queries use the raw connection, so ADR-0005 observers do not see them. The compile-only
   wrapper needs no hstore handling: its `compose_sql` renders only exact built-in types and
   rejects the rest. `connection_created` is **not** sent for our connections (documented).
   *Amended 2026-11-25 (#43): implemented as described (the earlier text was a plan, deferred
   to #43).*
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
  *Amended 2026-11-24 (#3, PR #45): the rest of this bullet is new.* The psycopg names Django's
  modules re-export (`psycopg_any`: `adapt`, `sql`, `ClientCursor`, `errors`, `Jsonb`, ranges, ...;
  `postgresql.base.Database`, ...) are driver API and may be used only in `drivers/` (guard A104,
  table in `tools/guards/reexports.py`, pinned against the installed Django). Django's own adapter
  helpers in those modules (`get_adapters_template`, `register_tzloader`) are imported by one
  `_django` boundary module, `_django/postgresql_adapters.py` (the psycopg driver's
  `adapters_template`), which only `drivers/postgresql` may import (guard A104); Django's `mogrify`
  (it renders through a cursor) is used nowhere; a driver cursor's own `.mogrify` is driver API.
  Drivers do not export psycopg objects under public names of their own
  (`from asyncopation.drivers.postgresql import sql`): a convention enforced by review, the guards
  do not follow that route.
- Exception mapping tables need maintenance when drivers add exception classes.

## Validation
- differential: `FieldZoo` round-trip on PostgreSQL, MySQL, MariaDB matches the sync ORM types;
  `TIME_ZONE` / `USE_TZ` matrix (UTC and non-UTC).
- integration: MySQL `update()` rowcount when rows already hold the value (FOUND_ROWS) equals sync.
- integration: unique, NOT NULL and CHECK violations raise `IntegrityError` on every backend;
  a killed connection raises `OperationalError` and is not returned to the pool.
- *Amended 2026-11-24 (#2, PR #46):* integration: `server_side_binding=True` statements and streams
  behave like the sync backend (values, cursors, withhold)
- *Amended 2026-11-24 (#3, PR #45): new Validation items:* the A104 table in `tools/guards/reexports.py` lists every psycopg name the installed Django's PostgreSQL, MySQL and PostGIS modules bind (drift test), and `mogrify` of a Django module appears nowhere in `src`.
- integration (PostgreSQL; `django.contrib.postgres` installed through the harness's `harness.pgcatalog` app; `server_side_binding` off and on): `HStoreField`, `ArrayField(HStoreField())`, `citext[]` and scalar `citext` round-trip with the sync ORM's values and types, including dict parameters; without the app the hstore column reads as `str`; with no extension installed `open()` succeeds and registers nothing; `open()` queries the OIDs once (never per pool connection) and a `close()` / `open()` pair queries again; the shared adapters template has no hstore loader afterwards; a reconnected pool connection is registered.
