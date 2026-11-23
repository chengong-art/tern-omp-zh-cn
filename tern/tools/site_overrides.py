def skip_translation(addr_zh, address, length):
    key = (address, length)
    if key not in addr_zh:
        return False
    value = addr_zh[key]
    if value is None:
        return True
    if not isinstance(value, str) or not value:
        raise ValueError(f"site override for {address:#x}:{length} must be a non-empty string or null")
    return False
