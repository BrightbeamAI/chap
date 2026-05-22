/**
 * State store — persists the Coordinator's snapshot() output to a
 * local JSON file. The playground uses this so the audit chain
 * survives restarts. In production you'd use a real database.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import type { Coordinator } from "@chap/coordinator";

export interface StateStore {
  load(): Promise<void>;
  save(): Promise<void>;
  reset(): Promise<void>;
  path: string;
}

export function makeFileStateStore(coord: Coordinator, dataDir: string): StateStore {
  const filePath = path.join(dataDir, "state.json");
  // Ensure the dir up front. Subsequent saves wait on `saveChain`
  // which is initialised to this promise, so the first save is
  // guaranteed to run after mkdir resolves and subsequent saves
  // are serialised behind the previous one.
  let saveChain: Promise<unknown> = fs.mkdir(dataDir, { recursive: true });

  return {
    path: filePath,

    async load() {
      try {
        await saveChain;
        const raw = await fs.readFile(filePath, "utf-8");
        const data = JSON.parse(raw);
        coord.restore(data);
      } catch (e: unknown) {
        const code = (e as NodeJS.ErrnoException)?.code;
        if (code !== "ENOENT") {
          console.warn(`state-store: load failed (${code}); starting fresh`);
        }
      }
    },

    async save() {
      const next = saveChain.then(async () => {
        const data = coord.snapshot();
        const tmp = filePath + ".tmp";
        await fs.writeFile(tmp, JSON.stringify(data, null, 2), "utf-8");
        await fs.rename(tmp, filePath);
      });
      saveChain = next.catch(() => undefined);
      return next;
    },

    async reset() {
      await saveChain;
      try {
        await fs.unlink(filePath);
      } catch {
        // file may not exist
      }
    },
  };
}
