CREATE DATABASE IF NOT EXISTS voicebot
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS agents (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    agent_type    VARCHAR(50)  NOT NULL,
    lang          VARCHAR(10)  NOT NULL,
    voice         VARCHAR(20)  NOT NULL DEFAULT 'F1',
    system_prompt MEDIUMTEXT,
    active        TINYINT(1)   NOT NULL DEFAULT 1,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_agents_type_lang (agent_type, lang)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS conversations (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    agent_id         INT,
    call_id          VARCHAR(64) NOT NULL,
    phone            VARCHAR(32),
    direction        ENUM('inbound','outbound') NOT NULL DEFAULT 'outbound',
    status           ENUM('active','ended','hangup','error') NOT NULL DEFAULT 'active',
    lang             VARCHAR(10) NOT NULL DEFAULT 'hi',
    outcome          VARCHAR(50),
    started_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at         DATETIME,
    duration_seconds INT,
    UNIQUE KEY uq_conv_call (call_id),
    KEY idx_conv_agent (agent_id),
    KEY idx_conv_status (status),
    CONSTRAINT fk_conv_agent FOREIGN KEY (agent_id) REFERENCES agents(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS messages (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    role            ENUM('system','user','assistant') NOT NULL,
    content         MEDIUMTEXT NOT NULL,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_msg_conv (conversation_id, id),
    CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sessions (
    conversation_id BIGINT NOT NULL,
    state_key       VARCHAR(50) NOT NULL,
    state_value     JSON,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, state_key),
    CONSTRAINT fk_sess_conv FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS orders (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    name            VARCHAR(255),
    phone           VARCHAR(32),
    email           VARCHAR(255),
    address         VARCHAR(1024),
    pincode         VARCHAR(12),
    raw_text        MEDIUMTEXT,
    confirmed_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_ord_conv (conversation_id),
    CONSTRAINT fk_ord_conv FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS agent_events (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    event           VARCHAR(50) NOT NULL,
    detail          VARCHAR(255),
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_evt_conv (conversation_id),
    KEY idx_evt_event (event),
    CONSTRAINT fk_evt_conv FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audio_artifacts (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    conversation_id BIGINT NOT NULL,
    kind            ENUM('caller','bot','bargein','merged') NOT NULL,
    file_path       VARCHAR(1024) NOT NULL,
    bytes           BIGINT,
    duration_seconds FLOAT,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_aa_conv (conversation_id),
    CONSTRAINT fk_aa_conv FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE
) ENGINE=InnoDB;
