# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Demande Materiel',
    'version': '1.2',
    'category': 'stock/achat',
    'sequence': 35,
    'summary': 'stock et achat',
    'website': '',
    'depends': ['hr','purchase','account','stock'],
    'data': [
        'security/demande_materiel_security.xml',
        'security/ir.model.access.csv',        
        'views/demande_materiel_views.xml',  
        'data/demande_materiel_data.xml', 
        'report/report.xml',        
        'report/bon_caisse.xml',
        'report/bon_pour.xml',
        'report/depense_caisse.xml', 
        'report/transfert.xml',
        'report/bon_caisse.xml',  
        'report/retour_caisse.xml', 
        'report/justification_bonpour.xml', 
        'report/change.xml',      
       
        
    ],
    'demo': [
        
    ],
    'installable': True,
    'application': True,
    'assets': {
        
    },
    'license': '',
}
