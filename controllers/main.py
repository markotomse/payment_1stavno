import logging
import pprint
import werkzeug
import hmac
import hashlib
import json
from datetime import datetime, timedelta

from odoo import http, fields
from odoo.http import request

_logger = logging.getLogger(__name__)

class SummitController(http.Controller):
    @http.route(['/payment/summit/return', '/payment/summit/cancel'], type='http', auth='public', csrf=False)
    def summit_return(self, **post):
        """ Process the notification data sent by Summit after redirection """
        _logger.info('Summit: entering form_feedback with post data %s', pprint.pformat(post))
        request.env['payment.transaction'].sudo().form_feedback(post, 'summit')
        return werkzeug.utils.redirect('/payment/process')

    @http.route(['/payment/summit/webhook'], type='json', auth='public', csrf=False)
    def summit_webhook(self):
        """ Process the webhook data sent by Summit """
        data = request.jsonrequest
        _logger.info('Summit: Webhook data received: %s', pprint.pformat(data))
        
        if not data:
            return 'No data received'
        
        reference = data.get('reference')
        if not reference:
            _logger.error('Summit: Received webhook data with missing reference')
            return 'Missing reference'

        tx = request.env['payment.transaction'].sudo().search([('reference', '=', reference)])
        if not tx:
            _logger.error('Summit: Received webhook data for non-existing transaction with reference %s', reference)
            return 'Transaction not found'

        if not self._verify_summit_signature(request.httprequest.headers, request.httprequest.data):
            _logger.error('Summit: Invalid webhook signature')
            return 'Invalid signature'

        tx.sudo()._summit_form_validate(data)
        return 'Webhook processed'

    def _verify_summit_signature(self, headers, payload):
        summit_signature = headers.get('Summit-Signature')
        if not summit_signature:
            return False

        acquirer = request.env['payment.acquirer'].sudo().search([('provider', '=', 'summit')], limit=1)
        secret = acquirer.summit_webhook_secret

        # Assuming Summit uses HMAC-SHA256 for signature
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected_signature, summit_signature)

    @http.route(['/payment/summit/update_order_statuses'], type='http', auth='public', csrf=False)
    def update_order_statuses(self):
        """ Update order statuses periodically """
        acquirer = request.env['payment.acquirer'].sudo().search([('provider', '=', 'summit')], limit=1)
        
        # Get transactions in specific states
        transactions = request.env['payment.transaction'].sudo().search([
            ('acquirer_id', '=', acquirer.id),
            ('state', 'in', ['draft', 'pending', 'authorized'])
        ])

        for tx in transactions:
            # Call Summit API to get order status
            # This is a placeholder. You need to implement the actual API call.
            status = self._get_summit_order_status(tx.reference)
            
            if status == 'completed':
                tx.write({'state': 'done'})
            elif status == 'cancelled':
                tx.write({'state': 'cancel'})

        return 'Order statuses updated'

    def _get_summit_order_status(self, reference):
        # Placeholder method. Implement the actual API call to Summit here.
        # Return the status as a string ('completed', 'cancelled', etc.)
        return 'completed'

    @http.route(['/payment/summit/update_installments'], type='http', auth='public', csrf=False)
    def update_installments(self):
        """ Update installment information for products """
        acquirer = request.env['payment.acquirer'].sudo().search([('provider', '=', 'summit')], limit=1)
        
        products = request.env['product.template'].sudo().search([('list_price', '<=', 15000)])

        for product in products:
            installments = self._get_summit_installments(product.list_price)
            if installments:
                product.write({'summit_installments': json.dumps(installments)})

        return 'Installments updated'

    def _get_summit_installments(self, price):
        # Placeholder method. Implement the actual API call to Summit here.
        # Return the installment information as a list or dictionary
        return [{'installment_nr': 3, 'installment_value': price / 3},
                {'installment_nr': 6, 'installment_value': price / 6}]

    @http.route(['/payment/summit/cron'], type='http', auth='public', csrf=False)
    def summit_cron(self):
        """ Run periodic tasks """
        self.update_order_statuses()
        self.update_installments()
        return 'Cron tasks completed'