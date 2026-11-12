import unittest
from unittest.mock import patch, MagicMock
import json
import time
import os
import hmac
import hashlib

from app import (
    app,
    get_user_premium_status,
    obter_dados_assinatura,
    verificar_permissao_plano,
    PLANOS_DISPONIVEIS
)

class StripeIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test_secret_stripe'

    def test_routes_exist(self):
        """Verifica se todos os endpoints SaaS do Stripe, portal e planos foram registrados corretamente."""
        rules = [rule.rule for rule in self.app.url_map.iter_rules()]
        self.assertIn('/checkout', rules)
        self.assertIn('/stripe/checkout', rules)
        self.assertIn('/webhook/stripe', rules)
        self.assertIn('/stripe/webhook', rules)
        self.assertIn('/api/stripe/webhook', rules)
        self.assertIn('/checkout/sucesso', rules)
        self.assertIn('/checkout/cancelado', rules)
        # Novos endpoints SaaS: Portal, Troca de Plano, Cancelamento e Métricas
        self.assertIn('/assinatura/portal', rules)
        self.assertIn('/api/billing/portal', rules)
        self.assertIn('/assinatura/trocar-plano', rules)
        self.assertIn('/assinatura/cancelar', rules)
        self.assertIn('/admin/assinaturas', rules)
        self.assertIn('/api/me/plano', rules)

    def test_webhook_rejects_invalid_signature_when_secret_set(self):
        """Requisito de Segurança: Valida que requisições com assinatura forjada/inválida são rejeitadas com 400."""
        old_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
        os.environ['STRIPE_WEBHOOK_SECRET'] = 'whsec_test_secret_12345'
        
        try:
            payload = json.dumps({'type': 'checkout.session.completed'})
            response = self.client.post(
                '/webhook/stripe',
                data=payload,
                content_type='application/json',
                headers={'Stripe-Signature': 't=12345,v1=assinatura_invalida'}
            )
            self.assertEqual(response.status_code, 400)
        finally:
            if old_secret is not None:
                os.environ['STRIPE_WEBHOOK_SECRET'] = old_secret
            else:
                os.environ.pop('STRIPE_WEBHOOK_SECRET', None)

    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    def test_webhook_valid_signature_handling(self, mock_log, mock_db):
        """Valida processamento de webhook com assinatura HMAC legítima e promoção de plano."""
        test_secret = 'whsec_test_secret_99999'
        old_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')
        os.environ['STRIPE_WEBHOOK_SECRET'] = test_secret

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        try:
            timestamp = int(time.time())
            payload_data = {
                'id': 'evt_test_123',
                'object': 'event',
                'type': 'checkout.session.completed',
                'data': {
                    'object': {
                        'id': 'cs_test_session',
                        'customer': 'cus_test_abc123',
                        'subscription': 'sub_test_xyz789',
                        'client_reference_id': '999999',
                        'amount_total': 990,
                        'metadata': {'user_id': '999999', 'plano': 'sentinela_pro'}
                    }
                }
            }
            payload_bytes = json.dumps(payload_data).encode('utf-8')
            signed_payload = f"{timestamp}.".encode('utf-8') + payload_bytes
            signature = hmac.new(
                test_secret.encode('utf-8'),
                signed_payload,
                hashlib.sha256
            ).hexdigest()
            header = f"t={timestamp},v1={signature}"

            response = self.client.post(
                '/webhook/stripe',
                data=payload_bytes,
                content_type='application/json',
                headers={'Stripe-Signature': header}
            )

            self.assertEqual(response.status_code, 200)
            mock_cursor.execute.assert_called()
            mock_conn.commit.assert_called()
            mock_log.assert_called_with('upgrade_sentinela_pro_confirmado_999999', '999999')
        finally:
            if old_secret is not None:
                os.environ['STRIPE_WEBHOOK_SECRET'] = old_secret
            else:
                os.environ.pop('STRIPE_WEBHOOK_SECRET', None)

    # -------------------------------------------------------------------------
    # NOVO REQUISITO SAAS: O QUE O PLANO PERMITE A CADA REQUISIÇÃO
    # -------------------------------------------------------------------------
    @patch('app.get_db_connection')
    def test_plan_entitlements_per_request(self, mock_db):
        """Valida que o sistema consulta o banco e sabe exatamente o que o plano permite a cada requisição."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # 1. Usuário Free ativo
        mock_cursor.fetchone.return_value = {
            'id': 10,
            'is_premium': 0,
            'plano': 'free',
            'status_assinatura': 'active',
            'stripe_customer_id': None,
            'stripe_subscription_id': None,
            'assinatura_valor_centavos': 0
        }
        dados_free = obter_dados_assinatura(10)
        self.assertEqual(dados_free['plano_efetivo'], 'free')
        self.assertFalse(dados_free['is_premium'])
        self.assertEqual(dados_free['direitos']['max_favoritos'], 3)
        self.assertEqual(dados_free['direitos']['delay_segundos'], 600)
        self.assertFalse(dados_free['direitos']['permite_download'])

        # 2. Usuário Pro ativo
        mock_cursor.fetchone.return_value = {
            'id': 20,
            'is_premium': 1,
            'plano': 'pro',
            'status_assinatura': 'active',
            'stripe_customer_id': 'cus_pro',
            'stripe_subscription_id': 'sub_pro',
            'assinatura_valor_centavos': 990
        }
        dados_pro = obter_dados_assinatura(20)
        self.assertEqual(dados_pro['plano_efetivo'], 'pro')
        self.assertTrue(dados_pro['is_premium'])
        self.assertIsNone(dados_pro['direitos']['max_favoritos']) # Ilimitado
        self.assertEqual(dados_pro['direitos']['delay_segundos'], 0) # Sem delay

        # 3. Usuário Central Tática VIP ativo
        mock_cursor.fetchone.return_value = {
            'id': 30,
            'is_premium': 1,
            'plano': 'central',
            'status_assinatura': 'active',
            'stripe_customer_id': 'cus_central',
            'stripe_subscription_id': 'sub_central',
            'assinatura_valor_centavos': 2990
        }
        dados_central = obter_dados_assinatura(30)
        self.assertEqual(dados_central['plano_efetivo'], 'central')
        self.assertTrue(dados_central['is_premium'])
        self.assertTrue(dados_central['direitos']['permite_download'])

        # 4. REGRA SAAS: Usuário que assinou Pro, mas está com cobrança atrasada (past_due)
        # Os direitos do plano pago só valem com a assinatura ativa: no past_due cai no Free!
        mock_cursor.fetchone.return_value = {
            'id': 40,
            'is_premium': 0,
            'plano': 'pro',
            'status_assinatura': 'past_due',
            'stripe_customer_id': 'cus_atrasado',
            'stripe_subscription_id': 'sub_atrasado',
            'assinatura_valor_centavos': 990
        }
        dados_inadimplente = obter_dados_assinatura(40)
        self.assertEqual(dados_inadimplente['plano_efetivo'], 'free')
        self.assertFalse(dados_inadimplente['is_premium'])
        self.assertTrue(dados_inadimplente['inadimplente'])
        self.assertEqual(dados_inadimplente['direitos']['max_favoritos'], 3)

    # -------------------------------------------------------------------------
    # LIMITES DO FREE BARRADOS NO BACKEND (403 FORBIDDEN + LIMITE.HTML)
    # -------------------------------------------------------------------------
    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    def test_favoritar_canal_limit_enforced_with_403(self, mock_audit, mock_db):
        """Ao atingir 3 favoritos no plano Free, a próxima tentativa responde HTTP 403 Forbidden."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Usuário é Free
        mock_cursor.fetchone.side_effect = [
            {'id': 50, 'is_premium': 0, 'plano': 'free', 'status_assinatura': 'active', 'stripe_customer_id': None, 'stripe_subscription_id': None, 'assinatura_valor_centavos': 0},
            (3,) # Já favoritou 3 canais
        ]

        with self.client.session_transaction() as sess:
            sess['user_id'] = 50
            sess['role'] = 'listener'

        response = self.client.post('/canais/1/favoritar')
        self.assertEqual(response.status_code, 403)
        html = response.data.decode('utf-8')
        self.assertIn('Limite do Plano Comunitário Gratuito', html)
        self.assertIn('HTTP 403', html)
        mock_audit.assert_called_with('403_limite_favoritos_atingido', 50)

    # -------------------------------------------------------------------------
    # AUTOATENDIMENTO: TROCAR DE PLANO E CANCELAR SOZINHO
    # -------------------------------------------------------------------------
    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    def test_self_service_trocar_plano(self, mock_audit, mock_db):
        """O cliente pode trocar de plano sozinho (ex: Pro -> Central Tática) diretamente na app."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Consulta dados atuais
        mock_cursor.fetchone.return_value = {
            'id': 60,
            'is_premium': 1,
            'plano': 'pro',
            'status_assinatura': 'active',
            'stripe_customer_id': 'cus_60',
            'stripe_subscription_id': 'sub_60',
            'assinatura_valor_centavos': 990
        }

        with self.client.session_transaction() as sess:
            sess['user_id'] = 60
            sess['role'] = 'listener'

        response = self.client.post('/assinatura/trocar-plano', data={'plano': 'central'})
        self.assertEqual(response.status_code, 302)
        mock_cursor.execute.assert_called()
        mock_conn.commit.assert_called()
        mock_audit.assert_called_with('troca_plano_pro_para_central', 60)

    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    def test_self_service_cancelar_assinatura(self, mock_audit, mock_db):
        """O cliente pode cancelar a assinatura sozinho, retornando ao plano Free imediatamente."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchone.return_value = {
            'id': 70,
            'is_premium': 1,
            'plano': 'pro',
            'status_assinatura': 'active',
            'stripe_customer_id': 'cus_70',
            'stripe_subscription_id': 'sub_70',
            'assinatura_valor_centavos': 990
        }

        with self.client.session_transaction() as sess:
            sess['user_id'] = 70
            sess['role'] = 'listener'

        response = self.client.post('/assinatura/cancelar')
        self.assertEqual(response.status_code, 302)
        mock_cursor.execute.assert_called()
        mock_conn.commit.assert_called()
        mock_audit.assert_called_with('cancelamento_assinatura_usuario_70', 70)

    @patch('stripe.billing_portal.Session.create')
    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    def test_portal_cliente_endpoint(self, mock_audit, mock_db, mock_portal):
        """O cliente pode abrir o Customer Portal oficial do Stripe para autoatendimento."""
        mock_portal.return_value = MagicMock(url='https://billing.stripe.com/p/session_test_xyz')
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {
            'id': 80,
            'is_premium': 1,
            'plano': 'pro',
            'status_assinatura': 'active',
            'stripe_customer_id': 'cus_80',
            'stripe_subscription_id': 'sub_80',
            'assinatura_valor_centavos': 990
        }

        old_key = os.environ.get('STRIPE_SECRET_KEY')
        os.environ['STRIPE_SECRET_KEY'] = 'sk_test_fake_key'

        try:
            with self.client.session_transaction() as sess:
                sess['user_id'] = 80
                sess['role'] = 'listener'

            response = self.client.post('/assinatura/portal')
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.location, 'https://billing.stripe.com/p/session_test_xyz')
            mock_portal.assert_called_once()
            mock_audit.assert_called_with('portal_faturamento_aberto', 80)
        finally:
            if old_key is not None:
                os.environ['STRIPE_SECRET_KEY'] = old_key
            else:
                os.environ.pop('STRIPE_SECRET_KEY', None)

    # -------------------------------------------------------------------------
    # IDEMPOTÊNCIA E CICLO DE VIDA COMPLETO DE WEBHOOKS
    # -------------------------------------------------------------------------
    @patch('app.get_db_connection')
    def test_webhook_idempotency_duplicate_event_ignored(self, mock_db):
        """Eventos repetidos do Stripe com mesmo event_id são ignorados sem reprocessamento."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Simula que o banco disparou IntegrityError (evento já inserido anteriormente)
        import mysql.connector
        mock_cursor.execute.side_effect = mysql.connector.IntegrityError(1062, "Duplicate entry for key 'PRIMARY'")

        payload = {
            'id': 'evt_duplicado_111',
            'object': 'event',
            'type': 'checkout.session.completed',
            'data': {'object': {}}
        }
        res = self.client.post('/webhook/stripe', json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json().get('duplicate'))

    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    def test_webhook_subscription_lifecycle_events(self, mock_audit, mock_db):
        """Valida eventos de ciclo de vida: updated, deleted e invoice.payment_failed."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # 1. customer.subscription.updated (past_due)
        ev_updated = {
            'id': 'evt_up_1',
            'type': 'customer.subscription.updated',
            'data': {
                'object': {
                    'id': 'sub_123',
                    'customer': 'cus_123',
                    'status': 'past_due'
                }
            }
        }
        res_up = self.client.post('/webhook/stripe', json=ev_updated)
        self.assertEqual(res_up.status_code, 200)
        mock_audit.assert_called_with('assinatura_atualizada_past_due', 'cus_123')

        # 2. customer.subscription.deleted
        ev_deleted = {
            'id': 'evt_del_1',
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {
                    'id': 'sub_123',
                    'customer': 'cus_123'
                }
            }
        }
        res_del = self.client.post('/webhook/stripe', json=ev_deleted)
        self.assertEqual(res_del.status_code, 200)
        mock_audit.assert_called_with('cancelamento_sentinela_pro_sub_123')

        # 3. invoice.payment_failed
        ev_failed = {
            'id': 'evt_fail_1',
            'type': 'invoice.payment_failed',
            'data': {
                'object': {
                    'customer': 'cus_999',
                    'subscription': 'sub_999'
                }
            }
        }
        res_fail = self.client.post('/webhook/stripe', json=ev_failed)
        self.assertEqual(res_fail.status_code, 200)
        mock_audit.assert_called_with('pagamento_falhou_cus_999', 'cus_999')

    # -------------------------------------------------------------------------
    # PAINEL ADMIN DE ASSINATURAS E MRR
    # -------------------------------------------------------------------------
    @patch('app.get_user_role')
    @patch('app.get_db_connection')
    def test_admin_assinaturas_dashboard(self, mock_db, mock_get_role):
        """Acesso exclusivo para administradores ao painel de métricas SaaS com MRR."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # Ouvinte comum tentando acessar responde 403
        mock_get_role.return_value = 'listener'
        with self.client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['role'] = 'listener'
        res_listener = self.client.get('/admin/assinaturas')
        self.assertEqual(res_listener.status_code, 403)

        # Admin acessando responde 200 com métricas
        mock_get_role.return_value = 'admin'
        mock_cursor.fetchall.side_effect = [
            [{'plano': 'free', 'total': 10}, {'plano': 'pro', 'total': 4}], # contagem_planos
            [{'event_id': 'evt_1', 'tipo': 'checkout.session.completed', 'recebido_em': '2026-11-12 12:00:00'}] # stripe_eventos
        ]
        mock_cursor.fetchone.side_effect = [
            {'ativas': 4, 'mrr_centavos': 3960}, # resumo_mrr
            {'total_inadimplentes': 0} # inadimplentes
        ]

        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'admin'
        res_admin = self.client.get('/admin/assinaturas')
        self.assertEqual(res_admin.status_code, 200)
        html = res_admin.data.decode('utf-8')
        self.assertIn('Métricas de Faturamento e Assinaturas SaaS', html)
        self.assertIn('R$ 39,60', html)

if __name__ == '__main__':
    unittest.main()
