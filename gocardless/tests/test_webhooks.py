# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

"""Webhook tests: signature verification (multi-account) and mandate status
synchronisation, preserved from the payments app."""

import hashlib
import hmac
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from gocardless.gateway import webhooks as webhook_module
from gocardless.tests.test_gocardless_settings import make_settings

SECRET = "whsec_test_secret"


def build_payload(mandate: str) -> bytes:
	return json.dumps(
		{"events": [{"resource_type": "mandates", "action": "active", "links": {"mandate": mandate}}]}
	).encode()


class FakeRequest:
	def __init__(self, payload: bytes):
		self._payload = payload

	def get_data(self):
		return self._payload


def sign(payload: bytes, secret: str) -> str:
	return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


class TestWebhooks(FrappeTestCase):
	def setUp(self):
		if "erpnext" not in frappe.get_installed_apps():
			self.skipTest("erpnext not installed on this site")

		self.settings = make_settings()

	def test_settings_generate_webhook_endpoint(self):
		self.assertIn("/api/method/gocardless.gateway.webhooks.webhooks", self.settings.webhook_endpoint)

	def set_webhook_secret(self, name: str, secret: str = SECRET) -> None:
		# Password fields must be stored through the password API to be decryptable
		from frappe.utils.password import set_encrypted_password

		set_encrypted_password("GoCardless Settings", name, secret, "webhooks_secret")
		webhook_module.clear_cache()

	def test_authenticate_signature_accepts_valid_signature(self):
		self.set_webhook_secret(self.settings.name)
		payload = build_payload("MD00001")

		with patch("frappe.get_request_header", return_value=sign(payload, SECRET)):
			self.assertTrue(webhook_module.authenticate_signature(FakeRequest(payload)))

	def test_authenticate_signature_rejects_wrong_signature(self):
		payload = build_payload("MD00001")
		with patch("frappe.get_request_header", return_value=sign(payload, "other-secret")):
			self.assertFalse(webhook_module.authenticate_signature(FakeRequest(payload)))

	def test_authenticate_signature_rejects_missing_header(self):
		payload = build_payload("MD00001")
		with patch("frappe.get_request_header", return_value=None):
			self.assertFalse(webhook_module.authenticate_signature(FakeRequest(payload)))

	def test_authenticate_signature_supports_multiple_accounts(self):
		second = make_settings()
		self.set_webhook_secret(second.name)
		payload = build_payload("MD00001")

		with patch("frappe.get_request_header", return_value=sign(payload, SECRET)):
			self.assertTrue(webhook_module.authenticate_signature(FakeRequest(payload)))

	def make_mandate(self, mandate: str | None = None):
		if mandate is None:
			mandate = f"MD{frappe.generate_hash(length=10)}"
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"_Test GC Webhook Customer {frappe.generate_hash(length=6)}",
				"customer_type": "Company",
			}
		).insert(ignore_permissions=True)

		return frappe.get_doc(
			{
				"doctype": "GoCardless Mandate",
				"mandate": mandate,
				"customer": customer.name,
				"gocardless_customer": "CU00001",
			}
		).insert(ignore_permissions=True)

	def test_mandate_event_toggles_disabled(self):
		mandate = self.make_mandate()
		mandate.db_set("disabled", 1)

		event = {"resource_type": "mandates", "action": "active", "links": {"mandate": mandate.name}}
		webhook_module.set_status(event)
		self.assertEqual(frappe.db.get_value("GoCardless Mandate", mandate.name, "disabled"), 0)

		event = {"resource_type": "mandates", "action": "cancelled", "links": {"mandate": mandate.name}}
		webhook_module.set_status(event)
		self.assertEqual(frappe.db.get_value("GoCardless Mandate", mandate.name, "disabled"), 1)

	def test_mandate_event_with_links_list(self):
		first = self.make_mandate()
		second = self.make_mandate()

		event = {
			"resource_type": "mandates",
			"action": "cancelled",
			"links": [{"mandate": first.name}, {"mandate": second.name}],
		}
		webhook_module.set_status(event)
		self.assertEqual(frappe.db.get_value("GoCardless Mandate", first.name, "disabled"), 1)
		self.assertEqual(frappe.db.get_value("GoCardless Mandate", second.name, "disabled"), 1)

	def test_payment_events_dispatch_to_settlement(self):
		event = {"id": "EVDISPATCH1", "resource_type": "payments", "action": "confirmed", "links": {"payment": "PM1"}}
		with patch.object(webhook_module.settlement, "sync_payment_event") as sync:
			webhook_module.set_status(event)
		sync.assert_called_once_with(event)

	def test_unknown_resource_type_is_ignored(self):
		# must not raise even though links are absent
		webhook_module.set_status({"resource_type": "refunds", "action": "created"})

	def test_webhooks_endpoint_verifies_signature(self):
		self.set_webhook_secret(self.settings.name)
		mandate = self.make_mandate()
		mandate.db_set("disabled", 1)
		payload = build_payload(mandate.name)

		frappe.local.request = FakeRequest(payload)
		try:
			with patch("frappe.get_request_header", return_value="invalid-signature"):
				self.assertRaises(frappe.AuthenticationError, webhook_module.webhooks)

			with patch("frappe.get_request_header", return_value=sign(payload, SECRET)):
				self.assertEqual(webhook_module.webhooks(), 200)

			self.assertEqual(frappe.db.get_value("GoCardless Mandate", mandate.name, "disabled"), 0)
		finally:
			del frappe.local.request

	def test_clear_cache_refreshes_secrets(self):
		from frappe.utils.password import set_encrypted_password

		payload = build_payload("MD00001")
		webhook_module.clear_cache()
		set_encrypted_password("GoCardless Settings", self.settings.name, SECRET, "webhooks_secret")
		# simulate a stale cached secret from before the rotation
		frappe.cache().set_value(webhook_module.WEBHOOK_CACHE_KEY, ["whsec_stale"])

		with patch("frappe.get_request_header", return_value=sign(payload, SECRET)):
			# stale cached secret does not verify the new secret's signature
			self.assertFalse(webhook_module.authenticate_signature(FakeRequest(payload)))
			# after invalidation the keys regenerate from the database
			webhook_module.clear_cache()
			self.assertTrue(webhook_module.authenticate_signature(FakeRequest(payload)))
