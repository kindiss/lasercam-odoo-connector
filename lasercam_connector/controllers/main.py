# -*- coding: utf-8 -*-
"""LaserCAM HTTP endpoints.

Two ways to move BOM data to/from the LaserCAM nesting app:

1. File round-trip (manual):
   * ``GET /lasercam/export?ids=1,2,3`` → ZIP with ``mrp.bom.csv`` +
     ``mrp.workcenter.csv`` (+ the product DXF). Drag&drop into the app.
   * The Import wizard uploads the ``lasercam_fixes.zip`` the app returns.

2. API bridge (no files) — the "Nest in LaserCAM" button:
   * ``GET  /lasercam/nest/job/<token>``   → the SAME two CSV (as text) + DXF
     (base64) as a JSON payload, so the app can preload everything.
   * ``POST /lasercam/nest/result/<token>`` → the app posts back the fixes CSV
     (bom_fixed / workcenter_fixed / routing_create); we reuse the import
     wizard processors to write straight into the BOM / work center.

Shared core for all Odoo versions (9–19): models/fields detected at runtime,
time ALWAYS normalized to hours (the LaserCAM format is stable). Py2/Py3 and
openerp/odoo namespace compatibility via try/except.
"""
import base64
import datetime
import io
import json
import uuid
import re
import zipfile

try:
    from odoo import http, SUPERUSER_ID
    from odoo.http import request
except ImportError:  # Odoo 9 / py2
    from openerp import http, SUPERUSER_ID
    from openerp.http import request

# Headers — the LaserCAM parser recognizes them (EN) and returns the technical
# field names (id, bom_line_ids/.id, product_qty, capacity_per_cycle, time_cycle).
BOM_HEADER = [
    'External ID',
    'BoM Lines/ID',
    'BoM Lines/Product Quantity',
    'BoM Lines/Display Name',
    'Routing/Work Centers/Name',
]
WC_HEADER = [
    'External ID',
    'Name',
    'Capacity per Cycle',
    'Time for 1 cycle (hour)',
]


def _q(cell):
    s = u'%s' % (cell if cell is not None else u'')
    if any(ch in s for ch in (u'"', u',', u'\n', u'\r')):
        return u'"%s"' % s.replace(u'"', u'""')
    return s


def _csv(rows):
    return u'\r\n'.join(u','.join(_q(c) for c in row) for row in rows) + u'\r\n'


def _xmlid(rec):
    """Existing or newly created __export__ external ID (as in Odoo export)."""
    if not rec:
        return u''
    return rec.export_data(['id'])['datas'][0][0]


def _product_dxf(env, bom):
    """Newest .dxf attachment for the BOM product → (file_name, bytes) or None.
    We look on both product.template and product.product (v9–19). The DXF app
    picks it up straight from the export ZIP — no manual upload needed."""
    Att = env['ir.attachment']
    tmpl = bom.product_tmpl_id if 'product_tmpl_id' in bom._fields else None
    prod = bom.product_id if 'product_id' in bom._fields else None
    domain = ['|',
              '&', ('res_model', '=', 'product.template'), ('res_id', '=', tmpl.id if tmpl else 0),
              '&', ('res_model', '=', 'product.product'), ('res_id', '=', prod.id if prod else 0)]
    has_fname = 'datas_fname' in Att._fields  # v9–12; removed in v13+
    code = u''
    for p in (prod, tmpl):
        if p and getattr(p, 'default_code', None):
            code = p.default_code
            break
    for att in Att.search(domain, order='id desc'):
        fname = (att.datas_fname if has_fname else None) or att.name or u''
        if not fname.lower().endswith('.dxf'):
            continue
        raw = att.datas
        if not raw:
            continue
        # Ensure the code is in the file name — the app links DXF↔BOM by code.
        # We prefix ONLY with the product code (FIRST run of ≥3 digits, e.g.
        # "17154-LANE-3600" -> 17154) and only if not already there (avoid doubling).
        runs = re.findall(r'\d{3,}(?:-\d+)?', code or u'')
        short = runs[0] if runs else u''
        if short and short not in fname:
            fname = u'%s_%s' % (short, fname)
        return (fname, base64.b64decode(raw))
    return None


def _collect(env, boms):
    """Builds the export payload for a set of BOMs (shared by the file export
    and the API bridge): returns (bom_rows, wc_rows, dxf_files, codes)."""
    Bom = env['mrp.bom']
    Wc = env['mrp.workcenter']
    has_routing = 'routing_id' in Bom._fields  # v9–13; v14+ operacijos BOM'e
    time_on_wc = 'time_cycle' in Wc._fields     # only v9 (hours on the workcenter)
    cap_field = None
    for f in ('capacity_per_cycle', 'default_capacity', 'capacity'):
        if f in Wc._fields:
            cap_field = f
            break

    bom_rows = [BOM_HEADER]
    wc_seen = {}    # target_xid -> [name, cap, time_h]
    wc_order = []   # keeps order, without duplicates
    dxf_files = []  # [(name, bytes)] — product DXF (attachment)
    dxf_names = set()
    codes = []      # product codes → for the ZIP file name

    for bom in boms:
        bom_xid = _xmlid(bom)
        _tmpl = bom.product_tmpl_id if 'product_tmpl_id' in bom._fields else None
        _prod = bom.product_id if 'product_id' in bom._fields else None
        bom_code = u''  # product code = FIRST >=3-digit run, e.g. "17154-LANE-3600" -> 17154
        for _p in (_prod, _tmpl):
            if _p and getattr(_p, 'default_code', None):
                _runs = re.findall(r'\d{3,}(?:-\d+)?', _p.default_code)
                if _runs:
                    bom_code = _runs[0]
                break
        if bom_code and bom_code not in codes:
            codes.append(bom_code)
        dxf = _product_dxf(env, bom)
        if dxf:
            name, raw = dxf
            base, i = name, 1
            while name in dxf_names:  # avoid duplicate names in the ZIP
                dot = base.rfind('.')
                name = (u'%s_%d%s' % (base[:dot], i, base[dot:])) if dot > 0 else u'%s_%d' % (base, i)
                i += 1
            dxf_names.add(name)
            dxf_files.append((name, raw))
        if has_routing:
            rt = bom.routing_id
            if rt:
                # routing operations o2m: v9-11 `workcenter_lines`; v12-13 `operation_ids`
                ops = rt.workcenter_lines if 'workcenter_lines' in rt._fields else rt.operation_ids
            else:
                ops = Wc.browse()
        else:
            ops = bom.operation_ids
        # Canonical laser name from the PRODUCT code (not the actual, possibly wrong, WC
        # name). Pick the LASER op by matching that code; else the first op (fallback).
        laser_name = (u'Laser %s' % bom_code) if bom_code else u''
        op = None
        if bom_code:
            for _o in ops:
                _wcn = ((_o.workcenter_id.name if _o.workcenter_id else u'') or u'')
                if _wcn.strip() == laser_name or bom_code in _wcn:
                    op = _o
                    break
        if op is None:
            op = ops[0] if ops else None
        op_name = op.name if op else u''
        wc = op.workcenter_id if op else None
        # Time TARGET for the reverse import: v9 — workcenter; v10+ — operation.
        target = wc if (time_on_wc and wc) else op
        target_xid = _xmlid(target)
        if time_on_wc and wc:
            time_h = wc.time_cycle or 0.0
        elif op is not None and 'time_cycle_manual' in op._fields:
            time_h = (op.time_cycle_manual or 0.0) / 60.0  # min → hours
        else:
            time_h = 0.0
        cap = wc[cap_field] if (wc and cap_field) else 0.0
        wc_name = (wc.name if wc else u'') or op_name  # ACTUAL WC name (for the WC CSV)
        # BOM op-name column = canonical "Laser <code>" so the app extracts the CORRECT
        # number; WC CSV keeps ACTUAL name so the app sees no "Laser <code>" WC -> CREATE.
        export_name = laser_name or wc_name

        if target_xid and target_xid not in wc_seen:
            wc_seen[target_xid] = [wc_name, cap, time_h]
            wc_order.append(target_xid)

        lines = bom.bom_line_ids
        if not lines:
            bom_rows.append([bom_xid, u'', u'', u'', export_name])
            continue
        first = True
        for line in lines:
            bom_rows.append([
                bom_xid if first else u'',
                u'%s' % line.id,
                u'%s' % line.product_qty,
                line.product_id.display_name or line.product_id.name or u'',
                export_name if first else u'',
            ])
            first = False

    wc_rows = [WC_HEADER]
    for xid in wc_order:
        name, cap, time_h = wc_seen[xid]
        wc_rows.append([xid, name, u'%s' % cap, u'%s' % time_h])

    return bom_rows, wc_rows, dxf_files, codes


def _remember_template(env, boms):
    """Template "training" (F2): the last exported product becomes the copy()
    source for the products the app creates (create_products)."""
    for bom in boms:
        tmpl = bom.product_tmpl_id if 'product_tmpl_id' in bom._fields else None
        if tmpl:
            env['ir.config_parameter'].sudo().set_param(
                'lasercam.template_product_tmpl_id', u'%s' % tmpl.id)
            return


class LaserCAMController(http.Controller):

    # ── 1. File export (manual round-trip) ───────────────────────────────────
    @http.route('/lasercam/export', type='http', auth='user')
    def lasercam_export(self, ids='', tpl='1', **kw):
        env = request.env
        # 5.3: manufacturing users only (same group as the LaserCAM action / wizard)
        if not env.user.has_group('mrp.group_mrp_user'):
            return request.make_response(u'LaserCAM: manufacturing user rights required', status=403,
                                         headers=[('Content-Type', 'text/plain; charset=utf-8')])
        bom_ids = [int(i) for i in ids.split(',') if i.strip().isdigit()]
        boms = env['mrp.bom'].browse(bom_ids).exists()
        bom_rows, wc_rows, dxf_files, codes = _collect(env, boms)
        _remember_template(env, boms)

        # Pack both CSV into one ZIP (a single download from "Export").
        # STORE (no compression) — so the LaserCAM app's pure-TS reader can read it.
        buf = io.BytesIO()
        zf = zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED)
        zf.writestr('mrp.bom.csv', _csv(bom_rows).encode('utf-8'))
        zf.writestr('mrp.workcenter.csv', _csv(wc_rows).encode('utf-8'))
        for name, raw in dxf_files:  # product DXF — the app picks them up automatically
            zf.writestr(name, raw)
        # 5.3: template flag for the app (older apps ignore the file; no file = template)
        zf.writestr('lasercam.json', (u'{"template": %s}' % (u'false' if tpl == '0' else u'true')).encode('utf-8'))
        zf.close()
        data = buf.getvalue()

        suffix = u'-'.join(codes[:5])
        zip_name = (u'lasercam_export_%s.zip' % suffix) if suffix else u'lasercam_export.zip'
        return request.make_response(
            data,
            headers=[
                ('Content-Type', 'application/zip'),
                ('Content-Disposition', u'attachment; filename="%s"' % zip_name),
            ],
        )

    # ── 2. API bridge (no files) ─────────────────────────────────────────────
    def _json(self, payload):
        """JSON response. CORS (Access-Control-Allow-Origin) is added by the
        route's cors='*' — do NOT set it here too, or the browser sees a
        duplicated header and rejects the response."""
        return request.make_response(
            json.dumps(payload),
            headers=[('Content-Type', 'application/json')],
        )

    JOB_TTL_DAYS = 1

    def _job_expired(self, job):
        cd = job.create_date
        if not cd:
            return False
        if not isinstance(cd, datetime.datetime):  # v9-12: string
            try:
                cd = datetime.datetime.strptime(str(cd)[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return False
        return (datetime.datetime.utcnow() - cd) > datetime.timedelta(days=self.JOB_TTL_DAYS)

    def _job_authorized(self, job, claim):
        """Write-back guard (5.3): the job must be claimed and the caller must present the
        claim key that the first GET handed out. Returns an error code or None."""
        if self._job_expired(job):
            return 'expired'
        if not job.claim or not claim or claim != job.claim:
            return 'not_authorized'
        return None

    @http.route('/lasercam/nest/job/<token>', type='http', auth='public',
                methods=['GET'], csrf=False, cors='*')
    def nest_job(self, token, **kw):
        """The app fetches the job (BOM + WC + DXF) by its token. The FIRST fetch claims
        the job and returns a claim key; later fetches must repeat that key (same tab
        reload) — a copied link opened elsewhere gets 'already_used'."""
        senv = request.env(user=SUPERUSER_ID)  # public route → superuser env for reads
        job = senv['lasercam.nest.job'].search([('token', '=', token)], limit=1)
        if not job or not job.bom_id or not job.bom_id.exists():
            return self._json({'error': 'not_found'})
        if self._job_expired(job):
            return self._json({'error': 'expired'})
        claim = (kw.get('claim') or u'').strip()
        if job.claim:
            if claim != job.claim:
                return self._json({'error': 'already_used'})
        else:
            job.write({'claim': uuid.uuid4().hex, 'claimed_at': datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')})
        bom_rows, wc_rows, dxf_files, codes = _collect(senv, job.bom_id)
        _remember_template(senv, job.bom_id)
        dxf = None
        if dxf_files:
            name, raw = dxf_files[0]
            dxf = {'name': name, 'b64': base64.b64encode(raw).decode('ascii')}
        return self._json({
            'token': token,
            'claim': job.claim,
            'bom_csv': _csv(bom_rows),
            'wc_csv': _csv(wc_rows),
            'dxf': dxf,
        })

    @http.route('/lasercam/nest/result/<token>', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def nest_result(self, token, **kw):
        """The app posts the fixes CSV back; we reuse the import wizard processors."""
        env = request.env
        job = env['lasercam.nest.job'].sudo().search([('token', '=', token)], limit=1)
        if not job:
            return self._json({'ok': False, 'error': 'not_found'})

        create_csv = kw.get('create_csv') or u''
        bom_csv = kw.get('bom_csv') or u''
        wc_csv = kw.get('wc_csv') or u''
        claim = kw.get('claim') or u''
        if not (create_csv or bom_csv or wc_csv):  # raw JSON body fallback
            try:
                body = json.loads((request.httprequest.get_data() or b'{}').decode('utf-8'))  # py3.5 (v11/12): bytes nepriima
                create_csv = body.get('create_csv', u'')
                bom_csv = body.get('bom_csv', u'')
                wc_csv = body.get('wc_csv', u'')
                claim = body.get('claim', u'') or claim
            except Exception:
                pass
        denied = self._job_authorized(job, claim)
        if denied:
            return self._json({'ok': False, 'error': denied})

        wiz = env['lasercam.import.wizard'].sudo().create({})
        msgs = []
        try:
            if create_csv:
                wiz._process_create(create_csv, msgs)
            if bom_csv:
                wiz._process_bom(bom_csv, msgs)
            if wc_csv:
                wiz._process_wc(wc_csv, msgs)
        except Exception as e:
            return self._json({'ok': False, 'error': u'%s' % e, 'messages': msgs})

        job.sudo().write({'state': 'done', 'result': u'\n'.join(msgs)})
        return self._json({'ok': True, 'messages': msgs})

    @http.route('/lasercam/nest/products/<token>', type='http', auth='public',
                methods=['POST'], csrf=False, cors='*')
    def nest_products(self, token, **kw):
        """create_products (F2): the app posts the products that are missing in
        Odoo (JSON body — see wizard._process_products). Same one-time token as
        the result endpoint. Response: {ok, created[], updated[], material_created,
        errors[], messages[]}."""
        env = request.env
        job = env['lasercam.nest.job'].sudo().search([('token', '=', token)], limit=1)
        if not job:
            return self._json({'ok': False, 'error': 'not_found'})
        try:
            body = json.loads((request.httprequest.get_data() or b'{}').decode('utf-8'))  # py3.5 (v11/12): bytes nepriima
        except Exception as e:
            return self._json({'ok': False, 'error': u'bad json: %s' % e})
        denied = self._job_authorized(job, (body.get('claim') or kw.get('claim') or u'') if isinstance(body, dict) else u'')
        if denied:
            return self._json({'ok': False, 'error': denied})
        wiz = env['lasercam.import.wizard'].sudo().create({})
        msgs = []
        try:
            res = wiz._process_products(body, msgs)
        except Exception as e:
            return self._json({'ok': False, 'error': u'%s' % e, 'messages': msgs})
        res['ok'] = True
        res['messages'] = msgs
        return self._json(res)
