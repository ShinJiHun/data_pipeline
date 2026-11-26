-- 토스증권 계좌 스냅샷 (invest/toss_snapshot.py 가 적재)
-- 먼저 riding_db_dev 에만 만든다. 운영(riding_db) 반영은 관리자 확인 후.
--
-- 금액·수량은 API 가 문자열 decimal 로 주므로 DECIMAL 로 그대로 저장한다(부동소수 변환 없음).
-- 손익률(rate)은 API 와 같은 소수비율 (0.1077 = 10.77%).
-- session: KR_CLOSE(16:10 KST, 국내 마감 후) / US_CLOSE(07:10 KST, 미국 마감 후)
-- snapshot_date: 실행한 날의 KST 날짜. US_CLOSE 는 직전 미국 거래일 장을 반영한다.
-- account_suffix: accountSeq 끝 4자리. 계좌번호(accountNo)는 저장하지 않는다.

CREATE TABLE IF NOT EXISTS invest_account_snapshot (
  id                        BIGINT AUTO_INCREMENT PRIMARY KEY,
  snapshot_at               DATETIME      NOT NULL COMMENT '조회 시각(KST)',
  snapshot_date             DATE          NOT NULL COMMENT '실행일(KST)',
  session                   VARCHAR(10)   NOT NULL COMMENT 'KR_CLOSE / US_CLOSE',
  account_suffix            VARCHAR(4)    NOT NULL COMMENT 'accountSeq 끝 4자리',
  purchase_krw              DECIMAL(24,4) NULL COMMENT '투자원금(국내)',
  purchase_usd              DECIMAL(24,4) NULL COMMENT '투자원금(해외), 해외 종목 없으면 NULL',
  eval_krw                  DECIMAL(24,4) NULL COMMENT '평가금액(국내)',
  eval_usd                  DECIMAL(24,4) NULL,
  eval_after_cost_krw       DECIMAL(24,4) NULL COMMENT '세금·수수료 공제 후 평가금액',
  eval_after_cost_usd       DECIMAL(24,4) NULL,
  pnl_krw                   DECIMAL(24,4) NULL COMMENT '손익금액',
  pnl_usd                   DECIMAL(24,4) NULL,
  pnl_after_cost_krw        DECIMAL(24,4) NULL,
  pnl_after_cost_usd        DECIMAL(24,4) NULL,
  pnl_rate                  DECIMAL(12,6) NULL COMMENT '손익률, 전체 원화 환산 기준',
  pnl_rate_after_cost       DECIMAL(12,6) NULL,
  daily_pnl_krw             DECIMAL(24,4) NULL COMMENT '일간 손익금액',
  daily_pnl_usd             DECIMAL(24,4) NULL,
  daily_pnl_rate            DECIMAL(12,6) NULL COMMENT '일간 손익률, 원화 환산 기준',
  holding_count             INT           NOT NULL DEFAULT 0,
  created_at                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_invest_acct_snap (snapshot_date, session, account_suffix),
  KEY idx_invest_acct_suffix_date (account_suffix, session, snapshot_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='토스증권 계좌 합계 스냅샷';

CREATE TABLE IF NOT EXISTS invest_snapshot (
  id                        BIGINT AUTO_INCREMENT PRIMARY KEY,
  account_snapshot_id       BIGINT        NOT NULL,
  snapshot_at               DATETIME      NOT NULL,
  snapshot_date             DATE          NOT NULL,
  session                   VARCHAR(10)   NOT NULL,
  account_suffix            VARCHAR(4)    NOT NULL,
  market                    VARCHAR(4)    NOT NULL COMMENT 'marketCountry: KR / US',
  symbol                    VARCHAR(20)   NOT NULL,
  name                      VARCHAR(200)  NULL,
  currency                  VARCHAR(3)    NOT NULL COMMENT 'KRW / USD (거래 통화)',
  quantity                  DECIMAL(24,8) NOT NULL COMMENT '보유 수량(소수점 주식 대비)',
  avg_price                 DECIMAL(24,8) NULL COMMENT '매수 평균가(거래 통화)',
  current_price             DECIMAL(24,8) NULL COMMENT '현재가(거래 통화)',
  purchase_amount           DECIMAL(24,4) NULL COMMENT '매입금액',
  eval_amount               DECIMAL(24,4) NULL COMMENT '평가금액',
  eval_after_cost           DECIMAL(24,4) NULL,
  pnl                       DECIMAL(24,4) NULL COMMENT '손익금액',
  pnl_after_cost            DECIMAL(24,4) NULL,
  pnl_rate                  DECIMAL(12,6) NULL,
  pnl_rate_after_cost       DECIMAL(12,6) NULL,
  daily_pnl                 DECIMAL(24,4) NULL,
  daily_pnl_rate            DECIMAL(12,6) NULL,
  commission                DECIMAL(24,4) NULL,
  tax                       DECIMAL(24,4) NULL,
  created_at                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_invest_snap (snapshot_date, session, account_suffix, market, symbol),
  KEY idx_invest_snap_symbol (symbol, snapshot_date),
  KEY idx_invest_snap_acct (account_snapshot_id),
  CONSTRAINT fk_invest_snap_acct FOREIGN KEY (account_snapshot_id)
    REFERENCES invest_account_snapshot (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='토스증권 보유 종목 스냅샷';

-- 거래내역 (GET /api/v1/transactions). API 가 거래 식별자를 주지 않아서
-- 매 실행마다 계좌별로 [미결제 전체 + 최근 N일 결제분] 을 지우고 다시 넣는다(멱등).
-- 금액은 currency 통화, 부호 없음. 현금 방향은 transaction_type 으로 판단(SELL·DEPOSIT·DIVIDEND… 유입).
CREATE TABLE IF NOT EXISTS invest_transaction (
  id                        BIGINT AUTO_INCREMENT PRIMARY KEY,
  account_suffix            VARCHAR(4)    NOT NULL,
  settlement_date           DATE          NOT NULL COMMENT '결제일(KST), 미결제는 예상 결제일',
  settled                   TINYINT(1)    NOT NULL COMMENT '결제 완료 여부',
  transaction_time          DATETIME      NULL COMMENT '결제 처리 시각(KST), 미결제는 NULL',
  transaction_type          VARCHAR(30)   NOT NULL COMMENT 'BUY/SELL/DIVIDEND/DEPOSIT/…',
  is_cancellation           TINYINT(1)    NOT NULL DEFAULT 0,
  market                    VARCHAR(4)    NOT NULL COMMENT 'KR / US',
  currency                  VARCHAR(3)    NOT NULL,
  asset_class               VARCHAR(10)   NULL COMMENT 'STOCK/BOND/OPTION, 현금 거래는 NULL',
  symbol                    VARCHAR(40)   NULL,
  isin_code                 VARCHAR(12)   NULL,
  quantity                  DECIMAL(24,8) NULL,
  filled_price              DECIMAL(24,8) NULL,
  amount                    DECIMAL(24,4) NULL,
  commission                DECIMAL(24,4) NULL,
  tax                       DECIMAL(24,4) NULL,
  net_amount                DECIMAL(24,4) NULL COMMENT '수수료·세금 반영 정산금액(절대값)',
  exchange_rate             DECIMAL(16,4) NULL COMMENT 'USD 결제 완료 거래의 환율',
  fetched_at                DATETIME      NOT NULL,
  created_at                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_invest_tx_acct_date (account_suffix, settlement_date),
  KEY idx_invest_tx_symbol (symbol, settlement_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='토스증권 거래내역';
