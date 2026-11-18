# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestSpeedUpGRN(TransactionCase):

    def setUp(self):
        super().setUp()
        self.product = self.env['product.product'].create({
            'name': 'Test Product GRN',
            'type': 'product',
            'tracking': 'none',
        })
        self.product_lot = self.env['product.product'].create({
            'name': 'Test Product Lot GRN',
            'type': 'product',
            'tracking': 'lot',
        })
        self.supplier_location = self.env.ref('stock.stock_location_suppliers')
        self.stock_location = self.env.ref('stock.stock_location_stock')
        self.customer_location = self.env.ref('stock.stock_location_customers')
        self.picking_type_in = self.env.ref('stock.picking_type_in')
        self.picking_type_out = self.env.ref('stock.picking_type_out')

    # ------------------------------------------------------------------
    # _update_available_quantity optimized path tests
    # ------------------------------------------------------------------

    def test_quant_update_creates_new_quant(self):
        """Direct SQL INSERT path: calling _update_available_quantity on a
        product with no existing quant should create a new quant record."""
        before = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', self.stock_location.id),
        ])
        self.assertFalse(before, "Should start with no quant for this product")

        self.env['stock.quant']._update_available_quantity(
            self.product, self.stock_location, quantity=10.0
        )

        after = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', self.stock_location.id),
        ])
        self.assertTrue(after, "A quant should have been created")
        self.assertAlmostEqual(after.quantity, 10.0)

    def test_quant_update_increments_existing_quant(self):
        """SQL SKIP LOCKED path: calling _update_available_quantity on an
        existing quant should increment its quantity."""
        self.env['stock.quant']._update_available_quantity(
            self.product, self.stock_location, quantity=5.0
        )
        self.env['stock.quant']._update_available_quantity(
            self.product, self.stock_location, quantity=3.0
        )

        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', self.stock_location.id),
        ])
        self.assertAlmostEqual(quant.quantity, 8.0)

    def test_quant_update_reserved_quantity(self):
        """reserved_quantity parameter should update reserved_quantity field."""
        self.env['stock.quant']._update_available_quantity(
            self.product, self.stock_location, quantity=20.0
        )
        self.env['stock.quant']._update_available_quantity(
            self.product, self.stock_location, reserved_quantity=5.0
        )
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', self.stock_location.id),
        ])
        self.assertAlmostEqual(quant.reserved_quantity, 5.0)

    def test_quant_update_raises_without_quantity(self):
        """Must raise ValidationError when neither quantity nor reserved_quantity is set."""
        with self.assertRaises(ValidationError):
            self.env['stock.quant']._update_available_quantity(
                self.product, self.stock_location
            )

    # ------------------------------------------------------------------
    # stock.move constraint tests
    # ------------------------------------------------------------------

    def test_stock_move_incoming_skips_lot_validation(self):
        """Incoming (GRN) moves must NOT raise even when the lot has no quant."""
        lot = self.env['stock.lot'].create({
            'name': 'LOT-GRN-001',
            'product_id': self.product_lot.id,
        })
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.picking_type_in.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
        })
        move = self.env['stock.move'].create({
            'name': 'GRN move',
            'product_id': self.product_lot.id,
            'product_uom': self.product_lot.uom_id.id,
            'product_uom_qty': 5.0,
            'picking_id': picking.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
            'lot_ids': [(4, lot.id)],
        })
        # No exception expected for incoming moves
        move._get_lot_domain_custom()

    def test_stock_move_outgoing_raises_when_lot_missing(self):
        """Outgoing move constraint raises ValidationError when lot has no quant
        in the source location."""
        lot = self.env['stock.lot'].create({
            'name': 'LOT-OUT-001',
            'product_id': self.product_lot.id,
        })
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.picking_type_out.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
        })
        move = self.env['stock.move'].create({
            'name': 'Outgoing move',
            'product_id': self.product_lot.id,
            'product_uom': self.product_lot.uom_id.id,
            'product_uom_qty': 1.0,
            'picking_id': picking.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
            'lot_ids': [(4, lot.id)],
        })
        with self.assertRaises(ValidationError):
            move._get_lot_domain_custom()

    # ------------------------------------------------------------------
    # stock.move.line constraint tests
    # ------------------------------------------------------------------

    def test_move_line_incoming_skips_validation(self):
        """Incoming move lines must NOT raise even with no stock for the lot."""
        lot = self.env['stock.lot'].create({
            'name': 'LOT-ML-IN-001',
            'product_id': self.product_lot.id,
        })
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.picking_type_in.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
        })
        move = self.env['stock.move'].create({
            'name': 'Incoming move',
            'product_id': self.product_lot.id,
            'product_uom': self.product_lot.uom_id.id,
            'product_uom_qty': 5.0,
            'picking_id': picking.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
        })
        ml = self.env['stock.move.line'].create({
            'move_id': move.id,
            'product_id': self.product_lot.id,
            'product_uom_id': self.product_lot.uom_id.id,
            'quantity': 5.0,
            'lot_id': lot.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
            'picking_id': picking.id,
        })
        # No exception expected
        ml._get_lot_domain_custom()

    def test_move_line_outgoing_raises_when_lot_not_in_location(self):
        """Outgoing move line raises ValidationError when lot is absent from
        the source location."""
        lot = self.env['stock.lot'].create({
            'name': 'LOT-ML-OUT-001',
            'product_id': self.product_lot.id,
        })
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.picking_type_out.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
        })
        move = self.env['stock.move'].create({
            'name': 'Outgoing move',
            'product_id': self.product_lot.id,
            'product_uom': self.product_lot.uom_id.id,
            'product_uom_qty': 1.0,
            'picking_id': picking.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
        })
        ml = self.env['stock.move.line'].create({
            'move_id': move.id,
            'product_id': self.product_lot.id,
            'product_uom_id': self.product_lot.uom_id.id,
            'quantity': 1.0,
            'lot_id': lot.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
            'picking_id': picking.id,
        })
        with self.assertRaises(ValidationError):
            ml._get_lot_domain_custom()

    def test_move_line_outgoing_raises_when_untracked_product_insufficient(self):
        """Outgoing move line for an untracked product raises when stock is
        insufficient."""
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.picking_type_out.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
        })
        move = self.env['stock.move'].create({
            'name': 'Outgoing move untracked',
            'product_id': self.product.id,
            'product_uom': self.product.uom_id.id,
            'product_uom_qty': 100.0,
            'picking_id': picking.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
        })
        ml = self.env['stock.move.line'].create({
            'move_id': move.id,
            'product_id': self.product.id,
            'product_uom_id': self.product.uom_id.id,
            'quantity': 100.0,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
            'picking_id': picking.id,
        })
        with self.assertRaises(ValidationError):
            ml._get_lot_domain_custom()
