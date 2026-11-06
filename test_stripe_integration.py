import unittest
from unittest.mock import patch, MagicMock
import json
import time
import os
import hmac
import hashlib

from app import app, get_user_premium_status

class StripeIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test_secret_stripe'

    def test_routes_exist(self):
        """Verifica se os endpoints do Stripe foram registrados corretamente."""
        rules = [rule.rule for rule in self.app.url_map.iter_rules()]
        self.assertIn('/checkout', rules)
        self.assertIn('/stripe/checkout', rules)
        self.assertIn('/webhook/stripe', rules)
        self.assertIn('/stripe/webhook', rules)
        self.assertIn('/api/stripe/webhook', rules)
        self.assertIn('/checkout/sucesso', rules)
        self.assertIn('/checkout/cancelado', rules)

    def test_webhook_rejects_invalid_signature_when_secret_set(self):
        """Requisito 3: Valida que requisições com assinatura forjada/inválida são rejeitadas."""
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
        """Valida processamento de webhook com assinatura HMAC legítima."""
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

if __name__ == '__main__':
    unittest.main()
