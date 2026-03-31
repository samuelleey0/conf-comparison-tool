IGNORE_PATTERNS = [
    "service timestamps",
    "crypto pki",
    "certificate",
    "ntp clock-period",
    "ssh key",
    "crypto key",
    "license udi",
    "diagonostic bootup",
    "memory free",
    "platform",
    "boot-start-marker",
    "boot-end-marker",
]


def should_ignore(line: str) -> bool:
    """
    Return True if line should be ignored in parsing
    """
    line = line.strip().lower()
    for pattern in IGNORE_PATTERNS:
        if pattern.lower() in line:
            return True
    return False
