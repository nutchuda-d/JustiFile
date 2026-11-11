{
    "name": "Soi Mai - Procurement Documentation Check",
    "version": "19.0.1.1.0",
    "summary": "Groups related direct-procurement contracts and collects the justification "
               "needed to keep them audit-ready.",
    "category": "Productivity",
    "author": "Soi Mai Team (ITCS380)",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "data": [
        "security/soi_mai_security.xml",
        "security/ir.model.access.csv",
        "data/soi_mai_data.xml",
        "data/soi_mai_users.xml",
        "views/contract_views.xml",
        "views/contract_group_views.xml",
        "wizard/cgd_import_views.xml",
        "views/dashboard_action.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "soi_mai/static/src/dashboard/*",
        ],
    },
    "application": True,
    "installable": True,
}
