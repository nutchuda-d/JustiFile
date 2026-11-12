{
    "name": "JustiFile - Audit-Ready Procurement Documentation",
    "version": "19.0.1.4.0",
    "summary": "Groups related direct-procurement contracts and collects the justification "
               "needed to keep them audit-ready.",
    "category": "Productivity",
    "author": "JustiFile Team (ITCS380)",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "data": [
        "security/justifile_security.xml",
        "security/ir.model.access.csv",
        "data/justifile_data.xml",
        "data/justifile_users.xml",
        "views/contract_views.xml",
        "views/contract_group_views.xml",
        "wizard/cgd_import_views.xml",
        "views/dashboard_action.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "justifile/static/src/dashboard/*",
        ],
    },
    "application": True,
    "installable": True,
}
