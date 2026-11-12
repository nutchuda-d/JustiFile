import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

/** One-page Management Dashboard: KPIs + status of the justification requests. */
export class JustiFileDashboard extends Component {
    static template = "justifile.Dashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null, year: "" });
        onWillStart(() => this.load());
    }

    async load() {
        const year = this.state.year ? parseInt(this.state.year) : false;
        this.state.data = await this.orm.call("justifile.contract.group", "get_dashboard_data", [], { year });
    }

    onYearChange(ev) {
        this.state.year = ev.target.value;
        this.load();
    }

    /** Domain part that limits a drill-down to the selected budget year. */
    yearDomain() {
        return this.state.data && this.state.data.year ? [["budget_year", "=", this.state.data.year]] : [];
    }

    /** "vs 2568: 76% (+6)" under a KPI, when a year with a previous year is selected. */
    compare(key, suffix = "", lowerIsBetter = false) {
        const data = this.state.data;
        if (!data || !data.previous_kpis) {
            return "";
        }
        const now = data.kpis[key];
        const before = data.previous_kpis[key];
        if (before === null || before === undefined) {
            return `vs ${data.previous_year}: –`;
        }
        if (now === null || now === undefined) {
            return `vs ${data.previous_year}: ${before}${suffix}`;
        }
        const diff = Math.round((now - before) * 10) / 10;
        const sign = diff > 0 ? "+" : "";
        return `vs ${data.previous_year}: ${before}${suffix} (${sign}${diff})`;
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

    yearWidth(row) {
        const max = Math.max(1, ...this.state.data.by_year.map((r) => r.amount));
        return `width: ${Math.max(1, Math.round((row.amount / max) * 100))}%`;
    }

    openYear(row) {
        this.state.year = String(row.year);
        this.load();
    }

    bandHeight(count) {
        const max = Math.max(1, ...this.state.data.amount_bands.map((band) => band.count));
        return `height: ${count ? Math.max(2, Math.round((count / max) * 100)) : 0}%`;
    }

    bandLabel(band) {
        return `${Math.round(band.high / 1000)}k`;
    }

    bandTitle(band) {
        return `${this.money(band.low)} – ${this.money(band.high)} THB: ${band.count} contract(s)`;
    }

    openBand(band) {
        this.openContracts(
            [["amount", ">", band.low], ["amount", "<=", band.high]],
            `Contracts ${this.money(band.low)} – ${this.money(band.high)} THB`
        );
    }

    openGroups(domain, name) {
        domain = [...domain, ...this.yearDomain()];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name || "Related Contract Groups",
            res_model: "justifile.contract.group",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }

    openContracts(domain, name) {
        domain = [...domain, ...this.yearDomain()];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name || "Contracts",
            res_model: "justifile.contract",
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }
}

registry.category("actions").add("justifile.dashboard", JustiFileDashboard);
