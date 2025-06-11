from odoo import models, fields, api
from odoo import api, fields, models, _
from odoo.addons.base.models.res_partner import WARNING_MESSAGE, WARNING_HELP
from odoo.tools.float_utils import float_round
from dateutil.relativedelta import relativedelta
from odoo.exceptions import ValidationError, UserError
from odoo import models, api, exceptions

class DemandeMateriel(models.Model):
    _name = 'demande.materiel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    name = fields.Char('Order Référence', required=True, index=True, copy=False, default='New')
    nom_chantier = fields.Many2one('stock.location', 'Chantier')
    code_chantier = fields.Char('CT')
    demandeur_id = fields.Many2one('hr.employee', string='Chef Chantier')
    achat_id = fields.Many2one('purchase.order', string='Achat')
    date_order = fields.Datetime('Date/heure', required=True, index=True, copy=False, default=lambda self: fields.Datetime.now())
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    ligne_id = fields.One2many('ligne.materiel', 'demande_id', 'Details')

    location_id = fields.Many2one(
        'stock.location', "Source Location",
        default=lambda self: self.env['stock.picking.type'].browse(self._context.get('default_picking_type_id')).default_location_src_id,
        check_company=True)
    location_dest_id = fields.Many2one(
        'stock.location', "Destination Location",
        default=lambda self: self.env['stock.picking.type'].browse(self._context.get('default_picking_type_id')).default_location_dest_id,
        check_company=True)
    picking_type_id = fields.Many2one('stock.picking.type', 'Operation Type')
    ref_stock_id = fields.Many2one('stock.picking', 'Ref Stock')
    montant_restant = fields.Float(compute='_compute_recurring_total_paye', string="Total versé", store=True, tracking=True)
    code_reliquat = fields.Char('Reliquat')
    reliquat = fields.Selection([
        ('normal', "Normal"),
        ('reliquat', "Reliquat"),
    ], string="Reliquat")

    state = fields.Selection([
        ('brouillon', "Brouillon"),
        ('valider', "Validée"),
        ('approuver', "Approuvée Livraison"),
        ('planning', "Livraison"),
        ('reliquat', "Reliquat"),
        ('planning_achat', "Planifiée pour Achat"),
        ('livrer', "Livrée"),
        ('commander', "Commandée"),
        ('stock', "En Stock"),
        ('cancel', "Annulée"),
    ], string="Status", readonly=True, copy=False, index=True, tracking=True, default='brouillon')

    @api.model
    def create(self, vals_list):
        if isinstance(vals_list, dict):
            vals_list = [vals_list]

        orders = self.env[self._name].browse()
        for vals in vals_list:
            company_id = vals.get('company_id', self.env.company.id)
            self_comp = self.with_company(company_id)
            if vals.get('name', 'New') == 'New':
                seq_date = None
                if 'date_order' in vals:
                    seq_date = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(vals['date_order']))
                vals['name'] = self_comp.env['ir.sequence'].next_by_code('demande.materiel', sequence_date=seq_date) or '/'

            orders |= super(DemandeMateriel, self_comp).create(vals)
        return orders

    @api.depends('ligne_id', 'ligne_id.qte_dif')
    def _compute_recurring_total_paye(self):
        for record in self:
            record.montant_restant = sum(line.qte_dif for line in record.ligne_id)

    @api.onchange('nom_chantier')
    def _onchange_nom_chantier(self):
        if self.nom_chantier:
            self.code_chantier = self.nom_chantier.compte_analytic.name
            self.demandeur_id = self.nom_chantier.demandeur_id.id

    @api.onchange('demandeur_id')
    def _onchange_demandeur_id(self):
        if self.demandeur_id:
            self.departement_id = self.demandeur_id.department_id.id

    def button_valider(self):
        self.ensure_one()
        picking_type = self.env['stock.picking.type'].search([
            ('sequence_code', '=', 'INT'),
            ('company_id', '=', self.company_id.id)
        ], limit=1)

        if picking_type:
            self.picking_type_id = picking_type
            self.location_id = picking_type.default_location_src_id or self.env['stock.warehouse']._get_partner_locations()[1]
            self.location_dest_id = picking_type.default_location_dest_id or self.nom_chantier

        for objet in self.ligne_id.filtered(lambda l: l.state != 'invalide'):
            objet.state = 'valider'

        self.state = 'valider'
        return True

    def button_approuverachat(self):
        self.ensure_one()
        for objet in self.ligne_id.filtered(lambda l: l.valide_achat):
            objet.state = 'planning_achat'
        self.state = 'planning_achat'
        return True

    def button_approuverreliquat(self):
        self.ensure_one()
        if self.montant_restant > 0:
            requi_id = self.env['demande.materiel'].create({
                'name': f"{self.reliquat}/{self.name}",
                'nom_chantier': self.nom_chantier.id,
                'code_chantier': self.code_chantier,
                'demandeur_id': self.demandeur_id.id,
                'code_reliquat': self.name,
                'state': 'reliquat',
                'location_id': self.location_id.id,
                'location_dest_id': self.nom_chantier.id,
                'picking_type_id': self.picking_type_id.id,
            })

            for objet in self.ligne_id.filtered(lambda l: l.qte_dif > 0):
                self.env['ligne.materiel'].create({
                    'produit_id': objet.produit_id.id,
                    'code_article': objet.code_article,
                    'qte': objet.qte_dif,
                    'qte_dif': objet.qte_dif,
                    'demande_id': requi_id.id,
                })
        self.state = 'reliquat'
        return True

    def button_valide_planning(self):
        self.ensure_one()
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.picking_type_id.id,
            'location_id': self.location_id.id,
            'location_dest_id': self.location_dest_id.id,
            'origin': self.name,
            'demande_requisition_id': self.id,
        })

        for objet in self.ligne_id.filtered(lambda l: l.valide):
            self.env['stock.move'].create({
                'product_id': objet.produit_id.id,
                'product_uom_qty': objet.qte_livraison,
                'name': objet.produit_id.name,
                'picking_id': picking.id,
                'product_uom': objet.produit_id.uom_po_id.id,
                'location_id': self.location_id.id,
                'location_dest_id': self.location_dest_id.id,
            })
            objet.state = 'approuver'

        self.ref_stock_id = picking
        self.state = 'approuver'
        return True

    def button_cancel(self):
        self.state = 'cancel'
        return True


from odoo import models, fields, api

class LigneMateriel(models.Model):
    _name = 'ligne.materiel'

    name = fields.Char('Num Commande')
    produit_id = fields.Many2one('product.product', string='Produit', required=True)
    code_article = fields.Char('Code Article')
    code_travail = fields.Char('Code du Travail')
    qte = fields.Float('Quantité Demandée')
    qte_stock = fields.Float('Quantité en Stock')
    qte_livraison = fields.Float('Quantité Livrée')
    qte_dif = fields.Float('Quantité Restante')
    date_order = fields.Datetime('Date Commande', required=True, default=fields.Datetime.now, copy=False, index=True)
    company_id = fields.Many2one('res.company', string='Société', required=True, default=lambda self: self.env.company)
    demande_id = fields.Many2one('demande.materiel', string='Demande')
    valide = fields.Boolean('Valide pour Stock')
    valide_achat = fields.Boolean('Valide pour Achat')

    state = fields.Selection([
        ('brouillon', "Brouillon"),
        ('valider', "Validée"),
        ('approuver', "Approuvée"),
        ('planning', "Planifiée pour Livraison"),
        ('planning_achat', "Planifiée pour Achat"),
        ('livrer', "Livrée"),
        ('commander', "Commandée pour Achat"),
        ('cancel', "Annulée"),
    ], string="Statut")

    @api.onchange('qte_livraison')
    def _onchange_qte_livraison(self):
        if self.qte and self.qte_livraison:
            self.qte_dif = self.qte - self.qte_livraison

    @api.onchange('qte')
    def _onchange_qte(self):
        self.qte_dif = self.qte

    @api.onchange('produit_id')
    def _onchange_produit_id(self):
        if self.produit_id:
            self.code_article = self.produit_id.default_code
            self.qte_stock = self.produit_id.qty_available



class StockPicking(models.Model):
    _inherit = 'stock.picking'

    demande_requisition_id = fields.Many2one('demande.materiel', string='Demande de Matériel')
    groupage_requisition = fields.One2many('ligne.groupage', 'stock_id', string='Groupage')
    valide = fields.Boolean('Valide Demande')

    def button_validate(self):
        res = super().button_validate()
        for picking in self:
            if picking.picking_type_id.sequence_code == 'INT':
                if picking.demande_requisition_id:
                    picking.demande_requisition_id.state = 'livrer'
                    picking.demande_requisition_id.ligne_id.write({'state': 'livrer'})
            elif picking.picking_type_id.sequence_code == 'IN':
                for line in picking.groupage_requisition:
                    if line.name:
                        line.name.state = 'stock'
        return res


class AccountMove(models.Model):
    _inherit = 'account.move'

    type_compta = fields.Selection([
        ('usd_cdf', 'USD/CDF'),
        ('usd', 'USD'),
        ('cdf', 'CDF'),
    ], string='Type de Compta')
    
    type_operation = fields.Selection([
        ('facture', 'Facture'),
        ('ecriture', 'Écriture')
    ], string='Type d\'Opération')


    def _reverse_moves(self, default_values_list=None, cancel=False):
        reversed_moves = super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)

        for move, reversed_move in zip(self, reversed_moves):
            # Si la société est en USD
            if move.company_id.currency_id.name == 'USD':
                # Recherche la société CDF
                company_cdf = self.env['res.company'].search([('currency_id.name', '=', 'CDF')], limit=1)
                if not company_cdf:
                    continue

                # Recherche du move CDF correspondant
                move_cdf = self.env['account.move'].search([
                    ('ref', '=', move.name),
                    ('company_id', '=', company_cdf.id),
                    ('state', '=', 'posted')
                ], limit=1)

                if move_cdf:
                    # Inverser aussi le move CDF
                    reversed_cdf = move_cdf._reverse_moves(default_values_list=[{
                        'ref': reversed_move.name
                    }], cancel=cancel)

                    # Poster automatiquement l'inverse CDF
                     #reversed_cdf.action_post()

        return reversed_moves


    def button_draft(self):
        res = super().button_draft()

        for move in self:
            if move.company_id.currency_id.name == 'USD':
                company_cdf = self.env['res.company'].search([('currency_id.name', '=', 'CDF')], limit=1)
                if company_cdf:
                    move_cdf = self.env['account.move'].search([
                        ('ref', '=', move.name),
                        ('company_id', '=', company_cdf.id),
                        ('state', '=', 'posted')
                    ], limit=1)
                    if move_cdf:
                        move_cdf.button_draft()
                        move_cdf.unlink()  # ← facultatif, seulement si tu veux la supprimer aussi

        return res

    def action_post(self):
        res = super().action_post()
        company_cdf = self.env['res.company'].search([('name', '=', 'CD Company')], limit=1)
        if not company_cdf:
            raise UserError("La société CDF n’a pas été trouvée.")
        
        
        journal_cdf = self.env['account.journal'].search([('type', '=', self.journal_id.type),('company_id', '=', company_cdf.id)], limit=1)
        
        if not journal_cdf:
            raise UserError("Aucun journal équivalent trouvé pour la société CDF.")
        if self.move_type in ['out_invoice', 'in_invoice']:

            company_cdf = self.env['res.company'].search([('currency_id.name', '=', 'CDF')], limit=1)
            if not company_cdf or company_cdf == self.company_id:
                return

            usd_currency = self.company_id.currency_id
            cdf_currency = self.env.ref('base.CDF')

            # Récupérer le taux de change à la date de la facture
            rate = usd_currency._get_conversion_rate(usd_currency, cdf_currency, self.company_id, self.date)

            # Journal dans la société cible (à adapter selon vos règles de mapping)
            cdf_journal = self.env['account.journal'].search([
                ('company_id', '=', company_cdf.id),
                ('type', '=', self.journal_id.type),
            ], limit=1)
            if not cdf_journal:
                raise ValueError("Aucun journal équivalent trouvé en CDF")

            # Copier les lignes de facture avec montants convertis
            line_ids = []
            for line in self.invoice_line_ids:
                line_ids.append((0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.name,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit * rate,
                    'account_id': self._get_cdf_account(line.account_id).id,
                    'tax_ids': [(6, 0, [self._get_cdf_tax(tax).id for tax in line.tax_ids])],
                }))

            # Créer la facture en CDF
            facture_CDF = self.env['account.move'].create({
                'move_type': self.move_type,
                'partner_id': self.partner_id.id,
                'invoice_date': self.invoice_date,
                'date': self.date,
                'journal_id': cdf_journal.id,
                'currency_id': cdf_currency.id,
                'company_id': company_cdf.id,
                'invoice_line_ids': line_ids,
                'ref': f"[CDF] Copie de {self.name}",
            })
            facture_CDF.action_post()



            # facture_existante = self.env['account.move'].search([
            #         ('ref', '=', self.name),
            #         ('company_id', '=', company_cdf.id),
            #         ('move_type', '=', self.move_type)
            #     ], limit=1)
                
            
            # facture_CDF = self.env['account.move'].create({'ref':self.ref,
            #                                                 'journal_id':journal_cdf.id,
            #                                                 'company_id':company_cdf.id,
            #                                                 'partner_id':self.partner_id.id,
            #                                                 'invoice_date':self.invoice_date,
            #                                                     })
            
            # for objet in self.invoice_line_ids:
            #     self.env['account.move.line'].create({'move_id':facture_CDF.id,'journal_id':journal_cdf.id,
            #                                           'company_id':company_cdf.id,
            #                                           'product_id':objet.product_id.id,
            #                                           'product_uom_id':objet.product_uom_id.id,
            #                                           'product_uom_category_id':objet.product_uom_category_id.id,
            #                                           'quantity':objet.quantity,
            #                                           'price_unit':(objet.price_unit)*2800,
            #                                           'price_subtotal':objet.price_subtotal,
            #                                           'price_total':objet.price_total,
            #                                           'discount':objet.discount,
            #                                           })
            

                

            # facture_CDF.action_post()

        else:
            if self.type_compta == 'usd_cdf':
                
                # Recherche de la société cible (CDF)
                

                journal_cdf = self.env['account.journal'].search([
                    ('company_id', '=', company_cdf.id),
                    ('type', '=', self.journal_id.type),
                ], limit=1)
                if not journal_cdf:
                    raise UserError("Aucun journal équivalent trouvé pour la société CDF.")

                lignes_cdf = []
                for line in self.line_ids:
                    # ✅ Recherche du compte par code + société dans le Many2many
                    account_obj = self.env['account.account'].sudo().with_context(force_company=company_cdf.id)
                    compte_cdf = account_obj.search([('code', '=', line.account_id.code),'|',('company_ids', 'in', [company_cdf.id]),('company_ids', '=', False)], limit=1)
                    
                    if not compte_cdf:
                        raise UserError(f"Le compte '{line.account_id.code}' n’existe pas dans la société CDF.")

                    debit_cdf = self.env['res.currency']._convert(
                        line.debit,
                        self.company_id.currency_id,
                        company_cdf.currency_id,
                        self.date,
                        self.company_id
                    )
                    credit_cdf = self.env['res.currency']._convert(
                        line.credit,
                        self.company_id.currency_id,
                        company_cdf.currency_id,
                        self.date,
                        self.company_id
                    )

                    lignes_cdf.append((0, 0, {
                        'account_id': compte_cdf.id,
                        'name': line.name,
                        'debit': line.debit_cdf,
                        'credit': line.credit_cdf,
                        'partner_id': line.partner_id.id,
                        
                    }))

                # Création de l’écriture miroir en CDF
                move_cdf = self.env['account.move'].create({
                    'ref': self.name,
                    'date': self.date,
                    'journal_id': journal_cdf.id,
                    'company_id': company_cdf.id,
                    'line_ids': lignes_cdf,
                })
                move_cdf.action_post()
                



                    
                    # if not compte_cdf:
                    #     raise ValidationError(_("Compte %s introuvable dans la société CDF.") % (line.account_id.code))

                    # values = {
                    #     'move_id': facture_cdf.id,
                    #     'account_id': compte_cdf.id,
                    # }
                    
                    # if line.amount_currency > 0:
                    #     values['debit'] = line.debit_cdf
                    # elif line.amount_currency < 0:
                    #     values['credit'] = line.credit_cdf

                    # if line.account_id.analytic and not line.analytic_distribution:
                    #     raise ValidationError(_("Impossible de continuer : le champ analytique est vide pour le compte %s.") % (line.account_id.code))

                    # self.env['account.move.line'].with_context(check_move_validity=False).create(values)
        return res
    

    def _get_cdf_account(self, source_account):
    
        company_cdf = self.env['res.company'].search([('currency_id.name', '=', 'CDF')], limit=1)
        if not company_cdf:
            return source_account  # fallback

        # Supposons que les comptes portent le même code (ex. 701100 dans les deux sociétés)
        cdf_account = self.env['account.account'].search([
            ('code', '=', source_account.code),
            '|',('company_ids', 'in', [company_cdf.id]),('company_ids', '=', False)], limit=1)
        if not cdf_account:
            raise ValueError(f"Aucun compte avec le code {source_account.code} dans la société CDF")

        return cdf_account
    

    def _get_cdf_tax(self, source_tax):
        company_cdf = self.env['res.company'].search([('currency_id.name', '=', 'CDF')], limit=1)
        if not company_cdf:
            return source_tax

        cdf_tax = self.env['account.tax'].search([
            ('name', '=', source_tax.name),
            ('company_id', '=', company_cdf.id),
        ], limit=1)

        if not cdf_tax:
            raise ValueError(f"Aucune taxe nommée '{source_tax.name}' trouvée pour la société CDF")

        return cdf_tax
    


class Location(models.Model):
    _inherit = 'stock.location'

    demandeur_id = fields.Many2one('hr.employee', string='Chef Chantier')
    compte_analytic = fields.Many2one('account.analytic.account', string='Code Chantier')


class AccountAccount(models.Model):
    _inherit = 'account.account'

    analytic = fields.Boolean('Analytique obligatoire')



class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    analytic = fields.Boolean('Analytique obligatoire')
    credit_cdf = fields.Float('Crédit CDF')
    balance_cdf = fields.Float('Balance CDF')
    debit_cdf = fields.Float('Débit CDF')
    taux_cdf = fields.Float('Taux CDF')
    taux_usd = fields.Float('Taux USD')

    @api.onchange('account_id')
    def _onchange_analytic(self):
        if self.account_id:
            self.analytic = self.account_id.analytic
        else:
            self.analytic = False

    @api.onchange('credit', 'debit', 'amount_currency')
    def _onchange_debit_credit(self):
        usd_currency = self.env['res.currency'].search([('name', '=', 'USD')], limit=1)
        cdf_currency = self.env['res.currency'].search([('name', '=', 'CDF')], limit=1)

        if not usd_currency or not cdf_currency:
            return
        
        usd_rate = usd_currency.rate or 1.0
        cdf_rate = cdf_currency.rate or 1.0
        
        self.taux_usd = usd_rate
        self.taux_cdf = cdf_rate

        if self.move_id.type_compta == 'usd_cdf' and self.currency_id.name == 'USD':
            if usd_rate == 0:
                raise ValidationError(_("Le taux de change USD est 0, veuillez le corriger avant de continuer."))

            if self.amount_currency < 0:
                # Ligne crédit
                self.credit = round((self.amount_currency / usd_rate) * (-1), 2)
                self.debit = 0.0
                self.credit_cdf = round(((self.amount_currency / usd_rate) * cdf_rate) * (-1), 2)
                self.debit_cdf = 0.0
            elif self.amount_currency > 0:
                # Ligne débit
                self.debit = round(self.amount_currency / usd_rate, 2)
                self.credit = 0.0
                self.debit_cdf = round((self.amount_currency / usd_rate) * cdf_rate, 2)
                self.credit_cdf = 0.0

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    requis_achat_id = fields.Many2one('demande.materiel', string='Réf Requisition')
    valide = fields.Boolean('Requisition OK')
    groupage_requisition = fields.One2many('ligne.groupage', 'achat_id', 'Groupage')
    mode_paiement = fields.Selection([
        ('bon Pour', "Bon Pour"),
        ('banque', "Banque"),
    ], string="Mode de Paiement")

    def button_confirm(self):
        for group in self.groupage_requisition:
            for ligne in group.name.ligne_id:
                ligne.state = 'commander'
                ligne.demande_id.state = 'commander'

        res = super(PurchaseOrder, self).button_confirm()

        picking_type = self.env['stock.picking'].search([('origin', '=', self.name)], limit=1)
        if picking_type:
            for group in self.groupage_requisition:
                group.stock_id = picking_type.id

        return res

    def valider_demamde_achat(self):
        for group in self.groupage_requisition:
            for ligne in group.name.ligne_id:
                if ligne.valide_achat:
                    self.env['purchase.order.line'].create({
                        'name': ligne.produit_id.name,
                        'product_id': ligne.produit_id.id,
                        'product_qty': ligne.qte_dif,
                        'order_id': self.id,
                    })
                    ligne.state = 'commander'
                    ligne.demande_id.state = 'commander'
        self.valide = True

    def valider_vert(self):
        self.valide_vert = True

    def button_valide_dg(self):
        self.valide_dg = True


class LigneGroupage(models.Model):
    _name = 'ligne.groupage'

    name = fields.Many2one('demande.materiel', string='Réf Requisition')
    achat_id = fields.Many2one('purchase.order', string='Commande Achat')
    stock_id = fields.Many2one('stock.picking', string='Réception')

class AccountBankStatement(models.Model):
    _inherit = 'account.bank.statement'

    mode_paiement = fields.Selection([
        ('bon Pour', "Bon Pour"),
        ('banque', "Banque"),
    ], string="Mode de Paiement")

    centre_frais = fields.Selection([
        ('440 DIRECTION', "440 DIRECTION"),
        ('544 GENIE', "544 GENIE CIVIL"),
        ('406 GARAGE', "406 GARAGE"),
        ('445 MONTAGE', "445 MONTAGE"),
        ('441 ATELIER', "441 ATELIER"),
        ('430 MAGASIN CENTRALE', "430 MAGASIN CENTRALE"),
    ], string="Centre de Frais")

    devise = fields.Many2one('res.currency', string='Devise')

class AccountBankStatementLine(models.Model):

    _inherit = 'account.bank.statement.line'

    montant = fields.Float('Montant')

    @api.onchange('amount')
    def _onchange_montant(self):
        if self.amount > 0:
            self.montant = self.amount
        else:
            self.montant = self.amount * (-1)



class EncaissementJournalier(models.Model):
    _name = 'encaissement.journalier'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            seq = self.env['ir.sequence'].with_context(force_company=vals.get('company_id')).next_by_code('encaissement.journalier')
            vals['name'] = seq or _('New')
        return super(EncaissementJournalier, self).create(vals)

    date = fields.Date('Date', default=fields.Date.today)
    name = fields.Char(string='Numéro', required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))
    libelle = fields.Char('Libellé')
    journal_id = fields.Many2one('account.journal', 'Caisse')
    partner_id = fields.Many2one('res.partner', 'Partenaire')
    company_id = fields.Many2one('res.company', 'Société', required=True, index=True, default=lambda self: self.env.company)
    montant = fields.Float('Montant')
    devise = fields.Char('Devise')
    state = fields.Selection([
        ('brouillon', 'Brouillon'),
        ('valide', 'Validé'),
    ], string='Statut', default='brouillon')
    centre_frais = fields.Selection([
        ('440_direction', '440 DIRECTION'),
        ('544_genie_civil', '544 GENIE CIVIL'),
        ('430_magasin_centrale', '430 MAGASIN CENTRALE'),
        ('406_garage', '406 GARAGE'),
        ('445_montage', '445 MONTAGE'),
        ('441_atelier', '441 ATELIER'),
    ], string='Centre de Frais')

    @api.onchange('journal_id')
    def devise_onchange(self):
        if self.journal_id:
            if self.journal_id.name == 'Caisse principale USD':
                self.devise = 'USD'
            elif self.journal_id.currency_id:
                self.devise = self.journal_id.currency_id.name
            else:
                self.devise = ''

    def creation_rapport(self):
        self.ensure_one()
        journal_obj = self.env['account.bank.statement'].search([
            ('journal_id', '=', self.journal_id.id),
            ('date', '=', self.date)
        ], limit=1)
        
        if not journal_obj:
            raise ValidationError(_("Aucun relevé trouvé pour ce journal et cette date."))

        ref = f"{self.name}/{self.libelle or ''}"
        self.env['account.bank.statement.line'].create({
            'payment_ref': ref,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'amount': self.montant,
            'statement_id': journal_obj.id,
        })


class decaissement_journalier(models.Model):

    _name = 'decaissement.journalier'

    _inherit = ['mail.thread', 'mail.activity.mixin']

    _order="name"

    @api.model
    def create(self, vals):

        if vals.get('type_decaissement') == 'bon_pour':
            vals['name'] = self.env['ir.sequence'].next_by_code('bon_pour') or _('New')
        elif vals.get('type_decaissement') == 'Depense':
            vals['name'] = self.env['ir.sequence'].next_by_code('Depense') or _('New')
        elif vals.get('type_decaissement') == 'transfert':
            vals['name'] = self.env['ir.sequence'].next_by_code('transfert') or _('New')
        elif vals.get('type_decaissement') == 'Echanger':
            vals['name'] = self.env['ir.sequence'].next_by_code('Echanger') or _('New')
        
   
        result = super(decaissement_journalier, self).create(vals)
        return result

    date = fields.Date('Date',default=fields.Date.today)
    name = fields.Char(string='num', required=True, copy=False, readonly=True,
                       index=True, default=lambda self: _('New'))
    libelle = fields.Char('libellé')
    journal_id = fields.Many2one('account.journal','Caisse')
    journal_dest = fields.Many2one('account.journal','Caisse destination')
    partner_id = fields.Many2one('res.partner','Bénéficiaire')
    montant = fields.Float('Montant')
    devise = fields.Char('Devise')
    justificatif = fields.Char('Motif')
    devise_destination = fields.Char('Devise Destination')
    montant_facture = fields.Float('Montant Facture')
    difference = fields.Float('Difference')
    Conversion = fields.Float('Conversion')
    autre_taux = fields.Boolean('Autre Taux')
    taux_autre = fields.Float('Taux')
    confirmation_autre = fields.Boolean('Autre Devise')
    devise_id = fields.Many2one('res.currency','Devise Secondaire')
    company_id = fields.Many2one('res.company', 'Company', required=True, index=True, default=lambda self: self.env.company)
    type_decaissement = fields.Selection([('bon_pour', 'Bon Pour'),
                                          ('Depense', 'Depense'),
                                          ('transfert', 'Transfert'),
                                          ('Echanger', 'Changer'),
                                          ], string='Type Opération')
    ref_facture = fields.Many2one('account.move','Réf Facture')
    ref_paiement = fields.Char('Réf paiement')
    centre_frais = fields.Selection([('440_direction', '440 DIRECTION'),
                                     ('544_genie_civil', '544 GENIE CIVIL'),
                                     ('430_magasin_centrale', '430 MAGASIN CENTRALE'),
                                     ('406_garage', '406 GARAGE'),
                                     ('445_montage', '445 MONTAGE'),
                                     ('441_atelier', '441 ATELIER')
                                     ], string='CENTRE DE FRAIS')
    
    type_transfert = fields.Selection([('cp_ca_usd', 'CP_CA_USD'),
                                      ('cp_cd_usd', 'CP_CD_USD'),
                                      ('cp_ca_cdf', 'CP_CA_CDF'),
                                      ('cp_cd_cdf', 'CP_CD_CDF'),
                                      ('ca_cp_usd', 'CA_CP_USD'),
                                      ('ca_cd_usd', 'CA_CD_USD'),
                                      ('ca_cp_cdf', 'CA_CP_CDF'),
                                      ('ca_cd_cdf', 'CA_CD_CDF'),
                                      ('cd_cp_usd', 'CD_CP_USD'),
                                      ('cd_ca_usd', 'CD_CA_USD'),
                                      ('cd_cp_cdf', 'CD_CP_CDF'),
                                      ('cd_ca_cdf', 'CD_CA_CDF'),
                                      ], string='Type Transfert')
    
    type_echange = fields.Selection([('cpusd_cpcdf', 'CPusd_CPcdf'),
                                      ('cpusd_cpeuro', 'CPusd_CPeuro'),
                                      ('cpcdf_cpusd', 'CPcdf_CPusd'),
                                      ('cpcdf_cpeuro', 'CPcdf_CPeuro'),
                                      ('cpeuro_cpusd', 'CPeuro_CPusd'),
                                      ('cpeuro_cpcdf', 'Cpeuro_Cpcdf'),
                                     
                                      ], string='Type Change')
    
    bonpour = fields.Selection([('caisse_principale', 'Caisse Principale'),
                                ('caisse_auxiliaire', 'Caisse Auxiliaire'),
                                ], string='Bon Pour')
    

    
    
    state = fields.Selection([('brouillon', 'brouillon'),
                              ('bon_pour', 'Bon Pour encours'),
                               ('confirmation_bonpour', 'Confirmation Bon Pour'),
                              ('valide', 'Valider'),
                              ('annuler', 'annuler'),                               
                              ], string='Statut')
    user_bon_pour = fields.Char('Bon Pour encours par')
    user_confirmation_bonpour = fields.Char('Confirmation Bon pour par')
    user_valide = fields.Char('Validée par')
    user_annuler = fields.Char('Annulée pqr')
    

    @api.model
    def default_get(self, fields_list):
        defaults = super(decaissement_journalier, self).default_get(fields_list)
        defaults['state'] = 'brouillon'
        return defaults
    

    @api.model
    def unlink(self):
        
        if self.state in ['bon_pour', 'confirmation_bonpour', 'valide', 'annuler']:
            if not self.env.user.has_group('base.group_system'):  
                raise exceptions.UserError("Seul un administrateur peut supprimer cet enregistrement.")
        return super(decaissement_journalier, self).unlink()
    

    
    @api.onchange('montant_facture')
    def montant_facture_onchange(self):
        if self.autre_taux == False:
            tt_cdf = self.env['res.currency.rate'].search ([('currency_id.name', '=','CDF')], limit=1)
            cdf = tt_cdf.rate 
        elif self.autre_taux == True:
            cdf = self.taux_autre
        self.difference = self.montant - self.montant_facture
        if self.devise == 'USD':
            self.Conversion = (self.montant - self.montant_facture) * cdf
        elif self.devise == 'CDF':
            self.Conversion = (self.montant - self.montant_facture) / cdf
    

    
        
    @api.onchange('journal_id')
    def devise_onchange(self):
        if self.journal_id:
            usd_journals = ['Caisse principale USD', 'Caisse direction USD', 'Caisse Auxiliaire USD']
            if self.journal_id.name in usd_journals:
                self.devise = 'USD'
            else:
                self.devise = self.journal_id.currency_id.name or ''
        else:
            self.devise = ''    

    @api.onchange('journal_dest')
    def devise_dest_onchange(self):
        if self.journal_dest:
            usd_journals = ['Caisse principale USD', 'Caisse direction USD', 'Caisse Auxiliaire USD']
            if self.journal_dest.name in usd_journals:
                self.devise_destination = 'USD'
            else:
                self.devise_destination = self.journal_dest.currency_id.name or ''
        else:
            self.devise_destination = ''       


    def cloture_bonpour (self):
        if self.difference > 0:
            self.state = 'confirmation_bonpour'
        else:
            self.state = 'valide'
        

    def annuler_bonpour (self):
        date = fields.datetime.now()
        self.difference = self.montant
        ref = 'Retour Total Bonpour' + '/' + str(self.name)
        self.env['encaissement.journalier'].create({'date':date,'libelle':ref,
                                                    'journal_id':self.journal_id.id,'montant':self.montant,'centre_frais':self.centre_frais,
                                                    'state':'valide'
                                                    })
        journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',date)])
        if journal_obj:
            ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
            self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                            'partner_id':self.partner_id.id,'amount':self.montant,'statement_id':journal_obj.id,
                                                            })
        self.state = 'annuler'
        

    
    def confirme_bonpour (self):
        date = fields.datetime.now()
        res =  self.montant - self.montant_facture
        self.difference = res
        tt_cdf = self.env['res.currency.rate'].search ([('currency_id.name', '=','CDF')], limit=1)
        cdf = tt_cdf.rate 
        self.justificatif = 'JUSTIFIER'
        if self.Conversion == True:
            if self.journal_id.name == 'Caisse principale USD':
                
                self.env['encaissement.journalier'].create({'date':date,'libelle':ref,
                                                            'journal_id':253,'montant':self.Conversion,'centre_frais':self.centre_frais,'state':'valide'
                                                            })
                journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',253),('date', '=',date)])
                if journal_obj:
                    ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                    self.env['account.bank.statement.line'].create({'payment_ref':ref,
                'partner_id':self.partner_id.id,'amount':self.Conversion,'statement_id':journal_obj.id,
                })
                self.state = 'valide'
            elif self.journal_id.name == 'Caisse principale CDF':

                self.env['encaissement.journalier'].create({'date':date,'libelle':ref,
                                                            'journal_id':255,'montant':self.Conversion,'centre_frais':self.centre_frais,'state':'valide'
                                                            })
                journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',255),('date', '=',date)])
                if journal_obj:
                    ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                    self.env['account.bank.statement.line'].create({'payment_ref':ref,
                'partner_id':self.partner_id.id,'amount':self.Conversion,'statement_id':journal_obj.id,
                })
                self.state = 'valide'
            
            elif self.journal_id.name== 'Caisse Auxiliaire USD':
                
                self.env['encaissement.journalier'].create({'date':date,'libelle':ref,
                                                            'journal_id':234,'montant':self.Conversion,'centre_frais':self.centre_frais,'state':'valide'
                                                            })
                journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',234),('date', '=',date)])
                if journal_obj:
                    ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                    self.env['account.bank.statement.line'].create({'payment_ref':ref,
                'partner_id':self.partner_id.id,'amount':self.Conversion,'statement_id':journal_obj.id,
                })
                self.state = 'valide'

            
            elif self.journal_id.name == 'Caisse Auxiliaire FC':
                
                self.env['encaissement.journalier'].create({'date':date,'libelle':ref,
                                                            'journal_id':235,'montant':self.Conversion,'centre_frais':self.centre_frais,'state':'valide'
                                                            })
                journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',235),('date', '=',date)])
                if journal_obj:
                    ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                    self.env['account.bank.statement.line'].create({'payment_ref':ref,
                'partner_id':self.partner_id.id,'amount':self.Conversion,'statement_id':journal_obj.id,
                })
                self.state = 'valide'

            


        else:
            
            
            ref = str(self.libelle) + '/' + str(self.name)
            self.env['encaissement.journalier'].create({'date':date,'libelle':ref,
            'journal_id':self.journal_id.id,'montant':res,'centre_frais':self.centre_frais,'state':'valide'
            })
                    
            journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',date)])
            if journal_obj:
                ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                tot = self.difference 
                        
                self.env['account.bank.statement.line'].create({'payment_ref':ref,
                'partner_id':self.partner_id.id,'amount':tot,'statement_id':journal_obj.id,
                })
                self.state = 'valide'
            else:
                raise ValidationError(_("Impossible de continuer vous devez faire l'ouverture de la caisse "))

            


        
            
    

    def creation_rapport (self):

        if self.type_decaissement == 'transfert':
            

            if self.devise != self.devise_destination:
                raise ValidationError(_("Impossible de continuer, vous ne pouvez pas faire le transfert avec deux devis différents"))
            else:

                caisse_dest = self.journal_dest 
                ref = str(self.name)
                self.env['encaissement.journalier'].create({'date':self.date,
                                                            'libelle':ref,
                                                            'journal_id':caisse_dest.id,
                                                            'montant':self.montant,
                                                            'centre_frais':self.centre_frais,
                                                            'state':'valide'
                                                                
                                                                })
                
                journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',self.date)])
                if journal_obj:
                
                    ref = str(self.name) + '/'  + str(self.type_decaissement)
                    tot = self.montant * (-1)
                    
                    self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                    'partner_id':self.partner_id.id,
                                                                    'amount':tot,
                                                                    'statement_id':journal_obj.id,
                                                                    
                                                                    })
                
                journal_obj_encaissement = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_dest.id),('date', '=',self.date)])
                if journal_obj_encaissement:
                
                    ref = str(self.name) + '/'  + str(self.type_decaissement)
                    tot = self.montant 
                    
                    self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                    'partner_id':self.partner_id.id,
                                                                    'amount':tot,
                                                                    'statement_id':journal_obj_encaissement.id,
                                                                    
                                                                    })
                    self.state = 'valide'
                else:
                    raise ValidationError(_("Impossible de continuer vous devez faire l'ouverture de la caisse "))
        elif self.type_decaissement == 'Echanger':
            caisse_exp = self.journal_id
            caisse_dest = self.journal_dest
            if self.devise == self.devise_destination:
                raise ValidationError(_("Impossible de continuer, vous ne pouvez pas faire le changement avec le meme  devis"))
            else:

                if caisse_exp.id == 255:
                    if self.autre_taux == True:
                        cdf = self.taux_autre
                    else:
                        tt_cdf = self.env['res.currency'].search([('name', '=','CDF')])
                        cdf = tt_cdf.rate

                    res = self.montant * cdf
                    ref = str(self.name)
                    self.env['encaissement.journalier'].create({'date':self.date,
                                                                'libelle':ref,
                                                                'journal_id':caisse_dest.id,
                                                                'montant':res,
                                                                'centre_frais':self.centre_frais,
                                                                'state':'valide'
                                                                    
                                                                    })
                    journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',self.date)])
                    if journal_obj:
                    
                        ref = str(self.name) + '/' + str(self.type_decaissement)
                        tot = self.montant * (-1)
                        
                        self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                        'partner_id':self.partner_id.id,
                                                                        'amount':tot,
                                                                        'statement_id':journal_obj.id,
                                                                        
                                                                        })
                    
                    journal_obj_encaissement = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_dest.id),('date', '=',self.date)])
                    if journal_obj_encaissement:
                
                        ref = str(self.name) + '/'  + str(self.type_decaissement)
                        tot = self.montant * cdf
                        
                        self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                        'partner_id':self.partner_id.id,
                                                                        'amount':tot,
                                                                        'statement_id':journal_obj_encaissement.id,
                                                                        
                                                                        })
                        
                    
                elif caisse_exp.id == 253:
                    if self.autre_taux == True:
                        cdf = self.taux_autre
                    else:
                        tt_cdf = self.env['res.currency'].search([('name', '=','CDF')])
                        cdf = tt_cdf.rate
                    res = self.montant / cdf
                    ref = str(self.name)
                    self.env['encaissement.journalier'].create({'date':self.date,
                                                                'libelle':ref,
                                                                'journal_id':caisse_dest.id,
                                                                'montant':res,
                                                                'centre_frais':self.centre_frais,
                                                                'state':'valide' 
                                                                    
                                                                    })
                    journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',self.date)])
                    if journal_obj:
                    
                        ref = str(self.name) + '/' + str(self.type_decaissement)
                        tot = self.montant * (-1)
                        
                        self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                        'partner_id':self.partner_id.id,
                                                                        'amount':tot,
                                                                        'statement_id':journal_obj.id,
                                                                        
                                                                        })
                    journal_obj_encaissement = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_dest.id),('date', '=',self.date)])
                    if journal_obj_encaissement:
                
                        ref = str(self.name) + '/'  + str(self.type_decaissement)
                        tot = self.montant / cdf
                        
                        self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                        'partner_id':self.partner_id.id,
                                                                        'amount':tot,
                                                                        'statement_id':journal_obj_encaissement.id,
                                                                        
                                                                        })
                        
                
                self.state = 'valide'

        elif self.type_decaissement == 'bon_pour':
            self.justificatif = 'En cours'
            
            journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',self.date)])
            if journal_obj:
                
                ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                tot = self.montant * (-1)
                
                self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                'partner_id':self.partner_id.id,
                                                                'amount':tot,
                                                                'statement_id':journal_obj.id,
                                                                
                                                                })
                if self.ref_paiement == False:
                    self.state = 'bon_pour'
                else:
                    self.state = 'valide'
            else:
                raise ValidationError(_("Impossible de continuer vous devez faire l'ouverture de la caisse "))
        else:
            journal_obj = self.env['account.bank.statement'].search([('journal_id', '=',self.journal_id.id),('date', '=',self.date)])
            if journal_obj:
                
                ref = str(self.name) + '/' + str(self.libelle) + '/' + str(self.type_decaissement)
                tot = self.montant * (-1)
                
                self.env['account.bank.statement.line'].create({'payment_ref':ref,
                                                                'partner_id':self.partner_id.id,
                                                                'amount':tot,
                                                                'statement_id':journal_obj.id,
                                                                
                                                                })
                self.state = 'valide'
            else:
                raise ValidationError(_("Impossible de continuer vous devez faire l'ouverture de la caisse "))
            

class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.constrains('name')
    def _check_duplicate_name(self):
        for record in self:
            if record.name:
                normalized_name = record.name.strip().lower()
                duplicates = self.search([
                    ('name', '=', normalized_name),
                    ('id', '!=', record.id)
                ])
                if duplicates:
                    raise ValidationError("Un client avec le même nom existe déjà : %s" % duplicates[0].name)