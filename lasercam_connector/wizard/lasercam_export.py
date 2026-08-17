# -*- coding: utf-8 -*-
"""Export wizard: Action menu → dialog with a "Download" button →
act_url to /lasercam/export. (A wizard instead of server action code — works
the same across all versions, without ir.actions.server eval context differences.)"""
try:
    from odoo import api, fields, models
except ImportError:
    from openerp import api, fields, models

# v14+ has no api.multi (methods are multi by default) — no-op fallback.
_multi = getattr(api, 'multi', lambda f: f)


class LaserCAMExportWizard(models.TransientModel):
    _name = 'lasercam.export.wizard'
    _description = 'Export to LaserCAM'

    info = fields.Char(default='CSV for LaserCAM (laser.ucase.eu/app) — drag&drop it into the app.', readonly=True)

    @_multi
    def action_download(self):
        ids = self._context.get('active_ids', [])
        return {
            'type': 'ir.actions.act_url',
            'url': '/lasercam/export?ids=%s' % ','.join(str(i) for i in ids),
            'target': 'self',
        }
