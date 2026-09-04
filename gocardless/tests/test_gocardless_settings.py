# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from gocardless.gocardless.doctype.gocardless_settings.gocardless_settings import GoCardlessSettings


def make_settings(gateway_name: str | None = None, use_sandbox: bool = True) -> GoCardlessSettings:
	"""Insert Base Settings with dummy credentials; the SDK constructor makes no network call."""
	if gateway_name is None:
		gateway_name = f"_Test GC {frappe.generate_hash(length=8)}"

	return frappe.get_doc(
		{
			"doctype": "GoCardless Settings",
			"gateway_name": gateway_name,
			"access_token": "sandbox_dummy_token",
			"use_sandbox": use_sandbox,
		}
	).insert(ignore_permissions=True)


class TestGoCardlessSettings(FrappeTestCase):
	def test_get_environment(self):
		sandbox = frappe.get_doc({"doctype": "GoCardless Settings", "use_sandbox": 1})
		live = frappe.get_doc({"doctype": "GoCardless Settings", "use_sandbox": 0})
		self.assertEqual(sandbox.get_environment(), "sandbox")
		self.assertEqual(live.get_environment(), "live")

	def test_initialize_client_fails_without_access_token(self):
		settings = frappe.get_doc({"doctype": "GoCardless Settings", "gateway_name": "_Test GC No Token"})
		self.assertRaises(frappe.ValidationError, settings.initialize_client)

	def test_on_update_registers_payment_gateway(self):
		if "erpnext" not in frappe.get_installed_apps():
			self.skipTest("erpnext not installed on this site")

		settings = make_settings()
		gateway = frappe.db.get_value(
			"Payment Gateway",
			"GoCardless-" + settings.gateway_name,
			["gateway_settings", "gateway_controller"],
			as_dict=1,
		)
		self.assertIsNotNone(gateway)
		self.assertEqual(gateway.gateway_settings, "GoCardless Settings")
		self.assertEqual(gateway.gateway_controller, settings.gateway_name)
