import json

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

OFFICER = "soi_mai.group_soi_mai_officer"


def _json(data, status=200):
    return request.make_json_response(data, status=status)


def _error(message, status=400):
    return _json({"error": message}, status=status)


def _body():
    if "application/json" not in (request.httprequest.content_type or ""):
        raise ValidationError("Content-Type must be application/json")
    try:
        return json.loads(request.httprequest.get_data(as_text=True) or "{}")
    except ValueError as exc:
        raise ValidationError("Invalid JSON body") from exc


def _officer_guard():
    """Returns an error response when the caller is not a logged-in officer."""
    user = request.env.user
    if not user or user._is_public():
        return _error("Please sign in.", 401)
    if not (user.has_group(OFFICER) or user.has_group("base.group_system")):
        return _error("This page is for procurement officers.", 403)
    return None


class SoiMaiApi(http.Controller):

    # ------------------------------------------------------------------ public
    @http.route("/api/soi_mai/public/config", type="http", auth="public", methods=["GET"], csrf=False)
    def public_config(self):
        user = request.env.user
        signed_in = bool(user) and not user._is_public()
        return _json({
            "db": request.db,
            "signed_in": signed_in,
            "user": user.name if signed_in else None,
            "is_officer": signed_in and (user.has_group(OFFICER) or user.has_group("base.group_system")),
        })

    @http.route("/api/soi_mai/public/summary", type="http", auth="public", methods=["GET"], csrf=False)
    def public_summary(self):
        Contract = request.env["soi.mai.contract"].sudo()
        by_method = Contract._read_group([], ["method_name"], ["__count", "amount:sum"])
        by_month = Contract._read_group(
            [("contract_date", "!=", False)], ["contract_date:month"], ["__count", "amount:sum"],
        )
        by_agency = Contract._read_group([], ["agency_name"], ["__count", "amount:sum"])
        total = sum(row[1] for row in by_method)
        return _json({
            "contract_count": total,
            "total_amount": sum(row[2] for row in by_method),
            "agency_count": len(by_agency),
            "by_method": sorted(
                [{"method": m or "Not specified", "count": c, "amount": a} for m, c, a in by_method],
                key=lambda r: -r["count"],
            ),
            "by_month": [
                {"month": fields.Date.to_string(m)[:7], "count": c, "amount": a} for m, c, a in by_month if m
            ],
            "by_agency": sorted(
                [{"agency": g, "count": c, "amount": a} for g, c, a in by_agency], key=lambda r: -r["amount"],
            )[:10],
        })

    @http.route("/api/soi_mai/public/contracts", type="http", auth="public", methods=["GET"], csrf=False)
    def public_contracts(self, q=None, limit=20, offset=0, **kw):
        Contract = request.env["soi.mai.contract"].sudo()
        domain = []
        if q:
            domain = ["|", "|", ("name", "ilike", q), ("agency_name", "ilike", q), ("vendor_name", "ilike", q)]
        try:
            limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
        except ValueError:
            return _error("limit and offset must be numbers")
        records = Contract.search(domain, limit=limit, offset=offset)
        return _json({
            "total": Contract.search_count(domain),
            "items": [c._public_dict() for c in records],
        })

    # ----------------------------------------------------------------- officer
    @http.route("/api/soi_mai/officer/contracts", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_contracts(self, limit=20, **kw):
        denied = _officer_guard()
        if denied:
            return denied
        records = request.env["soi.mai.contract"].search([("source", "=", "manual")], limit=min(int(limit), 100))
        return _json({"items": [dict(c._public_dict(), group=c.group_id.name or None) for c in records]})

    def _contract_vals(self, data):
        required = ["name", "agency_name", "vendor_tin", "vendor_name", "method_name", "amount", "contract_date"]
        missing = [f for f in required if data.get(f) in (None, "")]
        if missing:
            raise ValidationError("Missing fields: %s" % ", ".join(missing))
        try:
            amount = float(data["amount"])
            contract_date = fields.Date.to_date(data["contract_date"])
        except (TypeError, ValueError) as exc:
            raise ValidationError("amount must be a number and contract_date must be YYYY-MM-DD") from exc
        if amount <= 0:
            raise ValidationError("amount must be greater than zero")
        return {
            "name": str(data["name"]).strip(),
            "agency_name": str(data["agency_name"]).strip(),
            "vendor_tin": str(data["vendor_tin"]).strip(),
            "vendor_name": str(data["vendor_name"]).strip(),
            "method_name": str(data["method_name"]).strip(),
            "contract_no": str(data.get("contract_no") or "").strip() or False,
            "amount": amount,
            "contract_date": contract_date,
            "budget_year": contract_date.year + 543 + (1 if contract_date.month >= 10 else 0),
            "source": "manual",
        }

    @http.route("/api/soi_mai/officer/precheck", type="http", auth="public", methods=["POST"], csrf=False)
    def officer_precheck(self):
        denied = _officer_guard()
        if denied:
            return denied
        try:
            vals = self._contract_vals(_body())
        except ValidationError as exc:
            return _error(str(exc))
        return _json(request.env["soi.mai.contract.group"].precheck(
            vals["agency_name"], vals["vendor_tin"], vals["amount"], vals["contract_date"], vals["method_name"],
        ))

    @http.route("/api/soi_mai/officer/contracts", type="http", auth="public", methods=["POST"], csrf=False)
    def officer_create_contract(self):
        denied = _officer_guard()
        if denied:
            return denied
        try:
            vals = self._contract_vals(_body())
        except ValidationError as exc:
            return _error(str(exc))
        contract = request.env["soi.mai.contract"].create(vals)
        request.env["soi.mai.contract.group"].run_grouping(vals["agency_name"], vals["vendor_tin"])
        group = contract.group_id
        return _json({
            "contract": contract._public_dict(),
            "group": group.sudo()._api_dict(with_contracts=True) if group else None,
        }, status=201)

    @http.route("/api/soi_mai/officer/groups", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_groups(self, state=None, **kw):
        denied = _officer_guard()
        if denied:
            return denied
        domain = [("state", "=", state)] if state else []
        groups = request.env["soi.mai.contract.group"].search(domain, limit=100)
        return _json({"items": [g._api_dict(with_contracts=True) for g in groups]})

    @http.route("/api/soi_mai/officer/groups/<int:group_id>/justify", type="http", auth="public",
                methods=["POST"], csrf=False)
    def officer_justify(self, group_id):
        denied = _officer_guard()
        if denied:
            return denied
        group = request.env["soi.mai.contract.group"].browse(group_id).exists()
        if not group:
            return _error("Group not found", 404)
        try:
            data = _body()
            reasons = dict(group._fields["reason_category"].selection)
            if data.get("reason_category") not in reasons:
                raise ValidationError("reason_category must be one of: %s" % ", ".join(reasons))
            group.write({
                "reason_category": data["reason_category"],
                "justification": str(data.get("justification") or "").strip(),
            })
            group.action_submit_justification()
        except (ValidationError, UserError, AccessError) as exc:
            request.env.cr.rollback()
            return _error(str(exc))
        return _json({"group": group._api_dict(with_contracts=True)})

    @http.route("/api/soi_mai/officer/reasons", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_reasons(self):
        selection = request.env["soi.mai.contract.group"]._fields["reason_category"].selection
        return _json({"items": [{"value": v, "label": l} for v, l in selection]})
