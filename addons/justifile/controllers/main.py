import json

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

OFFICER = "justifile.group_justifile_officer"


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


class JustiFileApi(http.Controller):

    # ------------------------------------------------------------------ public
    @http.route("/api/justifile/public/config", type="http", auth="public", methods=["GET"], csrf=False)
    def public_config(self):
        user = request.env.user
        signed_in = bool(user) and not user._is_public()
        return _json({
            "db": request.db,
            "signed_in": signed_in,
            "user": user.name if signed_in else None,
            "is_officer": signed_in and (user.has_group(OFFICER) or user.has_group("base.group_system")),
        })

    def _public_filters(self, kw):
        try:
            return request.env["justifile.contract"]._public_domain(
                q=(kw.get("q") or "").strip(), agency=kw.get("agency"), province=kw.get("province"),
                method=kw.get("method"), year=kw.get("year"),
            )
        except ValueError as exc:
            raise ValidationError("year must be a number") from exc

    @http.route("/api/justifile/public/filters", type="http", auth="public", methods=["GET"], csrf=False)
    def public_filter_options(self):
        """Values for the filter boxes on the public page."""
        Contract = request.env["justifile.contract"].sudo()

        def values(field, limit=None):
            rows = Contract._read_group([(field, "!=", False)], [field], ["__count"])
            rows = sorted(rows, key=lambda r: -r[1])[:limit] if limit else rows
            return sorted(str(r[0]) for r in rows)

        return _json({
            "agencies": values("agency_name", limit=300),
            "provinces": values("province"),
            "methods": values("method_name"),
            "years": sorted({int(y) for y in values("budget_year") if y != "0"}, reverse=True),
        })

    @http.route("/api/justifile/public/summary", type="http", auth="public", methods=["GET"], csrf=False)
    def public_summary(self, **kw):
        Contract = request.env["justifile.contract"].sudo()
        try:
            domain = self._public_filters(kw)
        except ValidationError as exc:
            return _error(str(exc))
        by_method = Contract._read_group(domain, ["method_name"], ["__count", "amount:sum"])
        by_month = Contract._read_group(
            domain + [("contract_date", "!=", False)], ["contract_date:month"], ["__count", "amount:sum"],
        )
        by_agency = Contract._read_group(domain, ["agency_name"], ["__count", "amount:sum"])
        return _json({
            "contract_count": sum(row[1] for row in by_method),
            "total_amount": sum(row[2] or 0 for row in by_method),
            "agency_count": len(by_agency),
            "by_method": sorted(
                [{"method": m or "Not specified", "count": c, "amount": a or 0} for m, c, a in by_method],
                key=lambda r: -r["count"],
            ),
            "by_month": [
                {"month": fields.Date.to_string(m)[:7], "count": c, "amount": a or 0} for m, c, a in by_month if m
            ],
            "by_agency": sorted(
                [{"agency": g, "count": c, "amount": a or 0} for g, c, a in by_agency], key=lambda r: -r["amount"],
            )[:10],
        })

    @http.route("/api/justifile/public/contracts", type="http", auth="public", methods=["GET"], csrf=False)
    def public_contracts(self, limit=20, offset=0, **kw):
        Contract = request.env["justifile.contract"].sudo()
        try:
            domain = self._public_filters(kw)
            limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
        except (ValidationError, ValueError):
            return _error("limit, offset and year must be numbers")
        records = Contract.search(domain, limit=limit, offset=offset)
        return _json({
            "total": Contract.search_count(domain),
            "items": [c._public_dict() for c in records],
        })

    # ----------------------------------------------------------------- officer
    @http.route("/api/justifile/officer/contracts", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_contracts(self, limit=20, **kw):
        denied = _officer_guard()
        if denied:
            return denied
        records = request.env["justifile.contract"].search([("source", "=", "manual")], limit=min(int(limit), 100))
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

    @http.route("/api/justifile/officer/vendor", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_vendor(self, tin=None, agency=None, **kw):
        """Name and past contracts for a contractor tax ID (plain facts, no rating)."""
        denied = _officer_guard()
        if denied:
            return denied
        tin = (tin or "").strip()
        if not tin:
            return _error("tin is required")
        Contract = request.env["justifile.contract"].sudo()
        latest = Contract.search([("vendor_tin", "=", tin), ("vendor_name", "!=", False)], limit=1)

        def totals(domain):
            count, amount, last = Contract._read_group(domain, [], ["__count", "amount:sum", "contract_date:max"])[0]
            return {"count": count, "amount": amount or 0, "last_date": fields.Date.to_string(last) if last else None}

        result = {"tin": tin, "name": latest.vendor_name or "", "all": totals([("vendor_tin", "=", tin)])}
        if agency:
            result["with_agency"] = totals([("vendor_tin", "=", tin), ("agency_name", "=", agency.strip())])
        return _json(result)

    @http.route("/api/justifile/officer/attachments/<int:attachment_id>", type="http", auth="public",
                methods=["GET"], csrf=False)
    def officer_attachment(self, attachment_id):
        denied = _officer_guard()
        if denied:
            return denied
        attachment = request.env["ir.attachment"].sudo().search([
            ("id", "=", attachment_id), ("res_model", "=", "justifile.contract.group"),
        ], limit=1)
        if not attachment:
            return _error("File not found", 404)
        return request.env["ir.binary"]._get_stream_from(attachment, "raw").get_response(as_attachment=True)

    @http.route("/api/justifile/officer/precheck", type="http", auth="public", methods=["POST"], csrf=False)
    def officer_precheck(self):
        denied = _officer_guard()
        if denied:
            return denied
        try:
            vals = self._contract_vals(_body())
        except ValidationError as exc:
            return _error(str(exc))
        return _json(request.env["justifile.contract.group"].precheck(
            vals["agency_name"], vals["vendor_tin"], vals["amount"], vals["contract_date"], vals["method_name"],
        ))

    @http.route("/api/justifile/officer/contracts", type="http", auth="public", methods=["POST"], csrf=False)
    def officer_create_contract(self):
        denied = _officer_guard()
        if denied:
            return denied
        try:
            data = _body()
            vals = self._contract_vals(data)
        except ValidationError as exc:
            return _error(str(exc))
        contract = request.env["justifile.contract"].create(vals)
        request.env["justifile.contract.group"].run_grouping(vals["agency_name"], vals["vendor_tin"])
        group = contract.group_id

        # Optional: the reason for separate contracts, given together with the contract.
        justified, justification_error = False, None
        reason = data.get("justification")
        if isinstance(reason, dict) and group and group.state in ("requested", "referred"):
            try:
                with request.env.cr.savepoint():
                    self._apply_justification(group, reason)
                justified = True
            except (ValidationError, UserError, AccessError) as exc:
                justification_error = str(exc)   # the contract itself stays saved
        return _json({
            "contract": contract._public_dict(),
            "group": group.sudo()._api_dict(with_contracts=True) if group else None,
            "justified": justified,
            "justification_error": justification_error,
        }, status=201)

    def _apply_justification(self, group, data):
        reasons = dict(group._fields["reason_category"].selection)
        if data.get("reason_category") not in reasons:
            raise ValidationError("reason_category must be one of: %s" % ", ".join(reasons))
        if not str(data.get("justification") or "").strip():
            raise ValidationError("Please write the justification first.")
        group.write({
            "reason_category": data["reason_category"],
            "justification": str(data["justification"]).strip(),
            "reference_doc": str(data.get("reference_doc") or "").strip()[:100] or False,
        })
        if data.get("attachments"):
            group.add_evidence(data["attachments"])
        group.action_submit_justification()

    @http.route("/api/justifile/officer/groups", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_groups(self, state=None, **kw):
        denied = _officer_guard()
        if denied:
            return denied
        domain = [("state", "=", state)] if state else []
        groups = request.env["justifile.contract.group"].search(domain, limit=100)
        return _json({"items": [g._api_dict(with_contracts=True) for g in groups]})

    @http.route("/api/justifile/officer/groups/<int:group_id>/justify", type="http", auth="public",
                methods=["POST"], csrf=False)
    def officer_justify(self, group_id):
        denied = _officer_guard()
        if denied:
            return denied
        group = request.env["justifile.contract.group"].browse(group_id).exists()
        if not group:
            return _error("Group not found", 404)
        try:
            self._apply_justification(group, _body())
        except (ValidationError, UserError, AccessError) as exc:
            request.env.cr.rollback()
            return _error(str(exc))
        return _json({"group": group._api_dict(with_contracts=True)})

    @http.route("/api/justifile/officer/reasons", type="http", auth="public", methods=["GET"], csrf=False)
    def officer_reasons(self):
        selection = request.env["justifile.contract.group"]._fields["reason_category"].selection
        return _json({"items": [{"value": v, "label": l} for v, l in selection]})
