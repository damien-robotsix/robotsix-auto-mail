"""Root exception hierarchy for robotsix-auto-mail.

All domain exceptions in the package inherit from :class:`RobotsixMailError`,
giving callers a single catch-all for known failures while letting
unexpected exceptions propagate.
"""


class RobotsixMailError(Exception):
    """Root of all robotsix-auto-mail domain exceptions."""
