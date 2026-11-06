import unittest
from unittest.mock import patch, MagicMock
import json
import time
import os
import hmac
import hashlib

from app import app, get_user_premium_status

class OpenWaveIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.client = self.app.test_client()
        self.app.config['TESTING'] = True
        self.app.config['SECRET_KEY'] = 'test_secret_openwave'

    # -------------------------------------------------------------
    # REQUISITO 9: LANDING PAGE PÚBLICA
    # -------------------------------------------------------------
    def test_public_landing_page_accessible_without_login(self):
        """Verifica se visitantes não logados veem a landing page do OpenWave na raiz."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.data.decode('utf-8')
        self.assertIn('OpenWave', html)
        self.assertIn('A rede colaborativa de escuta de rádio regional', html)
        self.assertIn('Criar Conta', html)
        self.assertIn('Sentinela Pro', html)

    # -------------------------------------------------------------
    # REQUISITO 10: NADA DE TOM HANKS / TMDB / FILMES
    # -------------------------------------------------------------
    def test_no_prohibited_terms_in_templates_and_routes(self):
        """Garante que termos do projeto antigo (Tom Hanks, TMDB) foram 100% removidos."""
        rules = [rule.rule for rule in self.app.url_map.iter_rules()]
        for r in rules:
            self.assertNotIn('movie', r.lower())
            self.assertNotIn('filme', r.lower())
            self.assertNotIn('tmdb', r.lower())

        # Varre templates principais
        templates_dir = os.path.join(os.path.dirname(__file__), 'templates')
        for fname in os.listdir(templates_dir):
            if fname.endswith('.html'):
                with open(os.path.join(templates_dir, fname), 'r', encoding='utf-8') as f:
                    content = f.read().lower()
                    self.assertNotIn('tom hanks', content, f"Termo proibido encontrado em {fname}")
                    self.assertNotIn('tmdb', content, f"Termo proibido encontrado em {fname}")

    # -------------------------------------------------------------
    # REQUISITO 3: API PRÓPRIA COM CRUD DO RECURSO PRINCIPAL
    # -------------------------------------------------------------
    def test_routes_exist(self):
        """Verifica se os endpoints solicitados de canais, stripe e auditoria existem."""
        rules = [rule.rule for rule in self.app.url_map.iter_rules()]
        self.assertIn('/api/canais', rules)
        self.assertIn('/api/canais/<int:canal_id>', rules)
        self.assertIn('/canais/novo', rules)
        self.assertIn('/canais/<int:canal_id>', rules)
        self.assertIn('/canais/<int:canal_id>/editar', rules)
        self.assertIn('/canais/<int:canal_id>/remover', rules)
        self.assertIn('/canais/<int:canal_id>/gravacoes', rules)
        self.assertIn('/webhook/stripe', rules)
        self.assertIn('/admin/logs', rules)

    @patch('app.get_db_connection')
    def test_api_list_channels(self, mock_db):
        """Testa listagem pública de canais de rádio via API."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            {
                'id': 1,
                'nome': 'Bombeiros SP',
                'frequencia': '193.000 MHz',
                'categoria': 'Resgate & Bombeiros',
                'cidade': 'São Paulo',
                'estado': 'SP',
                'status': 'online',
                'feeder_nome': 'Estação Feeder SP'
            }
        ]

        response = self.client.get('/api/canais')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['canais']), 1)
        self.assertEqual(data['canais'][0]['frequencia'], '193.000 MHz')

    # -------------------------------------------------------------
    # REQUISITO 5: PAPÉIS DO NEGÓCIO E PERMISSÕES (RBAC - 403)
    # -------------------------------------------------------------
    @patch('app.get_user_role')
    def test_rbac_listener_cannot_create_channel(self, mock_get_role):
        """Um 'listener' tentando criar canal deve receber 403 Forbidden."""
        mock_get_role.return_value = 'listener'
        with self.client.session_transaction() as sess:
            sess['user_id'] = 10
            sess['nome'] = 'Ouvinte Comum'
            sess['role'] = 'listener'

        # Via formulário web
        res_web = self.client.get('/canais/novo')
        self.assertEqual(res_web.status_code, 403)

        # Via API direta
        res_api = self.client.post('/api/canais', json={
            'nome': 'Canal Invasor',
            'frequencia': '144.000 MHz',
            'cidade': 'São Paulo'
        }, headers={'X-User-Id': '10'})
        self.assertEqual(res_api.status_code, 403)

    @patch('app.get_db_connection')
    @patch('app.send_audit_log')
    @patch('app.get_user_role')
    def test_rbac_feeder_can_create_channel(self, mock_get_role, mock_audit, mock_db):
        """Um 'feeder' ou 'admin' pode criar canal com sucesso."""
        mock_get_role.return_value = 'feeder'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.lastrowid = 42

        res_api = self.client.post('/api/canais', json={
            'nome': 'SAMU Campinas',
            'frequencia': '155.175 MHz',
            'categoria': 'Resgate & Bombeiros',
            'cidade': 'Campinas',
            'estado': 'SP'
        }, headers={'X-User-Id': '2'})

        self.assertEqual(res_api.status_code, 201)
        data = res_api.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['canal_id'], 42)
        mock_audit.assert_called_with('canal_criado_42', '2')

    # -------------------------------------------------------------
    # REQUISITO 6: ISOLAMENTO POR USUÁRIO (MULTI-TENANCY - 403)
    # -------------------------------------------------------------
    @patch('app.get_db_connection')
    @patch('app.get_user_role')
    def test_multitenancy_isolation_edit_other_user_channel(self, mock_get_role, mock_db):
        """Feeder B (ID 20) tentando editar canal do Feeder A (ID 10) recebe 403 Forbidden."""
        mock_get_role.return_value = 'feeder'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        # O canal pertence ao usuário ID 10
        mock_cursor.fetchone.return_value = {
            'id': 1,
            'usuario_id': 10,
            'nome': 'Canal do Feeder A',
            'frequencia': '155.000 MHz',
            'categoria': 'Resgate & Bombeiros',
            'cidade': 'Santos',
            'estado': 'SP',
            'status': 'online'
        }

        # Usuário autenticado na sessão é ID 20 (Feeder B)
        with self.client.session_transaction() as sess:
            sess['user_id'] = 20
            sess['nome'] = 'Feeder B'
            sess['role'] = 'feeder'

        # Tentativa via tela web
        res_web = self.client.get('/canais/1/editar')
        self.assertEqual(res_web.status_code, 403)

        # Tentativa via API PUT
        res_api = self.client.put('/api/canais/1', json={'nome': 'Tentativa Hacker'}, headers={'X-User-Id': '20'})
        self.assertEqual(res_api.status_code, 403)

    @patch('app.get_db_connection')
    @patch('app.get_user_role')
    def test_multitenancy_isolation_delete_other_user_channel(self, mock_get_role, mock_db):
        """Feeder B tentando excluir canal do Feeder A recebe 403 Forbidden."""
        mock_get_role.return_value = 'feeder'
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {
            'id': 1,
            'usuario_id': 10,
            'nome': 'Canal do Feeder A'
        }

        with self.client.session_transaction() as sess:
            sess['user_id'] = 20
            sess['role'] = 'feeder'

        res_web = self.client.post('/canais/1/remover')
        self.assertEqual(res_web.status_code, 403)

        res_api = self.client.delete('/api/canais/1', headers={'X-User-Id': '20'})
        self.assertEqual(res_api.status_code, 403)

    # -------------------------------------------------------------
    # REQUISITO 7: AUDITORIA / EVENTOS DE NEGÓCIO NO LOG
    # -------------------------------------------------------------
    @patch('app.get_user_role')
    def test_admin_logs_access_control(self, mock_get_role):
        """Apenas administradores podem ver os logs do sistema."""
        # Usuário comum (listener)
        mock_get_role.return_value = 'listener'
        with self.client.session_transaction() as sess:
            sess['user_id'] = 5
            sess['role'] = 'listener'
        res_listener = self.client.get('/admin/logs')
        self.assertEqual(res_listener.status_code, 403)

        # Admin
        mock_get_role.return_value = 'admin'
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'admin'
        with patch('requests.get') as mock_req:
            mock_req.return_value.status_code = 200
            mock_req.return_value.json.return_value = {'logs': []}
            res_admin = self.client.get('/admin/logs')
            self.assertEqual(res_admin.status_code, 200)

    # -------------------------------------------------------------
    # REQUISITO 8: MINIO / UPLOAD LIGADO AO DOMÍNIO
    # -------------------------------------------------------------
    @patch('app.get_db_connection')
    @patch('app.get_user_role')
    def test_upload_audio_to_channel_permission(self, mock_get_role, mock_db):
        """Listener não pode enviar áudio para canal; feeder proprietário tem permissão."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {'id': 1, 'usuario_id': 10, 'nome': 'Canal 1'}

        # Listener tentando enviar áudio
        mock_get_role.return_value = 'listener'
        with self.client.session_transaction() as sess:
            sess['user_id'] = 99
            sess['role'] = 'listener'

        response = self.client.post('/canais/1/gravacoes', data={
            'titulo': 'Gravação Não Autorizada'
        })
        self.assertEqual(response.status_code, 403)

    # -------------------------------------------------------------
    # STRIPE: SENTINELA PRO E WEBHOOK CRIPTOGRÁFICO
    # -------------------------------------------------------------
    def test_webhook_rejects_invalid_signature_when_secret_set(self):
        """Valida que requisições com assinatura HMAC forjada são rejeitadas com 400."""
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
    def test_webhook_checkout_completed_upgrades_user(self, mock_audit, mock_db):
        """Valida promoção do usuário para o Plano Sentinela Pro via webhook assinado."""
        test_secret = 'whsec_test_sentinela_999'
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
                        'id': 'cs_test_sentinela',
                        'customer': 'cus_test_sentinela_123',
                        'subscription': 'sub_test_sentinela_456',
                        'client_reference_id': '88',
                        'metadata': {'user_id': '88', 'plano': 'sentinela_pro'}
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
            mock_audit.assert_called_with('upgrade_sentinela_pro_confirmado_88', '88')
        finally:
            if old_secret is not None:
                os.environ['STRIPE_WEBHOOK_SECRET'] = old_secret
            else:
                os.environ.pop('STRIPE_WEBHOOK_SECRET', None)

if __name__ == '__main__':
    unittest.main()
