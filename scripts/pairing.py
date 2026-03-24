#!/usr/bin/env python3
"""ClawSoc pairing — simplified direct-connect model.

No more invite codes. Two ways to pair:
  1. pair <endpoint>   — connect directly by URL
  2. pair <peer_id>    — look up endpoint from prior discover --record
"""
from __future__ import annotations

# This module is intentionally minimal now.
# The old invite-code machinery has been removed.
# Pairing is handled directly in clawsoc_cli.py via HTTP POST to /clawsoc/pair.
