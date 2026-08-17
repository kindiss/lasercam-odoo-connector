# LaserCAM Connector — installation

## v9 (HPC pilot)

1. Copy the `v9/lasercam_connector/` directory into the Odoo server's `addons`
   path (e.g. `/opt/odoo/custom-addons/`; the path is shown in `odoo.conf` → `addons_path`).
2. Restart the Odoo service.
3. Odoo UI: enable developer mode (Settings → at the bottom "Activate developer mode")
   → Apps → **Update Apps List** → search for "LaserCAM" → **Install**.
4. Check: Manufacturing → Bills of Materials → select the BOMs → at the top the
   **Action / More** menu must show **Export to LaserCAM** and
   **Import from LaserCAM**.

## Usage (ZIP round-trip)

> **Note:** enable **Work Orders** in *Manufacturing → Settings* so Odoo shows
> the Work Centers menu and uses the imported cutting time in its calculations.

1. Select the BOMs → More → **Export to LaserCAM** → Download →
   **`lasercam_export.zip`** (inside: `mrp.bom.csv` + `mrp.workcenter.csv`).
2. laser.ucase.eu/app: drag&drop **`lasercam_export.zip`** (the app unpacks both
   CSV itself; the sheet fills automatically) + DXF (file names contain the product
   codes) → nesting → "⤓ Odoo fixes".
3. The app downloads **`lasercam_fixes.zip`** (inside: `bom_fixed.csv` +
   `workcenter_fixed.csv`).
4. Odoo: More → **Import from LaserCAM** → upload **`lasercam_fixes.zip`** → Import
   → result summary in the window (BOM kg + work center parts/time updated).
   *(If the ZIP is already unpacked — you can also upload the individual CSV files.)*

## Other versions (v10–19)

The Python files (controllers/, wizard/) are THE SAME (openerp/odoo namespace and
model differences are detected at runtime). Only the packaging differs:

| Version | To do |
|---|---|
| v10–11 | rename `__openerp__.py` → `__manifest__.py`; XML `<openerp>` → `<odoo>` |
| v12–19 | + in `views/actions.xml` replace the ir.values records with `binding_model_id`/`binding_view_types` fields on act_window |

Details: `LaserCAM/docs/odoo-module-planas.md`.

## Limitations

- odoo.com SaaS does not allow custom modules — for such clients the universal
  export-template path remains (without the module): `LaserCAM/docs/odoo9-bom-eksportas.md`.
- The module takes the FIRST BOM operation (laser cutting) — if the BOM has
  several operations, the others are left untouched.
