# tools/

Drop-in tooling that doesn't need to be packaged.

## `audit-viewer.html`

Single-file HTML viewer for CHAP workspace exports. No build step, no
server, no dependencies. Open it in any browser, drop a JSON file
containing a `Coordinator.snapshot()` output (or a `.bbcell.json`),
see:

- Summary cards: audit entries, tasks, overrides, participants
- Hash-chain integrity check (warns on breaks)
- Method-frequency bars
- Override-tag bars (the protocol's "training data for free" dividend)
- The full hash-linked chain rendered with method, actor, prev-hash,
  and inline override detail (rationale, tags, the RFC 6902 JSON Patch)

**Usage**:

```bash
# Local
open tools/audit-viewer.html

# Or serve over HTTP for sharing
python3 -m http.server 8000 --directory tools
# then visit http://localhost:8000/audit-viewer.html
```

**Producing input from a running workspace**:

From any Coordinator instance:

```ts
// TypeScript
import { writeFileSync } from "fs";
writeFileSync("workspace.json", JSON.stringify(coord.snapshot(), null, 2));
```

```python
# Python
import json
open("workspace.json", "w").write(json.dumps(coord.snapshot(), indent=2))
```

The viewer accepts the array directly or a wrapper object with a
`workspaces` field.

**Privacy**: the viewer is fully client-side. Nothing the user drops
on it leaves the browser. Suitable for inspecting sensitive
production audit chains without uploading them anywhere.
