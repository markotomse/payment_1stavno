{
    'name': 'Summit Payment Acquirer',
    'version': '13.0.1.0.0',
    'category': 'Accounting/Payment Acquirers',
    'summary': 'Summit Payment Acquirer for Odoo',
    'description': """
This module adds Summit as a payment acquirer in Odoo.
It allows customers to pay using Summit payment gateway.
    """,
    'author': 'Marko Tomše',
    'website': 'https://www.tomse.eu',
    'depends': ['payment'],
    'data': [
        'views/payment_views.xml',
        'views/payment_summit_templates.xml',
        'data/payment_acquirer_data.xml',
    ],
    #'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
}