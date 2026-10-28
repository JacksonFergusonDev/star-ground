"""Turns Star Ground's API off when the month's spend reaches the cutoff budget.

Google Cloud has no hard spending limit. A budget publishes the month's spend
to Pub/Sub every few hours; this function, subscribed to that topic, closes
the Cloud Run service to the internet (ingress `internal`) once the spend
reaches the budget. Nothing is deleted. `deploy/README.md` explains how it's
set up and how to turn the service back on.
"""

import base64
import json
import logging
import os

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import run_v2

SERVICE = os.environ["SERVICE"]
"""The service to close: projects/<project>/locations/<region>/services/<name>."""

logger = logging.getLogger(__name__)


def over_budget(notification: dict[str, object]) -> bool:
    """Whether a budget notification reports spend at or over the budget.

    Args:
        notification: The notification's JSON, as the budget publishes it.

    Returns:
        True once the month's cost reaches the budget's amount.
    """
    cost = float(str(notification.get("costAmount", 0)))
    budget = float(str(notification.get("budgetAmount", 0)))
    return budget > 0 and cost >= budget


@functions_framework.cloud_event
def cut_off(event: CloudEvent) -> None:
    """Closes the service to the internet if the budget is spent.

    Args:
        event: The Pub/Sub message carrying a budget notification.
    """
    notification = json.loads(base64.b64decode(event.data["message"]["data"]))
    if not over_budget(notification):
        return
    client = run_v2.ServicesClient()
    service = client.get_service(name=SERVICE)
    if service.ingress == run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_ONLY:
        return
    service.ingress = run_v2.IngressTraffic.INGRESS_TRAFFIC_INTERNAL_ONLY
    client.update_service(service=service).result()
    logger.warning(
        "Spend %s reached the %s budget; closed %s to the internet.",
        notification.get("costAmount"),
        notification.get("budgetAmount"),
        SERVICE,
    )
