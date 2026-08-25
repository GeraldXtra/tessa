"""core.gcal — Google Calendar, read-only, for the Orb's TODAY panel."""

from .google import (  # noqa: F401
    CLIENT_SECRET_PATH,
    SCOPE,
    TOKEN_PATH,
    CalendarEvent,
    CalendarResult,
    CalendarUnavailable,
    GoogleCalendar,
    authorise,
    revoke,
)
