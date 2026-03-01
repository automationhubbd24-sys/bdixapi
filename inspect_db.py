import os
import pg8000.native
from dotenv import load_dotenv
from urllib.parse import urlparse

def inspect_table():
    load_dotenv()
    url_str = os.getenv("POSTGRES_URL")
    if not url_str:
        print("Error: POSTGRES_URL not found in .env")
        return

    # Parse URL
    # postgresql://user:pass@host:port/db
    try:
        parsed = urlparse(url_str)
        user = parsed.username
        password = parsed.password
        host = parsed.hostname
        port = parsed.port or 5432
        database = parsed.path.lstrip('/')
    except Exception as e:
        print(f"Error parsing URL: {e}")
        return

    try:
        print(f"Connecting to {host}:{port}/{database} as {user}...")
        conn = pg8000.native.Connection(
            user=user,
            password=password,
            host=host,
            port=port,
            database=database,
            timeout=10
        )
        print("Connected to PostgreSQL.")

        # Check table columns
        print("\n--- Columns in 'api_list' ---")
        columns = conn.run("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'api_list';
        """)
        
        if not columns:
            print("Table 'api_list' not found or has no columns.")
        else:
            # Check unique providers
            print("\n--- Unique Providers in 'api_list' ---")
            providers = conn.run("SELECT DISTINCT provider FROM api_list;")
            for p in providers:
                print(f"- {p[0]}")

            # Check for Google/Gemini keys specifically
            print("\n--- Google/Gemini keys count ---")
            count = conn.run("SELECT COUNT(*) FROM api_list WHERE provider ILIKE '%google%' OR provider ILIKE '%gemini%';")
            print(f"Total: {count[0][0]}")

            # Fetch sample data for google
            print("\n--- Sample Google Keys ---")
            google_keys = conn.run("SELECT id, provider, model, api, status FROM api_list WHERE provider ILIKE '%google%' OR provider ILIKE '%gemini%' LIMIT 5;")
            for row in google_keys:
                print(row)

        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_table()
