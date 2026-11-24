<!-- ce-capture guidance for after a record is stored, version 1.0. Returned by
capture_guide to a client's model. The server-side capture model never sees it:
it only writes the record. Raise the version when this changes. -->

# After the record is stored

The reply to `submit_request_record` gives a status. What you do next depends
on it.

- **captured:** call `run_request` with the `request_id`, then give the user
  its `reply` text as written. Each caution in it belongs to the figure above
  it: keep them together.
- **needs_clarification:** put the questions to the user as written and wait
  for the answer. Give no figure meanwhile. Submit the answer with
  `answers_request_id`.
- **check_failed:** correct the record from the errors and submit it again
  with `corrects_request_id`.
- **not_captured:** tell the user, in plain words, why this cannot be answered
  from the CE Interview Survey.

## A block is an answer

`run_request` may report that a statistic cannot be computed, with the reason.
That is the result for that statistic.

- Give the user the reason. Then say what could be computed instead, and let
  them choose.
- Do not capture a different request in its place: another column, another
  table, another statistic. The user asked for one thing; a figure for
  another, however well explained, is not what they asked for.
- A figure the server computed for another part of the same request may be
  shown, labelled as what it is.

## Only the server's figures are results

Show the figures the server returned. Do not work out further ones from them:
a share from two counts, an interval from a standard error, a total of the
groups, a difference of two rows. The server applies the weights, the months
in scope and the replicate weights; arithmetic on its rounded results does
not. If the user asks for such a figure and the server cannot give it, say so,
and if you still show your own arithmetic, mark it plainly as yours and not
the server's.

Never give a figure from memory, such as a published BLS number, as if it came
from this data.
