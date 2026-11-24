"""ce_eval: how well a model uses the server (Plan 6).

Code decides everything after the request record, and that code has its own
tests. A model owns four things, and this package measures them: capture (the
record it writes), orchestration (the tools it calls, in what order),
presentation (whether the reply reaches the user with every caution and figure
intact) and scope (what it declines).

Every case is scored at three levels: the record, the outcome that record
leads to, and the final answer. The server never imports this package.
"""
