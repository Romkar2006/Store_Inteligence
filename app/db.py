import os
from dotenv import load_dotenv
# Load .env file robustly from project root using absolute path relative to this file
_current_dir = os.path.dirname(os.path.abspath(__file__))
_dotenv_path = os.path.join(_current_dir, "..", ".env")
load_dotenv(_dotenv_path)
import logging
from datetime import datetime, timezone, timedelta
import pandas as pd
from sqlalchemy import (
    create_engine, MetaData, Table, Column, String, Integer, Float, Index, select
)
from sqlalchemy.ext.asyncio import create_async_engine

# Set up logging
logger = logging.getLogger("store_intelligence.db")

# Timezone definition (Correction 1)
IST = timezone(timedelta(hours=5, minutes=30))

# Path setup (Correction 8)
DB_PATH = os.environ.get('DB_PATH', 'store_intelligence.db')
# Ensure DB directory exists if specified as absolute path
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)

POS_CSV_PATH = os.environ.get(
    'POS_CSV_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'pos_transactions.csv')
)

# Async and Sync Engines
DATABASE_URL = f"sqlite:///{DB_PATH}"
ASYNC_DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

sync_engine = create_engine(DATABASE_URL, echo=False)
async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)

metadata = MetaData()

# Define tables
events = Table(
    'events', metadata,
    Column('event_id', String, primary_key=True),
    Column('store_id', String, nullable=False),
    Column('camera_id', String, nullable=False),
    Column('visitor_id', String, nullable=False),
    Column('event_type', String, nullable=False),
    Column('timestamp', String, nullable=False),  # ISO-8601 string with timezone offset
    Column('zone_id', String, nullable=True),
    Column('dwell_ms', Integer, default=0),
    Column('is_staff', Integer, default=0),       # 0/1 (exclude from customer metrics)
    Column('confidence', Float, nullable=True),
    Column('queue_depth', Integer, nullable=True),
    Column('sku_zone', String, nullable=True),
    Column('session_seq', Integer, nullable=True),
    Column('ingested_at', String, nullable=False)
)

pos_transactions = Table(
    'pos_transactions', metadata,
    Column('transaction_id', String, primary_key=True),
    Column('store_id', String, nullable=False),
    Column('timestamp', String, nullable=False),  # ISO-8601 IST string
    Column('basket_value_inr', Float, nullable=False)
)

# Create indices
Index('idx_events_store_ts', events.c.store_id, events.c.timestamp)
Index('idx_events_visitor', events.c.visitor_id)
Index('idx_events_type', events.c.event_type)

def init_db():
    """Synchronously create tables and load initial POS data if empty."""
    metadata.create_all(sync_engine)
    logger.info("Database tables initialized successfully.")
    
    # Load POS transactions if empty
    with sync_engine.connect() as conn:
        result = conn.execute(select(pos_transactions).limit(1)).fetchone()
        if result is None:
            load_pos_data(conn)
        else:
            logger.info("POS transactions already exist in database. Skipping load.")

def load_pos_data(conn):
    """Load and parse POS data from CSV into SQL database (Correction 2)."""
    # 1. Load Store 1 POS (ST1008)
    if os.path.exists(POS_CSV_PATH):
        try:
            logger.info(f"Loading Store 1 POS data from {POS_CSV_PATH}...")
            df = pd.read_csv(
                POS_CSV_PATH,
                usecols=['order_id', 'store_id', 'order_date', 'order_time', 'total_amount']
            )
            df = df.groupby(['order_id', 'store_id', 'order_date', 'order_time'], as_index=False)['total_amount'].sum()
            df['timestamp'] = df.apply(
                lambda r: datetime.strptime(
                    f"{r['order_date']} {r['order_time']}", "%d-%m-%Y %H:%M:%S"
                ).replace(tzinfo=IST).isoformat(), axis=1
            )
            df = df.rename(columns={
                'order_id': 'transaction_id',
                'total_amount': 'basket_value_inr'
            })
            records = df[['transaction_id', 'store_id', 'timestamp', 'basket_value_inr']]
            records.to_sql('pos_transactions', con=conn, if_exists='append', index=False)
            logger.info(f"Successfully loaded {len(records)} transactions for Store 1 (ST1008).")
        except Exception as e:
            logger.error(f"Failed to load Store 1 POS data: {e}", exc_info=True)
    else:
        logger.warning(f"Store 1 POS CSV file not found at {POS_CSV_PATH}.")

    # 2. Load Store 2 POS (ST1009)
    store_2_csv = os.path.join(os.path.dirname(POS_CSV_PATH), 'store_2_pos_transactions.csv')
    if os.path.exists(store_2_csv):
        try:
            logger.info(f"Loading Store 2 POS data from {store_2_csv}...")
            df2 = pd.read_csv(
                store_2_csv,
                usecols=['order_id', 'store_id', 'order_date', 'order_time', 'total_amount']
            )
            # Override store_id to ST1009 to distinguish it from ST1008
            df2['store_id'] = 'ST1009'
            df2 = df2.groupby(['order_id', 'store_id', 'order_date', 'order_time'], as_index=False)['total_amount'].sum()
            df2['timestamp'] = df2.apply(
                lambda r: datetime.strptime(
                    f"{r['order_date']} {r['order_time']}", "%d-%m-%Y %H:%M:%S"
                ).replace(tzinfo=IST).isoformat(), axis=1
            )
            df2 = df2.rename(columns={
                'order_id': 'transaction_id',
                'total_amount': 'basket_value_inr'
            })
            records2 = df2[['transaction_id', 'store_id', 'timestamp', 'basket_value_inr']]
            records2.to_sql('pos_transactions', con=conn, if_exists='append', index=False)
            logger.info(f"Successfully loaded {len(records2)} transactions for Store 2 (ST1009).")
        except Exception as e:
            logger.error(f"Failed to load Store 2 POS data: {e}", exc_info=True)
    else:
        logger.warning(f"Store 2 POS CSV file not found at {store_2_csv}.")

async def get_db_conn():
    """Dependency for obtaining an async database connection."""
    async with async_engine.connect() as conn:
        yield conn
