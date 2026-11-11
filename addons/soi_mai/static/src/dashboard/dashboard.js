import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

/** One-page Management Dashboard: KPIs + status of the justification requests. */
export class SoiMaiDashboard extends Component {
    static template = "soi_mai.Dashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.data = await this.orm.call("soi.mai.contract.group", "get_dashboard_data", []);
    }

    money(value) {
        return Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
    }

    show(value, suffix = "") {
        return value === null || value === undefined ? "–" : `${value}${suffix}`;
    }

    width(count, rows) {
        const max = Math.max(1, ...rows.map((row) => row.count));
        return `width: ${Math.round((count / max) * 100)}%`;
    }

    openGroups(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name || "Related Contract Groups",
            res_model: "soi.mai.contract.group",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    openContracts(domain, name) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name || "Contracts",
            res_model: "soi.mai.contract",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }
}

registry.category("actions").add("soi_mai.dashboard", SoiMaiDashboard);
