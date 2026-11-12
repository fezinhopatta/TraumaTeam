import os
import io
import uuid
import json
import requests
import mysql.connector
import stripe
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort, jsonify
from flasgger import Swagger
from minio import Minio
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'openwave_secret_key_change_me')

swagger = Swagger(app, template={
    'info': {
        'title': 'OpenWave Core API',
        'version': '2.0.0',
        'description': 'API do OpenWave para gerenciamento de canais de rádio, escuta e transmissões de emergência.'
    }
})

# ==============================================================
# CONFIGURAÇÕES DE SERVIÇOS EXTERNOS E INTERNOS
# ==============================================================
AUTH_SERVICE_URL = os.getenv('AUTH_SERVICE_URL', 'http://auth-service:5000')
LOG_SERVICE_URL = os.getenv('LOG_SERVICE_URL', 'http://log-service:5000')

# Stripe
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY')
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# MinIO
MINIO_HOST = os.getenv('MINIO_ENDPOINT', 'minio')
MINIO_PORT = os.getenv('MINIO_PORT', '9000')
if ':' in MINIO_HOST:
    MINIO_ENDPOINT = MINIO_HOST
else:
    MINIO_ENDPOINT = f"{MINIO_HOST}:{MINIO_PORT}"

MINIO_ACCESS_KEY = os.getenv('MINIO_ROOT_USER', os.getenv('MINIO_ACCESS_KEY', 'minioadmin'))
MINIO_SECRET_KEY = os.getenv('MINIO_ROOT_PASSWORD', os.getenv('MINIO_SECRET_KEY', 'minioadmin123'))
MINIO_PUBLIC_URL = os.getenv('MINIO_PUBLIC_URL', 'http://localhost:9010')
MINIO_BUCKET = os.getenv('MINIO_BUCKET', 'openwave-midias')

try:
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )
    # Garante que o bucket exista e tenha política de leitura pública
    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{MINIO_BUCKET}/*"]
            }]
        }
        minio_client.set_bucket_policy(MINIO_BUCKET, json.dumps(policy))
except Exception as e:
    print(f"[OpenWave] Aviso ao inicializar cliente MinIO: {e}")

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'}

def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS

def allowed_audio(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_AUDIO_EXTENSIONS

# ==============================================================
# AUDITORIA E BANCO DE DADOS
# ==============================================================
def send_audit_log(acao, user_id=None):
    """Envia eventos de negócio estruturados para o log-service / Redis."""
    try:
        uid = user_id or session.get('user_id', 'anonimo')
        requests.post(f"{LOG_SERVICE_URL}/api/log", json={
            'usuario_id': str(uid),
            'acao': acao,
            'ip_origem': request.remote_addr
        }, timeout=2)
    except Exception as e:
        print(f"[OpenWave] Erro ao enviar log de auditoria: {e}")

def get_db_connection():
    return mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )

def init_domain_db():
    """Garante a existência das 3 tabelas relacionais do domínio OpenWave."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Usuários
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

        # 2. Canais (Recurso Principal do OpenWave)
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

        # 3. Gravações de Ocorrências (Arquivos de áudio no MinIO)
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

        # 4. Canais Favoritos por Usuário
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

        # 5. Expansão para Modelo SaaS: Planos Recorrentes e Idempotência Stripe
        try:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS plano VARCHAR(20) NOT NULL DEFAULT 'free'")
            cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS status_assinatura VARCHAR(30) NULL DEFAULT 'active'")
            cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS assinatura_valor_centavos INT NULL DEFAULT 0")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stripe_eventos (
                    event_id VARCHAR(100) NOT NULL PRIMARY KEY,
                    tipo VARCHAR(80) NOT NULL,
                    recebido_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
        except Exception as mig_err:
            print(f"[OpenWave] Detalhe de migração SaaS: {mig_err}")

        conn.commit()
        cursor.close()
        conn.close()
        print("[OpenWave] Esquema relacional do domínio inicializado com sucesso.")
    except Exception as e:
        print(f"[OpenWave] Aviso ao inicializar tabelas do banco: {e}")

try:
    init_domain_db()
except Exception as e:
    print(f"[OpenWave] Erro na inicialização do banco: {e}")

def get_user_role(user_id):
    """Consulta o papel (role) do usuário no auth-service com fallback para sessão."""
    if not user_id:
        return 'listener'
    try:
        res = requests.get(f"{AUTH_SERVICE_URL}/api/check-role/{user_id}", timeout=3)
        if res.status_code == 200:
            return res.json().get('role', 'listener')
    except Exception:
        pass
    return session.get('role', 'listener')

# ==============================================================
# MODELO SAAS: PLANOS, DIREITOS E REGRAS DE NEGÓCIO POR REQUISIÇÃO
# ==============================================================

PLANOS_DISPONIVEIS = {
    'free': {
        'id': 'free',
        'nome': 'Plano Comunitário',
        'rotulo': 'Comunitário Free',
        'valor_mensal_centavos': 0,
        'preco_formatado': 'R$ 0,00',
        'max_favoritos': 3,
        'delay_segundos': 600,  # 10 minutos de delay proposital
        'acesso_gravacoes': False,
        'permite_download': False,
        'descricao': 'Acesso a canais abertos com delay de 10 minutos e limite de 3 frequências salvas.'
    },
    'pro': {
        'id': 'pro',
        'nome': 'Sentinela Pro',
        'rotulo': 'Sentinela Pro',
        'valor_mensal_centavos': 990,
        'preco_formatado': 'R$ 9,90/mês',
        'max_favoritos': None,  # Ilimitado
        'delay_segundos': 0,    # Tempo real sem delay
        'acesso_gravacoes': True,
        'permite_download': False,
        'descricao': 'Streaming em tempo real com baixa latência, favoritos ilimitados e acervo de 48h.'
    },
    'central': {
        'id': 'central',
        'nome': 'Central Tática VIP',
        'rotulo': 'Central Tática VIP',
        'valor_mensal_centavos': 2990,
        'preco_formatado': 'R$ 29,90/mês',
        'max_favoritos': None,  # Ilimitado
        'delay_segundos': 0,    # Prioridade máxima
        'acesso_gravacoes': True,
        'permite_download': True,
        'descricao': 'Prioridade total na transmissão, download direto de áudios (.mp3) e canais táticos.'
    }
}

STATUS_ATIVOS = ('active', 'trialing')

def obter_dados_assinatura(user_id):
    """
    Consulta o banco a cada requisição para determinar os direitos vigentes do usuário.
    Se o status for 'past_due', 'canceled' ou inativo, os direitos caem imediatamente
    para o plano Free, garantindo que o Pro/Central não continue de graça sem pagamento ativo.
    """
    free_plan = PLANOS_DISPONIVEIS['free']
    padrao = {
        'plano_contratado': 'free',
        'plano_efetivo': 'free',
        'status_assinatura': 'active',
        'is_premium': False,
        'em_dia': True,
        'inadimplente': False,
        'stripe_customer_id': None,
        'stripe_subscription_id': None,
        'assinatura_valor_centavos': 0,
        'direitos': free_plan,
        'rotulo': free_plan['rotulo']
    }
    if not user_id:
        return padrao

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, is_premium, 
                   COALESCE(plano, 'free') as plano, 
                   COALESCE(status_assinatura, 'active') as status_assinatura,
                   stripe_customer_id, stripe_subscription_id,
                   COALESCE(assinatura_valor_centavos, 0) as assinatura_valor_centavos
            FROM usuarios 
            WHERE id = %s
        """, (user_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return padrao

        if isinstance(row, dict):
            raw_premium = bool(row.get('is_premium', 0))
            raw_plano = row.get('plano') or ('pro' if raw_premium else 'free')
            status = (row.get('status_assinatura') or 'active').lower()
            customer_id = row.get('stripe_customer_id')
            subscription_id = row.get('stripe_subscription_id')
            valor_centavos = row.get('assinatura_valor_centavos') or 0
        else:
            raw_premium = bool(row[1]) if len(row) > 1 else False
            raw_plano = 'pro' if raw_premium else 'free'
            status = 'active'
            customer_id = None
            subscription_id = None
            valor_centavos = 990 if raw_premium else 0

        if raw_plano not in PLANOS_DISPONIVEIS:
            raw_plano = 'pro' if raw_premium else 'free'

        em_dia = status in STATUS_ATIVOS
        inadimplente = (status == 'past_due')

        if em_dia and (raw_plano in ('pro', 'central') or raw_premium):
            plano_efetivo = raw_plano if raw_plano in ('pro', 'central') else 'pro'
            is_premium_efetivo = True
        else:
            plano_efetivo = 'free'
            is_premium_efetivo = False

        direitos = PLANOS_DISPONIVEIS[plano_efetivo]

        return {
            'plano_contratado': raw_plano,
            'plano_efetivo': plano_efetivo,
            'status_assinatura': status,
            'is_premium': is_premium_efetivo,
            'em_dia': em_dia,
            'inadimplente': inadimplente,
            'stripe_customer_id': customer_id,
            'stripe_subscription_id': subscription_id,
            'assinatura_valor_centavos': valor_centavos or direitos['valor_mensal_centavos'],
            'direitos': direitos,
            'rotulo': PLANOS_DISPONIVEIS[raw_plano]['rotulo']
        }
    except Exception as e:
        print(f"[OpenWave] Erro ao consultar dados de assinatura: {e}")
        return padrao

def get_user_premium_status(user_id):
    """
    Mantém retrocompatibilidade: retorna True se o usuário tiver direitos Pro/Central ativos agora.
    """
    dados = obter_dados_assinatura(user_id)
    return dados['is_premium']

def verificar_permissao_plano(user_id, feature, **kwargs):
    """
    Verifica a cada requisição se a ação solicitada é permitida pelo plano efetivo do usuário.
    Retorna (permitido: bool, detalhe_erro: dict|None).
    """
    dados = obter_dados_assinatura(user_id)
    direitos = dados['direitos']

    if feature == 'favoritar':
        max_fav = direitos['max_favoritos']
        if max_fav is None:
            return True, None
        total_atual = kwargs.get('total_atual', 0)
        if total_atual >= max_fav:
            return False, {
                'recurso': 'favoritos',
                'limite': max_fav,
                'atual': total_atual,
                'plano_atual': dados['rotulo'],
                'plano_sugerido': 'Sentinela Pro',
                'mensagem': f"⚠️ Limite atingido! O plano gratuito permite favoritar até {max_fav} canais. Assine o Plano Sentinela Pro por R$ 9,90/mês para monitoramento ilimitado!"
            }
        return True, None

    elif feature == 'acesso_gravacoes':
        if direitos['acesso_gravacoes']:
            return True, None
        return False, {
            'recurso': 'gravacoes',
            'plano_atual': dados['rotulo'],
            'plano_sugerido': 'Sentinela Pro',
            'mensagem': "O acervo de gravações históricas é exclusivo para assinantes Sentinela Pro ou Central Tática."
        }

    elif feature == 'download_audio':
        if direitos['permite_download']:
            return True, None
        return False, {
            'recurso': 'download',
            'plano_atual': dados['rotulo'],
            'plano_sugerido': 'Central Tática VIP',
            'mensagem': "O download de arquivos de áudio é exclusivo do plano Central Tática VIP."
        }

    return True, None

# ==============================================================
# ROTAS PÚBLICAS E LANDING PAGE
# ==============================================================

@app.route('/')
def index():
    """
    Se o usuário não estiver autenticado, exibe a Landing Page pública com a proposta de valor.
    Se estiver autenticado, renderiza o Dashboard do OpenWave com a grade de canais.
    """
    if 'user_id' not in session:
        # Requisito 9: Landing page pública sem login
        return render_template('landing.html')

    # Usuário autenticado -> Dashboard de Canais
    user_id = session['user_id']
    is_premium = get_user_premium_status(user_id)
    session['is_premium'] = is_premium
    user_role = get_user_role(user_id)
    session['role'] = user_role

    categoria_filtro = request.args.get('categoria', '').strip()
    busca_filtro = request.args.get('busca', '').strip()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT c.*, u.nome as feeder_nome,
               (SELECT COUNT(*) FROM canais_favoritos cf WHERE cf.canal_id = c.id) as total_ouvintes
        FROM canais c
        JOIN usuarios u ON c.usuario_id = u.id
        WHERE 1=1
    """
    params = []
    if categoria_filtro:
        query += " AND c.categoria = %s"
        params.append(categoria_filtro)
    if busca_filtro:
        query += " AND (c.nome LIKE %s OR c.cidade LIKE %s OR c.frequencia LIKE %s)"
        like_term = f"%{busca_filtro}%"
        params.extend([like_term, like_term, like_term])

    query += " ORDER BY c.status = 'online' DESC, c.id DESC"

    cursor.execute(query, tuple(params))
    canais = cursor.fetchall()

    # Recupera IDs dos canais favoritados pelo usuário
    cursor.execute("SELECT canal_id FROM canais_favoritos WHERE usuario_id = %s", (user_id,))
    favoritos = {row['canal_id'] for row in cursor.fetchall()}

    cursor.close()
    conn.close()

    return render_template(
        'index.html',
        canais=canais,
        favoritos=favoritos,
        is_premium=is_premium,
        user_role=user_role,
        categoria_filtro=categoria_filtro,
        busca_filtro=busca_filtro,
        limite_favoritos=3
    )

@app.route('/landing')
def public_landing():
    """Acesso direto à landing page pública mesmo estando logado."""
    return render_template('landing.html')

# ==============================================================
# AUTENTICAÇÃO E SESSÃO
# ==============================================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        role = request.form.get('role', 'listener')

        try:
            res = requests.post(f"{AUTH_SERVICE_URL}/api/register", json={
                'nome': nome,
                'email': email,
                'senha': senha,
                'role': role
            }, timeout=15)
            data = res.json()
            if res.status_code in [200, 201] and data.get('success'):
                flash('Conta criada com sucesso no OpenWave! Faça login para começar.')
                return redirect(url_for('login'))
            else:
                flash(data.get('error', 'Erro ao realizar cadastro.'))
        except requests.RequestException as e:
            flash(f'Erro ao conectar ao serviço de autenticação: {e}')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')

        try:
            res = requests.post(f"{AUTH_SERVICE_URL}/api/login", json={
                'email': email,
                'senha': senha
            }, timeout=15)
            data = res.json()
            if res.status_code == 200 and data.get('success'):
                user = data.get('user', {})
                session['user_id'] = user.get('id')
                session['nome'] = user.get('nome')
                session['role'] = user.get('role', 'listener')
                session['avatar_url'] = user.get('avatar_url')
                session['is_premium'] = bool(user.get('is_premium', False))
                send_audit_log('login_sucesso', user.get('id'))
                return redirect(url_for('index'))
            else:
                flash(data.get('error', 'Credenciais inválidas.'))
        except requests.RequestException as e:
            flash(f'Erro de conexão com o serviço de autenticação: {e}')

    return render_template('login.html')

@app.route('/logout')
def logout():
    send_audit_log('logout')
    session.clear()
    flash('Você saiu do sistema com segurança.')
    return redirect(url_for('index'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        try:
            base_url = os.getenv('APP_URL') or request.host_url
            res = requests.post(f"{AUTH_SERVICE_URL}/api/forgot-password", json={
                'email': email,
                'base_url': base_url
            }, timeout=15)
            data = res.json()
            flash(data.get('message', 'Solicitação processada com sucesso.'))
        except requests.RequestException as e:
            flash(f'Erro ao comunicar com o serviço de autenticação: {e}')
        return redirect(url_for('login'))

    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        res_verify = requests.post(f"{AUTH_SERVICE_URL}/api/verify-token", json={'token': token}, timeout=15)
        data_verify = res_verify.json()
        if not data_verify.get('valid'):
            return render_template('reset_password.html', error=data_verify.get('reason', 'Link inválido ou expirado.'))
    except requests.RequestException as e:
        return render_template('reset_password.html', error=f'Erro de conexão com autenticação: {e}')

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        try:
            res = requests.post(f"{AUTH_SERVICE_URL}/api/reset-password", json={
                'token': token,
                'nova_senha': nova_senha
            }, timeout=15)
            data = res.json()
            if res.status_code == 200 and data.get('success'):
                flash('Senha redefinida com sucesso! Faça login com a nova senha.')
                return redirect(url_for('login'))
            else:
                return render_template('reset_password.html', error=data.get('error', 'Erro ao redefinir senha.'))
        except requests.RequestException as e:
            return render_template('reset_password.html', error=f'Erro ao comunicar com autenticação: {e}')

    return render_template('reset_password.html', error=None)

# ==============================================================
# CRUD DO RECURSO PRINCIPAL: CANAIS DE RÁDIO
# ==============================================================

@app.route('/canais/novo', methods=['GET', 'POST'])
def canal_novo():
    """
    Criação de novo canal de rádio.
    Acesso restrito a 'feeder' e 'admin'. Se 'listener' tentar, responde 403 Forbidden.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user_role = get_user_role(user_id)

    # Requisito 5: Bloqueio 403 para papéis não permitidos
    if user_role not in ['feeder', 'admin']:
        send_audit_log('403_permissao_negada_criar_canal', user_id)
        abort(403, description="Apenas operadores de estação (feeder) e administradores podem cadastrar canais de rádio.")

    if request.method == 'POST':
        nome = request.form.get('nome')
        frequencia = request.form.get('frequencia')
        categoria = request.form.get('categoria')
        cidade = request.form.get('cidade')
        estado = (request.form.get('estado') or 'SP').upper()
        descricao = request.form.get('descricao')
        stream_url = request.form.get('stream_url')
        status = request.form.get('status', 'online')
        delay_segundos = int(request.form.get('delay_segundos') or 600)

        icone_url = None
        foto = request.files.get('icone')
        if foto and foto.filename:
            if allowed_image(foto.filename):
                file_data = foto.read()
                filename = secure_filename(foto.filename)
                ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
                object_name = f"canais/icone_{uuid.uuid4().hex}.{ext}"
                try:
                    minio_client.put_object(
                        MINIO_BUCKET,
                        object_name,
                        io.BytesIO(file_data),
                        len(file_data),
                        content_type=foto.content_type
                    )
                    icone_url = url_for('serve_icone', filename=object_name)
                except Exception as e:
                    print(f"[MinIO] Erro ao salvar ícone do canal: {e}")

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO canais (usuario_id, nome, frequencia, categoria, cidade, estado, descricao, status, stream_url, icone_url, delay_segundos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, nome, frequencia, categoria, cidade, estado, descricao, status, stream_url, icone_url, delay_segundos))
            conn.commit()
            canal_id = cursor.lastrowid
            send_audit_log(f'canal_criado_{canal_id}', user_id)
            flash(f'Canal de rádio "{nome}" ({frequencia}) cadastrado com sucesso!')
            return redirect(url_for('canal_detalhe', canal_id=canal_id))
        except Exception as e:
            flash(f'Erro ao cadastrar canal: {e}')
        finally:
            cursor.close()
            conn.close()

    return render_template('canal_form.html', canal=None, modo='novo')

@app.route('/canais/<int:canal_id>')
def canal_detalhe(canal_id):
    """
    Exibe os detalhes da frequência, player de áudio, status e histórico de ocorrências gravadas.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    is_premium = get_user_premium_status(user_id)
    user_role = get_user_role(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT c.*, u.nome as feeder_nome, u.email as feeder_email
        FROM canais c
        JOIN usuarios u ON c.usuario_id = u.id
        WHERE c.id = %s
    """, (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        abort(404, description="Canal de rádio não encontrado.")

    # Busca ocorrências gravadas
    cursor.execute("""
        SELECT g.*, u.nome as autor_nome
        FROM gravacoes_ocorrencias g
        JOIN usuarios u ON g.usuario_id = u.id
        WHERE g.canal_id = %s
        ORDER BY g.data_hora_ocorrencia DESC
    """, (canal_id,))
    gravacoes = cursor.fetchall()

    # Verifica se o canal é favorito
    cursor.execute("SELECT id FROM canais_favoritos WHERE usuario_id = %s AND canal_id = %s", (user_id, canal_id))
    is_favorito = bool(cursor.fetchone())

    cursor.close()
    conn.close()

    is_owner = (canal['usuario_id'] == user_id)
    can_manage = is_owner or (user_role == 'admin')

    send_audit_log(f'acesso_canal_{canal_id}', user_id)

    return render_template(
        'canal_detalhe.html',
        canal=canal,
        gravacoes=gravacoes,
        is_favorito=is_favorito,
        is_premium=is_premium,
        is_owner=is_owner,
        can_manage=can_manage
    )

@app.route('/canais/<int:canal_id>/editar', methods=['GET', 'POST'])
def canal_editar(canal_id):
    """
    Edição de canal de rádio.
    Requisito 6 (Isolamento por Usuário): Apenas o feeder proprietário ou o admin podem editar.
    Tentar abrir ou alterar registro de outro usuário retorna 403 Forbidden.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user_role = get_user_role(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM canais WHERE id = %s", (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        abort(404, description="Canal de rádio não encontrado.")

    # Requisito 6: Isolamento por usuário (multi-tenancy) e RBAC
    is_owner = (canal['usuario_id'] == user_id)
    if not is_owner and user_role != 'admin':
        cursor.close()
        conn.close()
        send_audit_log(f'403_acesso_negado_editar_canal_{canal_id}', user_id)
        abort(403, description="403 Forbidden - Você não tem permissão para editar um canal pertencente a outra estação transmissora.")

    if request.method == 'POST':
        nome = request.form.get('nome') or canal['nome']
        frequencia = request.form.get('frequencia') or canal['frequencia']
        categoria = request.form.get('categoria') or canal['categoria']
        cidade = request.form.get('cidade') or canal['cidade']
        estado = (request.form.get('estado') or canal['estado']).upper()
        descricao = request.form.get('descricao')
        stream_url = request.form.get('stream_url')
        status = request.form.get('status', canal['status'])

        icone_url = canal['icone_url']
        foto = request.files.get('icone')
        if foto and foto.filename and allowed_image(foto.filename):
            file_data = foto.read()
            filename = secure_filename(foto.filename)
            ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
            object_name = f"canais/icone_{uuid.uuid4().hex}.{ext}"
            try:
                minio_client.put_object(
                    MINIO_BUCKET,
                    object_name,
                    io.BytesIO(file_data),
                    len(file_data),
                    content_type=foto.content_type
                )
                icone_url = url_for('serve_icone', filename=object_name)
            except Exception as e:
                print(f"[MinIO] Erro ao atualizar ícone: {e}")

        try:
            cursor.execute("""
                UPDATE canais
                SET nome = %s, frequencia = %s, categoria = %s, cidade = %s, estado = %s, descricao = %s, status = %s, stream_url = %s, icone_url = %s
                WHERE id = %s
            """, (nome, frequencia, categoria, cidade, estado, descricao, status, stream_url, icone_url, canal_id))
            conn.commit()
            send_audit_log(f'canal_atualizado_{canal_id}', user_id)
            flash('Canal de rádio atualizado com sucesso!')
            return redirect(url_for('canal_detalhe', canal_id=canal_id))
        except Exception as e:
            flash(f'Erro ao atualizar canal: {e}')
        finally:
            cursor.close()
            conn.close()

    cursor.close()
    conn.close()
    return render_template('canal_form.html', canal=canal, modo='editar')

@app.route('/canais/<int:canal_id>/remover', methods=['POST'])
def canal_remover(canal_id):
    """
    Exclusão do canal de rádio.
    Requisito 6 (Isolamento por Usuário): Apenas o feeder dono ou admin podem remover.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user_role = get_user_role(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM canais WHERE id = %s", (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        abort(404, description="Canal não encontrado.")

    is_owner = (canal['usuario_id'] == user_id)
    if not is_owner and user_role != 'admin':
        cursor.close()
        conn.close()
        send_audit_log(f'403_acesso_negado_remover_canal_{canal_id}', user_id)
        abort(403, description="403 Forbidden - Você não tem permissão para remover canais de outro operador.")

    try:
        cursor.execute("DELETE FROM canais WHERE id = %s", (canal_id,))
        conn.commit()
        send_audit_log(f'canal_removido_{canal_id}', user_id)
        flash(f'Canal "{canal["nome"]}" excluído com sucesso.')
    except Exception as e:
        flash(f'Erro ao excluir canal: {e}')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('index'))

# ==============================================================
# UPLOAD NO MINIO LIGADO AO DOMÍNIO: GRAVAÇÕES DE OCORRÊNCIAS
# ==============================================================

@app.route('/canais/<int:canal_id>/gravacoes', methods=['POST'])
def gravar_ocorrencia(canal_id):
    """
    Requisito 8: Upload de arquivo de áudio (.mp3/.ogg/.wav) de transmissão de emergência no MinIO.
    Acesso restrito ao operador dono do canal ou admin.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user_role = get_user_role(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM canais WHERE id = %s", (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        abort(404, description="Canal não encontrado.")

    is_owner = (canal['usuario_id'] == user_id)
    if not is_owner and user_role != 'admin':
        cursor.close()
        conn.close()
        send_audit_log(f'403_tentativa_upload_canal_alheio_{canal_id}', user_id)
        abort(403, description="Apenas a estação dona do canal ou administradores podem publicar gravações.")

    titulo = request.form.get('titulo') or 'Despacho de Ocorrência'
    descricao = request.form.get('descricao', '')
    duracao_segundos = int(request.form.get('duracao_segundos') or 60)
    audio_file = request.files.get('audio')

    if not audio_file or not audio_file.filename:
        cursor.close()
        conn.close()
        flash("Nenhum arquivo de áudio selecionado.")
        return redirect(url_for('canal_detalhe', canal_id=canal_id))

    if not allowed_audio(audio_file.filename):
        cursor.close()
        conn.close()
        flash("Formato de áudio inválido. Use .mp3, .ogg, .wav, .m4a.")
        return redirect(url_for('canal_detalhe', canal_id=canal_id))

    file_bytes = audio_file.read()
    if len(file_bytes) > 25 * 1024 * 1024:
        cursor.close()
        conn.close()
        flash("O arquivo de gravação deve ter no máximo 25MB.")
        return redirect(url_for('canal_detalhe', canal_id=canal_id))

    filename = secure_filename(audio_file.filename)
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'mp3'
    object_name = f"gravacoes/audio_{canal_id}_{uuid.uuid4().hex}.{ext}"

    try:
        minio_client.put_object(
            MINIO_BUCKET,
            object_name,
            io.BytesIO(file_bytes),
            len(file_bytes),
            content_type=audio_file.content_type or 'audio/mpeg'
        )
        audio_url = url_for('serve_audio', filename=object_name)

        cursor.execute("""
            INSERT INTO gravacoes_ocorrencias (canal_id, usuario_id, titulo, audio_url, duracao_segundos, descricao)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (canal_id, user_id, titulo, audio_url, duracao_segundos, descricao))
        conn.commit()
        gravacao_id = cursor.lastrowid
        send_audit_log(f'gravacao_audio_enviada_{gravacao_id}', user_id)
        flash('Gravação de ocorrência publicada com sucesso!')
    except Exception as e:
        flash(f'Erro ao enviar gravação para o storage MinIO: {e}')
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for('canal_detalhe', canal_id=canal_id))

# ==============================================================
# SERVIÇO DE ARQUIVOS (MINIO OBJECT STORAGE)
# ==============================================================

@app.route('/audio/<path:filename>')
def serve_audio(filename):
    """Entrega streaming de áudio gravado diretamente do MinIO."""
    try:
        response = minio_client.get_object(MINIO_BUCKET, filename)
        file_data = response.read()
        response.close()
        response.release_conn()
        mimetype = 'audio/mpeg'
        if filename.endswith('.ogg'): mimetype = 'audio/ogg'
        elif filename.endswith('.wav'): mimetype = 'audio/wav'
        elif filename.endswith('.m4a'): mimetype = 'audio/mp4'
        return send_file(io.BytesIO(file_data), mimetype=mimetype)
    except Exception as e:
        print(f"[MinIO] Erro ao servir áudio: {e}")
        abort(404)

@app.route('/icone/<path:filename>')
def serve_icone(filename):
    """Entrega ícones/emblemas de canais armazenados no MinIO."""
    try:
        response = minio_client.get_object(MINIO_BUCKET, filename)
        file_data = response.read()
        response.close()
        response.release_conn()
        return send_file(io.BytesIO(file_data), mimetype='image/jpeg')
    except Exception as e:
        print(f"[MinIO] Erro ao servir ícone: {e}")
        abort(404)

@app.route('/avatar/<path:filename>')
def serve_avatar(filename):
    """Entrega avatares de usuário a partir do MinIO."""
    try:
        response = minio_client.get_object(MINIO_BUCKET, filename)
        file_data = response.read()
        response.close()
        response.release_conn()
        return send_file(io.BytesIO(file_data), mimetype='image/jpeg')
    except Exception as e:
        print(f"[MinIO] Erro ao servir avatar: {e}")
        abort(404)

# ==============================================================
# FAVORITOS (RECURSO DO NEGÓCIO COM BENEFÍCIO STRIPE)
# ==============================================================

@app.route('/canais/<int:canal_id>/favoritar', methods=['POST'])
def favoritar_canal(canal_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    is_premium = get_user_premium_status(user_id)
    session['is_premium'] = is_premium
    LIMITE_GRATUITO = 3

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM canais_favoritos WHERE usuario_id = %s", (user_id,))
        (total_favoritos,) = cursor.fetchone()

        permitido, detalhe_erro = verificar_permissao_plano(user_id, 'favoritar', total_atual=total_favoritos)
        if not permitido:
            send_audit_log('403_limite_favoritos_atingido', user_id)
            cursor.close()
            conn.close()
            if request.is_json or request.headers.get('Accept') == 'application/json':
                return jsonify({'error': 'Limite do plano atingido', 'detalhes': detalhe_erro}), 403
            flash(detalhe_erro['mensagem'])
            return render_template('limite.html', limite=LIMITE_GRATUITO, total=total_favoritos, detalhe=detalhe_erro), 403

        cursor.execute("INSERT INTO canais_favoritos (usuario_id, canal_id) VALUES (%s, %s)", (user_id, canal_id))
        conn.commit()
        send_audit_log(f'canal_favoritado_{canal_id}', user_id)
        flash('Canal adicionado aos seus favoritos com sucesso!')
    except mysql.connector.IntegrityError:
        flash('Este canal já está na sua lista de favoritos.')
    except Exception as e:
        flash(f'Erro ao favoritar: {e}')
    finally:
        cursor.close()
        conn.close()

    return redirect(request.referrer or url_for('index'))

@app.route('/canais/<int:canal_id>/desfavoritar', methods=['POST'])
def desfavoritar_canal(canal_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM canais_favoritos WHERE usuario_id = %s AND canal_id = %s", (user_id, canal_id))
        conn.commit()
        send_audit_log(f'canal_desfavoritado_{canal_id}', user_id)
        flash('Canal removido dos favoritos.')
    except Exception as e:
        flash(f'Erro ao desfavoritar: {e}')
    finally:
        cursor.close()
        conn.close()

    return redirect(request.referrer or url_for('index'))

# ==============================================================
# PERFIL DO USUÁRIO
# ==============================================================

@app.route('/perfil/<int:user_id>', methods=['GET', 'POST'])
def perfil(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, nome, email, role, bio, avatar_url, is_premium, 
               COALESCE(plano, 'free') as plano, 
               COALESCE(status_assinatura, 'active') as status_assinatura,
               stripe_customer_id, stripe_subscription_id, 
               assinatura_valor_centavos, criado_em 
        FROM usuarios WHERE id = %s
    """, (user_id,))
    usuario = cursor.fetchone()

    if not usuario:
        cursor.close()
        conn.close()
        abort(404, description="Usuário não encontrado.")

    is_owner = (session['user_id'] == user_id)

    if request.method == 'POST':
        if not is_owner:
            cursor.close()
            conn.close()
            send_audit_log(f'403_editar_perfil_alheio_{user_id}')
            abort(403, description="Você não pode editar o perfil de outro usuário.")

        nome = request.form.get('nome') or usuario['nome']
        bio = request.form.get('bio')
        avatar_url = usuario['avatar_url']

        foto = request.files.get('foto')
        if foto and foto.filename and allowed_image(foto.filename):
            file_data = foto.read()
            if len(file_data) <= 5 * 1024 * 1024:
                filename = secure_filename(foto.filename)
                ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
                object_name = f"avatars/avatar_{user_id}_{uuid.uuid4().hex}.{ext}"
                try:
                    minio_client.put_object(
                        MINIO_BUCKET,
                        object_name,
                        io.BytesIO(file_data),
                        len(file_data),
                        content_type=foto.content_type
                    )
                    avatar_url = url_for('serve_avatar', filename=object_name)
                except Exception as e:
                    print(f"[MinIO] Erro ao salvar avatar: {e}")

        cursor.execute("UPDATE usuarios SET nome = %s, bio = %s, avatar_url = %s WHERE id = %s",
                       (nome, bio, avatar_url, user_id))
        conn.commit()
        session['nome'] = nome
        session['avatar_url'] = avatar_url
        send_audit_log(f'perfil_atualizado_{user_id}', user_id)
        flash('Perfil atualizado com sucesso!')
        cursor.close()
        conn.close()
        return redirect(url_for('perfil', user_id=user_id))

    # Canais criados (se for feeder)
    cursor.execute("SELECT * FROM canais WHERE usuario_id = %s ORDER BY id DESC", (user_id,))
    meus_canais = cursor.fetchall()

    # Canais favoritados
    cursor.execute("""
        SELECT c.*, u.nome as feeder_nome
        FROM canais_favoritos cf
        JOIN canais c ON cf.canal_id = c.id
        JOIN usuarios u ON c.usuario_id = u.id
        WHERE cf.usuario_id = %s
    """, (user_id,))
    canais_favoritados = cursor.fetchall()

    cursor.close()
    conn.close()

    dados_assinatura = obter_dados_assinatura(user_id)

    return render_template(
        'perfil.html',
        usuario=usuario,
        assinatura=dados_assinatura,
        meus_canais=meus_canais,
        canais_favoritados=canais_favoritados,
        is_owner=is_owner
    )

# ==============================================================
# ==============================================================
# STRIPE CHECKOUT, PORTAL E WEBHOOK (ASSINATURAS SAAS RECORRENTES)
# ==============================================================

@app.route('/checkout', methods=['GET', 'POST'])
@app.route('/stripe/checkout', methods=['GET', 'POST'])
def checkout():
    """Inicia sessão de pagamento recorrente mensal no Stripe com escolha de plano."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    plano_desejado = request.args.get('plano') or request.form.get('plano') or 'pro'
    if plano_desejado not in PLANOS_DISPONIVEIS or plano_desejado == 'free':
        plano_desejado = 'pro'

    dados_atuais = obter_dados_assinatura(user_id)
    if dados_atuais['is_premium'] and dados_atuais['plano_efetivo'] == plano_desejado:
        flash(f"⭐ Você já é um assinante ativo do plano {PLANOS_DISPONIVEIS[plano_desejado]['rotulo']}!")
        return redirect(url_for('perfil', user_id=user_id))

    stripe_key = os.getenv('STRIPE_SECRET_KEY')
    if not stripe_key:
        flash("Configuração do Stripe ausente no ambiente (.env).")
        return redirect(url_for('perfil', user_id=user_id))

    stripe.api_key = stripe_key
    app_url = os.getenv('APP_URL') or request.host_url.rstrip('/')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, email, nome, stripe_customer_id FROM usuarios WHERE id = %s", (user_id,))
    usuario = cursor.fetchone()
    cursor.close()
    conn.close()

    info_plano = PLANOS_DISPONIVEIS[plano_desejado]
    price_id = os.getenv(f'STRIPE_PRICE_{plano_desejado.upper()}') or (os.getenv('STRIPE_PRICE_ID') if plano_desejado == 'pro' else None)

    try:
        customer_id = usuario.get('stripe_customer_id') if usuario else None
        if not customer_id and usuario:
            try:
                cust = stripe.Customer.create(
                    email=usuario['email'],
                    name=usuario['nome'],
                    metadata={'user_id': str(user_id)}
                )
                customer_id = cust.id
                c_conn = get_db_connection()
                c_cur = c_conn.cursor()
                c_cur.execute("UPDATE usuarios SET stripe_customer_id = %s WHERE id = %s", (customer_id, user_id))
                c_conn.commit()
                c_cur.close()
                c_conn.close()
            except Exception as ce:
                print(f"[Stripe Customer Create] {ce}")

        line_items = [{
            'price_data': {
                'currency': 'brl',
                'product_data': {
                    'name': f"OpenWave — {info_plano['rotulo']}",
                    'description': info_plano['descricao']
                },
                'unit_amount': info_plano['valor_mensal_centavos'],
                'recurring': {'interval': 'month'}
            },
            'quantity': 1,
        }] if not price_id else [{'price': price_id.strip(), 'quantity': 1}]

        checkout_kwargs = {
            'line_items': line_items,
            'mode': 'subscription',
            'client_reference_id': str(user_id),
            'metadata': {'user_id': str(user_id), 'plano': plano_desejado},
            'subscription_data': {'metadata': {'user_id': str(user_id), 'plano': plano_desejado}},
            'success_url': f"{app_url}/checkout/sucesso?session_id={{CHECKOUT_SESSION_ID}}",
            'cancel_url': f"{app_url}/checkout/cancelado",
        }
        if customer_id:
            checkout_kwargs['customer'] = customer_id
        elif usuario:
            checkout_kwargs['customer_email'] = usuario['email']

        checkout_session = stripe.checkout.Session.create(**checkout_kwargs)
        send_audit_log('iniciar_checkout_sentinela_pro', user_id)
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        print(f"[Stripe Checkout Error] {e}")
        flash(f"Erro ao abrir checkout Stripe: {e}")
        return redirect(url_for('perfil', user_id=user_id))

@app.route('/checkout/sucesso')
def checkout_sucesso():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    send_audit_log('retorno_checkout_sucesso', user_id)
    session['is_premium'] = get_user_premium_status(user_id)
    flash("🎉 Assinatura confirmada com sucesso! Seus benefícios já estão ativos.")
    return redirect(url_for('perfil', user_id=user_id))

@app.route('/checkout/cancelado')
def checkout_cancelado():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    send_audit_log('cancelamento_checkout_stripe', user_id)
    flash("Processo de assinatura cancelado. Nenhuma cobrança foi gerada.")
    return redirect(url_for('perfil', user_id=user_id))

@app.route('/assinatura/portal', methods=['GET', 'POST'])
@app.route('/api/billing/portal', methods=['POST'])
def abrir_portal_cliente():
    """
    Abre o Stripe Customer Portal (Billing Portal) para autoatendimento:
    Permite trocar cartão, alterar planos ou cancelar assinatura por conta própria.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    dados = obter_dados_assinatura(user_id)
    customer_id = dados.get('stripe_customer_id')

    stripe_key = os.getenv('STRIPE_SECRET_KEY')
    if stripe_key:
        stripe.api_key = stripe_key

    app_url = os.getenv('APP_URL') or request.host_url.rstrip('/')
    return_url = f"{app_url}/perfil/{user_id}"

    if not customer_id and stripe_key:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT email, nome FROM usuarios WHERE id = %s", (user_id,))
            u = cursor.fetchone()
            if u:
                novo_cust = stripe.Customer.create(
                    email=u['email'],
                    name=u['nome'],
                    metadata={'user_id': str(user_id)}
                )
                customer_id = novo_cust.id
                cursor.execute("UPDATE usuarios SET stripe_customer_id = %s WHERE id = %s", (customer_id, user_id))
                conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"[Portal Stripe] Erro ao criar customer: {e}")

    if customer_id and stripe_key:
        try:
            portal_session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url
            )
            send_audit_log('portal_faturamento_aberto', user_id)
            if request.is_json:
                return jsonify({'url': portal_session.url}), 200
            return redirect(portal_session.url, code=303)
        except Exception as e:
            print(f"[Portal Stripe Error] {e}")
            flash(f"Portal de autoatendimento Stripe indisponível no momento: {e}. Utilize os controles diretos de troca de plano ou cancelamento.", "warning")
            return redirect(url_for('perfil', user_id=user_id))
    else:
        flash("Configuração do Stripe ausente ou assinatura ainda não iniciada. Utilize a gestão direta de planos.", "info")
        return redirect(url_for('perfil', user_id=user_id))

@app.route('/assinatura/trocar-plano', methods=['POST'])
def trocar_plano():
    """
    Permite ao cliente trocar de plano (upgrade ou downgrade) por conta própria diretamente na aplicação.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    novo_plano = request.form.get('plano') or (request.get_json() or {}).get('plano')

    if novo_plano not in PLANOS_DISPONIVEIS:
        flash("Plano selecionado inválido.", "danger")
        return redirect(url_for('perfil', user_id=user_id))

    if novo_plano == 'free':
        return cancelar_assinatura()

    dados = obter_dados_assinatura(user_id)
    plano_antigo = dados['plano_contratado']

    sub_id = dados.get('stripe_subscription_id')
    stripe_key = os.getenv('STRIPE_SECRET_KEY')
    if stripe_key and sub_id:
        try:
            stripe.api_key = stripe_key
            stripe.Subscription.modify(
                sub_id,
                metadata={'user_id': str(user_id), 'plano': novo_plano}
            )
        except Exception as e:
            print(f"[Troca Plano Stripe] Aviso: {e}")

    novo_valor = PLANOS_DISPONIVEIS[novo_plano]['valor_mensal_centavos']
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE usuarios 
            SET plano = %s, status_assinatura = 'active', is_premium = 1,
                assinatura_valor_centavos = %s
            WHERE id = %s
        """, (novo_plano, novo_valor, user_id))
        conn.commit()
        cursor.close()
        conn.close()

        send_audit_log(f'troca_plano_{plano_antigo}_para_{novo_plano}', user_id)
        flash(f"🎉 Plano alterado com sucesso para {PLANOS_DISPONIVEIS[novo_plano]['rotulo']}! Seus novos limites já estão ativos.", "success")
    except Exception as e:
        print(f"[Troca Plano Erro] {e}")
        flash(f"Erro ao trocar plano: {e}", "danger")

    if request.is_json:
        return jsonify({'success': True, 'plano': novo_plano}), 200
    return redirect(url_for('perfil', user_id=user_id))

@app.route('/assinatura/cancelar', methods=['POST'])
def cancelar_assinatura():
    """
    Permite ao cliente cancelar a assinatura sozinho a qualquer momento.
    Retorna o usuário ao Plano Comunitário Gratuito.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    dados = obter_dados_assinatura(user_id)

    sub_id = dados.get('stripe_subscription_id')
    stripe_key = os.getenv('STRIPE_SECRET_KEY')
    if stripe_key and sub_id:
        try:
            stripe.api_key = stripe_key
            stripe.Subscription.cancel(sub_id)
        except Exception as e:
            print(f"[Cancelamento Stripe] Aviso: {e}")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE usuarios 
            SET plano = 'free', is_premium = 0, status_assinatura = 'canceled',
                assinatura_valor_centavos = 0
            WHERE id = %s
        """, (user_id,))
        conn.commit()
        cursor.close()
        conn.close()

        send_audit_log(f'cancelamento_assinatura_usuario_{user_id}', user_id)
        flash("Sua assinatura foi cancelada com sucesso. Você retornou ao Plano Comunitário Gratuito.", "info")
    except Exception as e:
        print(f"[Cancelamento Erro] {e}")
        flash(f"Erro ao cancelar assinatura: {e}", "danger")

    if request.is_json:
        return jsonify({'success': True, 'plano': 'free'}), 200
    return redirect(url_for('perfil', user_id=user_id))

@app.route('/api/me/plano', methods=['GET'])
def api_me_plano():
    """Retorna os dados do plano e direitos efetivos do usuário autenticado."""
    user_id = session.get('user_id') or request.headers.get('X-User-Id')
    if not user_id:
        return jsonify({'error': 'Não autenticado'}), 401
    dados = obter_dados_assinatura(int(user_id))
    return jsonify({
        'success': True,
        'user_id': int(user_id),
        'plano': dados['plano_efetivo'],
        'plano_contratado': dados['plano_contratado'],
        'status_assinatura': dados['status_assinatura'],
        'is_premium': dados['is_premium'],
        'direitos': dados['direitos']
    }), 200

@app.route('/admin/assinaturas', methods=['GET'])
def admin_assinaturas():
    """Painel Administrativo de Métricas SaaS: MRR, distribuição de planos e auditoria."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_role = get_user_role(session['user_id'])
    if user_role != 'admin':
        send_audit_log('403_acesso_negado_admin_assinaturas', session['user_id'])
        abort(403, description="Acesso exclusivo para administradores do sistema.")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT COALESCE(plano, 'free') as plano, COUNT(*) as total
        FROM usuarios
        GROUP BY plano
    """)
    contagem_planos = {row['plano']: row['total'] for row in cursor.fetchall()}

    cursor.execute("""
        SELECT COUNT(*) as ativas, COALESCE(SUM(assinatura_valor_centavos), 0) as mrr_centavos
        FROM usuarios
        WHERE status_assinatura IN ('active', 'trialing') AND is_premium = 1
    """)
    resumo_mrr = cursor.fetchone() or {'ativas': 0, 'mrr_centavos': 0}
    mrr_reais = (resumo_mrr['mrr_centavos'] or 0) / 100.0

    cursor.execute("""
        SELECT COUNT(*) as total_inadimplentes
        FROM usuarios
        WHERE status_assinatura = 'past_due'
    """)
    inadimplentes_count = (cursor.fetchone() or {}).get('total_inadimplentes', 0)

    try:
        cursor.execute("""
            SELECT event_id, tipo, recebido_em
            FROM stripe_eventos
            ORDER BY recebido_em DESC
            LIMIT 15
        """)
        eventos_stripe = cursor.fetchall()
    except Exception:
        eventos_stripe = []

    cursor.close()
    conn.close()

    return render_template(
        'admin_assinaturas.html',
        contagem_planos=contagem_planos,
        total_ativas=resumo_mrr['ativas'] or 0,
        mrr_reais=mrr_reais,
        inadimplentes_count=inadimplentes_count,
        eventos_stripe=eventos_stripe
    )

@app.route('/webhook/stripe', methods=['POST'])
@app.route('/stripe/webhook', methods=['POST'])
@app.route('/api/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """
    Recebe eventos de webhook assinados do Stripe com HMAC SHA256 e garante idempotência.
    Processa o ciclo de vida completo: criação, alteração, cancelamento e falha de cobrança.
    """
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    stripe_key = os.getenv('STRIPE_SECRET_KEY')
    if stripe_key:
        stripe.api_key = stripe_key

    event = None
    if endpoint_secret:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        except Exception as e:
            print(f"[Webhook Error] Falha de verificação de assinatura: {e}")
            return jsonify({'error': 'Assinatura inválida'}), 400
    else:
        try:
            event = json.loads(payload.decode('utf-8'))
        except Exception as e:
            return jsonify({'error': 'Payload inválido'}), 400

    event_dict = event if isinstance(event, dict) else (event.to_dict() if hasattr(event, 'to_dict') else dict(event))
    event_id = event_dict.get('id')
    event_type = event_dict.get('type')
    data_object = event_dict.get('data', {}).get('object', {})
    print(f"[Stripe Webhook] Evento recebido: {event_type} (id: {event_id})")

    # Idempotência: Se o mesmo evento chegar duplicado, responde 200 sem reprocessar
    if event_id:
        try:
            conn_idemp = get_db_connection()
            cur_idemp = conn_idemp.cursor()
            try:
                cur_idemp.execute("INSERT INTO stripe_eventos (event_id, tipo) VALUES (%s, %s)", (event_id, event_type))
                conn_idemp.commit()
            except Exception as idemp_err:
                cur_idemp.close()
                conn_idemp.close()
                err_str = str(idemp_err).lower()
                if 'integrityerror' in type(idemp_err).__name__.lower() or 'duplicate' in err_str or '1062' in err_str:
                    print(f"[Stripe Webhook] Evento duplicado ignorado: {event_id}")
                    return jsonify({'received': True, 'duplicate': True}), 200
            else:
                cur_idemp.close()
                conn_idemp.close()
        except Exception:
            pass

    if event_type == 'checkout.session.completed':
        user_id = data_object.get('client_reference_id') or (data_object.get('metadata') or {}).get('user_id')
        customer_id = data_object.get('customer')
        subscription_id = data_object.get('subscription')
        metadata = data_object.get('metadata') or {}
        plano_meta = metadata.get('plano', 'sentinela_pro')
        plano_nome = 'central' if 'central' in plano_meta else 'pro'
        valor_mensal = data_object.get('amount_total') or PLANOS_DISPONIVEIS[plano_nome]['valor_mensal_centavos']

        if user_id:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE usuarios
                    SET is_premium = 1, plano = %s, status_assinatura = 'active',
                        stripe_customer_id = %s, stripe_subscription_id = %s,
                        assinatura_valor_centavos = %s
                    WHERE id = %s
                """, (plano_nome, customer_id, subscription_id, valor_mensal, user_id))
                conn.commit()
                cursor.close()
                conn.close()
                print(f"[Stripe Webhook] Usuário {user_id} promovido a SENTINELA PRO!")
                send_audit_log(f'upgrade_sentinela_pro_confirmado_{user_id}', user_id)
            except Exception as e:
                print(f"[Stripe Webhook] Erro ao persistir no banco: {e}")
                return jsonify({'error': 'Erro ao atualizar banco'}), 500

    elif event_type == 'customer.subscription.updated':
        subscription_id = data_object.get('id')
        customer_id = data_object.get('customer')
        status = (data_object.get('status') or 'active').lower()
        metadata = data_object.get('metadata') or {}
        plano_meta = metadata.get('plano', 'pro')

        items = data_object.get('items', {}).get('data', [])
        valor = None
        if items:
            p = items[0].get('price', {})
            valor = p.get('unit_amount')

        plano_final = 'central' if (plano_meta == 'central' or (valor and valor >= 2000)) else 'pro'
        valor_final = valor or PLANOS_DISPONIVEIS[plano_final]['valor_mensal_centavos']

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if status in STATUS_ATIVOS:
                cursor.execute("""
                    UPDATE usuarios 
                    SET plano = %s, status_assinatura = %s, is_premium = 1,
                        assinatura_valor_centavos = %s
                    WHERE stripe_subscription_id = %s OR stripe_customer_id = %s
                """, (plano_final, status, valor_final, subscription_id, customer_id))
            elif status == 'past_due':
                cursor.execute("""
                    UPDATE usuarios 
                    SET status_assinatura = 'past_due', is_premium = 0
                    WHERE stripe_subscription_id = %s OR stripe_customer_id = %s
                """, (subscription_id, customer_id))
            else:
                cursor.execute("""
                    UPDATE usuarios 
                    SET plano = 'free', status_assinatura = %s, is_premium = 0,
                        assinatura_valor_centavos = 0
                    WHERE stripe_subscription_id = %s OR stripe_customer_id = %s
                """, (status, subscription_id, customer_id))
            conn.commit()
            cursor.close()
            conn.close()
            send_audit_log(f'assinatura_atualizada_{status}', customer_id or subscription_id)
        except Exception as e:
            print(f"[Stripe Webhook] Erro ao processar customer.subscription.updated: {e}")
            return jsonify({'error': 'Erro ao atualizar banco'}), 500

    elif event_type in ['customer.subscription.deleted', 'customer.subscription.paused']:
        subscription_id = data_object.get('id')
        customer_id = data_object.get('customer')
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE usuarios 
                SET plano = 'free', status_assinatura = 'canceled', is_premium = 0,
                    assinatura_valor_centavos = 0
                WHERE stripe_subscription_id = %s OR stripe_customer_id = %s
            """, (subscription_id, customer_id))
            conn.commit()
            cursor.close()
            conn.close()
            send_audit_log(f'cancelamento_sentinela_pro_{subscription_id}')
        except Exception as e:
            print(f"[Stripe Webhook] Erro ao desativar assinatura: {e}")
            return jsonify({'error': 'Erro ao atualizar banco'}), 500

    elif event_type == 'invoice.payment_failed':
        customer_id = data_object.get('customer')
        subscription_id = data_object.get('subscription')
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE usuarios 
                SET status_assinatura = 'past_due', is_premium = 0
                WHERE stripe_customer_id = %s OR stripe_subscription_id = %s
            """, (customer_id, subscription_id))
            conn.commit()
            cursor.close()
            conn.close()
            send_audit_log(f'pagamento_falhou_{customer_id}', customer_id)
        except Exception as e:
            print(f"[Stripe Webhook] Erro ao registrar falha de pagamento: {e}")
            return jsonify({'error': 'Erro ao atualizar banco'}), 500

    return jsonify({'received': True}), 200

# ==============================================================
# AUDITORIA E LOGS (EXCLUSIVO ADMIN)
# ==============================================================

@app.route('/admin/logs')
def admin_logs():
    """
    Painel de Auditoria de Eventos de Negócio.
    Acesso restrito a 'admin'. Responde 403 Forbidden para outros papéis.
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    current_role = get_user_role(user_id)

    if current_role != 'admin':
        send_audit_log('403_acessar_logs', user_id)
        abort(403, description="403 Forbidden - Apenas administradores têm permissão para acessar os logs de auditoria do sistema.")

    logs = []
    try:
        res = requests.get(f"{LOG_SERVICE_URL}/api/logs?limit=50", timeout=5)
        if res.status_code == 200:
            logs = res.json().get('logs', [])
    except Exception as e:
        flash(f"Erro ao consultar serviço de logs: {e}")

    return render_template('logs.html', logs=logs)

# ==============================================================
# API REST DO RECURSO PRINCIPAL (COM SWAGGER)
# ==============================================================

@app.route('/api/canais', methods=['GET'])
def api_listar_canais():
    """
    Lista todos os canais de rádio disponíveis no OpenWave
    ---
    tags:
      - Canais de Rádio
    parameters:
      - name: categoria
        in: query
        type: string
        description: Filtro opcional por categoria
      - name: status
        in: query
        type: string
        description: Filtro opcional por status (online/offline)
    responses:
      200:
        description: Lista de canais retornada com sucesso
    """
    categoria = request.args.get('categoria')
    status = request.args.get('status')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    query = "SELECT c.*, u.nome as feeder_nome FROM canais c JOIN usuarios u ON c.usuario_id = u.id WHERE 1=1"
    params = []
    if categoria:
        query += " AND c.categoria = %s"
        params.append(categoria)
    if status:
        query += " AND c.status = %s"
        params.append(status)
    query += " ORDER BY c.id DESC"

    cursor.execute(query, tuple(params))
    canais = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'canais': canais}), 200

@app.route('/api/canais', methods=['POST'])
def api_criar_canal():
    """
    Cadastra um novo canal de rádio na plataforma
    ---
    tags:
      - Canais de Rádio
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            nome:
              type: string
            frequencia:
              type: string
            categoria:
              type: string
            cidade:
              type: string
            estado:
              type: string
            descricao:
              type: string
            stream_url:
              type: string
    responses:
      201:
        description: Canal criado com sucesso
      401:
        description: Autenticação requerida
      403:
        description: Apenas feeders e administradores podem cadastrar canais
      400:
        description: Dados insuficientes
    """
    user_id = session.get('user_id') or request.headers.get('X-User-Id')
    if not user_id:
        return jsonify({'error': 'Não autenticado'}), 401

    user_role = get_user_role(int(user_id))
    if user_role not in ['feeder', 'admin']:
        send_audit_log('403_api_criar_canal', user_id)
        return jsonify({'error': '403 Forbidden - Papel não autorizado a cadastrar canais'}), 403

    data = request.get_json() or {}
    nome = data.get('nome')
    frequencia = data.get('frequencia')
    categoria = data.get('categoria', 'Resgate & Bombeiros')
    cidade = data.get('cidade')
    estado = (data.get('estado') or 'SP').upper()
    descricao = data.get('descricao', '')
    stream_url = data.get('stream_url', '')

    if not nome or not frequencia or not cidade:
        return jsonify({'error': 'Campos nome, frequencia e cidade são obrigatórios'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO canais (usuario_id, nome, frequencia, categoria, cidade, estado, descricao, stream_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, nome, frequencia, categoria, cidade, estado, descricao, stream_url))
        conn.commit()
        canal_id = cursor.lastrowid
        send_audit_log(f'canal_criado_{canal_id}', user_id)
        return jsonify({'success': True, 'canal_id': canal_id, 'message': 'Canal criado com sucesso'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/canais/<int:canal_id>', methods=['GET'])
def api_detalhar_canal(canal_id):
    """
    Retorna os dados detalhados de um canal de rádio e suas ocorrências gravadas
    ---
    tags:
      - Canais de Rádio
    parameters:
      - name: canal_id
        in: path
        type: integer
        required: true
    responses:
      200:
        description: Detalhes do canal
      404:
        description: Canal não encontrado
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.*, u.nome as feeder_nome
        FROM canais c JOIN usuarios u ON c.usuario_id = u.id
        WHERE c.id = %s
    """, (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Canal não encontrado'}), 404

    cursor.execute("SELECT * FROM gravacoes_ocorrencias WHERE canal_id = %s ORDER BY data_hora_ocorrencia DESC", (canal_id,))
    gravacoes = cursor.fetchall()
    cursor.close()
    conn.close()

    canal['gravacoes'] = gravacoes
    return jsonify({'success': True, 'canal': canal}), 200

@app.route('/api/canais/<int:canal_id>', methods=['PUT'])
def api_editar_canal(canal_id):
    """
    Atualiza os dados de um canal de rádio
    ---
    tags:
      - Canais de Rádio
    parameters:
      - name: canal_id
        in: path
        type: integer
        required: true
      - in: body
        name: body
        schema:
          type: object
    responses:
      200:
        description: Canal atualizado
      403:
        description: Apenas o dono ou administrador podem editar este canal
      404:
        description: Canal não encontrado
    """
    user_id = session.get('user_id') or request.headers.get('X-User-Id')
    if not user_id:
        return jsonify({'error': 'Não autenticado'}), 401

    user_role = get_user_role(int(user_id))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM canais WHERE id = %s", (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Canal não encontrado'}), 404

    # Requisito 6: Isolamento por usuário (multi-tenancy)
    if canal['usuario_id'] != int(user_id) and user_role != 'admin':
        cursor.close()
        conn.close()
        send_audit_log(f'403_api_editar_canal_alheio_{canal_id}', user_id)
        return jsonify({'error': '403 Forbidden - Você não tem permissão para alterar canais de outro operador'}), 403

    data = request.get_json() or {}
    nome = data.get('nome', canal['nome'])
    frequencia = data.get('frequencia', canal['frequencia'])
    categoria = data.get('categoria', canal['categoria'])
    cidade = data.get('cidade', canal['cidade'])
    estado = (data.get('estado') or canal['estado']).upper()
    descricao = data.get('descricao', canal['descricao'])
    status = data.get('status', canal['status'])

    try:
        cursor.execute("""
            UPDATE canais SET nome = %s, frequencia = %s, categoria = %s, cidade = %s, estado = %s, descricao = %s, status = %s
            WHERE id = %s
        """, (nome, frequencia, categoria, cidade, estado, descricao, status, canal_id))
        conn.commit()
        send_audit_log(f'canal_atualizado_{canal_id}', user_id)
        return jsonify({'success': True, 'message': 'Canal atualizado com sucesso'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/canais/<int:canal_id>', methods=['DELETE'])
def api_remover_canal(canal_id):
    """
    Remove um canal de rádio
    ---
    tags:
      - Canais de Rádio
    parameters:
      - name: canal_id
        in: path
        type: integer
        required: true
    responses:
      200:
        description: Canal removido com sucesso
      403:
        description: Apenas o dono ou administrador podem remover este canal
      404:
        description: Canal não encontrado
    """
    user_id = session.get('user_id') or request.headers.get('X-User-Id')
    if not user_id:
        return jsonify({'error': 'Não autenticado'}), 401

    user_role = get_user_role(int(user_id))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM canais WHERE id = %s", (canal_id,))
    canal = cursor.fetchone()

    if not canal:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Canal não encontrado'}), 404

    # Requisito 6: Isolamento por usuário
    if canal['usuario_id'] != int(user_id) and user_role != 'admin':
        cursor.close()
        conn.close()
        send_audit_log(f'403_api_remover_canal_alheio_{canal_id}', user_id)
        return jsonify({'error': '403 Forbidden - Você não tem permissão para remover canais de outro operador'}), 403

    try:
        cursor.execute("DELETE FROM canais WHERE id = %s", (canal_id,))
        conn.commit()
        send_audit_log(f'canal_removido_{canal_id}', user_id)
        return jsonify({'success': True, 'message': 'Canal removido com sucesso'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
