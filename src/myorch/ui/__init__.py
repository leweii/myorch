"""Local console (`myorch ui`): the only place people interact with myorch.

There is no agent in the console. The middle of the page is the selected run's conversation: what the
user writes goes verbatim to the run, and approvals are buttons. The page binds to 127.0.0.1 and holds
no state of its own beyond what the user said, in myorch-data/ui/. See docs/03-architecture.md and
docs/05-cli.md.
"""
