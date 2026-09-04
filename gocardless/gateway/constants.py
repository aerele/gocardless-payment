# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

"""GoCardless-specific constants, preserved from the payments app migration."""

# Supported transaction currencies (preserved from the payments app).
# NOTE: this list predates the migration and must be re-verified against current
# official GoCardless documentation before enabling new currencies.

# Payment statuses treated as authorised-but-not-yet-settled at creation time.

# Payment statuses treated as cancelled at creation time.
CANCELLED_PAYMENT_STATUSES = ("cancelled", "customer_approval_denied", "charged_back")
