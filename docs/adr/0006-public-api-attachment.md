# ADR-0006: Public API attachment - `aq()` composition and the `aobjects` descriptor

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated

- Amended: 2026-11-25 (#5, PR #61) - Decision 6 (`ConcurrentQueryError` is public API); approved by: maintainer (@devgyurak), to be confirmed in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)

## Context
Plan §3 adds `aobjects` next to `objects` and §4.6 introduces `aq(qs)` for related managers. It
does not decide how they attach to user models. Real projects use custom managers and QuerySet
subclasses (`QuerySet.as_manager()`, `Manager.from_queryset()`), whose chaining methods must keep
working before async evaluation.

Django facts (6.1): `ModelBase` registers anything with `contribute_to_class` - a `Manager`
subclass would land in `_meta.managers`, could become `_default_manager` and suppress the implicit
`objects`. Plain descriptors go through `type.__new__` and get `__set_name__`. `QuerySet` already
has `aget`, `acount`, `aiterator`, `__aiter__`, ... built on `sync_to_async`, and `__repr__`
evaluates `self[:21]`. `FETCH_RAISE` (`django/db/models/fetch_modes.py`) raises
`FieldFetchBlocked` for forward FK/O2O, reverse O2O, GenericForeignKey and deferred fields, but
not for reverse FK / M2M managers (those return lazy QuerySets).

Prior art: django-async-backend monkeypatches every `Model` with a fixed manager (custom QuerySet
methods lost, related managers unsupported); django-turbo-orm has a separate `AsyncQuerySet`
(same loss); Ecto `NotLoaded`, Tortoise `NoValuesFetched` and Hibernate Reactive's explicit,
non-recursive `fetch()` (its silent null for unloaded associations is a counter-example).

## Options
### A - Subclass: generate an async subclass of each user QuerySet class at runtime
+ `isinstance` works. - MRO/metaclass surprises, pickling, one generated class per user class.

### B - Fixed async manager / QuerySet (DAB, turbo)
+ simple. - custom QuerySet methods unavailable.

### C - Composition: `aq()` wraps any QuerySet; chaining is proxied and re-wrapped
+ works with any user QuerySet and related manager; no monkeypatching. - careful proxying;
`isinstance(x, QuerySet)` is false for the wrapper.

## Decision
**C.**

1. **`aq(obj)`** accepts a QuerySet or a Manager (including related managers, via
   `get_queryset()`) and returns an `AsyncQuerySet` wrapper. A pre-filled `_result_cache` (e.g. a
   related manager after prefetch) is honoured: evaluation returns it with no I/O.
2. **Proxying.** Attribute access is proxied and any call returning a `QuerySet` is re-wrapped, so
   user-defined chaining methods work. Evaluation methods are Asyncopation's own; any `a*` name not
   yet implemented raises `NotImplementedError` and **never** falls through to Django's
   `sync_to_async` methods. Sync evaluators (`__iter__`, `__len__`, `__bool__`, integer
   `__getitem__`, `get`, `first`, `count`, `exists`, `iterator`, `aggregate`, `update`, `delete`,
   `create`, `get_or_create`, `in_bulk`) raise `asyncopation.SyncEvaluationError`. `__repr__` never
   evaluates.
3. **`aobjects`** is a plain descriptor, `asyncopation.AsyncObjects`, that is **not** a Django
   `Manager` and defines no `contribute_to_class`. `Model.aobjects` returns
   `aq(owner._default_manager.get_queryset())` using the accessed `owner` (abstract and
   multi-table inheritance); instance access raises like `Manager`. Models opt in by declaring
   `aobjects = AsyncObjects()`; third-party/swappable models (`auth.User`, `ContentType`) use
   `aq(Model.objects)` or `AsyncObjects.attach(Model)`. `Model` is never monkeypatched.
4. **Fetch mode.** Both `aobjects` and `aq()` apply `FETCH_RAISE` (any other sync fetch mode would
   do sync I/O on attribute access). Unloaded forward/one-to-one relations and deferred fields
   raise `FieldFetchBlocked`; reverse FK / M2M managers are evaluated through `aq()`. `afetch()` is
   explicit and non-recursive; batched peer loading returns results aligned with the parents.
5. Public names stay aligned with Django core's async work: `async_atomic(using, savepoint,
   durable)`, `on_commit` accepting async callables.
6. `asyncopation.ConcurrentQueryError(RuntimeError)` is public API (ADR-0001 Decision 3); it lives
   in the import-free `asyncopation.errors` and is re-exported at the package root (*added
   2026-11-25 (#5, PR #61)*).

## Consequences
+ Custom QuerySets, related managers and third-party QuerySet methods work.
- The wrapper is not a `QuerySet`; code that type-checks for `QuerySet` must unwrap (`.queryset`).
- Manager-only methods (e.g. `UserManager.create_user`) are not on `aobjects`, and user QuerySet
methods that evaluate internally raise `SynchronousOnlyOperation`.
- Changing this shape later is a breaking public API change.

## Validation
- differential: a custom QuerySet method chained through `aobjects` and through
  `aq(author.book_set)` returns the same rows as the sync ORM.
- integration: `aq(author.book_set)` after `prefetch_related` issues zero queries.
- unit: `repr(aq(qs))` issues zero queries; `aq(qs).acreate` raises `NotImplementedError` without a
  thread hop; `list(aq(qs))` raises `SyncEvaluationError`.
- unit: a model declaring only `aobjects` still has `objects` as its default manager; `aobjects` on
  a model with a custom default manager exposes its QuerySet methods.
