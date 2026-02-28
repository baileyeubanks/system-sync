"""Agent identity and business-unit enforcement middleware for Blaze V4 API."""

from typing import Optional

# Agent -> allowed business units
AGENT_PERMISSIONS = {
    "main":            {"CC", "ACS", "BOTH"},
    "cc-worker":       {"CC", "BOTH"},
    "acs-worker":      {"ACS"},
    "research-worker": {"CC", "ACS", "BOTH"},
}

# Agent -> default business unit (when not specified)
AGENT_DEFAULT_BU = {
    "main":            "CC",
    "cc-worker":       "CC",
    "acs-worker":      "ACS",
    "research-worker": "CC",
}

# Agent -> allowed email accounts
AGENT_EMAIL_ACCESS = {
    "main":            {"bailey@contentco-op.com", "caio@astrocleanings.com", "blaze@contentco-op.com"},
    "cc-worker":       {"bailey@contentco-op.com", "blaze@contentco-op.com"},
    "acs-worker":      {"caio@astrocleanings.com"},
    "research-worker": {"bailey@contentco-op.com", "caio@astrocleanings.com", "blaze@contentco-op.com"},
}


def get_agent_id(headers):
    """Extract agent identity from X-Agent-Id header."""
    agent_id = None
    if hasattr(headers, "get"):
        agent_id = headers.get("X-Agent-Id")
    if not agent_id or agent_id not in AGENT_PERMISSIONS:
        return "main"
    return agent_id


def check_business_unit(agent_id, requested_bu):
    """Check if agent is allowed to access the requested business_unit.
    Returns (allowed: bool, error_message: str or None).
    """
    if not requested_bu:
        return True, None
    allowed = AGENT_PERMISSIONS.get(agent_id, set())
    if requested_bu not in allowed:
        return False, "Agent '%s' is not authorized for business_unit '%s'" % (agent_id, requested_bu)
    return True, None


def default_business_unit(agent_id):
    """Return the default business_unit for an agent."""
    return AGENT_DEFAULT_BU.get(agent_id, "CC")


def resolve_business_unit(agent_id, requested_bu):
    """Resolve the effective business_unit: use requested if allowed, else default.
    Returns (business_unit: str, allowed: bool, error: str or None).
    """
    if not requested_bu:
        return default_business_unit(agent_id), True, None
    ok, err = check_business_unit(agent_id, requested_bu)
    if not ok:
        return None, False, err
    return requested_bu, True, None


def check_email_access(agent_id, email_account):
    """Check if agent can access a specific email account.
    Returns (allowed: bool, error_message: str or None).
    """
    allowed = AGENT_EMAIL_ACCESS.get(agent_id, set())
    if email_account not in allowed:
        return False, "Agent '%s' cannot access email '%s'" % (agent_id, email_account)
    return True, None
