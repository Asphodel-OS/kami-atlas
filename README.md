# kami-domain

The Kamigotchi domain manifest: what every component, system, function and entity type in the Kamigotchi contracts does and means, as YAML that people and agents can read without tracing the Solidity, plus a live config reader and an interactive map (the Kami Domain Atlas).

Lives next to the game checkout: `Asphodel-OS/kami/kami-domain/` reads `Asphodel-OS/kami/kamigotchi/`. Repository: https://github.com/Asphodel-OS/kami-domain (private; keep it out of any public mirror, since notes hold design intent).

```
generated/                   rebuilt from the code on every run; never edit
  systems/<Name>.yaml        one per system: summary + its own steps
  functions/<Library>.yaml   every library function a system can reach, each written once
  formulas.yaml              the calc* functions: code, stats, config and bonus inputs, which systems use them
  components.yaml            id, type, which systems write and read it
  entities.yaml              entity types: where created, components set at creation; hashed id namespaces
  domain-model.yaml / .json  31 typed entities: layer, ids, formal relationships, owners, sub-entities
notes/                       written by people; the generator never touches it
  systems/<Name>.yaml        summary, caller, preconditions, effects, invariants, open questions
  components.yaml            meaning, units, invariants, keyed by component name
  domain-model.yaml          entity descriptions, links the code can't resolve, confirmed facts, open questions
viz/                         the Kami Domain Atlas (interactive map); viz/data/ is written by generate.py
generate.py                  builds generated/ and viz/data/ from the contracts
config.py                    reads live config values from Yominet
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

Reads `kami/kamigotchi/packages/contracts` (whatever branch it is on); override with `KAMI_CONTRACTS=<path to packages/contracts>`. Needs Foundry and Python 3, nothing else.

## Domain model and the atlas

`generated/domain-model.yaml` (and `.json`) is derived from the code: the 31 typed entities with their layer (runtime, registry, or hybrid), id, links, owners and sub-entities. Links the generator resolved by tracing variable names are marked `inferred`; links it could not resolve have `to: null`. `notes/domain-model.yaml` adds what the code can't say: descriptions, the unresolved links, confirmed facts and open questions. The generator reports any entity without a description and any unresolved link not settled in notes.

`viz/index.html` is the Kami Domain Atlas. It reads `viz/data/`, which each generator run rewrites, and merges the notes in the browser. Run it locally with:

    python3 -m http.server 8765 --bind 127.0.0.1 -d viz     # then open http://127.0.0.1:8765/index.html (#KAMI opens one entity)

For the team it is deployed on Vercel at **https://kami-domain.vercel.app** (project `asphodel/kami-domain`, root directory `viz/`). Vercel Authentication is on for every URL, so only people logged into the Asphodel Vercel team can open it. Every push to `main` redeploys, so regenerate, commit `viz/data/` and push to update it.

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

## History

The first commits were made after the work, grouped by concern (generator, generated output, notes, config reader, atlas) in the order they were built, not as a step-by-step record. Later commits are real history.
