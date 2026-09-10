"""Shared data readers for the mechanisms.

Readers only: these modules parse files that already exist and normalise them
onto a grid.  They hold no rule, no threshold and no decision.  Anything that
decides belongs in a mechanism's own ``run.py``, where it is covered by that
mechanism's ``rules_hash``.
"""
