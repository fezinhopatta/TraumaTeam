-- =====================================================================
-- Script de Migração: Modelo SaaS de Assinaturas Recorrentes
-- Disciplina: ISW055 - Introdução à Computação em Nuvem
-- Professor: @siriani (https://github.com/siriani)
-- Projeto: OpenWave - Rede Colaborativa de Escuta de Rádio de Emergência
-- =====================================================================

SET NAMES utf8mb4;

-- 1. Expansão da tabela de usuários para modelo de planos e ciclo de vida SaaS
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS plano VARCHAR(20) NOT NULL DEFAULT 'free';
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS status_assinatura VARCHAR(30) NULL DEFAULT 'active';
ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS assinatura_valor_centavos INT NULL DEFAULT 0;

-- Sincroniza usuários legados que possuíam o Plano Premium antigo (09/10)
-- Quem já estava como premium (is_premium = 1) passa a constar como Sentinela Pro ativo
UPDATE usuarios 
SET plano = 'pro', 
    status_assinatura = 'active', 
    assinatura_valor_centavos = 990 
WHERE is_premium = 1 AND (plano IS NULL OR plano = 'free');

-- Quem é plano free garante is_premium = 0 e assinatura zerada
UPDATE usuarios 
SET plano = 'free', 
    status_assinatura = 'active', 
    assinatura_valor_centavos = 0 
WHERE is_premium = 0 AND (plano IS NULL OR plano = '');

-- 2. Tabela de Idempotência para Eventos do Stripe
-- Garante que o mesmo webhook nunca seja processado mais de uma vez
CREATE TABLE IF NOT EXISTS stripe_eventos (
    event_id    VARCHAR(100) NOT NULL PRIMARY KEY,
    tipo        VARCHAR(80)  NOT NULL,
    recebido_em TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
