# API Test Case Execution System

A Flask-based web application for generating, executing, and managing API test cases with SQL Server database integration.

## Project Structure (Cleaned)
## Project Structure (Cleaned) -- Added a new Line to check

After cleanup, the project contains only essential files:

### Core Application Files
- `app.py` - Main Flask application with REST API endpoints
- `database.py` - Database operations for SQL Server test case storage
- `testcaseengine.py` - Test case generation engine
- `requirements.txt` - Python dependencies

### Startup Scripts
- `start_flask.bat` - Windows batch script to start Flask server
- `start_flask.sh` - Linux shell script to start Flask server  
- `start_flask_no_debug.py` - Alternative Flask startup without debug mode

### Frontend
- `templates/index.html` - Web interface for test case management
- `static/` - Static assets (CSS, JavaScript)

### Documentation
- `LAUNCH_FLASK.md` - Instructions for launching the Flask application
- `FLASK_CONTROL_GUIDE.md` - Guide for controlling Flask server
- `FINAL_IMPLEMENTATION_SUMMARY.md` - Summary of implementation
- `DATABASE_TEST_CASE_EXECUTION_ENHANCEMENTS.md` - Database enhancements
- `DELETE_TEST_CASES.md` - Test case deletion procedures
- `NESTED_ARRAY_VALIDATION.md` - Validation for nested arrays
- `database_saving_implementation_plan.md` - Database saving plan

### Sample Data
- `POST_API_TEST_TestCases_2026-04-09_16-08-07.xlsx` - Example test case Excel file

### Backup
- `backup_20250419/` - Backup of removed debug/test files (can be deleted if not needed)

## Removed Files

All debug, test, and temporary files have been moved to the `backup_20250419/` directory, including:
- All `debug_*.py` files
- All `check_*.py` files  
- All `test_*.py` files (except testcaseengine.py)
- All `simple_*.py` files
- All `final_*.py` files
- All `diagnose_*.py` files
- All `quick_*.py` files
- Various temporary Excel files
- Cache directories (__pycache__, .pytest_cache, .zencoder, .zenflow)

## Quick Start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Start the Flask server:
   - Windows: Run `start_flask.bat`
   - Linux/Mac: Run `./start_flask.sh`
   - Manual: `python start_flask_no_debug.py`

3. Open browser to `http://localhost:5000`

## Core Features

- **Test Case Generation**: Generate comprehensive API test cases from payload definitions
- **Database Integration**: Save and retrieve test cases from SQL Server
- **Excel Export**: Download test cases as Excel files
- **API Execution**: Execute test cases against target APIs
- **Result Validation**: Validate API responses against expected results

## Dependencies

- Flask 2.3.3
- flask-cors
- openpyxl
- pyodbc (for SQL Server)
- requests
- jsonschema

## Database Configuration

Default SQL Server configuration uses Windows Authentication. Modify `database.py` for custom configuration.

## License

Proprietary - Internal use only