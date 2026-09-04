# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

"""Payment-status webhook synchronisation.

GoCardless ``payments`` events update the matching Integration Request and,
once funds are confirmed (``confirmed``/``paid_out``), settle the linked
Payment Request exactly once through ``payment_core.utils.settle_payment_request``.

Guarantees:
- Events are deduplicated on the provider event ID via the unique-named
  ``GoCardless Webhook Event`` record; a duplicate delivery returns early.
- Settlement is idempotent: an already-Completed Integration Request is never
  settled twice, and payment_core re-checks Payment Request status and existing
  Payment Entries before creating anything.
- The provider payment object is fetched from GoCardless before settlement and
  its amount/currency must match the reference, since webhook event bodies do
  not carry amounts.
- Failures propagate so the webhook returns a retryable status and the whole
  transaction (including the dedup row) rolls back for the next attempt.
"""

import json

import frappe

from payment_core.api.controllers import get_gateway_controller_name
from payment_core.utils import settle_payment_request

from gocardless.gateway.constants import CANCELLED_PAYMENT_STATUSES

INTEGRATION_SERVICE = "GoCardless"
SETTLED_PAYMENT_ACTIONS = ("confirmed", "paid_out")


def sync_payment_event(event: dict) -> None:
	payment_id = (event.get("links") or {}).get("payment")
	action = event.get("action")

	# dedup on the provider event id; the unique naming makes a racing
	# duplicate insert fail at the database level
	log = frappe.get_doc(
		{
			"doctype": "GoCardless Webhook Event",
			"event_id": event["id"],
			"resource_type": event.get("resource_type"),
			"action": action,
			"payment": payment_id,
			"status": "Processed",
		}
	)
	try:
		log.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		return

	integration_request_name = find_integration_request(payment_id)
	if not integration_request_name:
		# payment not created by this app; nothing to sync
		log.db_set("status", "Skipped")
		return

	integration_request = frappe.get_doc("Integration Request", integration_request_name)

	if action in SETTLED_PAYMENT_ACTIONS:
		settle_integration_request(integration_request, action, payment_id)
	elif action == "failed":
		integration_request.db_set("status", "Failed", update_modified=False)
		integration_request.db_set("error", action, update_modified=False)
	elif action in CANCELLED_PAYMENT_STATUSES:
		integration_request.db_set("status", "Cancelled", update_modified=False)
		integration_request.db_set("error", action, update_modified=False)
	else:
		# pending_submission / submitted / pending_customer_approval: informational
		integration_request.db_set("status", "Authorized", update_modified=False)
		integration_request.db_set("output", action, update_modified=False)


def find_integration_request(payment_id: str | None) -> str | None:
	"""Locate the Integration Request created for a GoCardless payment."""
	if not payment_id:
		return None

	candidates = frappe.get_all(
		"Integration Request",
		filters={
			"integration_request_service": INTEGRATION_SERVICE,
			"data": ("like", f'%"{payment_id}"%'),
		},
		fields=["name", "data", "status", "reference_doctype", "reference_docname"],
		order_by="creation desc",
		limit=20,
	)
	for candidate in candidates:
		try:
			data = json.loads(candidate.data or "{}")
		except (TypeError, ValueError):
			continue
		if data.get("payment_id") == payment_id:
			return candidate.name
	return None


def settle_integration_request(integration_request, action: str, payment_id: str) -> None:
	# exactly-once guard against redirect/webhook races and duplicate deliveries
	if integration_request.status == "Completed":
		return

	if integration_request.reference_doctype == "Payment Request":
		if not verify_payment_amount(integration_request.reference_docname, payment_id):
			integration_request.db_set("output", f"amount_mismatch:{action}", update_modified=False)
			frappe.log_error(
				title="GoCardless amount mismatch",
				reference_doctype="Integration Request",
				reference_name=integration_request.name,
				message=f"Payment {payment_id} does not match the amount/currency of "
				f"{integration_request.reference_docname}; settlement skipped.",
			)
			return

		pr = frappe.get_doc("Payment Request", integration_request.reference_docname)
		settle_payment_request(pr)

	integration_request.db_set("status", "Completed", update_modified=False)
	integration_request.db_set("output", action, update_modified=False)


def verify_payment_amount(pr_docname: str, payment_id: str) -> bool:
	"""Whether the provider payment still matches the reference amount/currency.

	Webhook event bodies do not carry amounts, so the payment object is fetched
	from GoCardless before any settlement.
	"""
	from frappe.utils import cint

	pr = frappe.get_doc("Payment Request", pr_docname)
	settings_name = get_gateway_controller_name(
		reference_doctype="Payment Request", reference_docname=pr_docname
	)
	settings = frappe.get_doc("GoCardless Settings", settings_name)
	payment = settings.initialize_client().payments.get(payment_id)

	return payment.amount == cint(pr.grand_total * 100) and payment.currency == pr.currency
