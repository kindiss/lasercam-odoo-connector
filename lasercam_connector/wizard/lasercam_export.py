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
    # 5.3: ticked -> the exported product opens in LaserCAM as a disabled TEMPLATE;
    # unticked (default) -> it opens active and its own BOM is recalculated.
    is_template = fields.Boolean('Template only', default=False)

    @_multi
    def action_download(self):
        ids = self._context.get('active_ids', [])
        return {
            'type': 'ir.actions.act_url',
            'url': '/lasercam/export?ids=%s&tpl=%s' % (','.join(str(i) for i in ids), '1' if self[:1].is_template else '0'),
            # 'new' (ne 'self'): Odoo 9/10 su target self nukreipia visa langa ir dialogas
            # 'pakimba' (Done nebeveikia). Naujas skirtukas su attachment'u uzsidaro pats.
            'target': 'new',
        }

    @_multi
    def action_done(self):
        u"""Close the dialog explicitly: after the act_url download Odoo 9/10 leave
        the modal open when the button is only special="cancel"."""
        return {'type': 'ir.actions.act_window_close'}
