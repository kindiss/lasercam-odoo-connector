# -*- coding: utf-8 -*-
import logging

from . import models
from . import controllers
from . import wizard

_logger = logging.getLogger(__name__)


def _bind(env):
    """Attaches the single 'LaserCAM' action (hub: Send / Export / Import) to the
    mrp.bom Action menu — version-sensitive: v9-11 → ir.values; v12+ → binding_model_id."""
    act = env.ref('lasercam_connector.action_lasercam_import', raise_if_not_found=False)
    if not act:
        return
    if 'binding_model_id' in act._fields:
        # v12+ — binding directly on the action
        bom = env['ir.model'].search([('model', '=', 'mrp.bom')], limit=1)
        vals = {'binding_model_id': bom.id}
        if 'binding_view_types' in act._fields:
            vals['binding_view_types'] = 'list,form'
        act.write(vals)
    else:
        # v9-11 — via ir.values
        env['ir.values'].sudo().create({
            'model': 'mrp.bom', 'key': 'action', 'key2': 'client_action_multi',
            'name': 'LaserCAM', 'value': 'ir.actions.act_window,%d' % act.id,
        })


def post_init(a, b=None):
    """post_init_hook. Signature differences: v9-15 → (cr, registry); v16+ → (env).
    Bindings are created in Python so that a SINGLE module works across all versions."""
    try:
        if b is None:
            env = a  # v16+: env is passed in
        else:
            cr = a   # v9-15: (cr, registry)
            try:
                from odoo import api, SUPERUSER_ID
            except ImportError:
                from openerp import api, SUPERUSER_ID
            env = api.Environment(cr, SUPERUSER_ID, {})
        _bind(env)
    except Exception as e:
        # Binding failed — the module still installs (the actions exist,
        # they can be attached manually). But we LOG the error so the reason is visible.
        _logger.warning('LaserCAM Connector: Action menu binding failed: %s', e, exc_info=True)
