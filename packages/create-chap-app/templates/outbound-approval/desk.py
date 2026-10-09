"""The process that owns the store: the desk, POST /chap and the API.

Run it first, then the agent. Everything it needs is in chap.config.json,
including the workspace mode, trial, under which every task requires review.
"""
from chap_server import serve

if __name__ == "__main__":
    serve()
