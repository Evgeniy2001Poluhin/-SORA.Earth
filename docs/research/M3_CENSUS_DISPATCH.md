# Bounded census dispatch

Status: offline preparation. No live census initialization or weather request is
authorized by the presence of this code or a JSON template.

The dispatcher wraps the existing census worker. It adds a separate journal lock,
an exclusive invocation ID, durable invocation and per-step start/result records,
and an externally supplied approval digest binding the exact clean commit, policy
digest, census root, journal root and a maximum of ten worker steps per invocation.
The worker continues to own P/controller.lock, P/worker.lock, shared expenses,
spacing, response classification and the no-retry acquisition state machine.

No sleeping or retries occur. A quota wait, paused state, completion or refusal
ends the invocation. The dispatcher never advances a date itself. An invocation
without a final result blocks every later invocation, including a different ID.
It requires investigation; this tool provides no recovery or clear-pause command.

Human provenance is an operator obligation. Arbitrary approval-shaped JSON does
not prove consent. The operator must record an actual owner decision, bind it to
the independently frozen commit/policy pair, then supply its digest separately.
Templates and decisions for different commits, policies or roots cannot authorize
execution. No key material or automatic consent mechanism is provided.

The policy pins a fixed scope declaration in authorization.txt. The actual owner
decision is a separate external approval record, avoiding a self-referential hash
cycle between a policy digest and the file that the policy itself hashes.

Initialization uses the same approval boundary and makes zero GET. The script
scripts/forecast_replay_census_dispatch.py requires explicit --mode initialize or
step, --approval, --approval-sha256, --run-id and all policy/source/root pins.
Each invocation is bounded by --steps (default one, maximum ten). Never run a
template as an approval. No sequential background daemon or deployment is added.


The CLI captures clean HEAD before importing application modules and binds every
later source guard to that loaded revision. A later clean checkout switch cannot
authorize code imported from the former revision. Run from a dedicated execution
checkout with no other writer; these checks are not a sandbox against an actor
rewriting and restoring Python files transiently during import. Census output and
dispatch journals must remain outside the execution checkout.
