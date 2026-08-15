# CHAP Conformance Action

Reusable GitHub Action that runs the CHAP conformance harness against
your CHAP coordinator endpoint and fails the job if any vector does
not match. The "credibility currency" for new implementations.

## Usage

```yaml
# .github/workflows/conformance.yml
name: CHAP conformance

on: [pull_request, push]

jobs:
  conformance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      # Start your CHAP coordinator however you usually do.
      - run: npm install && npm start &
        env:
          PORT: 8080
      - run: |
          for i in {1..30}; do
            curl -sf http://localhost:8080/health && break
            sleep 1
          done

      # Run the harness against it.
      - uses: BrightbeamAI/chap/.github/actions/chap-conformance@v0.2.9
        with:
          url: http://localhost:8080/chap
```

## Inputs

| Input          | Required | Default                                                              | Description                                                       |
| -------------- | -------- | -------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `url`          | yes      | (required)                                                           | JSON-RPC endpoint of your coordinator.                            |
| `ref`          | no       | `v0.2.5`                                                             | Tag/branch of the CHAP repo to source the harness from.           |
| `profiles`     | no       | `core,review,whisper,deliberation,handoff,control,routing`           | Comma-separated profiles to test.                                 |
| `node-version` | no       | `20`                                                                 | Node version for the harness.                                     |

## Outputs

| Output   | Description                                |
| -------- | ------------------------------------------ |
| `passed` | Count of conformance vectors that matched. |
| `failed` | Count that did not.                        |

## Add a "CHAP-conformant" badge

```markdown
![CHAP conformance](https://github.com/<owner>/<repo>/actions/workflows/conformance.yml/badge.svg)
```

Pair it with an entry in the [implementation registry](https://github.com/BrightbeamAI/chap/blob/main/IMPLEMENTATIONS.md).
