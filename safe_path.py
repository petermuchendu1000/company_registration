"""Containment check for file paths that come from a request.

Three endpoints used to decide "is this file inside the allowed directory?"
with a string prefix test on os.path.abspath(). That answers a different
question, and two bypasses followed from it - both reproduced against the
running app:

  1. CONTAINMENT. str(base) carries no trailing separator, so
     ".../pipeline_output_PROOF.txt".startswith(".../pipeline_output") is
     True even though the file sits outside the directory. A sibling whose
     name merely begins with the directory's name was served. This shape of
     file does occur here - .gitignore carries an entry
     "pipeline_outputlogsserver.log", which is exactly such a sibling,
     produced by a path-join bug.

  2. SYMLINKS. os.path.abspath normalises ".." but does not follow links,
     unlike os.path.realpath. A symlink inside the directory therefore kept
     a path string that passed the check while pointing anywhere on disk,
     and send_file followed it.

resolve_within fixes both: it resolves links on both sides and compares
path COMPONENTS, so a sibling can never match and a link cannot escape.
"""
from __future__ import annotations

from pathlib import Path


def resolve_within(base, candidate) -> Path | None:
    """Return the resolved `candidate` if it is inside `base`, else None.

    Returning None rather than raising keeps the call sites to a single
    `if target is None: abort(403)`, so there is one refusal path and no way
    to forget one.

    The base is resolved too, so this keeps working when the directory is
    itself a symlink - pipeline_output pointing at a mounted volume, which
    is how this will be laid out on a VPS with a separate data disk.

    `base` itself is accepted (a directory is "within" itself); callers
    still have to check is_file() before serving.
    """
    try:
        base_resolved = Path(base).resolve()
        target = Path(candidate).resolve()
    except (OSError, RuntimeError, ValueError):
        # RuntimeError: symlink loop. OSError: unreadable path component.
        # ValueError: embedded NUL byte. A path we cannot resolve is a path
        # we refuse.
        return None

    # Component-wise containment. Written with `parents` rather than
    # Path.is_relative_to so this does not raise the minimum Python version.
    if target != base_resolved and base_resolved not in target.parents:
        return None
    return target
