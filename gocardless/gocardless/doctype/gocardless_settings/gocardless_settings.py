# Copyright (c) Aerele and contributors
# License: MIT. See license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import call_hook_method, get_url

from payment_core.api.gateway import GatewayControllerMixin
from payment_core.utils import create_payment_gateway

from gocardless.gateway.client import get_client

WEBHOOK_ENDPOINT_PATH = "/api/method/gocardless.gateway.webhooks.webhooks"


class GoCardlessSettings(GatewayControllerMixin, Document):
	def validate(self):
		self.initialize_client()
		self.webhook_endpoint = get_url(WEBHOOK_ENDPOINT_PATH)

	def initialize_client(self):
		self.environment = self.get_environment()
		self.client = get_client(self.get_password("access_token"), self.environment)
		return self.client

	def on_update(self):
		create_payment_gateway(
			"GoCardless-" + self.gateway_name, settings="GoCardless Settings", controller=self.gateway_name
		)
		call_hook_method("payment_gateway_enabled", gateway="GoCardless-" + self.gateway_name)

	def get_environment(self):
		if self.use_sandbox:
			return "sandbox"
		else:
			return "live"
