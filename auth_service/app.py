import os
import uuid
import smtplib
import threading
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import mysql.connector
import requests
from flask import Flask, request, jsonify
from flasgger import Swagger
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
swagger = Swagger(app, template={
    'info': {
        'title': 'OpenWave Auth Service API',
        'version': '2.0.0',
        'description': 'Serviço de Autenticação, RBAC (admin, feeder, listener) e Recuperação de Senha do OpenWave.'
    }
})
app.secret_key = os.getenv('SECRET_KEY', 'auth_default_secret')
LOG_SERVICE_URL = os.getenv('LOG_SERVICE_URL', 'http://log-service:5000')

def send_audit_log(usuario_id, acao):
    try:
        requests.post(f"{LOG_SERVICE_URL}/api/log", json={
            'usuario_id': usuario_id,
            'acao': acao,
            'ip_origem': request.remote_addr
        }, timeout=2)
    except Exception as e:
        print(f"[Auth Service] Erro ao enviar log: {e}")

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )

def init_db():
    """Garante que as tabelas de usuarios, tokens e do domínio OpenWave existam."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Tabela usuarios (RBAC: admin, feeder, listener)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nome VARCHAR(100) NOT NULL,
                email VARCHAR(100) NOT NULL UNIQUE,
                senha_hash VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'listener',
                bio TEXT,
                avatar_url VARCHAR(255),
                is_premium BOOLEAN NOT NULL DEFAULT 0,
                stripe_customer_id VARCHAR(255),
                stripe_subscription_id VARCHAR(255),
                criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        
        # Garante migração de colunas caso tabela já existisse
        for col_def in [
            "ALTER TABLE usuarios ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'listener'",
            "ALTER TABLE usuarios ADD COLUMN bio TEXT",
            "ALTER TABLE usuarios ADD COLUMN avatar_url VARCHAR(255)",
            "ALTER TABLE usuarios ADD COLUMN is_premium BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE usuarios ADD COLUMN stripe_customer_id VARCHAR(255)",
            "ALTER TABLE usuarios ADD COLUMN stripe_subscription_id VARCHAR(255)",
            "ALTER TABLE usuarios ADD COLUMN criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ]:
            try:
                cursor.execute(col_def)
                conn.commit()
            except mysql.connector.Error:
                pass

        # Atualiza roles legados ('usuario' -> 'listener') se existirem
        try:
            cursor.execute("UPDATE usuarios SET role = 'listener' WHERE role = 'usuario'")
            conn.commit()
        except Exception:
            pass

        # Tabela reset_tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reset_tokens (
                id INT AUTO_INCREMENT PRIMARY KEY,
                token VARCHAR(255) NOT NULL UNIQUE,
                usuario_id INT NOT NULL,
                criado_em DATETIME NOT NULL,
                expira_em DATETIME NOT NULL,
                usado BOOLEAN NOT NULL DEFAULT 0,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()

        # Tabelas principais do domínio OpenWave
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS canais (
                id INT AUTO_INCREMENT PRIMARY KEY,
                usuario_id INT NOT NULL,
                nome VARCHAR(120) NOT NULL,
                frequencia VARCHAR(60) NOT NULL,
                categoria VARCHAR(50) NOT NULL,
                cidade VARCHAR(100) NOT NULL,
                estado VARCHAR(2) NOT NULL,
                descricao TEXT,
                status VARCHAR(20) NOT NULL DEFAULT 'online',
                stream_url VARCHAR(255),
                icone_url VARCHAR(255),
                delay_segundos INT NOT NULL DEFAULT 600,
                criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gravacoes_ocorrencias (
                id INT AUTO_INCREMENT PRIMARY KEY,
                canal_id INT NOT NULL,
                usuario_id INT NOT NULL,
                titulo VARCHAR(150) NOT NULL,
                audio_url VARCHAR(255) NOT NULL,
                duracao_segundos INT NOT NULL DEFAULT 60,
                data_hora_ocorrencia DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                descricao TEXT,
                criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (canal_id) REFERENCES canais(id) ON DELETE CASCADE,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS canais_favoritos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                usuario_id INT NOT NULL,
                canal_id INT NOT NULL,
                criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_user_canal (usuario_id, canal_id),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
                FOREIGN KEY (canal_id) REFERENCES canais(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """)
        conn.commit()

        cursor.close()
        conn.close()
        print("[Auth Service] Esquema OpenWave inicializado com sucesso.")
    except Exception as e:
        print(f"[Auth Service] Erro ao inicializar banco de dados: {e}")

try:
    init_db()
except Exception as e:
    print(f"[Auth Service] Aviso na inicialização: {e}")

def send_reset_email(to_email, reset_url):
    """Envia o e-mail de redefinição de senha via Mailtrap/Brevo SMTP."""
    mail_server = os.getenv('MAIL_SERVER', 'sandbox.smtp.mailtrap.io')
    mail_port = int(os.getenv('MAIL_PORT', 2525))
    mail_user = os.getenv('MAIL_USERNAME', '')
    mail_pass = os.getenv('MAIL_PASSWORD', '')
    mail_from = os.getenv('MAIL_FROM', 'no-reply@openwave.local')

    print(f"[Auth Service] Enviando e-mail de redefinição para: {to_email}")
    print(f"[Auth Service] Link de redefinição: {reset_url}")

    if not mail_user or not mail_pass:
        print("[Auth Service] Credenciais SMTP não configuradas. Link apenas exibido nos logs.")
        return True

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Redefinição de Senha - OpenWave'
        msg['From'] = mail_from
        msg['To'] = to_email

        text_content = f"Olá!\n\nVocê solicitou a redefinição de sua senha na plataforma OpenWave.\nClique no link abaixo para criar uma nova senha (válido por 30 minutos):\n{reset_url}\n\nSe você não solicitou esta alteração, ignore este e-mail."
        html_content = f"""
        <html>
          <body style="font-family: Arial, sans-serif; background-color: #0b0f19; color: #e2e8f0; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #161e2e; padding: 30px; border-radius: 8px; border: 1px solid #2d3748;">
              <h2 style="color: #38bdf8;">📡 OpenWave — Rede de Rádio Regional</h2>
              <p>Você solicitou a redefinição de sua senha no sistema <strong>OpenWave</strong>.</p>
              <p>Clique no botão abaixo para criar uma nova senha com segurança (válido por 30 minutos):</p>
              <p style="margin: 25px 0;">
                <a href="{reset_url}" style="padding: 12px 24px; background-color: #0284c7; color: white; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">Redefinir Senha</a>
              </p>
              <p style="color: #94a3b8; font-size: 0.9em;">Se o botão não funcionar, copie e cole este link no navegador:</p>
              <p style="word-break: break-all;"><a href="{reset_url}" style="color: #38bdf8;">{reset_url}</a></p>
              <hr style="border: 0; border-top: 1px solid #334155; margin: 20px 0;">
              <p style="color: #64748b; font-size: 0.8em;">Se você não solicitou esta redefinição, desconsidere esta mensagem com segurança.</p>
            </div>
          </body>
        </html>
        """

        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(mail_server, mail_port) as server:
            server.starttls()
            server.login(mail_user, mail_pass)
            server.sendmail(mail_from, [to_email], msg.as_string())
            
        print("[Auth Service] E-mail de redefinição enviado com sucesso via SMTP!")
        return True
    except Exception as e:
        print(f"[Auth Service] Erro ao enviar e-mail via SMTP: {e}")
        return False

@app.route('/api/register', methods=['POST'])
def register():
    """
    Registra um novo usuário no OpenWave com papel 'listener' ou 'feeder'.
    ---
    tags:
      - Autenticação
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            nome:
              type: string
            email:
              type: string
            senha:
              type: string
            role:
              type: string
              enum: ['listener', 'feeder']
              default: 'listener'
    responses:
      201:
        description: Usuário registrado com sucesso
      400:
        description: Erro de validação ou email já cadastrado
      500:
        description: Erro interno
    """
    data = request.get_json() or request.form
    nome = data.get('nome')
    email = data.get('email')
    senha = data.get('senha')
    requested_role = (data.get('role') or 'listener').lower()

    # Apenas 'listener' e 'feeder' podem ser auto-atribuídos no registro público
    if requested_role not in ['listener', 'feeder']:
        role = 'listener'
    else:
        role = requested_role

    if not nome or not email or not senha:
        return jsonify({'success': False, 'error': 'Preencha todos os campos obrigatórios.'}), 400

    senha_hash = generate_password_hash(senha)

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, role) VALUES (%s, %s, %s, %s)",
            (nome, email, senha_hash, role)
        )
        conn.commit()
        user_id = cursor.lastrowid
        send_audit_log(user_id, f'registro_usuario_{role}')
        return jsonify({
            'success': True,
            'message': 'Conta criada com sucesso no OpenWave.',
            'user': {'id': user_id, 'nome': nome, 'email': email, 'role': role}
        }), 201
    except mysql.connector.IntegrityError:
        return jsonify({'success': False, 'error': 'E-mail já cadastrado.'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    """
    Autentica um usuário e retorna dados com papel de negócio e status premium.
    ---
    tags:
      - Autenticação
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
            senha:
              type: string
    responses:
      200:
        description: Login realizado com sucesso
      400:
        description: Dados incompletos
      401:
        description: Credenciais inválidas
    """
    data = request.get_json() or request.form
    email = data.get('email')
    senha = data.get('senha')

    if not email or not senha:
        return jsonify({'success': False, 'error': 'Preencha e-mail e senha.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()
        
        if user and check_password_hash(user['senha_hash'], senha):
            role = user.get('role', 'listener')
            # Tratamento para roles legados
            if role == 'usuario':
                role = 'listener'

            send_audit_log(user['id'], 'login')
            return jsonify({
                'success': True,
                'user': {
                    'id': user['id'],
                    'nome': user['nome'],
                    'email': user['email'],
                    'role': role,
                    'avatar_url': user.get('avatar_url'),
                    'bio': user.get('bio'),
                    'is_premium': bool(user.get('is_premium', 0))
                }
            }), 200
            
        send_audit_log(email, 'tentativa_login_falha')
        return jsonify({'success': False, 'error': 'Credenciais inválidas.'}), 401
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    """
    Solicita recuperação de senha e despacha e-mail seguro com token UUIDv4.
    ---
    tags:
      - Recuperação
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            email:
              type: string
    responses:
      200:
        description: E-mail de recuperação enviado (resposta neutra por segurança)
      400:
        description: E-mail não fornecido
    """
    data = request.get_json() or request.form
    email = data.get('email')
    base_url = data.get('base_url') or os.getenv('APP_URL', 'http://localhost:5000')

    if not email:
        return jsonify({'success': False, 'error': 'Informe o e-mail.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
        user = cursor.fetchone()

        if user:
            token = uuid.uuid4().hex
            criado_em = datetime.now()
            expira_em = criado_em + timedelta(minutes=30)
            
            cursor.execute(
                """INSERT INTO reset_tokens (token, usuario_id, criado_em, expira_em, usado)
                   VALUES (%s, %s, %s, %s, 0)""",
                (token, user['id'], criado_em, expira_em)
            )
            conn.commit()

            reset_url = f"{base_url.rstrip('/')}/reset-password/{token}"
            threading.Thread(target=send_reset_email, args=(email, reset_url), daemon=True).start()

        return jsonify({
            'success': True,
            'message': 'Se o e-mail estiver cadastrado, você receberá o link para redefinir sua senha.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/verify-token', methods=['POST'])
def verify_token():
    """
    Verifica a integridade e validade de um token de redefinição de senha.
    ---
    tags:
      - Recuperação
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            token:
              type: string
    responses:
      200:
        description: Status de validação do token retornado
    """
    data = request.get_json() or request.form
    token = data.get('token')

    if not token:
        return jsonify({'valid': False, 'reason': 'Token não fornecido.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM reset_tokens WHERE token = %s", (token,))
        record = cursor.fetchone()

        if not record:
            return jsonify({'valid': False, 'reason': 'Token não encontrado.'})

        if record['usado']:
            return jsonify({'valid': False, 'reason': 'Este link de redefinição já foi utilizado.'})

        if record['expira_em'] < datetime.now():
            return jsonify({'valid': False, 'reason': 'Este link de redefinição expirou (válido por 30 minutos).'})

        return jsonify({'valid': True})
    except Exception as e:
        return jsonify({'valid': False, 'reason': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    """
    Redefine a senha de um usuário utilizando um token de acesso válido.
    ---
    tags:
      - Recuperação
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            token:
              type: string
            nova_senha:
              type: string
    responses:
      200:
        description: Senha redefinida com sucesso
      400:
        description: Token inválido ou expirado
    """
    data = request.get_json() or request.form
    token = data.get('token')
    nova_senha = data.get('nova_senha')

    if not token or not nova_senha:
        return jsonify({'success': False, 'error': 'Token e nova senha são obrigatórios.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM reset_tokens WHERE token = %s", (token,))
        record = cursor.fetchone()

        if not record:
            return jsonify({'success': False, 'error': 'Token inválido ou não encontrado.'}), 400

        if record['usado']:
            return jsonify({'success': False, 'error': 'Este link de redefinição já foi utilizado.'}), 400

        if record['expira_em'] < datetime.now():
            return jsonify({'success': False, 'error': 'Este link de redefinição expirou.'}), 400

        nova_hash = generate_password_hash(nova_senha)
        cursor.execute("UPDATE usuarios SET senha_hash = %s WHERE id = %s", (nova_hash, record['usuario_id']))
        cursor.execute("UPDATE reset_tokens SET usado = 1 WHERE id = %s", (record['id'],))
        conn.commit()

        send_audit_log(record['usuario_id'], 'senha_redefinida')
        return jsonify({'success': True, 'message': 'Senha redefinida com sucesso!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/check-role/<int:user_id>', methods=['GET'])
def check_role(user_id):
    """
    Consulta o papel (role: 'admin', 'feeder', 'listener') de um usuário.
    ---
    tags:
      - Autorização
    parameters:
      - in: path
        name: user_id
        type: integer
        required: true
    responses:
      200:
        description: Role e status premium retornados
      404:
        description: Usuário não encontrado
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT role, is_premium FROM usuarios WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if user:
            role = user.get('role', 'listener')
            if role == 'usuario':
                role = 'listener'
            return jsonify({
                'role': role,
                'is_premium': bool(user.get('is_premium', 0))
            }), 200
        return jsonify({'role': 'listener', 'is_premium': False}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/check-premium/<int:user_id>', methods=['GET'])
def check_premium(user_id):
    """
    Consulta o status do Plano Sentinela Pro ativo no Stripe.
    ---
    tags:
      - Pagamento
    parameters:
      - in: path
        name: user_id
        type: integer
        required: true
    responses:
      200:
        description: Status retornado com sucesso
      404:
        description: Usuário não encontrado
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT is_premium, stripe_customer_id, stripe_subscription_id FROM usuarios WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if user:
            return jsonify({
                'is_premium': bool(user.get('is_premium', 0)),
                'stripe_customer_id': user.get('stripe_customer_id'),
                'stripe_subscription_id': user.get('stripe_subscription_id')
            }), 200
        return jsonify({'is_premium': False}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
