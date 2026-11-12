import base64
import binascii
import os
from collections import defaultdict
from datetime import timedelta
from difflib import SequenceMatcher

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

EVIDENCE_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")
EVIDENCE_MAX_FILES = 5
EVIDENCE_MAX_BYTES = 5 * 1024 * 1024   # per file

REASONS = [
    ("urgent", "Urgent need - bidding lead time too long"),
    ("sole_supplier", "Only qualified supplier in the area"),
    ("separate_scope", "Separate, unrelated scopes of work"),
    ("budget_timing", "Budget released in separate instalments"),
    ("other", "Other (explain)"),
]


class JustiFileContractGroup(models.Model):
    _name = "justifile.contract.group"
    _description = "Related Contracts Group"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char("Reference", required=True, copy=False, default="New", readonly=True)
    agency_name = fields.Char("Agency", required=True, index=True, readonly=True)
    vendor_tin = fields.Char("Contractor Tax ID", index=True, readonly=True)
    vendor_name = fields.Char("Contractor", readonly=True)
    contract_ids = fields.One2many("justifile.contract", "group_id", "Contracts")

    contract_count = fields.Integer(compute="_compute_totals", store=True)
    total_amount = fields.Float("Combined Amount (THB)", digits=(16, 2), compute="_compute_totals", store=True)
    threshold = fields.Float("Bidding Threshold (THB)", digits=(16, 2), readonly=True)
    date_from = fields.Date("First Contract", compute="_compute_totals", store=True)
    date_to = fields.Date("Last Contract", compute="_compute_totals", store=True)
    span_days = fields.Integer("Span (days)", compute="_compute_totals", store=True)
    budget_year = fields.Integer(
        "Budget Year (B.E.)", compute="_compute_totals", store=True, index=True, aggregator=None,
        help="Thai budget year (Oct-Sep) of the first contract in the group.",
    )

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
    reference_doc = fields.Char(
        "Purchase Request Ref.", tracking=True,
        help="Number of the existing purchase request report(s) that already explain each contract, "
             "so the officer does not have to write the same thing twice.",
    )
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
            "threshold": float(icp.get_param("justifile.threshold", 500000)),
            "window_days": int(icp.get_param("justifile.window_days", 30)),
            "near_ratio": float(icp.get_param("justifile.near_ratio", 0.9)),
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
            first = rec.date_from
            rec.budget_year = (first.year + 543 + (1 if first.month >= 10 else 0)) if first else 0

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
                vals["name"] = self.env["ir.sequence"].next_by_code("justifile.contract.group") or "New"
        return super().create(vals_list)

    # ------------------------------------------------------------------ actions
    def _check_role(self, group_xmlid):
        if not (self.env.su or self.env.user.has_group(group_xmlid)
                or self.env.user.has_group("base.group_system")):
            raise AccessError(self.env._("You are not allowed to perform this step."))

    def action_submit_justification(self):
        self._check_role("justifile.group_justifile_officer")
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

    def add_evidence(self, files):
        """Attach supporting documents (list of {"name", "data"} with base64 data).
        They show up in the chatter of the group for the auditor."""
        self.ensure_one()
        self._check_role("justifile.group_justifile_officer")
        if self.state not in ("requested", "referred"):
            raise UserError(self.env._("Documents can only be added before the justification is submitted."))
        if not isinstance(files, list) or len(files) > EVIDENCE_MAX_FILES:
            raise UserError(self.env._("You can attach up to %s files.", EVIDENCE_MAX_FILES))
        vals_list = []
        for item in files:
            name = os.path.basename(str((item or {}).get("name") or "").replace("\\", "/")).strip()
            if not name or not name.lower().endswith(EVIDENCE_EXTENSIONS):
                raise UserError(self.env._("Only PDF, PNG and JPG files can be attached."))
            try:
                raw = base64.b64decode(str(item.get("data") or ""), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise UserError(self.env._("The file %s could not be read.", name)) from exc
            if not raw or len(raw) > EVIDENCE_MAX_BYTES:
                raise UserError(self.env._("%s is empty or larger than 5 MB.", name))
            vals_list.append({"name": name, "raw": raw, "res_model": self._name, "res_id": self.id})
        attachments = self.env["ir.attachment"].create(vals_list)
        if attachments:
            self.message_post(
                body=self.env._("Supporting documents added."), attachment_ids=attachments.ids,
            )
        return attachments

    def _evidence(self):
        self.ensure_one()
        return self.env["ir.attachment"].sudo().search(
            [("res_model", "=", self._name), ("res_id", "=", self.id)], order="id",
        )

    def _review(self, state):
        self._check_role("justifile.group_justifile_auditor")
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
        for rec in self:
            if not (rec.review_note or "").strip():
                raise UserError(self.env._(
                    "Please write a reviewer note first, explaining what is still needed."))
        return self._review("referred")

    # ----------------------------------------------------------------- grouping
    @api.model
    def run_grouping(self, agency_name=None, vendor_tin=None):
        """Put related direct-method contracts (same agency + contractor, inside
        the time window, combined amount above the bidding threshold) into a
        group that asks for a justification. Returns the groups created/extended."""
        params = self._get_params()
        threshold, window = params["threshold"], timedelta(days=params["window_days"])
        Contract = self.env["justifile.contract"].sudo()
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
            # 1) a new contract next to an existing group joins it; if that group was
            #    already justified, the justification has to be brought up to date
            open_groups = Group.search([("agency_name", "=", agency), ("vendor_tin", "=", tin)])
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
                    if group.state in ("submitted", "documented"):
                        group.write({"state": "requested"})
                        group.message_post(body=self.env._(
                            "A new related contract was added, so the justification needs to be updated."))

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
        related = self.env["justifile.contract"].sudo().search([
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
            "reference_doc": self.reference_doc or "",
            "review_note": self.review_note or "",
            "attachments": [{"id": a.id, "name": a.name} for a in self._evidence()],
        }
        if with_contracts:
            data["contracts"] = [c._public_dict() for c in self.contract_ids]
        return data

    # ---------------------------------------------------------------- dashboard
    @api.model
    def _dashboard_kpis(self, year=None):
        """KPI numbers, for one budget year or for all years (year=None)."""
        Contract = self.env["justifile.contract"]
        cdom = [("budget_year", "=", year)] if year else []
        gdom = [("budget_year", "=", year)] if year else []
        by_state = {state: count for state, count in self._read_group(gdom, ["state"], ["__count"])}
        total_groups = sum(by_state.values())
        documented = by_state.get("documented", 0)
        submitted = self._read_group(gdom + [("justified_date", "!=", False)], [], ["days_to_submit:avg", "__count"])[0]
        total_contracts, total_amount = Contract._read_group(cdom, [], ["__count", "amount:sum"])[0]
        direct_count, direct_amount = Contract._read_group(cdom + [("is_direct", "=", True)], [], ["__count", "amount:sum"])[0]
        return {
            "completeness_rate": round(100 * documented / total_groups, 1) if total_groups else None,
            "documented": documented,
            "total_groups": total_groups,
            "waiting": by_state.get("requested", 0) + by_state.get("referred", 0),
            "to_review": by_state.get("submitted", 0),
            "high_priority_open": self.search_count(
                gdom + [("state", "in", ("requested", "referred")), ("doc_priority", "=", "high")]),
            "avg_days_to_submit": round(submitted[0] or 0, 1) if submitted[1] else None,
            "direct_share": round(100 * direct_count / total_contracts, 1) if total_contracts else None,
            "direct_count": direct_count,
            "direct_amount": direct_amount or 0,
            "total_contracts": total_contracts,
            "total_amount": total_amount or 0,
            "near_threshold": Contract.search_count(cdom + [("near_threshold", "=", True), ("is_direct", "=", True)]),
        }

    @api.model
    def get_dashboard_data(self, year=None):
        """Numbers for the one-page Management Dashboard.
        year: Thai budget year (B.E.) to show, or None for all years. With a year, the
        previous year's KPIs are included for comparison."""
        Contract = self.env["justifile.contract"]
        params = self._get_params()
        year = int(year) if year else None
        gdom = [("budget_year", "=", year)] if year else []
        state_labels = dict(self._fields["state"].selection)
        reason_labels = dict(self._fields["reason_category"].selection)

        years = sorted({y for (y,) in Contract._read_group([("budget_year", "!=", 0)], ["budget_year"]) if y},
                       reverse=True)
        by_state = {state: count for state, count in self._read_group(gdom, ["state"], ["__count"])}
        reasons = self._read_group(gdom + [("reason_category", "!=", False)], ["reason_category"], ["__count"])
        open_by_agency = self._read_group(
            gdom + [("state", "in", ("requested", "referred"))], ["agency_name"], ["__count", "total_amount:sum"],
        )
        previous = year - 1 if year and (year - 1) in years else None
        by_year = Contract._read_group([("budget_year", "!=", 0)], ["budget_year"], ["__count", "amount:sum"])
        direct_by_year = dict(
            (y, (c, a)) for y, c, a in Contract._read_group(
                [("budget_year", "!=", 0), ("is_direct", "=", True)], ["budget_year"], ["__count", "amount:sum"])
        )
        return {
            "year": year,
            "years": years,
            "previous_year": previous,
            "kpis": self._dashboard_kpis(year),
            "previous_kpis": self._dashboard_kpis(previous) if previous else None,
            "by_state": [
                {"key": key, "label": label, "count": by_state.get(key, 0)} for key, label in state_labels.items()
            ],
            "by_reason": sorted(
                [{"key": key, "label": reason_labels.get(key, key), "count": count} for key, count in reasons],
                key=lambda r: -r["count"],
            ),
            "open_by_agency": sorted(
                [{"agency": agency, "count": count, "amount": amount or 0} for agency, count, amount in open_by_agency],
                key=lambda r: -r["count"],
            )[:5],
            "by_year": sorted([{
                "year": y, "count": c, "amount": a or 0,
                "direct_count": direct_by_year.get(y, (0, 0))[0],
                "direct_share": round(100 * direct_by_year.get(y, (0, 0))[0] / c, 1) if c else None,
            } for y, c, a in by_year if y], key=lambda r: r["year"]),
            "amount_bands": self._amount_bands(params["threshold"], year=year),
            "params": params,
        }

    @api.model
    def _amount_bands(self, threshold, bands_below=10, year=None):
        """Number of contracts per amount band, from 0 to twice the bidding threshold.
        A band covers (low, high], so a contract of exactly the threshold counts as below it."""
        Contract = self.env["justifile.contract"]
        Contract.check_access("read")
        Contract.flush_model(["amount"])
        width = threshold / bands_below
        Contract.flush_model(["budget_year"])
        query = ("SELECT (ceil(amount / %s) - 1)::int, count(*) FROM justifile_contract "
                 "WHERE amount > 0 AND amount <= %s")
        args = [width, threshold * 2]
        if year:
            query += " AND budget_year = %s"
            args.append(int(year))
        self.env.cr.execute(query + " GROUP BY 1", args)
        counts = dict(self.env.cr.fetchall())
        return [{
            "index": i, "low": i * width, "high": (i + 1) * width, "count": counts.get(i, 0),
            "just_below": i == bands_below - 1,
        } for i in range(bands_below * 2)]
