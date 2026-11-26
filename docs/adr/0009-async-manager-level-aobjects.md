# ADR-0009: `aobjects` is a manager-level object - async manager methods and a manager proxy

- Status: Accepted
- Date: 2026-11-26
- Deciders: maintainer (@devgyurak) - options B + C and the return type chosen 2026-11-26 on #69;
  lookup order changed (evaluators overridable) in the PR #73 review; accepted by: maintainer
  (@devgyurak), 2026-11-26 in the PR #73 review
- Architect review: APPROVE WITH CHANGES (architect review 2026-11-26, 12 required changes
  applied; re-review 2026-11-26: APPROVE WITH CHANGES, 4 remaining changes applied; final check
  2026-11-26 at 7eac090: APPROVE; PR #73 review changes re-reviewed 2026-11-26: APPROVE WITH
  CHANGES, 1 change applied; final: APPROVE)
- Supersedes in part: [ADR-0006](0006-public-api-attachment.md) Decision 3 (the `aobjects` shape),
  the first half of its Consequences bullet on manager-only methods and its last Validation item
  (the `AsyncQuerySet` return type); ADR-0006 Decisions 1, 2, 4, 5 and 6 stay in force

## Context
ADR-0006 Decision 3 made `aobjects` a plain descriptor whose value is
`aq(owner._default_manager.get_queryset())`, an `AsyncQuerySet`. It is implemented
(`src/asyncopation/query/objects.py`, #6, PRs #65 / #67 / #68) and its Consequences accept two gaps:

- **Manager-only methods are unreachable.** A method defined on a `Manager` subclass rather than on
  its QuerySet (`UserManager.create_user`, a project's `def published(self)` on its manager) raises
  `AttributeError` on `Model.aobjects`, and there is no place to write an async version of one.
  Projects that keep domain logic on managers have no async path for it.
- **User QuerySet methods that evaluate internally** (`return self.first().title`) raise.

The fix changes the public type of `Model.aobjects`, so a Validation item of ADR-0006 (the
`AsyncQuerySet` return type, pinned by
`test_aobjects_of_a_swapped_out_model_still_returns_an_async_queryset`) stops holding. The
`docs-and-adr` rule forbids amending an ADR in place when a Validation item becomes invalid, hence a
new ADR. Nothing has been released (pre-alpha), so the break hits only the accepted ADR, the #6 code
and its tests.

Django facts (6.1.3; paths below `django/`):

- `BaseManager._get_queryset_methods` (`db/models/manager.py:82-105`) copies every public QuerySet
  function that the manager class does not already have (`hasattr(cls, name)`), honouring
  `queryset_only` (`as_manager`, `delete`, `adelete`, `resolve_expression` are not copied;
  `_insert` / `_update` are). Each copy is `manager_method(self, *a, **kw): return
  getattr(self.get_queryset(), name)(*a, **kw)` decorated with `functools.wraps`, and lives in the
  `__dict__` of the class `from_queryset` generates (`manager.py:107-118`); `Manager` itself is
  `BaseManager.from_queryset(QuerySet)` (`manager.py:176`). `__qualname__`, `__wrapped__` and
  function identity cannot tell a copy from a manager's own method (`wraps` rewrites the first,
  `@wraps(QuerySet.filter)` on a user function forges the second, every `from_queryset` call makes
  new functions); **every copy shares one code object** (`co_qualname`
  `BaseManager._get_queryset_methods.<locals>.create_method.<locals>.manager_method`). An alias
  (`published = Manager.filter` on a user manager) shares it too, but keeps `__name__ == "filter"`.
- `BaseManager.all()` returns `get_queryset()` unchained (`manager.py:157-164`), so an `all()`
  override is followed by `Model.objects.all()` but not by `Model.objects.filter()`, which goes
  through `get_queryset()`. `db_manager(using, hints)` returns `copy.copy(self)` with `_db` /
  `_hints` replaced (`manager.py:136-140`). `_default_manager` is a per-class copy whose `model` is
  that class (`options.py:453-475`, `506-529`): proxy models, multi-table children and children of
  abstract models each get their own. A `Manager` can be copied, deep-copied and pickled; slicing,
  `|` and `iter()` raise `TypeError`, `.queryset` and `__aiter__` are `AttributeError`.
- `BaseManager` / `Manager` define no coroutine. The `aget`, `acount`, ... copies are plain `def`s
  that return Django's `sync_to_async` coroutine, so `inspect.iscoroutinefunction` is false for
  them. Coroutines a manager class defines itself are detected: `BaseUserManager.aget_by_natural_key`
  (`contrib/auth/base_user.py:39`), `GroupManager.aget_by_natural_key` (`contrib/auth/models.py:103`),
  `UserManager.acreate_user` / `acreate_superuser` (`contrib/auth/models.py:184`, `204`).
  `acreate_user` -> `_acreate_user` (171) -> `await user.asave(using=self._db)` (174) ->
  `Model.asave` -> `sync_to_async(self.save)` (`db/models/base.py:913-921`): a thread hop. Related
  managers' `aadd` / `aremove` / `aclear` / `aset` / `acreate` / `aget_or_create` /
  `aupdate_or_create` (`db/models/fields/related_descriptors.py`, `contrib/contenttypes/fields.py`)
  are `sync_to_async` too. asgiref's `SyncToAsync` instances and `markcoroutinefunction` functions
  are detected by `inspect.iscoroutinefunction` (unwrapping them would hide that). No QuerySet class
  Django ships defines a coroutine of its own beyond `QuerySet`'s, which the blocking tables cover.
- A user QuerySet method that evaluates (`self.first()`, `list(self.values_list(...))`), called
  through a wrapper inside a running event loop, raises
  `django.core.exceptions.SynchronousOnlyOperation` ("You cannot call this from an async context -
  use a thread or sync_to_async.") from `async_unsafe` (`utils/asyncio.py:24`) when the compiler asks
  for a cursor; `FETCH_RAISE` plays no part (it covers attribute fetches on instances only). Sync
  manager methods that query (`create_user`, `get_by_natural_key`, a cold
  `ContentTypeManager.get_for_model`) raise the same way.

## Options
### A - Keep ADR-0006 Decision 3 as it is
Document the gap; manager-only logic stays sync-only or moves to the QuerySet.
+ No break, no new code. - Projects with manager logic (every `auth.User` project) get no async
path; `Model.aobjects.published()` stays an `AttributeError` although `Model.objects.published()`
works.

### B - Extensible descriptor: async manager methods on an `AsyncObjects` subclass
Users subclass `AsyncObjects` and write `async def` methods that reach the QuerySet API through
`self`; `Model.aobjects` returns a manager-level object carrying those methods plus the QuerySet API.
+ A place for async manager logic that does I/O only through Asyncopation's evaluators (no thread
hop); still not a Django `Manager`, so `_meta.managers` is untouched. - Alone, the manager's own sync
chaining methods (`published()`) stay unreachable and have to be rewritten.

### C - Manager proxy
`aobjects` wraps the model's default manager instead of its QuerySet; manager attributes are proxied
and a returned QuerySet is re-wrapped.
+ Manager-only chaining methods work unchanged; `all()` overrides behave as in Django. - Manager
methods that do sync I/O still raise, so alone it gives no async path for them; a proxied Django
coroutine would be a thread hop, so coroutines have to be refused.

### D - A real `AsyncManager(Manager)` subclass
Rejected in ADR-0006 and re-checked here: with `contribute_to_class` it lands in `_meta.managers`, can
become `_default_manager` and suppresses the implicit `objects`; inheriting Django's manager brings
every copied `a*` method (`sync_to_async`) back, each to be overridden or blocked one by one; a
generated class per user manager brings ADR-0006 option A's costs (MRO, pickling, one class per user
class). The new requirement (async manager methods) does not change any of these reasons.
+ `isinstance(x, Manager)`. - All of the above.

## Decision
**B + C together** (maintainer decision on #69, 2026-11-26). A and D are rejected.

1. **What `Model.aobjects` is.** The value is a bound instance of the declared `AsyncObjects`
   (sub)class: `__get__(self, instance: None, owner) -> Self` (with a `typing.overload` typing
   instance access as `NoReturn`, since it raises). Every access builds a new object (no cache),
   bound to `owner` and to `owner._default_manager` for the class it is accessed on
   (abstract-model children, multi-table children and proxy models each bind their own class and
   manager, as today). It holds the manager and builds an `AsyncQuerySet` when it needs one; it does
   **not** subclass `AsyncQuerySet`, it is not a Django `Manager` and it has no
   `contribute_to_class`, so `_meta.managers`, `_default_manager` and the implicit `objects` are
   untouched (ADR-0006's reasons hold). There is no separate `AsyncManager` class: the syntax stays
   `aobjects = AsyncObjects()` and `AsyncObjects.attach(Model)` (which already instantiates `cls()`, so
   a subclass works and must be constructible without arguments). Instance access, an abstract model
   and a missing owner raise `AttributeError` as today; a swapped-out model is not refused (ADR-0006
   Decision 3's 2026-11-26 note stands).
   `isinstance(Model.aobjects, AsyncObjects)` is true; `isinstance(..., AsyncQuerySet)` and
   `isinstance(..., Manager)` are false. Typing: `__get__` returns `Self`, so a type checker sees the
   declared subclass and its methods; proxied names come from `__getattr__` and are `Any`.

   **Construction.** The declared object (`vars(Model)["aobjects"]`) is built once, at class
   definition or by `attach()`; a subclass's `__init__` runs then and never per access. A bound
   object is made by a private `_bind(model, manager)` that copies the declared object's instance
   state (`__dict__` / slots) shallowly and sets the binding, so state a subclass sets in `__init__`
   is carried over and binding never runs `__copy__`; a `db_manager()` re-bind (Decision 4) is a
   `_bind` of the bound object with the new manager. The declared object itself is unbound: its evaluators and
   every proxied name raise `AttributeError` ("... is not bound to a model; read it from the model
   class") without recursing; its `__getattr__` refuses dunder names and the binding slots the same
   way `AsyncQuerySet.__getattr__` does, so protocol lookups (`copy`, `pickle`) never reach the
   manager. `contribute_to_class` is never proxied either (bound or not, it raises
   `AttributeError`). Django calls it on any value that has it (`_has_contribute_to_class`,
   `db/models/base.py:90-92`, used at line 119 for a class body and line 393 by `add_to_class`),
   and a proxied call would run `Other._default_manager.contribute_to_class(NewModel, name)`, which
   sets that shared manager's `model` to `NewModel` and adds it to `NewModel._meta.managers`
   (checked on Django 6.1.3). The two cases differ:
   - `NewModel.add_to_class("x", Other.aobjects)` on an existing model: `Other`'s default manager
     goes over to `NewModel` (`Other.objects.model is NewModel`, so `Other.objects` queries the
     other table) until the `Options` caches are next cleared.
   - `aobjects = Other.aobjects` in a model body: registering the new model clears the app
     registry caches, so `Other` gets fresh manager copies and its own model back, but
     `NewModel._meta.managers` now holds the captured manager, so Django does not add the implicit
     `objects` and `NewModel.objects` does not exist.
2. **Async manager methods (B).** A subclass adds `async def` methods. They do I/O only through
   Asyncopation's evaluators reached from `self`, for example `await self.filter(...).aget(...)`, and
   read the bound model as `self.model` (the accessed class: a child or proxy model binds its own).
   Nothing in `src/` hops threads to run them (non-negotiable #1). They run in the caller's task, so
   inside `async_atomic` they use its pinned connection (ADR-0001).

   ```python
   class UserAsyncObjects(AsyncObjects):
       async def aactive_by_email(self, email):
           return await self.filter(is_active=True).aget(email=email)


   class User(Model):
       aobjects = UserAsyncObjects()  # a third-party model: UserAsyncObjects.attach(User)
   ```
3. **Lookup order.** A name on the manager-level object resolves, first match wins. Steps 1-3 are
   ordinary attribute lookup on the class (its MRO); steps 4 and 5 are `__getattr__`.
   *Changed 2026-11-26 by the maintainer in the PR #73 review: the fixed order put the evaluators
   before the subclass's methods; evaluators are now overridable through the normal MRO, and only
   `model` and `name` are protected.*
   1. **`model` and `name`**: `model` is the bound model and `name` the attribute name the
      descriptor was declared under (`Model.aobjects.name` is `"aobjects"` where
      `Model.objects.name` is `"objects"`, a new recorded divergence: under ADR-0006
      `Model.aobjects.name` was proxied to the QuerySet, which has no `name`, and raised
      `AttributeError`). These cannot be shadowed: a subclass whose MRO resolves either name to
      anything but `AsyncObjects`' own (its own definition or a mixin's) raises `TypeError` when the
      class is created (`__init_subclass__`), with a message that suggests another name.
   2. **The subclass's own attributes** (B), including names that also exist on `AsyncObjects`
      (an evaluator override, which reaches the default through `super().aget(...)`, as a Django
      `Manager` subclass overrides `create`), on the manager or in the blocking tables. `attach`,
      `__init__`, `__get__` and `__set_name__` are ordinary class plumbing a subclass may extend.
      `super()` never calls `__getattr__`, so it reaches only names `AsyncObjects` defines (the
      evaluators and the class plumbing): `super().aget(...)` works, while an override of a proxied
      name reaches the default with `super().__getattr__("published")(...)` (or, for a copied
      QuerySet method, `aq(self).filter(...)`), and a name that is in neither place, such as
      `super().acreate(...)` before #20 adds that evaluator, raises a plain `AttributeError`, not
      the blocking table's error. Overriding a manager-level evaluator does not affect chains:
      `Model.aobjects.filter(...).aget()` and `aq(Model.aobjects).aget()` bypass an `aget` override,
      as overriding `Manager.get` does not change `Model.objects.filter().get()`; an override used
      for access control has to cover the chaining methods too.
   3. **`AsyncObjects`' evaluators**: `alist`, `aget`, `afirst`, `acount`, `aexists` (one table in
      `query/objects.py` is the single source of these names; later evaluators join it as they
      land), each the same call on a fresh `aq()` of the bound manager.
   4. **The blocking tables** of `asyncopation.query.queryset`, unchanged and still single-sourced
      there: a `_SYNC_EVALUATORS` name (`get`, `count`, `iterator`, ...) raises
      `SyncEvaluationError`, a `_DJANGO_COROUTINES` name (`acreate`, `aiterator`, ...) and `raw`
      raise `NotImplementedError`, exactly as on an `AsyncQuerySet`, even when the manager overrides
      that name.
   5. **The bound default manager's attribute** (C), with the result rules of Decision 4; dunder
      names, the binding slots and `contribute_to_class` are never proxied (Decision 1).
4. **Results of the proxy.**
   - **A QuerySet method Django copied onto the manager** is not called on the manager: the call
     goes to the QuerySet of `aq(manager)` directly. A name is such a copy when it is not in the
     manager instance's `__dict__` and the first class in `type(manager).__mro__` whose `__dict__`
     has it holds a function `fn` with `fn.__code__ is` the shared `manager_method` code object
     **and** `fn.__name__ == name` (so an alias such as `published = Manager.filter` is not a copy);
     every other case, a decorated copy included, takes the manager path below, which is always
     correct. `manager_method` is exactly `getattr(self.get_queryset(), name)(...)`, so the result
     is the same, and `aq(manager)` switches the manager's fresh `get_queryset()` result to
     `FETCH_RAISE` in place (ADR-0006 Decision 4), so `Model.aobjects.filter(...)` builds one clone,
     not the two of today's `aq(get_queryset())` path, and none more than Django's
     `Model.objects.filter(...)`. For every copied name `Model.aobjects.<name>` therefore behaves as
     `aq(Model._default_manager).<name>` (a user QuerySet method included, chaining or not). This
     means the default-manager path no longer clones what `get_queryset()` returns: ADR-0006
     Decision 4's assumption that a real `Manager.get_queryset()` returns a fresh QuerySet (a
     shared one that is not `FETCH_RAISE` gets `FETCH_RAISE` set on it in place) now applies to
     `aobjects` too, as it already does to `aq(Model.objects)`.
   - **A name the manager defines itself** (`all`, `get_queryset`, `db_manager`, a custom
     manager's `published`, an override of a copied name such as `filter`, an alias of a copy, an
     attribute in the manager instance's `__dict__`) is taken from the manager. If it is a
     coroutine function or an async generator function (`inspect.iscoroutinefunction` /
     `inspect.isasyncgenfunction` on the attribute as resolved, not unwrapped, so asgiref's
     `SyncToAsync` and `markcoroutinefunction` are caught), it raises `NotImplementedError` and is
     never called: Django's own (`acreate_user`, `aget_by_natural_key`) end in `sync_to_async`.
     Otherwise it is proxied, and its result is handled as follows:
     - an awaitable (a sync method returning `self.filter(...).acount()`, Django's `sync_to_async`
       coroutine) is closed when it has `close()` (it has not started, so no thread has) and
       `NotImplementedError` is raised;
     - an async generator (`inspect.isasyncgen`; a sync method returning
       `self.get_queryset().aiterator()`, Django's `sync_to_async` iteration) is not an awaitable
       but is refused the same way: `NotImplementedError` is raised without iterating it (it has
       not started, so no thread has);
     - a `QuerySet` is wrapped in an `AsyncQuerySet` with ADR-0006 Decision 4's rules: the fresh
       result of Django's own `BaseManager.all()` / `get_queryset()` (whatever `get_queryset`
       override `all()` calls) is switched to `FETCH_RAISE` in place; the result of a user-defined
       method, an `all()` override included, is a clone unless its result cache is filled;
     - a `Manager` (`db_manager()`) is re-bound: a copy of the manager-level object (Decision 1)
       bound to that manager and its model, so `Model.aobjects.db_manager("other")` keeps the
       subclass methods;
     - anything else (`db`, `deconstruct()`, `check()`, a `RawQuerySet` a user method returns) is
       returned as it is.
   No sync `Manager`, unwrapped `QuerySet` or Django coroutine leaks through the proxy. Routing
   decisions may be cached per manager class and name, keyed weakly on the class
   (`from_queryset` creates classes at runtime); patching a manager class after its first access
   through `aobjects` is not supported.
5. **Manager parity.** The manager-level object supports what a Django `Manager` supports, by
   Django's own rule: `_get_queryset_methods` copies public named methods and skips `_`-prefixed
   and dunder names, so the named evaluators are there and the QuerySet protocols are not.
   - `Model.aobjects.all()` follows the default manager's `all()` override, as
     `Model.objects.all()` does; `Model.aobjects.filter()` does not, as `Model.objects.filter()`
     does not.
   - Slicing, `|` / `&` / `^`, `iter()` and `async for` raise `TypeError` and `.queryset` raises
     `AttributeError`, as on a `Manager`: these are QuerySet operations, used after `.all()`
     (`Model.aobjects.all()[:20]`, `Model.aobjects.all().queryset`). No raising `__aiter__` is
     defined, so `hasattr(Model.aobjects, "__aiter__")` stays false.
   - `copy.copy`, `copy.deepcopy` and `pickle` of the manager-level object itself work as they do
     on a `Manager`: the result is another manager-level object of the same class bound to the same
     model; a shallow copy shares the bound manager, a deep copy and an unpickled object hold a
     copy of it, and none of them touches the database. A copy of a QuerySet still needs `.all()`
     first. The 2026-11-26 decision on #69 listed `copy` among the operations that need `.all()`;
     a `Manager` can be copied, so copying the object itself follows `Manager` parity and only a
     QuerySet copy needs `.all()` (a clarification of the fixed decision, confirmed by the
     maintainer (@devgyurak), 2026-11-26 in the PR #73 review).
   - `alist()` is a public named evaluator like Django's manager-level `aget()` / `acount()`, so it
     stays on the manager level.
   - The manager-level evaluators never cache: each call builds a fresh `aq(manager)`, so
     `m = Model.aobjects; await m.alist(); await m.alist()` runs two statements, as Django's
     manager does (today's `AsyncQuerySet` value runs one), and concurrent evaluators on one bound
     object (`gather`) share no QuerySet.
   - `repr()` never touches the database.
6. **Django internals.** The copy test of Decision 4 reads `BaseManager._get_queryset_methods`'
   private `manager_method` code object (taken once from `Manager.filter.__code__`). It lives in
   `asyncopation._django` (layer 0) with `docs/django-api-ledger.toml` rows for it, for
   `_default_manager` (already read by `query/objects.py` and not yet in the ledger) and for any
   other manager attribute the implementation reads. A drift test in `tests/unit/django_drift`
   pins: two copies share one code object, its `co_qualname`, `Manager.filter` is a copy, `all`,
   `get_queryset` and `db_manager` are defined on `BaseManager`, and the copy dispatches through
   `getattr(self.get_queryset(), name)`. If Django changes this, the fallback is to send every name
   to the manager (correct, one clone more). No new Django sync-machinery exception is needed, and
   guards A101 / A102 are unchanged.
7. **`aq()`.** `aq(Model.aobjects)` (any bound manager-level object, a re-bound `db_manager()` one
   included) is `aq()` of its bound manager, taking the real-`Manager` path: a fresh `AsyncQuerySet`
   with the manager's alias. `aq()` recognises the object by a marker or protocol defined in
   `query/queryset.py` before its duck-typed `get_queryset()` branch, because `query/objects.py`
   imports `query/queryset.py`, not the other way round, and the proxied `get_queryset()` returns an
   `AsyncQuerySet`. `aq(related_manager)` and every other `aq()` contract of ADR-0006 are unchanged.
8. **User QuerySet methods that evaluate internally** stay a documented limitation: they raise
   `SynchronousOnlyOperation` (from Django's `async_unsafe`, not from `FETCH_RAISE`), through
   `aobjects` as through `aq()`; so do sync manager methods that query (`create_user`,
   `get_by_natural_key`). The async path for such logic is a B method that uses the evaluators. Only
   the error is pinned by tests; Asyncopation does not try to detect or rewrite such methods.

### Answers to the open questions of #69
1. *Which option, what does `Model.aobjects` return?* B + C; a bound instance of the declared
   `AsyncObjects` (sub)class (Decision 1), neither an `AsyncQuerySet` nor a `Manager`; typed as
   `Self`.
2. *How are async manager methods declared and how do they reach the QuerySet without a thread
   hop?* `async def` on an `AsyncObjects` subclass; `self.<queryset method>(...)` returns an
   `AsyncQuerySet` and its evaluators do native async I/O (Decisions 2 and 3).
3. *Third-party / swappable models?* `UserAsyncObjects.attach(User)`: `attach` instantiates the
   subclass it is called on; a swapped-out model is still not refused (Decision 1).
4. *User QuerySet methods that evaluate internally?* A documented limitation; the
   `SynchronousOnlyOperation` is pinned by a test (Decision 8).
5. *Sync evaluators and unimplemented `a*` names?* Unchanged: the blocking tables come before the
   manager in the lookup order and raise `SyncEvaluationError` / `NotImplementedError`; coroutines
   and async generators the manager defines, and awaitables and async generators its sync methods
   return, are refused too (Decisions 3 and 4).
6. *Impact on #6 and #18?* #6 is closed; its code and the tests that pin the `AsyncQuerySet` value
   change in the implementation issue (see Consequences). `Book.aobjects.aget(...)`,
   `.filter(...).acount()`, `.all()[:n].alist()`, `.order_by(...).afirst()` in the harness, bench and
   README keep working. #18 (`aq()` for related managers) is unaffected: `aq(related_manager)` keeps
   its ADR-0006 contract, and related managers' own methods (`add`, `aadd`, ...) stay out of reach
   of `aq()`; if #18 ever adds a manager-level related object, it follows this lookup order.

## Consequences
+ Manager-only chaining methods (`Model.aobjects.published()`) and `all()` overrides behave as on
  `Model.objects`; domain logic gets an async home without a thread hop.
+ `Model.aobjects.filter(...)` builds one QuerySet clone instead of two.
- **Breaking** (pre-alpha, unreleased): `Model.aobjects` is no longer an `AsyncQuerySet`.
  `Model.aobjects.queryset`, `Model.aobjects[:n]`, `Model.aobjects | other` and
  `async for x in Model.aobjects` stop working (write `Model.aobjects.all()...`), `copy.copy` of it
  is a manager-level object, not a QuerySet copy, and two evaluations on one bound object run two
  statements. The unit tests in `tests/unit/query/test_objects.py` that read `.queryset` of
  `Model.aobjects` or assert an `AsyncQuerySet` (directly or through `where_conditions` /
  `fetch_mode_of` in `tests/support/object_models.py`) change, among them
  `test_aobjects_is_an_async_queryset_of_the_model`,
  `test_aobjects_gives_a_fresh_queryset_on_every_access`,
  `test_aobjects_carries_fetch_raise_so_attribute_access_never_does_io`,
  `test_aobjects_has_not_run_a_query`,
  `test_aobjects_applies_the_filter_of_a_custom_default_manager`,
  `test_aobjects_adds_no_filter_of_a_default_manager_that_overrides_all`,
  `test_aobjects_uses_the_manager_named_by_default_manager_name`,
  `test_attach_adds_aobjects_to_a_model_that_cannot_declare_it`, `test_attach_applies_fetch_raise`,
  `test_attach_over_an_existing_attribute_replaces_it` and
  `test_aobjects_of_a_swapped_out_model_still_returns_an_async_queryset`. The documents that list
  `async for` on `aobjects` (README status line, `docs/architecture.md` status line, `CHANGELOG.md`,
  the Phase 1 row of the `plan.md` roadmap) change with the implementation.
- **Overriding evaluators**: a subclass may override an evaluator (`aget`, later `acreate`) and
  call the default with `super()`. When a new evaluator lands (`alast`, `acreate` with #20), a
  subclass that already defines that name keeps working and its method keeps winning (the normal
  MRO), so it silently replaces the new default; the CHANGELOG names every evaluator added to the
  table so such subclasses can check their semantics. Only `model` and `name` fail at class
  creation.
- A dependency on a private Django detail (the shared `manager_method` code object); the drift test
  and the ledger catch a Django change, and the fallback if it breaks is to send every name to the
  manager (correct, one clone more).
- Two places resolve names (`AsyncQuerySet.__getattr__` and the manager-level object); the blocking
  tables stay single-sourced in `query/queryset.py`.
- Asymmetry to know: a coroutine the **manager** defines is refused, while a user **QuerySet**
  coroutine copied by `from_queryset` keeps `aq()`'s existing behaviour (it is called and its
  coroutine returned), because copied names behave as `aq(manager).<name>`. The refusal covers
  coroutines Django's managers define and anything a manager defines; a coroutine handed out by
  user QuerySet code stays the user's choice, as with `aq()` today, even when it is a Django one
  (a sync QuerySet method returning `self.filter(...).acount()` reaches Django's `sync_to_async`
  through `aq()` and through `aobjects`, while the same method on the manager is refused).
  Refusing it on the QuerySet path would also refuse native user coroutines, which ADR-0006 calls.
  Non-negotiable #1 governs `src/`; no QuerySet class Django ships defines a coroutine beyond
  `QuerySet`'s own, which the blocking tables cover.
- Writes stay Phase 5 (#20). Until then an async `create_user` cannot be written; after #20 it is a
  B method such as `async def acreate_user(self, username, password, **extra): return await
  self.acreate(username=username, password=make_password(password), **extra)`. `make_password`
  hashes (PBKDF2 by default) on the event loop and blocks it for the hash's CPU time, as Django's
  `acreate_user` does before its thread hop; a project that cannot afford that hashes elsewhere.

## Validation
Implemented in the follow-up issue to #69 (PR 2); every item below is a test there.

- unit (`tests/unit/query/test_objects.py`): `Model.aobjects` is an instance of the declared
  `AsyncObjects` (sub)class, bound to the accessed model, new on every access, not an
  `AsyncQuerySet` and not a `Manager`; declaring it still adds nothing to `_meta.managers`;
  `Model.aobjects.name` is the declared attribute name; the unbound declared object
  (`vars(Model)["aobjects"]`) raises `AttributeError` for an evaluator and a proxied name;
  `hasattr(Model.aobjects, "contribute_to_class")` is false; adding `Model.aobjects` to another
  model, in a class body and with `add_to_class`, leaves `Model._default_manager` and its `model`
  untouched and leaves the receiving model's `_meta.managers` and its `objects` as they were (the
  implicit `objects` of a class-body model still exists).
- unit: **replaces ADR-0006's Validation item pinned by
  `test_aobjects_of_a_swapped_out_model_still_returns_an_async_queryset`**: a swapped-out model's
  `aobjects` returns a bound `AsyncObjects` (`no_sql`), while its `objects` raises as Django's does.
- unit: a manager-only chaining method (`Model.aobjects.published()`) returns an `AsyncQuerySet`
  with the manager's filter; a copied QuerySet method returns an `AsyncQuerySet` in `FETCH_RAISE`;
  `Model.aobjects.all()` applies an `all()` override, `Model.aobjects.filter()` does not (as
  Django).
- unit (routing): an alias of a copy (`published = Manager.filter`), a manager-defined `filter`
  override and a decorated copy take the manager path; a chained `from_queryset` (QS1, then QS2)
  routes its copies to `aq()`; a manager method returning a non-QuerySet (a `RawQuerySet`) returns
  it unchanged.
- unit: a subclass's `async def` sees `self.model` as the accessed class (inherited by a
  multi-table child, a proxy model and a concrete child of an abstract model);
  `UserAsyncObjects.attach(Model)` binds the subclass; a subclass or a mixin defining `model` or
  `name` raises `TypeError`; a subclass overriding `aget` has its method run and reaches the
  default through `super().aget()` (same result as the sync ORM), an override of `published`
  reaches the manager's through `super().__getattr__("published")()`, and `super().acreate()`
  raises `AttributeError` (until #20); a subclass defining a manager
  name or a blocking-table name has its own method run (step 2 before steps 4 and 5).
- unit: `Model.aobjects.db_manager("other")` is an instance of the same subclass whose QuerySets use
  `"other"`; `aq(Model.aobjects)` and `aq(Model.aobjects.db_manager("other"))` are fresh
  `AsyncQuerySet`s with the bound manager's alias.
- unit: a coroutine or async generator defined on the manager (a native `async def`, a
  `sync_to_async` attribute, `UserManager.acreate_user`), a sync manager method that returns a
  coroutine and one that returns an async generator (`self.get_queryset().aiterator()`) raise
  `NotImplementedError` without a thread hop (no thread started, no `sync_to_async`
  call); sync evaluators raise `SyncEvaluationError` and unimplemented `a*` names
  `NotImplementedError` as on `aq()`, also when the manager overrides them.
- unit: slicing, `|`, `iter()`, `.queryset` and `async for` on the manager-level object raise as on
  a `Manager`; `copy.copy`, `copy.deepcopy` and `pickle` round-trip to a manager-level object bound
  to the same model, with no statement.
- unit: two `alist()` calls on one bound object run two statements, and `gather` of two evaluators
  on one bound object shares no QuerySet.
- unit: a user QuerySet method that evaluates internally and a sync manager method that queries
  (`get_by_natural_key`) raise `SynchronousOnlyOperation`, with no statement run and no thread
  started.
- differential (`tests/integration/test_read_api_differential.py`): a manager-only chaining method
  and an `all()` override chained through `aobjects` read the same rows as the sync ORM (on
  PostgreSQL now, on MySQL / MariaDB with #7), using a default `Manager` subclass with `published()`
  on one `harness/catalog` model that overrides neither `get_queryset` nor `use_in_migrations` (no
  migration, no effect on the other tests); the existing `aobjects` differential tests
  (`test_aobjects_reads_match_the_sync_manager`,
  `test_a_filtered_aobjects_read_matches_the_sync_manager`) pass unchanged.
- drift (`tests/unit/django_drift`): the items of Decision 6.
- e2e: the existing `/aopt` scenarios pass unchanged.
- bench: `make bench-micro` on the `asyncopation.*` cases that use `aobjects` shows the expected
  gain (one QuerySet clone fewer per read) and no regression.
