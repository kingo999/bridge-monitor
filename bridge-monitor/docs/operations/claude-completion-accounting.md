# Claude completion accounting

The worker instructions list the `repair_effort` literals directly from
`DispositionRecord`'s schema: `none`, `minor`, `substantial`, and
`complete_replacement`. Unknown effort should be omitted. A small substantive
correction is `minor`; labels such as `low` are invalid.

Accepted substance can only have omitted or `none` effort. A revision cannot
claim `complete_replacement`. A replacement cannot claim `none` or `minor`.
These are the existing validation rules, now also stated in the worker prompt.

Invalid reports still produce diagnostics and incomplete accounting while the
original raw reply and delivered answer remain available. The bridge does not
silently translate labels or replay inference. A new report must satisfy the
same exact result hash, ownership and lineage checks as before.

This guidance applies to newly launched Claude workers after their parent MCP
process has loaded the updated adapter. Existing historical reports are retained
unchanged. Routing policy, model profiles, budgets and timeouts are unaffected.
