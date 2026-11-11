import json
import logging
import os
import re
from datetime import date, datetime

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Moved from /govspending/cgdcontract (now 404); checked with a real key on 10 Nov 2026.
CGD_URL = "https://opend.data.go.th/govspending/service/egp-contract"
PAGE_SIZE = 100


def _to_float(value):
    if value in (None, "", False):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return 0.0


THAI_MONTHS = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _to_date(value):
    """Reads the API's Thai short dates ("29 ม.ค. 69", Buddhist era, 2-digit year)
    as well as ISO (yyyy-mm-dd) and dd/mm/yyyy dates."""
    if not value:
        return False
    text = str(value).strip().translate(THAI_DIGITS)
    if text in ("-", ""):
        return False
    month = next((m for abbr, m in THAI_MONTHS.items() if abbr in text), None)
    numbers = [int(p) for p in re.findall(r"\d+", text)]
    if month and len(numbers) >= 2:
        day, year = numbers[0], numbers[-1]
    elif len(numbers) >= 3:
        if numbers[0] > 31:          # yyyy-mm-dd
            year, month, day = numbers[:3]
        else:                        # dd/mm/yyyy
            day, month, year = numbers[:3]
    else:
        return False
    if year < 100:                   # 2-digit Buddhist-era year, e.g. 69 = 2569
        year += 2500
    if year > 2400:                  # Buddhist era -> Christian era
        year -= 543
    try:
        return date(year, month, day)
    except ValueError:
        return False


def _fiscal_year_be(today=None):
    today = today or date.today()
    return today.year + 543 + (1 if today.month >= 10 else 0)


class SoiMaiCgdImport(models.TransientModel):
    _name = "soi.mai.cgd.import"
    _description = "Import Contracts from CGD Open Data"

    budget_year = fields.Integer("Budget Year (B.E.)", required=True, default=lambda s: _fiscal_year_be())
    dept_code = fields.Char("Agency Code", help="CGD department code (dept_code), e.g. the municipality's code.")
    keyword = fields.Char(help="Optional keyword in the project name.")
    budget_end = fields.Float("Max Project Budget (THB)", default=500000)
    max_records = fields.Integer("Max Projects to Fetch", default=500, required=True)
    run_grouping = fields.Boolean("Check related contracts after import", default=True)
    result_message = fields.Text(readonly=True)

    @api.model
    def _get_api_key(self):
        key = os.environ.get("CGD_API_KEY") or self.env["ir.config_parameter"].sudo().get_param("soi_mai.cgd_api_key")
        if not key:
            raise UserError(self.env._(
                "No CGD API key found. Put CGD_API_KEY in the .env file (or set the "
                "system parameter soi_mai.cgd_api_key) and restart Odoo."
            ))
        return key

    @api.model
    def _fetch_page(self, api_key, params):
        try:
            response = requests.get(
                CGD_URL, params={"api-key": api_key, **params},
                headers={"api-key": api_key, "Accept": "application/json"}, timeout=60,
            )
            response.raise_for_status()
            # The API sends UTF-8 without a charset header, so decode it ourselves.
            payload = json.loads(response.content.decode("utf-8"))
        except (requests.RequestException, ValueError) as exc:
            raise UserError(self.env._("Could not read from the CGD API: %s", exc)) from exc
        if payload.get("success") is False:
            raise UserError(self.env._("The CGD API answered: %s", payload.get("message")))
        return payload.get("data") or payload.get("result") or []

    @api.model
    def _prepare_contract_vals(self, project):
        """One CGD project can hold several contracts (one per winner)."""
        vals_list = []
        project_id = str(project.get("project_id") or "")
        contracts = project.get("contract") or []
        if isinstance(contracts, dict):
            contracts = [contracts]
        for index, contract in enumerate(contracts):
            amount = _to_float(contract.get("price_agree")) or _to_float(project.get("sum_price_agree"))
            contract_no = str(contract.get("contract_no") or "")
            tin = str(contract.get("winner_tin") or "").strip()
            vals_list.append({
                "external_key": "cgd:%s:%s:%s" % (project_id, contract_no or index, tin),
                "cgd_project_id": project_id,
                "name": project.get("project_name") or project_id or "-",
                "contract_no": contract_no,
                "agency_name": project.get("dept_name") or "-",
                "agency_sub_name": project.get("dept_sub_name") or False,
                "agency_code": project.get("dept_code") or False,
                "province": project.get("province") or False,
                "vendor_tin": tin or False,
                "vendor_name": contract.get("winner_name") or contract.get("winner") or False,
                "method_name": project.get("purchase_method_name") or False,
                "amount": amount,
                "budget_amount": _to_float(project.get("project_money")),
                "contract_date": _to_date(contract.get("contract_date")) or _to_date(project.get("transaction_date"))
                                 or _to_date(project.get("announce_date")),
                "budget_year": int(_to_float(project.get("year") or project.get("budget_year"))) or False,
                "source": "cgd",
            })
        return vals_list

    @api.model
    def import_contracts(self, budget_year, dept_code=None, keyword=None, budget_end=None, max_records=500):
        api_key = self._get_api_key()
        Contract = self.env["soi.mai.contract"].sudo()
        fetched = created = updated = 0
        offset = 0
        while fetched < max_records:
            params = {"year": budget_year, "limit": min(PAGE_SIZE, max_records - fetched), "offset": offset}
            if dept_code:
                params["dept_code"] = dept_code
            if keyword:
                params["keyword"] = keyword
            if budget_end:
                params.update({"budget_start": 0, "budget_end": int(budget_end)})
            projects = self._fetch_page(api_key, params)
            if not projects:
                break
            for project in projects:
                for vals in self._prepare_contract_vals(project):
                    existing = Contract.search([("external_key", "=", vals["external_key"])], limit=1)
                    if existing:
                        existing.write(vals)
                        updated += 1
                    else:
                        Contract.create(vals)
                        created += 1
            fetched += len(projects)
            offset += len(projects)
            if len(projects) < params["limit"]:
                break
        _logger.info("CGD import: %s projects, %s contracts created, %s updated", fetched, created, updated)
        return {"projects": fetched, "created": created, "updated": updated}

    def action_import(self):
        self.ensure_one()
        stats = self.import_contracts(
            self.budget_year, self.dept_code, self.keyword, self.budget_end, self.max_records,
        )
        groups = self.env["soi.mai.contract.group"].run_grouping() if self.run_grouping else []
        self.result_message = self.env._(
            "Finished at %(time)s\nProjects read: %(projects)s\nContracts created: %(created)s\n"
            "Contracts updated: %(updated)s\nRelated-contract groups created or extended: %(groups)s",
            time=datetime.now().strftime("%Y-%m-%d %H:%M"), groups=len(groups), **stats,
        )
        if not stats["projects"]:
            self.result_message += "\n\n" + self.env._(
                "The API returned no projects for these filters. Data for the current budget "
                "year may not be published yet - try the previous year (e.g. %s).", self.budget_year - 1,
            )
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
            "view_mode": "form", "target": "new",
        }

    @api.model
    def cron_import(self):
        """Scheduled import; agency code comes from the system parameter soi_mai.cgd_dept_code."""
        icp = self.env["ir.config_parameter"].sudo()
        dept_code = icp.get_param("soi_mai.cgd_dept_code")
        if not dept_code:
            _logger.info("CGD scheduled import skipped: soi_mai.cgd_dept_code is not set")
            return
        year = _fiscal_year_be()
        for budget_year in (year - 1, year):   # the new year's data is published late
            self.import_contracts(budget_year, dept_code=dept_code, budget_end=None, max_records=1000)
        self.env["soi.mai.contract.group"].run_grouping()
