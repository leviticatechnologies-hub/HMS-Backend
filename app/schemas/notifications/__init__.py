"""Notification schemas."""
from app.schemas.notifications.provider import (
    NotificationProviderResponse,
    NotificationProviderStatusUpdate,
    NotificationProviderConfigUpdate,
    NotificationProviderTestRequest,
)
from app.schemas.notifications.template import NotificationTemplateResponse
from app.schemas.notifications.preference import (
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
)
from app.schemas.notifications.job import (
    NotificationJobResponse,
    NotificationDeliveryLogResponse,
    NotificationJobDetailResponse,
)
from app.schemas.notifications.send import (
    NotificationSendRequest,
    NotificationScheduleRequest,
    OtpSendRequest,
    OtpVerifyRequest,
    BulkSmsRequest,
)
from app.schemas.notifications.history import NotificationHistoryFilters, NotificationQueueQuery
from app.schemas.notifications.ticket_email import TicketEmailRequest

__all__ = [
    "NotificationProviderResponse",
    "NotificationProviderStatusUpdate",
    "NotificationProviderConfigUpdate",
    "NotificationProviderTestRequest",
    "NotificationTemplateResponse",
    "NotificationPreferenceResponse",
    "NotificationPreferenceUpdate",
    "NotificationJobResponse",
    "NotificationDeliveryLogResponse",
    "NotificationJobDetailResponse",
    "NotificationSendRequest",
    "NotificationScheduleRequest",
    "OtpSendRequest",
    "OtpVerifyRequest",
    "BulkSmsRequest",
    "NotificationHistoryFilters",
    "NotificationQueueQuery",
    "TicketEmailRequest",
]
