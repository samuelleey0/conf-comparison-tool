"""
Small comparison-engine helpers.

This module contains shared filtering logic used by the parser to ignore
volatile Cisco configuration lines before comparison.
"""

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
    Return True when a config line is generated, volatile, or irrelevant.

    parser.py calls this while reading show running-config so changing device
    metadata such as certificates, keys, and platform boot markers does not
    create false grading differences.
    """
    line = line.strip().lower()
    for pattern in IGNORE_PATTERNS:
        if pattern.lower() in line:
            return True
    return False
