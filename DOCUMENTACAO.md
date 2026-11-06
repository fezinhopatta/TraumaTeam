# 📚 Documentação Técnica do Sistema — OpenWave

> **Projeto:** OpenWave — Rede Colaborativa de Escuta de Rádio e Emergências  
> **Disciplina:** ISW055 — Laboratório de Desenvolvimento Web / Engenharia de Software  
> **Professor Responsável:** [@siriani](https://github.com/siriani)  
> **Documento:** Especificação Técnica, Arquitetura de Microsserviços, Regras de Negócio, Modelo de Dados e Guia de Operação

---

## 🧭 Sumário

1. [Visão Geral e Contexto](#1-visão-geral-e-contexto)
2. [Especificação Funcional (Visão do Usuário)](#2-especificação-funcional)
   - [2.1 Landing Page Pública](#21-landing-page-pública)
   - [2.2 Cadastro, Autenticação e Papéis do Negócio (RBAC)](#22-cadastro-autenticação-e-papéis-do-negócio-rbac)
   - [2.3 Catálogo de Canais de Rádio e Player Web](#23-catálogo-de-canais-de-rádio-e-player-web)
   - [2.4 Despachos e Gravações de Ocorrências (Object Storage MinIO)](#24-despachos-e-gravações-de-ocorrências-object-storage-minio)
   - [2.5 Sistema de Favoritos e Cotas](#25-sistema-de-favoritos-e-cotas)
   - [2.6 Assinatura Sentinela Pro e Checkout Recorrente (Stripe)](#26-assinatura-sentinela-pro-e-checkout-recorrente-stripe)
   - [2.7 Painel de Auditoria e Logs (Exclusivo Admin)](#27-painel-de-auditoria-e-logs-exclusivo-admin)
   - [2.8 Documentação Interativa de APIs (Swagger/OpenAPI)](#28-documentação-interativa-de-apis-swaggeropenapi)
3. [Arquitetura de Software e Infraestrutura](#3-arquitetura-de-software-e-infraestrutura)
   - [3.1 Topologia de Rede e Portas](#31-topologia-de-rede-e-portas)
   - [3.2 Diagrama Arquitetural Completo](#32-diagrama-arquitetural-completo)
4. [Regras de Negócio e Isolamento Multi-Tenancy](#4-regras-de-negócio-e-isolamento-multi-tenancy)
5. [Modelo de Dados (MariaDB, Redis, MinIO)](#5-modelo-de-dados)
6. [Guia de Operação e Execução de Testes](#6-guia-de-operação-e-execução-de-testes)

---

## 1. Visão Geral e Contexto

O **OpenWave** é uma plataforma SaaS distribuída para escuta colaborativa de despachos e frequências de rádio de segurança pública e emergência (Bombeiros, SAMU, Defesa Civil, Polícia Rodoviária e Patrulhamento Comunitário).

O sistema aproveita integralmente a topologia de microsserviços estabelecida, operando sobre a mesma infraestrutura de containers gerenciada via Portainer.

---

## 2. Especificação Funcional

### 2.1 Landing Page Pública
- Visitantes anônimos acessam `/` e visualizam a proposta de valor, tabela comparativa dos planos (Comunitário Gratuito vs Sentinela Pro) e botões de chamada para ação sem necessidade de login prévio.

### 2.2 Cadastro, Autenticação e Papéis do Negócio (RBAC)
- O cadastro permite escolher o papel:
  - `listener`: Cidadão ouvinte que acompanha frequências públicas.
  - `feeder`: Operador voluntário de estação transmissora com scanner ou RTL-SDR.
  - `admin`: Operador central do sistema (configurado via seed no banco).
- Acesso à criação e edição de canais é estritamente restrito a `feeder` e `admin`. Tentativas de acesso indevido por um `listener` resultam em código **HTTP 403 Forbidden**.

### 2.3 Catálogo de Canais de Rádio e Player Web
- Apresenta a grade de frequências monitoradas com filtros por cidade e categoria (Resgate & Bombeiros, Polícia / Patrulha, Rodovias, Defesa Civil).
- Player de áudio web HTML5 integrado para escuta de transmissões.

### 2.4 Despachos e Gravações de Ocorrências (Object Storage MinIO)
- Feeder proprietário do canal pode fazer upload de gravações de áudio de despachos (`.mp3`, `.ogg`, `.wav`), que são armazenadas no MinIO e ligadas ao respectivo canal.
- Streaming seguro servido pelo endpoint `/audio/<filename>`.

### 2.5 Sistema de Favoritos e Cotas
- Ouvintes gratuitos podem favoritar até 3 frequências simultâneas.
- Ao atingir a cota, o sistema emite alerta flash com convite de upgrade para o Plano Sentinela Pro.

### 2.6 Assinatura Sentinela Pro e Checkout Recorrente (Stripe)
- Por **R$ 9,90/mês**, ouvintes desbloqueiam streaming em tempo real (baixa latência sem delay de 10 minutos), favoritos ilimitados e acervo de gravações das últimas 48h.
- Integração via Stripe Checkout Session com confirmação por webhook assinado criptograficamente com HMAC SHA-256 (`/webhook/stripe`).

### 2.7 Painel de Auditoria e Logs (Exclusivo Admin)
- Rota `/admin/logs` exibe em tempo real os eventos operacionais e de segurança persistidos no **Redis Streams** pelo `log-service`.
- Usuários não-administradores recebem resposta **HTTP 403 Forbidden**.

---

## 3. Arquitetura de Software e Infraestrutura

### 3.1 Topologia de Rede e Portas

| Serviço | Container Docker | Porta Interna | Porta Mapeada | Função |
|---|---|---|---|---|
| **OpenWave Core** | `catalog-service` | 5000 | 5000 (ou `${PORTA_ALUNO}`) | Web App, SSR, CRUD de Canais, Stripe Webhooks |
| **Auth Service** | `auth-service` | 5000 | 5001 | Autenticação, RBAC, Tokens e E-mail SMTP |
| **Log Service** | `log-service` | 5000 | 5002 | Auditoria e persistência de eventos no Redis |
| **Redis** | `redis-service` | 6379 | - | Redis Streams de auditoria de eventos |
| **MinIO API** | `minio` | 9000 | 9010 | S3 Object Storage para áudios e imagens |
| **MinIO Console** | `minio` | 9001 | 9011 | Interface administrativa do MinIO |
| **MariaDB** | Externo / VM | 3306 | - | Banco relacional para tabelas do domínio |

---

## 4. Regras de Negócio e Isolamento Multi-Tenancy

- **RN-01 (RBAC de Criação):** Se um usuário com papel `listener` tentar criar canal via tela ou `POST /api/canais`, o sistema bloqueia com **403 Forbidden** e emite log `403_permissao_negada_criar_canal`.
- **RN-02 (Multi-Tenancy de Edição):** Se o Feeder A tentar editar ou excluir um canal de propriedade do Feeder B, o sistema bloqueia a requisição com **403 Forbidden** e emite log de segurança `403_acesso_negado_canal_alheio`.
- **RN-03 (Isolamento de Gravações MinIO):** Apenas o operador responsável pelo canal ou administradores podem anexar gravações de áudio. Terceiros recebem **403 Forbidden**.
- **RN-04 (Cota de Favoritos):** Ouvintes do plano comunitário gratuito estão limitados a 3 canais favoritos. Assinantes Sentinela Pro têm favoritos ilimitados.
- **RN-05 (Webhook Seguro Stripe):** Notificações do Stripe têm assinatura verificada via `STRIPE_WEBHOOK_SECRET` com rejeição 400 em caso de payload adulterado.

---

## 5. Modelo de Dados

### Tabelas Principais (MariaDB)

1. `usuarios`: Cadastro com perfis `admin`, `feeder`, `listener` e campos Stripe.
2. `canais`: Recurso principal contendo frequência de rádio, categoria, cidade/estado, status e vínculo ao feeder.
3. `gravacoes_ocorrencias`: Gravações de áudio no MinIO ligadas a um canal e operador.
4. `canais_favoritos`: Relação many-to-many entre ouvintes e canais monitorados.
5. `reset_tokens`: Tokens temporários de redefinição de senha com validade de 30 minutos.

---

## 6. Guia de Operação e Execução de Testes

```bash
# Execução da suíte completa de testes automatizados do OpenWave
.\.venv\Scripts\python.exe -m unittest test_openwave_integration.py

# Execução dos testes de integração do Stripe
.\.venv\Scripts\python.exe -m unittest test_stripe_integration.py
```

---

Projeto acadêmico orientado pelo Prof. [@siriani](https://github.com/siriani) — Disciplina ISW055.
