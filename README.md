# 📡 OpenWave — Rede Colaborativa de Escuta de Rádio Regional

> A plataforma colaborativa de streaming de áudio de rádio (VHF/UHF/P25/DMR) que transmite despachos e ocorrências locais em tempo real para cidadãos acompanharem a segurança do seu bairro sem intermediários.

---

## 🌐 URL do Sistema no Ar

* **Endereço da Aplicação (Stack Portainer):** `http://localhost:5000` *(ou subdomínio de produção configurado na VM do aluno: `${APP_URL}`)*
* **Documentação Swagger (Auth Service):** `http://localhost:5001/apidocs/`
* **Documentação Swagger (OpenWave Core):** `http://localhost:5000/apidocs/`
* **Painel MinIO Console:** `http://localhost:9011` (User: `minioadmin` | Pass: `minioadmin123`)

* * **[Entrega p2](./docs/P2_ISW055_Victor_Siveri.pdf)**

---

## 1. Visão Geral e Proposta de Valor

O **OpenWave** substitui o ruído de fake news e a demora dos portais de notícias pelo acesso direto e cru às transmissões operacionais de emergência e segurança pública:
* **Bombeiros & SAMU (193/192):** Despachos de acidentes, resgates viários e combate a incêndios.
* **Polícia Rodoviária & Concessionárias:** Alertas de bloqueios de pistas, perseguições e condições de tráfego.
* **Defesa Civil:** Avisos antecipados de inundações, quedas de árvores e eventos meteorológicos extremos.
* **Patrulhamento Comunitário:** Ronda de viaturas em perímetros rurais e comerciais.

---

## 2. Diagrama de Arquitetura do Sistema

```mermaid
flowchart TD
    subgraph Clientes["Navegadores & Dispositivos"]
        Visitante["Visitante Anônimo"] -->|Acessa / (Público)| Landing["Landing Page Pública"]
        Ouvinte["Cidadão Ouvinte (listener)"] -->|Ouve canais / Favorita / Assina Pro| AppWeb["Dashboard OpenWave"]
        Operador["Estação Feeder (feeder)"] -->|Gerencia canais & Envia Áudio| AppWeb
        Admin["Administrador (admin)"] -->|Auditoria de Eventos & Gestão| AppWeb
    end

    subgraph CoreStack["Stack Docker no Portainer (Mesma Infraestrutura)"]
        AppWeb -->|Porta 5000| CatalogService["OpenWave Core (Flask)"]
        CatalogService -->|Porta 5001 - REST| AuthService["Auth Service (Flask + JWT/Sessão)"]
        CatalogService -->|Porta 5002 - REST| LogService["Log Service (Flask)"]
        CatalogService -->|Porta 9010 - S3 API| MinIOService["MinIO Object Storage"]
        CatalogService -->|Porta 3306 - SQL| MariaDBService[("MariaDB (Tabelas do Domínio)")]
        CatalogService -->|Webhooks & Checkout| StripeService["Stripe API (Sentinela Pro)"]

        AuthService -->|SQL| MariaDBService
        AuthService -->|SMTP| Mailtrap["Mailtrap / Brevo (E-mails Transacionais)"]
        AuthService -->|Auditoria| LogService

        LogService -->|Redis Streams| RedisService[("Redis Service")]
    end
```

---

## 3. Modelo de Dados no MariaDB (Script de Migração & Seed)

O banco de dados foi modelado para o domínio do OpenWave com 3 tabelas relacionais interligadas por chaves estrangeiras (`ON DELETE CASCADE`), criadas a partir do script versionado:

📁 **Script:** [`db/001_dominio.sql`](file:///c:/Users/User/Documents/TraumaTeam/db/001_dominio.sql)

```mermaid
erDiagram
    USUARIOS ||--o{ CANAIS : "opera (feeder)"
    USUARIOS ||--o{ GRAVACOES_OCORRENCIAS : "publica"
    USUARIOS ||--o{ CANAIS_FAVORITOS : "salva (listener)"
    USUARIOS ||--o{ RESET_TOKENS : "solicita"
    CANAIS ||--o{ GRAVACOES_OCORRENCIAS : "possui"
    CANAIS ||--o{ CANAIS_FAVORITOS : "é favoritado"

    USUARIOS {
        int id PK
        string nome
        string email UK
        string senha_hash
        string role "admin | feeder | listener"
        string avatar_url
        boolean is_premium "Sentinela Pro"
    }

    CANAIS {
        int id PK
        int usuario_id FK "Dono (Feeder)"
        string nome
        string frequencia "Ex: 193.000 MHz"
        string categoria "Resgate | Polícia | Rodovias | Defesa Civil"
        string cidade
        string estado
        string status "online | offline | manutencao"
        string stream_url
        string icone_url
        int delay_segundos
    }

    GRAVACOES_OCORRENCIAS {
        int id PK
        int canal_id FK
        int usuario_id FK
        string titulo
        string audio_url "Storage no MinIO (.mp3/.ogg)"
        int duracao_segundos
        datetime data_hora_ocorrencia
    }

    CANAIS_FAVORITOS {
        int id PK
        int usuario_id FK
        int canal_id FK
    }
```

---

## 4. Papéis do Negócio e Controle de Acesso (RBAC)

O sistema conta com 3 papéis distintos que determinam permissões estritas tanto na interface quanto diretamente na API:

| Papel | Descrição | Permissões |
|---|---|---|
| `admin` | Administrador Geral do OpenWave | Acesso aos logs de auditoria (`/admin/logs`), gestão global de todos os canais, gravações e usuários. |
| `feeder` | Estação Transmissora / Operador | Cadastra e gerencia seus próprios canais (`/canais/novo`), envia arquivos de áudio gravados de despachos para o MinIO. |
| `listener` | Cidadão Ouvinte | Escuta transmissões abertas, salva canais favoritos, realiza upgrade para o plano Sentinela Pro via Stripe. **Tentativa de criar ou editar canal responde HTTP 403 Forbidden.** |

---

## 5. Isolamento por Usuário (Multi-Tenancy)

Cada usuário do tipo `feeder` só enxerga e gerencia seus próprios registros de canais e gravações:
* O Feeder B tentando editar (`/canais/<id>/editar` ou `PUT /api/canais/<id>`) um canal do Feeder A recebe **403 Forbidden**.
* O Feeder B tentando excluir (`POST /canais/<id>/remover` ou `DELETE /api/canais/<id>`) um canal alheio recebe **403 Forbidden**.
* Toda ação proibida despacha um evento de segurança estruturado (`403_acesso_negado_canal_alheio`) para o serviço de auditoria.

---

## 6. Upload de Mídia no MinIO (Object Storage S3)

O MinIO armazena dois tipos de arquivos do domínio:
1. **Gravações de Despachos de Emergência (`.mp3`, `.ogg`, `.wav`):** Upload feito pelo operador do canal ou admin via `POST /canais/<id>/gravacoes`, persistido no bucket `openwave-midias` e servido via streaming seguro em `/audio/<filename>`.
2. **Emblemas e Logotipos de Estações (`.png`, `.jpg`, `.webp`):** Identificação visual dos canais sintonizados, servidos em `/icone/<filename>`.

---

## 7. Modelo SaaS de Assinaturas Recorrentes (Stripe Lifecycle)

Diferente do modelo legado (de 09/10) que realizava cobrança única e marcava apenas uma flag booleana (`is_premium = 1`), o **OpenWave SaaS** opera sob um motor completo de assinaturas recorrentes mensais, controle estrito de cotas por requisição e autonomia para o próprio cliente:

### 7.1 Grade de Planos SaaS
* **Plano Comunitário (Gratuito - R$ 0,00/mês):** Acesso a frequências públicas, delay proposital de 10 minutos (600s), limite de até 3 canais favoritos salvos e sem acervo histórico.
* **Plano Sentinela Pro (R$ 9,90/mês):** Streaming de baixa latência em **tempo real (zero delay)**, **favoritos ilimitados**, acervo de despachos das últimas 48h e selo de assinante.
* **Plano Central Tática VIP (R$ 29,90/mês):** Prioridade máxima de largura de banda, **download direto de arquivos de ocorrência (.mp3)**, frequências táticas exclusivas e monitoramento institucional.

### 7.2 O que o Plano Permite a Cada Requisição (Entitlement Policy)
* O sistema não confia em dados estáticos na sessão: **a cada requisição** de favoritar, ouvir stream ou acessar gravações, a função `obter_dados_assinatura(user_id)` valida o status relacional no MariaDB (`plano` e `status_assinatura`).
* **Trava de Inadimplência:** Se o webhook do Stripe reportar status `past_due`, `canceled` ou `unpaid`, os direitos do usuário retornam instantaneamente para os limites do plano Free, impedindo o usufruto de recursos Pro sem pagamento ativo.
* **Bloqueio Educativo com HTTP 403:** Tentativas de favoritar mais de 3 canais no plano Free são barradas com **HTTP 403 Forbidden**, apresentando a tela [`templates/limite.html`](templates/limite.html) com detalhamento da cota e opções de upgrade.

### 7.3 Autoatendimento do Cliente (Trocar de Plano e Cancelar Sozinho)
* **Portal do Cliente Stripe (`POST /assinatura/portal` ou `/api/billing/portal`):** Redireciona o cliente para o Billing Portal oficial do Stripe, permitindo atualizar cartões de crédito, baixar recibos e faturas fiscais.
* **Troca de Plano Direta (`POST /assinatura/trocar-plano`):** Permite ao assinante alternar livremente (upgrade/downgrade) entre Sentinela Pro e Central Tática VIP, sincronizando os metadados no Stripe e ajustando direitos imediatamente.
* **Cancelamento Autônomo (`POST /assinatura/cancelar`):** O usuário pode cancelar a assinatura a qualquer instante sem intermédio de suporte, revertendo sua conta para o Plano Comunitário Free.

### 7.4 Ciclo de Vida do Webhook e Idempotência
O endpoint `/webhook/stripe` valida assinaturas criptográficas HMAC SHA-256 e persiste eventos na tabela `stripe_eventos`, garantindo que mensagens duplicadas sejam descartadas com status 200:
1. `checkout.session.completed`: Ativa a assinatura do usuário, registra o `stripe_customer_id`, `stripe_subscription_id` e o plano escolhido.
2. `customer.subscription.updated`: Sincroniza mudanças de plano ou alterações de status (`active` vs `past_due`).
3. `customer.subscription.deleted`: Desativa os direitos Pro e reverte o usuário para o plano Free.
4. `invoice.payment_failed`: Marca o usuário como `past_due` e suspende temporariamente os benefícios.

### 7.5 Painel Administrativo SaaS (`/admin/assinaturas`)
Exclusivo para administradores (`admin`), apresentando em tempo real:
* **MRR (Monthly Recurring Revenue):** Receita recorrente mensal calculada dinamicamente em R$.
* **Assinantes Ativos:** Total de usuários nos planos Pro e Central.
* **Inadimplência:** Monitoramento de assinaturas em atraso (`past_due`).
* **Auditoria de Eventos:** Tabela com os últimos webhooks recebidos do Stripe com timestamp.

---

## 8. Roteiro de Testes e Validação (Cenário do Professor)

### 1. Acesso Público / Landing Page
* Abra a URL raiz `/` em aba anônima (sem login).
* **Resultado:** A Landing Page pública do OpenWave é exibida com proposta de valor, tabela comparativa dos planos Free e Pro e botões para login/cadastro.

### 2. Criação de Contas com Papéis Distintos
* Cadastre uma conta como **Ouvinte** (`listener`), por exemplo `ouvinte.teste@openwave.local`.
* Cadastre outra conta como **Estação Transmissora** (`feeder`), por exemplo `feeder.teste@openwave.local`.

### 3. Gestão de Canais e Upload (Feeder)
* Entre com a conta `feeder`.
* Clique em **"Novo Canal"** e cadastre uma nova frequência (ex: *193.000 MHz Bombeiros*).
* Na página do canal, faça o upload de um arquivo de áudio de ocorrência (.mp3).
* **Resultado:** O canal e a gravação são criados com sucesso e salvos no MariaDB e no MinIO.

### 4. Bloqueio de Permissão (RBAC - 403)
* Entre com a conta `listener`.
* Tente acessar diretamente a rota de criação: `GET /canais/novo` ou enviar `POST /api/canais`.
* **Resultado:** Resposta imediata com **403 Forbidden**.

### 5. Isolamento por Usuário (Multi-Tenancy - 403)
* Com a conta `listener` (ou outro feeder), tente editar o canal criado pelo primeiro feeder: `GET /canais/<id>/editar` ou `PUT /api/canais/<id>`.
* **Resultado:** Resposta com **403 Forbidden**.

### 6. Auditoria de Eventos no Log (Admin)
* Faça login com a conta de administrador (`admin@openwave.local` / senha: `senha123`).
* Acesse o menu **Logs** (`/admin/logs`).
* **Resultado:** Lista dos eventos registrados em tempo real no Redis Streams (`canal_criado`, `gravacao_audio_enviada`, `403_permissao_negada`, `login`).

### 7. Execução dos Testes Automatizados
```bash
# Executa a suíte completa de testes de integração do OpenWave
.\.venv\Scripts\python.exe -m unittest test_openwave_integration.py
```
**Resultado:** 12 testes executados e aprovados com 100% de sucesso.

---

## 9. Como Subir o Ambiente Localmente

```bash
# 1. Copiar as variáveis de ambiente
cp .env.example .env

# 2. Subir todos os microsserviços via Docker Compose
docker compose up -d --build

# 3. Aplicar o seed de migração (se necessário)
docker compose exec -T mariadb mysql -u usuario_disciplina -p'senha_disciplina' banco_disciplina < db/001_dominio.sql
```

---

Projeto acadêmico orientado pelo Prof. [@siriani](https://github.com/siriani) — Disciplina ISW055.
