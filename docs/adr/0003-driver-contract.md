# ADR-0003: Driver contract - protocol, capabilities, normalization and errors

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-24 (#2, PR #46) - Decision 1 (cursor selection mirrors Django's `server_side_binding`), Decision 2 (hstore/citext registration deferred to #43), Decision 4 (configuration-error exception), the Decision code block (`capabilities` property, `is_disconnect`), new Validation item (`server_side_binding=True`), Option D (PostgreSQL capabilities come from the driver's `open()` connection); approved by: maintainer (@devgyurak), 2026-11-24 in the PR #46 review; architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Amended: 2026-11-24 (#3, PR #45) - Context (Django's `get_adapters_template` reaches the driver through the `_django/postgresql_adapters.py` boundary), Consequences (A104 confinement of psycopg re-exports, the adapter boundary module importable only by `drivers/postgresql`, Django's `mogrify` unused, drivers export no psycopg objects), new Validation items; approved by: maintainer (@devgyurak), 2026-11-25 in the PR #45 review; architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-24, changes applied)
- Amended: 2026-11-25 (#5, PR #61) - Decision 7 (cancel-in-transaction contract); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#43) - Decision 2 (hstore/citext registration implemented: gated on `django.contrib.postgres` being installed at `configure()`, OIDs read once per `open()` on the first connection, registered per physical connection and never on the shared adapters template, not refreshed until the next `open()`), Option D note (the "no probe query" statement concerns capabilities), Validation (`HStoreField` / `ArrayField(HStoreField)` / `citext[]` round-trip); approved by: maintainer (@devgyurak), 2026-11-25 (issue #43 design session); architect verdict: AMEND OK WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#7, PR #70) - Decision 1 (asyncmy decoder shim, `stmt_cache_size`, autocommit), Decision 2 (MySQL/MariaDB `open()` reads `mysql_server_data` once; optional `SupportsServerData` protocol; `atomic_execute_many=False`; `Capabilities` unchanged), Decision 4 (integrity-code remap order, cryptography `RuntimeError`, disconnect codes), Decision 5 (stream early exit and `net_write_timeout`), Decision 7 (asyncmy cancel behaviour), Validation; approved by: maintainer (@devgyurak), 2026-11-25 (issue #7a design session); architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-26 (#7, PR #70) - Decision 2 (a rewritten `INSERT` is split into statements of at most 64 KiB, each atomic), Decision 4 (errno 2014 discards the connection and is a disconnect code), Decision 5 (the transaction state machine: `SET autocommit = 0` after a server-ended transaction, a failed `COMMIT` / `ROLLBACK` / autocommit restore discards the connection, `DO 0` status refresh; a stream open refuses further statements, `begin()` and `commit()` with `InterfaceError`; a connection is discarded after an exception that is not a server error; client-side formatting and the charset check before any I/O (a lone surrogate is a `UnicodeEncodeError`, as with mysqlclient) and the `TypeError` / `ProgrammingError` rules; `execute_many` owned by the driver (own `INSERT`/`REPLACE ... VALUES` rewrite with 64 KiB chunks, whole-batch validation before any I/O, an intended difference from the per-row loop of mysqlclient), the asyncmy internals the driver relies on and their pins, the rewrite rule adapted from asyncmy (Apache-2.0) and the list of intended differences from Django's sync MySQL path; parameter rendering: `set` / `frozenset` of strings, number subclasses as their quoted `str()`, exact container types, NaN / infinity (also `-inf`) as `ProgrammingError` at any depth, a top-level `bytes` parameter keeps asyncmy's `_binary` literal (a binary comparison: an intended sync/async difference for the maintainer to approve) and `OPTIONS["binary_prefix"]` is rejected, `OPTIONS["sql_mode"]` order; the seven-errno class mapping; stream cursor closing; the `LOCK TABLES` pool caveat), Validation (new items); approved by: pending maintainer approval (to be confirmed in the PR #70 review); architect verdict: APPROVE WITH CHANGES (architect reviews 2026-11-26 of the amendment and of the `execute_many` redesign, changes applied)

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
   `CLIENT.FOUND_ROWS`. *Amended 2026-11-25 (#7, PR #70): the shim is `{**asyncmy.converters.conversions, TIME: typecast_time}` plus decoders returning `None` for zero dates (mysqlclient's behaviour), `charset="utf8mb4"`, `autocommit=True` at connect (asyncmy defaults to off), session setup (`SET SQL_AUTO_IS_NULL = 0`, `SET SESSION TRANSACTION ISOLATION LEVEL`, composed after the user's `init_command` with its trailing `;` and whitespace stripped, joined by a newline and `;` so that a trailing `--` or `#` comment cannot swallow the assignments; a user `init_command` that ends in a comment after a `;` (`stmt; -- note`) or consists only of a comment is not supported: the joined text contains an empty statement and the server rejects it, error 1064; a trailing comment without a `;` works) through `init_command` because asyncmy's pool has no per-connection configure hook (measured on MySQL 8.4 and MariaDB 11.8: `init_command` runs as one `query()` with `MULTI_STATEMENTS` always on, the remaining result packets are consumed, the next statement on the connection works and a failing statement fails the connect; a user command ending in `;` would otherwise produce `;;`, a syntax error), and while the session has `sql_mode` `NO_BACKSLASH_ESCAPES`, `bytes`, `bytearray` and `memoryview` parameters are sent as hex literals (`X'..'`; asyncmy's own escaping ignores that mode and would let a quote end the literal; otherwise asyncmy's escaping stays, since a hex literal doubles the wire size and a blob near `max_allowed_packet` would stop fitting), and `stmt_cache_size` must stay 0 (the prepared-statement path ignores `conv`; a larger value raises `ImproperlyConfigured`). Measured against mysqlclient 2.3.2 on MySQL 8.4 and MariaDB 11.8 (DATETIME(6), DATE, TIME, DECIMAL, JSON, BLOB, VARBINARY, BIT(1)/BIT(8), TINYINT(1), YEAR, FLOAT, DOUBLE, BIGINT UNSIGNED, TEXT): with this shim every decoded value and type is equal; BIT, BLOB and JSON are `bytes`, `bytes` and `str` in both drivers, so they are not differences. The only difference asyncmy has by default is the zero date (`'0000-00-00'` / `'0000-00-00 00:00:00'` as `str`, where mysqlclient returns `None`), which the shim closes; `typecast_time` rejects a negative or over-24-hour TIME in both drivers alike (Django's behaviour). The list of values that cannot be matched is therefore empty; a unit test compares the shim with mysqlclient's `conversions` and the integration suite compares fetched rows.* *Amended 2026-11-24 (#2, PR #46): the cursor selection (default
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
   `CreateExtension` runs). A database dropped and re-created while the driver is open gets new
   OIDs too: its hstore then silently reads as `str` until the next `open()`, and with
   `server_side_binding` a dict parameter fails with `InternalError` because the dumper still
   names the stale OID (reproduced with a non-existent OID). The flag is fixed at `configure()`
   like `USE_TZ`. The catalog queries use the raw connection, so ADR-0005 observers do not see
   them. The compile-only wrapper needs no hstore handling: its `compose_sql` renders only exact
   built-in types and rejects the rest. `connection_created` is **not** sent for our connections (documented).
   *Amended 2026-11-25 (#43): implemented as described (the earlier text was a plan, deferred
   to #43).*
   *Amended 2026-11-25 (#7, PR #70): for MySQL/MariaDB the asyncmy driver's `open()` runs, on its first direct connection, Django's exact `mysql_server_data` `SELECT` once, and stores it as a frozen `MySQLServerData`. `Capabilities.server_version` is derived from the same `VERSION()` string with Django's `server_version_re`, so it equals `mysql_version` by construction; `Capabilities` gains no field (RETURNING is the wrapper's `features.can_return_columns_from_insert`) and `atomic_execute_many` is `False` (a multi-row `INSERT ... VALUES` is one atomic statement, every other `executemany` loops). The data is exposed through the optional protocol `SupportsServerData` in `drivers/base.py` (property `server_data`; `RuntimeError` before `open()` and after `close()`, like `capabilities`); PostgreSQL does not implement it. The probe connection is configured like a pooled one except for `SET SQL_AUTO_IS_NULL = 0`, which depends on the result: the user's `init_command` and `OPTIONS["sql_mode"]` apply (so `sql_mode` in the data is the session's) and so does the isolation level assignment, which does not influence the data.*
   *Amended 2026-11-26 (#7, PR #70): a rewritten `INSERT` is split into statements of at most 64 KiB, each atomic (Decision 5).*
3. **Single source of truth for SQL shape:** the compile-only wrapper's `features`. `Capabilities`
   holds only driver/runtime facts. Core code never reads vendor strings; it reads `Capabilities`
   or the wrapper's `features`.
4. **Errors:** each driver reproduces `DatabaseErrorWrapper` semantics - MRO-based mapping to the
   `django.db` hierarchy with subclasses first and `__cause__` chaining - for `execute` and
   `execute_many`, including MySQL's integrity-code remap and the seven errnos (1166, 1169, 1263, 1364, 1366, 1367, 2014) on which asyncmy's class table differs from mysqlclient's, which keep mysqlclient's class (`_ERRNO_CLASSES`, checked by a differential sweep). It exposes `is_disconnect(exc)`; broken
   connections are discarded. No driver exception type crosses layer `drivers`.
   *Amended 2026-11-24 (#2, PR #46): this is about database errors. A pool configuration error
   (`min_size > max_size`) surfaces as `psycopg_pool`'s builtin `ValueError` from `open()`, see
   ADR-0001 Decision 8.*
   *Amended 2026-11-25 (#7, PR #70): MySQL's `OperationalError` carrying 1048, 1690, 3819 or 4025 becomes `IntegrityError` first (Django's `CursorWrapper.codes_for_integrityerror`, in `execute` and `execute_many`), then the MRO mapping applies. `is_disconnect` covers 1053, 2006, 2013, 2055, 1927, 3169 and a closed/cancelled connection (`InterfaceError`), never the pool-timeout `OperationalError`. asyncmy's `RuntimeError` for a missing `cryptography` package (needed by `caching_sha2_password`) becomes `ImproperlyConfigured` with an install hint; `cryptography` joins the `mysql` extra.*
   *Amended 2026-11-26 (#7, PR #70): `is_disconnect` also covers 2014 (commands out of sync), and the connection is discarded.*
5. **Streams** are async context managers (async-correctness rule 7, ADR-0001 rule 7). *Amended 2026-11-25 (#7, PR #70): asyncmy streams use an unbuffered `SSCursor` (Django's sync MySQL backend buffers the whole result; `DISABLE_SERVER_SIDE_CURSORS` selects a buffered cursor chunked client-side). Leaving early (`break`) closes the cursor, which drains the unread rows; the drain is bounded, and the connection is discarded when it does not finish in time, or at once when the stream's body was cancelled, or failed outside a transaction (no drain for a consumer that is gone; a body that failed inside a transaction drains instead, so a handled error keeps the transaction, as with Django's buffered cursor). An unbuffered cursor keeps the server writing while the consumer is slow: past the server's `net_write_timeout` (60 s by default) the server drops the connection and the stream fails with a disconnect error (2013) - a documented limit of server-side streaming, not retried.*
   *Amended 2026-11-26 (#7, PR #70): A statement run while an unbuffered stream is still open raises `InterfaceError` (asyncmy would drop the stream's unread rows silently; the executors already refuse it with `ConcurrentQueryError`). A connection is also discarded after any exception that is not an error the server reported (for example a decoder's `ValueError` while a result set is read), because unread packets may be left on the wire; the one exception is an error the driver raises itself before anything is written. Client-side formatting and the charset check happen before any I/O: the driver checks every string the caller supplies (the statement and each text parameter, at any depth) against the connection's charset and encodes the statement to `bytes` itself with the `surrogateescape` asyncmy uses, because asyncmy would send a lone `U+DC80` as the byte 0x80 where mysqlclient raises `UnicodeEncodeError` (the formatted statement cannot be checked as a whole: asyncmy carries the raw bytes of a `bytes` parameter as surrogates), so a bad placeholder or parameter count (`ProgrammingError`, as with mysqlclient) leaves the connection untouched. `begin()` and `commit()` are refused like statements while a stream is open. Transactions keep Django's `autocommit = False` semantics although the connection stays in autocommit mode (`begin()` is `BEGIN`): the status flags of every server reply are watched, and when the server ended the transaction by itself (a deadlock rollback, `innodb_rollback_on_timeout`, a DDL statement's implicit commit) the session switches to `SET autocommit = 0` (not a second `BEGIN`, which would release `LOCK TABLES` locks) until `commit()`, `rollback()` or release switch it back (a failed `COMMIT`, `ROLLBACK` or restore discards the connection: a retry would commit implicitly, and the session may still be inside a transaction), so the next statement runs in a new implicit transaction and a later `rollback()` undoes it as with mysqlclient (after an error reply, whose status flags are stale, one `DO 0` refreshes them). Parameters asyncmy cannot escape are converted before formatting: a `list` becomes a tuple (`IN %s`), `bytearray` and `memoryview` become `bytes`, and subclasses of `int`, `float` and `Decimal` (`IntegerChoices` members) become their `str()` text, quoted, which is what mysqlclient sends for a type it has no encoder for (a number is not sent as a number); only the exact types `tuple`, `list`, `set` and `frozenset` are containers, as in mysqlclient (`type(o) is ...`), so a subclass such as a namedtuple is sent as its quoted `str()` too; outside `NO_BACKSLASH_ESCAPES` a top-level `bytes` parameter keeps asyncmy's `_binary'..'` literal (nested in a sequence it is a plain literal, as with mysqlclient; under `NO_BACKSLASH_ESCAPES` it is `X'..'`), so a comparison of such a parameter with a character column is binary (`'ABC' = %s` with `b"abc"` is 0, mysqlclient's plain literal gives 1): an intended sync/async difference, listed for the maintainer's approval; `OPTIONS["binary_prefix"]` is therefore rejected (asyncmy accepts and ignores it); a `set` or `frozenset` of strings is one comma-joined string literal and any other set raises `TypeError` (`Connection._set_literal`); a NaN or infinite float (`-inf` too, which asyncmy does not reject) is a `ProgrammingError("<value> can not be used with MySQL")` at any depth, before any I/O; under `NO_BACKSLASH_ESCAPES` strings inside a sequence, `bytes` and types asyncmy has no encoder for are rendered by the connection, because asyncmy's escaping of them ignores the mode. `OPTIONS["sql_mode"]` is quoted and run after the user's `init_command`, as mysqlclient does (asyncmy would send a list of modes unquoted); an empty value is ignored. Server errors keep mysqlclient's classes: the seven errnos on which asyncmy's table differs (1166, 1169, 1263, 1364, 1366, 1367, 2014) are mapped to mysqlclient's class, checked by a differential test. `execute_many` is owned by the driver (asyncmy's `executemany` is not used): every parameter set is prepared, checked, formatted once and encoded before the first statement is sent, so a client error (a bad set or placeholder, a missing pyformat key, a non-finite float, a lone surrogate) applies nothing, does no round trip and never leaves a resumed transaction behind, an intended difference from mysqlclient's per-row loop, which applies the rows before a bad one (and, inside `async_atomic`, leaves them in the open transaction); a server error on a later statement leaves the earlier statements applied, as with mysqlclient. `INSERT` / `REPLACE ... VALUES (<only placeholders>)` with an optional `ON DUPLICATE ...` is rewritten into multi-row statements of at most 64 KiB (`atomic_execute_many` stays `False`: atomic per statement, as with mysqlclient), anything else (an `AS alias`, `INSERT ... SELECT`, `UPDATE`) runs once per set; the rewrite rule and the chunk loop are adapted from asyncmy (`cursors.pyx`: `RE_INSERT_VALUES`, `_do_execute_many`; Apache-2.0, already a dependency, derived from PyMySQL, MIT; the source and licence are named in the header comment of `drivers/mysql/connection.py` and in `THIRD_PARTY_NOTICES.md`, the repository's first third-party notice file, whose convention the maintainer is asked to confirm), with a lazy head, possessive blanks and no `AS alias` in the tail; mysqlclient (GPL-2.0, not copied) is only the behavioural reference of the differential test, which compares rows, rowcount and statement count. This is wire-level batching of the compiled statement, as mysqlclient's cursor does; it does not decide SQL shape (Decision 3). The lazy head differs from asyncmy's greedy one only when an `ON DUPLICATE` tail ends in `VALUES(%s)`, which is invalid SQL either way. *Intended differences from Django's sync MySQL path (pending maintainer approval):* (1) a client error (a bad set or key, a non-finite float, a lone surrogate) applies none of the batch, where mysqlclient's per-row loop applies the rows before it; (2) a top-level `bytes` parameter keeps asyncmy's `_binary'..'` literal, so comparing it with a character column is binary, and a rewritten `INSERT` group is 7 bytes longer per such parameter, which moves the 64 KiB split (the rows left after a server error differ; reproduced by hand, no test); (3) `OPTIONS["binary_prefix"]` is rejected; (4) a `None` parameter set makes the batch run per row (mysqlclient builds one failing statement; Django never passes `None`); (5) a row that is not a tuple, list or dict is a `ProgrammingError` (mysqlclient accepts any iterable); a generator of rows is accepted as a batch by the MySQL driver although the driver protocol says `Sequence`; (6) the planned statements are held in memory until the batch is sent (about the size of the batch text, measured by hand at 57 MB for 50,000 rows of about 1 KB, no benchmark; the price of applying nothing on a client error); (7) only placeholder-only `VALUES` groups are rewritten, so a literal between named placeholders (`VALUES (%(a)s, NOW(), %(b)s)`) runs per row where mysqlclient rewrites it (other rows survive a server error, `NOW()` is evaluated per row); (8) the `ON DUPLICATE` tail is sent verbatim, while mysqlclient also formats a tail that ends in `)` per row, so `... CONCAT(name, %(c)s)` works for one row there and is a syntax error here; (9) a timezone-aware `time` is sent without its offset where mysqlclient sends `'01:02:03+00:00'` (the ORM rejects aware times first, so only raw SQL is concerned; no test). A placeholder or parameter-count `TypeError` becomes `ProgrammingError`, the `TypeError` of a set with non-string items stays a `TypeError`; `%` in the prefix of a rewritten statement must be written `%%` and raises the bare `TypeError` / `ValueError` as with mysqlclient. The driver relies on these asyncmy internals, pinned by `tests/integration/mysql/test_asyncmy_pins.py` (the `<0.3` bound is the review trigger): the pool's acquire/release behaviour, `Connection.literal` and `escape_string` following the session's `sql_mode`, the `server_status` flags, `Connection._encoding`, `mogrify` carrying the raw bytes of a `bytes` parameter as surrogate characters that `surrogateescape` encodes back (why the caller's strings are checked rather than the formatted statement), the pool's `size` and `freesize`, `cursor.execute(bytes)` sending the bytes unchanged, `mogrify` with `args=None` returning the statement, and `stmt_cache_size == 0`. A `LOCK TABLES` not followed by `UNLOCK TABLES` stays on the pooled connection when it is returned (the driver does not track it; a pool caveat, as for any session state the user sets). A stream always closes its cursor (an unclosed `SSCursor` warns).*
6. A **recording driver** (queued results + statement log) implements the protocol for unit tests
   of upper layers. It lives in `asyncopation.testing`. Query counting is built on observers
   (ADR-0005), not on the recording driver.
7. **Cancel contract** (*added 2026-11-25 (#5, PR #61)*). A statement cancelled while a transaction
   is open leaves its connection closed (discarded) before `CancelledError` propagates, and
   `rollback()` on such a connection is a no-op. The pinned executor relies on this (ADR-0001 6c)
   and needs no explicit `discard()` on the connection protocol: the pool drops a closed connection
   when it is returned. Every driver must keep this, asyncmy included (#7); `RecordingDriver` models
   it (`RecordingConnection.discarded`; later use raises `InterfaceError`). *Amended 2026-11-25 (#7, PR #70): asyncmy closes a connection whose statement or fetch is cancelled (`connected` is false, the next use raises `InterfaceError(0, 'Cancelled during execution')`), and its pool drops a closed connection or one released inside a transaction; `commit()` / `rollback()` on a closed connection are no-ops.*

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
- *Added 2026-11-25 (#5, PR #61):* integration: a statement cancelled inside a transaction closes the
  connection and `rollback()` afterwards is a no-op (PostgreSQL; the asyncmy case is `tests/integration/mysql/test_cancellation.py`, *amended 2026-11-25 (#7, PR #70)*)
- *Amended 2026-11-25 (#7, PR #70):* unit: the decoder shim equals mysqlclient's `conversions` (`mysql-sync` extra) except the listed documented differences; `OPTIONS` validation; error mapping and `is_disconnect`. The `mysql_server_data` `SELECT` that the driver runs carries a `# Ported from django/db/backends/mysql/base.py::mysql_server_data @ 6.1.3` header and a ledger row, and `typecast_time` comes through `_django.mysql` (ledger `[[internal]]` row). Integration (MySQL, MariaDB): `server_data` equals Django's sync `mysql_server_data` field by field and `Capabilities.server_version` equals `mysql_version`; `update()` rowcount for an unchanged row (`FOUND_ROWS`); MariaDB `INSERT ... RETURNING` through `execute`; `lastrowid` on MySQL; `execute_many` rewrite and non-atomicity of looped statements; cancel of a statement and of a stream discards the connection and the pool refills; stream early exit leaves a usable connection; an `init_command` and a non-default isolation level apply to pooled connections; a missing `cryptography` surfaces as `ImproperlyConfigured`; a transaction the server ended (a deadlock between two connections, a DDL statement inside `begin()`) is resumed so that later statements are undone by `rollback()` exactly as with mysqlclient; every errno from 1000-1900 and 3000-4200 raised through a stored procedure maps to the same Django class as with mysqlclient; nested sequence parameters and `bytes` are safe under `NO_BACKSLASH_ESCAPES`; `tests/unit/django_drift/test_mysql_ports.py` compares every ported attribute with Django's real MySQL backend (the Django-main canary installs mysqlclient for it).
- *Amended 2026-11-26 (#7, PR #70):* unit: a failed `COMMIT`, `ROLLBACK` or autocommit restore discards the connection, and a client side error does no round trip, on every entry point (`tests/unit/drivers/test_mysql_transaction_state.py`); the batch planner: rewrite shapes, chunk boundary (exactly at and one byte over 64 KiB), an oversized row, `%%`, `None` sets, and a lone surrogate or bad third set rejecting the whole batch (`tests/unit/drivers/test_mysql_batch.py`); `OPTIONS["binary_prefix"]` rejected (`tests/unit/drivers/test_mysql_build_config.py`); integration (MySQL/MariaDB): the table-driven differential of every parameter type and shape against mysqlclient, echoing each value back, in the three `sql_mode` variants, with the documented differences pinned per value, shape and mode (`tests/integration/mysql/test_param_rendering.py`, `tests/support/mysql_render.py`); `execute_many` against Django's sync `executemany`: rows, rowcount, statement count and error class for every rewrite shape (`AS alias`, `%%`, a postfix placeholder, `REPLACE`, `INSERT ... SELECT`, `UPDATE`, `DELETE`, dict parameters, `None` sets), chunking at several row widths, a server error in a later chunk, the intended client-error difference, a cancel between statements (`tests/integration/mysql/test_execute_many.py`); a value or batch the client rejects (non-string set, non-finite float at any depth, missing key, lone surrogate including U+DC80, in the SQL or a parameter) keeps the connection and its transaction on every entry point and applies nothing (`tests/integration/mysql/test_client_errors.py`); the errno class mapping, here also every errno from 1900-2100 and including 2014 (`tests/integration/mysql/test_errno_mapping.py`); a server-ended transaction (deadlock, DDL) is resumed, with its locks, on every entry point (`tests/integration/mysql/test_server_ended_transactions.py`); the asyncmy internals above (`tests/integration/mysql/test_asyncmy_pins.py`).
- differential: `FieldZoo` round-trip on PostgreSQL, MySQL, MariaDB matches the sync ORM types;
  `TIME_ZONE` / `USE_TZ` matrix (UTC and non-UTC).
- integration: MySQL `update()` rowcount when rows already hold the value (FOUND_ROWS) equals sync.
- integration: unique, NOT NULL and CHECK violations raise `IntegrityError` on every backend;
  a killed connection raises `OperationalError` and is not returned to the pool.
- *Amended 2026-11-24 (#2, PR #46):* integration: `server_side_binding=True` statements and streams
  behave like the sync backend (values, cursors, withhold)
- *Amended 2026-11-24 (#3, PR #45): new Validation items:* the A104 table in `tools/guards/reexports.py` lists every psycopg name the installed Django's PostgreSQL, MySQL and PostGIS modules bind (drift test), and `mogrify` of a Django module appears nowhere in `src`.
- integration (PostgreSQL; `django.contrib.postgres` installed through the harness's `harness.pgcatalog` app; `server_side_binding` off and on): `HStoreField`, `ArrayField(HStoreField())`, `citext[]` and scalar `citext` round-trip with the sync ORM's values and types, including dict parameters; without the app the hstore column reads as `str`; with no extension installed `open()` succeeds and registers nothing; `open()` queries the OIDs once (never per pool connection) and a `close()` / `open()` pair queries again; the shared adapters template has no hstore loader afterwards; a reconnected pool connection is registered.
