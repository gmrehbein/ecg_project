import os
import json
import duckdb
from pathlib import Path
from typing import Dict, Optional


class ECGSession:
    def __init__(self, session_path: str):
        self.session_dir = Path(session_path)

        # Paths
        self.raw_path = self.session_dir / "raw.parquet"
        self.filtered_path = self.session_dir / "filtered.parquet"
        self.bpm_path = self.session_dir / "bpm.parquet"
        self.meta_path = self.session_dir / "session.meta.json"

        if not self.meta_path.exists():
            raise FileNotFoundError(f"Metadata file missing: {self.meta_path}")

        with open(self.meta_path, "r") as f:
            self.meta = json.load(f)

        # Create DuckDB in-memory connection
        self.con = duckdb.connect()

        # Register Parquet files as virtual tables if they exist
        if self.raw_path.exists():
            self.con.execute(f"CREATE VIEW raw AS SELECT * FROM '{self.raw_path}'")
        if self.filtered_path.exists():
            self.con.execute(f"CREATE VIEW filtered AS SELECT * FROM '{self.filtered_path}'")
        if self.bpm_path.exists():
            self.con.execute(f"CREATE VIEW bpm AS SELECT * FROM '{self.bpm_path}'")

    def query(self, sql: str):
        """
        Run an SQL query on the session data.

        Example:
            session.query("SELECT * FROM filtered WHERE lead_II > 0.7")
        """
        return self.con.execute(sql).fetchdf()

    def list_tables(self):
        return self.con.execute("SHOW TABLES").fetchall()

    def get_metadata(self) -> Dict:
        return self.meta

