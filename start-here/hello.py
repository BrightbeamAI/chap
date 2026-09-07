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
        envelope = entry["envelope"]
        print(f"  {entry['seq']:2}  {envelope['method']:18}  {envelope['params'].get('from', '')}")
    print("\nLocal chain:", chap.verdict()["status"])
