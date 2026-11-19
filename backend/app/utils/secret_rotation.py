from app.utils.secret_encryption import decrypt_secret, encrypt_secret


def rotate_encrypted_value(
    secret_value: str,
    *,
    old_seed: str,
    new_seed: str,
) -> str:
    if not old_seed:
        raise ValueError("old_seed must not be empty")
    if not new_seed:
        raise ValueError("new_seed must not be empty")
    if old_seed == new_seed:
        raise ValueError("old_seed and new_seed must differ")

    plain_secret = decrypt_secret(secret_value, seed=old_seed)
    return encrypt_secret(plain_secret, seed=new_seed)
