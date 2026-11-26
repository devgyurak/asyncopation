# ADR-0006: Public API attachment - `aq()` composition and the `aobjects` descriptor

- Status: Accepted
- Date: 2026-11-24
- Deciders: maintainer (@devgyurak)
- Architect review: APPROVE WITH CHANGES (2026-11-24); required changes incorporated
- Amended: 2026-11-25 (#5, PR #61) - Decision 6 (`ConcurrentQueryError` is public API); approved by: maintainer (@devgyurak), 2026-11-25 in the PR #61 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25, changes applied)
- Amended: 2026-11-25 (#6, PR #65) - Decisions 1, 2, 4 and 6 (`SyncEvaluationError` is public API in `asyncopation.errors`, re-exported at the root; `aq` / `AsyncQuerySet` load lazily from the root; blocked `a*` names are the coroutine methods and async generators Django defines on `QuerySet`; a slice with a step is a sync evaluator; `raw()` raises `NotImplementedError`; the `iterator()` error points to `alist()`; `aq()` keeps a pre-filled result cache by not cloning; `FETCH_RAISE` is set in place only on a real `Manager`'s own fresh QuerySet, every other QuerySet or duck-typed `get_queryset()` result is cloned unless its result cache is filled, even if it already is `FETCH_RAISE`, and the clone and `all()` of a manager wrapper keep the pending sticky filter; `all()` shares only a cache that was filled when the manager was wrapped; copies keep the sticky filter but not the cache), new Validation items; approved by: maintainer (@devgyurak), to be confirmed in the PR #65 review; architect verdict: APPROVE WITH CHANGES (architect review 2026-11-25 of the PR #65 amendment, changes applied)
- Amended: 2026-11-26 (#6, PR #65) - Decisions 2 and 4 (the sync-evaluator list is the full `_SYNC_EVALUATORS` table and `__reduce__` raises `SyncEvaluationError`; wrapper arguments are unwrapped before Django sees them, nested ones are not; a result of a proxied method or operator that is an operand is cloned, and so is every result of a user-defined method, unless its result cache is filled: a result whose cache is filled is shared, as Django returns it, whether or not it is an operand, and the wrapper it gets is ordinary, so its own `all()` re-queries; reflected operators: `QuerySet | aq(...)` returns a plain QuerySet, keep the wrapper on the left), new Validation items; approved by: maintainer (@devgyurak), to be confirmed in the PR #65 review; architect verdict: APPROVE (architect review 2026-11-26 of the final wording at fd6c09b; re-review after the changes requested in the first 2026-11-26 pass)

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
   `iterator()` error says `aiterator()` is not implemented yet and points to `alist()`.* *Amended
   2026-11-26 (#6, PR #65): the sync evaluators are every name of the `_SYNC_EVALUATORS` table:
   the list above plus `last`, `earliest`, `latest`, `contains`, `update_or_create`,
   `bulk_create`, `bulk_update` and `explain`; `pickle` (`__reduce__`) raises
   `SyncEvaluationError` too, because `QuerySet.__getstate__` evaluates. Arguments are unwrapped
   before Django sees them (a wrapper passed to `union()`, `intersection()`, `difference()` or
   any user method reaches Django as its QuerySet), because Django may return an argument itself
   (`none().union(aq_qs)`) and would hand the wrapper back as if it were a QuerySet. Only direct
   arguments are unwrapped: a wrapper nested inside another argument (`Prefetch(queryset=aq(...))`,
   a list, a `Q`) is not, its behaviour is undefined (no error is defined for it), and it is
   unsupported; pass the QuerySet (`.queryset`) there.*
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
   a slice) skip the clone; copies are clones that keep `FETCH_RAISE`. The clone is a chain, which consumes the pending sticky
   filter (`Query.filter_is_sticky`) of a related-manager QuerySet, so it is copied onto the
   clone: `aq(tag.books.all()).filter(tags__name=x)` does not depend on how `tag` was loaded.
   `all()` of a wrapper that came straight from a manager keeps that state, as `Manager.all()`
   (which returns `get_queryset()` unchained) does: its sticky filter, and a prefetched result
   cache, which it returns as it is (`aq(t.books).all().alist()` makes no statement). `all()`
   shares only the cache that was already filled when the manager was wrapped, never one that
   `alist()` filled later. Copies (`copy.copy`) keep the sticky filter but not that cache: they
   are an unevaluated clone, so a copy of a prefetched wrapper queries where its `all()` does
   not. The in-place switch assumes `Manager.get_queryset()` returns a fresh QuerySet, as
   Django's and related managers do; a custom manager returning a shared one that is already
   `FETCH_RAISE` gets a clone.*

   *Amended 2026-11-26 (#6, PR #65): a result of a proxied call that IS the receiver or one of
   the arguments is cloned too (Django returns an operand itself when one side is empty:
   `none() | qs`, `qs ^ none()`, `none().union(qs)`, `qs.intersection(none())`, so
   `aq(none) | user_qs` would otherwise wrap the caller's QuerySet and `alist()` would fill its
   cache), and so is the result of any user-defined chaining method, a stored `FETCH_RAISE`
   QuerySet included: only Django's own methods are known to return a fresh clone (an operand is cloned even when it is `FETCH_RAISE`), and a
   user-defined method (an override of a Django one too) may return a QuerySet it stores. User-
   defined chaining methods must return a new QuerySet. The one exception: a result whose cache
   is filled is shared, whether or not it is an operand (a prefetched related manager, a wrapper
   that `alist()` filled, a stored QuerySet that a user-defined method returns). Django returns
   that same QuerySet (`cached | none() is cached`) and evaluating it makes no statement, so
   `aq(t.books) | aq(none)` and `aq(none) ^ w` after `w.alist()` share it and `alist()` only
   reads the cache; the instances in a shared cache keep the fetch mode they were loaded with,
   as in Django. The result is an ordinary wrapper, not a prefetched one: its own `all()` is a
   new unevaluated QuerySet, as Django's `cached.all()` is, and `aq(t.books).all().all()`
   queries like `t.books.all().all()`. A stored QuerySet with an unfilled cache that a
   user-defined method returns is cloned. `deepcopy` of a wrapper drops the cache, like
   `copy.copy`. Reflected operators: `QuerySet | aq(...)` runs Django's operator, which never
   defers to `__ror__`, so a non-empty left side returns a plain QuerySet that does not get `FETCH_RAISE` from `aq()` (the left side keeps its own fetch mode);
   with an empty left side `&` returns that plain empty QuerySet (`self`), while `|` and `^`
   return the other operand, the wrapper itself. Keep the wrapper on the left of `|`, `&` and
   `^`. A slice with a step is refused whatever the QuerySet holds, although Django evaluates
   only when it must: the code is conservative and the divergence is deliberate.*
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
has a result cache without cloning it (every other QuerySet is cloned, see Decision 4); evaluation methods fill the
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
- *Amended 2026-11-26 (#6, PR #65):* unit (`tests/unit/query/test_chaining.py`): a combination or
  proxied call never wraps a QuerySet one of its operands holds, for `|`, `&`, `^`, `union()`,
  `intersection()` and `difference()` with an empty receiver or argument, the second argument
  included; a combination whose operand has a filled cache shares it, and its own `all()`, like
  `aq(t.books).all().all()`, is a new unevaluated QuerySet; a user-defined method returning its
  receiver, an argument, the second argument or an unfilled stored QuerySet (`FETCH_RAISE` or
  another mode) is wrapped as a clone, and one with a filled cache is shared (no statement). Integration: `alist()` of a combination leaves the
  QuerySets the caller and the operand wrappers hold unevaluated, a combination with an
  `alist()`-evaluated operand runs the statements the sync ORM runs (0 for each operator and for
  `none().union(u)`), `aq(manager).same().all().alist()` over a prefetched user-method result runs
  the one statement Django's does, and `aq(author.books).all()
  .all().alist()` after `prefetch_related` runs the one statement Django's does.
- *Amended 2026-11-26 (#6, PR #65):* unit: `QuerySet | aq(...)`, `&` and `^` with a non-empty left
  side return a plain QuerySet that is not in `FETCH_RAISE`; with an empty left side `&` returns
  a plain QuerySet and `|` and `^` return the wrapper. Nested wrappers in arguments stay an
  undocumented-behaviour limit and are not pinned by a test.
