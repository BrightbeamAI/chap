"""Your first CHAP decision. Run: python start-here/hello.py"""
from chap_starter import ReviewGate, ReviewPending, ReviewRejected, ask_in_terminal

with ReviewGate() as chap:
    task = chap.propose({"text": "Your order will arrive tomorrow."})
    ask_in_terminal(chap, task)
    try:
        print("\nReviewed output:", chap.result(task))
    except (ReviewPending, ReviewRejected) as exc:
        print("\nStopped:", exc)
    print("\nYour real CHAP events:")
    for entry in chap.audit():
        # A refused call is recorded under "request", with its outcome.
        call = entry.get("envelope") or entry["request"]
        refused = "  (refused)" if "outcome" in entry else ""
        print(f"  {entry['seq']:2}  {call['method']:18}  {call['params'].get('from', '')}{refused}")
    print("\nLocal chain:", chap.verdict()["status"])
