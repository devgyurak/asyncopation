# ADR-0006: Public API attachment - `aq()` composition and the `aobjects` descriptor

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-25 (#5, PR #61) - Decision 6 (`ConcurrentQueryError` is public API); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#6, PR #65) - Decisions 1, 2, 4 and 6 (`SyncEvaluationError` is public API in `asyncopation.errors`, re-exported at the root; `aq` / `AsyncQuerySet` load lazily from the root; blocked `a*` names are the coroutine methods and async generators Django defines on `QuerySet`; a slice with a step is a sync evaluator; `raw()` raises `NotImplementedError`; the `iterator()` error points to `alist()`; `aq()` keeps a pre-filled result cache by not cloning; `FETCH_RAISE` is set in place only on a real `Manager`'s own fresh QuerySet, every other QuerySet or duck-typed `get_queryset()` result is cloned even if it already is `FETCH_RAISE`, and the clone and `all()` of a manager wrapper keep the pending sticky filter), new Validation items; approved by: maintainer (@devgyurak), to be confirmed in the PR #65 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25 of the PR #65 amendment, changes applied)

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
   related manager after prefetch) is honoured: evaluation returns it with no I/O. *Amended
   2026-11-25 (#6, PR #65): such a QuerySet is wrapped as it is, not cloned, because cloning drops
   the cache; a result list is always a copy, so changing it never changes the cache.*
2. **Proxying.** Attribute access is proxied and any call returning a `QuerySet` is re-wrapped, so
   user-defined chaining methods work. Evaluation methods are Asyncopation's own; any `a*` name not
   yet implemented raises `NotImplementedError` and **never** falls through to Django's
   `sync_to_async` methods. Sync evaluators (`__iter__`, `__len__`, `__bool__`, integer
   `__getitem__`, `get`, `first`, `count`, `exists`, `iterator`, `aggregate`, `update`, `delete`,
   `create`, `get_or_create`, `in_bulk`) raise `asyncopation.SyncEvaluationError`. `__repr__` never
   evaluates. *Amended 2026-11-25 (#6, PR #65): "any `a*` name" is every coroutine method **and
   async generator** (`aiterator`) Django defines on `QuerySet`, found by scanning the class, minus
   those implemented; a slice with a step (`qs[::2]`, which Django evaluates) is a sync evaluator
   too; `raw()` raises `NotImplementedError` (it returns an unwrapped `RawQuerySet`); the
   `iterator()` error says `aiterator()` is not implemented yet and points to `alist()`.*
3. **`aobjects`** is a plain descriptor, `asyncopation.AsyncObjects`, that is **not** a Django
   `Manager` and defines no `contribute_to_class`. `Model.aobjects` returns
   `aq(owner._default_manager.get_queryset())` using the accessed `owner` (abstract and
   multi-table inheritance); instance access raises like `Manager`. Models opt in by declaring
   `aobjects = AsyncObjects()`; third-party/swappable models (`auth.User`, `ContentType`) use
   `aq(Model.objects)` or `AsyncObjects.attach(Model)`. `Model` is never monkeypatched.
4. **Fetch mode.** Both `aobjects` and `aq()` apply `FETCH_RAISE` (any other sync fetch mode would
   do sync I/O on attribute access). *Amended 2026-11-25 (#6, PR #65): `aq()` sets it in place on
   the fresh `get_queryset()` result of a Manager (a clone would consume the pending sticky filter
   of a many-to-many related manager, so `aq(tag.books).filter(tags__name=x)` would differ from
   Django's), and only for a real `Manager` instance: what a duck-typed `get_queryset()` returns
   may be shared. A QuerySet with a filled result cache is wrapped as it is (`alist()` only reads
   it); every other QuerySet, `FETCH_RAISE` ones included, is cloned with
   `fetch_mode(FETCH_RAISE)`, so the caller's is never touched and `alist()` never fills its
   cache (a class-attribute QuerySet would otherwise return stale rows to later requests). Only
   the QuerySets Django creates while the wrapper re-wraps its own chaining (`filter()`, `all()`,
   a slice, copies) skip the clone. The clone is a chain, which consumes the pending sticky
   filter (`Query.filter_is_sticky`) of a related-manager QuerySet, so it is copied onto the
   clone: `aq(tag.books.all()).filter(tags__name=x)` does not depend on how `tag` was loaded.
   `all()` of a wrapper that came straight from a manager keeps that state, as `Manager.all()`
   (which returns `get_queryset()` unchained) does: its sticky filter, and a prefetched result
   cache, which it returns as it is (`aq(t.books).all().alist()` makes no statement); copies keep
   the sticky filter and that behaviour. The in-place switch assumes `Manager.get_queryset()`
   returns a fresh QuerySet, as Django's and related managers do; a custom manager returning a
   shared one that is already `FETCH_RAISE` gets a clone.*
   Unloaded forward/one-to-one relations and deferred fields raise `FieldFetchBlocked`; reverse FK
   / M2M managers are evaluated through `aq()`. `afetch()` is explicit and non-recursive; batched
   peer loading returns results aligned with the parents.
5. Public names stay aligned with Django core's async work: `async_atomic(using, savepoint,
   durable)`, `on_commit` accepting async callables.
6. `asyncopation.ConcurrentQueryError(RuntimeError)` is public API (ADR-0001 Decision 3); it lives
   in the import-free `asyncopation.errors` and is re-exported at the package root (*added
   2026-11-25 (#5, PR #61)*). *Amended 2026-11-25 (#6, PR #65):* `asyncopation.SyncEvaluationError`
   is public API on the same terms, `aq` and `AsyncQuerySet` are exported lazily (see the note
   below).

*Amended 2026-11-25 (#6, PR #65), Decision 6: `asyncopation.SyncEvaluationError(RuntimeError)` is
public API; like `ConcurrentQueryError` it lives in the import-free `asyncopation.errors` and is
re-exported at the root, while `aq` and `AsyncQuerySet` are exported lazily (PEP 562) so
`import asyncopation` loads neither Django nor a driver. The names that raise `NotImplementedError` are the coroutine
methods and async generators Django defines on `QuerySet` (found by scanning the class, so a Django
upgrade that adds one is blocked too) minus those implemented. `aq()` wraps a QuerySet that already
has a result cache, or is already `FETCH_RAISE`, without cloning it; evaluation methods fill the
wrapped QuerySet's result cache as Django's `alist` does, and a chain re-wraps through `aq()`.*

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
- *Amended 2026-11-25 (#6, PR #65):* differential: `aq(tag.books).filter(tags__name=x)` (many-to-many
  related manager, pending sticky filter) and chains on a reverse foreign-key manager return the
  same rows as the sync ORM; so do `aq(tag.books).all().filter(...)` and
  `aq(tag.books.all()).filter(...)`, whether `tag` was loaded by the sync ORM or by `aq()`;
  `aq(author.books).all().alist()` after `prefetch_related` issues no statement and returns the
  prefetched instances.
- *Amended 2026-11-25 (#6, PR #65):* unit and integration: `aq()` of a `FETCH_RAISE` QuerySet or of
  a duck-typed source's shared QuerySet clones it, and `alist()` leaves the caller's cache and
  fetch mode alone; only a real `Manager`'s QuerySet is switched in place.
- *Amended 2026-11-25 (#6, PR #65):* unit: `aq(qs).aiterator()` raises `NotImplementedError` without
  a thread hop (it is an async generator, so a coroutine-only scan would miss it); `aq(qs)[::2]`
  raises `SyncEvaluationError`; `aq(qs).raw(...)` raises `NotImplementedError`; copies keep
  `FETCH_RAISE`.
