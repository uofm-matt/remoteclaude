# remoteclaude

## Compact instructions

When compacting, keep, in this order:
1. The current task and the exact next step.
2. Every decision Matt made in this conversation, with the reason he gave, and any
   preference or correction he stated that is not already in a file.
3. Requests still waiting on Matt, and what each is waiting for.
4. Facts with their status marked verified or inferred, and the source (path, command, ID,
   number). Keep "not tested" and "inferred" labels; do not turn a guess into a fact.
5. Other sessions involved and what each was asked.
6. What was changed and where (paths, commits, config keys), and how to undo it.

Drop tool output, directory listings and exploration that led nowhere. Point to files for
detail instead of restating them.

Only in the conversation for this project: the reasons behind launcher design decisions
(idempotent launch, model pin and allowlist), the live build stamp and commit ids that were
shipped and verified, gate and CI results, any standing freeze on launcher changes or
reloads and who set it, and plans awaiting Matt's confirmation.
