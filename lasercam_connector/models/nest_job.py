# -*- coding: utf-8 -*-
"""A short-lived "nesting job": links a random token to a BOM so the LaserCAM
app can fetch the data and post the result back without any file handling.
The token is the shared secret — whoever holds it (the user who clicked
"Nest in LaserCAM") can read that one BOM and write its nesting result."""
import uuid

try:
    from odoo import fields, models
except ImportError:  # Odoo 9 / py2
    from openerp import fields, models


class LasercamNestJob(models.Model):
    _name = 'lasercam.nest.job'
    _description = 'LaserCAM nesting job'

    token = fields.Char(
        'Token', index=True, required=True, copy=False,
        default=lambda self: uuid.uuid4().hex)
    bom_id = fields.Many2one('mrp.bom', string='Bill of Materials',
                             ondelete='cascade')
    state = fields.Selection([('open', 'Open'), ('done', 'Done')],
                             default='open')
    result = fields.Text('Result', readonly=True)
    # 5.3: the first GET of the job "claims" it — the claim key is handed to that browser
    # tab only (never in the URL). Every write-back must present it; a copied link opened
    # elsewhere gets 'already_used'. Jobs expire after 1 day.
    claim = fields.Char('Claim key', copy=False, index=True)
    claimed_at = fields.Datetime('Claimed at', copy=False)
