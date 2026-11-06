-- =====================================================================
-- Script de Migração e Seed do Domínio: OpenWave
-- Disciplina: ISW055 - Laboratório de Desenvolvimento Web / Eng. Software
-- Professor: @siriani (https://github.com/siriani)
-- Projeto: OpenWave - Rede Colaborativa de Escuta de Rádio de Emergência
-- =====================================================================

-- 1. Criação / Ajuste da Tabela de Usuários
CREATE TABLE IF NOT EXISTS usuarios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(100) NOT NULL,
    email VARCHAR(100) NOT NULL UNIQUE,
    senha_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'listener', -- 'admin', 'feeder', 'listener'
    bio TEXT,
    avatar_url VARCHAR(255),
    is_premium BOOLEAN NOT NULL DEFAULT 0,
    stripe_customer_id VARCHAR(255),
    stripe_subscription_id VARCHAR(255),
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Tabela Principal do Domínio: Canais e Frequências de Rádio
CREATE TABLE IF NOT EXISTS canais (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT NOT NULL, -- Feeder (estação transmissora proprietária)
    nome VARCHAR(120) NOT NULL,
    frequencia VARCHAR(60) NOT NULL, -- Ex: "155.175 MHz VHF", "460.125 MHz DMR"
    categoria VARCHAR(50) NOT NULL, -- "Resgate & Bombeiros", "Polícia / Patrulha", "Rodovias", "Defesa Civil"
    cidade VARCHAR(100) NOT NULL,
    estado VARCHAR(2) NOT NULL,
    descricao TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'online', -- 'online', 'offline', 'manutencao'
    stream_url VARCHAR(255),
    icone_url VARCHAR(255),
    delay_segundos INT NOT NULL DEFAULT 600, -- 10 minutos para ouvintes gratuitos
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Tabela de Gravações e Despachos de Ocorrências (Arquivos no MinIO)
CREATE TABLE IF NOT EXISTS gravacoes_ocorrencias (
    id INT AUTO_INCREMENT PRIMARY KEY,
    canal_id INT NOT NULL,
    usuario_id INT NOT NULL, -- Feeder responsável pela transmissão/gravação
    titulo VARCHAR(150) NOT NULL,
    audio_url VARCHAR(255) NOT NULL, -- Chave / URL do arquivo de áudio no MinIO (.mp3, .ogg, .wav)
    duracao_segundos INT NOT NULL DEFAULT 60,
    data_hora_ocorrencia DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    descricao TEXT,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (canal_id) REFERENCES canais(id) ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Tabela de Canais Favoritados por Ouvintes
CREATE TABLE IF NOT EXISTS canais_favoritos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id INT NOT NULL,
    canal_id INT NOT NULL,
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_canal (usuario_id, canal_id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (canal_id) REFERENCES canais(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. Tabela de Tokens de Recuperação de Senha
CREATE TABLE IF NOT EXISTS reset_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    token VARCHAR(255) NOT NULL UNIQUE,
    usuario_id INT NOT NULL,
    criado_em DATETIME NOT NULL,
    expira_em DATETIME NOT NULL,
    usado BOOLEAN NOT NULL DEFAULT 0,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================
-- SEED DE DADOS INICIAIS (Senhas padrão: "senha123")
-- Hash gerado com Werkzeug: scrypt:32768:8:1$...
-- =====================================================================

-- Inserção de Usuários com diferentes papéis do negócio
INSERT INTO usuarios (id, nome, email, senha_hash, role, bio, is_premium) VALUES
(1, 'Administrador OpenWave', 'admin@openwave.local', 'scrypt:32768:8:1$KSmvQyV9Y4m7VvY1$5ebce1c67d710b1ca050bf3ce16cbe656cbdf917ea3fe61be2ea1298c928424a1b0be945ea138383fcf6ca010f37d7a8ceab3d0eb8be6f7f6c31ebdb0fae5306', 'admin', 'Operador central de telecomunicações e auditoria.', 1),
(2, 'Estação Feeder SP-Leste', 'feeder.sp@openwave.local', 'scrypt:32768:8:1$KSmvQyV9Y4m7VvY1$5ebce1c67d710b1ca050bf3ce16cbe656cbdf917ea3fe61be2ea1298c928424a1b0be945ea138383fcf6ca010f37d7a8ceab3d0eb8be6f7f6c31ebdb0fae5306', 'feeder', 'Scanner RTL-SDR v4 calibrado em antena colinear VHF/UHF a 30m.', 0),
(3, 'Rede Resgate Interior Feeder', 'feeder.resgate@openwave.local', 'scrypt:32768:8:1$KSmvQyV9Y4m7VvY1$5ebce1c67d710b1ca050bf3ce16cbe656cbdf917ea3fe61be2ea1298c928424a1b0be945ea138383fcf6ca010f37d7a8ceab3d0eb8be6f7f6c31ebdb0fae5306', 'feeder', 'Receptor Uniden BCD436HP monitorando ocorrências médicas e bombeiros.', 0),
(4, 'Marcos Rogério Antunes', 'marcos@openwave.local', 'scrypt:32768:8:1$KSmvQyV9Y4m7VvY1$5ebce1c67d710b1ca050bf3ce16cbe656cbdf917ea3fe61be2ea1298c928424a1b0be945ea138383fcf6ca010f37d7a8ceab3d0eb8be6f7f6c31ebdb0fae5306', 'listener', 'Comerciante e motorista atento à segurança do bairro. Plano Comunitário Free.', 0),
(5, 'Camila Sentinela Pro', 'pro@openwave.local', 'scrypt:32768:8:1$KSmvQyV9Y4m7VvY1$5ebce1c67d710b1ca050bf3ce16cbe656cbdf917ea3fe61be2ea1298c928424a1b0be945ea138383fcf6ca010f37d7a8ceab3d0eb8be6f7f6c31ebdb0fae5306', 'listener', 'Liderança comunitária e ouvinte assídua com plano Sentinela Pro ativo.', 1)
ON DUPLICATE KEY UPDATE nome=VALUES(nome), role=VALUES(role), is_premium=VALUES(is_premium);

-- Inserção de Canais do Negócio (Recurso Principal)
INSERT INTO canais (id, usuario_id, nome, frequencia, categoria, cidade, estado, descricao, status, stream_url, delay_segundos) VALUES
(1, 2, 'Corpo de Bombeiros & SAMU - Região Metropolitana', '193.000 MHz (VHF Analógico)', 'Resgate & Bombeiros', 'São Paulo', 'SP', 'Canal de despachos de chamados de urgência médica, resgates viários e combate a incêndios.', 'online', 'https://stream.openwave.local/live/bombeiros_sp.mp3', 0),
(2, 2, 'Polícia Rodoviária Estadual - Sistema Anhanguera/Bandeirantes', '460.125 MHz (DMR Digital)', 'Rodovias', 'Campinas', 'SP', 'Patrulhamento tático rodoviário, monitoramento de fluidez, acidentes e interdições de pistas.', 'online', 'https://stream.openwave.local/live/rodoviaria_sp.mp3', 0),
(3, 3, 'Defesa Civil Regional & Monitoramento Meteorológico', '148.550 MHz (VHF)', 'Defesa Civil', 'Ribeirão Preto', 'SP', 'Alertas de precipitações severas, quedas de árvores, enchentes e evacuações preventivas.', 'online', 'https://stream.openwave.local/live/defesacivil.mp3', 0),
(4, 3, 'Patrulhamento Comunitário & Ronda Rural', '452.300 MHz (P25 Digital)', 'Polícia / Patrulha', 'São José dos Campos', 'SP', 'Comunicações entre equipes de patrulha e apoio comunitário em áreas periféricas e rurais.', 'online', 'https://stream.openwave.local/live/patrulha.mp3', 0)
ON DUPLICATE KEY UPDATE nome=VALUES(nome), status=VALUES(status);

-- Inserção de Gravações de Ocorrências (MinIO)
INSERT INTO gravacoes_ocorrencias (id, canal_id, usuario_id, titulo, audio_url, duracao_segundos, data_hora_ocorrencia, descricao) VALUES
(1, 1, 2, 'Despacho Resgate: Colisão múltipla na Rodovia Ayrton Senna', 'gravacoes/ocorrencia_resgate_001.mp3', 84, NOW() - INTERVAL 2 HOUR, 'Equipes Bravo 03 e Auto-Bomba acionadas para atendimento prioritário com vítimas leves.'),
(2, 2, 2, 'Interdição Preventiva: Queda de barreira KM 78 Sentido Interior', 'gravacoes/ocorrencia_rodovia_002.mp3', 115, NOW() - INTERVAL 4 HOUR, 'Alerta aos usuários sobre pista lateral bloqueada e desvio para pista expressa.'),
(3, 3, 3, 'Alerta Meteorológico: Alagamento crítico na Avenida Marginal', 'gravacoes/ocorrencia_defesacivil_003.mp3', 62, NOW() - INTERVAL 6 HOUR, 'Transbordamento do córrego com bloqueio total de vias adjacentes.')
ON DUPLICATE KEY UPDATE titulo=VALUES(titulo);

-- Inserção de Canais Favoritos
INSERT INTO canais_favoritos (usuario_id, canal_id) VALUES
(4, 1),
(4, 2),
(5, 1),
(5, 3),
(5, 4)
ON DUPLICATE KEY UPDATE usuario_id=VALUES(usuario_id);
