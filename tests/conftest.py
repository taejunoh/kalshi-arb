import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Isolate the suite from the host's production .env.
#
# The softbook scripts resolve config as:
#     os.getenv(KEY) or <parse os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")>
# so on EC2 — where that file exists and sets CF_ACCESS_TEAM_DOMAIN — the API
# tests picked up the live Cloudflare Access gate and got 403/401 instead of
# the responses they assert. Four tests had been red on EC2 since the files
# were written (2026-06-13) while passing everywhere else, because a dev box
# has no /home/ubuntu/kalshi-arb/.env to find. Found 2026-07-28 running the
# full suite on EC2 after a git reconcile.
#
# Pointing ENV_PATH at os.devnull makes that lookup read an empty file and fall
# through to each caller's default, which is what a test host should see.
#
# Assigned at conftest import time rather than in a fixture: several modules
# (mem_watchdog, softbook_fill_listener) read ENV_PATH at MODULE import, which
# happens during collection — a fixture would run too late. Per-test overrides
# still work, since monkeypatch.setenv wins over this.
os.environ["ENV_PATH"] = os.devnull

# ...and shut the other door. `core/config.py` calls dotenv.load_dotenv() at
# module import, which walks up from the CWD, finds the same production .env on
# EC2 and copies every key into os.environ. `_env()` checks os.environ FIRST,
# so that path bypasses ENV_PATH entirely — which is why these tests failed
# only in the full suite and only on EC2: whichever test imported core.config
# first poisoned the process for everything after it, while a dev box has no
# .env to find. Neutralising the loader here (before any test module, so the
# `from dotenv import load_dotenv` in core.config binds this) keeps the suite
# reading its own fixtures instead of whatever the host happens to be
# configured with. No test depends on real .env values — the suite is green on
# machines that have no .env at all.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False
