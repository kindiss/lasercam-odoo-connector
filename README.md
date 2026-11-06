# LaserCAM Connector

Round-trip your Odoo **Bills of Materials** through [LaserCAM](https://laser.ucase.eu)
sheet-metal nesting — and get **real cutting times** and nested quantities back.

One code base, **works on Odoo 9.0 through 20.0**.

## What it does

Adds two actions to any Bill of Materials:

- **Export to LaserCAM** — downloads a single ZIP with everything the nester needs
  (BOM line ids/quantities, the sheet component, the routing operation, work-center
  capacity & cycle time). If a DXF is attached to the product, it travels inside the
  ZIP too, so the part geometry loads automatically. A STEP model attached to the product is sent
  too: LaserCAM unfolds it (flat pattern, bends, threads - Pro plan) and, when
  a product has both, asks which one to nest from.
- **Import from LaserCAM** — upload the file LaserCAM produces; the module updates the
  component quantity (kg incl. cutting waste), parts-per-sheet and the sheet cutting
  time **in place**. When the BOM has no laser work center yet, it creates one
  (`Laser <code>`) plus its routing operation, inheriting the cost/hour of your
  previous work center. No duplicates, no manual field mapping.

## Supported Odoo versions

A single module that adapts at runtime to each version's Manufacturing model:

| Odoo | Manufacturing model | Status |
|------|---------------------|--------|
| 9.0 – 13.0 | `mrp.routing` + `bom.routing_id` | ✅ |
| 14.0 – 20.0 | operations on `bom.operation_ids` | ✅ |

## Install

**From the Odoo Apps Store:** search *LaserCAM Connector* and click Install
(pulls in `mrp` automatically).

**Manually:** copy the `lasercam_connector` folder into your `addons_path`, then
**Apps → Update Apps List → Install**. See [INSTALL.md](INSTALL.md) for details.

> Enable **Work Orders** in *Manufacturing → Settings* if you want Odoo to use the
> imported cutting time in its scheduling and costing.

**Odoo Online:** custom modules cannot be installed there. Use the **LaserCAM Odoo
Connector** for Windows instead - it works through the standard Odoo API with your own
API key, nothing is installed in Odoo (Odoo 14+, LaserCAM Pro plan). Download:
[LaserCAM CAD add-ins setup](https://github.com/kindiss/lasercam-cad-addins/releases/latest).

## Round-trip in short

1. Open a BOM → **Action → Export to LaserCAM** → download the ZIP.
2. Drop the ZIP into [laser.ucase.eu/app](https://laser.ucase.eu), nest the parts,
   **Optimize**, then download the fixes ZIP.
3. Back in Odoo → **Action → Import from LaserCAM** → upload it. Done.

## License

[LGPL-3](https://www.gnu.org/licenses/lgpl-3.0.html). Free and open source — use it
commercially, modify it, redistribute it.

## Support

Questions or a version quirk? Email **info@ucase.eu**.
