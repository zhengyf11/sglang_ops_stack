from sglang_ops_stack.utils.masking import mask_secret


def test_mask_explicit_secret() -> None:
    assert mask_secret("stdout has p@ssw0rd", ["p@ssw0rd"]) == "stdout has ***"


def test_mask_token_and_password_patterns() -> None:
    text = "password=hunter2 token=abc Authorization: Bearer secret-token"
    masked = mask_secret(text)
    assert "hunter2" not in masked
    assert "abc" not in masked
    assert "secret-token" not in masked
    assert "***" in masked


def test_mask_private_key_block() -> None:
    text = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----"
    assert mask_secret(text) == "***"


def test_none_masks_to_empty_string() -> None:
    assert mask_secret(None) == ""
