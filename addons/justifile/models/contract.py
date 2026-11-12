from odoo import api, fields, models

DIRECT_KEYWORDS = ("เฉพาะเจาะจง", "specific", "direct")


class JustiFileContract(models.Model):
    _name = "justifile.contract"
    _description = "Procurement Contract"
    _order = "contract_date desc, id desc"

    name = fields.Char("Project Name", required=True)
    external_key = fields.Char(
        "External Key", index=True, copy=False,
        help="Unique key of the record in the source system (used to avoid duplicates on import).",
    )
    cgd_project_id = fields.Char("e-GP Project ID", index=True)
    contract_no = fields.Char("Contract No.")
    agency_name = fields.Char("Agency", required=True, index=True)
    agency_sub_name = fields.Char("Sub-agency")
    agency_code = fields.Char("Agency Code")
    province = fields.Char()
    vendor_tin = fields.Char("Contractor Tax ID", index=True)
    vendor_name = fields.Char("Contractor")
    method_name = fields.Char("Procurement Method")
    is_direct = fields.Boolean(
        "Direct Method", compute="_compute_is_direct", store=True,
        help="Procured with the specific/direct method (วิธีเฉพาะเจาะจง).",
    )
    amount = fields.Float("Contract Amount (THB)", digits=(16, 2))
    budget_amount = fields.Float("Project Budget (THB)", digits=(16, 2))
    contract_date = fields.Date(index=True)
    budget_year = fields.Integer("Budget Year (B.E.)", aggregator=None)
    source = fields.Selection(
        [("cgd", "CGD Open Data"), ("manual", "Entered by Officer")],
        default="manual", required=True,
    )
    near_threshold = fields.Boolean(
        "Close to Threshold", compute="_compute_near_threshold", store=True,
        help="Amount is just below the threshold that requires competitive bidding.",
    )
    group_id = fields.Many2one(
        "justifile.contract.group", "Related Contracts Group",
        ondelete="set null", index=True, copy=False,
    )

    _external_key_uniq = models.Constraint(
        "unique(external_key)", "This contract has already been imported.",
    )

    @api.depends("method_name")
    def _compute_is_direct(self):
        for rec in self:
            method = (rec.method_name or "").lower()
            rec.is_direct = any(k in method for k in DIRECT_KEYWORDS)

    @api.depends("amount")
    def _compute_near_threshold(self):
        params = self.env["justifile.contract.group"]._get_params()
        low = params["threshold"] * params["near_ratio"]
        for rec in self:
            rec.near_threshold = low <= rec.amount <= params["threshold"]

    @api.model
    def _public_domain(self, q=None, agency=None, province=None, method=None, year=None):
        """Search domain for the public page filters (all optional)."""
        domain = []
        if q:
            domain += ["|", "|", ("name", "ilike", q), ("agency_name", "ilike", q), ("vendor_name", "ilike", q)]
        if agency:
            domain.append(("agency_name", "=", agency))
        if province:
            domain.append(("province", "=", province))
        if method:
            domain.append(("method_name", "=", method))
        if year:
            domain.append(("budget_year", "=", int(year)))
        return domain

    def _public_dict(self):
        """Fields that are already open data; safe for the public page."""
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "agency": self.agency_name,
            "province": self.province or "",
            "contractor": self.vendor_name or "",
            "method": self.method_name or "",
            "amount": self.amount,
            "contract_date": fields.Date.to_string(self.contract_date) if self.contract_date else None,
            "budget_year": self.budget_year,
            "source": self.source,
        }
