# -*- coding: utf-8 -*-
from datetime import datetime
import logging
from odoo import _, api, fields, models
from odoo.tools.float_utils import float_compare, float_is_zero
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class StockMoveCustom(models.Model):
    _inherit = "stock.move"

    @api.constrains('lot_ids', 'product_id')
    def _get_lot_domain_custom(self):
        for rec in self:
            if not rec.picking_id:
                continue
            if rec.lot_ids:
                quantity_sum = 0
                for lot_id in rec.lot_ids:
                    quants_lot2 = self.env['stock.quant'].search([
                        ('location_id', '=', rec.picking_id.location_id.id),
                        ('lot_id', '=', lot_id.id),
                        ('quantity', '>', 0),
                    ])
                    quants_lot = quants_lot2.mapped('lot_id.id')
                    quantity = sum(quants_lot2.mapped('quantity'))
                    quantity_sum += quantity
                    if not quants_lot2 and rec.picking_id.picking_type_id.code in ('outgoing', 'internal'):
                        raise ValidationError(
                            f"This lot ({lot_id.name}) is not available in the selected location."
                        )
                if (
                    rec.product_id.tracking != 'none'
                    and rec.picking_type_id.code in ('outgoing', 'internal')
                    and quantity_sum < rec.quantity
                    and rec.picking_id.location_id.id != rec.picking_id.location_dest_id.id
                ):
                    raise ValidationError(
                        f"Quantity {rec.quantity} requested but only {quantity_sum} available."
                    )


class StockMoveLineCustom(models.Model):
    _inherit = "stock.move.line"

    @api.constrains('lot_id', 'product_id')
    def _get_lot_domain_custom(self):
        for rec in self:
            if not rec.picking_id:
                continue
            if rec.picking_id.picking_type_id.code == 'incoming':
                continue
            quants_lot2 = self.env['stock.quant'].search([
                ('location_id', '=', rec.picking_id.location_id.id),
                ('lot_id', '=', rec.lot_id.id),
            ])
            quants_lot = quants_lot2.mapped('lot_id.id')
            quantity = sum(quants_lot2.mapped('quantity'))
            if rec.product_id.tracking == 'none':
                quants_untracked = self.env['stock.quant'].search([
                    ('location_id', '=', rec.picking_id.location_id.id),
                    ('product_id', '=', rec.product_id.id),
                ])
                quantity1 = sum(quants_untracked.mapped('quantity'))
                if (
                    quantity1 < rec.quantity
                    and rec.picking_id.location_id.id != rec.picking_id.location_dest_id.id
                    and rec.picking_type_id.code in ('outgoing', 'internal')
                ):
                    raise ValidationError(
                        f"This product ({rec.product_id.name}) is not available in the selected location."
                    )
            if (
                rec.picking_id.location_id
                and rec.lot_id
                and rec.lot_id.id not in quants_lot
                and rec.product_id.tracking != 'none'
                and rec.picking_type_id.code in ('outgoing', 'internal')
                and quantity < rec.quantity
                and rec.picking_id.location_id.id != rec.picking_id.location_dest_id.id
            ):
                raise ValidationError(
                    f"This lot ({rec.lot_id.name}) is not available in the selected location."
                )


class InheritStockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _update_available_quantity(self, product_id, location_id, quantity=False, reserved_quantity=False, lot_id=None, package_id=None, owner_id=None, in_date=None):
        """ Optimized override: uses SQL SKIP LOCKED to avoid deadlocks on concurrent GRN validation,
        and direct SQL INSERT for new quants to bypass ORM overhead on large receipts (500+ lines).
        """
        if not (quantity or reserved_quantity):
            raise ValidationError(_('Quantity or Reserved Quantity should be set.'))

        self = self.sudo()
        quants = self._gather(product_id, location_id, lot_id=lot_id, package_id=package_id, owner_id=owner_id, strict=True)

        if lot_id:
            if float_compare(quantity, 0, precision_rounding=product_id.uom_id.rounding) > 0:
                quants = quants.filtered(lambda q: q.lot_id)
            else:
                quants = quants.filtered(
                    lambda q: float_compare(q.quantity, 0, precision_rounding=q.product_uom_id.rounding) > 0 or q.lot_id
                )

        if location_id.should_bypass_reservation():
            incoming_dates = []
        else:
            incoming_dates = [
                quant.in_date for quant in quants
                if quant.in_date and float_compare(quant.quantity, 0, precision_rounding=quant.product_uom_id.rounding) > 0
            ]
        if in_date:
            incoming_dates.append(in_date)
        in_date = min(incoming_dates) if incoming_dates else fields.Datetime.now()

        quant = None
        if quants:
            self._cr.execute(
                "SELECT id FROM stock_quant WHERE id IN %s ORDER BY lot_id LIMIT 1 FOR NO KEY UPDATE SKIP LOCKED",
                [tuple(quants.ids)],
            )
            result = self._cr.fetchone()
            if result:
                quant = self.browse(result[0])

        if quant:
            vals = {'in_date': in_date}
            if quantity:
                vals['quantity'] = quant.quantity + quantity
            if reserved_quantity:
                vals['reserved_quantity'] = quant.reserved_quantity + reserved_quantity
            quant.write(vals)
        else:
            self._cr.execute("""
                INSERT INTO stock_quant
                    (product_id, location_id, quantity, reserved_quantity,
                     lot_id, package_id, owner_id, in_date,
                     create_uid, create_date, write_uid, write_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                product_id.id,
                location_id.id,
                quantity or 0.0,
                reserved_quantity or 0.0,
                lot_id.id if lot_id else None,
                package_id.id if package_id else None,
                owner_id.id if owner_id else None,
                in_date,
                self.env.uid,
                datetime.now(),
                self.env.uid,
                datetime.now(),
            ))

        return (
            self._get_available_quantity(
                product_id, location_id,
                lot_id=lot_id, package_id=package_id, owner_id=owner_id,
                strict=True, allow_negative=True,
            ),
            in_date,
        )
