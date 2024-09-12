import logging
import requests
from werkzeug import urls
from odoo import api, fields, models, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.tools.float_utils import float_compare
from odoo.http import request

_logger = logging.getLogger(__name__)

class PaymentAcquirerSummit(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('summit', 'Summit')])
    summit_api_key_production = fields.Char(string='Summit Production API Key', required_if_provider='summit', groups='base.group_user')
    summit_api_key_test = fields.Char(string='Summit Test API Key', required_if_provider='summit', groups='base.group_user')
    summit_testing = fields.Boolean(string='Test Mode', default=True)
    summit_widget_id = fields.Char(string='Summit Widget ID', groups='base.group_user')
    summit_installments_size = fields.Integer(string='Installments Size (px)', default=14)
    summit_display_catalog_prices = fields.Boolean(string='Display Catalog Prices', default=True)
    summit_display_product_prices = fields.Boolean(string='Display Product Prices', default=True)
    summit_checkout_title = fields.Char(string='Checkout Title', default='Nakup na obroke')
    summit_description = fields.Text(string='Description', default='Obročna plačila z 1Stavno.')
    summit_instructions = fields.Char(string='Instructions URL', default='https://1stavno.si')

    def _get_summit_api_url(self):
        """
        Returns the appropriate Summit API URL based on the testing mode.
        """
        return 'https://pktest.takoleasy.si' if self.summit_testing else 'https://pk.takoleasy.si'

    def summit_form_generate_values(self, values):
        """
        Generates the values required for the Summit payment form.
        
        :param values: Dictionary containing transaction values
        :return: Dictionary with Summit-specific transaction values
        """
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        summit_tx_values = dict(values)
        api_url = self._get_summit_api_url() + "/webpayment/rest/v1/creditapi/getWebCreditLink/json"
        
        payload = {
            'AuthToken': self.summit_api_key_test if self.summit_testing else self.summit_api_key_production,
            'errorPage': urls.url_join(base_url, '/payment/summit/error'),
            'successPage': urls.url_join(base_url, '/payment/summit/return'),
            'referenceNumber': values.get('reference', ''),  # Use .get() with a default value
            'creditAmount': values.get('amount', ''),
        }
        
        try:
            response = requests.post(api_url, json=payload)
            response.raise_for_status()
            result = response.json()
            if result.get('serviceStatus') == 'OK' and 'data' in result and 'Url' in result['data']:
                summit_tx_values['summit_redirect_url'] = result['data']['Url']
                summit_tx_values['summit_payment_url'] = result['data']['Url']
            else:
                raise ValidationError(_("Invalid response from Summit API"))
        except requests.exceptions.RequestException as e:
            _logger.error("Error communicating with Summit: %s", str(e))
            raise ValidationError(_("Could not establish the connection to Summit."))
        
        summit_tx_values['installment_options'] = self.get_installments(values['amount'])
        
        return summit_tx_values

    def summit_get_form_action_url(self):
        """
        Returns the URL for the Summit payment form action.
        """
        try:
            values = self.summit_form_generate_values({})
            return values['summit_redirect_url']
        except (ValidationError, KeyError):
            _logger.error("Failed to generate Summit form values or 'summit_redirect_url' not found")
            return None

    def _summit_make_request(self, endpoint, payload=None, method='POST'):
        """
        Makes an HTTP request to the Summit API.
        
        :param endpoint: API endpoint
        :param payload: Request payload
        :param method: HTTP method (GET or POST)
        :return: JSON response from the API
        """
        api_url = self._get_summit_api_url()
        url = urls.url_join(api_url, endpoint)
        headers = {'Content-Type': 'application/json'}
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=payload)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=payload)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error(f"Error communicating with Summit: {e}")
            raise ValidationError(_("Could not establish the connection to Summit."))

    def get_installments(self, price):
        """
        Retrieves installment information for a given price.
        
        :param price: The price for which to get installment information
        :return: List of installment options
        """
        api_url = "/webpayment/rest/v1/creditapi/getInstallmentInfo/json"
        payload = {
            'APIKey': self.summit_api_key_test if self.summit_testing else self.summit_api_key_production,
            'CreditAmount': price
        }
        response = self._summit_make_request(api_url, payload)
        if response.get('serviceStatus') == 'OK' and 'data' in response:
            return response['data']['installmentInfoList']
        return []

    def update_installments(self):
        """
        Updates the installment information for all products with a list price <= 15000.
        """
        Product = self.env['product.template']
        products = Product.search([('list_price', '<=', 15000)])
        for product in products:
            installments = self.get_installments(product.list_price)
            if installments:
                product.write({
                    'summit_installments': installments,
                    'summit_min_installment': min(inst['installmentValue'] for inst in installments)
                })

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    summit_installments = fields.Text(string='Summit Installments')
    summit_min_installment = fields.Float(string='Minimum Installment')

class PaymentTransactionSummit(models.Model):
    _inherit = 'payment.transaction'

    summit_payment_id = fields.Char(string='Summit Payment ID')
    summit_selected_installments = fields.Integer(string='Selected Installments')
    summit_redirect_url = fields.Char(string='Summit Redirect URL')
    installment_options = fields.Text(string='Installment Options')

    @api.model
    def _summit_form_get_tx_from_data(self, data):
        """
        Finds and returns a payment transaction based on the Summit data.
        
        :param data: Transaction data received from Summit
        :return: payment.transaction record
        """
        reference = data.get('ReferenceNumber')
        if not reference:
            error_msg = _('Summit: received data with missing reference (%s)') % (reference)
            _logger.info(error_msg)
            raise ValidationError(error_msg)

        tx = self.search([('reference', '=', reference), ('provider', '=', 'summit')])
        if not tx or len(tx) > 1:
            error_msg = _('Summit: received data for reference %s') % (reference)
            if not tx:
                error_msg += _('; no order found')
            else:
                error_msg += _('; multiple orders found')
            _logger.info(error_msg)
            raise ValidationError(error_msg)
        return tx

    def _summit_form_get_invalid_parameters(self, data):
        """
        Checks for invalid parameters in the Summit data.
        
        :param data: Transaction data received from Summit
        :return: List of tuples with invalid parameters
        """
        invalid_parameters = []
        if float_compare(float(data.get('CreditAmount', '0.0')), self.amount, 2) != 0:
            invalid_parameters.append(('amount', data.get('CreditAmount'), str(self.amount)))
        return invalid_parameters

    def _summit_form_validate(self, data):
        """
        Validates the Summit payment and updates the transaction status accordingly.
        
        :param data: Transaction data received from Summit
        :return: True
        """
        status = data.get('status')
        if status == 'commit':
            self._set_transaction_done()
        elif status == 'create':
            self._set_transaction_pending()
        elif status == 'cancel':
            self._set_transaction_cancel()
        elif status == 'identification':
            self._set_transaction_pending()
            self.write({'state_message': _('Awaiting Identification')})
        else:
            _logger.info('Received unrecognized status for Summit payment %s: %s', self.reference, status)
            self._set_transaction_error('Received unrecognized status from Summit')
        return True

    def update_order_information(self):
        """
        Updates additional order information in Summit.
        
        :return: Status of the update operation
        """
        self.ensure_one()
        acquirer = self.acquirer_id
        api_url = "/webpayment/rest/v1/creditapi/sendOrderAdditionalInfo/json"
        
        payload = {
            'APIKey': acquirer.summit_api_key_test if acquirer.summit_testing else acquirer.summit_api_key_production,
            'ReferenceNumber': self.reference,
            'StNarocila': self.reference,
            'CenaDDV': self.amount,
            'CenaBrezDDV': "0",
            'DDV': "0",
            'Artikli': [f"Product name: {line.product_id.name} | Quantity: {line.product_uom_qty} | Item total: {line.price_total}" for line in self.sale_order_ids.order_line],
            'SelectedInstallments': self.summit_selected_installments
        }
        
        response = acquirer._summit_make_request(api_url, payload)
        if response.get('data', {}).get('status') == "0":
            self.write({'summit_additional_info': True})
        return response.get('data', {}).get('status')

    @api.model
    def cron_update_order_information(self):
        """
        Cron job to update order information for pending Summit transactions.
        """
        transactions = self.search([
            ('state', 'in', ['draft', 'pending', 'authorized']),
            ('provider', '=', 'summit'),
            ('summit_additional_info', '=', False)
        ])
        for transaction in transactions:
            transaction.update_order_information()