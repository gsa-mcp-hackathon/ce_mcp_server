"""ce_capture: turn a user's request into a request record, check it, and resolve it.

Plan 4 (CE Request Capture Design Plan). A model, the server's or the client's,
writes the part of the record that only words can give: the nodes, the
answers, the ambiguities and the unresolved phrases. Code does everything
else, the same way on both paths: it normalizes names and codes, locates the
user's words, checks form and references, sets the status, stores the record,
and derives how each statistic will be computed.
"""
