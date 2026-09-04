# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

"""GoCardless webhook endpoint: mandate and payment-status synchronisation.

Preserved from the payments app: raw-body HMAC-SHA256 verification against
every configured webhook secret (multi-account support). Mandate events toggle
the local mandate; payment events are delegated to gateway.settlement for
Integration Request updates and exactly-once Payment Request settlement.
"""

import hashlib
import hmac
import json

import frappe

from frappe.utils.password import get_decrypted_password

from gocardless.gateway import settlement
from gocardless.gateway.constants import CHARGEABLE_MANDATE_STATUSES

WEBHOOK_SIGNATURE_HEADER = "Webhook-Signature"
WEBHOOK_CACHE_KEY = "gocardless_webhooks_secret"

@frappe.whitelist(allow_guest=True)
def webhooks() -> int | None:
	r = frappe.request
	if not r:
		return None

	if not authenticate_signature(r):
		raise frappe.AuthenticationError
	settings = get_verified_webhook_settings(r)
	if not settings:
		raise frappe.AuthenticationError

	gocardless_events = json.loads(r.get_data()) or []
	for event in gocardless_events["events"]:
		set_status(event, settings)

	return 200


def set_status(event: dict, settings) -> None:
	resource_type = event.get("resource_type", {})

	if resource_type == "mandates":
		set_mandate_status(event, settings)
	elif resource_type == "payments":
		settlement.sync_payment_event(event)


def set_mandate_status(event: dict, settings) -> None:
	mandates = []
	if isinstance(event["links"], list):
		for link in event["links"]:
			mandates.append(link["mandate"])
	else:
		mandates.append(event["links"]["mandate"])

	for mandate in mandates:
		remote_mandate = settings.initialize_client().mandates.get(mandate)
		disabled = 0 if remote_mandate.status in CHARGEABLE_MANDATE_STATUSES else 1
		frappe.db.set_value("GoCardless Mandate", mandate, "disabled", disabled)


def authenticate_signature(r) -> bool:
	"""Returns True if the received signature matches the generated signature."""
	received_signature = frappe.get_request_header(WEBHOOK_SIGNATURE_HEADER)

	if not received_signature:
		return False

	for key in get_webhook_keys():
		computed_signature = hmac.new(key.encode("utf-8"), r.get_data(), hashlib.sha256).hexdigest()
		if hmac.compare_digest(str(received_signature), computed_signature):
			return True

	return False


def get_verified_webhook_settings(r):
	"""Return the Settings record whose secret verified this raw webhook request."""
	received_signature = frappe.get_request_header(WEBHOOK_SIGNATURE_HEADER)
	if not received_signature:
		return None

	for settings_name in frappe.get_all("GoCardless Settings", pluck="name"):
		key = get_decrypted_password(
			"GoCardless Settings", settings_name, fieldname="webhooks_secret", raise_exception=False
		)
		if not key:
			continue

		computed_signature = hmac.new(key.encode("utf-8"), r.get_data(), hashlib.sha256).hexdigest()
		if hmac.compare_digest(str(received_signature), computed_signature):
			return frappe.get_doc("GoCardless Settings", settings_name)

	return None


def get_webhook_keys() -> list[str]:
	def _get_webhook_keys():
		# Password fields cannot be read with get_all; decrypt each record's secret.
		webhook_keys = []
		for settings_name in frappe.get_all("GoCardless Settings", pluck="name"):
			key = get_decrypted_password(
				"GoCardless Settings", settings_name, fieldname="webhooks_secret", raise_exception=False
			)
			if key:
				webhook_keys.append(key)

		return webhook_keys

	return frappe.cache().get_value(WEBHOOK_CACHE_KEY, _get_webhook_keys)


def clear_cache(doc=None) -> None:
	"""Drop the cached webhook secrets (wired via doc_events, which pass the doc)."""
	frappe.cache().delete_value(WEBHOOK_CACHE_KEY)
