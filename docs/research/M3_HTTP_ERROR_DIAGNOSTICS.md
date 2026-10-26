# Bounded public HTTP error diagnostics

Offline code improvement prompted by HTTP400 on 2025-08-05. It cannot recover
the body of that already-finished failed request and does not establish why it
failed. No new HTTP request, retry, authorization or cache transition is part
of this change. Frozen acquisition sources and the paused owner ledger stay
unchanged. Activating another source/request needs a separately reviewed scope.

The existing isolated child still makes one allowlisted public GET with the
same timeout, no credentials/proxy/redirects and no automatic retry. If that
GET raises HTTPError with status400–599, the child reads at most1025 bytes
from the *existing* error response, retains at most1024, and closes it. This is
not another request. The existing45-second parent deadline also bounds error
body reading; timeout can still prevent delivery of any diagnostic.

A diagnostic contains base64 bytes, captured byte count, SHA256 of the captured
prefix, and a truncation flag. For a truncated body this hash is NOT the hash
of the complete server response. The parent strictly validates these fields,
size and digest. Invalid body metadata is omitted while preserving safe error
class/status. Error messages, headers and URLs are not copied. A failed body
read preserves the HTTP status without a body. Non-HTTP and3xx responses do
not enter this body-capture path. The existing4096-byte stderr envelope cap
and all acquisition quotas remain unchanged.

The prefix is untrusted provider content, potentially HTML or binary. It is
stored encoded only under failure_diagnostic.http_error_body, never rendered,
executed, treated as weather or included in a completion receipt. Operator
inspection should use a text-safe viewer, not HTML rendering. Error bodies can
reflect request information: this is restricted to the existing public,
credential-free allowlisted transport, not a generic authenticated client.
A failure remains charged and paused; its diagnostic cannot permit retries.
