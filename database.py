"""
Database module for API Test Command Center.
Handles SQL Server database operations for saving and retrieving test cases.
"""

import pyodbc
import json
import uuid
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse


class TestCaseDatabase:
    """Database operations for test case storage."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize database connection.
        
        Args:
            config: Database configuration dictionary with keys:
                   - server: SQL Server name (e.g., 'LPT2149-B1')
                   - database: Database name (e.g., 'API_Test_Cases')
                   - username: Username (e.g., 'testUser1')
                   - password: Password (e.g., 'TestUser@1')
                   - driver: ODBC driver (default: '{ODBC Driver 17 for SQL Server}')
        """
        if config is None:
            config = self._get_default_config()
        
        self.config = config
        self.connection_string = self._build_connection_string(config)
        
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default database configuration."""
        return {
            'server': 'LPT2149-B1',
            'database': 'TestCasesDB',
            'driver': '{ODBC Driver 17 for SQL Server}',
            'use_windows_auth': True  # Use Windows Authentication
        }
    
    def _build_connection_string(self, config: Dict[str, Any]) -> str:
        """Build ODBC connection string from configuration."""
        driver = config.get('driver', '{ODBC Driver 17 for SQL Server}')
        server = config['server']
        database = config['database']
        
        # Build base connection string
        conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};"
        
        # Add authentication method
        if config.get('use_windows_auth', True):
            # Windows Authentication (Trusted Connection)
            conn_str += "Trusted_Connection=yes;"
        else:
            # SQL Server Authentication
            username = config.get('username', '')
            password = config.get('password', '')
            if username and password:
                conn_str += f"UID={username};PWD={password};"
            else:
                # Fall back to Windows Auth if no credentials provided
                conn_str += "Trusted_Connection=yes;"
        
        # Add additional options for better compatibility
        conn_str += "TrustServerCertificate=yes;"
        
        return conn_str
    
    def _generate_table_name(self, base_url: str, endpoint: str, method: str) -> str:
        """
        Generate a table name from base URL, endpoint, and HTTP method.
        
        Args:
            base_url: Base URL (e.g., 'https://petstore.swagger.io/v2')
            endpoint: API endpoint (e.g., '/pet/')
            method: HTTP method (e.g., 'POST')
            
        Returns:
            Valid SQL Server table name
        """
        # Normalize base_url: remove protocol, replace special chars
        # if base_url:
        #     # Remove http:// or https://
        #     if base_url.startswith('http://'):
        #         base_url = base_url[7:]
        #     elif base_url.startswith('https://'):
        #         base_url = base_url[8:]
            
        #     # Replace dots, slashes, and other special chars with underscores
        #     base_url_clean = ''.join(c if c.isalnum() else '_' for c in base_url)
        #     # Remove consecutive underscores
        #     while '__' in base_url_clean:
        #         base_url_clean = base_url_clean.replace('__', '_')
        #     # Remove leading/trailing underscores
        #     base_url_clean = base_url_clean.strip('_')
        # else:
        #     base_url_clean = 'default'

        hostname = urlparse(base_url).hostname if base_url else None

        if hostname:
        # Extract the first part of the hostname
            url_name = hostname.split('.')[0]
        else:
            url_name = 'default'
        
        # Normalize endpoint: remove leading slash, replace special chars
        if endpoint:
            endpoint_clean = endpoint.lstrip('/')
            endpoint_clean = ''.join(c if c.isalnum() else '_' for c in endpoint_clean)
            while '__' in endpoint_clean:
                endpoint_clean = endpoint_clean.replace('__', '_')
            endpoint_clean = endpoint_clean.strip('_')
        else:
            endpoint_clean = 'root'
        
        # Method is already clean (GET, POST, PUT, DELETE, PATCH)
        method_clean = method.upper()
        
        # Combine and ensure table name is valid (max 128 chars in SQL Server)
        table_name = f"test_cases_{url_name}_{endpoint_clean}_{method_clean}"
        
        # Truncate if too long
        if len(table_name) > 128:
            # Keep first 100 chars and add hash of full name
            import hashlib
            hash_part = hashlib.md5(table_name.encode()).hexdigest()[:8]
            table_name = table_name[:100] + '_' + hash_part
        
        return table_name
    
    def _ensure_table_exists(self, table_name: str, cursor) -> bool:
        """
        Ensure a test cases table exists. If table already exists,
        delete it and recreate it with the new test cases.
        
        Args:
            table_name: Name of the table to check/create
            cursor: Database cursor
            
        Returns:
            True if table exists or was created successfully
        """
        try:
            # Check if table exists
            cursor.execute(f"""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_NAME = '{table_name}'
            """)
            
            table_exists = cursor.fetchone()[0] > 0
            
            if table_exists:
                if not self._can_replace_test_case_table(table_name, cursor):
                    return False

                # Table exists, delete it and related session records
                print(f"Table '{table_name}' already exists. Deleting and recreating...")
                
                # First drop the table (removes foreign key constraint)
                cursor.execute(f"DROP TABLE IF EXISTS [{table_name}]")
                print(f"Dropped existing table '{table_name}'")
                
                # Now delete any session records that reference this table
                cursor.execute("""
                    DELETE FROM test_case_sessions
                    WHERE table_name = ?
                """, table_name)
                print(f"Deleted session records referencing table '{table_name}'")
            
            # Create the table (whether it existed or not)
            print(f"Creating table '{table_name}'...")
            cursor.execute(f"""
                CREATE TABLE [{table_name}] (
                    test_case_id NVARCHAR(50) PRIMARY KEY,
                    session_id NVARCHAR(50),
                    test_case_number INT NOT NULL,
                    test_type NVARCHAR(50) NOT NULL,
                    scenario NVARCHAR(MAX) NOT NULL,
                    input_body NVARCHAR(MAX),
                    expected_response NVARCHAR(MAX),
                    expected_status_codes NVARCHAR(100),
                    base_url NVARCHAR(1000),
                    endpoint NVARCHAR(1000),
                    http_method NVARCHAR(10),
                    metadata NVARCHAR(MAX),
                    created_at DATETIME DEFAULT GETDATE(),
                    FOREIGN KEY (session_id) REFERENCES test_case_sessions(session_id)
                )
            """)
            
            # Create index on session_id
            cursor.execute(f"""
                CREATE INDEX idx_{table_name}_session_id
                ON [{table_name}](session_id)
            """)
            
            print(f"Table '{table_name}' created successfully")
            
            return True
            
        except Exception as e:
            print(f"Error ensuring table '{table_name}' exists: {e}")
            return False

    def _can_replace_test_case_table(self, table_name: str, cursor) -> bool:
        """Return False when a suite currently references the test-case table."""
        try:
            cursor.execute("""
                IF OBJECT_ID('active_test_suite_cases', 'U') IS NOT NULL
                SELECT COUNT(*) FROM active_test_suite_cases WHERE table_name = ?
                ELSE
                SELECT 0
            """, table_name)
            row = cursor.fetchone()
            references = int(row[0]) if row and row[0] is not None else 0
            if references:
                print(
                    f"Cannot replace test case table '{table_name}': "
                    f"{references} suite relation(s) exist"
                )
                return False
            return True
        except Exception as e:
            print(f"Error checking suite references for '{table_name}': {e}")
            return False

    def _ensure_active_testcase_pool_table_exists(self, cursor) -> bool:
        """Ensure the active testcase pool table exists."""
        try:
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='active_testcase_pool' AND xtype='U')
                CREATE TABLE active_testcase_pool (
                    pool_item_id NVARCHAR(50) PRIMARY KEY,
                    group_key NVARCHAR(255) NOT NULL,
                    test_case_index INT NOT NULL,
                    test_case_id NVARCHAR(100),
                    test_case_data NVARCHAR(MAX) NOT NULL,
                    created_at DATETIME DEFAULT GETDATE()
                )
            """)

            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_active_testcase_pool_group_key')
                CREATE INDEX idx_active_testcase_pool_group_key ON active_testcase_pool(group_key)
            """)

            return True
        except Exception as e:
            print(f"Error ensuring 'active_testcase_pool' table exists: {e}")
            return False

    def _ensure_active_test_suites_table_exists(self, cursor) -> bool:
        """Ensure the active test suites table exists."""
        try:
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='active_test_suites' AND xtype='U')
                CREATE TABLE active_test_suites (
                    suite_id NVARCHAR(100) PRIMARY KEY,
                    suite_name NVARCHAR(255) NOT NULL,
                    suite_index INT NOT NULL,
                    suite_data NVARCHAR(MAX) NOT NULL,
                    is_active BIT DEFAULT 0,
                    created_at DATETIME DEFAULT GETDATE(),
                    updated_at DATETIME DEFAULT GETDATE()
                )
            """)

            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_active_test_suites_index')
                CREATE INDEX idx_active_test_suites_index ON active_test_suites(suite_index)
            """)

            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_active_test_suites_active')
                CREATE INDEX idx_active_test_suites_active ON active_test_suites(is_active)
            """)

            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='active_test_suite_cases' AND xtype='U')
                CREATE TABLE active_test_suite_cases (
                    suite_id NVARCHAR(100) NOT NULL,
                    table_name NVARCHAR(255) NOT NULL,
                    test_case_id NVARCHAR(100) NOT NULL,
                    created_at DATETIME DEFAULT GETDATE(),
                    CONSTRAINT pk_active_test_suite_cases
                        PRIMARY KEY (suite_id, table_name, test_case_id),
                    CONSTRAINT fk_active_test_suite_cases_suite
                        FOREIGN KEY (suite_id) REFERENCES active_test_suites(suite_id)
                        ON DELETE CASCADE
                )
            """)

            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_active_test_suite_cases_table')
                CREATE INDEX idx_active_test_suite_cases_table
                    ON active_test_suite_cases(table_name, test_case_id)
            """)

            return True
        except Exception as e:
            print(f"Error ensuring 'active_test_suites' table exists: {e}")
            return False

    def _ensure_api_specifications_table_exists(self, cursor) -> bool:
        """Ensure uploaded Swagger/OpenAPI documents can be persisted."""
        try:
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='api_specifications' AND xtype='U')
                CREATE TABLE api_specifications (
                    spec_id NVARCHAR(100) PRIMARY KEY,
                    spec_name NVARCHAR(255) NOT NULL,
                    base_url NVARCHAR(1000),
                    spec_data NVARCHAR(MAX) NOT NULL,
                    created_at DATETIME DEFAULT GETDATE(),
                    updated_at DATETIME DEFAULT GETDATE()
                )
            """)
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_api_specifications_name')
                CREATE INDEX idx_api_specifications_name ON api_specifications(spec_name)
            """)
            return True
        except Exception as e:
            print(f"Error ensuring 'api_specifications' table exists: {e}")
            return False

    def save_api_specification(self, spec_id: str, spec_name: str, base_url: str, spec_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Persist a normalized Swagger/OpenAPI document."""
        success, message = self.test_connection()
        if not success:
            success, message = self._create_database_and_tables()
            if not success:
                return False, message

        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if not self._ensure_api_specifications_table_exists(cursor):
                return False, "Failed to create or verify api_specifications table"
            cursor.execute("""
                MERGE api_specifications AS target
                USING (SELECT ? AS spec_id) AS source ON target.spec_id = source.spec_id
                WHEN MATCHED THEN UPDATE SET
                    spec_name = ?, base_url = ?, spec_data = ?, updated_at = GETDATE()
                WHEN NOT MATCHED THEN INSERT
                    (spec_id, spec_name, base_url, spec_data)
                    VALUES (?, ?, ?, ?);
            """, spec_id, spec_name, base_url, json.dumps(spec_data, ensure_ascii=False),
                spec_id, spec_name, base_url, json.dumps(spec_data, ensure_ascii=False))
            conn.commit()
            return True, "API specification saved"
        except Exception as e:
            if conn is not None:
                conn.rollback()
            return False, f"Failed to save API specification: {e}"
        finally:
            if conn is not None:
                conn.close()

    def get_api_specifications(self) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Load normalized Swagger/OpenAPI documents."""
        success, message = self.test_connection()
        if not success:
            success, message = self._create_database_and_tables()
            if not success:
                return False, message, []
        conn = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            if not self._ensure_api_specifications_table_exists(cursor):
                return False, "Failed to create or verify api_specifications table", []
            cursor.execute("SELECT spec_id, spec_name, base_url, spec_data FROM api_specifications ORDER BY updated_at DESC")
            specifications = []
            for row in cursor.fetchall():
                try:
                    data = json.loads(row.spec_data)
                except (TypeError, json.JSONDecodeError):
                    data = {}
                specifications.append({
                    'id': row.spec_id,
                    'name': row.spec_name,
                    'base_url': row.base_url or '',
                    'data': data
                })
            return True, f"Loaded {len(specifications)} API specifications", specifications
        except Exception as e:
            return False, f"Failed to load API specifications: {e}", []
        finally:
            if conn is not None:
                conn.close()
    
    def _extract_response_code(self, expected_str: str) -> str:
        """
        Extract HTTP status codes from expected response string.
        This matches the logic used in app.py's extract_response_code function.
        
        Args:
            expected_str: The expected response string
            
        Returns:
            Comma-separated string of status codes (e.g., "200,201") or "N/A"
        """
        if not expected_str:
            return "N/A"
        
        # Look for all 3-digit numbers starting with 1-5 (standard HTTP status codes)
        matches = re.findall(r'\b([1-5]\d{2})\b', str(expected_str))
        if matches:
            return ",".join(matches)
        
        # Fallback to keywords if no 3-digit code is found
        expected_lower = str(expected_str).lower()
        if 'created' in expected_lower:
            return "201"
        if 'no content' in expected_lower:
            return "204"
        if 'success' in expected_lower or 'ok' in expected_lower:
            return "200"
        if 'bad request' in expected_lower or 'invalid' in expected_lower or 'missing' in expected_lower:
            return "400"
        if 'unauthorized' in expected_lower:
            return "401"
        if 'forbidden' in expected_lower:
            return "403"
        if 'not found' in expected_lower:
            return "404"
        if 'conflict' in expected_lower:
            return "409"
        if 'too many' in expected_lower or 'rate limit' in expected_lower:
            return "429"
        return "N/A"
    
    def get_connection(self):
        """Get a new database connection."""
        try:
            return pyodbc.connect(self.connection_string)
        except pyodbc.Error as e:
            raise ConnectionError(f"Failed to connect to database: {str(e)}")
    
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test database connection and ensure tables exist.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # First try to connect to the target database
            try:
                conn = self.get_connection()
                cursor = conn.cursor()

                def fetch_scalar() -> int:
                    row = cursor.fetchone()
                    return int(row[0]) if row and row[0] is not None else 0
                
                # Check if test_case_sessions table exists
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_NAME = 'test_case_sessions'
                """)
                table_count = fetch_scalar()
                
                if table_count == 1:
                    # Check if table_name column exists
                    cursor.execute("""
                        SELECT COUNT(*)
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_NAME = 'test_case_sessions' AND COLUMN_NAME = 'table_name'
                    """)
                    column_count = fetch_scalar()
                    
                    if column_count == 1:
                        conn.close()
                        return True, "Database connection successful, sessions table exists with table_name column"
                    else:
                        # Column is missing, need to upgrade schema
                        print("Table exists but missing table_name column, upgrading schema...")
                        # Add the column
                        cursor.execute("""
                            ALTER TABLE test_case_sessions ADD table_name NVARCHAR(255) NULL
                        """)
                        cursor.execute("""
                            UPDATE test_case_sessions SET table_name = 'test_cases' WHERE table_name IS NULL
                        """)
                        cursor.execute("""
                            ALTER TABLE test_case_sessions ALTER COLUMN table_name NVARCHAR(255) NOT NULL
                        """)
                        conn.commit()
                        conn.close()
                        return True, "Database schema upgraded successfully, added table_name column"
                else:
                    # Tables don't exist, create them
                    conn.close()
                    return self._create_database_and_tables()
                    
            except pyodbc.Error as e:
                # If connection fails, check if database doesn't exist
                error_msg = str(e)
                if "Cannot open database" in error_msg or "database .* requested by the login" in error_msg:
                    # Database doesn't exist, try to create it
                    return self._create_database_and_tables()
                else:
                    # Other connection error
                    return False, f"Database connection failed: {error_msg}"
                
        except Exception as e:
            return False, f"Database connection failed: {str(e)}"
    
    def _create_database_and_tables(self) -> Tuple[bool, str]:
        """
        Create database and tables if they don't exist.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Connect to master database
            master_config = self.config.copy()
            master_config['database'] = 'master'
            master_conn_str = self._build_connection_string(master_config)
            
            conn = pyodbc.connect(master_conn_str)
            cursor = conn.cursor()
            conn.autocommit = True
            
            db_name = self.config['database']
            
            # Check if database exists
            cursor.execute(f"SELECT name FROM sys.databases WHERE name = '{db_name}'")
            if cursor.fetchone():
                print(f"Database '{db_name}' already exists")
            else:
                # Create database
                print(f"Creating database '{db_name}'...")
                cursor.execute(f"CREATE DATABASE [{db_name}]")
                print(f"Database '{db_name}' created successfully")
            
            conn.close()
            
            # Now connect to the new database and create tables
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Create test_case_sessions table
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='test_case_sessions' AND xtype='U')
                CREATE TABLE test_case_sessions (
                    session_id NVARCHAR(50) PRIMARY KEY,
                    session_name NVARCHAR(255) NOT NULL,
                    endpoint NVARCHAR(1000) NOT NULL,
                    http_method NVARCHAR(10) NOT NULL,
                    base_url NVARCHAR(1000),
                    created_by NVARCHAR(100),
                    created_at DATETIME DEFAULT GETDATE(),
                    total_test_cases INT DEFAULT 0,
                    table_name NVARCHAR(255) NOT NULL
                )
            """)
            
            # Check if table_name column exists, add it if missing
            cursor.execute("""
                IF NOT EXISTS (
                    SELECT * FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = 'test_case_sessions' AND COLUMN_NAME = 'table_name'
                )
                BEGIN
                    -- First add as nullable
                    ALTER TABLE test_case_sessions ADD table_name NVARCHAR(255) NULL
                    -- Set default value for existing rows
                    UPDATE test_case_sessions SET table_name = 'test_cases' WHERE table_name IS NULL
                    -- Now alter to NOT NULL
                    ALTER TABLE test_case_sessions ALTER COLUMN table_name NVARCHAR(255) NOT NULL
                END
            """)
            
            # Note: We no longer create a generic test_cases table here
            # Dynamic tables will be created by _ensure_table_exists when needed
            
            # Create indexes
            cursor.execute("""
                IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name='idx_test_case_sessions_created_at')
                CREATE INDEX idx_test_case_sessions_created_at ON test_case_sessions(created_at DESC)
            """)

            if not self._ensure_active_testcase_pool_table_exists(cursor):
                conn.rollback()
                conn.close()
                return False, "Failed to create or verify active_testcase_pool table"

            if not self._ensure_active_test_suites_table_exists(cursor):
                conn.rollback()
                conn.close()
                return False, "Failed to create or verify active_test_suites table"

            if not self._ensure_api_specifications_table_exists(cursor):
                conn.rollback()
                conn.close()
                return False, "Failed to create or verify api_specifications table"
            
            conn.commit()
            conn.close()
            
            return True, f"Database '{db_name}' and tables created successfully"
            
        except Exception as e:
            return False, f"Failed to create database and tables: {str(e)}"
    
    def save_test_cases(self, session_data: Dict[str, Any], test_cases: List[Dict[str, Any]]) -> Tuple[bool, str, Optional[str], int]:
        """
        Save test cases to database.
        
        Args:
            session_data: Session metadata including:
                         - endpoint: API endpoint
                         - method: HTTP method
                         - base_url: Base URL
                         - session_name: Optional session name
                         - created_by: Optional creator name
            test_cases: List of test case dictionaries
            
        Returns:
            Tuple of (success, message, session_id, saved_count)
        """
        if not test_cases:
            return False, "No test cases to save", None, 0
        
        # First, ensure database and tables exist
        success, message = self.test_connection()
        if not success:
            # Try to create database and tables
            success, message = self._create_database_and_tables()
            if not success:
                return False, f"Failed to create database/tables: {message}", None, 0
        
        session_id = str(uuid.uuid4())
        saved_count = 0
        conn = None
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Generate table name from base_url, endpoint, and method
            session_name = session_data.get('session_name', f"Test Cases {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            endpoint = session_data.get('endpoint', '/api/test')
            method = session_data.get('method', 'GET')
            base_url = session_data.get('base_url', '')
            created_by = session_data.get('created_by', 'system')
            
            # Generate table name for this endpoint/method combination
            table_name = self._generate_table_name(base_url, endpoint, method)
            # Debug only.
            # print(f"[DEBUG] Generated table name: {table_name}")
            
            # Ensure the table exists
            if not self._ensure_table_exists(table_name, cursor):
                conn.rollback()
                conn.close()
                return False, (
                    f"Cannot replace test-case table '{table_name}': "
                    "it is referenced by an active test suite"
                ), None, 0
            
            # Filter test cases: only save those matching the frontend values
            filtered_test_cases = []
            for test_case in test_cases:
                # Get values from test case (default to session values if not present)
                tc_endpoint = test_case.get('endpoint', endpoint)
                tc_method = test_case.get('method', method)
                tc_base_url = test_case.get('baseUrl', base_url)
                
                # Check if test case matches the frontend values
                if (tc_endpoint == endpoint and
                    tc_method == method and
                    tc_base_url == base_url):
                    filtered_test_cases.append(test_case)
                else:
                    # Debug only.
                    # print("[DEBUG] Skipping test case - doesn't match frontend values:")
                    # print(f"  Test case: endpoint={tc_endpoint}, method={tc_method}, base_url={tc_base_url}")
                    # print(f"  Frontend: endpoint={endpoint}, method={method}, base_url={base_url}")
                    pass
            
            if not filtered_test_cases:
                return False, "No test cases match the frontend values (base URL, endpoint, method)", None, 0
            
            # Debug only.
            # print(f"[DEBUG] Filtered {len(filtered_test_cases)}/{len(test_cases)} test cases that match frontend values")
            
            # Save session with filtered count
            cursor.execute("""
                INSERT INTO test_case_sessions
                (session_id, session_name, endpoint, http_method, base_url, created_by, total_test_cases, table_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, session_id, session_name, endpoint, method, base_url, created_by, len(filtered_test_cases), table_name)
            
            # Save filtered test cases to the dynamic table
            for i, test_case in enumerate(filtered_test_cases, 1):
                test_case_id = str(
                    test_case.get('id') or uuid.uuid4()
                )[:50]
                test_type = test_case.get('type', 'Positive')
                scenario = test_case.get('scenario', '')
                
                # Handle input body (could be dict, list, or string)
                input_body = test_case.get('input', {})
                if isinstance(input_body, (dict, list)):
                    input_body_json = json.dumps(input_body, ensure_ascii=False)
                else:
                    input_body_json = str(input_body)
                
                expected_response = test_case.get('expected', '')
                
                # Extract status codes from expected response (matching Excel logic)
                expected_status_codes = self._extract_response_code(expected_response)
                
                # Extract from test_case if not in session_data
                tc_endpoint = test_case.get('endpoint', endpoint)
                tc_method = test_case.get('method', method)
                tc_base_url = test_case.get('baseUrl', base_url)
                
                metadata = {
                    'id': test_case.get('id', ''),
                    'additional_info': test_case.get('additional_info', {}),
                    'field_configs': test_case.get('field_configs', {}),
                    'original_table': table_name
                }
                
                # Insert into the dynamic table
                cursor.execute(f"""
                    INSERT INTO [{table_name}]
                    (test_case_id, session_id, test_case_number, test_type, scenario,
                     input_body, expected_response, expected_status_codes,
                     base_url, endpoint, http_method, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, test_case_id, session_id, i, test_type, scenario,
                   input_body_json, expected_response, expected_status_codes,
                   tc_base_url, tc_endpoint, tc_method, json.dumps(metadata))
                
                saved_count += 1
            
            # Commit the transaction
            conn.commit()
            conn.close()
            
            return True, f"Successfully saved {saved_count} test cases to database", session_id, saved_count
            
        except Exception as e:
            # Rollback on error
            try:
                if conn is not None:
                    conn.rollback()
            except:
                pass
            
            error_msg = f"Failed to save test cases: {str(e)}"
            print(f"[ERROR] {error_msg}")
            return False, error_msg, None, saved_count

    def save_active_testcase_pool(self, pool_data: Dict[str, List[Dict[str, Any]]]) -> Tuple[bool, str, int]:
        """
        Replace and persist the active testcase pool.

        Args:
            pool_data: Mapping of group_key -> list of testcase objects.

        Returns:
            Tuple of (success, message, saved_count)
        """
        if not isinstance(pool_data, dict):
            return False, "Invalid pool payload", 0

        success, message = self.test_connection()
        if not success:
            success, message = self._create_database_and_tables()
            if not success:
                return False, f"Failed to initialize database/tables: {message}", 0

        conn = None
        saved_count = 0

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            if not self._ensure_active_testcase_pool_table_exists(cursor):
                return False, "Failed to create or verify active_testcase_pool table", 0

            cursor.execute("DELETE FROM active_testcase_pool")

            for group_key, test_cases in pool_data.items():
                if not isinstance(group_key, str) or not isinstance(test_cases, list):
                    continue

                for index, test_case in enumerate(test_cases, 1):
                    normalized_case = test_case if isinstance(test_case, dict) else {'value': test_case}
                    test_case_id = str(normalized_case.get('id', ''))[:100]
                    test_case_json = json.dumps(normalized_case, ensure_ascii=False, default=str)

                    cursor.execute("""
                        INSERT INTO active_testcase_pool
                        (pool_item_id, group_key, test_case_index, test_case_id, test_case_data)
                        VALUES (?, ?, ?, ?, ?)
                    """, str(uuid.uuid4()), group_key[:255], index, test_case_id, test_case_json)
                    saved_count += 1

            conn.commit()
            return True, f"Active testcase pool saved ({saved_count} rows)", saved_count

        except Exception as e:
            try:
                if conn is not None:
                    conn.rollback()
            except:
                pass
            return False, f"Failed to save active testcase pool: {str(e)}", 0

        finally:
            try:
                if conn is not None:
                    conn.close()
            except:
                pass

    def get_active_testcase_pool(self) -> Tuple[bool, str, Dict[str, List[Dict[str, Any]]], int]:
        """
        Load active testcase pool from database.

        Returns:
            Tuple of (success, message, pool_data, total_rows)
        """
        success, message = self.test_connection()
        if not success:
            success, message = self._create_database_and_tables()
            if not success:
                return False, f"Failed to initialize database/tables: {message}", {}, 0

        conn = None

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            if not self._ensure_active_testcase_pool_table_exists(cursor):
                return False, "Failed to create or verify active_testcase_pool table", {}, 0

            cursor.execute("""
                SELECT group_key, test_case_index, test_case_data
                FROM active_testcase_pool
                ORDER BY group_key, test_case_index
            """)

            rows = cursor.fetchall()
            pool_data: Dict[str, List[Dict[str, Any]]] = {}

            for row in rows:
                group_key = str(row.group_key)
                test_case_raw = row.test_case_data

                try:
                    test_case = json.loads(test_case_raw) if test_case_raw else {}
                except Exception:
                    test_case = {}

                if group_key not in pool_data:
                    pool_data[group_key] = []

                if isinstance(test_case, dict):
                    pool_data[group_key].append(test_case)
                else:
                    pool_data[group_key].append({'value': test_case})

            return True, f"Loaded active testcase pool ({len(rows)} rows)", pool_data, len(rows)

        except Exception as e:
            return False, f"Failed to load active testcase pool: {str(e)}", {}, 0

        finally:
            try:
                if conn is not None:
                    conn.close()
            except:
                pass

    def save_active_test_suites(self, suites: List[Dict[str, Any]], active_suite_id: Optional[str]) -> Tuple[bool, str, int]:
        """
        Replace and persist active test suites.

        Args:
            suites: List of suite objects with {id, name, cases}.
            active_suite_id: Currently selected suite id.

        Returns:
            Tuple of (success, message, saved_count)
        """
        if not isinstance(suites, list):
            return False, "Invalid suites payload", 0

        success, message = self.test_connection()
        if not success:
            success, message = self._create_database_and_tables()
            if not success:
                return False, f"Failed to initialize database/tables: {message}", 0

        conn = None
        saved_count = 0

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            if not self._ensure_active_test_suites_table_exists(cursor):
                return False, "Failed to create or verify active_test_suites table", 0

            normalized_names = {}
            for suite in suites:
                if not isinstance(suite, dict):
                    continue
                suite_name = str(suite.get('name') or 'Unnamed Suite').strip()
                name_key = suite_name.casefold()
                if name_key in normalized_names:
                    return False, f"DUPLICATE_SUITE_NAME: Suite name '{suite_name}' already exists", 0
                normalized_names[name_key] = suite_name

            cursor.execute("SELECT suite_id, suite_name FROM active_test_suites")
            existing_names = {}
            for row in cursor.fetchall():
                name_key = str(row.suite_name or '').strip().casefold()
                existing_names.setdefault(name_key, set()).add(str(row.suite_id))

            for suite in suites:
                if not isinstance(suite, dict):
                    continue
                suite_name = str(suite.get('name') or 'Unnamed Suite').strip()
                suite_id = str(suite.get('id') or '')
                conflicting_ids = existing_names.get(suite_name.casefold(), set()) - {suite_id}
                if conflicting_ids:
                    return False, f"DUPLICATE_SUITE_NAME: Suite name '{suite_name}' already exists", 0

            cursor.execute("DELETE FROM active_test_suites")

            for suite_index, suite in enumerate(suites, 1):
                if not isinstance(suite, dict):
                    continue

                suite_id = str(suite.get('id') or str(uuid.uuid4()))[:100]
                suite_name = str(suite.get('name') or 'Unnamed Suite')[:255]
                suite_cases = suite.get('cases', [])
                if not isinstance(suite_cases, list):
                    suite_cases = []

                suite_payload = {
                    'id': suite_id,
                    'name': suite_name,
                    'cases': suite_cases
                }
                suite_json = json.dumps(suite_payload, ensure_ascii=False, default=str)
                is_active = 1 if active_suite_id and suite_id == str(active_suite_id) else 0

                cursor.execute("""
                    INSERT INTO active_test_suites
                    (suite_id, suite_name, suite_index, suite_data, is_active, updated_at)
                    VALUES (?, ?, ?, ?, ?, GETDATE())
                """, suite_id, suite_name, suite_index, suite_json, is_active)

                for test_case in suite_cases:
                    if not isinstance(test_case, dict):
                        continue
                    test_case_id = str(
                        test_case.get('id') or
                        test_case.get('test_case_id') or
                        test_case.get('test_case_number') or
                        ''
                    ).strip()[:100]
                    if not test_case_id:
                        continue
                    table_name = self._generate_table_name(
                        test_case.get('baseUrl', test_case.get('base_url', '')) or 'custom',
                        test_case.get('endpoint', '/api/test'),
                        test_case.get('method', 'GET')
                    )
                    cursor.execute("""
                        INSERT INTO active_test_suite_cases
                        (suite_id, table_name, test_case_id)
                        VALUES (?, ?, ?)
                    """, suite_id, table_name, test_case_id)
                saved_count += 1

            conn.commit()
            return True, f"Active test suites saved ({saved_count} rows)", saved_count

        except Exception as e:
            try:
                if conn is not None:
                    conn.rollback()
            except:
                pass
            return False, f"Failed to save active test suites: {str(e)}", 0

        finally:
            try:
                if conn is not None:
                    conn.close()
            except:
                pass

    def get_active_test_suites(self) -> Tuple[bool, str, List[Dict[str, Any]], Optional[str], int]:
        """
        Load active test suites from database.

        Returns:
            Tuple of (success, message, suites, active_suite_id, total_rows)
        """
        success, message = self.test_connection()
        if not success:
            success, message = self._create_database_and_tables()
            if not success:
                return False, f"Failed to initialize database/tables: {message}", [], None, 0

        conn = None

        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            if not self._ensure_active_test_suites_table_exists(cursor):
                return False, "Failed to create or verify active_test_suites table", [], None, 0

            cursor.execute("""
                SELECT suite_data, is_active
                FROM active_test_suites
                ORDER BY suite_index
            """)

            rows = cursor.fetchall()
            suites: List[Dict[str, Any]] = []
            active_suite_id: Optional[str] = None

            for row in rows:
                suite_raw = row.suite_data
                is_active = bool(row.is_active)

                try:
                    suite_obj = json.loads(suite_raw) if suite_raw else {}
                except Exception:
                    suite_obj = {}

                if not isinstance(suite_obj, dict):
                    continue

                suite_id = str(suite_obj.get('id') or '')
                suite_name = str(suite_obj.get('name') or 'Unnamed Suite')
                suite_cases = suite_obj.get('cases', [])
                if not isinstance(suite_cases, list):
                    suite_cases = []

                suite_data = {
                    'id': suite_id,
                    'name': suite_name,
                    'cases': suite_cases
                }
                suites.append(suite_data)

                if is_active and suite_id:
                    active_suite_id = suite_id

            return True, f"Loaded active test suites ({len(rows)} rows)", suites, active_suite_id, len(rows)

        except Exception as e:
            return False, f"Failed to load active test suites: {str(e)}", [], None, 0

        finally:
            try:
                if conn is not None:
                    conn.close()
            except:
                pass
    
    def get_sessions(self, limit: int = 50, offset: int = 0,
                     endpoint_filter: Optional[str] = None,
                     base_url_filter: Optional[str] = None,
                     method_filter: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
        """
        Retrieve saved test case sessions.
        
        Args:
            limit: Maximum number of sessions to return
            offset: Number of sessions to skip
            endpoint_filter: Optional endpoint filter
            base_url_filter: Optional base URL filter
            method_filter: Optional HTTP method filter
            
        Returns:
            Tuple of (sessions list, total count)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            def fetch_scalar() -> int:
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
            
            # Build query with optional filters
            query = """
                SELECT session_id, session_name, endpoint, http_method, base_url,
                       created_by, created_at, total_test_cases, table_name
                FROM test_case_sessions
                WHERE 1=1
            """
            params = []
            
            if endpoint_filter:
                query += " AND endpoint LIKE ?"
                params.append(f"%{endpoint_filter}%")
            
            if base_url_filter:
                query += " AND base_url LIKE ?"
                params.append(f"%{base_url_filter}%")
            
            if method_filter:
                query += " AND http_method = ?"
                params.append(method_filter)
            
            query += " ORDER BY created_at DESC"
            
            # Get total count
            count_query = "SELECT COUNT(*) FROM test_case_sessions WHERE 1=1"
            if endpoint_filter:
                count_query += " AND endpoint LIKE ?"
            if base_url_filter:
                count_query += " AND base_url LIKE ?"
            if method_filter:
                count_query += " AND http_method = ?"
            
            cursor.execute(count_query, params)
            total_count = fetch_scalar()
            
            # Get paginated results
            query += " OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
            params.extend([offset, limit])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            sessions = []
            for row in rows:
                session = {
                    'session_id': row.session_id,
                    'session_name': row.session_name,
                    'endpoint': row.endpoint,
                    'method': row.http_method,
                    'base_url': row.base_url,
                    'created_by': row.created_by,
                    'created_at': row.created_at.isoformat() if row.created_at else None,
                    'test_cases_count': row.total_test_cases,
                    'table_name': row.table_name
                }
                sessions.append(session)
            
            conn.close()
            return sessions, total_count
            
        except Exception as e:
            error_msg = f"Failed to retrieve sessions: {str(e)}"
            print(f"[ERROR] {error_msg}")
            return [], 0
    
    def get_test_cases(self, session_id: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Retrieve test cases for a specific session.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Tuple of (session_info, test_cases list)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Get session info including table_name
            cursor.execute("""
                SELECT session_id, session_name, endpoint, http_method, base_url,
                       created_by, created_at, total_test_cases, table_name
                FROM test_case_sessions
                WHERE session_id = ?
            """, session_id)
            
            session_row = cursor.fetchone()
            if not session_row:
                conn.close()
                return None, []
            
            table_name = session_row.table_name
            session_info = {
                'session_id': session_row.session_id,
                'session_name': session_row.session_name,
                'endpoint': session_row.endpoint,
                'http_method': session_row.http_method,
                'base_url': session_row.base_url,
                'created_by': session_row.created_by,
                'created_at': session_row.created_at.isoformat() if session_row.created_at else None,
                'total_test_cases': session_row.total_test_cases,
                'table_name': table_name
            }
            
            # Get test cases from the dynamic table
            cursor.execute(f"""
                SELECT test_case_id, test_case_number, test_type, scenario,
                       input_body, expected_response, expected_status_codes,
                       base_url, endpoint, http_method, metadata, created_at
                FROM [{table_name}]
                WHERE session_id = ?
                ORDER BY test_case_number
            """, session_id)
            
            rows = cursor.fetchall()
            test_cases = []
            
            for row in rows:
                # Parse input body JSON if possible
                input_body = row.input_body
                try:
                    if input_body and input_body.strip():
                        input_body = json.loads(input_body)
                except:
                    pass  # Keep as string if not valid JSON
                
                # Parse metadata JSON
                metadata = {}
                if row.metadata and row.metadata.strip():
                    try:
                        metadata = json.loads(row.metadata)
                    except:
                        pass
                original_test_case_id = (
                    metadata.get('id')
                    or row.test_case_id
                )

                test_case = {
                    'id': original_test_case_id,
                    'test_case_id': original_test_case_id,
                    'test_case_number': row.test_case_number,
                    'type': row.test_type,
                    'scenario': row.scenario,
                    'input': input_body,
                    'expected': row.expected_response,
                    'expected_status': row.expected_status_codes,
                    'baseUrl': row.base_url,
                    'endpoint': row.endpoint,
                    'method': row.http_method,
                    'metadata': metadata,
                    'field_configs': metadata.get('field_configs', {}),
                    'created_at': row.created_at.isoformat() if row.created_at else None
                }
                test_cases.append(test_case)
            
            conn.close()
            return session_info, test_cases
            
        except Exception as e:
            error_msg = f"Failed to retrieve test cases: {str(e)}"
            print(f"[ERROR] {error_msg}")
            return None, []


# Global database instance
db_instance = None


def get_database() -> TestCaseDatabase:
    """
    Get or create global database instance.
    
    Returns:
        TestCaseDatabase instance
    """
    global db_instance
    if db_instance is None:
        db_instance = TestCaseDatabase()
    return db_instance


def initialize_database() -> Tuple[bool, str]:
    """
    Initialize database connection and test.
    
    Returns:
        Tuple of (success, message)
    """
    try:
        db = get_database()
        return db.test_connection()
    except Exception as e:
        return False, f"Database initialization failed: {str(e)}"
