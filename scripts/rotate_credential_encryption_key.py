"""One-time re-encryption of stored secrets onto a new encryption key.

Covers accounts.ig_password_enc and the encrypted columns of system_configs
(smtp_password_enc, textbelt_key_enc, resend_api_key_enc).

Run this BEFORE setting CREDENTIAL_ENCRYPTION_KEY in the deployed environment
(see app/crypto.py and .env.example) — otherwise every account's stored IG
password becomes undecryptable the moment the app starts using the new key.

Usage:
    DATABASE_PUBLIC_URL=... python scripts/rotate_credential_encryption_key.py \\
        --old-key "$SECRET_KEY" \\
        --new-key "$CREDENTIAL_ENCRYPTION_KEY" \\
        [--dry-run]

--old-key is whatever app/crypto.py currently derives from — i.e. the
existing CREDENTIAL_ENCRYPTION_KEY if one is already set, otherwise
SECRET_KEY (the pre-migration fallback). --new-key is the value you're about
to set CREDENTIAL_ENCRYPTION_KEY to.

This is NOT idempotent: running it twice with the same --old-key/--new-key
would decrypt already-migrated rows with the wrong (new, not old) key and
fail loudly (Fernet raises InvalidToken) rather than silently corrupting
data — but don't run it twice regardless. Use --dry-run first to confirm the
row count and that every value decrypts cleanly under --old-key.
"""
import argparse
import asyncio
import base64
import hashlib
import os

import asyncpg
from cryptography.fernet import Fernet

# Fixed list, not user input: these names go into SQL text.
SYSTEM_CONFIG_ENC_COLUMNS = ("smtp_password_enc", "textbelt_key_enc", "resend_api_key_enc")


def _derive_fernet(key_source: str) -> Fernet:
    # Must match app/crypto.py's derivation exactly.
    key = base64.urlsafe_b64encode(hashlib.sha256(key_source.encode()).digest())
    return Fernet(key)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-key", required=True, help="Current effective key (secret_key or existing credential_encryption_key)")
    parser.add_argument("--new-key", required=True, help="New credential_encryption_key value to migrate onto")
    parser.add_argument("--dry-run", action="store_true", help="Decrypt/re-encrypt in memory only; don't write to the DB")
    args = parser.parse_args()

    if args.old_key == args.new_key:
        print("error: --old-key and --new-key are identical; nothing to rotate")
        return

    old_fernet = _derive_fernet(args.old_key)
    new_fernet = _derive_fernet(args.new_key)

    url = os.environ["DATABASE_PUBLIC_URL"]
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT id, ig_password_enc FROM accounts WHERE ig_password_enc IS NOT NULL"
        )
        print(f"found {len(rows)} account(s) with a stored password")

        config_rows = []
        for col in SYSTEM_CONFIG_ENC_COLUMNS:
            for row in await conn.fetch(
                f"SELECT id, {col} AS value FROM system_configs WHERE {col} IS NOT NULL"
            ):
                config_rows.append((col, row["id"], row["value"]))
        print(f"found {len(config_rows)} system_configs secret(s)")

        config_updates = []
        for col, config_id, value in config_rows:
            try:
                plaintext = old_fernet.decrypt(value.encode()).decode()
            except Exception as e:
                print(f"FAILED to decrypt system_configs.{col} under --old-key: {e}")
                print("aborting — no rows have been written")
                return
            config_updates.append((col, config_id, new_fernet.encrypt(plaintext.encode()).decode()))

        updates = []
        for row in rows:
            try:
                plaintext = old_fernet.decrypt(row["ig_password_enc"].encode()).decode()
            except Exception as e:
                print(f"FAILED to decrypt account {row['id']} under --old-key: {e}")
                print("aborting — no rows have been written")
                return
            new_ciphertext = new_fernet.encrypt(plaintext.encode()).decode()
            updates.append((row["id"], new_ciphertext))

        print(f"decrypted all {len(updates)} row(s) successfully under --old-key")

        if args.dry_run:
            print("dry run — no rows written. Re-run without --dry-run to apply.")
            return

        async with conn.transaction():
            for account_id, new_ciphertext in updates:
                await conn.execute(
                    "UPDATE accounts SET ig_password_enc = $1 WHERE id = $2",
                    new_ciphertext, account_id,
                )
            for col, config_id, new_ciphertext in config_updates:
                await conn.execute(
                    f"UPDATE system_configs SET {col} = $1 WHERE id = $2",
                    new_ciphertext, config_id,
                )
        print(f"ok: re-encrypted {len(updates)} account row(s) and {len(config_updates)} system_configs secret(s) onto the new key")
    finally:
        await conn.close()


asyncio.run(main())
