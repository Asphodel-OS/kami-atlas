# kami-atlas

The Kamigotchi domain manifest: what every component, system, function and entity type in the Kamigotchi contracts does and means, as YAML that people and agents can read without tracing the Solidity, plus a live config reader and an interactive map (the Kami Domain Atlas).

Lives next to the game checkout: `Asphodel-OS/kami/kami-atlas/` reads `Asphodel-OS/kami/kamigotchi/`. Repository: https://github.com/Asphodel-OS/kami-atlas (private; keep it out of any public mirror, since notes hold design intent).

```
generated/                   rebuilt from the code on every run; never edit
  systems/<Name>.yaml        one per system: summary + its own steps
  functions/<Library>.yaml   every library function a system can reach, each written once
  formulas.yaml              the calc* functions: code, stats, config and bonus inputs, which systems use them
  components.yaml            id, type, which systems write and read it
  entities.yaml              entity types: where created, components set at creation; hashed id namespaces
  domain-model.yaml / .json  31 typed entities: layer, ids, formal relationships, owners, sub-entities
  recognition.json           the same facts arranged for an indexer: ids, classification rules, columns (see Recognition spec)
notes/                       written by people; the generator never touches it
  systems/<Name>.yaml        summary, caller, preconditions, effects, invariants, open questions
  components.yaml            meaning, units, invariants, keyed by component name
  domain-model.yaml          entity descriptions, links the code can't resolve, confirmed facts, open questions
  layout.json                where each entity sits on the overview map; exported from the atlas's Settings
viz/                         the Kami Domain Atlas (interactive map); viz/data/ is written by generate.py
  index.html                 the page: markup only
  atlas.css                  its styles
  js/                        ES modules: main (wires them up), util, state, data, tips, canvas, nav, panels, focus, map, ui
generate.py                  builds generated/ and viz/data/ from the contracts
dev.py                       local dev server for the atlas, with live reload
config.py                    reads live config values from Yominet
grants.py                    asks the chain whether the flagged deploy grants are really missing
recognize.py                 applies the recognition spec to a dump of indexed state and reports what every entity is
.cache/                      compiler output with syntax trees (about 90 MB; safe to delete)
```

About 110k tokens in all (roughly: systems 34k, functions 50k, components 10k, formulas 8k, domain model 6k). Nothing is repeated: a helper used by 30 systems is written out once.

## Reading a system

1. `notes/systems/<Name>.yaml`: what it is for (when someone has written it).
2. `generated/systems/<Name>.yaml`: the summary and the system's own steps.
   - `writes`, `reads`: every component it can touch, through every function it calls
   - `events`: game events it can emit (the names the indexer sees, e.g. `HARVEST_COLLECT`)
   - `stats`, `config`, `bonuses`: the stat names, config keys and bonus types it depends on
   - `steps`: its entry points, in source order; `internal`: the system contract's own helpers
   - `writes_without_access` / `access_not_written`: differences from `deploy.json` `writeAccess`
3. Follow any call into `generated/functions/<Library>.yaml`, and any `calc*` into `formulas.yaml`.

Where notes and code disagree, the code wins: fix the note or add an open question.

## Step notation

- `get X`, `safeGet X`, `has X`: a component read; `set X`, `inc X`, `remove X`: a write
- `LibX.fn` or `LibX.fn("LITERAL")`: a call, with its string arguments (stat names, config keys, event names). Overloaded functions carry their parameter types: `LibKami.verifyState(uint256,string) "RESTING"`
- `revert if <condition>: <message>`, `if … / else`, `unless …`, `for each (…)`: control flow
- function `sig` leaves out the `components` and `world` parameters that nearly every function takes; `writes` on a function lists its callees' writes too, and is shown only when the steps don't already make it obvious

## Regenerate

    python3 generate.py             # recompiles with forge (about 1 minute), then generates
    python3 generate.py --no-build  # reuse .cache as is (about 1 second)
    python3 generate.py --no-build --diff [ref]   # also print every model value this run changed against the committed output (default HEAD)

By default it reads **`origin/main`** of `kami/kamigotchi`, through a private worktree under `.cache/src/main` that it fetches and fast-forwards on every build, so the atlas tracks main regardless of what branch the checkout is on (the checkout's `node_modules` are symlinked in for forge). `--ref <branch-or-tag-or-commit>` tracks something else, and with `--out <dir>` writes the output there instead of `generated/` (the atlas keeps showing main), which is how a recognition spec for an older deployed version is made: `python3 generate.py --ref b621eafda --out /tmp/spec-launch`; `--local` (or `KAMI_CONTRACTS=<path to packages/contracts>`) reads the checkout as is. `dev.py` takes the same two flags. Needs Foundry and Python 3, nothing else. Runs are serialised through a lock in `.cache/`, so a manual run while `dev.py` is regenerating waits instead of interleaving writes.

## Domain model and the atlas

`generated/domain-model.yaml` (and `.json`) is derived from the code: the 31 typed entities with their layer (runtime, registry, or hybrid), id, links, owners and sub-entities. Links the generator resolved by tracing variable names are marked `inferred`. A component chosen by name at runtime (the trait registry's `getIndexComp(comps, "BODY")` returns one of five `Index*` components) counts as a possible write to each candidate, so a kami's trait indexes are among its creation columns. `notes/domain-model.yaml` adds what the code can't say: descriptions, targets for the links the code leaves open, confirmed facts and open questions. The generator folds the notes' link settlements into the generated files, so they say what the atlas shows: a target taken from notes is marked `settled_in_notes` (what the code gave stays in `unresolved_from`), and a pointer the notes say is not a link is dropped and listed under `links_ignored_in_notes`. It reports any entity without a description and any open link not settled in notes.

`viz/` is the Kami Domain Atlas: `index.html` holds the markup, `atlas.css` the styles, and `js/` the code as ES modules. `main.js` loads `viz/data/`, which each generator run rewrites, then installs the modules in dependency order onto one shared context (`state`, `data`, `tips`, `canvas`, `nav`, `panels`, `focus`, `map`, `ui`); each reads what earlier ones defined and adds its own, and the notes are merged in the browser. For local work, run the dev server:

    python3 dev.py     # http://127.0.0.1:8765/index.html  (#KAMI opens one entity, #sys.HarvestStart one system)

It reloads the page whenever `viz/` changes, keeping the current view, and reruns `generate.py --no-build` first when `notes/` or `generate.py` change (about a second). Contract changes still need a full `python3 generate.py`.

The overview opens the same way for everyone: `notes/layout.json` holds each entity's position (the generator copies it to `viz/data/`), and entities it doesn't name settle around the held ones with seeded physics. To change the picture, drag entities where they read best, press **Export layout** in Settings, and commit the download as `notes/layout.json`. First-load defaults show the stored pointers only (keyed ids and passthrough edges are off), with the cursor lens, repulsion and snap on; everything is in Settings, which are saved per browser.

It is deployed on Vercel at **https://atlas.kamigotchi.io** (also https://kami-atlas.vercel.app; project `asphodel/kami-atlas`, root directory `viz/`). Production is public; preview deployments are behind Vercel Authentication. Every push to `main` redeploys, so regenerate, commit `viz/data/` and push to update it.

### What counts as an entity, and who named it

The generator finds five kinds of thing that hold components, and only the first is named by the contracts:

| kind | how the generator finds it | name comes from |
|---|---|---|
| **typed entity** (31) | a library calls `LibEntityType.set(id, "X")` or `checkAndSet` | the contracts (`"X"`) |
| **untyped shape** (3: CONDITION, ROOM_GATE, COMMIT) | a library takes an id from `world.getUniqueEntityId()`, writes components to it, and never sets an EntityType | **`generate.py`** — `SHAPE_NAMES`, after the creating library; `name_from` on the entry says so |
| **singleton** (7) | a `uint256 constant X = uint256(keccak256("name"))` that components are written to | the constant's name, except `SINGLETON_NAMES` (`gacha.id` → GACHA_POOL) |
| **record** (22) | a hashed-id namespace (`keccak256("ns", keys...)`) that components are stored at | the namespace string |
| **anchor key** (26) | a hashed-id namespace nothing is stored at; records point at it (IDAnchor, IDTo, IDFrom) | the namespace string |

An untyped shape reachable from no deployed system is dead code and is left out; `source.left_out` in `domain-model.{json,yaml}` lists what was dropped and why (currently `KILL_BOUNTY_LOG`, from `LibKill._logBounty`). The split between record and anchor key comes from the AST pass in `classify_namespaces`, not from anything the contracts declare, so treat it as the generator's reading. Whether the three untyped shapes should get an EntityType was settled with Acheron on 2026-10-16 (left as is; see `answered` in `notes/domain-model.yaml`).

### How the atlas draws a reference

Colour is the relationship type; a dashed line means the reference is only set under a condition. Two more things sit on the line itself: a small **glyph at the midpoint** when the reference passes through something — a box for a record stored under the holder (ACCOUNT → FACTION through `faction.reputation`), a dashed circle for an anchor key the target groups things under (CONDITION → NODE through `node.requirement`); hover it for the sub-entity — and a **marker at the holder end** for how many holders a target has: a crow's foot for many, a bar for one, a bar with a dot for one per key value. The markers appear once zoomed in (same rule as edge labels); the glyphs always. Edges through records can be hidden in Settings → Display.

The generator finds record references by following pointer fields (`IDType`, `IdSource`, `IDAnchor`, `Index*`) written at a record's hashed id, lifting the value back to the call site where the record was created so it resolves per record rather than per shared helper; `links` on the record entry and `via: {record}` on the holder's edge carry the result. Anchor-key passthroughs are `via: {anchor_keys}`.

**Keyed** references have no pointer field at all: the id is hashed from another entity's key (`registry.scavenge("NODE", nodeIndex)`, `amm.pool(lo, hi)`). The generator resolves each key of an entity's id formula the same way it resolves pointer values: by name (`nodeIndex`), then through callers, then by how the function that received it checks it (a value passed as the `index` of `LibItem.getByIndex`, or into a parameter named `itemIndex`, is an item), through locals assigned from a registry getter (`regID = LibItem.getByIndex(...)` is an item; a getter counts as producing its namespace when every return is the generated id or 0), and through pure helpers that only rearrange their arguments (`(lo, hi) = sortIndices(indexA, indexB)`). Keys that resolve to the same entity become one link (`key:lo,hi`, one POOL per ITEM per ITEM); a key that a stored pointer already covers is not drawn twice. The `derived` line on each link says which of these steps produced it.

### What the contracts do two ways

`notes/domain-model.yaml` keeps three lists beside the descriptions: `inconsistencies`, places where the contracts model the same kind of thing differently (typed and untyped shapes, two id-namespace conventions, `ID` against `Id` pointer names, requirements keyed by index for most registries but by use-case reference for items); `answered`, questions settled with the team, dated, so the reasoning survives; and `open_questions`, empty as of 2026-10-17. Nothing in the first list needs changing, but the atlas and the indexer have to know about each one.

Two things the generator notices about systems: a system whose every entry point reverts before doing anything is marked `disabled` with the message (KamiOnyxRespec, KamiOnyxRename), and shown as such in the atlas; and a system that can reach a component write it is not granted in `deploy.json` gets `writes_without_access` with, for each component, the shortest call path from the entry point (`writes_without_access_paths`). The atlas lists these under the system. Reachability is over-approximate: a write behind a branch the data never takes is listed too, so the path is there to judge it.

## Live config values

`config.py` reads config values from Yominet on demand: read-only `eth_call`, no keys.

    python3 config.py KAMI_LIQ_SALVAGE KAMI_HARV_BOUNTY   # named keys
    python3 config.py --system HarvestLiquidate           # every key a system depends on
    python3 config.py --formula LibKill.calcSalvage       # every key a formula reads
    python3 config.py --all --diff                        # keys whose live value differs from the deploy script
    python3 config.py KAMI_LIQ_SALVAGE --block 30000000   # value at a past block (archival RPC)
    options: --json  --world <addr>  --rpc <url>  (or KAMI_RPC)

It reads the production World from `kamigotchi/packages/client/.env.production`, finds the Value component through the World's component registry, and reads each key at `keccak256("is.config" ++ name)`, as `LibConfig` does. Values are decoded by the getter the code uses for that key (`getArray` → 8 numbers, `getString`, `getBool`, `getAddress`, else a number), and compared with `deployment/world/state/configs/configs.ts`. Array slots are shown raw; what each slot means is in the formula that reads it (`formulas.yaml`).

## How it works and its limits

The contracts are compiled with `--ast`, and calls are resolved by the compiler, so overloads and call order are exact. A component access is any method called on a component fetched by its ID (`getAddrByID` / `getCompByID`). Stat components fetched by name (`"HEALTH"`, …) are resolved where the name is passed as a literal; event names are resolved through the parameters that carry them down to `LibEmitter`.

- Summaries cover every path the code could take, not what a given transaction does.
- `Stat[by name]` / `Component[by id]` mark the two places where the component is only known at run time (EchoKamis, Kami721Metadata).
- `writes_without_access` comes from `LibSetter.update`, which picks what to write from registry data at run time. Whether those paths can fire depends on how items and skills are configured, not on the code.
- ECS framework internals (`src/solecs/`) and interfaces are not in the function table; calls to them are shown by name only.

## Recognition spec

`generated/recognition.json` is the model rearranged for a consumer that sees ECS events and has to decide what they mean, written for Kamigaze's domain-aware tables. It is a pure function of the contracts like everything else in `generated/`. Sections:

- `components` and `systems`, keyed by name, each with its on-chain id string (`component.index.kami`, `system.harvest.start`), so an indexer can map the ids in its own tables to names, and each component's value type.
- `entities`: for each entity type, the EntityType value that arrives at creation (`null` for the three untyped shapes), the components written at creation plus `components_later`, the ones any reachable function writes on the type afterwards (an account's name, a listing's price terms), each with where it is set in `components_later_in`; together they are the column list. Then the id formula and index component, the creating systems and library functions, `pointers` (component → target types, relation, required, how it was resolved), `keyed_by` (targets named by the id's own keys, like a pool's two items), and `records` (the sub-entities keyed under it, with their fields and pointers). Untyped shapes carry a `recognize` block: no EntityType ever arrives, so they are classified by the creating system and the set of components written together.
- `shared_records`: records held by more than one entity type (flags, logs), with `held_by`.
- `framework_components`: the ECS framework's own component and system registries, which the World writes like any component; an indexer should skip them.
- `singletons`, `id_namespaces` (which entity type each hashed namespace belongs to), and `left_out`.

`recognize.py` is the reference consumer: given a dump of ECS state (and optionally the transactions that wrote each entity, and each entity's creating system) it applies the rules below and reports what it could and could not place. It was built against the first 28k blocks after the World's deployment, indexed locally with Kamigaze. Records are found by recomputing their ids, never by their components: the spec carries each namespace's `encoding`, `key_order`, `key_types` and, for string keys, `key_values` (the literals the code passes), so `has.flag(id, "NOT_NAMEABLE")` or `listing.buy(regID)` can be hashed from a candidate parent and compared. Candidate parents come from the same transaction (what it wrote, what those point at, the singletons), which is how a live indexer sees them; a removed value still counts as context, since a droptable commit is deleted in the reveal that writes the log keyed by it. With a fast keccak available it also brute-forces records with no context, the ones a snapshot system imported from an earlier world.

How a consumer would use it, per transaction: the system id names the system; an EntityType write classifies the entity; a write of a known entity's component lands in that type's column; a write set matching an untyped shape's `signature` from one of its `created_by` systems creates that shape; a record is recognised by its namespace and keys. The spec says what the code does; it does not say what the chain has done, so an indexer still has to handle entities created before it started reading (bootstrap from current state).

## Reproducibility

Everything in `generated/` and `viz/data/` is a pure function of the contracts checkout: `rm -rf .cache && python3 generate.py` rebuilds it byte for byte (checked: a fresh compile and analysis reproduced all 177 files with no difference). Each output records the ref and commit it came from (`kamigotchi/packages/contracts (origin/main) at <sha>`; a `--local` build records the branch instead, and `-dirty` if that checkout had local changes), so any output can be traced to its source.

It depends on:
- **the contracts commit:** by default the tip of `origin/main` at build time; to reproduce an old output, `--ref <sha-or-tag>` from the header (or check it out and build `--local`);
- **the compiler:** solc is pinned by the contracts' `foundry.toml` (0.8.28); Foundry itself is not pinned, and a release that changes the AST format could change the output;
- **the rules in `generate.py`:** the few hand-written tables (stat names, untyped-shape names, singleton names, admin modifiers) are versioned with the script.

Only `notes/` is written by hand. CI (`.github/workflows/check.yml`) regenerates against kamigotchi's `main` on every push and pull request, and on Monday mornings, and fails if `generated/` or `viz/data/` would change: either a generator change was committed without its output, or kamigotchi moved and the atlas needs a `python3 generate.py`. It reads the public mirror `Asphodel-OS/kamigotchi-open`, which carries the same commits as the private main, so it needs no secret. The atlas's commit links point at the mirror for the same reason. The same check by hand:

    rm -rf .cache && python3 generate.py && git diff --exit-code generated viz/data

## History

The first commits were made after the work, grouped by concern (generator, generated output, notes, config reader, atlas) in the order they were built, not as a step-by-step record. Later commits are real history.
