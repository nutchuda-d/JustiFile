from collections import defaultdict
from datetime import timedelta
from difflib import SequenceMatcher

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

REASONS = [
    ("urgent", "Urgent need - bidding lead time too long"),
    ("sole_supplier", "Only qualified supplier in the area"),
    ("separate_scope", "Separate, unrelated scopes of work"),
    ("budget_timing", "Budget released in separate instalments"),
    ("other", "Other (explain)"),
]


class SoiMaiContractGroup(models.Model):
    _name = "soi.mai.contract.group"
    _description = "Related Contracts Group"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char("Reference", required=True, copy=False, default="New", readonly=True)
    agency_name = fields.Char("Agency", required=True, index=True, readonly=True)
    vendor_tin = fields.Char("Contractor Tax ID", index=True, readonly=True)
    vendor_name = fields.Char("Contractor", readonly=True)
    contract_ids = fields.One2many("soi.mai.contract", "group_id", "Contracts")

    contract_count = fields.Integer(compute="_compute_totals", store=True)
    total_amount = fields.Float("Combined Amount (THB)", digits=(16, 2), compute="_compute_totals", store=True)
    threshold = fields.Float("Bidding Threshold (THB)", digits=(16, 2), readonly=True)
    date_from = fields.Date("First Contract", compute="_compute_totals", store=True)
    date_to = fields.Date("Last Contract", compute="_compute_totals", store=True)
    span_days = fields.Integer("Span (days)", compute="_compute_totals", store=True)

    check_combined = fields.Boolean("Combined amount above threshold", compute="_compute_checks", store=True)
    check_near_threshold = fields.Boolean("Amounts close to threshold", compute="_compute_checks", store=True)
    check_similar_names = fields.Boolean("Similar project names", compute="_compute_checks", store=True)
    check_recurring = fields.Boolean("Recurring with same contractor", compute="_compute_checks", store=True)
    check_count = fields.Integer("Checks Matched", compute="_compute_checks", store=True)
    doc_priority = fields.Selection(
        [("normal", "Normal"), ("high", "High")], "Documentation Priority",
        compute="_compute_checks", store=True,
    )

    state = fields.Selection(
        [
            ("requested", "Justification Requested"),
            ("submitted", "Justification Submitted"),
            ("documented", "Documented"),
            ("referred", "Referred for Second Opinion"),
        ],
        default="requested", required=True, tracking=True, index=True,
        group_expand="_group_expand_state",
    )
    reason_category = fields.Selection(REASONS, "Reason", tracking=True)
    justification = fields.Text(tracking=True)
    justified_by = fields.Many2one("res.users", "Submitted By", readonly=True)
    justified_date = fields.Datetime("Submitted On", readonly=True)
    reviewer_id = fields.Many2one("res.users", "Reviewed By", readonly=True)
    review_date = fields.Datetime("Reviewed On", readonly=True)
    review_note = fields.Text("Reviewer Note")
    days_to_submit = fields.Float(
        "Days to Submit", compute="_compute_days", store=True, aggregator="avg",
        help="Days between the request and the officer's justification.",
    )
    is_documented = fields.Integer(
        "Documented (count)", compute="_compute_days", store=True,
        help="1 when documented, 0 otherwise. Average it to get the documentation completeness rate.",
    )

    # ------------------------------------------------------------------ helpers
    @api.model
    def _get_params(self):
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "threshold": float(icp.get_param("soi_mai.threshold", 500000)),
            "window_days": int(icp.get_param("soi_mai.window_days", 30)),
            "near_ratio": float(icp.get_param("soi_mai.near_ratio", 0.9)),
        }

    @api.model
    def _group_expand_state(self, states, domain):
        return [key for key, _label in self._fields["state"].selection]

    # ----------------------------------------------------------------- computes
    @api.depends("contract_ids.amount", "contract_ids.contract_date")
    def _compute_totals(self):
        for rec in self:
            contracts = rec.contract_ids
            dates = [d for d in contracts.mapped("contract_date") if d]
            rec.contract_count = len(contracts)
            rec.total_amount = sum(contracts.mapped("amount"))
            rec.date_from = min(dates) if dates else False
            rec.date_to = max(dates) if dates else False
            rec.span_days = (max(dates) - min(dates)).days if dates else 0

    @api.depends("contract_ids.amount", "contract_ids.name", "contract_ids.near_threshold",
                 "total_amount", "threshold", "agency_name", "vendor_tin")
    def _compute_checks(self):
        for rec in self:
            contracts = rec.contract_ids
            names = [n for n in contracts.mapped("name") if n]
            similar = any(
                SequenceMatcher(None, a, b).ratio() >= 0.6
                for i, a in enumerate(names) for b in names[i + 1:]
            )
            others = self.search_count([
                ("agency_name", "=", rec.agency_name),
                ("vendor_tin", "=", rec.vendor_tin),
                ("id", "!=", rec.id or 0),
            ]) if rec.vendor_tin else 0
            rec.check_combined = bool(rec.threshold) and rec.total_amount > rec.threshold
            rec.check_near_threshold = any(contracts.mapped("near_threshold"))
            rec.check_similar_names = similar
            rec.check_recurring = others > 0
            rec.check_count = sum([rec.check_combined, rec.check_near_threshold,
                                   rec.check_similar_names, rec.check_recurring])
            rec.doc_priority = "high" if rec.check_count >= 3 else "normal"

    @api.depends("state", "justified_date", "create_date")
    def _compute_days(self):
        for rec in self:
            if rec.justified_date and rec.create_date:
                rec.days_to_submit = (rec.justified_date - rec.create_date).total_seconds() / 86400
            else:
                rec.days_to_submit = 0
            rec.is_documented = 1 if rec.state == "documented" else 0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("soi.mai.contract.group") or "New"
        return super().create(vals_list)

    # ------------------------------------------------------------------ actions
    def _check_role(self, group_xmlid):
        if not (self.env.su or self.env.user.has_group(group_xmlid)
                or self.env.user.has_group("base.group_system")):
            raise AccessError(self.env._("You are not allowed to perform this step."))

    def action_submit_justification(self):
        self._check_role("soi_mai.group_soi_mai_officer")
        for rec in self:
            if rec.state not in ("requested", "referred"):
                raise UserError(self.env._("A justification has already been submitted."))
            if not rec.reason_category or not (rec.justification or "").strip():
                raise UserError(self.env._("Please select a reason and write the justification first."))
            rec.write({
                "state": "submitted",
                "justified_by": self.env.user.id,
                "justified_date": fields.Datetime.now(),
            })
        return True

    def _review(self, state):
        self._check_role("soi_mai.group_soi_mai_auditor")
        for rec in self:
            if rec.state != "submitted":
                raise UserError(self.env._("Only submitted justifications can be reviewed."))
        self.write({
            "state": state,
            "reviewer_id": self.env.user.id,
            "review_date": fields.Datetime.now(),
        })
        return True

    def action_mark_documented(self):
        return self._review("documented")

    def action_refer(self):
        return self._review("referred")

    # ----------------------------------------------------------------- grouping
    @api.model
    def run_grouping(self, agency_name=None, vendor_tin=None):
        """Put related direct-method contracts (same agency + contractor, inside
        the time window, combined amount above the bidding threshold) into a
        group that asks for a justification. Returns the groups created/extended."""
        params = self._get_params()
        threshold, window = params["threshold"], timedelta(days=params["window_days"])
        Contract = self.env["soi.mai.contract"].sudo()
        Group = self.sudo()

        domain = [
            ("is_direct", "=", True), ("group_id", "=", False),
            ("amount", ">", 0), ("amount", "<=", threshold),
            ("vendor_tin", "!=", False), ("contract_date", "!=", False),
        ]
        if agency_name:
            domain.append(("agency_name", "=", agency_name))
        if vendor_tin:
            domain.append(("vendor_tin", "=", vendor_tin))

        buckets = defaultdict(lambda: Contract)
        for contract in Contract.search(domain, order="contract_date, id"):
            buckets[(contract.agency_name, contract.vendor_tin)] |= contract

        touched = Group
        for (agency, tin), contracts in buckets.items():
            # 1) extend groups that are still waiting for a justification
            open_groups = Group.search([
                ("agency_name", "=", agency), ("vendor_tin", "=", tin), ("state", "=", "requested"),
            ])
            for group in open_groups:
                if not group.date_from:
                    continue
                joiners = contracts.filtered(
                    lambda c: group.date_from - window <= c.contract_date <= group.date_to + window
                )
                if joiners:
                    joiners.write({"group_id": group.id})
                    contracts -= joiners
                    touched |= group

            # 2) look for new groups among what is left
            rest = contracts.sorted(lambda c: (c.contract_date, c.id))
            i = 0
            while i < len(rest):
                start = rest[i]
                cluster = rest.filtered(
                    lambda c: start.contract_date <= c.contract_date <= start.contract_date + window
                )
                if len(cluster) >= 2 and sum(cluster.mapped("amount")) > threshold:
                    group = Group.create({
                        "agency_name": agency,
                        "vendor_tin": tin,
                        "vendor_name": cluster[0].vendor_name,
                        "threshold": threshold,
                    })
                    cluster.write({"group_id": group.id})
                    touched |= group
                    rest -= cluster
                else:
                    i += 1
        return touched

    @api.model
    def action_run_grouping(self):
        groups = self.run_grouping()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("Related contracts check finished"),
                "message": self.env._("%s group(s) created or extended.", len(groups)),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    @api.model
    def precheck(self, agency_name, vendor_tin, amount, contract_date, method_name=None):
        """Early reminder used by the officer page before a contract is saved."""
        params = self._get_params()
        window = timedelta(days=params["window_days"])
        result = {
            "threshold": params["threshold"], "window_days": params["window_days"],
            "related": [], "combined_amount": amount, "needs_justification": False,
        }
        direct = any(k in (method_name or "").lower() for k in ("เฉพาะเจาะจง", "specific", "direct"))
        if not (vendor_tin and contract_date and direct):
            return result
        related = self.env["soi.mai.contract"].sudo().search([
            ("agency_name", "=", agency_name), ("vendor_tin", "=", vendor_tin),
            ("is_direct", "=", True), ("amount", "<=", params["threshold"]),
            ("contract_date", ">=", contract_date - window),
            ("contract_date", "<=", contract_date + window),
        ], order="contract_date")
        combined = amount + sum(related.mapped("amount"))
        result.update({
            "related": [c._public_dict() for c in related],
            "combined_amount": combined,
            "needs_justification": bool(related) and combined > params["threshold"],
        })
        return result

    def _api_dict(self, with_contracts=False):
        self.ensure_one()
        labels = dict(self._fields["state"].selection)
        checks = [
            self._fields[f].string
            for f in ("check_combined", "check_near_threshold", "check_similar_names", "check_recurring")
            if self[f]
        ]
        data = {
            "id": self.id, "ref": self.name, "agency": self.agency_name,
            "contractor": self.vendor_name or "", "contractor_tin": self.vendor_tin or "",
            "contract_count": self.contract_count, "total_amount": self.total_amount,
            "threshold": self.threshold,
            "date_from": fields.Date.to_string(self.date_from) if self.date_from else None,
            "date_to": fields.Date.to_string(self.date_to) if self.date_to else None,
            "state": self.state, "state_label": labels.get(self.state),
            "priority": self.doc_priority, "checks": checks,
            "reason_category": self.reason_category or "",
            "justification": self.justification or "",
            "review_note": self.review_note or "",
        }
        if with_contracts:
            data["contracts"] = [c._public_dict() for c in self.contract_ids]
        return data
