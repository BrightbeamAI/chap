# @brightbeamai/chap-code-gate

The CHAP code gate. Every change a coding agent makes in a git repository is
proposed as a patch, reviewed as a diff in the desk, decided and signed by a
person, and committed only then, by hooks that refuse anything else. An
agent that commits on its own proposes its branch, which is reviewed commit
by commit and sealed before it is pushed. Each governed commit names its
reviewers and the model that wrote it, with the evidence beside it as a git
note, and a verifier checks any range of commits against a trust policy in
CI.

`npx create-chap-app my-gate` makes a gate project that depends on this
package; its README walks through the whole gate. This README is the
package's reference.

## The gate project

The commands run against a gate project: the directory that holds its
`chap.config.json`, its `keys/` and its `data/`. The server is
`@brightbeamai/chap-server`, run from the project with `npm start`.

## The commands

```
chap-code-gate propose "<summary>" [--wait --commit]   propose the working tree's change for review
chap-code-gate propose-branch <base>..<head> [--wait]  propose a branch for review before it is pushed
chap-code-gate verify [<range>] [--trust <file>]       check that commits were approved through the gate
chap-code-gate trust [--out <file>]                    write the trust policy, chap-trust.json
chap-code-gate install-hooks --repo <path>             put a repository under the gate's git hooks
chap-code-gate hook <name>                             run one of the git hooks, as git does
chap-code-gate push-notes [--remote origin]            send the evidence notes with the commits
chap-code-gate report [--json]                         what the gate recorded, as Markdown
chap-code-gate stop                                    stop a gate a command started in the background
```

`--gate <dir>` names the gate project; by default it is the nearest
directory up from the working directory that holds `chap.config.json`. A
gate project keeps a small file for the commands named by path from other
places, `propose.mjs` for an agent working in another repository and
`verify.mjs` for CI among them; each runs the command for the project it is
in.

## The library

The modules behind the commands are the package's subpaths:

| Import | What |
|---|---|
| `@brightbeamai/chap-code-gate/gate` | the configuration, the agent's client, a change as a task under review, the trust policy, the evidence note and the checks on it |
| `@brightbeamai/chap-code-gate/git` | git as the gate uses it: canonical patches, the tree a patch gives, trailers, notes, signatures |
| `@brightbeamai/chap-code-gate/range` | a branch under review, and sealing it once approved |
| `@brightbeamai/chap-code-gate/verify` | `verifyRange`, `verifyCommit` |
| `@brightbeamai/chap-code-gate/hook` | the git hooks |
| `@brightbeamai/chap-code-gate/install-hooks` | `installHooks`, `installClaudeHook` |
| `@brightbeamai/chap-code-gate/demo-repo` | the demo repository the built-in agent works in |

## Licence

Apache-2.0
