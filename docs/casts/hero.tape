# hero.tape — generates docs/img/hero.gif
#
# This drives the CHAP playground demo and records a ~30s walkthrough
# showing: agent drafts -> human overrides -> chain replays.
#
# Prerequisites:
#   1. Install vhs:  brew install vhs   (or see https://github.com/charmbracelet/vhs)
#   2. Have the playground running locally:
#        cd reference/playground && npm install && npm run dev
#      (starts on http://localhost:5173 by default)
#
# Generate:
#   vhs docs/casts/hero.tape
#
# Output is written to docs/img/hero.gif

Output docs/img/hero.gif

Set FontSize 16
Set Width 1200
Set Height 700
Set Theme "Dracula"
Set Padding 24
Set Framerate 24
Set TypingSpeed 35ms

# ---- Scene 1: spin up a workspace ----
Type "# A tiny CHAP workspace. Two participants, one task."
Sleep 800ms
Enter

Type "npx @chap/playground create-workspace wsp_demo --profiles core/1.0,review/1.0"
Sleep 500ms
Enter
Sleep 2s

Type "npx @chap/playground join wsp_demo --as human:alice@example.com"
Sleep 500ms
Enter
Sleep 1500ms

Type "npx @chap/playground join wsp_demo --as agent:reviewer-bot"
Sleep 500ms
Enter
Sleep 1500ms

# ---- Scene 2: agent drafts, human overrides ----
Type "# The bot proposes a review."
Sleep 600ms
Enter

Type "npx @chap/playground agent reviewer-bot draft --task tsk_pr_482 --file demo/draft.json"
Sleep 500ms
Enter
Sleep 2s

Type "# I disagree with one comment. Override with rationale."
Sleep 600ms
Enter

Type "npx @chap/playground human alice override --task tsk_pr_482 \\"
Sleep 200ms
Enter
Type "  --path /comments/0/severity --to info \\"
Sleep 200ms
Enter
Type '  --rationale "Framework convention. Not a bug." \\'
Sleep 200ms
Enter
Type "  --tag false-positive --tag framework-pattern-misread"
Sleep 300ms
Enter
Sleep 2s

# ---- Scene 3: replay the chain ----
Type "# Six months later. Replay the whole chain."
Sleep 600ms
Enter

Type "npx @chap/playground audit read --task tsk_pr_482"
Sleep 500ms
Enter
Sleep 3s

Type "# Every step. Verified by prev_hash. That's CHAP."
Sleep 800ms
Enter
Sleep 2s
