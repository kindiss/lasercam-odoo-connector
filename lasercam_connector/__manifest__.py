# -*- coding: utf-8 -*-
{
    'name': 'LaserCAM Connector',
    'version': '10.0.5.3.0',
    'category': 'Manufacturing',
    'summary': 'Export BOM data to LaserCAM nesting and import corrected quantities/times back',
    'description': """
LaserCAM Connector
==================
Adds two actions to Bills of Materials (Action / "More" menu):

* **Export to LaserCAM** — downloads ONE ZIP (mrp.bom.csv + mrp.workcenter.csv)
  with everything LaserCAM needs (BOM line ids/quantities, sheet component name,
  routing operation name, work center capacity & cycle time). Drag&drop the ZIP
  straight into laser.ucase.eu/app (it unpacks both CSV automatically).
* **Import from LaserCAM** — upload the single lasercam_fixes.zip produced by
  LaserCAM (bom_fixed.csv + workcenter_fixed.csv inside); updates component
  quantities (kg incl. waste), parts-per-sheet and sheet cutting time in place.
  No duplicates, no manual field mapping. (Individual CSV also accepted.)

* **Send directly to LaserCAM** (Odoo 11.0+) — opens LaserCAM with the selected
  BOMs, work centers and attached DXF files already loaded; **Save to Odoo** in
  LaserCAM writes the result back without any file.
* **New products from DXF** (5.0) — parts unknown to Odoo become products (copy
  of your template product) with a BOM, a "Laser <code>" work center/operation
  and the DXF attached.
* Mixed parts from several BOMs on one sheet; cutting time and kg are split back
  per product.
* Not only laser: plasma, waterjet, CNC router, textile/leather, foam, glass —
  any flat part cut from a sheet or a roll.

Works with LaserCAM (https://laser.ucase.eu) — free web nesting with
calibrated cutting times.

Note: enable **Work Orders** in Manufacturing -> Settings for Odoo to use the
imported cutting time (the work-center / operation time) in its calculations.
""",
    'author': 'UCASE MB',
    'website': 'https://laser.ucase.eu',
    'support': 'info@ucase.eu',
    'images': [
        'static/description/cover.gif',
        'static/description/mixed-nesting.gif',
        'static/description/screenshot-3.png',
        'static/description/screenshot-1.png',
        'static/description/screenshot-2.png',
    ],
    'depends': ['mrp'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/actions.xml',
        'wizard/import_wizard_view.xml',
    ],
    'post_init_hook': 'post_init',
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
