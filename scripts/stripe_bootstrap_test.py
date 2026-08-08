"""
Create the SSA package catalog in Stripe TEST mode.

Reads STRIPE_SECRET_KEY from the environment (or the project's gitignored
.env) and REFUSES anything that isn't an sk_test_ key — this script never
touches live mode. Provisioning itself lives in scripts/_bootstrap_common.py
(shared with the live bootstrap): one Product per SKU, a monthly AND a
6-month price each, at the launch amounts in
api/entitlements.py::LAUNCH_PRICE_CENTS.

Idempotent: run it again and it reuses what exists. Prices minted at older
amounts (e.g. the pre-2026-08-08 $9.99 placeholders) stay active and are
appended to the printed env lines so test subscriptions on them keep
resolving.

Prints ready-to-paste .env lines (12: STRIPE_PRICE_<SKU> + …_6MO) when done.

Usage (from the project root):
    python3 -m scripts.stripe_bootstrap_test
"""

from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from scripts._bootstrap_common import ensure_catalog


def main() -> int:
    try:
        import stripe
    except ImportError:
        print("The stripe package isn't installed. Run:\n"
              "    python3 -m pip install stripe python-dotenv")
        return 1

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    # Any test-mode key shape is fine (sk_test_, rk_test_, the CLI sandboxes'
    # rkcs_test_); anything without _test_ in it is refused — this script
    # never touches live mode.
    if "_test_" not in key:
        print("STRIPE_SECRET_KEY must be a TEST-mode key — this script "
              "never touches live mode.\nPut the test key in .env "
              "(see .env.example) and re-run.")
        return 1
    stripe.api_key = key

    lines = ensure_catalog(stripe)

    print("\nPaste into .env:")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
