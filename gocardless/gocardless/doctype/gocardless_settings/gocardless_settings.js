// Copyright (c) Aerele and contributors
// License: MIT. See license.txt

frappe.ui.form.on("GoCardless Settings", {
	refresh(frm) {
		// the endpoint is generated server-side on save; surface it on fresh loads too
		if (!frm.doc.webhook_endpoint && !frm.is_new()) {
			frm.set_value(
				"webhook_endpoint",
				frappe.urllib.get_base_url() + "/api/method/gocardless.gateway.webhooks.webhooks"
			);
		}
	},
});
