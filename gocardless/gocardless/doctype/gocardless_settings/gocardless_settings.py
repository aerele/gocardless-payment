# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import call_hook_method, cint, flt, get_url, nowdate

from payment_core.api.gateway import GatewayControllerMixin
from payment_core.utils import create_payment_gateway

from gocardless.gateway import mandates as mandate_helpers
from gocardless.gateway.client import get_client
from gocardless.gateway.constants import (
	SUPPORTED_CURRENCIES,
)

WEBHOOK_ENDPOINT_PATH = "/api/method/gocardless.gateway.webhooks.webhooks"


class GoCardlessSettings(GatewayControllerMixin, Document):
	supported_currencies = SUPPORTED_CURRENCIES

	def validate(self):
		self.initialize_client()
		self.webhook_endpoint = get_url(WEBHOOK_ENDPOINT_PATH)

	def initialize_client(self):
		self.environment = self.get_environment()
		try:
			self.client = get_client(self.get_password("access_token"), self.environment)
			return self.client
		except Exception as e:
			frappe.throw(e)

	def on_update(self):
		create_payment_gateway(
			"GoCardless-" + self.gateway_name, settings="GoCardless Settings", controller=self.gateway_name
		)
		call_hook_method("payment_gateway_enabled", gateway="GoCardless-" + self.gateway_name)

	def on_payment_request_submission(self, data):
		if data.reference_doctype != "Fees":
			customer_data = frappe.db.get_value(
				data.reference_doctype, data.reference_name, ["company", "customer_name"], as_dict=1
			)

		data = {
			"amount": flt(data.grand_total, data.precision("grand_total")),
			"title": customer_data.company,
			"description": data.subject,
			"reference_doctype": data.doctype,
			"reference_docname": data.name,
			"payer_email": data.email_to or frappe.session.user,
			"payer_name": customer_data.customer_name,
			"order_id": data.name,
			"currency": data.currency,
			"charge_date": data.transaction_date or nowdate(),
		}

		valid_mandate, next_possible_charge_date = self.check_mandate_validity(data)
		if valid_mandate is not None:
			data.update(valid_mandate)
			data["charge_date"] = max(data.get("charge_date"), next_possible_charge_date)
			self.create_payment_request(data)
			return False
		else:
			return True

	def check_mandate_validity(self, data):
		return mandate_helpers.check_mandate_validity(self, data)

	def get_environment(self):
		if self.use_sandbox:
			return "sandbox"
		else:
			return "live"

	def validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. Go Cardless does not support transactions in currency '{0}'"
				).format(currency)
			)

	def get_payment_url(self, **kwargs):
		return get_url(f"gocardless_checkout?{urlencode(kwargs)}")

	def create_payment_request(self, data):
		self.data = frappe._dict(data)

		try:
			self.integration_request = create_request_log(self.data, "Host", "GoCardless")
			return self.create_charge_on_gocardless()

		except Exception:
			frappe.log_error("Gocardless payment request failed")
			return {
				"redirect_to": frappe.redirect_to_message(
					_("Server Error"),
					_(
						"There seems to be an issue with the server's GoCardless configuration. Don't worry, in case of failure, the amount will get refunded to your account."
					),
				),
				"status": 401,
			}

	def create_charge_on_gocardless(self):
		# NOTE: charge-time status semantics are intentionally preserved from the
		# payments app, including running the completion path on submission-stage
		# statuses. Payment-status webhooks (gateway.settlement) now update the
		# Integration Request and settle the Payment Request once GoCardless
		# confirms funds; changing the charge-time behaviour itself remains a
		# tracked future improvement.
		redirect_to = self.data.get("redirect_to") or None
		redirect_message = self.data.get("redirect_message") or None

		reference_doc = frappe.get_doc(self.data.get("reference_doctype"), self.data.get("reference_docname"))
		self.initialize_client()

		try:
			payment = self.client.payments.create(
				params={
					"amount": cint(reference_doc.grand_total * 100),
					"charge_date": self.data.get("charge_date"),
					"currency": reference_doc.currency,
					"links": {"mandate": self.data.get("mandate")},
					"metadata": {
						"reference_doctype": reference_doc.doctype,
						"reference_document": reference_doc.name,
					},
				},
				headers={
					"Idempotency-Key": self.data.get("reference_docname"),
				},
			)

			# persist the provider payment id so webhook events can be matched
			self.data["payment_id"] = payment.id
			self.integration_request.db_set("data", frappe.as_json(self.data), update_modified=False)

			if payment.status in ("pending_submission", "pending_customer_approval", "submitted"):
				self.integration_request.db_set("status", "Authorized", update_modified=False)
				self.flags.status_changed_to = "Completed"
				self.integration_request.db_set("output", payment.status, update_modified=False)

			elif payment.status in ("confirmed", "paid_out"):
				self.integration_request.db_set("status", "Completed", update_modified=False)
				self.flags.status_changed_to = "Completed"
				self.integration_request.db_set("output", payment.status, update_modified=False)

			elif payment.status in ("cancelled", "customer_approval_denied", "charged_back"):
				self.integration_request.db_set("status", "Cancelled", update_modified=False)
				frappe.log_error("Gocardless payment cancelled")
				self.integration_request.db_set("error", payment.status, update_modified=False)
			else:
				self.integration_request.db_set("status", "Failed", update_modified=False)
				frappe.log_error("Gocardless payment failed")
				self.integration_request.db_set("error", payment.status, update_modified=False)

		except Exception:
			frappe.log_error("GoCardless Payment Error")

		if self.flags.status_changed_to == "Completed":
			status = "Completed"
			if "reference_doctype" in self.data and "reference_docname" in self.data:
				custom_redirect_to = None
				try:
					custom_redirect_to = frappe.get_doc(
						self.data.get("reference_doctype"), self.data.get("reference_docname")
					).run_method("on_payment_authorized", self.flags.status_changed_to)
				except Exception:
					frappe.log_error("Gocardless redirect failed")

				if custom_redirect_to:
					redirect_to = custom_redirect_to

			redirect_url = redirect_to
		else:
			status = "Error"
			redirect_url = "payment-failed"

			if redirect_message:
				redirect_url += "&" + urlencode({"redirect_message": redirect_message})

			redirect_url = get_url(redirect_url)

		return {"redirect_to": redirect_url, "status": status}
