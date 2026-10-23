# One diagnostic request after the reviewed HTTP400

An independently pinned one-request policy chains all five parent pins, the
exact failed state and a clean execution commit. It authorizes the failed date
only. The original failed checkpoint/policy are saved durably. Shared controller
and worker locks, full parent validation, raw hashes and unchanged rolling quota
checks precede a21-unit reservation in the canonical journal. This is not a
separate quota ledger. Total ceilings equal prior attempts+1 / prior units+21.

The request permission is consumed durably BEFORE transport. A crash, guard
failure or output failure cannot authorize another call. New invocations refuse
a reserved probe. Quota waits consume nothing. No auto retry/daemon/sleep exists.

Original pause, inflight and completions stay unchanged, even for HTTP200.
Diagnostic bytes are saved under http-probe-response.bin with hash/size, outside
the raw weather directory. HTTP-error bodies use the bounded1024-byte prefix
capture; untrusted bytes must be inspected as plain text, never rendered or run.
No response headers or credentials are saved. Only the existing public request
is used. The response is not a completion receipt or analysis input.

Changing the effective policy pin makes older collectors fail closed. After
the probe the ledger intentionally remains STOP: further collection needs a
separate reviewed transition that accounts for this diagnostic reservation and
its provenance. This implementation does not authorize that transition.
