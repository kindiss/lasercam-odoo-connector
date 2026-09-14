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
import json
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
        for cfld in ('calendar_id', 'resource_calendar_id'):  # v9-13 calendar_id / v14+ resource_calendar_id
            if cfld not in f:
                continue
            if cal:
                c = self.env['resource.calendar'].search([('name', '=', cal)], limit=1)
                if c:
                    vals[cfld] = c.id
            elif old_wc and cfld in old_wc._fields and old_wc[cfld]:
                # 5.1: related field on v17+ (copy=False) -> copy() drops it; inherit explicitly.
                vals[cfld] = old_wc[cfld].id
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
            # (5.1) + time_efficiency: related on v17+ (copy=False) -> copy() resets it to 100.
            if key in ('costs_hour', 'costs_cycle', 'time_efficiency') and (val is None or val == 0.0) \
                    and old_wc and key in old_wc._fields and old_wc[key]:
                val = old_wc[key]
            if val is not None:
                vals[key] = val
        return vals

    def _op_lines(self, routing):
        u"""Routing operations o2m: v9-11 `workcenter_lines`, v12-13 `operation_ids`."""
        if routing and 'workcenter_lines' in routing._fields:
            return routing.workcenter_lines
        if routing and 'operation_ids' in routing._fields:
            return routing.operation_ids
        return routing[:0] if routing else routing

    def _process_create(self, text, msgs):
        rows = _parse_csv(text)
        if not rows:
            return
        head = [h.strip().lower() for h in rows[0]]
        idx = dict((name, i) for i, name in enumerate(head))
        done = 0
        for r in rows[1:]:
            def get(key, _r=r):
                i = idx.get(key, -1)
                return _r[i].strip() if 0 <= i < len(_r) else u''

            if self._create_one(get, msgs):
                done += 1
        msgs.append(u'Routings/work centers created/updated: %s' % done)

    def _create_one(self, get, msgs):
        u"""ONE routing_create row (``get(key)`` -> str). Shared by the CSV path
        (_process_create) and the new-products path (_process_products).
        Returns True when the BOM was found and the WC/operation written."""
        WC = self.env['mrp.workcenter']
        ROP = self.env['mrp.routing.workcenter']  # operation model (v9-19; the name stayed)
        BL = self.env['mrp.bom.line']
        BOM = self.env['mrp.bom']
        # v9-13: there is `mrp.routing` + `bom.routing_id`. v14+: routing is merged into
        # the BOM, operations are directly on `bom.operation_ids` (no `mrp.routing`).
        # Behavior is IDENTICAL: create a "Laser <code>" WC and attach ONLY it
        # (remove the old shared WC).
        has_routing = 'routing_id' in BOM._fields
        ROUTING = self.env['mrp.routing'] if has_routing else None
        code = get('code')
        wc_name = get('wc_name') or (u'Laser %s' % code if code else u'Laser')

        bom = self._find_bom(get('bom'), get('bom_db_id'))
        if not bom:
            msgs.append(u'! BOM not found: %s' % (get('bom') or get('bom_db_id')))
            return False

        # The old (default) WC — to inherit the cost. DO NOT TOUCH (shared, used
        # elsewhere). v9-13: from bom.routing_id; v14+: from bom.operation_ids.
        old_wc = None
        old_routing = False
        if has_routing:
            old_routing = bom.routing_id if bom.routing_id else False
            # routing operations o2m: v9 `workcenter_lines`; v10-13 `operation_ids`
            _ops = (old_routing.workcenter_lines if 'workcenter_lines' in old_routing._fields
                    else old_routing.operation_ids) if old_routing else None
            if _ops:
                old_wc = _ops[0].workcenter_id
        elif 'operation_ids' in bom._fields and bom.operation_ids:
            old_wc = bom.operation_ids[0].workcenter_id

        # 1) find-or-create WC "Laser <code>"; cost inherited from the old one
        wc = WC.search([('name', '=', wc_name)], limit=1)
        wc_vals = self._wc_vals(WC, get, wc_name, code, old_wc)
        if wc:
            wc.write(wc_vals)
        else:
            wc = WC.create(wc_vals)

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
            # v9-13: routing + operation + bom.routing_id. Reuse only if the routing
            # is already "Laser <code>" (clear out foreign ops); otherwise — NEW.
            routing_name = (u'Laser %s' % code) if code else (bom.display_name or u'LaserCAM')
            routing = None
            if old_routing and (old_routing.name or u'').strip() == routing_name.strip():
                routing = old_routing
                _lines = (old_routing.workcenter_lines if 'workcenter_lines' in old_routing._fields
                          else old_routing.operation_ids)
                for o in [x for x in _lines if x.workcenter_id.id != wc.id]:
                    o.unlink()
            if routing is None:
                routing = ROUTING.create({'name': routing_name})
            op = ROP.search([('routing_id', '=', routing.id), ('workcenter_id', '=', wc.id)], limit=1)
            op_vals = _apply_time({'routing_id': routing.id, 'workcenter_id': wc.id, 'name': wc_name})
            if 'cycle_nbr' in ROP._fields:
                op_vals['cycle_nbr'] = 1.0
            if op:
                op.write(op_vals)
            else:
                op = ROP.create(op_vals)
            # 5.1: a routing copied from the template may still carry the template's laser
            # operation (e.g. "Lazeris 00692") — drop it; other operations (threading...) stay.
            if old_wc and old_wc.id != wc.id:
                for _o in self._op_lines(routing):
                    if _o.id != op.id and _o.workcenter_id and _o.workcenter_id.id == old_wc.id:
                        _o.unlink()
            if 'routing_id' in bom._fields:
                bom.write({'routing_id': routing.id})
        else:
            # v14+: operation DIRECTLY on the BOM (bom_id) + remove other operations
            # (the old shared WC) — the analog of v9 "replace the routing".
            op = ROP.search([('bom_id', '=', bom.id), ('workcenter_id', '=', wc.id)], limit=1)
            if not op:
                # 5.1: the BOM copied from the template (or an old BOM) has the laser operation
                # on the OLD work center ("Lazeris 00692") — re-point it instead of adding a 2nd op.
                for _o in bom.operation_ids:
                    _wcn = (_o.workcenter_id.name if _o.workcenter_id else u'') or u''
                    if (old_wc and _o.workcenter_id and _o.workcenter_id.id == old_wc.id) or re.search(u'la[sz]er', _wcn, re.I):
                        op = _o
                        break
            op_vals = _apply_time({'bom_id': bom.id, 'workcenter_id': wc.id, 'name': wc_name})
            if op:
                op.write(op_vals)
            else:
                op = ROP.create(op_vals)
            if old_wc and old_wc.id != wc.id:
                for _o in bom.operation_ids:
                    if _o.id != op.id and _o.workcenter_id and _o.workcenter_id.id == old_wc.id:
                        _o.unlink()
            for o in ROP.search([('bom_id', '=', bom.id), ('id', '!=', op.id)]):
                o.unlink()

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
        return True

    # ── New products from DXF (F2): ``create_products`` payload ─────────────────
    # {template_code, material{odoo_code,name,thickness,density},
    #  products[{code,name,kg_per_unit,minutes_per_unit,qty_nested,per_sheet,
    #            dxf_base64,dxf_filename}]}
    # New product = copy() of the TEMPLATE (the last exported product — remembered by
    # the export / nest_job endpoints; inherits every field incl. custom ones)
    # -> BOM with the sheet-material line (kg/unit incl. waste) -> "Laser <code>" work
    # center + operation (via _create_one) -> DXF attachment (so "Nest in LaserCAM"
    # works next time). Existing code -> update path (idempotent).

    def _kg_uom(self):
        for xid in ('uom.product_uom_kgm', 'product.product_uom_kgm'):
            rec = self.env.ref(xid, raise_if_not_found=False)
            if rec:
                return rec
        model = 'uom.uom' if 'uom.uom' in self.env else 'product.uom'
        return self.env[model].search([('name', 'ilike', 'kg')], limit=1)

    def _storable_vals(self, PT):
        u"""Product type vals: v18+ type=consu + is_storable; v9-17 type=product."""
        f = PT._fields
        if 'is_storable' in f:
            return {'type': 'consu', 'is_storable': True}
        if 'detailed_type' in f:
            return {'detailed_type': 'product'}
        return {'type': 'product'}

    def _template_product(self, code):
        u"""Template for copy(): explicit code from the app, else the remembered
        last-exported product (ir.config_parameter lasercam.template_product_tmpl_id)."""
        PT = self.env['product.template']
        if code:
            t = PT.search([('default_code', '=', code)], limit=1)
            if t:
                return t
        pid = self.env['ir.config_parameter'].sudo().get_param('lasercam.template_product_tmpl_id')
        if pid and (u'%s' % pid).strip().isdigit():
            t = PT.browse(int(pid)).exists()
            if t:
                return t
        return PT.browse()

    def _find_or_create_material(self, mat, res, msgs):
        PP = self.env['product.product']
        code = (mat.get('odoo_code') or u'').strip()
        name = (mat.get('name') or u'').strip()
        # Odoo display_name "[CODE] Name" (the BOM line from the export) -> code + name.
        m = re.match(r'^\[(.+?)\]\s*(.*)$', name)
        if m:
            code = code or m.group(1).strip()
            name = m.group(2).strip() or name
        if code:
            p = PP.search([('default_code', '=', code)], limit=1)
            if p:
                return p
        if not name:
            return PP.browse()
        p = PP.search([('name', '=', name)], limit=1)
        if p:
            return p
        vals = {'name': name}
        if code:
            vals['default_code'] = code
        kg = self._kg_uom()
        if kg:
            vals['uom_id'] = kg.id
            if 'uom_po_id' in PP._fields:
                vals['uom_po_id'] = kg.id
        vals.update(self._storable_vals(PP))
        p = PP.create(vals)
        res['material_created'] = True
        msgs.append(u'Material created: %s' % name)
        return p

    def _attach_dxf(self, tmpl, fname, b64):
        Att = self.env['ir.attachment']
        old = Att.search([('res_model', '=', 'product.template'), ('res_id', '=', tmpl.id),
                          ('name', '=', fname)], limit=1)
        if old:
            old.write({'datas': b64})
            return
        vals = {'name': fname, 'res_model': 'product.template', 'res_id': tmpl.id,
                'type': 'binary', 'datas': b64}
        if 'datas_fname' in Att._fields:  # v9-12
            vals['datas_fname'] = fname
        att = Att.create(vals)
        # Chatter: v9-12 shows only MESSAGE attachments (v13+ shows every record attachment)
        # -> also post a note with the DXF so it is visible on the product form everywhere.
        if hasattr(tmpl, 'message_post'):
            try:
                tmpl.message_post(body=u'DXF from LaserCAM', attachment_ids=[att.id])
            except Exception:
                pass

    def _process_products(self, payload, msgs):
        PT = self.env['product.template']
        BOM = self.env['mrp.bom']
        BL = self.env['mrp.bom.line']
        res = {'created': [], 'updated': [], 'material_created': False, 'errors': []}
        tmpl = self._template_product((payload.get('template_code') or u'').strip())
        mat = self._find_or_create_material(payload.get('material') or {}, res, msgs)
        kg_uom = self._kg_uom()
        for pr in payload.get('products') or []:
            code = (pr.get('code') or u'').strip()
            name = (pr.get('name') or code).strip()
            if not code:
                res['errors'].append(u'product without code: %s' % name)
                continue
            existing = PT.search([('default_code', '=', code)], limit=1)
            if existing:
                new = existing
                res['updated'].append(code)
            elif tmpl:
                new = tmpl.copy({'name': name})
                new.write({'name': name, 'default_code': code})
                res['created'].append(code)
            else:
                vals = {'name': name, 'default_code': code}
                vals.update(self._storable_vals(PT))
                new = PT.create(vals)
                res['created'].append(code)
            # BOM (one per product) + sheet-material line (kg/unit incl. waste).
            bom = BOM.search([('product_tmpl_id', '=', new.id)], limit=1)
            if not bom:
                # 5.1: COPY the template's BOM (without its component lines) instead of
                # an empty one — the copy carries the routing (v9-13) / operations (v14+),
                # so _create_one finds the template's laser work center and copies it:
                # cost/hour, efficiency, calendar, capacity all carry over (an empty BOM
                # produced a bare "Laser <code>" work center with zero cost).
                tmpl_bom = BOM.search([('product_tmpl_id', '=', tmpl.id)], limit=1) if tmpl else BOM.browse()
                if tmpl_bom:
                    dflt = {'product_tmpl_id': new.id, 'bom_line_ids': False}
                    if 'product_id' in BOM._fields:
                        dflt['product_id'] = False
                    if 'code' in BOM._fields:
                        dflt['code'] = False
                    bom = tmpl_bom.copy(dflt)
                    msgs.append(u'BOM %s: copied from template %s' % (code, tmpl.default_code or tmpl.name))
                else:
                    bvals = {'product_tmpl_id': new.id, 'product_qty': 1.0}
                    if 'type' in BOM._fields:
                        bvals['type'] = 'normal'
                    bom = BOM.create(bvals)
            kg = pr.get('kg_per_unit')
            if mat and kg is not None:
                line = None
                for bl in bom.bom_line_ids:
                    if bl.product_id.id == mat.id:
                        line = bl
                        break
                if line:
                    line.write({'product_qty': float(kg)})
                else:
                    lvals = {'bom_id': bom.id, 'product_id': mat.id, 'product_qty': float(kg)}
                    for uf in ('product_uom_id', 'product_uom'):
                        if uf in BL._fields and kg_uom:
                            lvals[uf] = kg_uom.id
                            break
                    BL.create(lvals)
            # "Laser <code>" work center + operation: capacity = parts per sheet,
            # cycle time (hours) = min/unit x parts per sheet / 60 (Odoo: min/unit back).
            minutes = pr.get('minutes_per_unit')
            per_sheet = pr.get('per_sheet') or pr.get('qty_nested') or 1
            row = {
                'bom_db_id': u'%s' % bom.id, 'code': code, 'wc_name': u'Laser %s' % code,
                'capacity_per_cycle': u'%s' % per_sheet,
                'time_cycle': (u'%s' % (float(minutes) * float(per_sheet) / 60.0)) if minutes is not None else u'',
            }
            self._create_one(lambda k, _r=row: _r.get(k, u''), msgs)
            b64 = pr.get('dxf_base64')
            if b64:
                self._attach_dxf(new, pr.get('dxf_filename') or (u'%s.dxf' % code), b64)
        msgs.append(u'Products created: %s, updated: %s' % (len(res['created']), len(res['updated'])))
        return res


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
            else:
                msgs.append(u'! Unknown model %s (%s)' % (rec._name, r[id_i]))
                continue
            done += 1
        msgs.append(u'Work centers/operations updated: %s' % done)

    @_multi
    def action_done(self):
        u"""Close the dialog explicitly: after the act_url download Odoo 9/10 leave
        the modal open when the button is only special="cancel"."""
        return {'type': 'ir.actions.act_window_close'}

    @_multi
    def action_import(self):
        self.ensure_one()
        msgs = []
        bom_text = None
        wc_text = None
        create_text = None
        products_json = None  # F5: nauji produktai (kaip create_products payload) ZIP'e
        dxf_in_zip = {}       # F5: <dxf_filename> -> bytes (DXF kaip atskiri failai ZIP'e)

        # 1) ZIP (the main path — the LaserCAM "Odoo fixes" output).
        if self.zip_file:
            raw = base64.b64decode(self.zip_file)
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
            except Exception:
                raise UserError(u'Could not open the ZIP file (is it lasercam_fixes.zip?)')
            for name in zf.namelist():
                low = name.lower()
                if low.endswith('products.json'):
                    products_json = self._to_text(zf.read(name))
                    continue
                if low.endswith('.dxf'):
                    dxf_in_zip[name.rsplit('/', 1)[-1].lower()] = zf.read(name)
                    continue
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

        # F5: NAUJI produktai pirmiau (kad jų BOM/WC jau būtų), tada esamų pataisymai.
        if products_json:
            try:
                payload = json.loads(products_json)
            except ValueError as e:
                raise UserError(u'products.json: %s' % e)
            # DXF iš ZIP (kai JSON be base64): pagal dxf_filename.
            for pr in payload.get('products') or []:
                if not pr.get('dxf_base64') and pr.get('dxf_filename'):
                    raw = dxf_in_zip.get((u'%s' % pr['dxf_filename']).rsplit('/', 1)[-1].lower())
                    if raw:
                        pr['dxf_base64'] = base64.b64encode(raw).decode('ascii')
            self._process_products(payload, msgs)
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
