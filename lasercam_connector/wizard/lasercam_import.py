# -*- coding: utf-8 -*-
"""Import from LaserCAM: the wizard accepts `lasercam_fixes.zip` (the LaserCAM
"Odoo fixes" output with `bom_fixed.csv` + `workcenter_fixed.csv` inside) OR
individual CSV files (manual fallback), and updates via the ORM:

* BOM component quantity (kg/unit incl. waste) — by `bom_line_ids/.id`
* "parts per sheet" + sheet time — by the wc file External ID; the target
  MODEL is detected via env.ref: v9 → mrp.workcenter (time_cycle HOURS,
  capacity_per_cycle), v10+ → mrp.routing.workcenter (time_cycle_manual MIN.)
  plus its work center's capacity/default_capacity.

Writing via the ORM (not the Odoo import machinery) → no header-mapping issues.
The ZIP is unpacked with Python `zipfile` (stdlib) — no extra installs.
"""
import base64
import io
import re
import zipfile

try:
    from odoo import api, fields, models
    from odoo.exceptions import UserError
except ImportError:
    from openerp import api, fields, models
    from openerp.exceptions import UserError

# v14+ has no api.multi (methods are multi by default) — no-op fallback.
_multi = getattr(api, 'multi', lambda f: f)


def _parse_csv(text):
    """Minimal RFC4180 parser (py2/py3). Fixed files — comma + period."""
    rows, row, cur, in_q = [], [], u'', False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_q:
            if ch == u'"':
                if i + 1 < len(text) and text[i + 1] == u'"':
                    cur += u'"'
                    i += 1
                else:
                    in_q = False
            else:
                cur += ch
        elif ch == u'"':
            in_q = True
        elif ch == u',':
            row.append(cur)
            cur = u''
        elif ch in (u'\n', u'\r'):
            if ch == u'\r' and i + 1 < len(text) and text[i + 1] == u'\n':
                i += 1
            row.append(cur)
            cur = u''
            if len(row) > 1 or row[0] != u'':
                rows.append(row)
            row = []
        else:
            cur += ch
        i += 1
    if cur != u'' or row:
        row.append(cur)
        if len(row) > 1 or row[0] != u'':
            rows.append(row)
    return rows


class LaserCAMImportWizard(models.TransientModel):
    _name = 'lasercam.import.wizard'
    _description = 'Import from LaserCAM'

    zip_file = fields.Binary('lasercam_fixes.zip')
    zip_filename = fields.Char('ZIP file name')
    bom_file = fields.Binary('bom_fixed.csv (optional)')
    bom_filename = fields.Char('BOM file name')
    wc_file = fields.Binary('workcenter_fixed.csv (optional)')
    wc_filename = fields.Char('WC file name')
    create_file = fields.Binary('routing_create.csv (optional)')
    create_filename = fields.Char('Create file name')
    result = fields.Text('Result', readonly=True)

    def _to_text(self, raw):
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        return raw.decode('utf-8')

    def _cap_field(self, wc):
        for f in ('capacity_per_cycle', 'default_capacity', 'capacity'):
            if f in wc._fields:
                return f
        return None

    def _relabel(self, old_name, code):
        u"""New name for a copied routing / work center: "Laser <code>" + whatever text
        followed the old code in the old name. E.g. "00641 Pjovimas" -> "Laser 00692
        Pjovimas"; "Lazeris 00641" -> "Laser 00692". No >=3-digit run -> "Laser <code>"."""
        old_name = old_name or u''
        runs = list(re.finditer(r'\d{3,}', old_name))
        suffix = old_name[runs[-1].end():] if runs else u''
        return (u'Laser %s%s' % (code, suffix)).strip()

    def _op_lines(self, routing):
        u"""Routing operations o2m: v9-11 `workcenter_lines`, v12-13 `operation_ids`."""
        if routing and 'workcenter_lines' in routing._fields:
            return routing.workcenter_lines
        if routing and 'operation_ids' in routing._fields:
            return routing.operation_ids
        return routing[:0] if routing else routing

    def _apply_wc_capacity(self, wc, product, cap_raw):
        """v9-17: capacity is a field ON mrp.workcenter (set in _wc_vals). v18+
        removed it — capacity lives per product in mrp.workcenter.capacity
        ('Product Capacities' tab). Here we upsert that record so the parts-per-
        sheet count is visible and used. No-op where the workcenter has the field."""
        if not wc or not product:
            return
        cap_raw = (u'%s' % (cap_raw or u'')).replace(',', '.')
        if not cap_raw:
            return
        try:
            cap = float(cap_raw)
        except ValueError:
            return
        if self._cap_field(wc):
            return  # v9-17 — already set on the workcenter itself
        if 'mrp.workcenter.capacity' not in self.env:
            return
        Cap = self.env['mrp.workcenter.capacity']
        rec = Cap.search([('workcenter_id', '=', wc.id), ('product_id', '=', product.id)], limit=1)
        if rec:
            rec.write({'capacity': cap})
        else:
            Cap.create({'workcenter_id': wc.id, 'product_id': product.id, 'capacity': cap})

    def _process_bom(self, text, msgs):
        rows = _parse_csv(text)
        if not rows:
            return
        head = [h.strip().lower() for h in rows[0]]
        try:
            qty_i = head.index('bom_line_ids/product_qty')
        except ValueError:
            raise UserError(u'bom_fixed.csv: column bom_line_ids/product_qty not found')
        dbid_i = head.index('bom_line_ids/.id') if 'bom_line_ids/.id' in head else -1
        xid_i = head.index('bom_line_ids/id') if 'bom_line_ids/id' in head else -1
        done = 0
        for r in rows[1:]:
            line = None
            if dbid_i >= 0 and r[dbid_i].strip().isdigit():
                line = self.env['mrp.bom.line'].browse(int(r[dbid_i])).exists()
            elif xid_i >= 0 and r[xid_i].strip():
                line = self.env.ref(r[xid_i].strip(), raise_if_not_found=False)
            if not line:
                msgs.append(u'! BOM line not found: %s' % u','.join(r))
                continue
            line.write({'product_qty': float(r[qty_i])})
            done += 1
        msgs.append(u'BOM lines updated: %s' % done)

    # ── "Create" mode: the BOM exists, but routing/WC are not created ─────────
    # routing_create.csv (LaserCAM app output when the BOM has no routing/WC):
    #   bom, bom_db_id, code, wc_name, resource_type, calendar_name,
    #   time_efficiency, capacity_per_cycle, time_cycle, time_start, time_stop,
    #   costs_hour, costs_cycle, bom_line_db_id, bom_line_id, product_qty
    # All parameters come from the app (confirmed 2026-08-14). time_cycle — HOURS.

    def _find_bom(self, ext, db):
        if db and db.strip().isdigit():
            b = self.env['mrp.bom'].browse(int(db)).exists()
            if b:
                return b
        if ext and ext.strip():
            rec = self.env.ref(ext.strip(), raise_if_not_found=False)
            if rec and rec._name == 'mrp.bom':
                return rec
        return False

    def _wc_vals(self, WC, get, wc_name, code, old_wc=None):
        """Builds the mrp.workcenter vals dict, ONLY from existing fields (v9/v10+).
        Costs (costs_hour/costs_cycle) — if the app gives 0/empty, INHERIT from the
        old (default) WC (`old_wc`)."""
        f = WC._fields
        vals = {'name': wc_name}
        if 'code' in f and code:
            vals['code'] = code
        rt = get('resource_type')
        if 'resource_type' in f and rt:
            vals['resource_type'] = rt
        cal = get('calendar_name')
        if cal:  # calendar field: v9-13 calendar_id / v14+ resource_calendar_id
            for cfld in ('calendar_id', 'resource_calendar_id'):
                if cfld in f:
                    c = self.env['resource.calendar'].search([('name', '=', cal)], limit=1)
                    if c:
                        vals[cfld] = c.id
                    break
        # capacity → the right field: v9 capacity_per_cycle / v14+ default_capacity
        cap_raw = get('capacity_per_cycle').replace(',', '.')
        if cap_raw:
            for cf in ('capacity_per_cycle', 'default_capacity', 'capacity'):
                if cf in f:
                    try:
                        vals[cf] = float(cap_raw)
                    except ValueError:
                        pass
                    break
        for key in ('time_efficiency', 'time_cycle',
                    'time_start', 'time_stop', 'costs_hour', 'costs_cycle'):
            if key not in f:
                continue
            raw = get(key).replace(',', '.')
            val = None
            if raw:
                try:
                    val = float(raw)
                except ValueError:
                    val = None
            # Costs: app 0/empty → inherit from the old WC (so it is not 0).
            if key in ('costs_hour', 'costs_cycle') and (val is None or val == 0.0) \
                    and old_wc and key in old_wc._fields and old_wc[key]:
                val = old_wc[key]
            if val is not None:
                vals[key] = val
        return vals

    def _process_create(self, text, msgs):
        rows = _parse_csv(text)
        if not rows:
            return
        head = [h.strip().lower() for h in rows[0]]
        idx = dict((name, i) for i, name in enumerate(head))
        WC = self.env['mrp.workcenter']
        ROP = self.env['mrp.routing.workcenter']  # operation model (v9-19; the name stayed)
        BL = self.env['mrp.bom.line']
        BOM = self.env['mrp.bom']
        # v9-13: there is `mrp.routing` + `bom.routing_id`. v14+: routing is merged into
        # the BOM, operations are directly on `bom.operation_ids` (no `mrp.routing`).
        # Behavior: add/update a "Laser <code>" operation, KEEPING any other operations
        # (threading etc.). v9-13 COPIES the whole routing when it mismatches the code.
        has_routing = 'routing_id' in BOM._fields
        ROUTING = self.env['mrp.routing'] if has_routing else None
        done = 0
        for r in rows[1:]:
            def get(key, _r=r):
                i = idx.get(key, -1)
                return _r[i].strip() if 0 <= i < len(_r) else u''

            code = get('code')

            bom = self._find_bom(get('bom'), get('bom_db_id'))
            if not bom:
                msgs.append(u'! BOM not found: %s' % (get('bom') or get('bom_db_id')))
                continue

            # Find the LASER work center currently on the BOM (by WC name "laser/lazer").
            # v9-13: from the BOM routing; v14+: from bom.operation_ids. Threading and
            # other operations are ignored here — they are preserved, not touched.
            def _find_laser_wc(lines):
                for _o in lines:
                    _wcn = (_o.workcenter_id.name if _o.workcenter_id else u'') or u''
                    if re.search(u'la[sz]er', _wcn, re.I):
                        return _o.workcenter_id
                return lines[0].workcenter_id if lines else None

            old_wc = None
            old_routing = False
            if has_routing:
                old_routing = bom.routing_id if bom.routing_id else False
                if old_routing:
                    old_wc = _find_laser_wc(self._op_lines(old_routing))
            elif 'operation_ids' in bom._fields and bom.operation_ids:
                old_wc = _find_laser_wc(bom.operation_ids)

            # Target WC name: "Laser <code>" + whatever followed the code in the old
            # laser WC name (keeps any description). App's wc_name is the fallback.
            if code:
                wc_name = self._relabel(old_wc.name if old_wc else get('wc_name'), code)
            else:
                wc_name = get('wc_name') or u'Laser'

            # 1) find-or-create WC "Laser <code>". If it must be created and the BOM
            # already has a (wrongly-named) laser WC, COPY that one — so ALL its
            # parameters (capacity, cost, calendar, efficiency) carry over — then rename
            # + apply the app's corrected values. The old (shared) WC stays untouched.
            wc = WC.search([('name', '=', wc_name)], limit=1)
            wc_vals = self._wc_vals(WC, get, wc_name, code, old_wc)
            if wc:
                wc.write(wc_vals)
            elif old_wc:
                wc = old_wc.copy()
                wc.write(wc_vals)
            else:
                wc = WC.create(wc_vals)

            # v18+: capacity moved off the workcenter → Product Capacities record.
            product = bom.product_id if ('product_id' in bom._fields and bom.product_id) else False
            if not product and 'product_tmpl_id' in bom._fields and bom.product_tmpl_id:
                product = bom.product_tmpl_id.product_variant_id
            self._apply_wc_capacity(wc, product, get('capacity_per_cycle'))

            tc = get('time_cycle').replace(',', '.')

            def _apply_time(vals):
                if tc and 'time_cycle_manual' in ROP._fields:  # v10+ — minutes
                    try:
                        vals['time_cycle_manual'] = float(tc) * 60.0
                        if 'time_mode' in ROP._fields:
                            vals['time_mode'] = 'manual'
                    except ValueError:
                        pass
                return vals

            if has_routing:
                # v9-13: the routing is WRONG for this product if its name doesn't match
                # the product code. COPY the whole routing (keeps threading + all other
                # operations), rename it, and assign the COPY to this BOM. The original
                # routing is left untouched (it may belong to another product).
                if old_routing:
                    routing_name = self._relabel(old_routing.name, code) if code else (old_routing.name or u'')
                    if (old_routing.name or u'').strip() == routing_name.strip():
                        routing = old_routing            # already correct → reuse
                    else:
                        routing = old_routing.copy({'name': routing_name})
                else:
                    routing_name = (u'Laser %s' % code) if code else (bom.display_name or u'LaserCAM')
                    routing = ROUTING.create({'name': routing_name})
                # Point the laser operation (identified by WC name) at the "Laser <code>"
                # WC; keep every other operation (threading etc.) exactly as it is.
                op = None
                for _o in self._op_lines(routing):
                    _wcn = (_o.workcenter_id.name if _o.workcenter_id else u'') or u''
                    if re.search(u'la[sz]er', _wcn, re.I):
                        op = _o
                        break
                if op is None:
                    op = ROP.search([('routing_id', '=', routing.id), ('workcenter_id', '=', wc.id)], limit=1)
                op_vals = _apply_time({'routing_id': routing.id, 'workcenter_id': wc.id, 'name': wc_name})
                if 'cycle_nbr' in ROP._fields:
                    op_vals['cycle_nbr'] = 1.0
                if op:
                    op.write(op_vals)
                else:
                    op = ROP.create(op_vals)
                if 'routing_id' in bom._fields:
                    bom.write({'routing_id': routing.id})
            else:
                # v14+: operation directly on the BOM. Add/update the laser op;
                # KEEP all other operations (do not remove them).
                op = ROP.search([('bom_id', '=', bom.id), ('workcenter_id', '=', wc.id)], limit=1)
                op_vals = _apply_time({'bom_id': bom.id, 'workcenter_id': wc.id, 'name': wc_name})
                if op:
                    op.write(op_vals)
                else:
                    op = ROP.create(op_vals)

            # 5) BOM line kg (if provided)
            qty = get('product_qty').replace(',', '.')
            if qty:
                line = None
                bl_db = get('bom_line_db_id')
                bl_x = get('bom_line_id')
                if bl_db.isdigit():
                    line = BL.browse(int(bl_db)).exists()
                elif bl_x:
                    line = self.env.ref(bl_x, raise_if_not_found=False)
                if line:
                    try:
                        line.write({'product_qty': float(qty)})
                    except ValueError:
                        pass
            done += 1
        msgs.append(u'Routings/work centers created/updated: %s' % done)

    def _process_wc(self, text, msgs):
        rows = _parse_csv(text)
        if not rows:
            return
        head = [h.strip().lower() for h in rows[0]]
        try:
            id_i = head.index('id')
            cap_i = head.index('capacity_per_cycle')
            time_i = head.index('time_cycle')
        except ValueError:
            raise UserError(u'workcenter_fixed.csv: expected columns id, capacity_per_cycle, time_cycle')
        done = 0
        for r in rows[1:]:
            rec = self.env.ref(r[id_i].strip(), raise_if_not_found=False)
            if not rec:
                msgs.append(u'! Record not found: %s' % r[id_i])
                continue
            cap = float(r[cap_i])
            time_h = float(r[time_i])
            if rec._name == 'mrp.workcenter':
                vals = {}
                if 'time_cycle' in rec._fields:
                    vals['time_cycle'] = time_h  # v9 — hours
                cf = self._cap_field(rec)
                if cf:
                    vals[cf] = cap
                rec.write(vals)
            elif rec._name == 'mrp.routing.workcenter':
                if 'time_cycle_manual' in rec._fields:
                    rec.write({'time_cycle_manual': time_h * 60.0})  # v10+ — minutes
                wc = rec.workcenter_id
                cf = self._cap_field(wc) if wc else None
                if cf:
                    wc.write({cf: cap})
                elif wc and 'bom_id' in rec._fields and rec.bom_id:
                    # v18+: capacity via Product Capacities (per BOM product)
                    b = rec.bom_id
                    product = b.product_id if ('product_id' in b._fields and b.product_id) else False
                    if not product and 'product_tmpl_id' in b._fields and b.product_tmpl_id:
                        product = b.product_tmpl_id.product_variant_id
                    self._apply_wc_capacity(wc, product, u'%s' % cap)
            else:
                msgs.append(u'! Unknown model %s (%s)' % (rec._name, r[id_i]))
                continue
            done += 1
        msgs.append(u'Work centers/operations updated: %s' % done)

    # ── Hub buttons: one "LaserCAM" dialog offers all three paths ─────────────
    def _app_url(self, token):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or u''
        app = self.env['ir.config_parameter'].sudo().get_param('lasercam.app_url') \
            or u'https://laser.ucase.eu/app'
        sep = u'&' if u'?' in app else u'?'
        return u'%s%ssrc=%s&job=%s' % (app, sep, base, token)

    @_multi
    def action_nest(self):
        """Send directly: create a job for the selected BOM and open the app."""
        ids = self._context.get('active_ids') or (
            [self._context.get('active_id')] if self._context.get('active_id') else [])
        if not ids:
            raise UserError(u'Select a Bill of Materials first.')
        bom = self.env['mrp.bom'].browse(ids[0]).exists()
        if not bom:
            raise UserError(u'Bill of Materials not found.')
        job = self.env['lasercam.nest.job'].create({'bom_id': bom.id})
        return {'type': 'ir.actions.act_url', 'url': self._app_url(job.token), 'target': 'new'}

    @_multi
    def action_export(self):
        """Download the export ZIP (manual drag&drop path)."""
        ids = self._context.get('active_ids', [])
        return {
            'type': 'ir.actions.act_url',
            'url': u'/lasercam/export?ids=%s' % u','.join(str(i) for i in ids),
            'target': 'self',
        }

    @_multi
    def action_import(self):
        self.ensure_one()
        msgs = []
        bom_text = None
        wc_text = None
        create_text = None

        # 1) ZIP (the main path — the LaserCAM "Odoo fixes" output).
        if self.zip_file:
            raw = base64.b64decode(self.zip_file)
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except Exception:
                raise UserError(u'Could not open the ZIP file (is it lasercam_fixes.zip?)')
            for name in zf.namelist():
                low = name.lower()
                if not low.endswith('.csv'):
                    continue
                content = self._to_text(zf.read(name))
                if 'create' in low or 'routing' in low:
                    create_text = content
                elif 'workcenter' in low or 'wc' in low:
                    wc_text = content
                elif 'bom' in low:
                    bom_text = content
            zf.close()

        # 2) Individual CSV (manual fallback, if someone unpacked the ZIP).
        if self.bom_file:
            bom_text = self._to_text(base64.b64decode(self.bom_file))
        if self.wc_file:
            wc_text = self._to_text(base64.b64decode(self.wc_file))
        if self.create_file:
            create_text = self._to_text(base64.b64decode(self.create_file))

        if create_text:
            self._process_create(create_text, msgs)
        if bom_text:
            self._process_bom(bom_text, msgs)
        if wc_text:
            self._process_wc(wc_text, msgs)

        self.result = u'\n'.join(msgs) if msgs else u'Nothing imported — add lasercam_fixes.zip (or a CSV).'  # noqa
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
