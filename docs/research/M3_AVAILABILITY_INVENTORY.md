# Offline acquisition inventory

`scripts/forecast_replay_inventory.py --cache DIR --state-sha256 SHA --plan-sha256 SHA`
prints one deterministic JSON report. Obtain both pins independently from the
reviewed execution record. Redirect stdout to a **new file outside the cache**.
No acquisition code is imported, no HTTP request is made, and no lock, receipt,
policy or ledger is written. The tool requires the local schema-1 cache layout.

Every date in the plan receives exactly one acquisition status:

* `receipted_hash_checked`: receipt size and SHA256 match local bytes. This is
  **not** a fresh weather-grid validation or evidence of historical publication.
* `provider_unavailable_observed`: a pinned, complete HTTP400 diagnostic contains
  the exact unavailable-run message for the same date, linked to its reserved
  attempt and policy. It describes that retrieval, not all archives or all time.
* `attempted_unresolved`: reserved attempt or inflight marker without a receipt
  and without the above complete diagnostic. Reservation need not mean an actual
  HTTP request reached the provider. Timeout, partial bytes and unknown failures
  must not silently become unavailable-run classifications.
* `not_attempted`: neither receipt nor recorded attempt/inflight marker. This
  says nothing about whether the provider would return data.

Hash mismatches, ambiguous calendars, unexpected raw files, symlinks, duplicate
JSON keys and observed concurrent state changes refuse the report. A partial or
missing diagnostic stays unresolved; a corrupted pinned response refuses.
The input pins provide byte identity, not independent trust in whoever supplied
the pins. Same-user replacement of ancestor directories is out of scope.
An unavailable classification is an interpretation of that pinned recorded
response, not independent attestation that transport occurred. The separate
execution receipt must support any claim about the actual HTTP request. SHA256
establishes byte identity; it cannot authenticate a forged but newly pinned log.

The report never accepts source gaps, changes the scientific protocol, exports
training pairs or runs calibration/quality metrics. All-calendar byte coverage,
weather-grid validation, historical availability and scientific acceptance are
separate claims. Even all-calendar byte coverage does not imply the other three.
Any future missing-source experiment needs a separately reviewed protocol and
authorization. Existing pause and one-request permissions remain unchanged.
