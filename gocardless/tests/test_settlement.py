# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from gocardless.gateway import settlement
from gocardless.tests.test_gocardless_settings import make_settings

SETTLEMENT_MODULE = "gocardless.gateway.settlement"


def make_event(action: str, payment_id: str = "PM00001TEST", event_id: str | None = None) -> dict:
	return {
		"id": event_id or f"EV{frappe.generate_hash(length=10)}",
		"resource_type": "payments",
		"action": action,
		"links": {"payment": payment_id},
	}


class TestPaymentEventSync(FrappeTestCase):
	def setUp(self):
		self.settings = make_settings()
		self.payment_id = f"PM{frappe.generate_hash(length=8)}"
		self.integration_request = frappe.get_doc(
			{
				"doctype": "Integration Request",
				"integration_request_service": "GoCardless",
				"data": json.dumps({"payment_id": self.payment_id}),
			}
		).insert(ignore_permissions=True)

	def ir_status(self) -> str:
		return frappe.db.get_value("Integration Request", self.integration_request.name, "status")

	def ir_value(self, fieldname: str):
		return frappe.db.get_value("Integration Request", self.integration_request.name, fieldname)

	def test_duplicate_event_delivery_is_skipped(self):
		event = make_event("submitted", self.payment_id)
		settlement.sync_payment_event(event)

		with patch(f"{SETTLEMENT_MODULE}.settle_integration_request") as settle:
			settlement.sync_payment_event(event)

		settle.assert_not_called()
		self.assertEqual(frappe.db.count("GoCardless Webhook Event", {"event_id": event["id"]}), 1)

	def test_unknown_payment_is_skipped(self):
		event = make_event("submitted", "PM-UNKNOWN-XYZ")
		settlement.sync_payment_event(event)
		self.assertEqual(
			frappe.db.get_value("GoCardless Webhook Event", event["id"], "status"), "Skipped"
		)

	def test_confirmed_event_completes_integration_request(self):
		settlement.sync_payment_event(make_event("confirmed", self.payment_id))
		self.assertEqual(self.ir_status(), "Completed")
		self.assertEqual(self.ir_value("output"), "confirmed")

	def test_failed_and_cancelled_actions_mark_integration_request(self):
		for action, expected_status in (("failed", "Failed"), ("cancelled", "Cancelled")):
			with self.subTest(action=action):
				self.integration_request.db_set("status", "Queued")
				settlement.sync_payment_event(make_event(action, self.payment_id))
				self.assertEqual(self.ir_status(), expected_status)
				self.assertEqual(self.ir_value("error"), action)

	def test_pending_action_marks_integration_request_authorized(self):
		settlement.sync_payment_event(make_event("submitted", self.payment_id))
		self.assertEqual(self.ir_status(), "Authorized")
		self.assertEqual(self.ir_value("output"), "submitted")

	def test_find_integration_request_matches_stored_payment_id(self):
		self.assertEqual(settlement.find_integration_request(self.payment_id), self.integration_request.name)
		self.assertIsNone(settlement.find_integration_request("PM-UNKNOWN-XYZ"))

	def test_settlement_calls_payment_core_only_after_verification(self):
		integration_request = MagicMock(
			status="Authorized", reference_doctype="Payment Request", reference_docname="PR-TEST-SETTLE"
		)
		payment_request = MagicMock()

		with (
			patch(f"{SETTLEMENT_MODULE}.verify_payment_amount", return_value=True),
			patch(f"{SETTLEMENT_MODULE}.settle_payment_request") as settle,
			patch("frappe.get_doc", return_value=payment_request),
		):
			settlement.settle_integration_request(integration_request, "confirmed", self.payment_id)

		settle.assert_called_once_with(payment_request)

	def test_settlement_does_not_call_payment_core_when_verification_fails(self):
		integration_request = MagicMock(
			status="Authorized", reference_doctype="Payment Request", reference_docname="PR-TEST-SETTLE"
		)

		with (
			patch(f"{SETTLEMENT_MODULE}.verify_payment_amount", return_value=False),
			patch(f"{SETTLEMENT_MODULE}.settle_payment_request") as settle,
		):
			settlement.settle_integration_request(integration_request, "confirmed", self.payment_id)

		settle.assert_not_called()


class TestPaymentVerification(FrappeTestCase):
	def verify(self, payment_id: str, payment):
		payment_request = MagicMock(grand_total=100, currency="GBP")
		settings = MagicMock()
		settings.initialize_client.return_value.payments.get.return_value = payment

		with (
			patch("frappe.get_doc", side_effect=[payment_request, settings]),
			patch(f"{SETTLEMENT_MODULE}.get_gateway_controller_name", return_value="_Test GC"),
		):
			return settlement.verify_payment_amount("PR-TEST", payment_id)

	def test_payment_verification_requires_matching_id_settled_status_amount_and_currency(self):
		payment_id = "PM-TEST"
		for status in ("confirmed", "paid_out"):
			with self.subTest(status=status):
				valid_payment = MagicMock(id=payment_id, status=status, amount=10000, currency="GBP")
				self.assertTrue(self.verify(payment_id, valid_payment))

		for changed_value in (
			{"id": "PM-OTHER"},
			{"status": "submitted"},
			{"amount": 9999},
			{"currency": "EUR"},
		):
			with self.subTest(changed_value=changed_value):
				payment = MagicMock(id=payment_id, status="confirmed", amount=10000, currency="GBP")
				for fieldname, value in changed_value.items():
					setattr(payment, fieldname, value)
				self.assertFalse(self.verify(payment_id, payment))
