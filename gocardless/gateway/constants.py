# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

"""GoCardless-specific constants, preserved from the payments app migration."""

# Mandate statuses under which GoCardless accepts a charge against a mandate.
CHARGEABLE_MANDATE_STATUSES = (
	"pending_customer_approval",
	"pending_submission",
	"submitted",
	"active",
)

# Payment statuses treated as cancelled at creation time.
CANCELLED_PAYMENT_STATUSES = ("cancelled", "customer_approval_denied", "charged_back")
